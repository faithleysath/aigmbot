from ncatbot.core import BotClient

# 创建 BotClient 实例
bot = BotClient()

# 以前台模式运行 Bot，这将自动加载 plugins 目录下的所有插件
# 注意：NcatBot 会在工作目录下寻找 plugins 文件夹
# 因此，请确保您的工作目录是 src 文件夹
bot.run_frontend()
