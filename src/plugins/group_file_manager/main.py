import aiohttp
from ncatbot.plugin_system import NcatBotPlugin, command_registry, admin_group_filter, on_message
from ncatbot.core.event import GroupMessageEvent
from ncatbot.core.event.message_segment import File
from ncatbot.utils import get_log

LOG = get_log("GroupFileManager")

class GroupFileManagerPlugin(NcatBotPlugin):
    name = "GroupFileManager"
    version = "1.0.0"
    description = "一个用于管理群文件的插件"
    author = "Cline"

    async def on_load(self):
        LOG.info(f"插件 {self.name} 加载成功")

    @on_message
    async def handle_group_file(self, event: GroupMessageEvent):
        files = event.message.filter(File)
        if not files:
            return

        for file in files:
            file_info = (
                f"检测到文件：\n"
                f"文件名: {file.file}\n"
                f"文件ID: {file.file_id}\n"
                f"文件大小: {file.file_size} 字节"
            )

            if file.file.endswith((".txt", ".md")):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(file.url) as response:
                            if response.status == 200:
                                content = await response.text()
                                preview = content[:100]
                                file_info += f"\n\n文件预览 (前100字):\n---\n{preview}"
                            else:
                                LOG.warning(f"下载文件预览失败，状态码: {response.status}")
                                file_info += "\n\n无法获取文件预览。"
                except Exception as e:
                    LOG.error(f"下载或读取文件预览时出错: {e}")
                    file_info += "\n\n无法获取文件预览。"

            await event.reply(file_info)

    @admin_group_filter
    @command_registry.command("delete_root_files", description="删除群文件根目录下的所有文件")
    async def delete_root_files(self, event: GroupMessageEvent):
        group_id = event.group_id
        bot_id = str(event.self_id)

        try:
            bot_member_info = await self.api.get_group_member_info(group_id=group_id, user_id=bot_id)
            if bot_member_info.role not in ["owner", "admin"]:
                await event.reply("我不是群管理员，无法删除文件。")
                return

            files_data = await self.api.get_group_root_files(group_id=group_id)
            if not files_data or not files_data.get("files"):
                await event.reply("根目录下没有文件。")
                return

            files = files_data["files"]
            if not files:
                await event.reply("根目录下没有文件。")
                return

            deleted_count = 0
            for file_info in files:
                try:
                    await self.api.delete_group_file(group_id=group_id, file_id=file_info["file_id"])
                    deleted_count += 1
                except Exception as e:
                    LOG.error(f"删除文件 {file_info.get('file_name')} ({file_info.get('file_id')}) 失败: {e}")

            await event.reply(f"成功删除了 {deleted_count} 个根目录文件。")

        except Exception as e:
            LOG.error(f"获取群 {group_id} 文件列表或删除文件时出错: {e}")
            await event.reply("操作失败，请检查日志获取详细信息。")
