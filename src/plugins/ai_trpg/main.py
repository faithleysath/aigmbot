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

    @command_registry.command("trpg start", description="开始一场新的 TRPG 游戏")
    async def start_game_command(self, event: GroupMessageEvent):
        """处理 /trpg start 命令，开始新游戏"""
        # 核心逻辑待实现
        await event.reply("新游戏功能待实现...")

    @filter_registry.group_filter
    async def on_group_message(self, event: GroupMessageEvent):
        """处理群聊消息，用于捕获对游戏主消息的回复"""
        # 核心逻辑待实现
        pass

    @on_notice
    async def handle_emoji_reaction(self, event: NoticeEvent):
        """处理表情回应，这是游戏结算和状态变更的核心触发器"""
        # 核心逻辑待实现
        pass

    # --- 辅助方法 (待实现) ---

    async def _create_new_game(self, group_id: str):
        """内部方法，处理新游戏的完整启动流程"""
        # 核心逻辑待实现
        pass

    async def _advance_story(self, group_id: str, choice: str):
        """根据玩家的选择推进故事"""
        # 核心逻辑待实现
        pass

    async def _handle_admin_action(self, group_id: str, action: str):
        """处理管理员的确认、否决、回退等操作"""
        # 核心逻辑待实现
        pass
