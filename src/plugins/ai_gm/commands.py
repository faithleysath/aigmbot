# src/plugins/ai_gm/commands.py
from ncatbot.plugin_system import NcatBotPlugin
from ncatbot.core.event import GroupMessageEvent
from ncatbot.core.event.message_segment import At, MessageArray, Text, Reply, Image
from ncatbot.core.helper.forward_constructor import ForwardConstructor
from ncatbot.core.api import BotAPI
from ncatbot.utils import get_log
import json

from .db import Database
from .game_manager import GameManager
from .cache import CacheManager
from .visualizer import Visualizer
from .renderer import MarkdownRenderer
from .utils import bytes_to_base64

LOG = get_log(__name__)


class CommandHandler:
    def __init__(
        self,
        plugin: NcatBotPlugin,
        db: Database,
        game_manager: GameManager,
        cache_manager: CacheManager,
        visualizer: Visualizer,
        renderer: MarkdownRenderer,
    ):
        self.plugin = plugin
        self.api = plugin.api
        self.db = db
        self.game_manager = game_manager
        self.cache_manager = cache_manager
        self.visualizer = visualizer
        self.renderer = renderer
        self.rbac_manager = plugin.rbac_manager

    async def _is_authorized_for_channel_action(
        self, user_id: str, group_id: str, sender_role: str | None
    ) -> bool:
        """检查用户是否有权对当前频道内的游戏执行写操作"""
        # Root用户
        if self.rbac_manager.user_has_role(user_id, "root"):
            return True

        # 群管理员
        if sender_role in ["admin", "owner"]:
            return True

        # 游戏主持人 (当前频道游戏的)
        game = await self.db.get_game_by_channel_id(group_id)
        if game and str(game["host_user_id"]) == user_id:
            return True

        return False

    async def handle_help(self, event: GroupMessageEvent):
        """处理 /aigm help 命令"""
        help_text = """
/aigm help - 显示此帮助信息
/aigm status - 查看当前群组的游戏状态
/aigm game list - 列出所有游戏
/aigm game attach <id> - [主持人/管理员] 将游戏附加到当前频道
/aigm game detach - [主持人/管理员] 从当前频道分离游戏
/aigm game sethost @user - [主持人/管理员] 变更当前频道游戏的主持人
/aigm game sethost-by-id <id> @user - [主持人/管理员] 变更指定ID游戏的主持人
/aigm checkout head - [主持人/管理员] 重新加载并显示当前游戏的最新状态
/aigm admin unfreeze - [群管理/ROOT] 强制解冻当前游戏
/aigm admin delete <id> - [ROOT] 删除指定ID的游戏
/aigm branch list - 可视化显示当前游戏的分支图（简化）
/aigm branch list all - 可视化显示当前游戏的完整分支图
/aigm branch show <branch_name> - 查看指定分支顶端的内容
/aigm branch history [branch_name] [limit=N] - 查看指定分支的历史记录（默认为HEAD）
/aigm round show <round_id> - 查看指定回合的内容
/aigm round history <round_id> [limit=N] - 查看指定回合及其历史记录
        """
        await event.reply(help_text.strip(), at=False)

    async def handle_status(self, event: GroupMessageEvent, api: BotAPI):
        """处理 /aigm status 命令"""
        group_id = str(event.group_id)
        game = await self.db.get_game_by_channel_id(group_id)

        if not game:
            await event.reply("当前群组没有正在进行的游戏。", at=False)
            return

        message_array = MessageArray([
            Text("游戏状态：\n"),
            Text(f"- 游戏ID: {game['game_id']}\n"),
            Text("- 主持人: "), At(game['host_user_id']), Text("\n"),
            Text(f"- 是否冻结: {'是' if game['is_frozen'] else '否'}\n"),
            Text(f"- 创建时间: {game['created_at']}\n"),
            Text(f"- 更新时间: {game['updated_at']}")
        ])
        if game['main_message_id']:
            message_array += MessageArray([
                Text(f"\n- 主消息ID: {game['main_message_id']}\n"),
                Reply(game['main_message_id'])
            ])

        await api.post_group_array_msg(event.group_id, message_array)

    async def handle_branch_list(self, event: GroupMessageEvent):
        """处理 /aigm branch list 命令"""
        group_id = str(event.group_id)
        game = await self.db.get_game_by_channel_id(group_id)

        if not game:
            await event.reply("当前群组没有正在进行的游戏。", at=False)
            return

        game_id = game['game_id']
        await event.reply("正在生成分支图，请稍候...", at=False)
        
        image_bytes = await self.visualizer.create_branch_graph(game_id)

        if image_bytes:
            await self.api.post_group_file(
                group_id,
                image=f"data:image/png;base64,{bytes_to_base64(image_bytes)}",
            )
        else:
            await event.reply("生成分支图失败，请检查日志。", at=False)

    async def handle_branch_history(self, event: GroupMessageEvent, branch_name: str | None = None, limit: int = 10):
        """处理 /aigm branch history [name] [limit] 命令"""
        game = await self.db.get_game_by_channel_id(str(event.group_id))
        if not game:
            await event.reply("当前群组没有正在进行的游戏。", at=False)
            return

        branch = None
        if branch_name:
            branch = await self.db.get_branch_by_name(game['game_id'], branch_name)
        else:
            # 如果没有提供分支名，则使用 HEAD 分支
            if game['head_branch_id']:
                branch = await self.db.get_branch_by_id(game['head_branch_id'])

        if not branch or branch['tip_round_id'] is None:
            display_name = f"名为 '{branch_name}' 的" if branch_name else "HEAD"
            await event.reply(f"找不到{display_name}分支或该分支没有指向任何回合。", at=False)
            return

        tip_round_id = branch['tip_round_id']
        await self.handle_round_history(event, tip_round_id, limit)

    async def handle_branch_list_all(self, event: GroupMessageEvent):
        """处理 /aigm branch list all 命令"""
        group_id = str(event.group_id)
        game = await self.db.get_game_by_channel_id(group_id)

        if not game:
            await event.reply("当前群组没有正在进行的游戏。", at=False)
            return

        game_id = game['game_id']
        await event.reply("正在生成完整分支图，请稍候...", at=False)
        
        image_bytes = await self.visualizer.create_full_branch_graph(game_id)

        if image_bytes:
            await self.api.post_group_file(
                group_id,
                image=f"data:image/png;base64,{bytes_to_base64(image_bytes)}",
            )
        else:
            await event.reply("生成完整分支图失败，请检查日志。", at=False)

    async def _show_round_content(self, event: GroupMessageEvent, round_id: int):
        """根据 round_id 显示其内容的通用函数"""
        round_info = await self.db.get_round_info(round_id)
        if not round_info:
            await event.reply(f"找不到 ID 为 {round_id} 的回合。", at=False)
            return
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
        await event.reply(f"正在渲染 Round {round_id} 的内容...", at=False)
        image_bytes = await self.renderer.render_markdown(
            round_info["assistant_response"],
            extra_text=extra_text
        )

        if image_bytes:
            await self.api.post_group_file(
                str(event.group_id),
                image=f"data:image/png;base64,{bytes_to_base64(image_bytes)}",
            )
        else:
            await event.reply("渲染内容失败，请检查日志。", at=False)

    async def handle_round_show(self, event: GroupMessageEvent, round_id: int):
        """处理 /aigm round show <id> 命令"""
        game = await self.db.get_game_by_channel_id(str(event.group_id))
        if not game:
            await event.reply("当前群组没有正在进行的游戏。", at=False)
            return
        await self._show_round_content(event, round_id)

    async def handle_round_history(self, event: GroupMessageEvent, round_id: int, limit: int = 10):
        """处理 /aigm round history <id> [limit] 命令，并将每轮渲染到一张图片中"""
        game = await self.db.get_game_by_channel_id(str(event.group_id))
        if not game:
            await event.reply("当前群组没有正在进行的游戏。", at=False)
            return

        if limit > 10:
            limit = 10
            await event.reply("为了防止消息刷屏和性能问题，历史记录上限设置为10条。", at=False)

        await event.reply(f"正在生成 round {round_id} 的历史记录（最多{limit}条），请稍候...", at=False)

        history = await self.db.get_round_ancestors(round_id, limit)
        if not history:
            await event.reply(f"找不到 round {round_id} 或其历史记录。", at=False)
            return

        # 使用动态昵称 f"#{round_id}"
        fcr = ForwardConstructor(user_id=str(event.self_id), nickname=f"#{round_id}")
        
        for round_data in history:
            # 1. 从 llm_usage 计算 extra_text
            extra_text = None
            llm_usage_str = round_data["llm_usage"]
            if llm_usage_str:
                try:
                    usage = json.loads(llm_usage_str)
                    prompt_tokens = usage.get("prompt_tokens", 0)
                    if prompt_tokens > 0:
                        extra_text = f"{round(prompt_tokens / 1000)}k / 1M"
                except (json.JSONDecodeError, TypeError):
                    LOG.warning(f"无法解析 llm_usage: {llm_usage_str}")

            # 2. 将玩家选择和 GM 回应合并为一个 Markdown 字符串
            combined_markdown = (
                f"### 玩家选择 (Round {round_data['parent_id']} -> {round_data['round_id']})\n\n"
                f"{round_data['player_choice']}\n\n"
                f"---\n\n"
                f"### GM 回应 (Round {round_data['round_id']})\n\n"
                f"{round_data['assistant_response']}"
            )

            # 3. 将合并后的 Markdown 渲染为一张图片
            image_bytes = await self.renderer.render_markdown(
                combined_markdown,
                extra_text=extra_text
            )

            # 4. 将图片附加到合并转发构造器中
            if image_bytes:
                node_content = MessageArray([Image(f"data:image/png;base64,{bytes_to_base64(image_bytes)}")])
                fcr.attach(node_content)
            else:
                # 如果渲染失败，则回退到文本
                fcr.attach(MessageArray([Text(f"[渲染失败]\n{combined_markdown}")]))

        forward_msg = fcr.to_forward()
        
        await self.api.post_group_forward_msg(event.group_id, forward_msg)

    async def handle_branch_show(self, event: GroupMessageEvent, branch_name: str):
        """处理 /aigm branch show <name> 命令"""
        group_id = str(event.group_id)
        game = await self.db.get_game_by_channel_id(group_id)

        if not game:
            await event.reply("当前群组没有正在进行的游戏。", at=False)
            return

        game_id = game['game_id']
        branch = await self.db.get_branch_by_name(game_id, branch_name)
        if not branch or branch['tip_round_id'] is None:
            await event.reply(f"找不到名为 '{branch_name}' 的分支或该分支没有指向任何回合。", at=False)
            return

        await self._show_round_content(event, branch['tip_round_id'])

    async def handle_game_list(self, event: GroupMessageEvent):
        """处理 /aigm game list 命令"""
        games = await self.db.get_all_games()

        if not games:
            await event.reply("当前没有已创建的游戏。")
            return

        game_list_text = "游戏列表：\n"
        for game in games:
            game_list_text += (
                f"- ID: {game['game_id']}, "
                f"频道: {game['channel_id'] or '未附加'}, "
                f"主持人: {game['host_user_id']}, "
                f"创建于: {game['created_at']}\n"
            )

        await event.reply(game_list_text.strip(), at=False)

    async def handle_game_attach(self, event: GroupMessageEvent, game_id: int):
        """处理 /aigm game attach <id> 命令"""
        try:
            target_game = await self.db.get_game_by_game_id(game_id)

            # Permission Check
            is_root = self.rbac_manager.user_has_role(str(event.user_id), "root")
            is_group_admin = event.sender.role in ["admin", "owner"]
            is_target_game_host = target_game and str(target_game["host_user_id"]) == str(event.user_id)

            if not (is_root or is_group_admin or is_target_game_host):
                await event.reply("权限不足。您必须是群管理员、root用户或该游戏的主持人。", at=False)
                return

            # Logic
            group_id = str(event.group_id)
            if await self.db.is_game_running(group_id):
                await event.reply("当前频道已经有一个正在进行的游戏。", at=False)
                return
            if not target_game:
                await event.reply(f"找不到ID为 {game_id} 的游戏。", at=False)
                return
            if target_game['channel_id']:
                await event.reply(f"游戏 {game_id} 已经附加到频道 {target_game['channel_id']}。", at=False)
                return

            await self.db.attach_game_to_channel(game_id, group_id)
            await event.reply(f"成功将游戏 {game_id} 附加到当前频道。正在发送主消息中...", at=False)
            await self.game_manager.checkout_head(game_id)

        except ValueError:
            await event.reply("无效的游戏ID，请输入一个数字。")
        except Exception as e:
            # 兜底处理 UNIQUE 约束错误或其他 DB 写入错误
            LOG.error(f"附加游戏失败: {e}", exc_info=True)
            await event.reply("附加失败：可能已被其他并发操作占用本频道，请稍后重试。", at=False)

    async def handle_game_set_host(
        self, event: GroupMessageEvent, new_host_id: str, game_id: int | None = None
    ):
        """处理 /aigm game sethost [id] @user 命令"""
        target_game_id = game_id

        try:
            if not target_game_id:
                game = await self.db.get_game_by_channel_id(str(event.group_id))
                if game:
                    target_game_id = game["game_id"]

            if target_game_id is None:
                await event.reply("无法确定要操作的游戏。", at=False)
                return

            # Permission Check
            target_game = await self.db.get_game_by_game_id(target_game_id)
            is_root = self.rbac_manager.user_has_role(str(event.user_id), "root")
            is_group_admin = event.sender.role in ["admin", "owner"]
            is_target_game_host = target_game and str(target_game["host_user_id"]) == str(event.user_id)

            if not (is_root or is_group_admin or is_target_game_host):
                await event.reply("权限不足。您必须是群管理员、root用户或该游戏的主持人。", at=False)
                return

            # Logic
            await self.db.update_game_host(target_game_id, new_host_id)
            await event.reply(
                at=False,
                rtf=MessageArray(
                    [
                        Text(f"✅ 成功将游戏 {target_game_id} 的主持人变更为 "),
                        At(new_host_id),
                        Text("。"),
                    ]
                )
            )
        except ValueError:
            await event.reply("无效的游戏ID。", at=False)
        except Exception as e:
            LOG.error(f"变更游戏主持人失败: {e}", exc_info=True)
            await event.reply("变更主持人失败，请查看日志。", at=False)

    async def handle_game_detach(self, event: GroupMessageEvent):
        """处理 /aigm game detach 命令"""
        user_id = str(event.user_id)
        group_id = str(event.group_id)
        if not await self._is_authorized_for_channel_action(user_id, group_id, event.sender.role):
            await event.reply("权限不足，您必须是群管理员、root用户或该频道游戏的主持人。", at=False)
            return

        game = await self.db.get_game_by_channel_id(group_id)
        if not game:
            await event.reply("当前频道没有附加任何游戏。", at=False)
            return

        game_id = game['game_id']
        await self.db.detach_game_from_channel(game_id)
        await self.cache_manager.clear_group_vote_cache(group_id)
        await event.reply(f"成功从当前频道分离游戏 {game_id}，并已清理相关缓存。", at=False)

    async def handle_checkout_head(self, event: GroupMessageEvent):
        """处理 /aigm checkout head 命令"""
        user_id = str(event.user_id)
        group_id = str(event.group_id)
        if not await self._is_authorized_for_channel_action(user_id, group_id, event.sender.role):
            await event.reply("权限不足，您必须是群管理员、root用户或该频道游戏的主持人。", at=False)
            return

        game = await self.db.get_game_by_channel_id(group_id)
        if not game:
            await event.reply("当前频道没有正在进行的游戏。", at=False)
            return

        game_id = game['game_id']
        await self.game_manager.checkout_head(game_id)

    async def handle_cache_pending_clear(self, event: GroupMessageEvent):
        """处理 /aigm cache pending clear 命令"""
        await self.cache_manager.clear_pending_games()
        await event.reply("已清空所有待处理的新游戏请求缓存。", at=False)

    async def handle_admin_unfreeze(self, event: GroupMessageEvent):
        """处理 /aigm admin unfreeze 命令"""
        is_root = self.rbac_manager.user_has_role(str(event.user_id), "root")
        is_group_admin = event.sender.role in ["admin", "owner"]
        if not (is_root or is_group_admin):
            await event.reply("权限不足。您必须是群管理员或root用户。", at=False)
            return

        group_id = str(event.group_id)
        game = await self.db.get_game_by_channel_id(group_id)

        if not game:
            await event.reply("当前频道没有正在进行的游戏。", at=False)
            return

        if not game["is_frozen"]:
            await event.reply("游戏未处于冻结状态。", at=False)
            return

        game_id = game["game_id"]
        await self.db.set_game_frozen_status(game_id, False)
        await event.reply(f"✅ 游戏 {game_id} 已被成功解冻，您可以继续操作了。", at=False)

    async def handle_admin_delete_game(self, event: GroupMessageEvent, game_id: int):
        """处理 /aigm admin delete <id> 命令"""
        if not self.rbac_manager.user_has_role(str(event.user_id), "root"):
            await event.reply("权限不足。只有root用户才能删除游戏。", at=False)
            return

        try:
            game = await self.db.get_game_by_game_id(game_id)
            if not game:
                await event.reply(f"找不到ID为 {game_id} 的游戏。", at=False)
                return

            channel_id = game["channel_id"]
            await self.db.delete_game(game_id)

            # 如果游戏附加在频道上，清理投票缓存
            if channel_id:
                await self.cache_manager.clear_group_vote_cache(str(channel_id))

            await event.reply(f"✅ 成功删除游戏 {game_id}。", at=False)
            LOG.info(f"Root用户 {event.user_id} 删除了游戏 {game_id}。")

        except Exception as e:
            LOG.error(f"删除游戏 {game_id} 失败: {e}", exc_info=True)
            await event.reply(f"删除游戏 {game_id} 失败，请查看日志。", at=False)
