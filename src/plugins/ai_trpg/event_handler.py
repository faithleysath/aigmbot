# src/plugins/ai_trpg/event_handler.py
import json
from datetime import datetime, timedelta, timezone
import aiohttp

from ncatbot.core.event import GroupMessageEvent, NoticeEvent
from ncatbot.core.event.message_segment import File, Reply, At
from ncatbot.plugin_system import NcatBotPlugin
from ncatbot.utils import get_log

from .db import Database
from .cache import CacheManager
from .game_manager import GameManager
from .renderer import MarkdownRenderer
from .utils import EMOJI, bytes_to_base64
from .content_fetcher import ContentFetcher

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
    ):
        self.plugin = plugin
        self.api = plugin.api
        self.db = db
        self.cache_manager = cache_manager
        self.game_manager = game_manager
        self.renderer = renderer
        self.config = plugin.config
        self.content_fetcher = content_fetcher

    async def handle_group_message(self, event: GroupMessageEvent):
        """处理群聊消息，包括文件上传启动和自定义输入"""
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

    async def _handle_file_upload(self, event: GroupMessageEvent, file: File):
        """处理.txt或.md文件上传，作为开启游戏的入口"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(file.url) as response:
                    if response.status != 200:
                        await event.reply("无法获取文件内容。", at=False)
                        return
                    content = await response.text()

            preview = content[:2000]
            img: bytes | None = None
            if self.renderer:
                img = await self.renderer.render_markdown(preview)

            reply_message_id = None
            if img:
                reply_message_id = await event.reply(
                    image=f"data:image/png;base64,{bytes_to_base64(img)}", at=False
                )
            else:
                reply_message_id = await event.reply(
                    f"文件预览:\n\n{preview}", at=False
                )

            if not reply_message_id:
                return

            if self.db and await self.db.is_game_running(str(event.group_id)):
                await self.api.set_msg_emoji_like(
                    reply_message_id, str(EMOJI["COFFEE"])
                )  # 频道繁忙
            else:
                await self.api.set_msg_emoji_like(
                    reply_message_id, str(EMOJI["CONFIRM"])
                )  # 确认

            key = str(reply_message_id)
            async with self.cache_manager._cache_lock:
                self.cache_manager.pending_new_games[key] = {
                    "user_id": event.user_id,
                    "system_prompt": content,
                    "message_id": event.message_id,
                    "create_time": datetime.now(timezone.utc),
                }
            await self.cache_manager.save_to_disk()
        except Exception as e:
            LOG.error(f"处理文件消息时出错: {e}", exc_info=True)
            await event.reply("处理文件时出错。", at=False)

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
        async with self.cache_manager._cache_lock:
            group_vote_cache = self.cache_manager.vote_cache.setdefault(group_id, {})
            group_vote_cache[custom_input_message_id] = {
                "content": custom_input_content,
                "votes": {},
            }
        await self.cache_manager.save_to_disk()

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
            or event.user_id == event.self_id
        ):
            return

        # 检查是否是待处理的新游戏
        if str(event.message_id) in self.cache_manager.pending_new_games:
            await self._handle_new_game_confirmation(event)
            return

        # 检查是否是游戏中的表情回应
        await self._handle_game_reaction(event)

    async def _handle_new_game_confirmation(self, event: NoticeEvent):
        """处理新游戏创建的表情确认"""
        message_id_str = str(event.message_id)
        pending_game = self.cache_manager.pending_new_games.get(message_id_str)
        if not pending_game:
            return

        # 清理过期的请求
        timeout_minutes = self.config.get("pending_game_timeout", 5)
        if datetime.now(timezone.utc) - pending_game["create_time"] > timedelta(
            minutes=timeout_minutes
        ):
            del self.cache_manager.pending_new_games[message_id_str]
            await self.cache_manager.save_to_disk()
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
                del self.cache_manager.pending_new_games[message_id_str]
                await self.cache_manager.save_to_disk()

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
            del self.cache_manager.pending_new_games[message_id_str]
            await self.cache_manager.save_to_disk()

            await self.game_manager.start_new_game(
                group_id=group_id,
                user_id=pending_game["user_id"],
                system_prompt=pending_game["system_prompt"],
            )

    async def _is_group_admin_or_host(self, group_id: str, user_id: str) -> bool:
        """检查用户是否为群管理员或游戏主持人"""
        if not self.db:
            return False
        try:
            host_user_id = await self.db.get_host_user_id(group_id)
            if host_user_id and user_id == host_user_id:
                return True  # Is the host

            member_info = await self.api.get_group_member_info(group_id, user_id)
            return member_info.role in ["admin", "owner"]
        except Exception as e:
            LOG.error(f"获取群 {group_id} 成员 {user_id} 信息失败: {e}")
            return False

    async def _handle_admin_main_message_reaction(
        self, game_id: int, group_id: str, main_message_id: str, emoji_id: str
    ):
        """处理管理员/主持人对主消息的表情回应"""
        if emoji_id == str(EMOJI["CONFIRM"]):
            await self._tally_and_advance(game_id)
        elif emoji_id == str(EMOJI["DENY"]):
            if not self.db:
                return
            game = await self.db.get_game_by_game_id(game_id)
            if not game:
                return
            _, result_lines = await self._tally_votes(
                group_id, main_message_id, game["candidate_custom_input_ids"]
            )
            await self.api.post_group_msg(
                group_id,
                text="\n".join(result_lines)
                + f"\n由于一位管理员/主持人的反对票，本轮投票并未获通过，将重新开始本輪。",
                reply=main_message_id,
            )
            if self.cache_manager:
                async with self.cache_manager._cache_lock:
                    self.cache_manager.vote_cache[group_id] = {}  # 清理本轮投票缓存
                await self.cache_manager.save_to_disk()
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
            group_id, text=" 一条自定义输入已被撤回。", reply=message_id
        )
        # 从缓存中删除
        if self.cache_manager:
            async with self.cache_manager._cache_lock:
                group_map = self.cache_manager.vote_cache.get(group_id)
                if group_map is not None:
                    group_map.pop(message_id, None)
            await self.cache_manager.save_to_disk()

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

        game = await self.db.get_game_by_channel_id(group_id)
        if not game or game["is_frozen"]:
            return

        game_id = game["game_id"]
        main_message_id = str(game["main_message_id"])
        candidate_ids = json.loads(game["candidate_custom_input_ids"])

        # --- 主动防御：只处理对有效消息的回应 ---
        if message_id != main_message_id and message_id not in candidate_ids:
            return

        # 更新投票缓存
        if self.cache_manager:
            async with self.cache_manager._cache_lock:
                group_vote_cache = self.cache_manager.vote_cache.setdefault(group_id, {})
                message_votes = group_vote_cache.setdefault(message_id, {"votes": {}})
                if "votes" not in message_votes:
                    message_votes["votes"] = {}
                vote_set = message_votes["votes"].setdefault(emoji_id, set())
                if event.is_add:
                    vote_set.add(user_id)
                else:
                    vote_set.discard(user_id)
            await self.cache_manager.save_to_disk()

        # 检查是否是管理员或主持人
        is_admin_or_host = await self._is_group_admin_or_host(group_id, user_id)
        if not is_admin_or_host:
            return

        # 根据消息ID和表情ID分发给不同的处理函数
        if message_id == main_message_id:
            await self._handle_admin_main_message_reaction(
                game_id, group_id, main_message_id, emoji_id
            )
        elif message_id in candidate_ids and emoji_id == str(EMOJI["CANCEL"]):
            await self._handle_admin_custom_input_reaction(
                game_id, group_id, message_id
            )

    async def _tally_votes(
        self, group_id: str, main_message_id: str, candidate_ids_json: str
    ) -> tuple[dict[str, int], list[str]]:
        """计票并返回分数和结果文本"""
        scores: dict[str, int] = {}
        result_lines = ["🗳️ 投票结果统计："]

        group_vote_cache = self.cache_manager.vote_cache.get(group_id, {})

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
            scores[cid] = net_score

            content = await self.content_fetcher.get_custom_input_content(group_id, cid)
            display_content = f'"{content}"' if "ID:" not in content else content
            result_lines.append(f"- 自定义输入 {display_content}: {net_score} 票")

        return scores, result_lines

    async def _tally_and_advance(self, game_id: int):
        """计票并推进游戏到下一回合"""
        game = await self.db.get_game_by_game_id(game_id)
        if not game:
            return

        group_id = str(game["channel_id"])
        main_message_id = str(game["main_message_id"])
        candidate_ids_json = game["candidate_custom_input_ids"]

        scores, result_lines = await self._tally_votes(
            group_id, main_message_id, candidate_ids_json
        )

        await self.game_manager.tally_and_advance(game_id, scores, result_lines)
