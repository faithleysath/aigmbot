# AigmBot - NcatBot 插件集合

这是一个基于 NcatBot 框架的 QQ 群聊机器人插件集合，包含三个主要插件：AI GM 游戏、群文件管理器和消息压缩器。

## 📦 插件列表

### 1. AI GM Plugin - AI 主持的互动叙事游戏

一个创新的互动叙事游戏插件，结合了 AI 主持人和 Git 风格的版本控制概念，允许玩家通过投票决定剧情走向，并支持分支、标签等高级功能。

#### 核心特性

- **AI 驱动的剧情生成**: 使用 OpenAI API 生成动态剧情内容
- **Git 风格的版本控制**: 
  - 支持分支（Branches）管理，可创建平行世界线
  - 支持标签（Tags）标记重要剧情节点
  - 支持回合（Rounds）的历史追溯和回退
- **投票系统**: 玩家通过表情投票决定剧情走向
- **自定义输入**: 支持玩家提交自定义选项并投票
- **可视化**: 自动生成分支图展示剧情树
- **Markdown 渲染**: 将剧情内容渲染为精美图片

#### 快速开始

1. **启动游戏**: 上传 `.txt` 或 `.md` 文件作为系统提示词（游戏背景设定）
2. **投票**: 对 GM 生成的选项使用表情投票（🍎 A、⚙️ B、🍇 C 等）
3. **自定义输入**: 回复主消息并 @机器人 提交自定义选项
4. **确认推进**: 主持人/管理员使用 🎉 表情确认投票结果并推进剧情

#### 主要命令

```
/aigm help                              # 显示帮助信息
/aigm status                            # 查看当前游戏状态

# 游戏管理
/aigm game list                         # 列出所有游戏
/aigm game attach <id>                  # 将游戏附加到当前频道
/aigm game detach                       # 从当前频道分离游戏
/aigm game sethost @user                # 变更游戏主持人

# 分支操作
/aigm branch list [all]                 # 显示分支图（all: 完整图）
/aigm branch show <name>                # 查看分支顶端内容
/aigm branch history [name] [limit=N]   # 查看分支历史记录
/aigm branch create <name> [round_id]   # 创建新分支
/aigm branch rename <old> <new>         # 重命名分支
/aigm branch delete <name>              # 删除分支

# 标签操作
/aigm tag list                          # 列出所有标签
/aigm tag show <name>                   # 查看标签指向的回合
/aigm tag create <name> [round_id]      # 创建标签
/aigm tag delete <name>                 # 删除标签

# 历史操作
/aigm checkout <branch>                 # 切换到指定分支
/aigm checkout head                     # 重新加载最新状态
/aigm reset <round_id>                  # 重置到指定回合
/aigm round show <id>                   # 查看指定回合内容
/aigm round history <id> [limit=N]      # 查看回合历史

# 管理员命令
/aigm admin unfreeze                    # 强制解冻游戏
/aigm admin delete <id>                 # [ROOT] 删除游戏
```

#### 配置项

在插件配置文件中需要设置以下项：

```python
openai_api_key = "YOUR_API_KEY_HERE"           # OpenAI API Key
openai_base_url = "https://api.openai.com/v1" # API 基础 URL
openai_model_name = "gpt-4-turbo"              # 使用的模型
openai_max_retries = 2                         # 最大重试次数
openai_base_delay = 1.0                        # 基础重试延迟（秒）
openai_max_delay = 30.0                        # 最大重试延迟（秒）
openai_timeout = 60.0                          # API 超时时间（秒）
pending_game_timeout = 300                     # 新游戏确认超时（秒）
```

#### 权限系统

- **群管理员/群主**: 可以管理本群的游戏
- **游戏主持人**: 可以管理自己主持的游戏
- **Root 用户**: 拥有所有权限

#### 数据存储

- 数据库: SQLite (`data/AIGMPlugin/ai_gm.db`)
- 缓存: JSON (`data/AIGMPlugin/cache.json`)

#### 依赖项

- `openai`: LLM API 调用
- `aiosqlite`: 异步数据库操作
- `markdown-it-py`: Markdown 解析
- `playwright`: 渲染引擎（需运行 `playwright install`）
- `graphviz`: 分支图生成
- `aiofiles`: 异步文件操作

---

### 2. Group File Manager - 群文件管理器

一个简单但实用的群文件管理插件，用于批量清理群文件根目录。

#### 功能特性

- **批量删除**: 一键删除群文件根目录下的所有文件
- **安全机制**: 需要二次确认，防止误操作
- **权限控制**: 仅群管理员和群主可使用

#### 使用方法

```
/delete_root_files
```

