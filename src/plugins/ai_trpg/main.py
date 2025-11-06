from ncatbot.plugin_system import NcatBotPlugin, command_registry, on_notice, filter_registry
from ncatbot.core.event import GroupMessageEvent, NoticeEvent
from ncatbot.core.event.message_segment import File
from ncatbot.utils import get_log
from pathlib import Path
import aiohttp
from datetime import datetime, timedelta

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
        self.pending_new_games: dict[str, dict] = {} # key是message_id，value是{"user_id": str, "system_prompt": str, "message_id": str, create_time: datetime}

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

        file = files[0]
        if not file.file.endswith((".txt", ".md")):
            return

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(file.url) as response:
                    if response.status != 200:
                        await event.reply("无法获取文件内容。", at=False)
                        return
                    content = await response.text()

            preview = content[:2000]
            img: bytes | None = None
            if self.renderer:
                img = await self.renderer.render(preview)

            reply_message_id = None
            if img:
                reply_message_id = await event.reply(image=f"data:image/png;base64,{bytes_to_base64(img)}", at=False)
            else:
                reply_message_id = await event.reply(f"文件预览:\n\n{preview}", at=False)

            if not reply_message_id:
                return

            if self.db and await self.db.is_game_running(str(event.group_id)):
                await self.api.set_msg_emoji_like(reply_message_id, "9749")
            else:
                await self.api.set_msg_emoji_like(reply_message_id, "127881")
            self.pending_new_games[reply_message_id] = {
                "user_id": event.user_id,
                "system_prompt": content,
                "message_id": event.message_id,
                "create_time": datetime.now(),
            }
        except Exception as e:
            LOG.error(f"处理文件消息时出错: {e}")
            await event.reply("处理文件时出错。", at=False)

    @on_notice
    async def on_emoji_like(self, event: NoticeEvent):
            
        if event.notice_type != "group_msg_emoji_like":
            return

        # Clean up expired pending games
        now = datetime.now()
        expired_games = [
            msg_id for msg_id, game_data in self.pending_new_games.items()
            if now - game_data["create_time"] > timedelta(minutes=5)
        ]
        for msg_id in expired_games:
            del self.pending_new_games[msg_id]

        if not event.message_id:
            return

        pending_game = self.pending_new_games.get(event.message_id)
        if not pending_game:
            return

        if pending_game["user_id"] != event.user_id:
            return

        if event.emoji_like_id == "9749":
            try:
                await self.api.delete_msg(pending_game["message_id"])
                await self.api.set_msg_emoji_like(event.message_id, "127881", set=False)
                await self.api.set_msg_emoji_like(event.message_id, "9749")
                await self.api.post_group_msg(event.group_id, "新游戏创建已取消。", at=event.user_id, reply=event.message_id)
                LOG.info(f"用户 {event.user_id} 取消了新游戏创建请求。删除消息 {pending_game['message_id']}")
            except Exception as e:
                LOG.error(f"删除消息失败: {e}")
            finally:
                del self.pending_new_games[event.message_id]
        elif event.emoji_like_id == "127881":
            # 游戏开始
            await self.api.post_group_msg(event.group_id, "新的 AI TRPG 游戏已创建！请稍候，正在初始化游戏环境...", at=event.user_id, reply=event.message_id)
            LOG.info(f"用户 {event.user_id} 确认创建新游戏。初始化游戏环境中...")
            # TODO
