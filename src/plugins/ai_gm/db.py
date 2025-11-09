import aiosqlite
from ncatbot.utils import get_log
from contextlib import asynccontextmanager
from contextvars import ContextVar
import uuid
import time

from .constants import DB_BUSY_TIMEOUT_MS, DB_WAL_AUTOCHECKPOINT

LOG = get_log(__name__)

# 用于跟踪当前事务深度的上下文变量
_transaction_depth: ContextVar[int] = ContextVar('transaction_depth', default=0)


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None
        self._connection_healthy = True
        self._last_health_check = 0.0
        self._health_check_interval = 60.0  # 60秒检查一次连接健康

    async def connect(self):
        """连接到数据库并进行初始化"""
        try:
            self.conn = await aiosqlite.connect(self.db_path)
            self.conn.row_factory = aiosqlite.Row
            await self.conn.execute("PRAGMA journal_mode=WAL;")
            await self.conn.execute("PRAGMA synchronous=NORMAL;")
            await self.conn.execute("PRAGMA foreign_keys = ON;")
            await self.conn.execute(f"PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS};")
            await self.conn.execute(f"PRAGMA wal_autocheckpoint={DB_WAL_AUTOCHECKPOINT};")
            await self.init_db()
            LOG.info(f"成功连接并初始化数据库: {self.db_path}")
        except aiosqlite.Error as e:
            LOG.error(f"数据库连接失败: {e}")
            raise

    async def _ensure_connection(self):
        """
        确保数据库连接可用，如果连接断开则尝试重连。
        
        优化版本：减少不必要的健康检查，只在间隔时间后才测试连接。
        
        Raises:
            RuntimeError: 如果数据库未初始化或重连失败
        """
        if not self.conn:
            LOG.warning("数据库连接不存在，尝试建立连接...")
            await self.connect()
            return
        
        # 只在间隔时间后才做健康检查
        now = time.time()
        if now - self._last_health_check < self._health_check_interval:
            return
        
        # 测试连接是否可用
        try:
            await self.conn.execute("SELECT 1")
            self._last_health_check = now
            self._connection_healthy = True
        except Exception as e:
            LOG.warning(f"数据库连接健康检查失败: {e}，尝试重连...")
            self._connection_healthy = False
            try:
                await self.close()
            except Exception:
                pass
            await self.connect()
            self._last_health_check = now

    async def close(self):
        """关闭数据库连接"""
        if self.conn:
            await self.conn.close()
            LOG.info("数据库连接已关闭。")

    async def init_db(self):
        """创建所有必要的表"""
        if not self.conn:
            LOG.error("数据库未连接，无法初始化。")
            return

        async with self.conn.cursor() as cursor:
            # 创建 games 表
            await cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS games (
                    game_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT UNIQUE,
                    main_message_id TEXT,
                    candidate_custom_input_ids TEXT,
                    head_branch_id INTEGER,
                    system_prompt TEXT,
                    host_user_id TEXT,
                    is_frozen BOOLEAN NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (head_branch_id) REFERENCES branches (branch_id) ON DELETE SET NULL
                );
            """
            )

            # 创建 branches 表
            await cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS branches (
                    branch_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    tip_round_id INTEGER,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(game_id, name),
                    FOREIGN KEY (game_id) REFERENCES games (game_id) ON DELETE CASCADE,
                    FOREIGN KEY (tip_round_id) REFERENCES rounds (round_id) ON DELETE SET NULL
                );
            """
            )

            # 创建 rounds 表
            await cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS rounds (
                    round_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL,
                    parent_id INTEGER NOT NULL CHECK(parent_id >= -1),
                    player_choice TEXT NOT NULL,
                    assistant_response TEXT NOT NULL,
                    llm_usage TEXT,
                    model_name TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games (game_id) ON DELETE CASCADE
                );
            """
            )

            # 创建 tags 表
            await cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS tags (
                    tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    round_id INTEGER NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(game_id, name),
                    FOREIGN KEY (game_id) REFERENCES games (game_id) ON DELETE CASCADE,
                    FOREIGN KEY (round_id) REFERENCES rounds (round_id) ON DELETE CASCADE
                );
                """
            )

            # 创建触发器，用于自动更新 games 表的 updated_at
            await cursor.execute(
                """
                CREATE TRIGGER IF NOT EXISTS update_game_updated_at
                AFTER UPDATE ON games
                FOR EACH ROW
                WHEN NEW.updated_at = OLD.updated_at
                BEGIN
                    UPDATE games SET updated_at = CURRENT_TIMESTAMP WHERE game_id = OLD.game_id;
                END;
            """
            )

            # 创建触发器，用于自动更新 branches 表的 updated_at
            await cursor.execute(
                """
                CREATE TRIGGER IF NOT EXISTS update_branch_updated_at
                AFTER UPDATE ON branches
                FOR EACH ROW
                WHEN NEW.updated_at = OLD.updated_at
                BEGIN
                    UPDATE branches SET updated_at = CURRENT_TIMESTAMP WHERE branch_id = OLD.branch_id;
                END;
            """
            )

            # 创建索引
            await cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_games_main_msg ON games(main_message_id);"
            )
            await cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_branches_game ON branches(game_id);"
            )
            await cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_rounds_game ON rounds(game_id);"
            )
            await cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_rounds_parent ON rounds(parent_id);"
            )
            await cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_tags_game ON tags(game_id);"
            )
            await cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_tags_round_id ON tags(round_id);"
            )

        if self.conn:
            await self.conn.commit()

    @asynccontextmanager
    async def transaction(self):
        """
        提供支持嵌套的事务上下文管理器。
        
        嵌套事务通过 SAVEPOINT 实现。顶层事务使用 BEGIN IMMEDIATE。
        改进版本：使用 UUID 生成 savepoint 名称，避免潜在的命名冲突。
        
        Yields:
            None
            
        Raises:
            RuntimeError: 如果数据库未连接
            Exception: 事务执行过程中的任何异常都会导致回滚
        """
        if not self.conn:
            raise RuntimeError("数据库未连接")

        # 获取当前事务深度
        depth = _transaction_depth.get()
        _transaction_depth.set(depth + 1)

        try:
            if depth > 0:
                # 嵌套事务：使用 UUID 生成唯一的 savepoint 名称
                savepoint_name = f"sp_{uuid.uuid4().hex[:8]}"
                try:
                    await self.conn.execute(f"SAVEPOINT {savepoint_name};")
                    yield
                    await self.conn.execute(f"RELEASE SAVEPOINT {savepoint_name};")
                except Exception:
                    await self.conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name};")
                    await self.conn.execute(f"RELEASE SAVEPOINT {savepoint_name};")
                    raise
            else:
                # 顶层事务
                try:
                    await self.conn.execute("BEGIN IMMEDIATE;")
                    yield
                    await self.conn.commit()
                except Exception:
                    await self.conn.rollback()
                    raise
        finally:
            # 恢复事务深度
            _transaction_depth.set(depth)

    async def is_game_running(self, channel_id: str) -> bool:
        """
        检查指定频道当前是否有正在进行的游戏。
        
        Args:
            channel_id: 频道ID
            
        Returns:
            bool: 如果频道有正在进行的游戏返回 True，否则返回 False
            
        Raises:
            RuntimeError: 如果数据库连接失败
        """
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.execute(
            "SELECT 1 FROM games WHERE channel_id = ?", (channel_id,)
        ) as cursor:
            result = await cursor.fetchone()
            return result is not None

    async def get_game_by_channel_id(self, channel_id: str):
        """
        通过 channel_id 获取游戏信息。
        
        Args:
            channel_id: 频道ID
            
        Returns:
            aiosqlite.Row | None: 游戏记录，如果不存在则返回 None
            
        Raises:
            RuntimeError: 如果数据库未连接
        """
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.execute(
            "SELECT * FROM games WHERE channel_id = ?", (channel_id,)
        ) as cursor:
            return await cursor.fetchone()

    async def get_game_by_game_id(self, game_id: int):
        """
        通过 game_id 获取游戏信息。
        
        Args:
            game_id: 游戏ID
            
        Returns:
            aiosqlite.Row | None: 游戏记录，如果不存在则返回 None
            
        Raises:
            RuntimeError: 如果数据库未连接
        """
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.execute(
            "SELECT * FROM games WHERE game_id = ?", (game_id,)
        ) as cursor:
            return await cursor.fetchone()

    async def set_game_frozen_status(self, game_id: int, is_frozen: bool):
        """设置游戏的冻结状态"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.transaction():
            async with self.conn.cursor() as cursor:
                await cursor.execute(
                    "UPDATE games SET is_frozen = ? WHERE game_id = ?",
                    (is_frozen, game_id),
                )

    async def update_candidate_custom_input_ids(
        self, game_id: int, candidate_ids_json: str
    ):
        """更新候选自定义输入ID"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.transaction():
            async with self.conn.cursor() as cursor:
                await cursor.execute(
                    "UPDATE games SET candidate_custom_input_ids = ? WHERE game_id = ?",
                    (candidate_ids_json, game_id),
                )

    async def get_host_user_id(self, channel_id: str) -> str | None:
        """
        获取游戏主持人ID。
        
        Args:
            channel_id: 频道ID
            
        Returns:
            str | None: 主持人用户ID，如果游戏不存在则返回 None
            
        Raises:
            RuntimeError: 如果数据库未连接
        """
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.execute(
            "SELECT host_user_id FROM games WHERE channel_id = ?", (channel_id,)
        ) as cursor:
            result = await cursor.fetchone()
            return result["host_user_id"] if result else None

    async def create_game(
        self, channel_id: str, user_id: str, system_prompt: str
    ) -> int:
        """创建新游戏并返回 game_id"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.transaction():
            async with self.conn.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO games (channel_id, host_user_id, system_prompt) VALUES (?, ?, ?)",
                    (channel_id, user_id, system_prompt),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("插入游戏数据失败")
                return cursor.lastrowid

    async def create_round(
        self,
        game_id: int,
        parent_id: int,
        player_choice: str,
        assistant_response: str,
        llm_usage: str | None = None,
        model_name: str | None = None,
    ) -> int:
        """创建新回合并返回 round_id"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.transaction():
            async with self.conn.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO rounds (game_id, parent_id, player_choice, assistant_response, llm_usage, model_name) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        game_id,
                        parent_id,
                        player_choice,
                        assistant_response,
                        llm_usage,
                        model_name,
                    ),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("插入回合数据失败")
                return cursor.lastrowid

    async def create_branch(
        self, game_id: int, name: str, tip_round_id: int
    ) -> int:
        """创建新分支并返回 branch_id"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.transaction():
            async with self.conn.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO branches (game_id, name, tip_round_id) VALUES (?, ?, ?)",
                    (game_id, name, tip_round_id),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("插入分支数据失败")
                return cursor.lastrowid

    async def update_game_head_branch(self, game_id: int, branch_id: int):
        """更新游戏的 head_branch_id"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.transaction():
            async with self.conn.cursor() as cursor:
                await cursor.execute(
                    "UPDATE games SET head_branch_id = ? WHERE game_id = ?",
                    (branch_id, game_id),
                )

    async def get_game_and_head_branch_info(self, game_id: int):
        """
        获取游戏和 head 分支信息。
        
        Args:
            game_id: 游戏ID
            
        Returns:
            aiosqlite.Row: 包含 channel_id 和 tip_round_id 的记录
            
        Raises:
            RuntimeError: 如果数据库未连接或游戏 head 分支未设置
        """
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.execute(
            """SELECT g.channel_id, b.tip_round_id
               FROM games g
               LEFT JOIN branches b ON g.head_branch_id = b.branch_id
               WHERE g.game_id = ?""",
            (game_id,),
        ) as cursor:
            row = await cursor.fetchone()
            if not row or row["tip_round_id"] is None:
                raise RuntimeError("游戏 head 分支未设置或已损坏")
            return row

    async def get_round_info(self, round_id: int):
        """
        获取回合信息。
        
        Args:
            round_id: 回合ID
            
        Returns:
            aiosqlite.Row | None: 回合记录，如果不存在则返回 None
            
        Raises:
            RuntimeError: 如果数据库未连接
        """
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.execute(
            "SELECT * FROM rounds WHERE round_id = ?", (round_id,)
        ) as cursor:
            return await cursor.fetchone()

    async def update_game_main_message(self, game_id: int, main_message_id: str):
        """更新游戏的主消息ID"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.transaction():
            async with self.conn.cursor() as cursor:
                await cursor.execute(
                    "UPDATE games SET main_message_id = ?, candidate_custom_input_ids = '[]' WHERE game_id = ?",
                    (main_message_id, game_id),
                )

    async def update_branch_tip(self, branch_id: int, round_id: int):
        """更新分支的 tip_round_id (用于推进或回退)"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.transaction():
            async with self.conn.cursor() as cursor:
                await cursor.execute(
                    "UPDATE branches SET tip_round_id = ? WHERE branch_id = ?",
                    (round_id, branch_id),
                )

    async def rename_branch(self, branch_id: int, new_name: str):
        """重命名分支"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.transaction():
            async with self.conn.cursor() as cursor:
                await cursor.execute(
                    "UPDATE branches SET name = ? WHERE branch_id = ?",
                    (new_name, branch_id),
                )

    async def delete_branch(self, branch_id: int):
        """删除分支"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.transaction():
            async with self.conn.cursor() as cursor:
                await cursor.execute("DELETE FROM branches WHERE branch_id = ?", (branch_id,))

    async def delete_game(self, game_id: int):
        """删除游戏"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.transaction():
            async with self.conn.cursor() as cursor:
                await cursor.execute("DELETE FROM games WHERE game_id = ?", (game_id,))

    async def get_all_games(self):
        """
        获取所有游戏的信息。
        
        Returns:
            list[aiosqlite.Row]: 所有游戏记录的列表
            
        Raises:
            RuntimeError: 如果数据库未连接
        """
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.execute(
            "SELECT game_id, channel_id, host_user_id, created_at, updated_at FROM games"
        ) as cursor:
            return await cursor.fetchall()

    async def get_all_branches_for_game(self, game_id: int):
        """
        获取指定游戏的所有分支信息。
        
        Args:
            game_id: 游戏ID
            
        Returns:
            list[aiosqlite.Row]: 该游戏的所有分支记录
            
        Raises:
            RuntimeError: 如果数据库未连接
        """
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.execute(
            "SELECT * FROM branches WHERE game_id = ?", (game_id,)
        ) as cursor:
            return await cursor.fetchall()

    async def get_branch_by_name(self, game_id: int, branch_name: str):
        """
        通过名称获取指定游戏的分支信息。
        
        Args:
            game_id: 游戏ID
            branch_name: 分支名称
            
        Returns:
            aiosqlite.Row | None: 分支记录，如果不存在则返回 None
            
        Raises:
            RuntimeError: 如果数据库未连接
        """
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.execute(
            "SELECT * FROM branches WHERE game_id = ? AND name = ?",
            (game_id, branch_name),
        ) as cursor:
            return await cursor.fetchone()

    async def get_branch_by_id(self, branch_id: int):
        """
        通过 branch_id 获取分支信息。
        
        Args:
            branch_id: 分支ID
            
        Returns:
            aiosqlite.Row | None: 分支记录，如果不存在则返回 None
            
        Raises:
            RuntimeError: 如果数据库未连接
        """
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.execute(
            "SELECT * FROM branches WHERE branch_id = ?",
            (branch_id,),
        ) as cursor:
            return await cursor.fetchone()

    async def get_all_rounds_for_game(self, game_id: int):
        """
        获取指定游戏的所有回合信息。
        
        Args:
            game_id: 游戏ID
            
        Returns:
            list[aiosqlite.Row]: 该游戏的所有回合记录（仅包含 round_id 和 parent_id）
            
        Raises:
            RuntimeError: 如果数据库未连接
        """
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.execute(
            "SELECT round_id, parent_id FROM rounds WHERE game_id = ?", (game_id,)
        ) as cursor:
            return await cursor.fetchall()

    async def create_tag(self, game_id: int, name: str, round_id: int) -> int:
        """创建新标签并返回 tag_id"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.transaction():
            async with self.conn.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO tags (game_id, name, round_id) VALUES (?, ?, ?)",
                    (game_id, name, round_id),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("插入标签数据失败")
                return cursor.lastrowid

    async def get_tag_by_name(self, game_id: int, name: str):
        """
        通过名称获取标签信息。
        
        Args:
            game_id: 游戏ID
            name: 标签名称
            
        Returns:
            aiosqlite.Row | None: 标签记录，如果不存在则返回 None
            
        Raises:
            RuntimeError: 如果数据库未连接
        """
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.execute(
            "SELECT * FROM tags WHERE game_id = ? AND name = ?",
            (game_id, name),
        ) as cursor:
            return await cursor.fetchone()

    async def get_all_tags_for_game(self, game_id: int):
        """
        获取指定游戏的所有标签信息。
        
        Args:
            game_id: 游戏ID
            
        Returns:
            list[aiosqlite.Row]: 该游戏的所有标签记录
            
        Raises:
            RuntimeError: 如果数据库未连接
        """
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.execute(
            "SELECT * FROM tags WHERE game_id = ?", (game_id,)
        ) as cursor:
            return await cursor.fetchall()

    async def delete_tag(self, game_id: int, name: str):
        """删除标签"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.transaction():
            async with self.conn.cursor() as cursor:
                await cursor.execute(
                    "DELETE FROM tags WHERE game_id = ? AND name = ?", (game_id, name)
                )

    async def attach_game_to_channel(self, game_id: int, channel_id: str):
        """将游戏附加到频道"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.transaction():
            async with self.conn.cursor() as cursor:
                await cursor.execute(
                    "UPDATE games SET channel_id = ? WHERE game_id = ?",
                    (channel_id, game_id),
                )

    async def detach_game_from_channel(self, game_id: int):
        """从频道分离游戏，并清空频道相关的字段"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.transaction():
            async with self.conn.cursor() as cursor:
                await cursor.execute(
                    """UPDATE games SET
                        channel_id = NULL,
                        main_message_id = NULL,
                        candidate_custom_input_ids = '[]'
                       WHERE game_id = ?""",
                    (game_id,),
                )

    async def update_game_host(self, game_id: int, new_host_id: str):
        """更新游戏的主持人"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.transaction():
            async with self.conn.cursor() as cursor:
                await cursor.execute(
                    "UPDATE games SET host_user_id = ? WHERE game_id = ?",
                    (new_host_id, game_id),
                )

    async def get_round_ancestors(self, round_id: int, limit: int = 10) -> list[aiosqlite.Row]:
        """
        获取一个回合及其祖先，按时间正序排列（从最早的祖先到当前回合）。
        
        使用递归 CTE 一次性查询所有祖先，性能优于逐个查询。
        
        Args:
            round_id: 起始回合ID
            limit: 最多返回的祖先数量（包括起始回合）
            
        Returns:
            list[aiosqlite.Row]: 祖先回合列表，按时间正序排列
            
        Raises:
            RuntimeError: 如果数据库未连接
        """
        if not self.conn:
            raise RuntimeError("数据库未连接")
        
        # 使用递归 CTE 一次性获取所有祖先
        query = """
        WITH RECURSIVE ancestors AS (
            SELECT *, 0 as depth 
            FROM rounds 
            WHERE round_id = ?
            
            UNION ALL
            
            SELECT r.*, a.depth + 1 
            FROM rounds r 
            JOIN ancestors a ON r.round_id = a.parent_id
            WHERE a.parent_id != -1 AND a.depth < ?
        )
        SELECT * FROM ancestors ORDER BY depth DESC;
        """
        
        async with self.conn.execute(query, (round_id, limit - 1)) as cursor:
            rows = await cursor.fetchall()
            return list(rows)
