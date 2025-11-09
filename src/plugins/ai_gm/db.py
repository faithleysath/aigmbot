import aiosqlite
from ncatbot.utils import get_log
from contextlib import asynccontextmanager
import itertools

LOG = get_log(__name__)


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None
        self._savepoint_counter = itertools.count()

    async def connect(self):
        """连接到数据库并进行初始化"""
        try:
            self.conn = await aiosqlite.connect(self.db_path)
            self.conn.row_factory = aiosqlite.Row
            await self.conn.execute("PRAGMA journal_mode=WAL;")
            await self.conn.execute("PRAGMA synchronous=NORMAL;")
            await self.conn.execute("PRAGMA foreign_keys = ON;")
            await self.conn.execute("PRAGMA busy_timeout=5000;")  # 5s
            await self.conn.execute("PRAGMA wal_autocheckpoint=2000;")
            await self.init_db()
            LOG.info(f"成功连接并初始化数据库: {self.db_path}")
        except aiosqlite.Error as e:
            LOG.error(f"数据库连接失败: {e}")
            raise

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

        if self.conn:
            await self.conn.commit()

    @asynccontextmanager
    async def transaction(self):
        """Provides a transaction context manager with savepoint support for nesting."""
        if not self.conn:
            raise RuntimeError("数据库未连接")

        if getattr(self.conn, "in_transaction", False):
            # Nested transaction: use savepoints
            savepoint_name = f"sp_{next(self._savepoint_counter)}"
            try:
                await self.conn.execute(f"SAVEPOINT {savepoint_name};")
                yield
                await self.conn.execute(f"RELEASE SAVEPOINT {savepoint_name};")
            except Exception:
                await self.conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name};")
                await self.conn.execute(f"RELEASE SAVEPOINT {savepoint_name};")
                raise
        else:
            # Top-level transaction
            try:
                await self.conn.execute("BEGIN IMMEDIATE;")
                yield
                await self.conn.commit()
            except Exception:
                await self.conn.rollback()
                raise

    async def is_game_running(self, channel_id: str) -> bool:
        """检查指定频道当前是否有正在进行的游戏"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT 1 FROM games WHERE channel_id = ?", (channel_id,)
            )
            result = await cursor.fetchone()
            return result is not None

    async def get_game_by_channel_id(self, channel_id: str):
        """通过 channel_id 获取游戏信息"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT * FROM games WHERE channel_id = ?", (channel_id,)
            )
            return await cursor.fetchone()

    async def get_game_by_game_id(self, game_id: int):
        """通过 game_id 获取游戏信息"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT * FROM games WHERE game_id = ?", (game_id,)
            )
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
        """获取游戏主持人ID"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT host_user_id FROM games WHERE channel_id = ?", (channel_id,)
            )
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
        """获取游戏和 head 分支信息"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.cursor() as cursor:
            await cursor.execute(
                """SELECT g.channel_id, b.tip_round_id
                   FROM games g
                   LEFT JOIN branches b ON g.head_branch_id = b.branch_id
                   WHERE g.game_id = ?""",
                (game_id,),
            )
            row = await cursor.fetchone()
            if not row or row["tip_round_id"] is None:
                raise RuntimeError("游戏 head 分支未设置或已损坏")
            return row

    async def get_round_info(self, round_id: int):
        """获取回合信息"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.cursor() as cursor:
            await cursor.execute("SELECT * FROM rounds WHERE round_id = ?", (round_id,))
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

    async def delete_game(self, game_id: int):
        """删除游戏"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.transaction():
            async with self.conn.cursor() as cursor:
                await cursor.execute("DELETE FROM games WHERE game_id = ?", (game_id,))

    async def get_all_games(self):
        """获取所有游戏的信息"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.cursor() as cursor:
            await cursor.execute("SELECT game_id, channel_id, host_user_id, created_at, updated_at FROM games")
            return await cursor.fetchall()

    async def get_all_branches_for_game(self, game_id: int):
        """获取指定游戏的所有分支信息"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.cursor() as cursor:
            await cursor.execute("SELECT * FROM branches WHERE game_id = ?", (game_id,))
            return await cursor.fetchall()

    async def get_branch_by_name(self, game_id: int, branch_name: str):
        """通过名称获取指定游戏的分支信息"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT * FROM branches WHERE game_id = ? AND name = ?",
                (game_id, branch_name),
            )
            return await cursor.fetchone()

    async def get_branch_by_id(self, branch_id: int):
        """通过 branch_id 获取分支信息"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT * FROM branches WHERE branch_id = ?",
                (branch_id,),
            )
            return await cursor.fetchone()

    async def get_all_rounds_for_game(self, game_id: int):
        """获取指定游戏的所有回合信息"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        async with self.conn.cursor() as cursor:
            await cursor.execute("SELECT round_id, parent_id FROM rounds WHERE game_id = ?", (game_id,))
            return await cursor.fetchall()

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
        """获取一个回合及其祖先，按时间倒序排列"""
        if not self.conn:
            raise RuntimeError("数据库未连接")
        
        ancestors = []
        current_id = round_id
        for _ in range(limit):
            if current_id == -1:
                break
            async with self.conn.cursor() as cursor:
                await cursor.execute("SELECT * FROM rounds WHERE round_id = ?", (current_id,))
                round_data = await cursor.fetchone()
                if not round_data:
                    break
                ancestors.append(round_data)
                current_id = round_data["parent_id"]
                
        return list(reversed(ancestors))
