# src/plugins/ai_trpg/game_manager.py
from typing import cast
from ncatbot.utils import get_log
from ncatbot.plugin_system import NcatBotPlugin
from .db import Database
from .llm_api import LLM_API, ChatCompletionMessageParam
from .renderer import MarkdownRenderer
from .utils import EMOJI, bytes_to_base64
from .cache import CacheManager
from .content_fetcher import ContentFetcher
from .exceptions import TipChangedError

LOG = get_log(__name__)


class GameManager:
    def __init__(
        self,
        plugin: NcatBotPlugin,
        db: Database,
        llm_api: LLM_API,
        renderer: MarkdownRenderer,
        cache_manager: CacheManager,
        content_fetcher: ContentFetcher,
    ):
        self.plugin = plugin
        self.api = plugin.api
        self.db = db
        self.llm_api = llm_api
        self.renderer = renderer
        self.cache_manager = cache_manager
        self.content_fetcher = content_fetcher

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
            branch_id = await self.db.create_branch(game_id, "main", round_id)

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
            image_bytes = await self.renderer.render_markdown(assistant_response)
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
                    await self.api.set_msg_emoji_like(
                        main_message_id, str(emoji_id)
                    )
                except Exception as e:
                    LOG.warning(f"为消息 {main_message_id} 贴表情 {emoji_id} 失败: {e}")

            LOG.info(f"游戏 {game_id} 已成功检出 head，主消息 ID: {main_message_id}")

        except Exception as e:
            LOG.error(f"检出 head (game_id: {game_id}) 时出错: {e}", exc_info=True)
            if channel_id:
                await self.api.post_group_msg(
                    str(channel_id), text=f"❌ 更新游戏状态失败: {e}"
                )

    async def _build_llm_history(
        self, system_prompt: str, tip_round_id: int
    ) -> list[ChatCompletionMessageParam] | None:
        """从数据库构建用于 LLM 的对话历史"""
        if not self.db:
            return None

        history: list[ChatCompletionMessageParam] = []
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

        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt}
        ]
        messages.extend(reversed(history))
        return messages

    async def tally_and_advance(self, game_id: int, scores: dict, result_lines: list[str]):
        """计票并推进游戏到下一回合"""
        if not self.db or not self.db.conn or not self.llm_api:
            return

        channel_id = None
        main_message_id = None
        try:
            async with self.db.conn.cursor() as cursor:
                await cursor.execute(
                    "SELECT * FROM games WHERE game_id = ?", (game_id,)
                )

                game_data = await cursor.fetchone()
            if not game_data:
                return

            channel_id = str(game_data["channel_id"])
            main_message_id = str(game_data["main_message_id"] or "")
            system_prompt = game_data["system_prompt"]
            head_branch_id = game_data["head_branch_id"]

            if not scores:
                await self.api.post_group_msg(channel_id, text="无人投票，请继续投票后再确认。", reply=main_message_id)
                return

            await self.db.set_game_frozen_status(game_id, True)
            
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

            await self.api.post_group_msg(
                channel_id, text="\n".join(result_lines), reply=main_message_id
            )

            # 2. 找出胜利者
            max_score = max(scores.values())
            winners = [k for k, v in scores.items() if v == max_score]
            winner_lines = []
            for x in winners:
                if x in "ABCDEFG":
                    winner_lines.append(f"选择选项 {x}")
                else:
                    content = await self.content_fetcher.get_custom_input_content(channel_id, x)
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
            async with self.db.transaction():
                async with self.db.conn.cursor() as cursor:
                    await cursor.execute(
                        "SELECT tip_round_id FROM branches WHERE branch_id = ?",
                        (head_branch_id,),
                    )
                    latest_tip_data = await cursor.fetchone()
                    if (
                        not latest_tip_data
                        or latest_tip_data[0] != initial_tip_round_id
                    ):
                        raise TipChangedError()

                    # 创建新回合
                    await cursor.execute(
                        "INSERT INTO rounds (game_id, parent_id, player_choice, assistant_response) VALUES (?, ?, ?, ?)",
                        (
                            game_id,
                            initial_tip_round_id,
                            winner_content,
                            new_assistant_response,
                        ),
                    )
                    new_round_id = cursor.lastrowid

                    # 更新 tip
                    await cursor.execute(
                        "UPDATE branches SET tip_round_id = ? WHERE branch_id = ?",
                        (new_round_id, head_branch_id),
                    )

            # 6. 清理并进入下一轮
            await self.cache_manager.clear_group_vote_cache(channel_id)
            await self.checkout_head(game_id)

        except TipChangedError:
            if channel_id and main_message_id:
                await self.api.post_group_msg(
                    channel_id,
                    text="本轮状态已变化，为避免并发冲突本次推进已取消。",
                    reply=main_message_id,
                )
        except Exception as e:
            LOG.error(f"推进失败: {e}", exc_info=True)
        finally:
            if self.db:
                await self.db.set_game_frozen_status(game_id, False)

    async def revert_last_round(self, game_id: int):
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

            if self.cache_manager:
                await self.cache_manager.clear_group_vote_cache(str(channel_id))

            # 5. 刷新游戏界面
            await self.checkout_head(game_id)

        except Exception as e:
            LOG.error(f"回退游戏 (game_id: {game_id}) 时出错: {e}", exc_info=True)
            if channel_id:
                await self.api.post_group_msg(str(channel_id), text=f"❌ 回退失败: {e}")
