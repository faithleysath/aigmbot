from ncatbot.plugin_system import NcatBotPlugin, on_notice
from ncatbot.core.event import NoticeEvent
from ncatbot.core.event.message_segment import At, Text, Reply, MessageArray
from ncatbot.utils import get_log

LOG = get_log(__name__)

class EmojiNotifierPlugin(NcatBotPlugin):
    name = "EmojiNotifierPlugin"
    version = "1.0.0"
    description = "监听群聊表情回应并发送通知"
    author = "Cline"

    async def on_load(self):
        LOG.info(f"{self.name} 已加载。")

    @on_notice
    async def handle_emoji_like(self, event: NoticeEvent):
        """处理群消息表情回应通知"""
        if event.notice_type == 'group_msg_emoji_like':
            group_id = event.group_id
            user_id = event.user_id  # 操作者（贴表情/撤销表情的人）
            target_message_id = event.message_id  # 被贴表情的消息
            emoji_id = event.emoji_like_id
            is_add = event.is_add

            if not target_message_id:
                LOG.warning("收到了没有 message_id 的 group_msg_emoji_like 事件")
                return

            action_text = "贴上了" if is_add else "撤销了"
            
            # 构建回复消息
            message_to_send = MessageArray([
                Reply(target_message_id),  # 回复被贴表情的消息
                At(user_id),
                Text(f" {action_text}一个表情: {emoji_id}")
            ])

            try:
                # 使用 post_group_array_msg 发送包含多个片段的消息
                await self.api.post_group_array_msg(group_id, message_to_send)
            except Exception as e:
                LOG.error(f"发送表情回应通知失败: {e}")
