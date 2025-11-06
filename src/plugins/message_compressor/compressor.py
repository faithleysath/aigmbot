from ncatbot.plugin_system import NcatBotPlugin, command_registry, group_filter, on_notice
from ncatbot.core.event import GroupMessageEvent, NoticeEvent
from ncatbot.core.event.message_segment import File, At
from ncatbot.core.helper.forward_constructor import ForwardConstructor
from ncatbot.utils import get_log
from collections import defaultdict
import asyncio
import json

LOG = get_log(__name__)

class MessageCompressorPlugin(NcatBotPlugin):
    name = "MessageCompressorPlugin"
    version = "1.0.1"
    description = "全自动打包压缩群聊消息"
    author = "Cline"

    async def on_load(self):
        self.bot_id = None
        
        # 注册全局配置项
        self.register_config("message_threshold", 33)
        self.register_config("forward_threshold", 3)
        
        # 用于存储每个群的特定设置, a dict that will be persisted automatically
        self.register_config("group_settings", {})

        # 配置文件健壮性修复
        if isinstance(self.config.get("group_settings"), str):
            try:
                # 尝试将字符串解析为字典
                self.config["group_settings"] = json.loads(self.config["group_settings"].replace("'", "\""))
            except (json.JSONDecodeError, TypeError):
                # 如果解析失败，则重置为默认值
                self.config["group_settings"] = {}
                LOG.warning("group_settings 配置格式错误，已重置为默认值。")

        # 使用 defaultdict 简化缓冲区初始化
        self.message_buffers = defaultdict(list)
        self.forward_buffers = defaultdict(list)
        self.admin_status_cache = {}

    # The _load_group_settings, _save_group_settings, and on_close methods
    # are no longer needed as the framework handles config persistence automatically.

    async def _fetch_bot_admin_status(self, group_id: str) -> bool:
        """强制从 API 获取机器人是否为群管理员或群主，并更新缓存。"""
        if not self.bot_id:
            LOG.warning("Bot ID not available, cannot check admin status.")
            return False

        try:
            member_info = await self.api.get_group_member_info(group_id, self.bot_id)
            is_admin = member_info.role in ["admin", "owner"]
            self.admin_status_cache[group_id] = is_admin  # 更新缓存
            return is_admin
        except Exception as e:
            LOG.error(f"获取群 {group_id} 的机器人成员信息失败: {e}")
            self.admin_status_cache[group_id] = False  # 缓存失败结果
            return False

    async def _is_bot_admin_in_group(self, group_id: str) -> bool:
        """检查机器人是否为群管理员或群主，优先使用缓存。"""
        if group_id in self.admin_status_cache:
            return self.admin_status_cache[group_id]
        
        # 如果缓存中没有，则从 API 获取
        return await self._fetch_bot_admin_status(group_id)

    def _is_group_admin(self, event: GroupMessageEvent) -> bool:
        """检查消息发送者是否为群管理员或群主"""
        return event.sender.role in ["admin", "owner"]

    @on_notice
    async def _handle_admin_change_notice(self, event: NoticeEvent):
        """监听管理员变更通知以直接更新缓存"""
        if event.notice_type == 'group_admin' and event.user_id == self.bot_id:
            group_id = event.group_id
            is_admin = (event.sub_type == 'set')
            self.admin_status_cache[group_id] = is_admin
            status_text = "授予" if is_admin else "取消"
            LOG.info(f"检测到机器人在群 {group_id} 的管理员权限被{status_text}，已更新缓存。")

    @group_filter
    async def on_group_message(self, event: GroupMessageEvent):
        # 首次接收消息时，记录 bot 的 ID
        if self.bot_id is None:
            self.bot_id = event.self_id

        # 忽略命令消息或被禁用的群聊
        if event.raw_message.startswith('/') or not self.config["group_settings"].get(event.group_id, {}).get("enabled", True):
            return

        await self._handle_message_buffering(event)

    async def _handle_message_buffering(self, event: GroupMessageEvent):
        """处理消息缓冲和触发压缩"""
        group_id = event.group_id

        if event.message.is_forward_msg() or event.message.filter(File):
            return

        # 将消息加入缓冲区
        self.message_buffers[group_id].append(event)

        # 检查是否达到一级合并转发的阈值
        group_conf = self.config["group_settings"].get(group_id, {})
        message_threshold = int(group_conf.get("message_threshold", self.config["message_threshold"]))
        
        if len(self.message_buffers[group_id]) >= message_threshold:
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

            # 检查 bot 是否为管理员或群主（使用缓存），仅用于决定是否撤回
            if await self._is_bot_admin_in_group(group_id):
                # 撤回原始消息
                for msg in messages:
                    at_segments = msg.message.filter(At)
                    is_at_self = any(at.qq == str(self.bot_id) for at in at_segments)
                    if self._is_group_admin(msg) or msg.user_id == self.bot_id or is_at_self:
                        continue
                    await asyncio.sleep(0.2) # 短暂延迟以避免过于频繁的 API 调用
                    try:
                        await self.api.delete_msg(msg.message_id)
                    except Exception:
                        pass # 忽略撤回失败的消息

            # 将新发送的合并转发加入二级缓冲区
            self.forward_buffers[group_id].append(sent_forward_info)

            # 检查二级合并转发条件
            group_conf = self.config["group_settings"].get(group_id, {})
            forward_threshold = int(group_conf.get("forward_threshold", self.config["forward_threshold"]))
            if len(self.forward_buffers[group_id]) >= forward_threshold:
                forwards_to_nest = self.forward_buffers[group_id][:]
                self.forward_buffers[group_id].clear()
                await self.create_and_send_level_two_forward(group_id, forwards_to_nest)

        except Exception as e:
            LOG.error(f"创建一级合并转发时出错: {e}")

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
            LOG.error(f"创建二级合并转发时出错: {e}")

    # --- Admin Commands ---

    @group_filter
    @command_registry.command("compressor", description="管理自动打包压缩功能")
    async def compressor_main_command(self, event: GroupMessageEvent, action: str, val1: str = "", val2: str = ""):
        # 首次接收消息时，记录 bot 的 ID
        if self.bot_id is None:
            self.bot_id = event.self_id
        if not self._is_group_admin(event):
            await event.reply("抱歉，只有群管理员或群主才能使用此命令。")
            return

        group_id = event.group_id
        
        if action in ["enable", "on"]:
            self.config["group_settings"].setdefault(group_id, {})["enabled"] = True
            await event.reply("✅ 在本群已启用自动打包压缩功能。")
        
        elif action in ["disable", "off"]:
            self.config["group_settings"].setdefault(group_id, {})["enabled"] = False
            await event.reply("❌ 在本群已禁用自动打包压缩功能。")

        elif action == "threshold":
            if not val1 or not val2:
                await event.reply("❌ 参数不足，请提供两个有效的数字作为阈值。\n用法: /compressor threshold <消息数> <转发数>")
                return
            try:
                errors = []
                msg_threshold = int(val1)
                fwd_threshold = int(val2)

                if msg_threshold < 2:
                    errors.append("消息数阈值不能小于2。")
                if fwd_threshold < 2:
                    errors.append("转发数阈值不能小于2。")
                
                if not errors and msg_threshold * fwd_threshold > 100:
                    errors.append("两个阈值的乘积不能超过100。")

                if errors:
                    error_message = "❌ 设置失败：\n" + "\n".join(f"- {e}" for e in errors)
                    await event.reply(error_message)
                    return

                group_conf = self.config["group_settings"].setdefault(group_id, {})
                group_conf["message_threshold"] = msg_threshold
                group_conf["forward_threshold"] = fwd_threshold
                await event.reply(
                    f"✅ 在本群的触发阈值已更新：\n"
                    f"- 消息数达到 {msg_threshold} 条时打包\n"
                    f"- 打包记录达到 {fwd_threshold} 条时再次打包"
                )
            except (ValueError, TypeError):
                await event.reply("❌ 参数错误，请提供两个有效的数字作为阈值。\n用法: /compressor threshold <消息数> <转发数>")

        elif action == "status":
            settings = self.config["group_settings"].get(group_id, {})
            enabled = settings.get("enabled", True)
            # 强制刷新状态以获取最新信息
            has_admin_privilege = await self._fetch_bot_admin_status(group_id)

            is_msg_thresh_global = "message_threshold" not in settings
            is_fwd_thresh_global = "forward_threshold" not in settings

            msg_thresh = settings.get("message_threshold", self.config["message_threshold"])
            fwd_thresh = settings.get("forward_threshold", self.config["forward_threshold"])

            msg_thresh_str = f"{msg_thresh}{' (全局)' if is_msg_thresh_global else ''}"
            fwd_thresh_str = f"{fwd_thresh}{' (全局)' if is_fwd_thresh_global else ''}"

            # 获取当前缓冲区状态
            msg_buffer_count = len(self.message_buffers.get(group_id, []))
            fwd_buffer_count = len(self.forward_buffers.get(group_id, []))

            status_text = (
                f"--- 本群自动打包状态 ---\n"
                f"功能状态: {'✅ 已启用' if enabled else '❌ 已禁用'}\n"
                f"撤回权限: {'✅ 可用' if has_admin_privilege else '❌ 不可用'}\n"
                f"一级阈值: {msg_thresh_str} 条消息\n"
                f"二级阈值: {fwd_thresh_str} 条打包记录\n"
                f"当前缓存: {msg_buffer_count} 条消息 | {fwd_buffer_count} 条打包记录\n"
                f"--------------------------"
            )
            if is_msg_thresh_global or is_fwd_thresh_global:
                status_text += "\n提示: 阈值后面带有 '(全局)' 字样表示当前使用的是默认配置。"

            await event.reply(status_text)
        
        else:
            await event.reply(
                "用法: /compressor <action>\n"
                "action 可为: enable, disable, threshold, status"
            )
