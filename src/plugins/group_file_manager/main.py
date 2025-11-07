import time
from ncatbot.plugin_system import NcatBotPlugin, command_registry, group_filter
from ncatbot.core.event import GroupMessageEvent
from ncatbot.core.event.message_segment import At
from ncatbot.utils import get_log

LOG = get_log("GroupFileManager")

class GroupFileManagerPlugin(NcatBotPlugin):
    name = "GroupFileManager"
    version = "1.0.0"
    description = "一个用于管理群文件的插件"
    author = "Cline"

    async def on_load(self):
        self.confirmation_pending = {}
        LOG.info(f"插件 {self.name} 加载成功")

    @group_filter
    async def on_group_message(self, event: GroupMessageEvent):
        """监听群消息以处理二次确认"""
        group_id = event.group_id
        user_id = event.user_id
        pending_key = f"{group_id}_{user_id}"

        if pending_key in self.confirmation_pending:
            pending_info = self.confirmation_pending[pending_key]
            
            # 检查是否超时
            if time.time() - pending_info["timestamp"] > 60:
                del self.confirmation_pending[pending_key]
                return

            # 检查消息是否 at 了机器人
            at_segments = event.message.filter(At)
            is_at_self = any(at.qq == str(event.self_id) for at in at_segments)

            # 通过拼接所有文本段来获取纯文本，这是更稳妥的方式
            text_segments = event.message.filter_text()
            plain_text = "".join(seg.text for seg in text_segments).strip()

            if plain_text == "确认删除" and is_at_self:
                del self.confirmation_pending[pending_key]
                await self._execute_deletion(group_id)
            # 如果消息不是预期的确认指令，则取消操作
            else:
                del self.confirmation_pending[pending_key]
                await event.reply("操作已取消。")
            
    @group_filter
    @command_registry.command("delete_root_files", description="删除群文件根目录下的所有文件")
    async def delete_root_files(self, event: GroupMessageEvent):
        if event.sender.role not in ["admin", "owner"]:
            await event.reply("抱歉，只有群管理员或群主才能使用此命令。")
            return

        group_id = event.group_id
        user_id = event.user_id
        pending_key = f"{group_id}_{user_id}"

        self.confirmation_pending[pending_key] = {"timestamp": time.time()}
        await event.reply("⚠️ 警告：此操作将删除群文件根目录下的所有文件，且不可恢复。\n请在 60 秒内 @我 并回复“确认删除”以继续操作。回复其他任何内容或超时将取消操作。")

    async def _execute_deletion(self, group_id: str):
        """执行实际的删除操作"""
        try:
            bot_id = str(self.api.get_login_info_sync().user_id)
            bot_member_info = await self.api.get_group_member_info(group_id=group_id, user_id=bot_id)
            if bot_member_info.role not in ["owner", "admin"]:
                await self.api.post_group_msg(group_id, "我不是群管理员，无法删除文件。")
                return

            files_data = await self.api.get_group_root_files(group_id=group_id)
            files = files_data.get("files") if files_data else None
            if not files:
                await self.api.post_group_msg(group_id, "根目录下没有文件。")
                return

            deleted_count = 0
            for file_info in files:
                try:
                    await self.api.delete_group_file(group_id=group_id, file_id=file_info["file_id"])
                    deleted_count += 1
                except Exception as e:
                    LOG.error(f"删除文件 {file_info.get('file_name')} ({file_info.get('file_id')}) 失败: {e}")

            await self.api.post_group_msg(group_id, f"✅ 成功删除了 {deleted_count} 个根目录文件。")

        except Exception as e:
            LOG.error(f"获取群 {group_id} 文件列表或删除文件时出错: {e}")
            await self.api.post_group_msg(group_id, "操作失败，请检查日志获取详细信息。")
