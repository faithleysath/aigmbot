from ncatbot.plugin_system import NcatBotPlugin
from ncatbot.utils import get_log

from .cache import CacheManager

LOG = get_log(__name__)


class ContentFetcher:
    def __init__(self, plugin: NcatBotPlugin, cache_manager: CacheManager):
        self.api = plugin.api
        self.cache_manager = cache_manager

    async def get_custom_input_content(self, group_id: str, message_id: str) -> str:
        """获取自定义输入消息的内容，优先从缓存读取，否则通过API获取并更新缓存"""
        item_cache = await self.cache_manager.get_vote_item(group_id, message_id)
        content = item_cache.get("content", "") if item_cache else ""

        if not content:
            try:
                msg_event = await self.api.get_msg(message_id)
                content = "".join(s.text for s in msg_event.message.filter_text())
                await self.cache_manager.set_custom_input_content(
                    group_id, message_id, content
                )
            except Exception as e:
                LOG.warning(f"获取消息 {message_id} 内容失败: {e}")
                content = f"自定义输入 (ID: {message_id})"
        return content
