from ncatbot.plugin_system import NcatBotPlugin, on_notice, command_registry
from ncatbot.core.event import NoticeEvent, BaseMessageEvent
from ncatbot.core.event.message_segment import At, Text, Reply, MessageArray
from ncatbot.utils import get_log

LOG = get_log(__name__)

class EmojiNotifierPlugin(NcatBotPlugin):
    name = "EmojiNotifierPlugin"
    version = "1.0.0"
    description = "监听群聊表情回应并发送通知, 并提供一个命令让bot贴表情"
    author = "Cline"

    async def on_load(self):
        LOG.info(f"{self.name} 已加载。")

    @command_registry.command("react", description="让bot给当前消息贴上指定emoji")
    async def react_command(self, event: BaseMessageEvent, emoji_id: str):
        """处理贴表情命令"""
        try:
            # 将 emoji_id 转换为整数
            int_emoji_id = int(emoji_id)
            await self.api.set_msg_emoji_like(event.message_id, int_emoji_id)
        except ValueError:
            await event.reply("无效的 emoji_id, 请输入数字。")
        except Exception as e:
            LOG.error(f"贴表情失败: {e}")
            await event.reply(f"贴表情失败: {e}")

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
