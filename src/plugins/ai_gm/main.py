import os
import json
import uuid
from pathlib import Path

from ncatbot.plugin_system import NcatBotPlugin, command_registry, on_notice
from ncatbot.core.event import GroupMessageEvent, NoticeEvent
from ncatbot.core.event.message_segment import Reply
from ncatbot.utils import get_log

from .db import Database
from .llm_api import LLM_API
from .renderer import MarkdownRenderer

LOG = get_log(__name__)

class AIGamePlugin(NcatBotPlugin):
    name = "AIGamePlugin"
    version = "1.0.0"
    description = "一个基于 AI GM 的互动叙事游戏插件"
    author = "Cline"

    def __init__(self):
        super().__init__()
        self.db: Database | None = None
        self.llm_api: LLM_API | None = None
        self.renderer: MarkdownRenderer | None = None
        self.data_path: Path = Path() # Add type hint for data_path

    async def on_load(self):
        """插件加载时执行的初始化操作"""
        LOG.info(f"{self.name} 正在加载...")
        
        # 注册配置项
        self.register_config("openai_api_key", "YOUR_API_KEY_HERE")
        self.register_config("openai_base_url", "https://api.openai.com/v1")
        self.register_config("openai_model_name", "gpt-4-turbo")
        self.register_config("system_prompt", "你是一个互动叙事游戏的主持人（GM），故事背景设定在一个末世废土世界。\n你的职责是：\n1. **游戏开局**：首先，你必须要求玩家以自定义回复的形式，提供他们想要扮演的角色信息，例如：姓名、年龄、性别、背景、技能等。\n2. **推进故事**：在收到玩家的角色信息或后续选择后，根据故事进展，为玩家提供明确的、以大写字母（A, B, C...）开头的多个选项。\n3. **引导互动**：玩家将通过投票选择选项或提交自定义回复来决定故事走向。你需要根据他们的选择来动态发展剧情。")

        # 初始化数据库
        # NcatBotPlugin 基类提供了 self.data_path，这是一个 Path 对象，指向插件的私有数据目录
        db_path = self.data_path / "aigm.db"
        self.db = Database(str(db_path))
        await self.db.connect()

        # 初始化 LLM API
        try:
            api_key = self.config.get("openai_api_key", "")
            base_url = self.config.get("openai_base_url", "https://api.openai.com/v1")
            model_name = self.config.get("openai_model_name", "gpt-4-turbo")

            if not isinstance(api_key, str) or not isinstance(base_url, str) or not isinstance(model_name, str):
                raise TypeError("Config values must be strings.")

            self.llm_api = LLM_API(
                api_key=api_key,
                base_url=base_url,
                model_name=model_name,
            )
        except (ValueError, TypeError) as e:
            LOG.error(f"LLM API 初始化失败: {e}. 请在 data/AIGamePlugin/AIGamePlugin.yaml 中配置正确的 openai 参数。")
            self.llm_api = None # 标记为不可用

        # 初始化 Markdown 渲染器
        render_output_path = self.data_path / "renders"
        self.renderer = MarkdownRenderer(str(render_output_path))
        LOG.info(f"{self.name} 加载完成。")

    async def on_close(self):
        """插件关闭时执行的操作"""
        if self.db:
            await self.db.close()
        LOG.info(f"{self.name} 已卸载。")

    @command_registry.command("aigm", description="开始一场 AI GM 游戏")
    async def start_game_command(self, event: GroupMessageEvent):
        """处理 /aigm 命令，开始新游戏"""
        if not self.llm_api or not self.db or not self.renderer:
            await event.reply("❌ 插件未完全初始化，无法开始游戏。")
            return

        group_id = str(event.group_id)
        
        if not self.db.conn:
            await event.reply("❌ 数据库未连接，无法检查游戏状态。")
            return

        async with self.db.conn.cursor() as cursor:
            await cursor.execute("SELECT status FROM games WHERE group_id = ?", (group_id,))
            game = await cursor.fetchone()
            if game and game[0] == 'running':
                await event.reply("❌ 本群已有一局游戏正在进行中，请先结束或等待当前游戏完成。")
                return
        
        await event.reply("🚀 新游戏即将开始... 正在联系 GM 生成开场白...")
        LOG.info(f"群 {group_id} 的用户 {event.user_id} 正在开始新游戏。")
        
        try:
            await self._start_new_game(group_id)
        except Exception as e:
            LOG.error(f"开始新游戏时发生严重错误: {e}", exc_info=True)
            await self.api.post_group_msg(group_id, text=f"❌ 启动游戏失败，发生内部错误: {e}")

    async def _start_new_game(self, group_id: str):
        """内部方法，处理新游戏的完整启动流程"""
        if not self.llm_api or not self.db or not self.renderer:
            LOG.error("游戏启动失败：组件未初始化。")
            return

        # 1. 构建初始 messages
        system_prompt = self.config.get("system_prompt", "你是一个互动叙事游戏的主持人（GM）。")
        initial_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "开始"}
        ]

        # 2. 调用 LLM 获取开场白
        assistant_response = await self.llm_api.get_completion(initial_messages)
        if not assistant_response:
            await self.api.post_group_msg(group_id, text="❌ GM 没有回应，无法开始游戏。")
            return

        # 3. 渲染 Markdown 为图片
        image_filename = f"round_{group_id}_{uuid.uuid4()}"
        image_path = await self.renderer.render(assistant_response, image_filename)
        if not image_path:
            await self.api.post_group_msg(group_id, text="❌ 渲染游戏场景失败，无法开始游戏。")
            return
            
        # 4. 发送图片
        main_message_id = await self.api.post_group_file(group_id, image=image_path)
        if not main_message_id:
            await self.api.post_group_msg(group_id, text="❌ 发送游戏场景失败，无法开始游戏。")
            return

        # 5. 贴上表情
        # 表情ID来自于你的描述
        emoji_map = {
            'A': 127822, 'B': 9973, 'C': 128663, 'D': 128054,
            'E': 127859, 'F': 128293, 'G': 128123
        }
        for _, emoji_id in emoji_map.items():
            try:
                await self.api.set_msg_emoji_like(main_message_id, emoji_id)
            except Exception as e:
                LOG.warning(f"为消息 {main_message_id} 贴表情 {emoji_id} 失败: {e}")

        # 6. 在数据库中创建记录
        if not self.db or not self.db.conn:
            LOG.error("数据库未连接，无法创建游戏记录。")
            await self.api.post_group_msg(group_id, text="❌ 内部错误：数据库连接丢失。")
            return

        async with self.db.conn.cursor() as cursor:
            # 检查是否已有游戏，有则更新，无则创建
            await cursor.execute("SELECT * FROM games WHERE group_id = ?", (group_id,))
            game = await cursor.fetchone()
            
            messages_history_json = json.dumps(initial_messages + [{"role": "assistant", "content": assistant_response}])

            if game:
                await cursor.execute(
                    "UPDATE games SET status = ?, messages_history = ?, updated_at = CURRENT_TIMESTAMP WHERE group_id = ?",
                    ("running", messages_history_json, group_id)
                )
            else:
                await cursor.execute(
                    "INSERT INTO games (group_id, status, messages_history) VALUES (?, ?, ?)",
                    (group_id, "running", messages_history_json)
                )
            
            # 创建新的回合记录
            await cursor.execute(
                "INSERT INTO rounds (game_group_id, round_number, main_message_id, assistant_response) VALUES (?, ?, ?, ?)",
                (group_id, 1, main_message_id, assistant_response)
            )
        await self.db.conn.commit()
        
        LOG.info(f"群 {group_id} 的新游戏已成功开始，主消息 ID: {main_message_id}")

    async def on_group_message(self, event: GroupMessageEvent):
        """处理群聊消息，主要用于捕获对游戏主消息的回复"""
        if not self.db or not self.db.conn:
            return # 插件未完全初始化

        # 检查消息是否为回复
        reply_segments = event.message.filter(Reply)
        if not reply_segments:
            return
        reply_segment = reply_segments[0]

        replied_to_id = reply_segment.id
        group_id = str(event.group_id)

        async with self.db.conn.cursor() as cursor:
            # 检查被回复的消息是否是当前游戏回合的主消息
            await cursor.execute(
                """SELECT id FROM rounds 
                   WHERE game_group_id = ? AND main_message_id = ? 
                   ORDER BY round_number DESC LIMIT 1""",
                (group_id, replied_to_id)
            )
            round_row = await cursor.fetchone()

            if round_row:
                round_id = round_row[0]
                user_id = str(event.user_id)
                message_id = str(event.message_id)
                content = "".join(seg.text for seg in event.message.filter_text())

                # 将自定义输入存入数据库
                await cursor.execute(
                    """INSERT INTO custom_inputs (round_id, user_id, message_id, content)
                       VALUES (?, ?, ?, ?)""",
                    (round_id, user_id, message_id, content)
                )
                await self.db.conn.commit()
                LOG.info(f"记录了新的自定义输入 from {user_id}: {content}")

                # 为该回复贴上表情
                reaction_emojis = [127881, 128560, 10060] # 🎉, 😰, ❌
                for emoji in reaction_emojis:
                    try:
                        await self.api.set_msg_emoji_like(message_id, emoji)
                    except Exception as e:
                        LOG.warning(f"为自定义输入 {message_id} 贴表情 {emoji} 失败: {e}")

    @on_notice
    async def handle_emoji_reaction(self, event: NoticeEvent):
        """处理表情回应，这是游戏结算和状态变更的核心触发器"""
        if event.notice_type != 'group_msg_emoji_like' or not event.is_add:
            return # 只处理添加表情的事件

        if not self.db or not self.db.conn:
            return

        group_id = str(event.group_id)
        user_id = str(event.user_id)
        message_id = str(event.message_id)
        
        if event.emoji_like_id is None:
            return
        emoji_id = int(event.emoji_like_id)

        # 定义管理员操作的表情
        admin_action_emojis = {127881: 'confirm', 128560: 'deny', 10060: 'retract_game'}
        # 定义用户撤回自定义输入的表情
        input_retract_emoji = 10060

        try:
            # 检查是否是管理员操作
            is_admin = await self._is_group_admin(group_id, user_id)
            if is_admin and emoji_id in admin_action_emojis:
                # 检查表情是否贴在当前回合的主消息上
                async with self.db.conn.cursor() as cursor:
                    await cursor.execute(
                        "SELECT 1 FROM rounds WHERE game_group_id = ? AND main_message_id = ? ORDER BY round_number DESC LIMIT 1",
                        (group_id, message_id)
                    )
                    if await cursor.fetchone():
                        action = admin_action_emojis[emoji_id]
                        if action == 'confirm':
                            await self._handle_confirm(group_id, message_id)
                        elif action == 'deny':
                            await self._handle_deny(group_id, message_id)
                        elif action == 'retract_game':
                            await self._handle_retract_game(group_id, message_id)
                        return

            # 检查是否是用户撤回自己的自定义输入
            if emoji_id == input_retract_emoji:
                async with self.db.conn.cursor() as cursor:
                    # 检查表情是否贴在某个自定义输入上，并且操作者是该输入的作者或管理员
                    await cursor.execute(
                        "SELECT user_id FROM custom_inputs WHERE message_id = ?", (message_id,)
                    )
                    row = await cursor.fetchone()
                    if row and (user_id == str(row[0]) or is_admin):
                        await self._handle_retract_input(group_id, message_id)

        except Exception as e:
            LOG.error(f"处理表情回应时出错: {e}", exc_info=True)

    async def _is_group_admin(self, group_id: str, user_id: str) -> bool:
        """检查用户是否为群管理员或群主"""
        try:
            member_info = await self.api.get_group_member_info(group_id, user_id)
            return member_info.role in ["admin", "owner"]
        except Exception as e:
            LOG.error(f"获取群 {group_id} 成员 {user_id} 信息失败: {e}")
            return False

    async def _tally_votes(self, group_id: str, main_message_id: str) -> tuple[dict, str]:
        """统计一轮投票的结果，返回分数和格式化的结果字符串"""
        if not self.db or not self.db.conn:
            raise RuntimeError("Database not connected.")

        scores = {}
        result_lines = ["🗳️ 投票结果统计："]
        
        # 1. 统计 A-G 选项的票数
        option_emoji_map = {
            127822: 'A', 9973: 'B', 128663: 'C', 128054: 'D',
            127859: 'E', 128293: 'F', 128123: 'G'
        }
        for emoji_id, option in option_emoji_map.items():
            try:
                reactors = await self.api.fetch_emoji_like(main_message_id, emoji_id, emoji_type=1)
                count = len(reactors.get('emojiLikesList', []))
                if count > 0:
                    scores[option] = count
                    result_lines.append(f"- 选项 {option}: {count} 票")
            except Exception as e:
                LOG.warning(f"获取表情 {emoji_id} 反应失败: {e}")

        # 2. 统计自定义输入的票数
        async with self.db.conn.cursor() as cursor:
            await cursor.execute(
                """SELECT ci.message_id, ci.content, ci.user_id FROM custom_inputs ci
                   JOIN rounds r ON ci.round_id = r.id
                   WHERE r.main_message_id = ? AND ci.is_retracted = 0""",
                (main_message_id,)
            )
            custom_inputs = await cursor.fetchall()

        for msg_id, content, user_id in custom_inputs:
            try:
                yay_reactors = await self.api.fetch_emoji_like(msg_id, 127881, emoji_type=1) # 🎉
                yay_count = len(yay_reactors.get('emojiLikesList', []))
                nay_reactors = await self.api.fetch_emoji_like(msg_id, 128560, emoji_type=1) # 😰
                nay_count = len(nay_reactors.get('emojiLikesList', []))
                
                net_score = yay_count - nay_count
                scores[f"custom_{msg_id}"] = {"score": net_score, "content": content, "user_id": user_id}
                result_lines.append(f"- 自定义输入 (来自 @{user_id}): \"{content[:20]}...\" - 净得票: {net_score}")
            except Exception as e:
                LOG.warning(f"获取自定义输入 {msg_id} 反应失败: {e}")
                
        return scores, "\n".join(result_lines)

    async def _handle_confirm(self, group_id: str, message_id: str):
        """处理确认操作"""
        if not self.db or not self.db.conn or not self.llm_api: return
        LOG.info(f"群 {group_id} 管理员确认了投票 (消息: {message_id})")
        
        scores, result_text = await self._tally_votes(group_id, message_id)
        await self.api.post_group_msg(group_id, text=result_text, reply=message_id)

        if not scores:
            await self.api.post_group_msg(group_id, text="无人投票，本轮无效，请重新开始或由管理员继续。")
            return

        max_score = -float('inf')
        for key, value in scores.items():
            current_score = value if isinstance(value, int) else value['score']
            if current_score > max_score:
                max_score = current_score
        
        winners = []
        for key, value in scores.items():
            current_score = value if isinstance(value, int) else value['score']
            if current_score == max_score:
                winners.append(key)

        winning_content = []
        for winner in winners:
            if winner.startswith("custom_"):
                winning_content.append(scores[winner]['content'])
            else:
                winning_content.append(f"选择选项 {winner}")
        
        user_choice_text = " & ".join(winning_content)

        async with self.db.conn.cursor() as cursor:
            await cursor.execute("SELECT messages_history FROM games WHERE group_id = ?", (group_id,))
            game_row = await cursor.fetchone()
            if not game_row: return

            messages_history = json.loads(game_row[0])
            messages_history.append({"role": "user", "content": user_choice_text})
            
            new_assistant_response = await self.llm_api.get_completion(messages_history)
            if not new_assistant_response:
                await self.api.post_group_msg(group_id, text="❌ GM 没有回应，游戏中断。")
                return
            
            messages_history.append({"role": "assistant", "content": new_assistant_response})
            
            await cursor.execute("UPDATE games SET messages_history = ? WHERE group_id = ?", (json.dumps(messages_history), group_id))
            await self.db.conn.commit()

        await self._start_next_round(group_id, new_assistant_response)

    async def _handle_deny(self, group_id: str, message_id: str):
        """处理否决操作"""
        LOG.info(f"群 {group_id} 管理员否决了投票 (消息: {message_id})")
        _, result_text = await self._tally_votes(group_id, message_id)
        announcement = result_text + "\n\n**由于管理员的一票否决，本次投票作废，将重新开始本轮投票。**"
        await self.api.post_group_msg(group_id, text=announcement, reply=message_id)

        if not self.db or not self.db.conn: return
        async with self.db.conn.cursor() as cursor:
            await cursor.execute("SELECT assistant_response FROM rounds WHERE main_message_id = ?", (message_id,))
            row = await cursor.fetchone()
            if row:
                await self._start_next_round(group_id, row[0])

    async def _handle_retract_game(self, group_id: str, message_id: str):
        """处理游戏回退操作"""
        LOG.info(f"群 {group_id} 管理员回退了游戏 (消息: {message_id})")
        if not self.db or not self.db.conn: return

        await self.api.post_group_msg(group_id, text="**管理员执行了悔棋操作，游戏将回退到上一轮。**", reply=message_id)

        async with self.db.conn.cursor() as cursor:
            await cursor.execute("SELECT messages_history FROM games WHERE group_id = ?", (group_id,))
            row = await cursor.fetchone()
            if not row: return
            
            messages_history = json.loads(row[0])
            if len(messages_history) >= 2:
                messages_history.pop()
                messages_history.pop()

            await cursor.execute("UPDATE games SET messages_history = ? WHERE group_id = ?", (json.dumps(messages_history), group_id))
            await self.db.conn.commit()

            if messages_history:
                previous_assistant_response = messages_history[-1]['content']
                await self._start_next_round(group_id, previous_assistant_response)

    async def _handle_retract_input(self, group_id: str, message_id: str):
        """处理自定义输入的撤回"""
        if not self.db or not self.db.conn: return
        async with self.db.conn.cursor() as cursor:
            await cursor.execute("UPDATE custom_inputs SET is_retracted = 1 WHERE message_id = ?", (message_id,))
            await self.db.conn.commit()
        
        await self.api.post_group_msg(group_id, text=f"一条自定义输入已被撤回，将不参与最终投票。", reply=message_id)
        LOG.info(f"群 {group_id} 用户撤回了自定义输入 (消息: {message_id})")

    async def _start_next_round(self, group_id: str, assistant_response: str):
        """开启一个新回合的通用函数"""
        if not self.renderer or not self.db or not self.db.conn: return

        image_filename = f"round_{group_id}_{uuid.uuid4()}"
        image_path = await self.renderer.render(assistant_response, image_filename)
        if not image_path:
            await self.api.post_group_msg(group_id, text="❌ 渲染新场景失败，游戏中断。")
            return
            
        main_message_id = await self.api.post_group_file(group_id, image=image_path)
        if not main_message_id:
            await self.api.post_group_msg(group_id, text="❌ 发送新场景失败，游戏中断。")
            return

        emoji_map = {
            'A': 127822, 'B': 9973, 'C': 128663, 'D': 128054,
            'E': 127859, 'F': 128293, 'G': 128123
        }
        for _, emoji_id in emoji_map.items():
            try:
                await self.api.set_msg_emoji_like(main_message_id, emoji_id)
            except Exception as e:
                LOG.warning(f"为消息 {main_message_id} 贴表情 {emoji_id} 失败: {e}")

        async with self.db.conn.cursor() as cursor:
            await cursor.execute("SELECT MAX(round_number) FROM rounds WHERE game_group_id = ?", (group_id,))
            max_round = await cursor.fetchone()
            next_round_number = (max_round[0] or 0) + 1 if max_round else 1
            
            await cursor.execute(
                "INSERT INTO rounds (game_group_id, round_number, main_message_id, assistant_response) VALUES (?, ?, ?, ?)",
                (group_id, next_round_number, main_message_id, assistant_response)
            )
        await self.db.conn.commit()
        LOG.info(f"群 {group_id} 第 {next_round_number} 回合已开始，主消息 ID: {main_message_id}")