执行后会提示二次确认，在 60 秒内 @机器人 并回复 `确认删除` 即可执行删除。

#### 注意事项

- 机器人需要是群管理员才能删除文件
- 删除操作不可恢复，请谨慎使用
- 只删除根目录文件，不影响子文件夹

---

### 3. Message Compressor - 消息压缩器

一个全自动的消息打包插件，可以将大量群聊消息自动合并转发，保持聊天记录整洁。

#### 核心特性

- **两级打包**: 
  - 一级：将多条消息合并为一个转发消息
  - 二级：将多个转发消息再次嵌套合并
- **自动撤回**: 如果机器人是管理员，会自动撤回原始消息
- **智能过滤**: 不会打包命令消息、转发消息、文件消息和 @机器人的消息
- **灵活配置**: 支持全局和群组级别的阈值设置

#### 主要命令

```
/compressor enable              # 启用自动打包（本群）
/compressor disable             # 禁用自动打包（本群）
/compressor threshold <消息数> <转发数>  # 设置打包阈值
/compressor status              # 查看当前状态和配置
```

#### 配置说明

- **消息阈值**: 积累多少条消息后触发一级打包（默认 33 条）
- **转发阈值**: 积累多少个一级打包后触发二级打包（默认 3 个）
- **全局配置**: 在插件配置文件中设置默认值
- **群组配置**: 每个群可以有独立的阈值设置

#### 配置限制

- 消息数阈值 ≥ 2
- 转发数阈值 ≥ 2
- 两个阈值的乘积 ≤ 100

#### 状态显示示例

```
--- 本群自动打包状态 ---
功能状态: ✅ 已启用
撤回权限: ✅ 可用
一级阈值: 33 条消息 (全局)
二级阈值: 3 条打包记录
当前缓存: 15 条消息 | 1 条打包记录
--------------------------
```

#### 注意事项

- 只有群管理员可以修改设置
- 机器人需要是管理员才能撤回消息
- 不会打包群管理员、机器人自己或 @机器人的消息

---

## 🚀 部署指南

### 前置要求

- Python 3.10+
- NcatBot 框架
- Docker（可选）

### 本地部署

1. **克隆仓库**
   ```bash
   git clone https://github.com/faithleysath/aigmbot.git
   cd aigmbot
   ```

2. **安装依赖**
   ```bash
   pip install -r requirements.txt
   playwright install  # 仅 AI GM 插件需要
   ```

3. **配置插件**
   
   编辑 NcatBot 的配置文件，添加或修改插件配置。

4. **运行机器人**
   ```bash
   python src/main.py
   ```

### Docker 部署

```bash
docker-compose up -d
```

查看部署说明：[deploy.md](deploy.md)

---

## 📋 系统要求

### AI GM Plugin
- OpenAI API Key 或兼容的 API
- Chromium 浏览器（通过 Playwright）
- Graphviz

### Group File Manager
- 无特殊要求

### Message Compressor
- 无特殊要求

---

## 🛠️ 开发

### 项目结构

```
src/plugins/
├── ai_gm/                    # AI GM 游戏插件
│   ├── __init__.py
│   ├── main.py               # 插件入口
│   ├── db.py                 # 数据库操作
│   ├── game_manager.py       # 游戏逻辑管理
│   ├── llm_api.py            # LLM API 封装
│   ├── renderer.py           # Markdown 渲染
│   ├── visualizer.py         # 分支图可视化
│   ├── event_handler.py      # 事件处理
│   ├── commands.py           # 命令处理
│   ├── cache.py              # 缓存管理
│   ├── content_fetcher.py    # 内容获取
│   ├── constants.py          # 常量定义
│   ├── exceptions.py         # 异常定义
│   └── utils.py              # 工具函数
│
├── group_file_manager/       # 群文件管理器
│   ├── __init__.py
│   └── main.py
│
└── message_compressor/       # 消息压缩器
    ├── __init__.py
    └── compressor.py
```

### 技术栈

- **异步编程**: asyncio, aiofiles, aiosqlite
- **数据库**: SQLite with WAL mode
- **LLM**: OpenAI API
- **渲染**: Playwright, markdown-it-py
- **可视化**: Graphviz
- **框架**: NcatBot

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

---

## 📄 许可证

本项目采用 MIT 许可证。

---

## 👥 作者

- **Cline** - 初始开发

---

## 📞 联系方式

如有问题或建议，请通过 GitHub Issues 联系。

---

## 🔄 更新日志

### v1.0.0 (2025-01-09)
- ✨ AI GM Plugin: 完整的游戏系统实现
- ✨ Group File Manager: 批量文件删除功能
- ✨ Message Compressor: 两级消息打包系统
