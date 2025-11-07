# src/plugins/ai_gm/commands.py
from ncatbot.plugin_system import NcatBotPlugin
from ncatbot.core.event import BaseMessageEvent, GroupMessageEvent
from ncatbot.core.event.message_segment import At, MessageArray, Text
from ncatbot.utils import get_log

from .db import Database
from .game_manager import GameManager

LOG = get_log(__name__)


class CommandHandler:
    def __init__(
        self,
        plugin: NcatBotPlugin,
        db: Database,
        game_manager: GameManager,
    ):
        self.plugin = plugin
        self.api = plugin.api
        self.db = db
        self.game_manager = game_manager

    async def handle_help(self, event: GroupMessageEvent):
        """处理 /aigm help 命令"""
        help_text = """
        /aigm - 显示此帮助信息
        /aigm status - 查看当前群组的游戏状态
        /aigm game list - 列出所有游戏
        /aigm game attach <id> - 将游戏附加到当前频道
        /aigm game detach - 从当前频道分离游戏
        /aigm checkout head - 重新加载并显示当前游戏的最新状态
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

    async def handle_game_attach(self, event: GroupMessageEvent, game_id_str: str):
        """处理 /aigm game attach <id> 命令"""
        try:
            game_id = int(game_id_str)
        except ValueError:
            await event.reply("无效的游戏ID，请输入一个数字。")
            return

        group_id = str(event.group_id)

        # 检查当前频道是否已有游戏
        if await self.db.is_game_running(group_id):
            await event.reply("当前频道已经有一个正在进行的游戏。")
            return

        # 检查目标游戏是否存在
        target_game = await self.db.get_game_by_game_id(game_id)
        if not target_game:
            await event.reply(f"找不到ID为 {game_id} 的游戏。")
            return

        # 检查目标游戏是否已附加到其他频道
        if target_game['channel_id']:
            await event.reply(f"游戏 {game_id} 已经附加到频道 {target_game['channel_id']}。")
            return

        # 附加游戏
        await self.db.attach_game_to_channel(game_id, group_id)
        await event.reply(f"成功将游戏 {game_id} 附加到当前频道。")

    async def handle_game_detach(self, event: GroupMessageEvent):
        """处理 /aigm game detach 命令"""
        group_id = str(event.group_id)
        game = await self.db.get_game_by_channel_id(group_id)

        if not game:
            await event.reply("当前频道没有附加任何游戏。")
            return

        game_id = game['game_id']
        await self.db.detach_game_from_channel(game_id)
        await event.reply(f"成功从当前频道分离游戏 {game_id}。")

    async def handle_checkout_head(self, event: GroupMessageEvent):
        """处理 /aigm checkout head 命令"""
        group_id = str(event.group_id)
        game = await self.db.get_game_by_channel_id(group_id)

        if not game:
            await event.reply("当前频道没有正在进行的游戏。")
            return

        game_id = game['game_id']
        await self.game_manager.checkout_head(game_id)
        await event.reply(f"游戏 {game_id} 的最新状态已刷新。")
