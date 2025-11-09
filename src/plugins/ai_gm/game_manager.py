import json
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
from .constants import MAX_HISTORY_ROUNDS

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
        """
        开始一个新游戏。

        Args:
            group_id: 游戏所在的群组ID。
            user_id: 游戏的发起者（主持人）ID。
            system_prompt: 游戏的系统提示词。
        """
        if not self.db or not self.db.conn or not self.llm_api:
            await self.api.post_group_msg(
                group_id, text="❌ 插件未完全初始化，无法开始游戏。"
            )
            return

        game_id = None
        try:
            # 1. 在数据库中创建游戏记录
            game_id = await self.db.create_game(group_id, user_id, system_prompt)
            LOG.info(f"群 {group_id} 创建了新游戏，ID: {game_id}")

            # 2. 调用 LLM 获取开场白
            await self.api.post_group_msg(
                group_id, text="🚀 新游戏即将开始... 正在联系 GM 生成开场白..."
            )
            initial_messages: list[ChatCompletionMessageParam] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "开始"},
            ]
            assistant_response, usage, model_name = await self.llm_api.get_completion(
                initial_messages
            )

            if not assistant_response:
                raise Exception("LLM 未能生成开场白。")

            # 3. 创建 Round 和 Branch
            round_id = await self.db.create_round(
                game_id,
                -1,
                "开始",
                assistant_response,
                llm_usage=json.dumps(usage) if usage else None,
                model_name=model_name,
            )
            branch_id = await self.db.create_branch(game_id, "main", round_id)

            await self.db.update_game_head_branch(game_id, branch_id)

            LOG.info(f"游戏 {game_id} 的初始 round 和 branch 已创建")

            # 4. 检出 head，向玩家展示
            if game_id is not None:
                await self.checkout_head(game_id)

        except Exception as e:
            LOG.error(f"开始新游戏失败: {e}", exc_info=True)
            await self.api.post_group_msg(group_id, text=f"❌ 启动游戏失败: {e}")
            # 如果游戏记录已创建，则删除
            if game_id and self.db:
                await self.db.delete_game(game_id)
                LOG.info(f"已清理失败的游戏记录，ID: {game_id}")

    async def checkout_head(self, game_id: int):
        """
        检出并显示游戏的HEAD分支的最新状态。

        这包括渲染最新回合的内容作为图片，发送到频道，并更新主消息ID。

        Args:
            game_id: 要检出的游戏ID。
        """
        if not self.db or not self.db.conn or not self.renderer or not self.cache_manager:
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

            # 清理当前频道的投票缓存
            await self.cache_manager.clear_group_vote_cache(str(channel_id))

            # 2. 获取最新回合的剧情
            round_info = await self.db.get_round_info(tip_round_id)
            if not round_info:
                raise Exception("找不到最新的回合信息。")

            assistant_response = round_info["assistant_response"]
            llm_usage_str = round_info["llm_usage"]
            extra_text = None
            if llm_usage_str:
                try:
                    usage = json.loads(llm_usage_str)
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    if prompt_tokens > 0:
                        extra_text = f"{round(prompt_tokens / 1000)}k / 1M"
                except (json.JSONDecodeError, TypeError):
                    LOG.warning(f"无法解析 llm_usage: {llm_usage_str}")

            # 3. 渲染并发送图片
            image_bytes = await self.renderer.render_markdown(
                assistant_response, extra_text=extra_text
            )
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
        """
        从数据库构建用于 LLM 的对话历史。
        
        使用递归 CTE 一次性获取所有祖先回合，避免 N+1 查询问题。
        
        Args:
            system_prompt: 系统提示词
            tip_round_id: 当前回合ID
            
        Returns:
            完整的对话历史列表，如果失败则返回 None
        """
        if not self.db:
            return None

        # 使用递归 CTE 一次性获取所有历史回合
        rounds = await self.db.get_round_ancestors(tip_round_id, limit=MAX_HISTORY_ROUNDS)
        
        if not rounds:
            return None
        
        # 构建消息列表（rounds 已经按时间正序排列：从最早到最新）
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt}
        ]
        
        for round_data in rounds:
            messages.append({"role": "user", "content": round_data["player_choice"]})
            messages.append({"role": "assistant", "content": round_data["assistant_response"]})
        
        return messages

    async def tally_and_advance(self, game_id: int, scores: dict, result_lines: list[str]):
        """
        根据投票结果计票，并推进游戏到下一回合。
        
        使用乐观锁机制防止并发冲突：在事务内验证 tip_round_id 未被修改。
        
        Args:
            game_id: 游戏ID
            scores: 包含各选项得分的字典
            result_lines: 用于向用户展示的投票结果文本行
        """
        if not self.db or not self.db.conn or not self.llm_api:
            return

        channel_id = None
        main_message_id = None
        
        try:
            # 1. 先冻结游戏，防止其他操作
            await self.db.set_game_frozen_status(game_id, True)
            
            # 2. 在单个事务内获取所有必要数据（读锁）
            async with self.db.transaction():
                game_data = await self.db.get_game_by_game_id(game_id)
                if not game_data:
                    return
                
                channel_id = str(game_data["channel_id"])
                main_message_id = str(game_data["main_message_id"] or "")
                system_prompt = game_data["system_prompt"]
                head_branch_id = game_data["head_branch_id"]
                
                # 获取当前分支的 tip_round_id
                branch = await self.db.get_branch_by_id(head_branch_id)
                if not branch:
                    return
                initial_tip_round_id = branch["tip_round_id"]

            # 3. 检查投票结果
            if not scores:
                await self.api.post_group_msg(
                    channel_id, 
                    text="无人投票，请继续投票后再确认。", 
                    reply=main_message_id
                )
                return

            # 4. 找出胜利者
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

            await self.api.post_group_msg(
                channel_id,
                text=f"🏆 本轮胜出选项：{winner_content}\n" + "\n".join(result_lines),
                reply=main_message_id,
            )

            # 5. 构建历史
            messages = await self._build_llm_history(system_prompt, initial_tip_round_id)
            if not messages:
                await self.api.post_group_msg(channel_id, text="构建对话历史失败，游戏中断。")
                return
            messages.append({"role": "user", "content": winner_content})

            await self.api.post_group_msg(channel_id, text="🛠 GM 正在思考下一步剧情...")

            # 6. 调用LLM（可能耗时）
            new_assistant_response, usage, model_name = await self.llm_api.get_completion(
                cast(list[ChatCompletionMessageParam], messages)
            )
            if not new_assistant_response:
                await self.api.post_group_msg(channel_id, text="GM没有回应，游戏中断。")
                return

            # 7. 在事务内完成所有更新，使用乐观锁检查
            async with self.db.transaction():
                # 再次获取分支状态，检查是否被并发修改
                current_branch = await self.db.get_branch_by_id(head_branch_id)
                if not current_branch or current_branch["tip_round_id"] != initial_tip_round_id:
                    raise TipChangedError()

                # 创建新回合
                async with self.db.conn.cursor() as cursor:
                    await cursor.execute(
                        "INSERT INTO rounds (game_id, parent_id, player_choice, assistant_response, llm_usage, model_name) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            game_id,
                            initial_tip_round_id,
                            winner_content,
                            new_assistant_response,
                            json.dumps(usage) if usage else None,
                            model_name,
                        ),
                    )
                    new_round_id = cursor.lastrowid
                    if new_round_id is None:
                        raise RuntimeError("创建新回合失败")

                # 更新分支 tip
                await self.db.update_branch_tip(head_branch_id, new_round_id)

            # 8. 清理并进入下一轮
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
            if channel_id:
                await self.api.post_group_msg(channel_id, text="推进失败，游戏已解冻，请重试。")
        finally:
            if self.db:
                await self.db.set_game_frozen_status(game_id, False)

    async def revert_last_round(self, game_id: int):
        """
        将当前HEAD分支回退到上一回合。

        Args:
            game_id: 游戏ID。
        """
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

            LOG.info(f"游戏 {game_id} 已成功回退到 round {parent_id}")
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

    async def create_new_branch(
        self, game_id: int, new_branch_name: str, from_round_id: int | None = None
    ):
        """
        从指定回合创建新分支。

        Args:
            game_id: 游戏ID。
            new_branch_name: 新分支的名称。
            from_round_id: 从哪个回合创建分支，如果为None，则从当前HEAD分支的顶端创建。
        
        Raises:
            ValueError: 如果游戏或目标回合不存在。
        """
        if not self.db:
            return
        channel_id = None
        try:
            game = await self.db.get_game_by_game_id(game_id)
            if not game:
                raise ValueError(f"找不到游戏 {game_id}")
            channel_id = game["channel_id"]

            target_round_id = from_round_id
            if target_round_id is None:
                # 默认为当前 HEAD 指向的回合
                head_info = await self.db.get_game_and_head_branch_info(game_id)
                target_round_id = head_info["tip_round_id"]

            if not await self.db.get_round_info(target_round_id):
                raise ValueError(f"目标回合 {target_round_id} 不存在")

            await self.db.create_branch(game_id, new_branch_name, target_round_id)
            LOG.info(f"游戏 {game_id} 从 round {target_round_id} 创建了新分支 '{new_branch_name}'")
            if channel_id:
                await self.api.post_group_msg(
                    str(channel_id),
                    text=f"🌿 已从回合 {target_round_id} 创建新分支: {new_branch_name}",
                )
        except Exception as e:
            LOG.error(f"创建新分支失败: {e}", exc_info=True)
            if channel_id:
                await self.api.post_group_msg(str(channel_id), text=f"❌ 创建分支失败: {e}")

    async def switch_branch(self, game_id: int, branch_name: str):
        """
        切换游戏的HEAD分支。

        Args:
            game_id: 游戏ID。
            branch_name: 要切换到的目标分支名称。

        Raises:
            ValueError: 如果游戏或分支不存在。
        """
        if not self.db:
            return
        channel_id = None
        try:
            game = await self.db.get_game_by_game_id(game_id)
            if not game:
                raise ValueError(f"找不到游戏 {game_id}")
            channel_id = game["channel_id"]

            branch = await self.db.get_branch_by_name(game_id, branch_name)
            if not branch:
                raise ValueError(f"找不到名为 '{branch_name}' 的分支")

            await self.db.update_game_head_branch(game_id, branch["branch_id"])
            LOG.info(f"游戏 {game_id} 的 HEAD 已切换到分支 '{branch_name}'")

            if channel_id:
                await self.api.post_group_msg(
                    str(channel_id), text=f"✅ 已切换到分支: {branch_name}。正在加载最新状态..."
                )
                await self.checkout_head(game_id)

        except Exception as e:
            LOG.error(f"切换分支失败: {e}", exc_info=True)
            if channel_id:
                await self.api.post_group_msg(str(channel_id), text=f"❌ 切换分支失败: {e}")

    async def reset_current_branch(self, game_id: int, round_id: int):
        """
        将当前HEAD分支硬重置到指定的历史回合。

        Args:
            game_id: 游戏ID。
            round_id: 要重置到的目标回合ID。

        Raises:
            ValueError: 如果游戏或目标回合不存在。
        """
        if not self.db:
            return
        channel_id = None
        try:
            game = await self.db.get_game_by_game_id(game_id)
            if not game:
                raise ValueError(f"找不到游戏 {game_id}")
            channel_id = game["channel_id"]
            head_branch_id = game["head_branch_id"]

            if not await self.db.get_round_info(round_id):
                raise ValueError(f"目标回合 {round_id} 不存在")

            await self.db.update_branch_tip(head_branch_id, round_id)
            LOG.info(f"游戏 {game_id} 的 HEAD 分支已重置到 round {round_id}")

            if channel_id:
                await self.api.post_group_msg(
                    str(channel_id), text=f"⏪ 当前分支已重置到回合 {round_id}。正在加载..."
                )
                await self.checkout_head(game_id)

        except Exception as e:
            LOG.error(f"重置分支失败: {e}", exc_info=True)
            if channel_id:
                await self.api.post_group_msg(str(channel_id), text=f"❌ 重置分支失败: {e}")
