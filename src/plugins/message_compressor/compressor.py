from typing import Optional
from ncatbot.plugin_system import NcatBotPlugin, on_message, command_registry, group_filter
from ncatbot.core.event import GroupMessageEvent
from ncatbot.core.helper.forward_constructor import ForwardConstructor
from collections import defaultdict
import asyncio
import json
import os

class MessageCompressorPlugin(NcatBotPlugin):
    name = "MessageCompressorPlugin"
    version = "1.0.1"
    description = "全自动打包压缩群聊消息"
    author = "Cline"

    async def on_load(self):
        self.bot_id = None
        # 确保插件名和文件名一致，以便正确找到配置文件
        self.settings_file_path = "data/MessageCompressorPlugin/group_settings.json"
        
        # 注册全局配置项
        self.register_config("message_threshold", 33)
        self.register_config("forward_threshold", 3)
        
        # 用于存储每个群的特定设置
        self.group_settings = defaultdict(dict)
        self._load_group_settings()

        # 使用 defaultdict 简化缓冲区初始化
        self.message_buffers = defaultdict(list)
        self.forward_buffers = defaultdict(list)

    def _load_group_settings(self):
        """从文件加载群聊设置"""
        try:
            if os.path.exists(self.settings_file_path):
                with open(self.settings_file_path, 'r', encoding='utf-8') as f:
                    loaded_settings = json.load(f)
                    # 将加载的 dict 转换为 defaultdict
                    self.group_settings.update({k: v for k, v in loaded_settings.items()})
        except (FileNotFoundError, json.JSONDecodeError):
            # 文件不存在或损坏，使用默认空设置
            pass

    def _save_group_settings(self):
        """将群聊设置保存到文件"""
        try:
            # 确保目录存在
            os.makedirs(os.path.dirname(self.settings_file_path), exist_ok=True)
            with open(self.settings_file_path, 'w', encoding='utf-8') as f:
                json.dump(self.group_settings, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"保存群聊设置时出错: {e}")

    async def on_close(self):
        """插件关闭时保存设置"""
        self._save_group_settings()

    async def _is_group_admin(self, event: GroupMessageEvent) -> bool:
        """检查消息发送者是否为群管理员或群主"""
        member_info = await self.api.get_group_member_info(event.group_id, event.user_id)
        return member_info.role in ["admin", "owner"]

    @on_message
    async def on_group_message(self, event: GroupMessageEvent):
        group_id = event.group_id
        
        # 检查功能是否在该群被禁用
        if not self.group_settings[group_id].get("enabled", True):
            return

        # 首次接收消息时，记录 bot 的 ID
        if self.bot_id is None:
            self.bot_id = event.self_id

        # 1. 过滤消息
        if event.user_id == self.bot_id or f"[CQ:at,qq={self.bot_id}]" in event.raw_message:
            return

        # 2. 将消息加入缓冲区
        self.message_buffers[group_id].append(event)

        # 3. 检查一级合并转发条件
        message_threshold = self.group_settings[group_id].get("message_threshold", self.config["message_threshold"])
        if len(self.message_buffers[group_id]) >= message_threshold:
            messages_to_forward = self.message_buffers[group_id][:]
            self.message_buffers[group_id].clear()
            await self.create_and_send_level_one_forward(group_id, messages_to_forward)

    async def create_and_send_level_one_forward(self, group_id: str, messages: list[GroupMessageEvent]):
        """创建并发送一级合并转发"""
        if not self.bot_id:
            return  # 如果 bot_id 未知，则无法继续

        try:
            # 检查 bot 是否为管理员或群主
            member_info = await self.api.get_group_member_info(group_id, self.bot_id)
            if member_info.role not in ["admin", "owner"]:
                # 如果不是管理员或群主，则不执行任何操作
                return

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
            forward_threshold = self.group_settings[group_id].get("forward_threshold", self.config["forward_threshold"])
            if len(self.forward_buffers[group_id]) >= forward_threshold:
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

    # --- Admin Commands ---

    @group_filter
    @command_registry.command("compressor", description="管理自动打包压缩功能")
    async def compressor_main_command(self, event: GroupMessageEvent, action: str, val1: Optional[str] = None, val2: Optional[str] = None):
        if not await self._is_group_admin(event):
            await event.reply("抱歉，只有群管理员或群主才能使用此命令。")
            return

        group_id = event.group_id
        
        if action in ["enable", "on"]:
            self.group_settings[group_id]["enabled"] = True
            self._save_group_settings()
            await event.reply("✅ 在本群已启用自动打包压缩功能。")
        
        elif action in ["disable", "off"]:
            self.group_settings[group_id]["enabled"] = False
            self._save_group_settings()
            await event.reply("❌ 在本群已禁用自动打包压缩功能。")

        elif action == "threshold":
            if val1 is None or val2 is None:
                await event.reply("❌ 参数不足，请提供两个有效的数字作为阈值。\n用法: /compressor threshold <消息数> <转发数>")
                return
            try:
                msg_threshold = int(val1)
                fwd_threshold = int(val2)
                if msg_threshold * fwd_threshold > 100:
                    await event.reply("❌ 设置失败：两个阈值的乘积不能超过100。")
                    return
                if msg_threshold < 2 or fwd_threshold < 2:
                    await event.reply("❌ 设置失败：单个阈值不能小于2。")
                    return
                
                self.group_settings[group_id]["message_threshold"] = msg_threshold
                self.group_settings[group_id]["forward_threshold"] = fwd_threshold
                self._save_group_settings()
                await event.reply(
                    f"✅ 在本群的触发阈值已更新：\n"
                    f"- 消息数达到 {msg_threshold} 条时打包\n"
                    f"- 打包记录达到 {fwd_threshold} 条时再次打包"
                )
            except (ValueError, TypeError):
                await event.reply("❌ 参数错误，请提供两个有效的数字作为阈值。\n用法: /compressor threshold <消息数> <转发数>")

        elif action == "status":
            settings = self.group_settings[group_id]
            enabled = settings.get("enabled", True)
            msg_thresh = settings.get("message_threshold", self.config["message_threshold"])
            fwd_thresh = settings.get("forward_threshold", self.config["forward_threshold"])

            status_text = (
                f"--- 本群自动打包状态 ---\n"
                f"功能状态: {'✅ 已启用' if enabled else '❌ 已禁用'}\n"
                f"一级阈值: {msg_thresh} 条消息\n"
                f"二级阈值: {fwd_thresh} 条打包记录\n"
                f"--------------------------\n"
                f"提示: 阈值后面带有 '(全局)' 字样表示当前使用的是默认配置。"
            )
            await event.reply(status_text.replace(f" {self.config['message_threshold']}", f" {self.config['message_threshold']} (全局)")
                                     .replace(f" {self.config['forward_threshold']}", f" {self.config['forward_threshold']} (全局)"))
        
        else:
            await event.reply(
                "用法: /compressor <action>\n"
                "action 可为: enable, disable, threshold, status"
            )
