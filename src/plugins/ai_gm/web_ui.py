import asyncio
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from hypercorn.asyncio import serve
from hypercorn.config import Config
from contextlib import asynccontextmanager
from flaredantic import FlareTunnel

from ncatbot.utils import get_log

from .db import Database

LOG = get_log(__name__)

class WebUI:
    def __init__(self, db: Database, plugin_data_path: Path):
        self.db = db
        self.plugin_data_path = plugin_data_path
        self.app = FastAPI(lifespan=self.lifespan)
        self.templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
        # Tunnel 相关属性由外部（main.py）管理
        self.tunnel: FlareTunnel | None = None
        self.tunnel_url: str | None = None
        self.tunnel_ready = asyncio.Event()
        self._setup_routes()

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
        # Tunnel 的启动和关闭由插件生命周期管理（main.py）
        yield
        # Shutdown
        LOG.info("Web UI server is shutting down...")

    async def run_in_background(self):
        """Run the FastAPI app with hypercorn."""
        config = Config()
        config.bind = ["127.0.0.1:8000"]
        config.loglevel = "info"
        
        # 禁用信号处理器，因为我们在后台任务中运行
        # 信号会由主进程处理，不需要在这里注册
        config.worker_class = "asyncio"
        config.use_reloader = False
        
        LOG.info("Starting Web UI server on http://127.0.0.1:8000")
        
        try:
            await serve(self.app, config)  # type: ignore
        except RuntimeError as e:
            # 捕获并忽略在非主线程中注册信号处理器的错误
            if "set_wakeup_fd" in str(e):
                LOG.warning(
                    f"Signal handler registration failed (expected in non-main thread): {e}. "
                    f"Server shutdown will be handled by task cancellation."
                )
                # 让协程继续运行，直到被取消
                # 由于我们在 main.py 的 on_close() 中取消任务，所以这是安全的
                try:
                    await asyncio.Event().wait()  # 永久等待，直到任务被取消
                except asyncio.CancelledError:
                    LOG.info("Web UI server task was cancelled, shutting down gracefully.")
                    raise
            else:
                raise

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

    async def route_game_list(self, request: Request):
        games = await self.db.get_all_games()
        return self.templates.TemplateResponse("game_list.html", {"request": request, "games": games})

    async def route_game_detail(self, request: Request, game_id: int):
        game = await self.db.get_game_by_game_id(game_id)
        if not game:
            raise HTTPException(status_code=404, detail="Game not found")
        branches = await self.db.get_all_branches_for_game(game_id)
        tags = await self.db.get_all_tags_for_game(game_id)
        return self.templates.TemplateResponse("game_detail.html", {"request": request, "game": game, "branches": branches, "tags": tags})

    async def route_branch_history(self, request: Request, game_id: int, branch_name: str):
        branch = await self.db.get_branch_by_name(game_id, branch_name)
        if not branch or branch['tip_round_id'] is None:
            raise HTTPException(status_code=404, detail="Branch not found or empty")
        
        history = await self.db.get_round_ancestors(branch['tip_round_id'], limit=9999)
        return self.templates.TemplateResponse("branch_history.html", {"request": request, "game_id": game_id, "branch": branch, "history": history})

    async def route_round_detail(self, request: Request, game_id: int, round_id: int):
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

    async def route_graph_page(self, request: Request, game_id: int):
        return self.templates.TemplateResponse("graph.html", {"request": request, "game_id": game_id})

    async def route_graph_data(self, request: Request, game_id: int):
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
                    node["label"] += f"\\n{branch_label}"

        for tag in all_tags:
            for node in nodes:
                if node["id"] == str(tag["round_id"]):
                    node["label"] += f"\\n🏷️ {tag['name']}"
        
        return JSONResponse(content={"nodes": nodes, "edges": edges})
