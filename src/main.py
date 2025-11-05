import json
from ncatbot.core import BotClient
from ncatbot.core.event import NoticeEvent, GroupMessageEvent
from ncatbot.utils import get_log

LOG = get_log("EmojiReactionListener")

bot = BotClient()

LOG.info("机器人已启动，正在监听贴表情事件...")

@bot.on_notice()
async def handle_notice_event(event: NoticeEvent):
    """
    处理所有通知事件，并专门找出贴表情事件。
    """
    # 检查通知类型是否为“群消息表情回应”
    if event.notice_type == "group_msg_emoji_like":
        
        # 从事件数据中提取关键信息
        group_id = event.group_id
        user_id = event.user_id      # 操作者QQ
        message_id = event.message_id  # 被回应的消息ID
        is_add = getattr(event, "is_add", False) # is_add is a dynamic attribute
        
        # 获取表情信息
        likes = getattr(event, "likes", []) # likes is a dynamic attribute
        emoji_info = "未知表情"
        if likes:
            emoji_id = likes[0].get("emoji_id", "未知")
            emoji_info = f"表情ID:{emoji_id}"

        # 构建基础回应消息
        action = "贴上了" if is_add else "取消了"
        message = (
            f"🔔 表情回应通知：\n"
            f"群聊: {group_id}\n"
            f"用户: {user_id}\n"
            f"动作: {action} {emoji_info}\n"
            f"目标消息ID: {message_id}"
        )

        # 主动调用API获取详细的点赞列表并附加
        if likes:
            emoji_id = likes[0].get("emoji_id", "未知")
            try:
                response = await bot.api.fetch_emoji_like(message_id=message_id, emoji_id=emoji_id, emoji_type=1)
                likers_list = response.get('emojiLikesList', [])
                if likers_list:
                    # 将原始列表格式化为JSON字符串并附加
                    raw_list_str = json.dumps(likers_list, indent=2, ensure_ascii=False)
                    message += f"\n表情{emoji_id}详细 (emojiLikesList):\n{raw_list_str}"
                else:
                    message += f"\n表情{emoji_id}详细 (emojiLikesList): []"
            except Exception as e:
                LOG.error(f"获取表情详情失败: {e}")
                message += f"\n获取表情{emoji_id}详情失败。"
        
        LOG.info(f"捕获到贴表情事件: {message}")
        
        # 你可以在这里发送消息到群里或进行其他操作
        # 例如，回复被贴表情的消息
        await bot.api.post_group_msg(group_id, text=message, reply=message_id)


@bot.on_group_message()
async def handle_group_message(msg: GroupMessageEvent):
    """一个简单的命令，用于确认机器人是否在线"""
    if msg.raw_message == "ping":
        await msg.reply("pong")

# 启动 Bot
bot.run_frontend(debug=True)
