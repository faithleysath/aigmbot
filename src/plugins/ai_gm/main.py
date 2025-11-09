from ncatbot.plugin_system import (
    NcatBotPlugin,
    on_notice,
    filter_registry,
    command_registry,
)
from ncatbot.core.event import GroupMessageEvent, NoticeEvent
from ncatbot.core.event.message_segment import At
from ncatbot.utils import get_log
from pathlib import Path

from .db import Database
from .llm_api import LLM_API
from .renderer import MarkdownRenderer
from .cache import CacheManager
from .game_manager import GameManager
from .event_handler import EventHandler
from .content_fetcher import ContentFetcher
from .commands import CommandHandler
from .visualizer import Visualizer
from .web_ui import WebUI
import asyncio

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
        self.command_handler: CommandHandler | None = None
        self.visualizer: Visualizer | None = None
        self.web_ui: WebUI | None = None
        self.web_ui_task: asyncio.Task | None = None
        self.data_path: Path = Path()

    async def on_load(self):
        """插件加载时执行的初始化操作"""
        LOG.info(f"[{self.name}] 正在加载...")

        # 1. 注册配置项
        self.register_config("openai_api_key", "YOUR_API_KEY_HERE")
        self.register_config("openai_base_url", "https://api.openai.com/v1")
        self.register_config("openai_model_name", "gpt-4-turbo")
        self.register_config("openai_max_retries", 2, "LLM API 最大重试次数")
        self.register_config("openai_base_delay", 1.0, "LLM API 基础重试延迟（秒）")
        self.register_config("openai_max_delay", 30.0, "LLM API 最大重试延迟（秒）")
        self.register_config("openai_timeout", 60.0, "LLM API 调用超时时间（秒）")
        self.register_config("pending_game_timeout", 300, "新游戏等待确认的超时时间（秒）")
        # TODO: 实现并发渲染限制机制
        self.register_config("max_concurrent_renders", 3, "最大并发渲染数量（预留配置）")
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
            max_retries = int(self.config.get("openai_max_retries", 2))
            base_delay = float(self.config.get("openai_base_delay", 1.0))
            max_delay = float(self.config.get("openai_max_delay", 30.0))
            timeout = float(self.config.get("openai_timeout", 60.0))
            
            self.llm_api = LLM_API(
                api_key=api_key,
                base_url=base_url,
                model_name=model_name,
                max_retries=max_retries,
                base_delay=base_delay,
                max_delay=max_delay,
                timeout=timeout,
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
            self.web_ui = WebUI(self.db, data_dir)
            self.web_ui_task = asyncio.create_task(self.web_ui.run_in_background())

            self.visualizer = Visualizer(self.db)
            content_fetcher = ContentFetcher(self, self.cache_manager)
            self.game_manager = GameManager(
                self,
                self.db,
                self.llm_api,
                self.renderer,
                self.cache_manager,
                content_fetcher,
            )
            self.command_handler = CommandHandler(
                self,
                self.db,
                self.game_manager,
                self.cache_manager,
                self.visualizer,
                self.renderer,
                web_ui=self.web_ui,
            )
            self.event_handler = EventHandler(
                self,
                self.db,
                self.cache_manager,
                self.game_manager,
                self.renderer,
                content_fetcher,
                self.command_handler,
            )
        else:
            LOG.error(f"[{self.name}] 部分组件初始化失败，插件功能可能不完整。")

        LOG.info(f"[{self.name}] 加载完成。")

    async def on_close(self):
        """插件关闭时执行的操作"""
        if self.web_ui_task and not self.web_ui_task.done():
            self.web_ui_task.cancel()
            try:
                await self.web_ui_task
            except asyncio.CancelledError:
                LOG.info("Web UI task cancelled.")
        if self.cache_manager:
            await self.cache_manager.shutdown()
        if self.db:
            await self.db.close()
        if self.renderer:
            await self.renderer.close()
        LOG.info(f"[{self.name}] 已卸载。")

    @filter_registry.group_filter
    async def handle_group_message(self, event: GroupMessageEvent):
        if self.event_handler:
            await self.event_handler.handle_group_message(event)

    @on_notice
    async def handle_emoji_reaction(self, event: NoticeEvent):
        if self.event_handler:
            await self.event_handler.handle_emoji_reaction(event)

    @on_notice
    async def handle_message_retraction(self, event: NoticeEvent):
        if self.event_handler:
            await self.event_handler.handle_message_retraction(event)

    aigm_group = command_registry.group("aigm", description="AI GM 游戏插件命令")

    @aigm_group.command("", aliases=["help"], description="显示帮助信息")  # 默认命令
    async def aigm_help(self, event: GroupMessageEvent):
        if self.command_handler:
            await self.command_handler.handle_help(event)

    @aigm_group.command("help", description="显示帮助信息")
    async def aigm_help_alias(self, event: GroupMessageEvent):
        if self.command_handler:
            await self.command_handler.handle_help(event)

    @aigm_group.command("status", description="查看当前游戏状态")
    async def aigm_status(self, event: GroupMessageEvent):
        if self.command_handler:
            await self.command_handler.handle_status(event, self.api)

    @aigm_group.command("webui", description="获取 Web UI 地址")
    async def aigm_webui(self, event: GroupMessageEvent):
        if self.command_handler:
            await self.command_handler.handle_webui(event)

    # --- Branch Subcommands ---
    branch_group = aigm_group.group("branch", description="分支管理")

    @branch_group.command("list", description="可视化显示当前游戏的分支")
    async def aigm_branch_list(self, event: GroupMessageEvent, mode: str = ""):
        if self.command_handler:
            await self.command_handler.handle_branch_list(event, mode)

    @branch_group.command("show", description="查看指定分支顶端的内容")
    async def aigm_branch_show(self, event: GroupMessageEvent, branch_name: str):
        if self.command_handler:
            await self.command_handler.handle_branch_show(event, branch_name)

    @branch_group.command("history", description="查看指定分支的历史记录")
    async def aigm_branch_history(self, event: GroupMessageEvent, branch_name: str = "", limit: int = 10):
        if self.command_handler:
            await self.command_handler.handle_branch_history(event, branch_name, limit)

    @branch_group.command("create", description="创建新分支")
    async def aigm_branch_create(
        self, event: GroupMessageEvent, name: str, from_round_id: int = -1
    ):
        if name.lower() == "head":
            await event.reply("❌ 'head' 是一个保留关键字，不能用作分支名称。", at=False)
            return
        if self.command_handler:
            actual_from_round_id = from_round_id if from_round_id != -1 else None
            await self.command_handler.handle_branch_create(
                event, name, actual_from_round_id
            )

    @branch_group.command("rename", description="重命名分支")
    async def aigm_branch_rename(self, event: GroupMessageEvent, old_name: str, new_name: str):
        if new_name.lower() == "head":
            await event.reply("❌ 'head' 是一个保留关键字，不能用作分支名称。", at=False)
            return
        if self.command_handler:
            await self.command_handler.handle_branch_rename(event, old_name, new_name)

    @branch_group.command("delete", description="删除分支")
    async def aigm_branch_delete(self, event: GroupMessageEvent, name: str):
        if self.command_handler:
            await self.command_handler.handle_branch_delete(event, name)

    # --- Tag Subcommands ---
    tag_group = aigm_group.group("tag", description="标签管理")

    @tag_group.command("create", description="创建新标签")
    async def aigm_tag_create(
        self, event: GroupMessageEvent, name: str, round_id: int = -1
    ):
        if self.command_handler:
            actual_round_id = round_id if round_id != -1 else None
            await self.command_handler.handle_tag_create(event, name, actual_round_id)

    @tag_group.command("list", description="列出所有标签")
    async def aigm_tag_list(self, event: GroupMessageEvent):
        if self.command_handler:
            await self.command_handler.handle_tag_list(event)

    @tag_group.command("show", description="查看标签指向的回合")
    async def aigm_tag_show(self, event: GroupMessageEvent, name: str):
        if self.command_handler:
            await self.command_handler.handle_tag_show(event, name)

    @tag_group.command("history", description="查看标签指向的回合的历史记录")
    async def aigm_tag_history(self, event: GroupMessageEvent, name: str, limit: int = 10):
        if self.command_handler:
            await self.command_handler.handle_tag_history(event, name, limit)

    @tag_group.command("delete", description="删除标签")
    async def aigm_tag_delete(self, event: GroupMessageEvent, name: str):
        if self.command_handler:
            await self.command_handler.handle_tag_delete(event, name)

    # --- Round Subcommands ---
    round_group = aigm_group.group("round", description="回合管理")

    @round_group.command("show", description="查看指定回合的内容")
    async def aigm_round_show(self, event: GroupMessageEvent, round_id: int):
        if self.command_handler:
            await self.command_handler.handle_round_show(event, round_id)

    @round_group.command("history", description="查看指定回合及其历史记录")
    async def aigm_round_history(self, event: GroupMessageEvent, round_id: int, limit: int = 10):
        if self.command_handler:
            await self.command_handler.handle_round_history(event, round_id, limit)

    # --- Game Subcommands ---
    game_group = aigm_group.group("game", description="游戏管理")

    @game_group.command("list", description="列出所有游戏")
    async def aigm_game_list(self, event: GroupMessageEvent):
        if self.command_handler:
            await self.command_handler.handle_game_list(event)

    @game_group.command("attach", description="将游戏附加到当前频道")
    async def aigm_game_attach(self, event: GroupMessageEvent, game_id: int):
        if self.command_handler:
            await self.command_handler.handle_game_attach(event, game_id)

    @game_group.command("detach", description="从当前频道分离游戏")
    async def aigm_game_detach(self, event: GroupMessageEvent):
        if self.command_handler:
            await self.command_handler.handle_game_detach(event)

    @game_group.command("sethost", description="变更当前频道游戏的主持人")
    async def aigm_game_set_host(self, event: GroupMessageEvent, at_user: At):
        if self.command_handler:
            await self.command_handler.handle_game_set_host(
                event, new_host_id=at_user.qq
            )

    @game_group.command("sethost-by-id", description="根据ID变更游戏主持人")
    async def aigm_game_set_host_by_id(
        self, event: GroupMessageEvent, game_id: int, at_user: At
    ):
        if self.command_handler:
            await self.command_handler.handle_game_set_host(
                event, new_host_id=at_user.qq, game_id=game_id
            )

    # --- Checkout Command ---
    @aigm_group.command(
        "checkout", aliases=["co"], description="切换到指定分支或重新加载HEAD"
    )
    async def aigm_checkout(self, event: GroupMessageEvent, target: str):
        if self.command_handler:
            if target.lower() == "head":
                await self.command_handler.handle_checkout_head(event)
            else:
                await self.command_handler.handle_checkout(event, target)

    @aigm_group.command("reset", description="将当前分支重置到指定回合")
    async def aigm_reset(self, event: GroupMessageEvent, round_id: int):
        if self.command_handler:
            await self.command_handler.handle_reset(event, round_id)

    # --- Admin Subcommands ---
    admin_group = aigm_group.group("admin", description="管理员命令")

    @admin_group.command("unfreeze", description="强制解冻当前游戏")
    async def aigm_admin_unfreeze(self, event: GroupMessageEvent):
        if self.command_handler:
            await self.command_handler.handle_admin_unfreeze(event)

    @admin_group.command("delete", description="[ROOT] 删除指定ID的游戏")
    async def aigm_admin_delete_game(self, event: GroupMessageEvent, game_id: int):
        if self.command_handler:
            await self.command_handler.handle_admin_delete_game(event, game_id)

    # --- Cache Subcommands ---
    cache_group = aigm_group.group("cache", description="缓存管理")
    pending_group = cache_group.group("pending", description="待处理游戏缓存")

    @pending_group.command("clear", description="清空待处理的新游戏请求")
    async def aigm_cache_pending_clear(self, event: GroupMessageEvent):
        if self.command_handler:
            await self.command_handler.handle_cache_pending_clear(event)
