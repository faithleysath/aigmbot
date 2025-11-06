from ncatbot.plugin_system import NcatBotPlugin, command_registry, on_notice, filter_registry
from ncatbot.core.event import GroupMessageEvent, NoticeEvent
from ncatbot.utils import get_log
from pathlib import Path

from .db import Database
from .llm_api import LLM_API
from .renderer import MarkdownRenderer

LOG = get_log(__name__)

class AITRPGPlugin(NcatBotPlugin):
    name = "AITRPGPlugin"
    version = "1.0.0"
    description = "一个基于 AI GM 和 Git 版本控制概念的互动叙事游戏插件"
    author = "Cline"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.db: Database | None = None
        self.llm_api: LLM_API | None = None
        self.renderer: MarkdownRenderer | None = None
        self.data_path: Path = Path()

    async def on_load(self):
        """插件加载时执行的初始化操作"""
        LOG.info(f"[{self.name}] 正在加载...")
        
        # 1. 注册配置项 (示例)
        self.register_config("openai_api_key", "YOUR_API_KEY_HERE")
        self.register_config("openai_base_url", "https://api.openai.com/v1")
        self.register_config("openai_model_name", "gpt-4-turbo")
        LOG.debug(f"[{self.name}] 配置项注册完毕。")

        # 2. 初始化数据库
        db_path = self.data_path / "data" / "AITRPGPlugin" / "ai_trpg.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = Database(str(db_path))
        await self.db.connect()
        LOG.debug(f"[{self.name}] 数据库连接成功。")

        # 3. 初始化 LLM API
        try:
            api_key = self.config.get("openai_api_key", "")
            base_url = self.config.get("openai_base_url", "https://api.openai.com/v1")
            model_name = self.config.get("openai_model_name", "gpt-4-turbo")
            self.llm_api = LLM_API(api_key=api_key, base_url=base_url, model_name=model_name)
        except ValueError as e:
            LOG.error(f"LLM API 初始化失败: {e}. 请检查相关配置。")
            self.llm_api = None

        # 4. 初始化 Markdown 渲染器
        self.renderer = MarkdownRenderer()
        LOG.debug(f"[{self.name}] Markdown渲染器初始化完成。")
        
        LOG.info(f"[{self.name}] 加载完成。")

    async def on_close(self):
        """插件关闭时执行的操作"""
        if self.db:
            await self.db.close()
        LOG.info(f"[{self.name}] 已卸载。")

    # --- 核心游戏逻辑 (待实现) ---

    