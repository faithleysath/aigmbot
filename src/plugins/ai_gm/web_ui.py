import asyncio
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
import uvicorn
from contextlib import asynccontextmanager

from ncatbot.utils import get_log

from .db import Database
from flaredantic import FlareTunnel, FlareConfig, CloudflaredError

LOG = get_log(__name__)

class WebUI:
    def __init__(self, db: Database, plugin_data_path: Path):
        self.db = db
        self.plugin_data_path = plugin_data_path
        self.app = FastAPI(lifespan=self.lifespan)
        self.templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
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
        LOG.info("Web UI is starting up...")
        try:
            LOG.info("Configuring Flare tunnel...")
            config = FlareConfig(
                port=8000,
                bin_dir=self.plugin_data_path / "bin",
                timeout=60,  # 增加超时时间，首次启动需要下载 cloudflared
                verbose=True
            )
            self.tunnel = FlareTunnel(config)
            
            # 在线程池中启动 tunnel，避免阻塞事件循环
            LOG.info("Starting Flare tunnel (this may take a while on first run)...")
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self.tunnel.start)
            
            self.tunnel_url = self.tunnel.tunnel_url
            if self.tunnel_url:
                LOG.info(f"✅ Flare tunnel started successfully at: {self.tunnel_url}")
            else:
                LOG.warning("⚠️ Tunnel started but URL is not available")
            self.tunnel_ready.set()
        except CloudflaredError as e:
            LOG.error(f"❌ Failed to start flare tunnel (Cloudflare error): {e}", exc_info=True)
            self.tunnel_url = None
            self.tunnel_ready.set()  # 即使失败也设置标志
        except Exception as e:
            LOG.error(f"❌ Failed to start flare tunnel (unexpected error): {e}", exc_info=True)
            self.tunnel_url = None
            self.tunnel_ready.set()  # 即使失败也设置标志
        
        yield
        
        # Shutdown
        LOG.info("Web UI is shutting down...")
        if self.tunnel:
            try:
                self.tunnel.stop()
                LOG.info("Flare tunnel stopped successfully.")
            except Exception as e:
                LOG.error(f"Error stopping tunnel: {e}", exc_info=True)

    async def run_in_background(self):
        """Run the FastAPI app."""
        config = uvicorn.Config(self.app, host="127.0.0.1", port=8000, log_level="info")
        server = uvicorn.Server(config)
        await server.serve()

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
