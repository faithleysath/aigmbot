import aiosqlite
from ncatbot.utils import get_log

LOG = get_log(__name__)

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = None

    async def connect(self):
        """连接到数据库并进行初始化"""
        try:
            self.conn = await aiosqlite.connect(self.db_path)
            await self.init_db()
            LOG.info(f"成功连接并初始化数据库: {self.db_path}")
        except Exception as e:
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
            # 启用外键约束
            await cursor.execute("PRAGMA foreign_keys = ON;")

            # 创建 games 表
            await cursor.execute("""
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
            """)

            # 创建 branches 表
            await cursor.execute("""
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
            """)

            # 创建 rounds 表
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS rounds (
                    round_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL,
                    parent_id INTEGER NOT NULL CHECK(parent_id >= -1),
                    player_choice TEXT NOT NULL,
                    assistant_response TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_id) REFERENCES games (game_id) ON DELETE CASCADE
                );
            """)
            
            # 创建触发器，用于自动更新 games 表的 updated_at
            await cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS update_game_updated_at
                AFTER UPDATE ON games
                FOR EACH ROW
                WHEN NEW.updated_at = OLD.updated_at
                BEGIN
                    UPDATE games SET updated_at = CURRENT_TIMESTAMP WHERE game_id = OLD.game_id;
                END;
            """)

            # 创建触发器，用于自动更新 branches 表的 updated_at
            await cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS update_branch_updated_at
                AFTER UPDATE ON branches
                FOR EACH ROW
                WHEN NEW.updated_at = OLD.updated_at
                BEGIN
                    UPDATE branches SET updated_at = CURRENT_TIMESTAMP WHERE branch_id = OLD.branch_id;
                END;
            """)

            # 创建索引
            await cursor.execute("CREATE INDEX IF NOT EXISTS idx_games_channel ON games(channel_id);")
            await cursor.execute("CREATE INDEX IF NOT EXISTS idx_games_main_msg ON games(main_message_id);")
            await cursor.execute("CREATE INDEX IF NOT EXISTS idx_branches_game ON branches(game_id);")
            await cursor.execute("CREATE INDEX IF NOT EXISTS idx_rounds_game ON rounds(game_id);")

        if self.conn:
            await self.conn.commit()

    async def is_game_running(self, channel_id: str) -> bool:
        """检查指定频道当前是否有正在进行的游戏"""
        if not self.conn:
            LOG.error("数据库未连接，无法查询游戏状态。")
            return False
        async with self.conn.cursor() as cursor:
            await cursor.execute("SELECT 1 FROM games WHERE channel_id = ?", (channel_id,))
            result = await cursor.fetchone()
            return result is not None

    async def get_game_by_channel_id(self, channel_id: str):
        """通过 channel_id 获取游戏信息"""
        if not self.conn: return None
        async with self.conn.cursor() as cursor:
            cursor.row_factory = aiosqlite.Row
            await cursor.execute("SELECT * FROM games WHERE channel_id = ?", (channel_id,))
            return await cursor.fetchone()

    async def set_game_frozen_status(self, game_id: int, is_frozen: bool):
        """设置游戏的冻结状态"""
        if not self.conn: return
        async with self.conn.cursor() as cursor:
            await cursor.execute("UPDATE games SET is_frozen = ? WHERE game_id = ?", (is_frozen, game_id))
            await self.conn.commit()
