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
                    group_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    messages_history TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 创建 rounds 表
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS rounds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_group_id TEXT NOT NULL,
                    round_number INTEGER NOT NULL,
                    main_message_id TEXT UNIQUE NOT NULL,
                    assistant_response TEXT NOT NULL,
                    winning_choice TEXT,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (game_group_id) REFERENCES games (group_id) ON DELETE CASCADE
                );
            """)

            # 创建 custom_inputs 表
            await cursor.execute("""
                CREATE TABLE IF NOT EXISTS custom_inputs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    round_id INTEGER NOT NULL,
                    user_id TEXT NOT NULL,
                    message_id TEXT UNIQUE NOT NULL,
                    content TEXT NOT NULL,
                    is_retracted BOOLEAN NOT NULL DEFAULT 0,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (round_id) REFERENCES rounds (id) ON DELETE CASCADE
                );
            """)
            
            # 创建触发器，用于自动更新 games 表的 updated_at
            await cursor.execute("""
                CREATE TRIGGER IF NOT EXISTS update_game_updated_at
                AFTER UPDATE ON games
                FOR EACH ROW
                BEGIN
                    UPDATE games SET updated_at = CURRENT_TIMESTAMP WHERE group_id = OLD.group_id;
                END;
            """)

        if self.conn:
            await self.conn.commit()
