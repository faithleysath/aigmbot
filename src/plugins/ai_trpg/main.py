from ncatbot.plugin_system import NcatBotPlugin, command_registry, on_notice, filter_registry
from ncatbot.core.event import GroupMessageEvent, NoticeEvent
from ncatbot.core.event.message_segment import File
from ncatbot.utils import get_log
from pathlib import Path
import aiohttp

from .db import Database
from .llm_api import LLM_API
from .renderer import MarkdownRenderer

LOG = get_log(__name__)

import base64

def bytes_to_base64(bytes: bytes) -> str:
    """将字节数据转换为Base64字符串"""
    return base64.b64encode(bytes).decode('utf-8')


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

    @filter_registry.group_filter
    async def listen_file_message(self, event: GroupMessageEvent):
        """监听群文件消息，检查是否是.txt或.md文件"""
        files = event.message.filter(File)
        if not files:
            return
        # 取第一个文件
        file = files[0]
        if not file.file.endswith((".txt", ".md")):
            return
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(file.url) as response:
                    if response.status == 200:
                        content = await response.text()
                        preview = content[:500]
                        # 交给markdown渲染器处理，或直接输出预览
                        if self.renderer:
                            img: bytes | None = await self.renderer.render(preview)
                            if img:
                                await event.reply(image=f"data:image/png;base64,{bytes_to_base64(img)}")
                            else:
                                await event.reply(f"文件预览:\n\n{preview}")
                        else:
                            await event.reply(f"文件预览:\n\n{preview}")
                    else:
                        LOG.warning(f"下载文件预览失败，状态码: {response.status}")
                        await event.reply("无法获取文件预览。")
        except Exception as e:
            LOG.error(f"下载或读取文件预览时出错: {e}")
            await event.reply("无法获取文件预览。")