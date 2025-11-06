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
                    candidate_custom_inputs TEXT,
                    head_branch_id INTEGER,
                    system_prompt TEXT,
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
                    FOREIGN KEY (tip_round_id) REFERENCES rounds (round_id) ON DELETE CASCADE
                );
            """)

            # 创建 rounds 表
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS rounds (
                    round_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id INTEGER NOT NULL,
                    parent_id INTEGER NOT NULL,
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
                BEGIN
                    UPDATE games SET updated_at = CURRENT_TIMESTAMP WHERE game_id = OLD.game_id;
                END;
            """)

            # 创建触发器，用于自动更新 branches 表的 updated_at
            await cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS update_branch_updated_at
                AFTER UPDATE ON branches
                FOR EACH ROW
                BEGIN
                    UPDATE branches SET updated_at = CURRENT_TIMESTAMP WHERE branch_id = OLD.branch_id;
                END;
            """)

        if self.conn:
            await self.conn.commit()
