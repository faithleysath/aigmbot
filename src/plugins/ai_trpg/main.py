from ncatbot.plugin_system import (
    NcatBotPlugin,
    command_registry,
    on_notice,
    filter_registry,
)
from typing import cast, TypedDict, Literal, Any, NotRequired
from ncatbot.core.event import GroupMessageEvent, NoticeEvent
from ncatbot.core.event.message_segment import File, Reply, At
from ncatbot.utils import get_log
from pathlib import Path
import aiohttp
import json
from datetime import datetime, timedelta, timezone
import aiofiles
import aiofiles.os as aio_os
import aiosqlite

from .db import Database
from .llm_api import LLM_API, ChatCompletionMessageParam
from .renderer import MarkdownRenderer

LOG = get_log(__name__)


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class VoteCacheItem(TypedDict):
    content: NotRequired[str]
    votes: dict[str, set[str]]


EMOJI = {
    # 主贴选项
    "A": 127822,
    "B": 9973,
    "C": 128663,
    "D": 128054,
    "E": 127859,
    "F": 128293,
    "G": 128123,
    # 管理员确认/否决（主贴）
    "CONFIRM": 127881,  # 🎉
    "DENY": 128560,  # 😰
    "RETRACT": 10060,  # ❌
    # 自定义输入投票
    "YAY": 127881,  # 🎉
    "NAY": 128560,  # 😰
    "CANCEL": 10060,  # ❌
    # 频道繁忙
    "COFFEE": 9749,  # ☕
}

import base64


def bytes_to_base64(b: bytes) -> str:
    """将字节数据转换为Base64字符串"""
    return base64.b64encode(b).decode("utf-8")


def _normalize_emoji_id(emoji_id: Any) -> str:
    """将表情ID统一转换为字符串"""
    return str(emoji_id)


