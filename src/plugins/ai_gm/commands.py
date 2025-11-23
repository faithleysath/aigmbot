# src/plugins/ai_gm/commands.py
from ncatbot.plugin_system import NcatBotPlugin
from ncatbot.core.event import GroupMessageEvent, PrivateMessageEvent
from ncatbot.core.event.message_segment import At, MessageArray, Text, Reply, Image
from ncatbot.core.helper.forward_constructor import ForwardConstructor
from ncatbot.core.api import BotAPI
from ncatbot.utils import get_log
import json
import re
import time
import uuid
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .main import AIGMPlugin
    from .event_handler import EventHandler
    from .llm_api import LLM_API

from .db import Database
from .game_manager import GameManager
from .cache import CacheManager
from .visualizer import Visualizer
from .renderer import MarkdownRenderer
from .utils import bytes_to_base64
from .constants import HISTORY_MAX_LIMIT
from .web_ui import WebUI
from .channel_config import ChannelConfigManager
from .llm_config import LLMConfigManager, LLMPreset

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
        web_ui: WebUI | None = None,
        channel_config: ChannelConfigManager | None = None,
        llm_config_manager: LLMConfigManager | None = None,
    ):
        self.plugin = plugin
        self.web_ui = web_ui
        self.api = plugin.api
        self.db = db
        self.game_manager = game_manager
        self.cache_manager = cache_manager
        self.visualizer = visualizer
        self.renderer = renderer
        self.rbac_manager = plugin.rbac_manager
        self.channel_config = channel_config
        self.llm_config_manager = llm_config_manager

    async def _validate_name(self, name: str) -> bool:
        """验证分支或标签名称的格式"""
        if not name or len(name) > 50:
            return False
        # 允许字母、数字、下划线和连字符
        if not re.match(r"^[a-zA-Z0-9_-]+$", name):
            return False
        return True

    async def check_channel_permission(
        self, user_id: str, group_id: str, sender_role: str | None
    ) -> bool:
        """
        检查用户是否有权对当前频道内的游戏执行写操作。
        
        权限层级（从高到低）：
        1. Root 用户：拥有所有权限
        2. 群管理员/群主：可以管理本群的游戏
        3. 游戏主持人：可以管理自己主持的游戏
        
        Args:
            user_id: 用户ID
            group_id: 群组ID
            sender_role: 发送者在群组中的角色 (admin/owner/member)
            
        Returns:
            bool: 如果用户有权限返回 True，否则返回 False
        """
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

    def _check_is_game_host(self, user_id: str, game_host_id: str) -> bool:
        """
        检查用户是否是指定游戏的主持人。
        
        这是一个纯函数，用于避免代码重复。
        
        Args:
            user_id: 要检查的用户ID
            game_host_id: 游戏的主持人ID
            
        Returns:
            bool: 如果用户是游戏主持人返回 True，否则返回 False
        """
        return str(game_host_id) == user_id

    def _check_has_root_or_admin(
        self, user_id: str, sender_role: str | None
    ) -> bool:
        """
        检查用户是否是 Root 用户或群管理员。
        
        这是一个纯函数，用于避免代码重复。
        
        Args:
            user_id: 用户ID
            sender_role: 发送者在群组中的角色 (admin/owner/member)
            
        Returns:
            bool: 如果用户是 Root 或群管理员返回 True，否则返回 False
        """
        return (
            self.rbac_manager.user_has_role(user_id, "root")
            or sender_role in ["admin", "owner"]
        )

    async def _get_channel_game(self, event: GroupMessageEvent):
        """获取当前频道的游戏，如果不存在则回复用户并返回 None"""
        game = await self.db.get_game_by_channel_id(str(event.group_id))
        if not game:
            await event.reply("当前频道没有正在进行的游戏。", at=False)
        return game


    async def handle_help(self, event: GroupMessageEvent):
        """处理 /aigm help 命令，将其渲染为图片发送"""
        try:
            image_bytes = await self.renderer.render_help_page()
            
            if image_bytes:
                await self.api.post_group_file(
                    str(event.group_id),
                    image=f"data:image/png;base64,{bytes_to_base64(image_bytes)}",
                )
            else:
                await event.reply("❌ 生成帮助图片失败，请检查日志。", at=False)
        except Exception as e:
            LOG.error(f"处理帮助命令时出错: {e}", exc_info=True)
            await event.reply("❌ 处理命令时发生错误，请联系管理员。", at=False)

    async def handle_webui(self, event: GroupMessageEvent):
        """处理 /aigm webui 命令"""
        if not self.web_ui:
            await event.reply("Web UI 未启用。", at=False)
            return
        
        # 等待 tunnel 就绪（最多等待 60 秒，首次启动需要下载 cloudflared）
        if not self.web_ui.tunnel_ready.is_set():
            await event.reply("⏳ Web UI 正在启动中，首次启动可能需要下载必要的组件，请稍候...", at=False)
            tunnel_ready = await self.web_ui.wait_for_tunnel(timeout=60.0)
            if not tunnel_ready:
                await event.reply("❌ Web UI 启动超时，请稍后重试或检查日志获取详细信息。", at=False)
                return
        
        if not self.web_ui.tunnel_url:
            await event.reply("❌ Web UI 启动失败，请检查日志获取详细信息。", at=False)
            return

        base_url = self.web_ui.tunnel_url
        game = await self.db.get_game_by_channel_id(str(event.group_id))
        
        if game:
            url = f"{base_url}/game/{game['game_id']}"
            message = f"✅ 当前游戏的 Web UI 地址:\n{url}"
        else:
            url = base_url
            message = f"✅ Web UI 入口地址:\n{url}"
            
        await event.reply(message, at=False)

    async def handle_status(self, event: GroupMessageEvent, api: BotAPI):
        """处理 /aigm status 命令"""
        try:
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
        except Exception as e:
            LOG.error(f"处理状态命令时出错: {e}", exc_info=True)
            await event.reply("❌ 获取状态失败，请联系管理员。", at=False)

    async def handle_branch_list(self, event: GroupMessageEvent, mode: str | None = None):
        """处理 /aigm branch list [all] 命令"""
        group_id = str(event.group_id)
        game = await self.db.get_game_by_channel_id(group_id)

        if not game:
            await event.reply("当前群组没有正在进行的游戏。", at=False)
            return

        game_id = game['game_id']
        
        if mode == "all":
            await event.reply("正在生成完整分支图，请稍候...", at=False)
            image_bytes = await self.visualizer.create_full_branch_graph(game_id)
        else:
            await event.reply("正在生成分支图，请稍候...", at=False)
            image_bytes = await self.visualizer.create_branch_graph(game_id)

        if image_bytes:
            await self.api.post_group_file(
                group_id,
                image=f"data:image/png;base64,{bytes_to_base64(image_bytes)}",
            )
        else:
            await event.reply("生成分支图失败，请检查日志。", at=False)

    async def handle_branch_history(self, event: GroupMessageEvent, branch_name: str | None = None, limit: int = HISTORY_MAX_LIMIT):
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

    async def handle_round_history(self, event: GroupMessageEvent, round_id: int, limit: int = HISTORY_MAX_LIMIT):
        """处理 /aigm round history <id> [limit] 命令，并将每轮渲染到一张图片中"""
        game = await self.db.get_game_by_channel_id(str(event.group_id))
        if not game:
            await event.reply("当前群组没有正在进行的游戏。", at=False)
            return

        if limit > HISTORY_MAX_LIMIT:
            limit = HISTORY_MAX_LIMIT
            await event.reply(f"为了防止消息刷屏和性能问题，历史记录上限设置为{HISTORY_MAX_LIMIT}条。", at=False)

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

    async def handle_branch_create(
        self, event: GroupMessageEvent, name: str, from_round_id: int | None = None
    ):
        """处理 /aigm branch create 命令"""
        user_id = str(event.user_id)
        group_id = str(event.group_id)
        if not await self.check_channel_permission(
            user_id, group_id, event.sender.role
        ):
            await event.reply("权限不足。", at=False)
            return

        game = await self.db.get_game_by_channel_id(group_id)
        if not game:
            await event.reply("当前频道没有正在进行的游戏。", at=False)
            return

        if not await self._validate_name(name):
            await event.reply("❌ 无效的分支名称。名称长度应在1-50之间，且只能包含字母、数字、下划线和连字符。", at=False)
            return

        # 检查分支名是否已存在
        existing_branch = await self.db.get_branch_by_name(game["game_id"], name)
        if existing_branch:
            await event.reply(f"❌ 分支 '{name}' 已存在。", at=False)
            return

        await self.game_manager.create_new_branch(game["game_id"], name, from_round_id)

    async def handle_branch_rename(
        self, event: GroupMessageEvent, old_name: str, new_name: str
    ):
        """处理 /aigm branch rename 命令"""
        user_id = str(event.user_id)
        group_id = str(event.group_id)
        if not await self.check_channel_permission(
            user_id, group_id, event.sender.role
        ):
            await event.reply("权限不足。", at=False)
            return

        game = await self.db.get_game_by_channel_id(group_id)
        if not game:
            await event.reply("当前频道没有正在进行的游戏。", at=False)
            return

        if not await self._validate_name(new_name):
            await event.reply("❌ 无效的分支名称。名称长度应在1-50之间，且只能包含字母、数字、下划线和连字符。", at=False)
            return

        try:
            branch = await self.db.get_branch_by_name(game["game_id"], old_name)
            if not branch:
                await event.reply(f"找不到名为 '{old_name}' 的分支。", at=False)
                return

            # 使用数据库 UNIQUE 约束处理重名，让数据库保证原子性
            await self.db.rename_branch(branch["branch_id"], new_name)
            await event.reply(f"✅ 分支 '{old_name}' 已成功重命名为 '{new_name}'。", at=False)
        except Exception as e:
            error_msg = str(e).lower()
            if "unique" in error_msg or "constraint" in error_msg:
                await event.reply(f"❌ 分支名 '{new_name}' 已被占用。", at=False)
            else:
                LOG.error(f"重命名分支失败: {e}", exc_info=True)
                await event.reply(f"❌ 重命名分支失败: {e}", at=False)

    async def handle_branch_delete(self, event: GroupMessageEvent, name: str):
        """处理 /aigm branch delete 命令"""
        user_id = str(event.user_id)
        group_id = str(event.group_id)
        if not await self.check_channel_permission(
            user_id, group_id, event.sender.role
        ):
            await event.reply("权限不足。", at=False)
            return

        game = await self.db.get_game_by_channel_id(group_id)
        if not game:
            await event.reply("当前频道没有正在进行的游戏。", at=False)
            return

        try:
            async with self.db.transaction():
                # 在事务内获取分支和检查，防止竞态条件
                branch = await self.db.get_branch_by_name(game["game_id"], name)
                if not branch:
                    raise ValueError(f"找不到名为 '{name}' 的分支")
                
                current_game = await self.db.get_game_by_game_id(game["game_id"])
                if not current_game:
                    raise ValueError("游戏不存在")
                if current_game["head_branch_id"] == branch["branch_id"]:
                    raise ValueError("不能删除当前所在的 HEAD 分支")
                
                await self.db.delete_branch(branch["branch_id"])
            
            await event.reply(f"✅ 已成功删除分支 '{name}'。", at=False)
        except ValueError as e:
            await event.reply(f"❌ 删除失败: {e}", at=False)
        except Exception as e:
            LOG.error(f"删除分支 '{name}' 时出现意外错误: {e}", exc_info=True)
            await event.reply("❌ 删除分支时出现意外错误，请检查日志。", at=False)

    async def handle_game_list(self, event: GroupMessageEvent):
        """处理 /aigm game list 命令"""
        try:
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
        except Exception as e:
            LOG.error(f"获取游戏列表失败: {e}", exc_info=True)
            await event.reply("❌ 获取游戏列表失败，请联系管理员。", at=False)

    async def handle_game_start(self, event: GroupMessageEvent, system_prompt: str = ""):
        """处理 /aigm start [system_prompt] 命令"""
        group_id = str(event.group_id)
        user_id = str(event.user_id)

        # 1. 权限检查
        # 如果已有游戏运行，则不允许启动新游戏
        if await self.db.is_game_running(group_id):
            await event.reply("当前频道已有正在进行的游戏。请先结束或 detach 当前游戏。", at=False)
            return

        # 2. 处理 System Prompt
        if system_prompt:
            # 直接启动模式
            # 显式转换类型以通过静态检查
            if TYPE_CHECKING:
                plugin = cast(AIGMPlugin, self.plugin)
            else:
                plugin = self.plugin

            event_handler = getattr(plugin, 'event_handler', None)
            if event_handler is None:
                await event.reply("❌ 插件未完全初始化。", at=False)
                return
            
            if TYPE_CHECKING:
                event_handler = cast(EventHandler, event_handler)
                
            success, error_msg = await event_handler.process_system_prompt(
                group_id,
                user_id,
                system_prompt,
                str(event.message_id)
            )
            if not success:
                # 详细错误已经在 process_system_prompt 中记录到日志，但我们也返回给用户
                await event.reply(f"❌ 处理剧本失败: {error_msg}", at=False)
        else:
            # Web UI 启动模式
            if not self.web_ui or not self.web_ui.tunnel_url:
                await event.reply("❌ Web UI 未启用或 Tunnel 未就绪，无法使用网页启动功能。\n请尝试直接附带剧本: /aigm start <剧本内容>", at=False)
                return

            # 生成一次性 Token
            token = str(uuid.uuid4())
            await self.cache_manager.add_web_start_token(token, group_id, user_id)
            
            start_url = f"{self.web_ui.tunnel_url}/game/start?token={token}"
            
            await event.reply(
                f"🚀 请点击下方链接进入网页端输入剧本：\n{start_url}\n\n"
                f"💡 链接有效期 10 分钟，提交后请在群内确认。",
                at=False
            )

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
        if not await self.check_channel_permission(user_id, group_id, event.sender.role):
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
        if not await self.check_channel_permission(user_id, group_id, event.sender.role):
            await event.reply("权限不足，您必须是群管理员、root用户或该频道游戏的主持人。", at=False)
            return

        game = await self.db.get_game_by_channel_id(group_id)
        if not game:
            await event.reply("当前频道没有正在进行的游戏。", at=False)
            return

        game_id = game['game_id']
        await self.game_manager.checkout_head(game_id)

    async def handle_checkout(self, event: GroupMessageEvent, branch_name: str):
        """处理 /aigm checkout <branch> 命令"""
        user_id = str(event.user_id)
        group_id = str(event.group_id)
        if not await self.check_channel_permission(
            user_id, group_id, event.sender.role
        ):
            await event.reply("权限不足。", at=False)
            return

        game = await self.db.get_game_by_channel_id(group_id)
        if not game:
            await event.reply("当前频道没有正在进行的游戏。", at=False)
            return

        await self.game_manager.switch_branch(game["game_id"], branch_name)

    async def handle_reset(self, event: GroupMessageEvent, round_id: int):
        """处理 /aigm reset <round_id> 命令"""
        user_id = str(event.user_id)
        group_id = str(event.group_id)
        if not await self.check_channel_permission(
            user_id, group_id, event.sender.role
        ):
            await event.reply("权限不足。", at=False)
            return

        game = await self.db.get_game_by_channel_id(group_id)
        if not game:
            await event.reply("当前频道没有正在进行的游戏。", at=False)
            return

        await self.game_manager.reset_current_branch(game["game_id"], round_id)

    async def handle_tag_create(
        self, event: GroupMessageEvent, name: str, round_id: int | None = None
    ):
        """处理 /aigm tag create 命令"""
        user_id = str(event.user_id)
        group_id = str(event.group_id)
        if not await self.check_channel_permission(
            user_id, group_id, event.sender.role
        ):
            await event.reply("权限不足。", at=False)
            return

        game = await self.db.get_game_by_channel_id(group_id)
        if not game:
            await event.reply("当前频道没有正在进行的游戏。", at=False)
            return

        target_round_id = round_id
        if target_round_id is None:
            head_info = await self.db.get_game_and_head_branch_info(game["game_id"])
            target_round_id = head_info["tip_round_id"]

        if not await self._validate_name(name):
            await event.reply("❌ 无效的标签名称。名称长度应在1-50之间，且只能包含字母、数字、下划线和连字符。", at=False)
            return

        if not await self.db.get_round_info(target_round_id):
            await event.reply(f"找不到回合 {target_round_id}。", at=False)
            return

        # 检查标签名是否已存在
        existing_tag = await self.db.get_tag_by_name(game["game_id"], name)
        if existing_tag:
            await event.reply(f"❌ 标签 '{name}' 已存在。", at=False)
            return

        await self.db.create_tag(game["game_id"], name, target_round_id)
        await event.reply(f"🏷️ 已在回合 {target_round_id} 创建标签 '{name}'。", at=False)

    async def handle_tag_list(self, event: GroupMessageEvent):
        """处理 /aigm tag list 命令"""
        game = await self.db.get_game_by_channel_id(str(event.group_id))
        if not game:
            await event.reply("当前频道没有正在进行的游戏。", at=False)
            return

        tags = await self.db.get_all_tags_for_game(game["game_id"])
        if not tags:
            await event.reply("当前游戏还没有任何标签。", at=False)
            return

        tag_list_text = "标签列表:\n"
        for tag in tags:
            tag_list_text += f"- {tag['name']} -> (Round {tag['round_id']})\n"
        await event.reply(tag_list_text.strip(), at=False)

    async def handle_tag_show(self, event: GroupMessageEvent, name: str):
        """处理 /aigm tag show 命令"""
        game = await self.db.get_game_by_channel_id(str(event.group_id))
        if not game:
            await event.reply("当前频道没有正在进行的游戏。", at=False)
            return

        tag = await self.db.get_tag_by_name(game["game_id"], name)
        if not tag:
            await event.reply(f"找不到名为 '{name}' 的标签。", at=False)
            return

        await self._show_round_content(event, tag["round_id"])

    async def handle_tag_history(
        self, event: GroupMessageEvent, name: str, limit: int = HISTORY_MAX_LIMIT
    ):
        """处理 /aigm tag history 命令"""
        game = await self.db.get_game_by_channel_id(str(event.group_id))
        if not game:
            await event.reply("当前频道没有正在进行的游戏。", at=False)
            return

        tag = await self.db.get_tag_by_name(game["game_id"], name)
        if not tag:
            await event.reply(f"找不到名为 '{name}' 的标签。", at=False)
            return

        await self.handle_round_history(event, tag["round_id"], limit)

    async def handle_tag_delete(self, event: GroupMessageEvent, name: str):
        """处理 /aigm tag delete 命令"""
        user_id = str(event.user_id)
        group_id = str(event.group_id)
        if not await self.check_channel_permission(
            user_id, group_id, event.sender.role
        ):
            await event.reply("权限不足。", at=False)
            return

        game = await self.db.get_game_by_channel_id(group_id)
        if not game:
            await event.reply("当前频道没有正在进行的游戏。", at=False)
            return

        await self.db.delete_tag(game["game_id"], name)
        await event.reply(f"✅ 已成功删除标签 '{name}'。", at=False)

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

    async def handle_admin_refresh_tunnel(self, event: GroupMessageEvent):
        """处理 /aigm admin refresh-tunnel 命令"""
        # 权限检查：只允许 ROOT 用户
        if not self.rbac_manager.user_has_role(str(event.user_id), "root"):
            await event.reply("权限不足。只有root用户才能刷新tunnel。", at=False)
            return
        
        if not self.web_ui:
            await event.reply("❌ Web UI 未启用。", at=False)
            return
        
        await event.reply("🔄 正在刷新 Cloudflare tunnel，请稍候...", at=False)
        
        success = await self.web_ui.refresh_tunnel()
        
        if success and self.web_ui.tunnel_url:
            await event.reply(f"✅ Tunnel 刷新成功！\n新地址: {self.web_ui.tunnel_url}", at=False)
        else:
            await event.reply("❌ Tunnel 刷新失败，请查看日志获取详细信息。", at=False)

    async def handle_admin_clear_help_cache(self, event: GroupMessageEvent):
        """处理 /aigm admin clear-help-cache 命令"""
        # 权限检查：只允许 ROOT 用户
        if not self.rbac_manager.user_has_role(str(event.user_id), "root"):
            await event.reply("权限不足。只有root用户才能清除帮助缓存。", at=False)
            return
        
        self.renderer.clear_help_cache()
        await event.reply("✅ 已成功清除帮助图片缓存。", at=False)

    async def handle_advanced_mode(self, event: GroupMessageEvent, action: str):
        """处理 /aigm advanced-mode <enable|disable|status> 命令"""
        user_id = str(event.user_id)
        group_id = str(event.group_id)

        # 权限检查：只有群管理员、root用户或游戏主持人可以操作
        if not await self.check_channel_permission(user_id, group_id, event.sender.role):
            await event.reply("权限不足。您必须是群管理员、root用户或该频道游戏的主持人。", at=False)
            return

        if not self.channel_config:
            await event.reply("❌ 频道配置管理器未初始化。", at=False)
            return

        if action == "enable":
            # 启用高级模式
            success = await self.channel_config.enable_advanced_mode(group_id, user_id)
            if success:
                await event.reply(
                    "✅ 已为本频道启用高级模式。\n"
                    "📌 在此模式下，AI GM 将发送 Web UI 链接而非渲染图片，但表情功能保持正常。",
                    at=False
                )
            else:
                await event.reply("❌ 启用高级模式失败，请检查日志。", at=False)

        elif action == "disable":
            # 禁用高级模式
            success = await self.channel_config.disable_advanced_mode(group_id)
            if success:
                await event.reply("✅ 已为本频道禁用高级模式，将恢复发送渲染图片。", at=False)
            else:
                await event.reply("❌ 禁用高级模式失败，请检查日志。", at=False)

        elif action == "status":
            # 查看状态
            is_enabled = await self.channel_config.is_advanced_mode_enabled(group_id)
            config = await self.channel_config.get_channel_config(group_id)

            if is_enabled:
                enabled_at = config.get("enabled_at", "未知时间")
                enabled_by = config.get("enabled_by", "未知用户")
                status_msg = (
                    f"🔧 当前频道状态：高级模式已启用\n"
                    f"👤 启用者：{enabled_by}\n"
                    f"⏰ 启用时间：{enabled_at}\n"
                    f"📱 AI GM 将发送 Web UI 链接而非图片"
                )
            else:
                status_msg = "🔧 当前频道状态：高级模式未启用\n📱 AI GM 将发送渲染图片"

            await event.reply(status_msg, at=False)

        else:
            await event.reply(
                "❌ 无效的操作。请使用：/aigm advanced-mode <enable|disable|status>\n"
                "• enable - 启用高级模式\n"
                "• disable - 禁用高级模式\n"
                "• status - 查看当前状态",
                at=False
            )

    # --- LLM Management ---

    async def handle_llm_add(self, event: PrivateMessageEvent, name: str, model: str, base_url: str, api_key: str, force: bool = False):
        """处理私聊 /aigm llm add 指令"""
        if not self.llm_config_manager:
            await event.reply("❌ LLM 配置管理器未初始化。")
            return
        
        user_id = str(event.user_id)
        
        # 构建预设对象
        preset: LLMPreset = {
            "model": model,
            "base_url": base_url,
            "api_key": api_key
        }
        
        # 先测试预设可用性
        await event.reply(f"🔍 正在测试预设 '{name}' 的连接性...")
        
        llm_api = getattr(self.plugin, 'llm_api', None)
        if TYPE_CHECKING:
            llm_api = cast(LLM_API | None, llm_api)

        is_valid, error_msg = await self.llm_config_manager.test_preset(preset, llm_api)
        
        if is_valid or force:
            # 测试成功或强制保存
            try:
                await self.llm_config_manager.add_preset(user_id, name, model, base_url, api_key)
                
                # Safe logging
                key_preview = "***" + api_key[-4:] if len(api_key) > 4 else "***"
                LOG.info(f"User {user_id} added LLM preset '{name}' (model={model}, base_url={base_url}, key={key_preview})")
                
                msg = f"✅ 已保存 LLM 预设: {name}\n模型: {model}\n📌 现在可以在群聊中使用 /aigm llm bind {name} 贡献算力"
                if not is_valid:
                    msg = f"⚠️ 预设测试失败({error_msg})，但已强制保存。\n" + msg
                else:
                    msg = "✅ 预设测试成功！\n" + msg
                
                await event.reply(msg)
            except Exception as e:
                LOG.error(f"保存预设失败: {e}", exc_info=True)
                await event.reply(f"❌ 保存预设失败: {e}")
        else:
            # 测试失败且未强制保存
            await event.reply(
                f"⚠️ 预设测试失败: {error_msg}\n\n"
                f"可能的原因：\n"
                f"• API Key 无效或已过期\n"
                f"• 模型名称错误\n"
                f"• Base URL 不正确\n"
                f"• 网络连接问题\n\n"
                f"❌ 预设未保存。如需强制保存，请在命令末尾添加 --force"
            )

    async def handle_llm_remove(self, event: PrivateMessageEvent, name: str):
        """处理私聊 /aigm llm remove 指令"""
        if not self.llm_config_manager:
            await event.reply("❌ LLM 配置管理器未初始化。")
            return

        user_id = str(event.user_id)
        success, using_groups = await self.llm_config_manager.remove_preset(user_id, name)
        
        if success:
            await event.reply(f"✅ 已删除 LLM 预设: {name}")
        else:
            if using_groups:
                groups_str = ", ".join(using_groups)
                await event.reply(f"❌ 删除失败: 该预设正在被以下群组使用: {groups_str}\n请先解除绑定后再删除。")
            else:
                await event.reply(f"❌ 删除失败: 找不到名为 '{name}' 的预设。")

    async def handle_llm_test(self, event: PrivateMessageEvent, name: str):
        """处理私聊 /aigm llm test 指令 - 手动测试预设"""
        if not self.llm_config_manager:
            await event.reply("❌ LLM 配置管理器未初始化。")
            return

        user_id = str(event.user_id)
        preset = await self.llm_config_manager.get_preset(user_id, name)
        
        if not preset:
            await event.reply(f"❌ 找不到名为 '{name}' 的预设。")
            return

        await event.reply(f"🔍 正在测试预设 '{name}'...\n模型: {preset['model']}\nBase URL: {preset['base_url']}")
        
        llm_api = getattr(self.plugin, 'llm_api', None)
        if TYPE_CHECKING:
            llm_api = cast(LLM_API | None, llm_api)

        is_valid, error_msg = await self.llm_config_manager.test_preset(preset, llm_api)
        
        if is_valid:
            await event.reply(
                f"✅ 测试成功！\n"
                f"预设 '{name}' 可以正常使用\n"
                f"模型: {preset['model']}"
            )
        else:
            await event.reply(
                f"❌ 测试失败\n"
                f"预设: {name}\n"
                f"错误: {error_msg}\n\n"
                f"💡 建议：\n"
                f"• 检查 API Key 是否有效\n"
                f"• 确认模型名称正确\n"
                f"• 验证 Base URL 可访问\n"
                f"• 如需修改，请删除后重新添加"
            )

    async def handle_llm_status(self, event: GroupMessageEvent | PrivateMessageEvent):
        """显示 LLM 状态信息：私聊显示预设列表，群聊显示绑定状态"""
        if not self.llm_config_manager:
            await event.reply("❌ LLM 配置管理器未初始化。")
            return

        msg = ""
        
        # 1. 私聊/所有场景：显示用户的预设列表
        # 在群聊中也显示这个吗？用户可能想知道自己有哪些预设可以 bind。
        # 为了保持界面整洁，群聊中可以简化显示，或者只显示 status。
        # 现在的逻辑是混合显示的。如果用户只是想看群状态，看到一大堆自己的预设可能会烦。
        # 改动：群聊只显示绑定状态，私聊只显示预设列表。
        
        if isinstance(event, PrivateMessageEvent):
            user_id = str(event.user_id)
            presets = await self.llm_config_manager.get_user_presets_safe(user_id)
            msg += "📋 您的 LLM 预设列表:\n"
            if not presets:
                msg += "(无)\n"
            else:
                for name, p in presets.items():
                    msg += f"- {name}: {p['model']} ({p['api_key']})\n"

        elif isinstance(event, GroupMessageEvent):
            group_id = str(event.group_id)
            status = await self.llm_config_manager.get_binding_status(group_id)
            msg += "🔗 当前群聊 LLM 绑定状态:\n"
            
            active = status.get("active")
            if active:
                owner = active["owner_id"]
                ttl = "永久"
                if active["expire_at"]:
                    remaining = int(active["expire_at"] - time.time())
                    ttl = f"剩余 {remaining//60} 分钟" if remaining > 0 else "已过期"
                msg += f"✅ Active: {active['preset_name']} (by {owner}) - {ttl}\n"
            else:
                msg += "⚪ Active: 无\n"
                
            fallback = status.get("fallback")
            if fallback:
                msg += f"🛡️ Fallback: {fallback['preset_name']} (by {fallback['owner_id']})\n"
            else:
                msg += "⚪ Fallback: 无\n"

        if msg:
            await event.reply(msg)

    async def handle_llm_bind(self, event: GroupMessageEvent, preset_name: str, duration_str: str | None = None):
        """处理群聊 /aigm llm bind 指令"""
        if not self.llm_config_manager:
            await event.reply("❌ LLM 配置管理器未初始化。", at=False)
            return

        user_id = str(event.user_id)
        preset = await self.llm_config_manager.get_preset(user_id, preset_name)
        if not preset:
            await event.reply(f"❌ 找不到名为 '{preset_name}' 的预设，请先私聊 Bot 添加。", at=False)
            return

        duration = None
        if duration_str:
            if duration_str == "--session":
                # Session 暂时等同于 24 小时，或者直到 detach
                duration = 24 * 3600
            else:
                duration = self.llm_config_manager.parse_duration(duration_str)
                if duration is None:
                    await event.reply(
                        "❌ 时长格式错误。\n"
                        "请务必包含时间单位（m/h/d）。\n"
                        "支持的格式示例：\n"
                        "• 30m (30分钟)\n"
                        "• 12h (12小时)\n"
                        "• 7d (7天)\n"
                        "• --session (会话级，暂定24h)\n"
                        "注意：最长支持 90 天。",
                        at=False
                    )
                    return
        
        success, msg = await self.llm_config_manager.bind_active(str(event.group_id), user_id, preset_name, duration)
        if success:
            ttl_msg = f"有效时长: {duration//60} 分钟" if duration else "永久有效"
            await event.reply(f"✅ 成功绑定 LLM 预设: {preset_name}\n{ttl_msg}\n感谢您的算力贡献！", at=False)
        else:
            await event.reply(f"❌ 绑定失败：{msg}", at=False)

    async def handle_llm_unbind(self, event: GroupMessageEvent):
        """处理群聊 /aigm llm unbind 指令"""
        if not self.llm_config_manager:
            return

        group_id = str(event.group_id)
        user_id = str(event.user_id)
        
        # 检查绑定状态
        status = await self.llm_config_manager.get_binding_status(group_id)
        active = status.get("active")
        
        if not active:
            await event.reply("当前没有 Active 绑定。", at=False)
            return

        # 权限检查：所有者 或 管理员 或 游戏主持人
        is_owner = active["owner_id"] == user_id
        # 复用 check_channel_permission，它包含了 Root、群管理员和游戏主持人的检查
        has_permission = await self.check_channel_permission(user_id, group_id, event.sender.role)
        
        if is_owner or has_permission:
            await self.llm_config_manager.unbind_active(group_id)
            await event.reply("✅ 已解除 Active 绑定。", at=False)
        else:
            await event.reply("❌ 权限不足：只能解除自己绑定的预设，管理员和当前游戏主持人除外。", at=False)

    async def handle_llm_set_fallback(self, event: GroupMessageEvent, preset_name: str):
        """处理 /aigm llm set-fallback 指令 (仅管理员)"""
        if not self.llm_config_manager:
            return

        user_id = str(event.user_id)
        if not self._check_has_root_or_admin(user_id, event.sender.role):
            await event.reply("❌ 权限不足：只有管理员可以设置 Fallback。", at=False)
            return

        preset = await self.llm_config_manager.get_preset(user_id, preset_name)
        if not preset:
            await event.reply(f"❌ 找不到名为 '{preset_name}' 的预设。", at=False)
            return

        await self.llm_config_manager.set_fallback(str(event.group_id), user_id, preset_name)
        await event.reply(f"🛡️ 已设置保底 LLM 预设: {preset_name}", at=False)

    async def handle_llm_clear_fallback(self, event: GroupMessageEvent):
        """处理 /aigm llm clear-fallback 指令 (仅管理员)"""
        if not self.llm_config_manager:
            return

        user_id = str(event.user_id)
        if not self._check_has_root_or_admin(user_id, event.sender.role):
            await event.reply("❌ 权限不足。", at=False)
            return

        await self.llm_config_manager.clear_fallback(str(event.group_id))
        await event.reply("已清除保底 LLM 配置。", at=False)
