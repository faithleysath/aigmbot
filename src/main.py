from ncatbot.core import BotClient, PrivateMessage

# ========== 创建 BotClient 实例 ==========
bot = BotClient()

# ========= 注册一个简单的回调函数 ==========
# 当收到私聊消息 "ping" 时，回复 "pong"
@bot.private_event()
async def on_private_message(msg: PrivateMessage):
    print(f"收到来自 {msg.user_id} 的私聊消息: {msg.raw_message}")
    if msg.raw_message == "ping":
        await bot.api.post_private_msg(msg.user_id, text="pong from docker!")
    if msg.raw_message == "测试":
        await bot.api.post_private_msg(msg.user_id, text="NcatBot in Docker 测试成功喵~")

# ========== 启动 BotClient ==========
print(f"NcatBot 正在启动...")
print("首次运行或QQ掉线后，请在终端内扫描二维码登录。")

# 以前台模式运行 Bot，它会一直运行直到容器停止
bot.run_frontend()
