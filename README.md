# 📖 AI GM - 互动叙事游戏插件

**AI GM** 是一个为 `ncatbot` 设计的、富有创造力的插件，它将大型语言模型（LLM）的叙事能力与 Git 的版本控制思想相结合，为您和您的朋友带来前所未有的互动式文字冒险体验。

在这个插件中，每一次游戏都是一个故事库。您可以像管理代码一样，通过创建分支、合并想法、标记关键节点，来探索故事的无限可能性。

[![演示](https://img.shields.io/badge/功能-演示-blueviolet)](https://your-demo-link.com)
[![许可证](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![QQ群](https://img.shields.io/badge/QQ群-123456789-blue)](https://your-qq-group-link)

---

## ✨ 核心特性

- **🤖 AI 驱动的叙事**：利用强大的 LLM（如 GPT-4、Claude）作为您的专属游戏主持人（Game Master），动态生成丰富、连贯且充满想象力的故事情节。
- **🔐 用户级 LLM 预设管理**：每个用户可以添加自己的 API 预设，灵活贡献算力。支持临时绑定和保底机制。
- **🌿 Git 式故事管理**：
    - **分支 (Branch)**：对故事的任何节点创建"平行宇宙"，探索不同的选择，而不影响主线剧情。
    - **检出 (Checkout)**：在不同的故事线之间自由切换，体验每一种可能性。
    - **历史 (History)**：轻松回顾任何分支的完整演变过程。
    - **重置 (Reset)**：不满意当前进展？一键将故事回滚到任意历史节点。
    - **标签 (Tag)**：为故事中的里程碑事件或精彩瞬间打上永久标记，方便随时回顾。
- **🎨 可视化分支图**：自动生成精美的分支图，让故事的脉络和所有可能性一目了然。
- **🗳️ 社区驱动决策**：通过表情符号投票，让所有玩家共同决定故事的走向。支持预设选项和玩家自定义输入。
- **🌐 Web UI 界面**：提供一个现代化的 Web 界面，让您可以在浏览器中直观地浏览游戏历史、查看分支详情和回合内容。
- **🔐 灵活的权限管理**：支持多层级权限控制（Root、群管理员、游戏主持人），确保游戏管理井然有序。
- **🖼️ 高质量内容渲染**：将 Markdown 格式的故事情节渲染成精美的图片，提供沉浸式的阅读体验。

## 🚀 快速开始

### 1. 准备工作

- **环境**：确保您的系统已安装 `Python 3.10+`。
- **依赖**：安装所有必要的 Python 库：
  ```bash
  pip install -r requirements.txt
  ```
- **浏览器引擎**：本插件使用 `Playwright` 渲染故事内容，请安装必要的浏览器驱动：
  ```bash
  playwright install
  ```
- **Graphviz**：为了生成分支图，您需要在系统中安装 `Graphviz`。
  - **macOS**: `brew install graphviz`
  - **Ubuntu/Debian**: `sudo apt-get install graphviz`
  - **Windows**: 可从官网下载或使用 `choco install graphviz`。

### 2. 配置 LLM 预设（v1.1.0+）

**从 v1.1.0 开始，LLM 配置改为用户级管理。** 不再需要在全局配置文件中设置 API 密钥。

#### 添加您的 LLM 预设

1. **私聊机器人**添加预设：

```
/aigm llm add <预设名称> <模型> <API地址> <API密钥>
```

**示例：**

```
/aigm llm add gpt4 gpt-4-turbo https://api.openai.com/v1 sk-xxxxxxxxxxxxxxxx
/aigm llm add claude claude-3-5-sonnet-20241022 https://api.anthropic.com/v1 sk-ant-xxxxxxxx
```

2. **在群聊中绑定预设**贡献算力：

```
/aigm llm bind <预设名称> [时长]
```

**示例：**

```
/aigm llm bind gpt4           # 永久绑定
/aigm llm bind gpt4 30d       # 绑定 30 天
/aigm llm bind gpt4 12h       # 绑定 12 小时
```

#### 管理员设置保底预设（可选）

管理员可以设置保底 LLM 预设，确保即使没有人绑定也能运行游戏：

```
/aigm llm set-fallback <用户ID> <预设名称>
```

**查看更多：** 详细的配置和升级指南请参考 [UPGRADE_GUIDE.md](./UPGRADE_GUIDE.md)

### 3. 开始第一场游戏

**方式一：Web UI 启动（推荐）**
1.  **获取链接**：在群聊中发送 `/aigm start`，机器人会回复一个专属的网页链接。
2.  **编写剧本**：点击链接进入 Web 界面，在舒适的编辑器中编写您的故事背景、规则和世界观。支持长文本和格式预览。
3.  **提交开启**：点击提交按钮，机器人会在群内发送预览消息。点击 🎉 表情确认后即可开启游戏！

**方式二：文件上传启动**
1.  **编写文件**：创建一个 `.txt` 或 `.md` 文件，写入剧本内容。
2.  **上传文件**：将该文件直接发送到您希望进行游戏的 QQ 群中。
3.  **确认开启**：在预览消息下方点击 🎉 表情确认。

## 🎮 如何游玩

游戏开始后，您和朋友们可以通过指令和表情互动来推进故事。

### 主要指令

所有指令都以 `/aigm` 开头。

- `/aigm help` - 显示完整的帮助信息。
- `/aigm start [剧本]` - 启动新游戏（不带参数可获取 Web UI 链接）。
- `/aigm status` - 查看当前游戏的基本状态。
- `/aigm webui` - 获取当前游戏的 Web UI 访问地址。

#### LLM 配置管理 (`/aigm llm ...`)

**私聊命令：**
- `add <name> <model> <base_url> <api_key>` - 添加新的 LLM 预设
- `remove <name>` - 删除预设（正在使用的预设无法删除）
- `test <name>` - 测试预设的连接性
- `list` 或 `status` - 查看您的所有预设

**群聊命令：**
- `status` - 查看当前群的 LLM 绑定状态
- `bind <name> [duration]` - 绑定您的预设贡献算力
- `unbind` - 解除您的绑定
- `set-fallback <name>` - [管理员] 设置保底预设
- `clear-fallback` - [管理员] 清除保底预设

#### 分支管理 (`/aigm branch ...`)

- `list [all]` - 以图片形式显示故事分支图（`all` 显示完整图）。
- `show <name>` - 查看指定分支的最新内容。
- `create <name> [from_round_id]` - 创建一个新的故事分支。
- `rename <old> <new>` - 重命名一个分支。
- `delete <name>` - 删除一个分支（不能删除 HEAD 分支）。
- `history [name]` - 查看指定分支或当前分支的历史记录。

#### 游戏流程控制

- `/aigm checkout <branch_name>` - 切换到另一个故事分支。
- `/aigm checkout head` - 重新加载并显示当前分支的最新状态。
- `/aigm reset <round_id>` - 将当前分支的故事线回滚到指定的回合。

#### 标签与历史 (`/aigm tag ...`, `/aigm round ...`)

- `tag list` - 列出所有已创建的标签。
- `tag create <name> [round_id]` - 为某个回合创建一个永久标签。
- `round show <id>` - 查看特定 ID 回合的详细内容。

#### 管理员命令 (`/aigm admin ...`)

- `admin unfreeze` - [群管理/ROOT] 强制解冻当前游戏。
- `admin refresh-tunnel` - [ROOT] 重新刷新 Cloudflare tunnel，用于 Web UI 访问异常时恢复连接。
- `admin delete <id>` - [ROOT] 删除指定 ID 的游戏。

### 投票机制

- **预设选项**：机器人发送的故事消息下方会有一系列字母表情（🅰️, 🅱️, ...），点击即可为对应选项投票。
- **自定义输入**：回复机器人发送的故事主消息，并 `@` 机器人，您的回复将成为一个新的候选选项。其他玩家可以通过 🎉 (赞成) 和 😰 (反对) 来为您的提议投票。
- **确认推进**：当投票结束后，游戏主持人（或管理员）可以点击主消息下方的 🎉 表情来统计票数并推进故事进入下一回合。

## 🔐 安全特性

### API 密钥加密存储

- 所有用户添加的 API 密钥都使用 **Fernet (AES-128-CBC + HMAC-SHA256)** 加密存储
- 加密密钥自动生成并保存在 `data/.secret.key`（权限 600）
- 支持解密失败检测和友好错误提示

### 数据保护

- 使用原子写入防止数据损坏
- 并发访问控制（asyncio.Lock）
- 自动备份建议和恢复流程

### 权限管理

- 用户只能管理自己的预设
- 预设删除前检查使用情况
- 管理员权限分层（Root、群管理、游戏主持人）

**⚠️ 重要提醒：** `data/.secret.key` 文件极其重要！丢失后所有 API 密钥将永久无法解密。请定期备份！

详细的安全指南和备份策略请参考 [UPGRADE_GUIDE.md](./UPGRADE_GUIDE.md#-密钥管理指南)

## 🏛️ 架构概览

本插件采用模块化设计，各组件职责分明：

- `main.py`: 插件入口，负责加载、配置和注册命令。
- `game_manager.py`: 核心游戏逻辑控制器，处理游戏流程的推进、分支切换等。
- `db.py`: 基于 `aiosqlite` 的异步数据库模块，负责持久化存储所有游戏数据。
- `llm_api.py`: 封装了与 OpenAI API 的交互，包含连接池、重试和错误处理机制。
- `llm_config.py`: **[v1.1.0新增]** 用户级 LLM 预设管理，支持加密存储和灵活绑定。
- `commands.py`: 定义所有面向用户的斜杠指令 (`/aigm`)。
- `event_handler.py`: 处理非指令的事件，如文件上传、表情投票、消息撤回等。
- `renderer.py`: 使用 `Playwright` 将 Markdown 渲染为高质量的图片。
- `visualizer.py`: 使用 `Graphviz` 将游戏的分支结构可视化。
- `cache.py`: 内存缓存，用于暂存待处理的游戏和投票数据，并通过延迟写入优化磁盘 I/O。
- `web_ui.py`: 基于 `FastAPI` 和 `Jinja2` 的 Web 服务，提供外部访问界面。

## 📊 性能优化

### 连接池管理

- LRU 策略的 OpenAI 客户端连接池
- 空闲连接自动清理（默认 1 小时超时）
- 可配置的池大小限制（默认 20）

### 数据库优化

- 使用递归 CTE 批量查询历史回合
- 异步事务和乐观锁
- 自动清理过期绑定

## 🔄 版本历史

### v1.1.0 (2025-11-23) - 重大架构升级

**新特性：**
- ✨ 用户级 LLM 预设管理系统
- 🔐 API 密钥加密存储（Fernet）
- ⚡ OpenAI 客户端连接池（LRU + 空闲清理）
- 🎯 灵活的算力绑定机制（FCFS + 临时绑定 + Fallback）

**安全改进：**
- 🔒 密钥文件权限保护（600）
- 💾 原子写入防止数据损坏
- 🛡️ 解密错误主动检测

**破坏性变更：**
- ❌ 移除全局 `openai_api_key` 等配置项
- 📦 新增 `cryptography` 依赖

**升级指南：** 请参考 [UPGRADE_GUIDE.md](./UPGRADE_GUIDE.md)

### v1.0.0 - 初始发布

- 基于 Git 概念的故事管理
- 表情投票系统
- Web UI 界面
- 分支可视化

## 🤝 贡献

欢迎您为这个项目做出贡献！无论是提交 Bug 报告、提出功能建议还是直接贡献代码，我们都非常欢迎。

1.  Fork 本仓库
2.  创建您的特性分支 (`git checkout -b feature/AmazingFeature`)
3.  提交您的更改 (`git commit -m 'Add some AmazingFeature'`)
4.  推送到分支 (`git push origin feature/AmazingFeature`)
5.  开启一个 Pull Request

## 📞 获取帮助

- 📖 [升级指南](./UPGRADE_GUIDE.md)
- 🐛 [报告问题](https://github.com/your-repo/issues)
- 💬 [QQ 群](https://your-qq-group-link)

## 📜 许可证

本项目采用 [MIT 许可证](./LICENSE)授权。

---
