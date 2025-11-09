"""AI GM 插件的常量定义"""

# 缓存相关
CACHE_SAVE_THROTTLE_SECONDS = 0.3  # 缓存保存节流时间（秒）
CACHE_SAVE_DELAY_SECONDS = 0.5  # 延迟保存等待时间（秒）

# 数据库相关
DB_BUSY_TIMEOUT_MS = 5000  # 数据库忙等待超时时间（毫秒）
DB_WAL_AUTOCHECKPOINT = 2000  # WAL 自动检查点阈值
MAX_HISTORY_ROUNDS = 999999  # 历史记录查询的最大回合数（事实上的无限）

# 渲染相关
RENDER_WIDTH = 1200  # 渲染图片宽度（像素）
RENDER_PADDING = 50  # 渲染图片内边距（像素）
RENDER_TOP_PADDING = 100  # 顶部内边距，为阅读时间提示留出空间（像素）
BASE_FONT_SIZE = 47  # 基础字体大小（像素）
HEADER_FONT_SIZE = 30  # 头部信息字体大小（像素）
READING_SPEED_WPM = 350  # 阅读速度（字/分钟）
MAX_CONCURRENT_RENDERS = 3  # 最大并发渲染数量

# 命令相关
HISTORY_MAX_LIMIT = 10  # 历史记录显示的默认/最大条数

# 表情 ID
EMOJI = {
    # 主贴选项
    "A": 127822,
    "B": 9973,
    "C": 128663,
    "D": 128054,
    "E": 127859,
    "F": 128293,
    "G": 128123,
    # 管理员确认/否决（主贴）
    "CONFIRM": 127881,  # 🎉
    "DENY": 128560,  # 😰
    "RETRACT": 10060,  # ❌
    # 自定义输入投票
    "YAY": 127881,  # 🎉
    "NAY": 128560,  # 😰
    "CANCEL": 10060,  # ❌
    # 频道繁忙
    "COFFEE": 9749,  # ☕
}
