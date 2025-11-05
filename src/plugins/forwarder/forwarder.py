from ncatbot.plugin_system import NcatBotPlugin, on_message
from ncatbot.core.event import GroupMessageEvent
from ncatbot.core.helper.forward_constructor import ForwardConstructor
from collections import defaultdict
import asyncio

class ForwarderPlugin(NcatBotPlugin):
    name = "ForwarderPlugin"
    version = "1.0.0"
    description = "自动合并转发群聊消息"
    author = "Cline"

    async def on_load(self):
        self.bot_id = None
        # 使用 defaultdict 简化缓冲区初始化
        # group_id -> list of GroupMessageEvent
        self.message_buffers = defaultdict(list)
        # group_id -> list of sent forward message IDs
        self.forward_buffers = defaultdict(list)

    @on_message
    async def on_group_message(self, event: GroupMessageEvent):
        # 首次接收消息时，记录 bot 的 ID
        if self.bot_id is None:
            self.bot_id = event.self_id

        # 1. 过滤消息
        # 忽略 bot 自身消息
        if event.user_id == self.bot_id:
            return
        
        # 忽略回复 bot 的消息
        if f"[CQ:at,qq={self.bot_id}]" in event.raw_message:
            return

        # 2. 将消息加入缓冲区
        group_id = event.group_id
        self.message_buffers[group_id].append(event)

        # 3. 检查一级合并转发条件
        if len(self.message_buffers[group_id]) >= 33:
            # 防止并发问题，复制并清空
            messages_to_forward = self.message_buffers[group_id][:]
            self.message_buffers[group_id].clear()
            
            await self.create_and_send_level_one_forward(group_id, messages_to_forward)

    async def create_and_send_level_one_forward(self, group_id: str, messages: list[GroupMessageEvent]):
        """创建并发送一级合并转发"""
        if not self.bot_id:
            return  # 如果 bot_id 未知，则无法继续

        try:
            # 构造转发内容
            forward_constructor = ForwardConstructor(self.bot_id, "消息摘要")
            for msg in messages:
                forward_constructor.attach(msg.message, user_id=msg.user_id, nickname=msg.sender.nickname)
            
            forward_msg = forward_constructor.to_forward()

            # 发送合并转发
            sent_forward_info = await self.api.post_group_forward_msg(group_id, forward_msg)
            if not sent_forward_info:
                return

            # 撤回原始消息
            for msg in messages:
                await asyncio.sleep(0.2) # 短暂延迟以避免过于频繁的 API 调用
                try:
                    await self.api.delete_msg(msg.message_id)
                except Exception:
                    pass # 忽略撤回失败的消息

            # 将新发送的合并转发加入二级缓冲区
            self.forward_buffers[group_id].append(sent_forward_info)

            # 检查二级合并转发条件
            if len(self.forward_buffers[group_id]) >= 3:
                forwards_to_nest = self.forward_buffers[group_id][:]
                self.forward_buffers[group_id].clear()
                await self.create_and_send_level_two_forward(group_id, forwards_to_nest)

        except Exception as e:
            # 记录错误，实际使用中建议使用 self.log
            print(f"创建一级合并转发时出错: {e}")

    async def create_and_send_level_two_forward(self, group_id: str, forward_message_ids: list[str]):
        """创建并发送嵌套的二级合并转发"""
        if not self.bot_id:
            return  # 如果 bot_id 未知，则无法继续

        try:
            # 构造嵌套转发内容
            nested_constructor = ForwardConstructor(self.bot_id, "消息记录")
            for msg_id in forward_message_ids:
                nested_constructor.attach_message_id(msg_id)
            
            nested_forward_msg = nested_constructor.to_forward()

            # 发送嵌套合并转发
            await self.api.post_group_forward_msg(group_id, nested_forward_msg)

            # 撤回一级合并转发消息
            for msg_id in forward_message_ids:
                await asyncio.sleep(0.2)
                try:
                    await self.api.delete_msg(msg_id)
                except Exception:
                    pass # 忽略撤回失败

        except Exception as e:
            print(f"创建二级合并转发时出错: {e}")
