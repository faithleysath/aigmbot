import json
import re
import shlex
from datetime import datetime, timezone
import aiohttp

from ncatbot.core.event import GroupMessageEvent, NoticeEvent, PrivateMessageEvent
from ncatbot.core.event.message_segment import File, Reply, At
from ncatbot.plugin_system import NcatBotPlugin
from ncatbot.utils import get_log

from .db import Database
from .cache import CacheManager
from .game_manager import GameManager
from .renderer import MarkdownRenderer
from .utils import EMOJI, bytes_to_base64
from .content_fetcher import ContentFetcher
from .commands import CommandHandler
from .channel_config import ChannelConfigManager
from .llm_config import LLMConfigManager

LOG = get_log(__name__)


class EventHandler:
    def __init__(
        self,
        plugin: NcatBotPlugin,
        db: Database,
        cache_manager: CacheManager,
        game_manager: GameManager,
        renderer: MarkdownRenderer,
        content_fetcher: ContentFetcher,
        command_handler: CommandHandler,
        channel_config: ChannelConfigManager,
        llm_config_manager: LLMConfigManager | None = None
    ):
        self.plugin = plugin
        self.api = plugin.api
        self.db = db
        self.cache_manager = cache_manager
        self.game_manager = game_manager
        self.renderer = renderer
        self.config = plugin.config
        self.content_fetcher = content_fetcher
        self.command_handler = command_handler
        self.channel_config = channel_config
        self.llm_config_manager = llm_config_manager

    async def handle_group_message(self, event: GroupMessageEvent):
        """处理群聊消息，包括文件上传启动和自定义输入"""
        URL_PATTERN = re.compile(r"^/text_file\s+(https?://[^\s]+)$")
        if (m:=URL_PATTERN.match(event.raw_message)):
            file = File(file="")
            file.url = m.group(1)
            await self._handle_file_upload(event, file)
            return

        # 文件上传启动游戏
        files = event.message.filter(File)
        if files and files[0].file.lower().endswith((".txt", ".md")):
            await self._handle_file_upload(event, files[0])
            return

        # 自定义输入
        reply_segments = event.message.filter(Reply)
        if reply_segments:
            await self._handle_custom_input(event, reply_segments[0])
            return

    async def handle_private_message(self, event: PrivateMessageEvent):
        """处理私聊消息命令"""
        content = event.raw_message.strip()
        
        try:
            # /aigm llm add <name> <model> <base_url> <api_key> [--force]
            if content.startswith("/aigm llm add"):
                parts = shlex.split(content)
                
                # Check for --force flag
                force = False
                if "--force" in parts:
                    force = True
                    parts.remove("--force")
                
                if len(parts) != 7:
                    await event.reply("❌ 格式错误。请使用: /aigm llm add <name> <model> <base_url> <api_key> [--force]")
                    return
                
                await self.command_handler.handle_llm_add(event, parts[3], parts[4], parts[5], parts[6], force=force)
                return

            # /aigm llm remove <name>
            if content.startswith("/aigm llm remove"):
                parts = shlex.split(content)
                if len(parts) != 4:
                    await event.reply("❌ 格式错误。请使用: /aigm llm remove <name>")
                    return
                await self.command_handler.handle_llm_remove(event, parts[3])
                return

            # /aigm llm test <name>
            if content.startswith("/aigm llm test"):
                parts = shlex.split(content)
                if len(parts) != 4:
                    await event.reply("❌ 格式错误。请使用: /aigm llm test <name>")
                    return
                await self.command_handler.handle_llm_test(event, parts[3])
                return

            # /aigm llm list (status)
            if content.startswith("/aigm llm list") or content.startswith("/aigm llm status"):
                await self.command_handler.handle_llm_status(event)
                return

            # 默认提示
            if content.startswith("/aigm"):
                await event.reply(
                    "🤖 AI GM 私聊助手\n\n"
                    "📋 可用命令:\n\n"
                    "• /aigm llm add <name> <model> <base_url> <api_key>\n"
                    "  添加新的 LLM 预设配置\n"
                    "  示例: /aigm llm add gpt4 gpt-4-turbo https://api.openai.com/v1 sk-xxx\n\n"
                    "• /aigm llm remove <name>\n"
                    "  删除已保存的预设（正在使用的预设无法删除）\n"
                    "  示例: /aigm llm remove gpt4\n\n"
                    "• /aigm llm test <name>\n"
                    "  测试指定预设的连接性\n"
                    "  示例: /aigm llm test gpt4\n\n"
                    "• /aigm llm list\n"
                    "  查看您的所有 LLM 预设\n\n"
                    "💡 使用技巧:\n"
                    "- 如果参数包含空格，请使用引号包裹\n"
                    "  例如: /aigm llm add \"my preset\" gpt-4 \"https://api.example.com\" sk-xxx\n"
                    "- 在群聊中使用 /aigm llm bind <name> 来贡献算力\n"
                    "- 管理员可以设置保底预设: /aigm llm set-fallback <name>"
                )
        except ValueError as e:
             await event.reply(f"❌ 参数解析错误: {e}\n提示: 如果参数包含空格，请使用引号包裹。")
             return

    async def process_system_prompt(self, group_id: str, user_id: str, system_prompt: str, reply_to_msg_id: str | None = None) -> tuple[bool, str]:
        """
        处理新的剧本（System Prompt），发送预览并进入确认流程。
        
        Args:
            group_id: 群组ID
            user_id: 提交用户ID
            system_prompt: 剧本内容
            reply_to_msg_id: 可选，回复的消息ID
            
        Returns:
            tuple[bool, str]: (是否成功, 错误信息/成功提示)
        """
        try:
            preview = system_prompt[:2000] # 预览前2000字符
            img: bytes | None = None
            if self.renderer:
                img = await self.renderer.render_markdown(preview)

            reply_message_id = None
            if img:
                reply_message_id = await self.api.post_group_file(
                    group_id,
                    image=f"data:image/png;base64,{bytes_to_base64(img)}",
                )
            else:
                reply_message_id = await self.api.post_group_msg(
                    group_id,
                    text=f"文件预览:\n\n{preview}",
                    reply=reply_to_msg_id
                )

            if not reply_message_id:
                return False, "无法发送预览消息到群聊"

            if self.db and await self.db.is_game_running(group_id):
                await self.api.set_msg_emoji_like(
                    reply_message_id, str(EMOJI["COFFEE"])
                )  # 频道繁忙
            else:
                await self.api.set_msg_emoji_like(
                    reply_message_id, str(EMOJI["CONFIRM"])
                )  # 确认

            await self.cache_manager.add_pending_game(
                str(reply_message_id),
                {
                    "user_id": user_id,
                    "system_prompt": system_prompt,
                    "message_id": reply_to_msg_id, # origin message (optional)
                    "create_time": datetime.now(timezone.utc),
                },
            )
            return True, "成功发起确认流程"
        except Exception as e:
            LOG.error(f"处理剧本时出错: {e}", exc_info=True)
            return False, str(e)

    async def _handle_file_upload(self, event: GroupMessageEvent, file: File):
        """处理.txt或.md文件上传，作为开启游戏的入口"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(file.url) as response:
                    if response.status != 200:
                        await event.reply("无法获取文件内容。", at=False)
                        return
                    content = await response.text()
            
            success, error_msg = await self.process_system_prompt(
                str(event.group_id),
                str(event.user_id),
                content,
                str(event.message_id)
            )
            
            if not success:
                await event.reply(f"❌ 处理文件失败: {error_msg}", at=False)
                
        except aiohttp.ClientError as e:
            LOG.error(f"下载文件失败: {e}", exc_info=True)
            await event.reply("无法下载文件，请稍后重试。", at=False)
        except Exception as e:
            LOG.error(f"处理文件消息时出错: {e}", exc_info=True)
            await event.reply("处理文件时发生意外错误。", at=False)

    async def _handle_custom_input(self, event: GroupMessageEvent, reply: Reply):
        """处理对主消息的回复，作为自定义输入"""
        if not self.db or not self.db.conn:
            return

        group_id = str(event.group_id)
        replied_to_id = reply.id

        game = await self.db.get_game_by_channel_id(group_id)
        if not game or str(game["main_message_id"]) != replied_to_id:
            return  # 不是对当前游戏主消息的回复

        # 检查event消息中是否有at段，如果没有，则终止
        at_segments = event.message.filter(At)
        is_at_self = any(at.qq == str(event.self_id) for at in at_segments)
        if not is_at_self:
            return

        game_id = game["game_id"]
        candidate_ids_json = game["candidate_custom_input_ids"]

        custom_input_message_id = str(event.message_id)
        custom_input_content = "".join(
            s.text for s in event.message.filter_text()
        ).strip()

        candidate_ids: list = json.loads(candidate_ids_json)
        candidate_ids.append(custom_input_message_id)

        await self.db.update_candidate_custom_input_ids(
            game_id, json.dumps(candidate_ids)
        )

        # 将内容添加到缓存
        await self.cache_manager.set_custom_input_content(
            group_id, custom_input_message_id, custom_input_content
        )

        LOG.info(f"游戏 {game_id} 收到新的自定义输入: {custom_input_message_id}")

        # 为自定义输入添加投票表情
        for emoji_key in ["YAY", "NAY", "CANCEL"]:
            try:
                await self.api.set_msg_emoji_like(
                    custom_input_message_id, str(EMOJI[emoji_key])
                )
            except Exception as e:
                LOG.warning(
                    f"为自定义输入 {custom_input_message_id} 贴表情 {EMOJI[emoji_key]} 失败: {e}"
                )

    async def handle_emoji_reaction(self, event: NoticeEvent):
        """处理表情回应，包括游戏启动、投票、撤回等"""
        if (
            event.notice_type != "group_msg_emoji_like"
            or event.user_id == str(event.self_id)
        ):
            return

        pending_game = await self.cache_manager.get_pending_game(str(event.message_id))
        if pending_game:
            await self._handle_new_game_confirmation(event, pending_game)
            return

        # 检查是否是游戏中的表情回应
        await self._handle_game_reaction(event)

    async def _handle_new_game_confirmation(
        self, event: NoticeEvent, pending_game: dict
    ):
        """处理新游戏创建的表情确认"""
        message_id_str = str(event.message_id)

        # 批量清理所有过期的请求
        timeout_seconds = int(self.config.get("pending_game_timeout", 300))
        expired_ids = await self.cache_manager.cleanup_expired_pending_games(
            timeout_seconds
        )

        # 检查当前这个游戏 proposal 是否已过期（在刚刚的批量清理中被移除）
        if message_id_str in expired_ids:
            LOG.info(f"待处理游戏 {message_id_str} 已超时并被清理，操作中止。")
            return

        # 权限检查：只有发起人可以确认或取消
        if str(event.user_id) != pending_game["user_id"]:
            return

        group_id = str(event.group_id)
        emoji_id = str(event.emoji_like_id)

        if emoji_id == str(EMOJI["COFFEE"]):  # 频道繁忙
            try:
                await self.api.delete_msg(pending_game["message_id"])
                await self.api.set_msg_emoji_like(
                    message_id_str, str(EMOJI["CONFIRM"]), set=False
                )
                await self.api.set_msg_emoji_like(
                    message_id_str, str(EMOJI["COFFEE"])
                )
                await self.api.post_group_msg(
                    group_id,
                    " 新游戏创建已取消。",
                    at=event.user_id,
                    reply=message_id_str,
                )
                LOG.info(f"用户 {event.user_id} 取消了新游戏创建请求。")
            except Exception as e:
                LOG.error(f"处理取消新游戏时出错: {e}")
            finally:
                await self.cache_manager.remove_pending_game(message_id_str)

        elif emoji_id == str(EMOJI["CONFIRM"]):  # 确认
            if self.db and await self.db.is_game_running(group_id):
                await self.api.post_group_msg(
                    group_id,
                    " 当前已有正在进行的游戏，无法创建新游戏。",
                    at=event.user_id,
                    reply=message_id_str,
                )
                await self.api.set_msg_emoji_like(
                    message_id_str, str(EMOJI["COFFEE"])
                )
                await self.api.set_msg_emoji_like(
                    message_id_str, str(EMOJI["CONFIRM"]), set=False
                )
                return

            await self.api.set_msg_emoji_like(
                message_id_str, str(EMOJI["CONFIRM"])
            )
            await self.api.set_msg_emoji_like(
                message_id_str, str(EMOJI["COFFEE"]), set=False
            )
            await self.cache_manager.remove_pending_game(message_id_str)

            await self.game_manager.start_new_game(
                group_id=group_id,
                user_id=pending_game["user_id"],
                system_prompt=pending_game["system_prompt"],
            )

    async def _handle_admin_main_message_reaction(
        self, game_id: int, group_id: str, main_message_id: str, emoji_id: str
    ):
        """处理管理员/主持人对主消息的表情回应"""
        if not self.db:
            return

        game = await self.db.get_game_by_game_id(game_id)
        if not game:
            return
        
        if game["is_frozen"]:
            await self.api.post_group_msg(
                group_id, text="正在处理其他操作，请稍后再试。", reply=main_message_id
            )
            return

        if emoji_id == str(EMOJI["CONFIRM"]):
            await self._tally_and_advance(game_id, channel_id = group_id)
        elif emoji_id == str(EMOJI["DENY"]):
            _, result_lines = await self._tally_votes(
                group_id, main_message_id, game["candidate_custom_input_ids"]
            )
            await self.api.post_group_msg(
                group_id,
                text="\n".join(result_lines)
                + "\n由于一位管理员/主持人的反对票，本轮投票并未获通过，将重新开始本轮。",
                reply=main_message_id,
            )
            if self.cache_manager:
                await self.cache_manager.clear_group_vote_cache(group_id)
            if self.game_manager:
                await self.game_manager.checkout_head(game_id)
        elif emoji_id == str(EMOJI["RETRACT"]):
            if self.game_manager:
                await self.game_manager.revert_last_round(game_id)


    async def _handle_admin_custom_input_reaction(
        self, game_id: int, group_id: str, message_id: str
    ):
        """处理管理员/主持人撤回自定义输入的行为"""
        if not self.db:
            return
        game = await self.db.get_game_by_game_id(game_id)
        if not game:
            return
        candidate_ids = json.loads(game["candidate_custom_input_ids"])
        if message_id not in candidate_ids:
            return

        candidate_ids.remove(message_id)
        await self.db.update_candidate_custom_input_ids(
            game_id, json.dumps(candidate_ids)
        )
        await self.api.post_group_msg(
            group_id, text=" 由于一名管理员/主持人的撤回，该条回复将不会被计入投票", reply=message_id
        )
        # 从缓存中删除
        if self.cache_manager:
            await self.cache_manager.remove_vote_item(group_id, message_id)

    async def _handle_game_reaction(self, event: NoticeEvent):
        """处理游戏进行中的表情回应，包括投票、撤回和管理员操作"""
        if (
            not self.db
            or not self.db.conn
            or not event.message_id
            or not event.emoji_like_id
        ):
            return

        group_id = str(event.group_id)
        user_id = str(event.user_id)
        message_id = str(event.message_id)
        emoji_id = str(event.emoji_like_id)

        # 读取游戏状态并验证（原子操作）
        game = await self.db.get_game_by_channel_id(group_id)
        if not game:
            return

        game_id = game["game_id"]
        main_message_id = str(game["main_message_id"])
        candidate_ids = json.loads(game["candidate_custom_input_ids"])

        # --- 主动防御：只处理对有效消息的回应 ---
        if message_id != main_message_id and message_id not in candidate_ids:
            return

        # 无论是否冻结，先记录投票（避免数据丢失）
        if self.cache_manager:
            await self.cache_manager.update_vote(
                group_id, message_id, emoji_id, user_id, event.is_add or False
            )

        # 后续仅管理员/主持人的控制动作需要受冻结状态约束
        if game["is_frozen"]:
            return

        # 检查是否是管理员或主持人
        sender_role = None
        try:
            member_info = await self.api.get_group_member_info(group_id, user_id)
            sender_role = member_info.role
        except Exception as e:
            LOG.warning(f"获取群 {group_id} 成员 {user_id} 信息失败: {e}")

        is_admin_or_host = await self.command_handler.check_channel_permission(
            user_id, group_id, sender_role
        )
        if not is_admin_or_host:
            return

        # 根据消息ID和表情ID分发给不同的处理函数
        # 注意：这些函数内部会再次检查游戏状态
        if message_id == main_message_id:
            await self._handle_admin_main_message_reaction(
                game_id, group_id, main_message_id, emoji_id
            )
        elif message_id in candidate_ids and emoji_id == str(EMOJI["CANCEL"]):
            await self._handle_admin_custom_input_reaction(
                game_id, group_id, message_id
            )

    async def handle_message_retraction(self, event: NoticeEvent):
        """处理消息撤回通知，如果撤回的是候选自定义输入，则自动移除"""
        if event.notice_type != "group_recall" or not self.db:
            return

        group_id = str(event.group_id)
        message_id = str(event.message_id)

        game = await self.db.get_game_by_channel_id(group_id)
        if not game:
            return

        candidate_ids = json.loads(game["candidate_custom_input_ids"])
        if message_id not in candidate_ids:
            return

        # 找到了匹配的候选输入，执行移除逻辑
        LOG.info(f"检测到候选回复 {message_id} 被撤回，将自动移除。")
        candidate_ids.remove(message_id)
        await self.db.update_candidate_custom_input_ids(
            game["game_id"], json.dumps(candidate_ids)
        )
        await self.api.post_group_msg(
            group_id, text="一条候选回复已被作者撤回，将不计入投票。", reply=game["main_message_id"]
        )
        if self.cache_manager:
            await self.cache_manager.remove_vote_item(group_id, message_id)

    async def _tally_votes(
        self, group_id: str, main_message_id: str, candidate_ids_json: str
    ) -> tuple[dict[str, int], list[str]]:
        """计票并返回分数和结果文本"""
        scores: dict[str, int] = {}
        result_lines = ["🗳️ 投票结果统计："]

        group_vote_cache = await self.cache_manager.get_group_vote_cache(group_id)

        option_emojis = {
            EMOJI["A"]: "A",
            EMOJI["B"]: "B",
            EMOJI["C"]: "C",
            EMOJI["D"]: "D",
            EMOJI["E"]: "E",
            EMOJI["F"]: "F",
            EMOJI["G"]: "G",
        }
        main_votes_cache = group_vote_cache.get(main_message_id, {}).get("votes", {})
        for emoji, option in option_emojis.items():
            count = len(main_votes_cache.get(str(emoji), set()))
            if count > 0:
                scores[option] = count
                result_lines.append(f"- 选项 {option}: {count} 票")

        candidate_ids = json.loads(candidate_ids_json)
        for cid in candidate_ids:
            item_cache = group_vote_cache.get(cid, {})
            input_votes = item_cache.get("votes", {})
            yay = len(input_votes.get(str(EMOJI["YAY"]), set()))
            nay = len(input_votes.get(str(EMOJI["NAY"]), set()))
            net_score = yay - nay

            # 只有在有人投票时才计入 scores，以供后续逻辑判断
            if yay > 0 or nay > 0:
                scores[cid] = net_score

            content = await self.content_fetcher.get_custom_input_content(group_id, cid)
            display_content = f'"{content}"' if "ID:" not in content else content
            result_lines.append(f"- {display_content}: {net_score} 票")

        return scores, result_lines

    async def _tally_and_advance(self, game_id: int, channel_id: str):
        """计票并推进游戏到下一回合"""
        # 3. 检查是否启用高级模式
        is_advanced_mode = False
        if self.channel_config:
            is_advanced_mode = await self.channel_config.is_advanced_mode_enabled(str(channel_id))
        game = await self.db.get_game_by_game_id(game_id)
        if not game:
            return

        group_id = str(game["channel_id"])
        main_message_id = str(game["main_message_id"])
        candidate_ids_json = game["candidate_custom_input_ids"]

        scores, result_lines = await self._tally_votes(
            group_id, main_message_id, candidate_ids_json
        )

        await self.game_manager.tally_and_advance(game_id, scores, result_lines, nsfw_mode=is_advanced_mode)
