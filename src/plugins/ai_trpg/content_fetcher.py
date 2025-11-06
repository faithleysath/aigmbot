# src/plugins/ai_trpg/content_fetcher.py
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
        # 确保是引用对象，而不是临时副本
        group_vote_cache = self.cache_manager.vote_cache.setdefault(group_id, {})
        item_cache = group_vote_cache.get(message_id, {})
        content = item_cache.get("content", "")

        if not content:
            try:
                msg_event = await self.api.get_msg(message_id)
                content = "".join(s.text for s in msg_event.message.filter_text())
                # 统一用 setdefault，避免 KeyError 并确保写回
                entry = group_vote_cache.setdefault(message_id, {"votes": {}})
                entry["content"] = content
                await self.cache_manager.save_to_disk()
            except Exception as e:
                LOG.warning(f"获取消息 {message_id} 内容失败: {e}")
                content = f"自定义输入 (ID: {message_id})"
        return content
