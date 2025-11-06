from ncatbot.plugin_system import (
    NcatBotPlugin,
    on_notice,
    filter_registry,
)
from ncatbot.core.event import GroupMessageEvent, NoticeEvent
from ncatbot.utils import get_log
from pathlib import Path

from .db import Database
from .llm_api import LLM_API
from .renderer import MarkdownRenderer
from .cache import CacheManager
from .game_manager import GameManager
from .event_handler import EventHandler
from .content_fetcher import ContentFetcher

LOG = get_log(__name__)


class AIGMPlugin(NcatBotPlugin):
    name = "AIGMPlugin"
    version = "1.0.0"
    description = "一个基于 AI GM 和 Git 版本控制概念的互动叙事游戏插件"
    author = "Cline"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.db: Database | None = None
        self.llm_api: LLM_API | None = None
        self.renderer: MarkdownRenderer | None = None
        self.cache_manager: CacheManager | None = None
        self.game_manager: GameManager | None = None
        self.event_handler: EventHandler | None = None
        self.data_path: Path = Path()

    async def on_load(self):
        """插件加载时执行的初始化操作"""
        LOG.info(f"[{self.name}] 正在加载...")

        # 1. 注册配置项
        self.register_config("openai_api_key", "YOUR_API_KEY_HERE")
        self.register_config("openai_base_url", "https://api.openai.com/v1")
        self.register_config("openai_model_name", "gpt-4-turbo")
        self.register_config("pending_game_timeout", 300, "新游戏等待确认的超时时间（秒）")
        LOG.debug(f"[{self.name}] 配置项注册完毕。")

        # 2. 初始化路径和数据库
        data_dir = self.data_path / "data" / "AIGMPlugin"
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "ai_gm.db"
        cache_path = data_dir / "cache.json"

        self.db = Database(str(db_path))
        await self.db.connect()
        LOG.debug(f"[{self.name}] 数据库连接成功。")

        # 3. 初始化LLM API
        try:
            api_key = self.config.get("openai_api_key", "")
            if not api_key or api_key == "YOUR_API_KEY_HERE":
                raise ValueError("OpenAI API key is not configured.")
            base_url = self.config.get("openai_base_url", "https://api.openai.com/v1")
            model_name = self.config.get("openai_model_name", "gpt-4-turbo")
            self.llm_api = LLM_API(
                api_key=api_key, base_url=base_url, model_name=model_name
            )
        except ValueError as e:
            LOG.error(f"LLM API 初始化失败: {e}")

        # 4. 初始化渲染器
        self.renderer = MarkdownRenderer()
        LOG.debug(f"[{self.name}] Markdown渲染器初始化完成。")

        # 5. 初始化管理器
        self.cache_manager = CacheManager(cache_path)
        await self.cache_manager.load_from_disk()

        if self.db and self.llm_api and self.renderer and self.cache_manager:
            content_fetcher = ContentFetcher(self, self.cache_manager)
            self.game_manager = GameManager(
                self,
                self.db,
                self.llm_api,
                self.renderer,
                self.cache_manager,
                content_fetcher,
            )
            self.event_handler = EventHandler(
                self,
                self.db,
                self.cache_manager,
                self.game_manager,
                self.renderer,
                content_fetcher,
            )
        else:
            LOG.error(f"[{self.name}] 部分组件初始化失败，插件功能可能不完整。")

        LOG.info(f"[{self.name}] 加载完成。")

    async def on_close(self):
        """插件关闭时执行的操作"""
        if self.cache_manager:
            await self.cache_manager.save_to_disk()
        if self.db:
            await self.db.close()
        if self.renderer:
            # await self.renderer.close()
            pass # 因为 MarkdownRenderer 目前没有异步关闭操作，会报错
        LOG.info(f"[{self.name}] 已卸载。")

    @filter_registry.group_filter
    async def handle_group_message(self, event: GroupMessageEvent):
        if self.event_handler:
            await self.event_handler.handle_group_message(event)

    @on_notice
    async def handle_emoji_reaction(self, event: NoticeEvent):
        if self.event_handler:
            await self.event_handler.handle_emoji_reaction(event)