class AITRPGPlugin(NcatBotPlugin):
    name = "AITRPGPlugin"
    version = "1.0.0"
    description = "一个基于 AI GM 和 Git 版本控制概念的互动叙事游戏插件"
    author = "Cline"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.db: Database | None = None
        self.llm_api: LLM_API | None = None
        self.renderer: MarkdownRenderer | None = None
        self.data_path: Path = Path()
        self.cache_path: Path | None = None
        self.pending_new_games: dict[str, dict] = {}
        self.vote_cache: dict[str, dict[str, VoteCacheItem]] = {}

    async def _load_cache_from_disk(self):
        """从磁盘加载缓存文件"""
        if not self.cache_path or not await aio_os.path.exists(self.cache_path):
            return
        try:
            async with aiofiles.open(self.cache_path, "r", encoding="utf-8") as f:
                content = await f.read()
                data = json.loads(content)

                # 恢复 pending_new_games，转换时间字符串
                self.pending_new_games = data.get("pending_new_games", {})
                for key, game in self.pending_new_games.items():
                    if "create_time" in game and isinstance(game["create_time"], str):
                        game["create_time"] = datetime.fromisoformat(
                            game["create_time"]
                        )

                # 恢复 vote_cache，转换 set
                raw_vote_cache = data.get("vote_cache", {})
                self.vote_cache = {}
                for group_id, messages in raw_vote_cache.items():
                    self.vote_cache[group_id] = {}
                    for msg_id, data in messages.items():
                        item: VoteCacheItem = {"votes": {}}
                        if "content" in data and data["content"] is not None:
                            item["content"] = data["content"]

                        if "votes" in data:
                            item["votes"] = {
                                _normalize_emoji_id(k): set(v)
                                for k, v in data["votes"].items()
                            }
                        self.vote_cache[group_id][msg_id] = item
            LOG.info("成功从磁盘加载缓存。")
        except Exception as e:
            LOG.error(f"从磁盘加载缓存失败: {e}", exc_info=True)

    async def _save_cache_to_disk(self):
        """将当前缓存保存到磁盘"""
        if not self.cache_path:
            return
        try:
            # 准备待序列化的数据
            serializable_pending = {
                key: {**game, "create_time": game["create_time"].isoformat()}
                for key, game in self.pending_new_games.items()
            }
            serializable_votes = {
                group_id: {
                    msg_id: {
                        "content": item.get("content"),
                        "votes": {
                            emoji_id: list(users)
                            for emoji_id, users in item.get("votes", {}).items()
                        },
                    }
                    for msg_id, item in messages.items()
                }
                for group_id, messages in self.vote_cache.items()
            }

            data = {
                "pending_new_games": serializable_pending,
                "vote_cache": serializable_votes,
            }

            async with aiofiles.open(self.cache_path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data, indent=4, ensure_ascii=False))
        except Exception as e:
            LOG.error(f"保存缓存到磁盘失败: {e}", exc_info=True)

    async def on_load(self):
        """插件加载时执行的初始化操作"""
        LOG.info(f"[{self.name}] 正在加载...")

        # 1. 注册配置项 (示例)
        self.register_config("openai_api_key", "YOUR_API_KEY_HERE")
        self.register_config("openai_base_url", "https://api.openai.com/v1")
        self.register_config("openai_model_name", "gpt-4-turbo")
        LOG.debug(f"[{self.name}] 配置项注册完毕。")

        # 2. 初始化数据库和缓存路径
        data_dir = self.data_path / "data" / "AITRPGPlugin"
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "ai_trpg.db"
        self.cache_path = data_dir / "cache.json"

        self.db = Database(str(db_path))
        await self.db.connect()
        LOG.debug(f"[{self.name}] 数据库连接成功。")

        # 3. 加载缓存
        await self._load_cache_from_disk()

        # 4. 初始化 LLM API
        try:
            api_key = self.config.get("openai_api_key", "")
            base_url = self.config.get("openai_base_url", "https://api.openai.com/v1")
            model_name = self.config.get("openai_model_name", "gpt-4-turbo")
            self.llm_api = LLM_API(
                api_key=api_key, base_url=base_url, model_name=model_name
            )
        except ValueError as e:
            LOG.error(f"LLM API 初始化失败: {e}. 请检查相关配置。")
            self.llm_api = None

        # 4. 初始化 Markdown 渲染器
        self.renderer = MarkdownRenderer()
        LOG.debug(f"[{self.name}] Markdown渲染器初始化完成。")

        LOG.info(f"[{self.name}] 加载完成。")

    async def on_close(self):
        """插件关闭时执行的操作"""
        await self._save_cache_to_disk()
        if self.db:
            await self.db.close()
        if self.renderer:
            await self.renderer.close()
        LOG.info(f"[{self.name}] 已卸载。")

    # --- 核心游戏逻辑 (待实现) ---

    @filter_registry.group_filter
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
                img = await self.renderer.render(preview)

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
            self.pending_new_games[key] = {
                "user_id": event.user_id,
                "system_prompt": content,
                "message_id": event.message_id,
                "create_time": datetime.now(timezone.utc),
            }
            await self._save_cache_to_disk()
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
        group_vote_cache = self.vote_cache.setdefault(group_id, {})
        group_vote_cache[custom_input_message_id] = {
            "content": custom_input_content,
            "votes": {},
        }
        await self._save_cache_to_disk()

        LOG.info(f"游戏 {game_id} 收到新的自定义输入: {custom_input_message_id}")

        # 为自定义输入添加投票表情
        for emoji_key in ["YAY", "NAY", "CANCEL"]:
            try:
                await self.api.set_msg_emoji_like(
                    custom_input_message_id, EMOJI[emoji_key]
                )
            except Exception as e:
                LOG.warning(
                    f"为自定义输入 {custom_input_message_id} 贴表情 {EMOJI[emoji_key]} 失败: {e}"
                )

    @on_notice
    async def handle_emoji_reaction(self, event: NoticeEvent):
        """处理表情回应，包括游戏启动、投票、撤回等"""
        if (
            event.notice_type != "group_msg_emoji_like"
            or event.user_id == event.self_id
        ):
            return

        # 检查是否是待处理的新游戏
        if str(event.message_id) in self.pending_new_games:
            await self._handle_new_game_confirmation(event)
            return

        # 检查是否是游戏中的表情回应
        await self._handle_game_reaction(event)

    async def _handle_new_game_confirmation(self, event: NoticeEvent):
        """处理新游戏创建的表情确认"""
        message_id_str = str(event.message_id)
        pending_game = self.pending_new_games.get(message_id_str)
        if not pending_game:
            return

        # 清理过期的请求
        if datetime.now(timezone.utc) - pending_game["create_time"] > timedelta(
            minutes=5
        ):
            del self.pending_new_games[message_id_str]
            await self._save_cache_to_disk()
            return

        # 权限检查：只有发起人可以确认或取消
        if str(event.user_id) != pending_game["user_id"]:
            return

        group_id = str(event.group_id)
        emoji_id = _normalize_emoji_id(event.emoji_like_id)

        if emoji_id == _normalize_emoji_id(EMOJI["COFFEE"]):  # 频道繁忙
            try:
                await self.api.delete_msg(pending_game["message_id"])
                await self.api.set_msg_emoji_like(
                    message_id_str, _normalize_emoji_id(EMOJI["CONFIRM"]), set=False
                )
                await self.api.set_msg_emoji_like(
                    message_id_str, _normalize_emoji_id(EMOJI["COFFEE"])
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
                del self.pending_new_games[message_id_str]
                await self._save_cache_to_disk()

        elif emoji_id == _normalize_emoji_id(EMOJI["CONFIRM"]):  # 确认
            if self.db and await self.db.is_game_running(group_id):
                await self.api.post_group_msg(
                    group_id,
                    " 当前已有正在进行的游戏，无法创建新游戏。",
                    at=event.user_id,
                    reply=message_id_str,
                )
                await self.api.set_msg_emoji_like(
                    message_id_str, _normalize_emoji_id(EMOJI["COFFEE"])
                )
                await self.api.set_msg_emoji_like(
                    message_id_str, _normalize_emoji_id(EMOJI["CONFIRM"]), set=False
                )
                return

            await self.api.set_msg_emoji_like(
                message_id_str, _normalize_emoji_id(EMOJI["CONFIRM"])
            )
            await self.api.set_msg_emoji_like(
                message_id_str, _normalize_emoji_id(EMOJI["COFFEE"]), set=False
            )
            del self.pending_new_games[message_id_str]
            await self._save_cache_to_disk()

            await self.start_new_game(
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

    async def start_new_game(self, group_id: str, user_id: str, system_prompt: str):
        """开始一个新游戏"""
        if not self.db or not self.db.conn or not self.llm_api:
            await self.api.post_group_msg(
                group_id, text="❌ 插件未完全初始化，无法开始游戏。"
            )
            return

        game_id = None
        try:
            # 1. 在数据库中创建游戏记录
            game_id = await self.db.create_game(group_id, user_id, system_prompt)
            if not game_id:
                raise Exception("创建游戏失败")
            LOG.info(f"群 {group_id} 创建了新游戏，ID: {game_id}。")

            # 2. 调用 LLM 获取开场白
            await self.api.post_group_msg(
                group_id, text="🚀 新游戏即将开始... 正在联系 GM 生成开场白..."
            )
            initial_messages: list[ChatCompletionMessageParam] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "开始"},
            ]
            assistant_response, _ = await self.llm_api.get_completion(initial_messages)

            if not assistant_response:
                raise Exception("LLM 未能生成开场白。")

            # 3. 创建 Round 和 Branch
            round_id = await self.db.create_round(
                game_id, -1, "开始", assistant_response
            )
            if not round_id:
                raise Exception("创建初始 round 失败")

            branch_id = await self.db.create_branch(game_id, "main", round_id)
            if not branch_id:
                raise Exception("创建初始 branch 失败")

            await self.db.update_game_head_branch(game_id, branch_id)

            LOG.info(f"游戏 {game_id} 的初始 round 和 branch 已创建。")

            # 4. 检出 head，向玩家展示
            if game_id is not None:
                await self.checkout_head(game_id)

        except Exception as e:
            LOG.error(f"开始新游戏失败: {e}", exc_info=True)
            await self.api.post_group_msg(group_id, text=f"❌ 启动游戏失败: {e}")
            # 如果游戏记录已创建，则删除
            if game_id and self.db:
                await self.db.delete_game(game_id)
                LOG.info(f"已清理失败的游戏记录，ID: {game_id}。")

    async def checkout_head(self, game_id: int):
        """检出游戏 head 指向的分支的最新回合，并向玩家展示"""
        if not self.db or not self.db.conn or not self.renderer:
            LOG.error(f"检出 head 失败：组件未初始化。")
            return

        channel_id = None
        try:
            # 1. 获取游戏和 head 分支信息
            game_info = await self.db.get_game_and_head_branch_info(game_id)
            if not game_info:
                raise Exception("找不到游戏或其 head 分支。")

            channel_id, tip_round_id = (
                game_info["channel_id"],
                game_info["tip_round_id"],
            )

            # 2. 获取最新回合的剧情
            round_info = await self.db.get_round_info(tip_round_id)
            if not round_info:
                raise Exception("找不到最新的回合信息。")

            assistant_response = round_info["assistant_response"]

            # 3. 渲染并发送图片
            image_bytes = await self.renderer.render(assistant_response)
            if not image_bytes:
                raise Exception("渲染剧情图片失败。")

            main_message_id = await self.api.post_group_file(
                channel_id,
                image=f"data:image/png;base64,{bytes_to_base64(image_bytes)}",
            )
            if not main_message_id:
                raise Exception("发送剧情图片失败。")

            # 4. 更新数据库
            await self.db.update_game_main_message(game_id, main_message_id)

            # 5. 添加表情回应
            emoji_list = [
                EMOJI["A"],
                EMOJI["B"],
                EMOJI["C"],
                EMOJI["D"],
                EMOJI["E"],
                EMOJI["F"],
                EMOJI["G"],
                EMOJI["CONFIRM"],
                EMOJI["DENY"],
                EMOJI["RETRACT"],
            ]
            for emoji_id in emoji_list:
                try:
                    await self.api.set_msg_emoji_like(main_message_id, emoji_id)
                except Exception as e:
                    LOG.warning(f"为消息 {main_message_id} 贴表情 {emoji_id} 失败: {e}")

            LOG.info(f"游戏 {game_id} 已成功检出 head，主消息 ID: {main_message_id}")

        except Exception as e:
            LOG.error(f"检出 head (game_id: {game_id}) 时出错: {e}", exc_info=True)
            if channel_id:
                await self.api.post_group_msg(
                    str(channel_id), text=f"❌ 更新游戏状态失败: {e}"
                )

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
        emoji_id = _normalize_emoji_id(event.emoji_like_id)

        if not self.db:
            return
        game = await self.db.get_game_by_channel_id(group_id)
        if not game:
            return

        if game["is_frozen"]:
            LOG.info(f"游戏 {game['game_id']} 已冻结，忽略表情回应。")
            return

        game_id, main_message_id, candidate_ids_json = (
            game["game_id"],
            game["main_message_id"],
            game["candidate_custom_input_ids"],
        )
        candidate_ids: list = json.loads(candidate_ids_json)

        # --- 主动防御：只缓存有效投票 ---
        if message_id != str(main_message_id) and message_id not in candidate_ids:
            return  # 不是对当前游戏有效消息的回应，直接忽略

        # 更新投票缓存
        group_vote_cache = self.vote_cache.setdefault(group_id, {})
        message_votes = group_vote_cache.setdefault(message_id, {"votes": {}})

        # 确保 'votes' 键存在
        if "votes" not in message_votes:
            message_votes["votes"] = {}

        vote_set = message_votes["votes"].setdefault(emoji_id, set())
        if event.is_add:
            vote_set.add(user_id)
        else:
            vote_set.discard(user_id)
        await self._save_cache_to_disk()

        is_admin_or_host = await self._is_group_admin_or_host(group_id, user_id)

        # 处理管理员/主持人往main_message_id上贴表情的行为
        if message_id == str(main_message_id) and is_admin_or_host:
            # 判断三种操作
            if emoji_id == _normalize_emoji_id(EMOJI["CONFIRM"]):
                await self._tally_and_advance(int(game_id))
                return
            elif emoji_id == _normalize_emoji_id(EMOJI["DENY"]):
                _, result_lines = await self._tally_votes(
                    group_id, str(main_message_id), candidate_ids_json
                )
                await self.api.post_group_msg(
                    group_id,
                    text="\n".join(result_lines)
                    + f"\n由于一位管理员/主持人的反对票，本轮投票并未获通过，将重新开始本輪。",
                    reply=message_id,
                )
                self.vote_cache[group_id] = {}  # 清理本轮投票缓存
                await self._save_cache_to_disk()
                await self.checkout_head(int(game_id))
                return
            elif emoji_id == _normalize_emoji_id(EMOJI["RETRACT"]):
                await self._revert_last_round(int(game_id))
                return

        # 处理管理员/主持人撤回自定义输入的行为
        if (
            message_id in candidate_ids
            and is_admin_or_host
            and emoji_id == _normalize_emoji_id(EMOJI["CANCEL"])
        ):
            candidate_ids.remove(message_id)
            await self.db.update_candidate_custom_input_ids(
                game_id, json.dumps(candidate_ids)
            )
            await self.api.post_group_msg(
                group_id, text=" 一条自定义输入已被撤回。", reply=message_id
            )
            # 从缓存中删除
            if group_id in self.vote_cache:
                self.vote_cache[group_id].pop(message_id, None)
            await self._save_cache_to_disk()
            return

    async def _tally_votes(
        self, group_id: str, main_message_id: str, candidate_ids_json: str
    ) -> tuple[dict[str, int], list[str]]:
        """计票并返回分数和结果文本"""
        scores: dict[str, int] = {}
        result_lines = ["🗳️ 投票结果统计："]

        group_vote_cache = self.vote_cache.get(group_id, {})

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
            count = len(main_votes_cache.get(_normalize_emoji_id(emoji), set()))
            if count > 0:
                scores[option] = count
                result_lines.append(f"- 选项 {option}: {count} 票")

        candidate_ids = json.loads(candidate_ids_json)
        for cid in candidate_ids:
            item_cache = group_vote_cache.get(cid, {})
            input_votes = item_cache.get("votes", {})
            yay = len(input_votes.get(_normalize_emoji_id(EMOJI["YAY"]), set()))
            nay = len(input_votes.get(_normalize_emoji_id(EMOJI["NAY"]), set()))
            net_score = yay - nay
            scores[cid] = net_score

            content = item_cache.get("content", "")
            if not content:
                try:
                    msg_event = await self.api.get_msg(cid)
                    content = "".join(s.text for s in msg_event.message.filter_text())
                    item_cache["content"] = content
                    await self._save_cache_to_disk()
                except Exception as e:
                    LOG.warning(f"获取消息 {cid} 内容失败: {e}")

            display_content = f'"{content}"' if content else f"(ID: {cid})"
            result_lines.append(f"- 自定义输入 {display_content}: {net_score} 票")

        return scores, result_lines

    async def _build_llm_history(
        self, system_prompt: str, tip_round_id: int
    ) -> list[ChatMessage] | None:
        """从数据库构建用于 LLM 的对话历史"""
        if not self.db:
            return None

        history: list[ChatMessage] = []
        current_round_id = tip_round_id
        while current_round_id != -1:
            round_data = await self.db.get_round_info(current_round_id)
            if not round_data:
                break
            history.append(
                {"role": "assistant", "content": round_data["assistant_response"]}
            )
            history.append({"role": "user", "content": round_data["player_choice"]})
            current_round_id = round_data["parent_id"]

        messages: list[ChatMessage] = [{"role": "system", "content": system_prompt}]
        messages.extend(reversed(history))
        return messages

    async def _tally_and_advance(self, game_id: int):
        """计票并推进游戏到下一回合"""
        if not self.db or not self.db.conn or not self.llm_api:
            return

        channel_id = None
        try:
            await self.db.set_game_frozen_status(game_id, True)

            async with self.db.conn.cursor() as cursor:
                cursor.row_factory = aiosqlite.Row
                await cursor.execute(
                    "SELECT * FROM games WHERE game_id = ?", (game_id,)
                )
                game_data = await cursor.fetchone()
            if not game_data:
                return

            channel_id = game_data["channel_id"]
            main_message_id = game_data["main_message_id"]
            candidate_ids_json = game_data["candidate_custom_input_ids"]
            system_prompt = game_data["system_prompt"]
            head_branch_id = game_data["head_branch_id"]

            # Get tip_round_id
            async with self.db.conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT tip_round_id FROM branches WHERE branch_id = ?",
                    (head_branch_id,),
                )
                tip_now_data = await cursor.fetchone()
                if not tip_now_data:
                    return
                initial_tip_round_id = tip_now_data[0]

            # 1. 计票
            scores, result_lines = await self._tally_votes(
                channel_id, str(main_message_id), candidate_ids_json
            )
            await self.api.post_group_msg(
                channel_id, text="\n".join(result_lines), reply=main_message_id
            )

            if not scores:
                await self.api.post_group_msg(channel_id, text="无人投票，本轮无效。")
                return

            # 2. 找出胜利者
            max_score = max(scores.values())
            winners = [k for k, v in scores.items() if v == max_score]
            winner_lines = []
            group_vote_cache = self.vote_cache.get(channel_id, {})
            for x in winners:
                if x in "ABCDEFG":
                    winner_lines.append(f"选择选项 {x}")
                else:
                    item_cache = group_vote_cache.get(x, {})
                    content = item_cache.get("content")
                    if not content:
                        try:
                            msg = await self.api.get_msg(x)
                            content = "".join(s.text for s in msg.message.filter_text())
                            item_cache["content"] = content
                            await self._save_cache_to_disk()
                        except Exception as e:
                            LOG.warning(f"获取胜利者消息 {x} 内容失败: {e}")
                            content = f"自定义输入 (ID: {x})"
                    winner_lines.append(content)
            winner_content = "\n".join(winner_lines)

            # 3. 构建历史
            messages = await self._build_llm_history(
                system_prompt, initial_tip_round_id
            )
            if not messages:
                await self.api.post_group_msg(
                    channel_id, text="构建对话历史失败，游戏中断。"
                )
                return
            messages.append({"role": "user", "content": winner_content})

            # 4. 调用LLM
            new_assistant_response, _ = await self.llm_api.get_completion(
                cast(list[ChatCompletionMessageParam], messages)
            )
            if not new_assistant_response:
                await self.api.post_group_msg(channel_id, text="GM没有回应，游戏中断。")
                return

            # 5. 数据库操作
            async with self.db.conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT tip_round_id FROM branches WHERE branch_id = ?",
                    (head_branch_id,),
                )
                latest_tip_data = await cursor.fetchone()
                if not latest_tip_data or latest_tip_data[0] != initial_tip_round_id:
                    await self.api.post_group_msg(
                        channel_id,
                        text="本轮状态已变化，为避免并发冲突本次推进已取消。",
                        reply=main_message_id,
                    )
                    return

            new_round_id = await self.db.create_round(
                game_id, initial_tip_round_id, winner_content, new_assistant_response
            )
            if not new_round_id:
                raise Exception("创建新 round 失败")

            await self.db.update_branch_tip(head_branch_id, new_round_id)

            # 6. 清理并进入下一轮
            self.vote_cache[channel_id] = {}
            await self._save_cache_to_disk()
            await self.checkout_head(game_id)

        finally:
            if self.db:
                await self.db.set_game_frozen_status(game_id, False)

    async def _revert_last_round(self, game_id: int):
        """将游戏回退到上一轮"""
        if not self.db or not self.db.conn:
            return

        channel_id = None
        try:
            game_info = await self.db.get_game_and_head_branch_info(game_id)
            if not game_info:
                raise Exception("找不到游戏或其 head 分支。")

            channel_id, tip_round_id = (
                game_info["channel_id"],
                game_info["tip_round_id"],
            )

            round_info = await self.db.get_round_info(tip_round_id)
            if not round_info:
                raise Exception("找不到当前回合信息。")

            parent_id = round_info["parent_id"]

            if parent_id == -1:
                await self.api.post_group_msg(
                    str(channel_id), text="已经是第一轮了，无法再回退。"
                )
                return

            async with self.db.conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT head_branch_id FROM games WHERE game_id = ?", (game_id,)
                )
                head_branch_id_tuple = await cursor.fetchone()
                if not head_branch_id_tuple:
                    raise Exception("找不到游戏的 head_branch_id")
                head_branch_id = head_branch_id_tuple[0]
                await self.db.update_branch_tip(head_branch_id, parent_id)

            LOG.info(f"游戏 {game_id} 已成功回退到 round {parent_id}。")
            await self.api.post_group_msg(
                str(channel_id), text="🔄 游戏已成功回退到上一轮。"
            )

            # 5. 刷新游戏界面
            await self.checkout_head(game_id)

        except Exception as e:
            LOG.error(f"回退游戏 (game_id: {game_id}) 时出错: {e}", exc_info=True)
            if channel_id:
                await self.api.post_group_msg(str(channel_id), text=f"❌ 回退失败: {e}")
