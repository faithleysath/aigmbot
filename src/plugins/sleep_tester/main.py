import asyncio
from ncatbot.plugin_system import NcatBotPlugin, filter_registry
from ncatbot.core.event import GroupMessageEvent

class SleepTesterPlugin(NcatBotPlugin):
    name = "SleepTesterPlugin"
    version = "1.0.0"
    description = "A plugin to test asyncio event loop blocking."
    author = "Cline"

    @filter_registry.group_filter
    async def handle_group_message(self, event: GroupMessageEvent):
        if event.raw_message.strip() == "/sleep":
            await event.reply("Starting a long sleep... Other plugins should still be responsive.")
            await asyncio.sleep(99999)
            await event.reply("Sleep finished (this should not be seen).")
