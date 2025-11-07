# AIGM Bot

基于 [NcatBot](https://github.com/soulter/ncatbot) 框架的 QQ 机器人，提供 AI 互动叙事游戏、群文件管理、消息自动打包等功能。

## 功能特性

### 🎮 AI Game Master (AI GM)
- **AI 驱动的互动叙事游戏**：使用 OpenAI API 生成动态剧情
- **分支与版本控制**：类 Git 的游戏状态管理，支持回退和分支
- **投票系统**：支持预设选项和自定义输入投票
- **Markdown 渲染**：将剧情渲染为精美图片
- **权限管理**：支持主持人和管理员角色
- **游戏持久化**：SQLite 数据库存储游戏状态

### 📁 群文件管理器
- **批量删除**：一键清理群文件根目录
- **二次确认**：防止误操作
- **权限控制**：仅群管理员可用

### 📦 消息自动打包
- **智能压缩**：自动将群聊消息打包为合并转发
- **二级压缩**：支持嵌套打包，节省聊天空间
- **灵活配置**：可自定义触发阈值
- **自动撤回**：Bot 拥有管理员权限时自动撤回原始消息

### 📊 运行状态查询
- 查看 Bot 运行时长
- 系统状态监控

## 快速开始

### 环境要求

- Python 3.9+
- Docker (可选，用于容器化部署)
- Playwright 浏览器驱动 (用于 Markdown 渲染)

### 安装

1. **克隆仓库**
```bash
git clone https://github.com/faithleysath/aigmbot.git
cd aigmbot
```

2. **安装依赖**
```bash
pip install -r requirements.txt
```

3. **安装 Playwright 浏览器**
```bash
playwright install chromium
```

### 配置

1. **NcatBot 配置**

首次运行时，NcatBot 会自动生成配置文件。你需要配置 QQ 账号连接信息（如 go-cqhttp、LLOneBot 等）。

2. **AI GM 插件配置**

编辑 `data/configs/AIGMPlugin.json`（首次运行后自动生成）：

```json
{
  "openai_api_key": "your-api-key-here",
  "openai_base_url": "https://api.openai.com/v1",
  "openai_model_name": "gpt-4-turbo",
  "pending_game_timeout": 300
}
```

配置项说明：
- `openai_api_key`: OpenAI API 密钥（必填）
- `openai_base_url`: API 端点地址（支持兼容 OpenAI 的第三方服务）
- `openai_model_name`: 使用的模型名称
- `pending_game_timeout`: 新游戏等待确认的超时时间（秒）

### 运行

```bash
cd src
python main.py
```

## 使用说明

### AI GM 游戏插件

#### 开始游戏

1. 在群聊中上传 `.txt` 或 `.md` 文件作为系统提示词（游戏设定）
2. Bot 会发送预览消息并添加表情供确认
3. 点击 🎉 表情确认开始游戏

#### 游戏控制命令

```bash
# 查看帮助
/aigm help

# 查看当前游戏状态
/aigm status

# 游戏管理
/aigm game list                    # 列出所有游戏
/aigm game attach <id>             # 将游戏附加到当前频道
/aigm game detach                  # 从当前频道分离游戏
/aigm game sethost @user           # 变更当前游戏主持人
/aigm game sethost-by-id <id> @user # 变更指定游戏主持人

# 历史操作
/aigm checkout head                # 重新加载并显示最新状态

# 管理员命令
/aigm admin unfreeze               # 强制解冻游戏
/aigm admin delete <id>            # [ROOT] 删除指定游戏

# 缓存管理
/aigm cache pending clear          # 清空待处理的新游戏请求
```

#### 游戏进行流程

1. **查看剧情**：Bot 发送带选项（A-G）的剧情图片
2. **投票选择**：
   - 对主消息添加字母表情（A-G）投票预设选项
   - 回复主消息（需 @Bot）提交自定义输入
3. **自定义输入投票**：
   - 🎉 赞成
   - 😰 反对
   - ❌ 取消（管理员/主持人）
4. **确认推进**：管理员/主持人在主消息上添加：
   - 🎉 确认推进到下一轮
   - 😰 否决本轮，重新开始
   - ❌ 回退到上一轮

#### 权限说明

- **Root 用户**：拥有所有权限（通过 NcatBot 的 RBAC 系统配置）
- **群管理员/群主**：可管理本群游戏
- **游戏主持人**：可管理自己创建的游戏

### 群文件管理器

```bash
# 删除群文件根目录下所有文件
/delete_root_files
```

使用流程：
1. 输入命令后，Bot 会要求二次确认
2. 60 秒内 @Bot 并回复"确认删除"
3. Bot 将删除所有根目录文件

**注意**：
- 仅群管理员/群主可用
- Bot 需要群管理员权限才能删除文件
- 操作不可恢复，请谨慎使用

### 消息自动打包

```bash
# 查看状态
/compressor status

# 启用/禁用
/compressor enable    # 或 /compressor on
/compressor disable   # 或 /compressor off

# 设置阈值
/compressor threshold <消息数> <转发数>
# 例如：/compressor threshold 30 3
# 表示每 30 条消息打包一次，每 3 个打包记录再次打包
```

功能说明：
- **一级打包**：消息数达到阈值时，将消息打包为合并转发
- **二级打包**：打包记录达到阈值时，将多个打包记录再次打包
- **自动撤回**：Bot 为管理员时，自动撤回原始消息（保留管理员消息和 @Bot 的消息）

配置项：
- `message_threshold`：一级打包阈值（默认 33）
- `forward_threshold`：二级打包阈值（默认 3）
- 可为每个群单独配置阈值

### 状态查询

```bash
/status
```

显示 Bot 运行时长和状态信息。

## Docker 部署

### 使用 Docker Compose

1. **配置环境变量**

创建 `.env` 文件：
```env
OPENAI_API_KEY=your-api-key-here
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL_NAME=gpt-4-turbo
```

2. **启动服务**
```bash
docker-compose up -d
```

3. **查看日志**
```bash
docker-compose logs -f
```

4. **停止服务**
```bash
docker-compose down
```

### 直接使用 Docker

```bash
# 构建镜像
docker build -t aigmbot .

# 运行容器
docker run -d \
  --name aigmbot \
  -v $(pwd)/data:/app/data \
  -e OPENAI_API_KEY=your-api-key \
  aigmbot
```

## 项目结构

```
aigmbot/
├── src/
│   ├── main.py                      # Bot 入口文件
│   └── plugins/                     # 插件目录
│       ├── ai_gm/                   # AI GM 游戏插件
│       │   ├── main.py              # 插件主文件
│       │   ├── db.py                # 数据库操作
│       │   ├── game_manager.py      # 游戏逻辑管理
│       │   ├── llm_api.py           # LLM API 封装
│       │   ├── renderer.py          # Markdown 渲染
│       │   ├── event_handler.py     # 事件处理
│       │   ├── commands.py          # 命令处理
│       │   ├── cache.py             # 缓存管理
│       │   ├── content_fetcher.py   # 内容获取
│       │   └── utils.py             # 工具函数
│       ├── group_file_manager/      # 群文件管理插件
│       ├── message_compressor/      # 消息压缩插件
│       └── status/                  # 状态插件
├── data/                            # 数据目录（自动生成）
│   ├── configs/                     # 配置文件
│   └── AIGMPlugin/                  # AI GM 数据
│       ├── ai_gm.db                 # 游戏数据库
│       └── cache.json               # 投票缓存
├── requirements.txt                 # Python 依赖
├── Dockerfile                       # Docker 镜像定义
├── docker-compose.yml               # Docker Compose 配置
└── README.md                        # 本文件
```

## 开发说明

### 技术栈

- **框架**：NcatBot (基于 OneBot 协议)
- **数据库**：aiosqlite (SQLite 异步封装)
- **LLM**：OpenAI API (支持兼容的第三方服务)
- **渲染**：Playwright (浏览器自动化) + markdown-it-py
- **异步 I/O**：aiohttp, aiofiles

### AI GM 核心概念

#### 数据模型

- **Games**：游戏实例，包含系统提示词、主持人等信息
- **Branches**：分支，类似 Git 分支，指向特定的回合
- **Rounds**：回合，存储玩家选择和 AI 响应，形成树状结构

#### 状态管理

- 使用 `head_branch_id` 指向当前活跃分支
- 每个分支的 `tip_round_id` 指向最新回合
- 通过 `parent_id` 形成回合树，支持回溯和分支

#### 并发控制

- 使用 `is_frozen` 标志防止并发操作
- 使用数据库事务和 savepoint 确保数据一致性
- 推进游戏时检测 tip 变化，防止冲突

### 添加新插件

1. 在 `src/plugins/` 下创建新目录
2. 创建 `__init__.py` 和主文件
3. 继承 `NcatBotPlugin` 类
4. 实现 `on_load()` 和其他事件处理方法
5. 使用装饰器注册命令和过滤器

示例：
```python
from ncatbot.plugin_system import NcatBotPlugin, command_registry

class MyPlugin(NcatBotPlugin):
    name = "MyPlugin"
    version = "1.0.0"
    
    async def on_load(self):
        # 初始化代码
        pass
    
    @command_registry.command("hello", description="打招呼")
    async def hello_cmd(self, event):
        await event.reply("Hello!")
```

## 常见问题

### Q: Bot 无法连接 QQ
A: 检查 NcatBot 的配置文件，确保正确配置了 OneBot 协议端（如 go-cqhttp、LLOneBot）。

### Q: AI GM 插件无法启动
A: 检查 OpenAI API 密钥是否正确配置，网络是否能访问 API 端点。

### Q: Markdown 渲染失败
A: 确保已安装 Playwright 浏览器：`playwright install chromium`

### Q: 消息打包不工作
A: 检查插件是否启用（`/compressor status`），以及 Bot 是否有管理员权限（影响撤回功能）。

### Q: 游戏被冻结无法操作
A: 使用 `/aigm admin unfreeze` 命令解冻（需要群管理员或 root 权限）。

## 贡献

欢迎提交 Issue 和 Pull Request！

## 许可证

MIT License

## 致谢

- [NcatBot](https://github.com/soulter/ncatbot) - 优秀的 QQ Bot 框架
- [OneBot](https://github.com/botuniverse/onebot) - 统一的聊天机器人应用接口标准
- OpenAI - 强大的 LLM API

## 联系方式

- GitHub: [faithleysath/aigmbot](https://github.com/faithleysath/aigmbot)
- Issues: [提交问题](https://github.com/faithleysath/aigmbot/issues)

---

**注意**：本项目仅供学习交流使用，请遵守相关法律法规和平台使用条款。
