from ncatbot.plugin_system import NcatBotPlugin, command_registry, on_notice, filter_registry
from typing import cast
from ncatbot.core.event import GroupMessageEvent, NoticeEvent
from ncatbot.core.event.message_segment import File, Reply
from ncatbot.utils import get_log
from pathlib import Path
import aiohttp
import json
from datetime import datetime, timedelta

from .db import Database
from .llm_api import LLM_API, ChatCompletionMessageParam
from .renderer import MarkdownRenderer

LOG = get_log(__name__)

EMOJI = {
    # 主贴选项
    "A": 127822, "B": 9973, "C": 128663, "D": 128054,
    "E": 127859, "F": 128293, "G": 128123,
    # 管理员确认/否决（主贴）
    "CONFIRM": 9989,   # ✅
    "DENY": 10060,     # ❌
    # 自定义输入投票
    "YAY": 127881,     # 🎉
    "NAY": 128560,     # 😰
}

import base64

def bytes_to_base64(b: bytes) -> str:
    """将字节数据转换为Base64字符串"""
    return base64.b64encode(b).decode('utf-8')


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
        self.pending_new_games: dict[str, dict] = {}
        self.vote_cache: dict[str, dict[int, set[str]]] = {}

    async def on_load(self):
        """插件加载时执行的初始化操作"""
        LOG.info(f"[{self.name}] 正在加载...")
        
        # 1. 注册配置项 (示例)
        self.register_config("openai_api_key", "YOUR_API_KEY_HERE")
        self.register_config("openai_base_url", "https://api.openai.com/v1")
        self.register_config("openai_model_name", "gpt-4-turbo")
        LOG.debug(f"[{self.name}] 配置项注册完毕。")

        # 2. 初始化数据库
        db_path = self.data_path / "data" / "AITRPGPlugin" / "ai_trpg.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db = Database(str(db_path))
        await self.db.connect()
        LOG.debug(f"[{self.name}] 数据库连接成功。")

        # 3. 初始化 LLM API
        try:
            api_key = self.config.get("openai_api_key", "")
            base_url = self.config.get("openai_base_url", "https://api.openai.com/v1")
            model_name = self.config.get("openai_model_name", "gpt-4-turbo")
            self.llm_api = LLM_API(api_key=api_key, base_url=base_url, model_name=model_name)
        except ValueError as e:
            LOG.error(f"LLM API 初始化失败: {e}. 请检查相关配置。")
            self.llm_api = None

        # 4. 初始化 Markdown 渲染器
        self.renderer = MarkdownRenderer()
        LOG.debug(f"[{self.name}] Markdown渲染器初始化完成。")
        
        LOG.info(f"[{self.name}] 加载完成。")

    async def on_close(self):
        """插件关闭时执行的操作"""
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
        if files and files[0].file.endswith((".txt", ".md")):
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
                reply_message_id = await event.reply(image=f"data:image/png;base64,{bytes_to_base64(img)}", at=False)
            else:
                reply_message_id = await event.reply(f"文件预览:\n\n{preview}", at=False)

            if not reply_message_id:
                return

            if self.db and await self.db.is_game_running(str(event.group_id)):
                await self.api.set_msg_emoji_like(reply_message_id, str(EMOJI["DENY"]))      # 取消
            else:
                await self.api.set_msg_emoji_like(reply_message_id, str(EMOJI["CONFIRM"]))  # 确认
            
            key = str(reply_message_id)
            self.pending_new_games[key] = {
                "user_id": event.user_id,
                "system_prompt": content,
                "message_id": event.message_id,
                "create_time": datetime.now(),
            }
        except Exception as e:
            LOG.error(f"处理文件消息时出错: {e}", exc_info=True)
            await event.reply("处理文件时出错。", at=False)

    async def _handle_custom_input(self, event: GroupMessageEvent, reply: Reply):
        """处理对主消息的回复，作为自定义输入"""
        if not self.db or not self.db.conn: return

        group_id = str(event.group_id)
        replied_to_id = reply.id

        async with self.db.conn.cursor() as cursor:
            await cursor.execute(
                "SELECT game_id, candidate_custom_input_ids FROM games WHERE channel_id = ? AND main_message_id = ?",
                (group_id, replied_to_id)
            )
            game_row = await cursor.fetchone()

            if not game_row:
                return # 不是对当前游戏主消息的回复

            game_id, candidate_ids_json = game_row
            
            custom_input_message_id = str(event.message_id)
            
            candidate_ids = json.loads(candidate_ids_json)
            candidate_ids.append(custom_input_message_id)

            await cursor.execute(
                "UPDATE games SET candidate_custom_input_ids = ? WHERE game_id = ?",
                (json.dumps(candidate_ids), game_id)
            )
            await self.db.conn.commit()
            
            LOG.info(f"游戏 {game_id} 收到新的自定义输入: {custom_input_message_id}")

            # 为自定义输入添加投票表情
            for emoji in [127881, 128560, 10060]: # 🎉, 😰, ❌ (沿用旧版表情)
                try:
                    await self.api.set_msg_emoji_like(custom_input_message_id, emoji)
                except Exception as e:
                    LOG.warning(f"为自定义输入 {custom_input_message_id} 贴表情 {emoji} 失败: {e}")

    @on_notice
    async def handle_emoji_reaction(self, event: NoticeEvent):
        """处理表情回应，包括游戏启动、投票、撤回等"""
        if event.notice_type != "group_msg_emoji_like":
            return

        # 检查是否是待处理的新游戏
        if str(event.message_id) in self.pending_new_games:
            await self._handle_new_game_confirmation(event)
            return
        
        # 检查是否是游戏中的表情回应
        await self._handle_game_reaction(event)
    
    async def _handle_new_game_confirmation(self, event: NoticeEvent):
        """处理新游戏创建的表情确认"""
        pending_game = self.pending_new_games.get(str(event.message_id))
        if not pending_game:
            return

        # 清理过期的请求
        if datetime.now() - pending_game["create_time"] > timedelta(minutes=5):
            del self.pending_new_games[str(event.message_id)]
            return

        # 权限检查：只有发起人可以确认或取消
        if str(event.user_id) != pending_game["user_id"]:
            return

        group_id = str(event.group_id)
        message_id = str(event.message_id)

        if event.emoji_like_id == str(EMOJI["DENY"]):   # 取消
            try:
                await self.api.delete_msg(pending_game["message_id"])
                await self.api.set_msg_emoji_like(message_id, str(EMOJI["CONFIRM"]), set=False)
                await self.api.set_msg_emoji_like(message_id, str(EMOJI["DENY"]))
                await self.api.post_group_msg(group_id, " 新游戏创建已取消。", at=event.user_id, reply=message_id)
                LOG.info(f"用户 {event.user_id} 取消了新游戏创建请求。")
            except Exception as e:
                LOG.error(f"处理取消新游戏时出错: {e}")
            finally:
                del self.pending_new_games[message_id]

        elif event.emoji_like_id == str(EMOJI["CONFIRM"]):  # 确认
            if self.db and await self.db.is_game_running(group_id):
                await self.api.post_group_msg(group_id, " 当前已有正在进行的游戏，无法创建新游戏。", at=event.user_id, reply=message_id)
                await self.api.set_msg_emoji_like(message_id, str(EMOJI["DENY"]))
                await self.api.set_msg_emoji_like(message_id, str(EMOJI["CONFIRM"]), set=False)
                return
            
            await self.api.set_msg_emoji_like(message_id, str(EMOJI["CONFIRM"]))
            await self.api.set_msg_emoji_like(message_id, str(EMOJI["DENY"]), set=False)
            del self.pending_new_games[message_id]
            
            await self.start_new_game(
                group_id=group_id,
                user_id=pending_game["user_id"],
                system_prompt=pending_game["system_prompt"]
            )

    async def _is_group_admin_or_host(self, group_id: str, user_id: str) -> bool:
        """检查用户是否为群管理员或游戏主持人"""
        if not self.db or not self.db.conn:
            return False
        try:
            async with self.db.conn.cursor() as cursor:
                await cursor.execute("SELECT host_user_id FROM games WHERE channel_id = ?", (group_id,))
                game = await cursor.fetchone()
                if game and user_id == str(game[0]):
                    return True # Is the host

            member_info = await self.api.get_group_member_info(group_id, user_id)
            return member_info.role in ["admin", "owner"]
        except Exception as e:
            LOG.error(f"获取群 {group_id} 成员 {user_id} 信息失败: {e}")
            return False

    async def start_new_game(self, group_id: str, user_id: str, system_prompt: str):
        """开始一个新游戏"""
        if not self.db or not self.db.conn or not self.llm_api:
            await self.api.post_group_msg(group_id, text="❌ 插件未完全初始化，无法开始游戏。")
            return

        game_id = None
        try:
            # 1. 在数据库中创建游戏记录
            async with self.db.conn.cursor() as cursor:
                await cursor.execute(
                    "INSERT INTO games (channel_id, host_user_id, system_prompt) VALUES (?, ?, ?)",
                    (group_id, user_id, system_prompt)
                )
                game_id = cursor.lastrowid
                await self.db.conn.commit()
            LOG.info(f"群 {group_id} 创建了新游戏，ID: {game_id}。")

            # 2. 调用 LLM 获取开场白
            await self.api.post_group_msg(group_id, text="🚀 新游戏即将开始... 正在联系 GM 生成开场白...")
            initial_messages: list[ChatCompletionMessageParam] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "开始"}
            ]
            assistant_response, _ = await self.llm_api.get_completion(initial_messages)

            if not assistant_response:
                raise Exception("LLM 未能生成开场白。")

            # 3. 创建 Round 和 Branch
            async with self.db.conn.cursor() as cursor:
                # 创建第一个 round
                await cursor.execute(
                    "INSERT INTO rounds (game_id, parent_id, player_choice, assistant_response) VALUES (?, ?, ?, ?)",
                    (game_id, -1, "开始", assistant_response)
                )
                round_id = cursor.lastrowid

                # 创建 "main" 分支
                await cursor.execute(
                    "INSERT INTO branches (game_id, name, tip_round_id) VALUES (?, ?, ?)",
                    (game_id, "main", round_id)
                )
                branch_id = cursor.lastrowid

                # 更新 game 的 head_branch_id
                await cursor.execute(
                    "UPDATE games SET head_branch_id = ? WHERE game_id = ?",
                    (branch_id, game_id)
                )
                await self.db.conn.commit()
            
            LOG.info(f"游戏 {game_id} 的初始 round 和 branch 已创建。")

            # 4. 检出 head，向玩家展示
            if game_id is not None:
                await self.checkout_head(game_id)

        except Exception as e:
            LOG.error(f"开始新游戏失败: {e}", exc_info=True)
            await self.api.post_group_msg(group_id, text=f"❌ 启动游戏失败: {e}")
            # 如果游戏记录已创建，则删除
            if game_id and self.db and self.db.conn:
                async with self.db.conn.cursor() as cursor:
                    await cursor.execute("DELETE FROM games WHERE game_id = ?", (game_id,))
                    await self.db.conn.commit()
                LOG.info(f"已清理失败的游戏记录，ID: {game_id}。")


    async def checkout_head(self, game_id: int):
        """检出游戏 head 指向的分支的最新回合，并向玩家展示"""
        if not self.db or not self.db.conn or not self.renderer:
            LOG.error(f"检出 head 失败：组件未初始化。")
            return
        
        channel_id = None
        try:
            async with self.db.conn.cursor() as cursor:
                # 1. 获取游戏和 head 分支信息
                await cursor.execute(
                    """SELECT g.channel_id, b.tip_round_id
                       FROM games g
                       JOIN branches b ON g.head_branch_id = b.branch_id
                       WHERE g.game_id = ?""",
                    (game_id,)
                )
                game_info = await cursor.fetchone()
                if not game_info:
                    raise Exception("找不到游戏或其 head 分支。")
                
                channel_id, tip_round_id = game_info

                # 2. 获取最新回合的剧情
                await cursor.execute(
                    "SELECT assistant_response FROM rounds WHERE round_id = ?",
                    (tip_round_id,)
                )
                round_info = await cursor.fetchone()
                if not round_info:
                    raise Exception("找不到最新的回合信息。")
                
                assistant_response = round_info[0]

            # 3. 渲染并发送图片
            image_bytes = await self.renderer.render(assistant_response)
            if not image_bytes:
                raise Exception("渲染剧情图片失败。")

            main_message_id = await self.api.post_group_file(channel_id, image=f"data:image/png;base64,{bytes_to_base64(image_bytes)}")
            if not main_message_id:
                raise Exception("发送剧情图片失败。")

            # 4. 更新数据库
            async with self.db.conn.cursor() as cursor:
                await cursor.execute(
                    "UPDATE games SET main_message_id = ?, candidate_custom_input_ids = ? WHERE game_id = ?",
                    (main_message_id, "[]", game_id)
                )
                await self.db.conn.commit()

            # 5. 添加表情回应
            emoji_map = {
                'A': EMOJI["A"], 'B': EMOJI["B"], 'C': EMOJI["C"], 'D': EMOJI["D"],
                'E': EMOJI["E"], 'F': EMOJI["F"], 'G': EMOJI["G"],
                'Confirm': EMOJI["CONFIRM"], 'Deny': EMOJI["DENY"]
            }
            for _, emoji_id in emoji_map.items():
                try:
                    await self.api.set_msg_emoji_like(main_message_id, emoji_id)
                except Exception as e:
                    LOG.warning(f"为消息 {main_message_id} 贴表情 {emoji_id} 失败: {e}")
            
            LOG.info(f"游戏 {game_id} 已成功检出 head，主消息 ID: {main_message_id}")

        except Exception as e:
            LOG.error(f"检出 head (game_id: {game_id}) 时出错: {e}", exc_info=True)
            if channel_id:
                await self.api.post_group_msg(str(channel_id), text=f"❌ 更新游戏状态失败: {e}")

    async def _handle_game_reaction(self, event: NoticeEvent):
        """处理游戏进行中的表情回应，包括投票、撤回和管理员操作"""
        if not self.db or not self.db.conn or not event.message_id or not event.emoji_like_id:
            return

        group_id = str(event.group_id)
        user_id = str(event.user_id)
        message_id = str(event.message_id)
        emoji_id = int(event.emoji_like_id)

        # 更新投票缓存
        if event.is_add:
            self.vote_cache.setdefault(message_id, {}).setdefault(emoji_id, set()).add(user_id)
        elif message_id in self.vote_cache and emoji_id in self.vote_cache[message_id]:
            self.vote_cache[message_id][emoji_id].discard(user_id)

        # 检查是否是管理员/主持人操作
        is_admin_or_host = await self._is_group_admin_or_host(group_id, user_id)
        if is_admin_or_host:
            # 确认或否决回合
            if emoji_id in [EMOJI["CONFIRM"], EMOJI["DENY"]]:
                async with self.db.conn.cursor() as cursor:
                    await cursor.execute("SELECT game_id FROM games WHERE main_message_id = ?", (message_id,))
                    game = await cursor.fetchone()
                    if game:
                        if emoji_id == EMOJI["CONFIRM"]:
                            await self._tally_and_advance(game[0])
                        else:
                            await self.api.post_group_msg(group_id, text="本轮投票已被管理员/主持人作废，将重新开始本轮。", reply=message_id)
                            await self.checkout_head(game[0])
                        return

        # 检查是否是撤回自定义输入
        if emoji_id == EMOJI["DENY"]: # ❌ (沿用旧版表情作为撤回)
            async with self.db.conn.cursor() as cursor:
                await cursor.execute("SELECT game_id, candidate_custom_input_ids FROM games WHERE channel_id = ?", (group_id,))
                game = await cursor.fetchone()
                if game:
                    game_id, candidate_ids_json = game
                    candidate_ids = json.loads(candidate_ids_json)
                    if message_id in candidate_ids:
                        # 权限检查：只有作者或管理员/主持人可以撤回
                        # (简化：此处仅检查是否是管理员/主持人，实际可查询消息发送者)
                        if is_admin_or_host: # 实际应更复杂
                            candidate_ids.remove(message_id)
                            await cursor.execute("UPDATE games SET candidate_custom_input_ids = ? WHERE game_id = ?", (json.dumps(candidate_ids), game_id))
                            await self.db.conn.commit()
                            await self.api.post_group_msg(group_id, text="一条自定义输入已被撤回。", reply=message_id)
                            # 从缓存中删除
                            self.vote_cache.pop(message_id, None)

    async def _tally_and_advance(self, game_id: int):
        """计票并推进游戏到下一回合"""
        if not self.db or not self.db.conn or not self.llm_api: return

        async with self.db.conn.cursor() as cursor:
            await cursor.execute("SELECT channel_id, main_message_id, candidate_custom_input_ids FROM games WHERE game_id = ?", (game_id,))
            game = await cursor.fetchone()
            if not game: return
            channel_id, main_message_id, candidate_ids_json = game
        
        # 计票
        scores = {}
        result_lines = ["🗳️ 投票结果统计："]
        
        # 预设选项
        option_emojis = {
            EMOJI["A"]: 'A', EMOJI["B"]: 'B', EMOJI["C"]: 'C', EMOJI["D"]: 'D',
            EMOJI["E"]: 'E', EMOJI["F"]: 'F', EMOJI["G"]: 'G'
        }
        main_votes = self.vote_cache.get(str(main_message_id), {})
        for emoji, option in option_emojis.items():
            count = len(main_votes.get(emoji, set()))
            if count > 0:
                scores[option] = count
                result_lines.append(f"- 选项 {option}: {count} 票")

        # 自定义输入
        candidate_ids = json.loads(candidate_ids_json)
        for cid in candidate_ids:
            input_votes = self.vote_cache.get(cid, {})
            yay = len(input_votes.get(EMOJI["YAY"], set()))
            nay = len(input_votes.get(EMOJI["NAY"], set()))
            net_score = yay - nay
            scores[cid] = net_score
            # 为了显示内容，需要获取消息
            try:
                msg_event = await self.api.get_msg(cid)
                content = "".join(s.text for s in msg_event.message.filter_text())
                result_lines.append(f"- 自定义输入 \"{content[:20]}...\": {net_score} 票")
            except:
                result_lines.append(f"- 自定义输入 (ID: {cid}): {net_score} 票")

        await self.api.post_group_msg(channel_id, text="\n".join(result_lines), reply=main_message_id)

        if not scores:
            await self.api.post_group_msg(channel_id, text="无人投票，本轮无效。")
            return

        # 找出胜利者
        winner_id = max(scores, key=lambda k: scores[k])
        
        # 获取胜利者内容
        winner_content = ""
        if winner_id in option_emojis.values():
            winner_content = f"选择选项 {winner_id}"
        else:
            try:
                msg_event = await self.api.get_msg(winner_id)
                winner_content = "".join(s.text for s in msg_event.message.filter_text())
            except Exception as e:
                LOG.error(f"获取胜利者自定义输入内容失败: {e}")
                await self.api.post_group_msg(channel_id, text="获取胜利者内容失败，游戏中断。")
                return

        # 构造完整的对话历史
        async with self.db.conn.cursor() as cursor:
            await cursor.execute("SELECT system_prompt, head_branch_id FROM games WHERE game_id = ?", (game_id,))
            game_data = await cursor.fetchone()
            if not game_data: return
            system_prompt, head_branch_id = game_data

            await cursor.execute("SELECT tip_round_id FROM branches WHERE branch_id = ?", (head_branch_id,))
            branch_data = await cursor.fetchone()
            if not branch_data: return
            current_round_id = branch_data[0]

            # 版本校验
            await cursor.execute("SELECT tip_round_id FROM branches WHERE branch_id = ?", (head_branch_id,))
            tip_now_data = await cursor.fetchone()
            if not tip_now_data or tip_now_data[0] != current_round_id:
                await self.api.post_group_msg(channel_id, text="本轮状态已变化，为避免并发冲突本次推进已取消。", reply=main_message_id)
                return

            history = []
            while current_round_id != -1:
                await cursor.execute("SELECT parent_id, player_choice, assistant_response FROM rounds WHERE round_id = ?", (current_round_id,))
                round_data = await cursor.fetchone()
                if not round_data: break
                parent_id, player_choice, assistant_response = round_data
                history.insert(0, {"role": "assistant", "content": assistant_response})
                history.insert(0, {"role": "user", "content": player_choice})
                current_round_id = parent_id
            
            messages: list[ChatCompletionMessageParam] = [{"role": "system", "content": system_prompt}]
            # The 'history' list may contain dicts that are not valid ChatCompletionMessageParam
            # so we need to cast them.
            for item in history:
                messages.append(cast(ChatCompletionMessageParam, item))
            messages.append({"role": "user", "content": winner_content})

        # 调用LLM获取下一轮内容
        new_assistant_response, _ = await self.llm_api.get_completion(messages)
        if not new_assistant_response:
            await self.api.post_group_msg(channel_id, text="GM没有回应，游戏中断。")
            return

        # 创建新回合和更新分支
        async with self.db.conn.cursor() as cursor:
            await cursor.execute("SELECT tip_round_id FROM branches WHERE branch_id = ?", (head_branch_id,))
            parent_round_data = await cursor.fetchone()
            if not parent_round_data:
                LOG.error(f"无法找到 parent_round_id for branch {head_branch_id}")
                return
            parent_round_id = parent_round_data[0]

            await cursor.execute(
                "INSERT INTO rounds (game_id, parent_id, player_choice, assistant_response) VALUES (?, ?, ?, ?)",
                (game_id, parent_round_id, winner_content, new_assistant_response)
            )
            new_round_id = cursor.lastrowid

            await cursor.execute("UPDATE branches SET tip_round_id = ? WHERE branch_id = ?", (new_round_id, head_branch_id))
            await self.db.conn.commit()

        # 进入下一轮
        await self.checkout_head(game_id)
        
        # 清理缓存
        self.vote_cache.pop(str(main_message_id), None)
        for cid in candidate_ids:
            self.vote_cache.pop(cid, None)
