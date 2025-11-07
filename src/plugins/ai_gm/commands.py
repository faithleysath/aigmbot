# src/plugins/ai_gm/commands.py
from ncatbot.plugin_system import NcatBotPlugin
from ncatbot.core.event import GroupMessageEvent
from ncatbot.core.event.message_segment import At, MessageArray, Text
from ncatbot.utils import get_log

from .db import Database
from .game_manager import GameManager
from .cache import CacheManager

LOG = get_log(__name__)


class CommandHandler:
    def __init__(
        self,
        plugin: NcatBotPlugin,
        db: Database,
        game_manager: GameManager,
        cache_manager: CacheManager,
    ):
        self.plugin = plugin
        self.api = plugin.api
        self.db = db
        self.game_manager = game_manager
        self.cache_manager = cache_manager
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
        /aigm - 显示此帮助信息
        /aigm status - 查看当前群组的游戏状态
        /aigm game list - 列出所有游戏
        /aigm game attach <id> - 将游戏附加到当前频道
        /aigm game detach - 从当前频道分离游戏
        /aigm game sethost [id] @user - 变更游戏主持人
        /aigm checkout head - 重新加载并显示当前游戏的最新状态
        /aigm admin unfreeze - [管理员] 强制解冻当前游戏
        """
        await event.reply(help_text.strip())

    async def handle_status(self, event: GroupMessageEvent):
        """处理 /aigm status 命令"""
        group_id = str(event.group_id)
        game = await self.db.get_game_by_channel_id(group_id)

        if not game:
            await event.reply("当前群组没有正在进行的游戏。")
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
                Text(f"\n- 主消息ID: {game['main_message_id']}")
            ])

        await event.reply(rtf=message_array)

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

        await event.reply(game_list_text.strip())

    async def handle_game_attach(self, event: GroupMessageEvent, args: tuple[str, ...]):
        """处理 /aigm game attach <id> 命令"""
        if len(args) < 3:
            await event.reply("请提供游戏ID。用法: /aigm game attach <id>")
            return
        game_id_str = args[2]

        try:
            game_id = int(game_id_str)
            target_game = await self.db.get_game_by_game_id(game_id)

            # Permission Check
            is_root = self.rbac_manager.user_has_role(str(event.user_id), "root")
            is_group_admin = event.sender.role in ["admin", "owner"]
            is_target_game_host = target_game and str(target_game["host_user_id"]) == str(event.user_id)

            if not (is_root or is_group_admin or is_target_game_host):
                await event.reply("权限不足。您必须是群管理员、root用户或该游戏的主持人。")
                return

            # Logic
            group_id = str(event.group_id)
            if await self.db.is_game_running(group_id):
                await event.reply("当前频道已经有一个正在进行的游戏。")
                return
            if not target_game:
                await event.reply(f"找不到ID为 {game_id} 的游戏。")
                return
            if target_game['channel_id']:
                await event.reply(f"游戏 {game_id} 已经附加到频道 {target_game['channel_id']}。")
                return

            await self.db.attach_game_to_channel(game_id, group_id)
            await event.reply(f"成功将游戏 {game_id} 附加到当前频道。")
            await self.game_manager.checkout_head(game_id)

        except ValueError:
            await event.reply("无效的游戏ID，请输入一个数字。")
        except Exception as e:
            # 兜底处理 UNIQUE 约束错误或其他 DB 写入错误
            LOG.error(f"附加游戏失败: {e}", exc_info=True)
            await event.reply("附加失败：可能已被其他并发操作占用本频道，请稍后重试。")

    async def handle_game_set_host(self, event: GroupMessageEvent, args: tuple[str, ...]):
        """处理 /aigm game sethost [id] @user 命令"""
        at_segments = event.message.filter(At)
        if not at_segments:
            await event.reply("请 @ 一位用户作为新的主持人。")
            return
        new_host_id = at_segments[0].qq

        # Parse args: /aigm game sethost @user OR /aigm game sethost 123 @user
        game_id_str = args[2] if len(args) > 2 and args[2].isdigit() else None
        target_game_id = None

        try:
            if game_id_str:
                target_game_id = int(game_id_str)
            else:
                game = await self.db.get_game_by_channel_id(str(event.group_id))
                if game:
                    target_game_id = game["game_id"]

            if target_game_id is None:
                await event.reply("无法确定要操作的游戏。")
                return

            # Permission Check
            target_game = await self.db.get_game_by_game_id(target_game_id)
            is_root = self.rbac_manager.user_has_role(str(event.user_id), "root")
            is_group_admin = event.sender.role in ["admin", "owner"]
            is_target_game_host = target_game and str(target_game["host_user_id"]) == str(event.user_id)

            if not (is_root or is_group_admin or is_target_game_host):
                await event.reply("权限不足。您必须是群管理员、root用户或该游戏的主持人。")
                return

            # Logic
            await self.db.update_game_host(target_game_id, new_host_id)
            await event.reply(
                rtf=MessageArray(
                    [
                        Text(f"✅ 成功将游戏 {target_game_id} 的主持人变更为 "),
                        At(new_host_id),
                        Text("。"),
                    ]
                )
            )
        except ValueError:
            await event.reply("无效的游戏ID。")
        except Exception as e:
            LOG.error(f"变更游戏主持人失败: {e}", exc_info=True)
            await event.reply("变更主持人失败，请查看日志。")

    async def handle_game_detach(self, event: GroupMessageEvent):
        """处理 /aigm game detach 命令"""
        user_id = str(event.user_id)
        group_id = str(event.group_id)
        if not await self._is_authorized_for_channel_action(user_id, group_id, event.sender.role):
            await event.reply("权限不足，您必须是群管理员、root用户或该频道游戏的主持人。")
            return

        game = await self.db.get_game_by_channel_id(group_id)
        if not game:
            await event.reply("当前频道没有附加任何游戏。")
            return

        game_id = game['game_id']
        await self.db.detach_game_from_channel(game_id)
        await self.cache_manager.clear_group_vote_cache(group_id)
        await event.reply(f"成功从当前频道分离游戏 {game_id}，并已清理相关缓存。")

    async def handle_checkout_head(self, event: GroupMessageEvent):
        """处理 /aigm checkout head 命令"""
        user_id = str(event.user_id)
        group_id = str(event.group_id)
        if not await self._is_authorized_for_channel_action(user_id, group_id, event.sender.role):
            await event.reply("权限不足，您必须是群管理员、root用户或该频道游戏的主持人。")
            return

        game = await self.db.get_game_by_channel_id(group_id)
        if not game:
            await event.reply("当前频道没有正在进行的游戏。")
            return

        game_id = game['game_id']
        await self.game_manager.checkout_head(game_id)
        await event.reply(f"游戏 {game_id} 的最新状态已刷新。")

    async def handle_cache_pending_clear(self, event: GroupMessageEvent):
        """处理 /aigm cache pending clear 命令"""
        await self.cache_manager.clear_pending_games()
        await event.reply("已清空所有待处理的新游戏请求缓存。")

    async def handle_admin_unfreeze(self, event: GroupMessageEvent):
        """处理 /aigm admin unfreeze 命令"""
        is_root = self.rbac_manager.user_has_role(str(event.user_id), "root")
        is_group_admin = event.sender.role in ["admin", "owner"]
        if not (is_root or is_group_admin):
            await event.reply("权限不足。您必须是群管理员或root用户。")
            return

        group_id = str(event.group_id)
        game = await self.db.get_game_by_channel_id(group_id)

        if not game:
            await event.reply("当前频道没有正在进行的游戏。")
            return

        if not game["is_frozen"]:
            await event.reply("游戏未处于冻结状态。")
            return

        game_id = game["game_id"]
        await self.db.set_game_frozen_status(game_id, False)
        await event.reply(f"✅ 游戏 {game_id} 已被成功解冻，您可以继续操作了。")
