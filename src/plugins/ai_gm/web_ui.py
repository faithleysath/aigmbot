import asyncio
import threading
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from contextlib import asynccontextmanager
from flaredantic import FlareTunnel

from ncatbot.utils import get_log
from markupsafe import Markup
from markdown_it import MarkdownIt

from typing import TYPE_CHECKING
from pydantic import BaseModel
from .db import Database
from .constants import MAX_SYSTEM_PROMPT_LENGTH

if TYPE_CHECKING:
    from .main import AIGMPlugin

LOG = get_log(__name__)

class SystemPromptRequest(BaseModel):
    token: str
    system_prompt: str

class WebUI:
    def __init__(self, db_path: str, plugin_data_path: Path, plugin: "AIGMPlugin | None" = None):
        self.db_path = db_path
        self.db: Database | None = None
        self.plugin = plugin
        self.plugin_data_path = plugin_data_path
        self.app = FastAPI(lifespan=self.lifespan)
        self.templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

        # 初始化 Markdown 解析器
        self.md = MarkdownIt("commonmark", {"breaks": True}).disable("html_block").disable("html_inline")

        # 注册自定义 Jinja2 过滤器
        self.templates.env.filters['nl2br'] = self._nl2br
        self.templates.env.filters['markdown'] = self._markdown_to_html

        # Tunnel 相关属性由外部（main.py）管理
        self.tunnel: FlareTunnel | None = None
        self.tunnel_url: str | None = None
        self.tunnel_ready = asyncio.Event()
        # 服务器线程
        self._server_thread: threading.Thread | None = None
        self._setup_routes()

    @staticmethod
    def _nl2br(value):
        """将换行符转换为 <br> 标签的 Jinja2 过滤器"""
        if not value:
            return value
        return Markup(str(value).replace('\n', '<br>\n'))

    def _markdown_to_html(self, value):
        """将 Markdown 转换为 HTML 的 Jinja2 过滤器"""
        if not value:
            return value
        html = self.md.render(str(value))
        return Markup(html)

    def _setup_routes(self):
        self.app.add_api_route("/", self.route_game_list, methods=["GET"], response_class=HTMLResponse)
        self.app.add_api_route("/game/start", self.route_start_game_page, methods=["GET"], response_class=HTMLResponse)
        self.app.add_api_route("/api/game/start", self.route_submit_system_prompt, methods=["POST"], response_class=JSONResponse)
        self.app.add_api_route("/game/{game_id}", self.route_game_detail, methods=["GET"], response_class=HTMLResponse)
        self.app.add_api_route("/game/{game_id}/branch/{branch_name}/history", self.route_branch_history, methods=["GET"], response_class=HTMLResponse)
        self.app.add_api_route("/game/{game_id}/round/{round_id}", self.route_round_detail, methods=["GET"], response_class=HTMLResponse)
        self.app.add_api_route("/game/{game_id}/graph", self.route_graph_page, methods=["GET"], response_class=HTMLResponse)
        self.app.add_api_route("/game/{game_id}/graph-data", self.route_graph_data, methods=["GET"], response_class=JSONResponse)

    @asynccontextmanager
    async def lifespan(self, app: FastAPI):
        # Startup
        LOG.info("Web UI server is starting up...")
        self.db = Database(self.db_path)
        await self.db.connect()
        
        # Tunnel 的启动和关闭由插件生命周期管理（main.py）
        yield
        # Shutdown
        LOG.info("Web UI server is shutting down...")
        if self.db:
            await self.db.close()

    def start_server(self):
        """在独立线程中启动 Web UI 服务器"""
        def run_server():
            LOG.info("Starting Web UI server on http://127.0.0.1:8000")
            
            # 在新线程中创建新的事件循环
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            from hypercorn.asyncio import serve
            from hypercorn.config import Config
            
            config = Config()
            config.bind = ["127.0.0.1:8000"]
            config.loglevel = "info"
            
            # 创建一个关闭事件，避免在非主线程中注册信号处理器
            shutdown_event = asyncio.Event()
            
            async def serve_with_trigger():
                await serve(self.app, config, shutdown_trigger=shutdown_event.wait) # type: ignore
            
            try:
                loop.run_until_complete(serve_with_trigger())
            except Exception as e:
                LOG.error(f"Web UI server error: {e}", exc_info=True)
            finally:
                loop.close()
        
        self._server_thread = threading.Thread(target=run_server, daemon=True, name="WebUI-Server")
        self._server_thread.start()
        LOG.info("Web UI server thread started")

    def stop_server(self):
        """停止 Web UI 服务器"""
        if self._server_thread and self._server_thread.is_alive():
            LOG.info("Web UI server will be cleaned up automatically (daemon thread)")
            # daemon 线程会在主进程退出时自动清理

    async def wait_for_tunnel(self, timeout: float = 10.0) -> bool:
        """
        等待 tunnel 启动完成。
        
        Args:
            timeout: 超时时间（秒）
            
        Returns:
            bool: 如果 tunnel 成功启动返回 True，否则返回 False
        """
        try:
            await asyncio.wait_for(self.tunnel_ready.wait(), timeout=timeout)
            return self.tunnel_url is not None
        except asyncio.TimeoutError:
            LOG.warning("Tunnel startup timed out")
            return False

    async def refresh_tunnel(self) -> bool:
        """
        重新刷新 Cloudflare tunnel。
        
        Returns:
            bool: 刷新成功返回 True，失败返回 False
        """
        LOG.info("开始刷新 Cloudflare tunnel...")
        
        # 1. 停止旧 tunnel
        if self.tunnel:
            try:
                self.tunnel.stop()
                LOG.info("已停止旧 tunnel")
            except Exception as e:
                LOG.warning(f"停止旧 tunnel 时出错: {e}")
            finally:
                self.tunnel = None
                self.tunnel_url = None
        
        # 2. 重置状态
        self.tunnel_ready.clear()
        
        # 3. 重新创建并启动 tunnel
        try:
            from flaredantic import FlareTunnel, FlareConfig
            config = FlareConfig(
                port=8000,
                bin_dir=self.plugin_data_path / "bin",
                timeout=60,
                verbose=True
            )
            self.tunnel = FlareTunnel(config)
            await asyncio.to_thread(self.tunnel.start)
            self.tunnel_url = self.tunnel.tunnel_url
            
            if self.tunnel_url:
                LOG.info(f"✅ Tunnel 刷新成功: {self.tunnel_url}")
                return True
            else:
                LOG.error("⚠️ Tunnel 启动但 URL 不可用")
                return False
                
        except Exception as e:
            LOG.error(f"❌ 刷新 tunnel 失败: {e}", exc_info=True)
            self.tunnel_url = None
            return False
        finally:
            self.tunnel_ready.set()

    async def route_game_list(self, request: Request):
        if not self.db:
            raise HTTPException(status_code=503, detail="Database not initialized")
        try:
            games = await self.db.get_all_games()
            return self.templates.TemplateResponse("game_list.html", {"request": request, "games": games})
        except Exception as e:
            LOG.error(f"Error fetching game list: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    async def route_game_detail(self, request: Request, game_id: int):
        if not self.db:
            raise HTTPException(status_code=503, detail="Database not initialized")
        try:
            game = await self.db.get_game_by_game_id(game_id)
            if not game:
                raise HTTPException(status_code=404, detail="Game not found")
            branches = await self.db.get_all_branches_for_game(game_id)
            tags = await self.db.get_all_tags_for_game(game_id)
            return self.templates.TemplateResponse("game_detail.html", {"request": request, "game": game, "branches": branches, "tags": tags})
        except HTTPException:
            raise
        except Exception as e:
            LOG.error(f"Error fetching game details {game_id}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    async def route_branch_history(self, request: Request, game_id: int, branch_name: str):
        if not self.db:
            raise HTTPException(status_code=503, detail="Database not initialized")
        try:
            branch = await self.db.get_branch_by_name(game_id, branch_name)
            if not branch or branch['tip_round_id'] is None:
                raise HTTPException(status_code=404, detail="Branch not found or empty")
            
            history = await self.db.get_round_ancestors(branch['tip_round_id'], limit=9999)
            return self.templates.TemplateResponse("branch_history.html", {"request": request, "game_id": game_id, "branch": branch, "history": history})
        except HTTPException:
            raise
        except Exception as e:
            LOG.error(f"Error fetching branch history {branch_name}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    async def route_round_detail(self, request: Request, game_id: int, round_id: int):
        if not self.db:
            raise HTTPException(status_code=503, detail="Database not initialized")
        try:
            round_info = await self.db.get_round_info(round_id)
            if not round_info:
                raise HTTPException(status_code=404, detail="Round not found")
            
            # Find next and previous rounds
            parent_id = round_info['parent_id']
            children = await self.db.get_child_rounds(round_id)
            next_round_id = children[0]['round_id'] if children else None

            return self.templates.TemplateResponse("round_detail.html", {
                "request": request, 
                "game_id": game_id,
                "round": round_info,
                "prev_round_id": parent_id if parent_id != -1 else None,
                "next_round_id": next_round_id
            })
        except HTTPException:
            raise
        except Exception as e:
            LOG.error(f"Error fetching round details {round_id}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    async def route_graph_page(self, request: Request, game_id: int):
        return self.templates.TemplateResponse("graph.html", {"request": request, "game_id": game_id})

    async def route_graph_data(self, request: Request, game_id: int):
        if not self.db:
            raise HTTPException(status_code=530, detail="Database not initialized")
        try:
            game = await self.db.get_game_by_game_id(game_id)
            if not game:
                raise HTTPException(status_code=404, detail="Game not found")

            all_rounds = await self.db.get_all_rounds_for_game(game_id)
            all_branches = await self.db.get_all_branches_for_game(game_id)
            all_tags = await self.db.get_all_tags_for_game(game_id)
            head_branch_id = game["head_branch_id"]

            nodes = []
            edges = []

            for r in all_rounds:
                round_id = r["round_id"]
                label = f"Round {round_id}"
                nodes.append({"id": str(round_id), "label": label})
                if r["parent_id"] != -1:
                    edges.append({"from": str(r["parent_id"]), "to": str(round_id)})

            # Add branch and tag info to nodes
            for branch in all_branches:
                for node in nodes:
                    if node["id"] == str(branch["tip_round_id"]):
                        is_head = branch['branch_id'] == head_branch_id
                        branch_label = f"🌿 {branch['name']}" + (" (HEAD)" if is_head else "")
                        node["label"] += f"\n{branch_label}"

            for tag in all_tags:
                for node in nodes:
                    if node["id"] == str(tag["round_id"]):
                        node["label"] += f"\n🏷️ {tag['name']}"
            
            return JSONResponse(content={"nodes": nodes, "edges": edges})
        except HTTPException:
            raise
        except Exception as e:
            LOG.error(f"Error fetching graph data for game {game_id}: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Internal server error")

    async def route_start_game_page(self, request: Request, token: str):
        """渲染启动新游戏页面，验证并消费 token"""
        if not self.plugin or not self.plugin.cache_manager:
            raise HTTPException(status_code=503, detail="Plugin not initialized")

        # 消费 token，防止重复使用
        token_data = await self.plugin.cache_manager.consume_web_start_token(token)
        if not token_data:
            return HTMLResponse(content="<h1>链接已失效或已被使用</h1><p>请在群聊中重新使用 /aigm start 命令获取新链接。</p>", status_code=403)
        
        # 将必要信息嵌入页面，但不直接暴露在 URL 中
        # 注意：这里我们只传递 token 给前端作为一种简单的会话标识（尽管它已经从后端缓存中移除），
        # 实际提交时我们需要一种方式来验证身份。
        # 由于 consume_web_start_token 已经移除了 token，我们需要生成一个新的临时凭证或者
        # 直接在渲染页面时将 group_id 和 user_id 嵌入到表单中（加密或签名会更安全，但这里简化处理，
        # 假设短时间内不会被篡改，且主要依赖一次性链接的安全性）。
        # 
        # 为了安全性，我们在 consume 后生成一个短期有效的 submit_token 存入内存，
        # 或者简单地：由于是前后端分离的 API 调用，我们需要在服务端保持这个状态。
        # 
        # 修正方案：
        # 1. route_start_game_page 消费 URL token。
        # 2. 生成一个新的、仅用于提交的 session token (submit_token)，存入 cache。
        # 3. 将 submit_token 传给前端。
        # 4. 前端提交时带上 submit_token。
        
        # 生成提交专用的临时 token (有效期较短，例如 30 分钟，足够填完表单)
        import secrets
        submit_token = secrets.token_urlsafe(32)
        await self.plugin.cache_manager.add_web_start_token(submit_token, token_data["group_id"], token_data["user_id"])
        
        return self.templates.TemplateResponse("start_game.html", {
            "request": request, 
            "token": submit_token,
            "max_length": MAX_SYSTEM_PROMPT_LENGTH
        })

    async def route_submit_system_prompt(self, request: SystemPromptRequest):
        """处理 Web 端提交的剧本"""
        # 输入验证
        if not request.system_prompt or not request.system_prompt.strip():
            raise HTTPException(status_code=400, detail="剧本内容不能为空")
        
        if len(request.system_prompt) > MAX_SYSTEM_PROMPT_LENGTH:
            raise HTTPException(
                status_code=400,
                detail=f"剧本内容过长 (最大 {MAX_SYSTEM_PROMPT_LENGTH} 字符)",
            )

        if not self.plugin or not self.plugin.cache_manager or not self.plugin.event_handler or not self.db:
            raise HTTPException(status_code=503, detail="系统服务未完全初始化")

        # 消费 token (立即消费以防止竞态条件)
        token_data = await self.plugin.cache_manager.consume_web_start_token(request.token)
        if not token_data:
            raise HTTPException(status_code=403, detail="会话已过期或提交令牌无效，请重新获取链接")

        group_id = token_data.get("group_id")
        user_id = token_data.get("user_id")

        if not group_id or not user_id:
            LOG.error(f"Invalid token data: {token_data}")
            raise HTTPException(status_code=500, detail="Invalid session data")
        
        # 业务逻辑检查：检查群组是否已有游戏运行
        if await self.db.is_game_running(group_id):
            # 虽然 Token 已消费，但业务规则阻止了操作。
            # 用户需要重新生成链接，这是为了安全性的权衡。
            raise HTTPException(status_code=409, detail="当前群组已有正在进行的游戏，无法启动新游戏")

        # 调用 EventHandler 处理剧本
        try:
            success, error_msg = await self.plugin.event_handler.process_system_prompt(
                group_id, 
                user_id, 
                request.system_prompt
            )

            if success:
                return JSONResponse(content={"status": "success"})
            else:
                raise HTTPException(status_code=500, detail=f"处理剧本失败: {error_msg}")
        except Exception as e:
            LOG.error(f"Error processing system prompt via WebUI: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"服务器内部错误: {str(e)}")
