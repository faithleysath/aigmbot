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

from .db import Database

LOG = get_log(__name__)

class WebUI:
    def __init__(self, db_path: str, plugin_data_path: Path):
        self.db_path = db_path
        self.db: Database | None = None
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
