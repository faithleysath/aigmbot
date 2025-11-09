# 📖 AI GM - 互动叙事游戏插件

**AI GM** 是一个为 `ncatbot` 设计的、富有创造力的插件，它将大型语言模型（LLM）的叙事能力与 Git 的版本控制思想相结合，为您和您的朋友带来前所未有的互动式文字冒险体验。

在这个插件中，每一次游戏都是一个故事库。您可以像管理代码一样，通过创建分支、合并想法、标记关键节点，来探索故事的无限可能性。

[![演示](https://img.shields.io/badge/功能-演示-blueviolet)](https://your-demo-link.com)
[![许可证](https://img.shields.io/badge/license-MIT-green)](./LICENSE)
[![QQ群](https://img.shields.io/badge/QQ群-123456789-blue)](https://your-qq-group-link)

---

## ✨ 核心特性

- **🤖 AI 驱动的叙事**：利用强大的 LLM（如 GPT-4）作为您的专属游戏主持人（Game Master），动态生成丰富、连贯且充满想象力的故事情节。
- **🌿 Git 式故事管理**：
    - **分支 (Branch)**：对故事的任何节点创建“平行宇宙”，探索不同的选择，而不影响主线剧情。
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

### 2. 配置插件

在 `ncatbot` 的配置文件中，找到 `AIGMPlugin` 的配置项，并填入您的 OpenAI API 信息：

```yaml
AIGMPlugin:
  openai_api_key: "sk-..."
  openai_base_url: "https://api.openai.com/v1"
  openai_model_name: "gpt-4-turbo"
```

### 3. 开始第一场游戏

1.  **编写剧本**：创建一个 `.txt` 或 `.md` 文件，写入您的故事背景、规则和世界观。这就是游戏的“系统提示词”。
2.  **上传文件**：将该文件直接发送到您希望进行游戏的 QQ 群中。
3.  **确认开启**：插件机器人会发送一条预览消息。点击消息下方的 🎉 表情符号，即可正式开启游戏！

## 🎮 如何游玩

游戏开始后，您和朋友们可以通过指令和表情互动来推进故事。

### 主要指令

所有指令都以 `/aigm` 开头。

- `/aigm help` - 显示完整的帮助信息。
- `/aigm status` - 查看当前游戏的基本状态。
- `/aigm webui` - 获取当前游戏的 Web UI 访问地址。

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

### 投票机制

- **预设选项**：机器人发送的故事消息下方会有一系列字母表情（🅰️, 🅱️, ...），点击即可为对应选项投票。
- **自定义输入**：回复机器人发送的故事主消息，并 `@` 机器人，您的回复将成为一个新的候选选项。其他玩家可以通过 🎉 (赞成) 和 😰 (反对) 来为您的提议投票。
- **确认推进**：当投票结束后，游戏主持人（或管理员）可以点击主消息下方的 🎉 表情来统计票数并推进故事进入下一回合。

## 🏛️ 架构概览

本插件采用模块化设计，各组件职责分明：

- `main.py`: 插件入口，负责加载、配置和注册命令。
- `game_manager.py`: 核心游戏逻辑控制器，处理游戏流程的推进、分支切换等。
- `db.py`: 基于 `aiosqlite` 的异步数据库模块，负责持久化存储所有游戏数据。
- `llm_api.py`: 封装了与 OpenAI API 的交互，包含重试和错误处理机制。
- `commands.py`: 定义所有面向用户的斜杠指令 (`/aigm`)。
- `event_handler.py`: 处理非指令的事件，如文件上传、表情投票、消息撤回等。
- `renderer.py`: 使用 `Playwright` 将 Markdown 渲染为高质量的图片。
- `visualizer.py`: 使用 `Graphviz` 将游戏的分支结构可视化。
- `cache.py`: 内存缓存，用于暂存待处理的游戏和投票数据，并通过延迟写入优化磁盘 I/O。
- `web_ui.py`: 基于 `FastAPI` 和 `Jinja2` 的 Web 服务，提供外部访问界面。

##🤝 贡献

欢迎您为这个项目做出贡献！无论是提交 Bug 报告、提出功能建议还是直接贡献代码，我们都非常欢迎。

1.  Fork 本仓库
2.  创建您的特性分支 (`git checkout -b feature/AmazingFeature`)
3.  提交您的更改 (`git commit -m 'Add some AmazingFeature'`)
4.  推送到分支 (`git push origin feature/AmazingFeature`)
5.  开启一个 Pull Request

## 📜 许可证

本项目采用 [MIT 许可证](./LICENSE)授权。
