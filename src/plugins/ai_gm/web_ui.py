import asyncio
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pathlib import Path
import uvicorn
from contextlib import asynccontextmanager
import threading

from ncatbot.utils import get_log

from .db import Database
from flaredantic import FlareTunnel, FlareConfig

LOG = get_log(__name__)

class WebUI:
    def __init__(self, db: Database, plugin_data_path: Path):
        self.db = db
        self.plugin_data_path = plugin_data_path
        self.app = FastAPI(lifespan=self.lifespan)
        self.templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
        self.tunnel: FlareTunnel | None = None
        self.tunnel_url: str | None = None
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
        threading.Thread(target=self.start_tunnel, daemon=True).start()
        yield
        # Shutdown
        LOG.info("Web UI is shutting down...")
        if self.tunnel:
            self.tunnel.stop()

    def start_tunnel(self):
        try:
            config = FlareConfig(
                port=8000,
                bin_dir=self.plugin_data_path / "bin",
                verbose=True
            )
            self.tunnel = FlareTunnel(config)
            self.tunnel.start()
            self.tunnel_url = self.tunnel.tunnel_url
            LOG.info(f"Flare tunnel started at: {self.tunnel_url}")
        except Exception as e:
            LOG.error(f"Failed to start flare tunnel: {e}", exc_info=True)

    async def run_in_background(self):
        """Run the FastAPI app in a separate thread."""
        config = uvicorn.Config(self.app, host="127.0.0.1", port=8000, log_level="info")
        server = uvicorn.Server(config)
        
        loop = asyncio.get_event_loop()
        await loop.create_task(server.serve())

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
