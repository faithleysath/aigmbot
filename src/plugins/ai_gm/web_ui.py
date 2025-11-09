import asyncio
from aiohttp import web
import aiohttp_jinja2
import jinja2
from pathlib import Path
from flaredantic import FlareTunnel

from ncatbot.utils import get_log

from .db import Database

LOG = get_log(__name__)

class WebUI:
    def __init__(self, db: Database, plugin_data_path: Path):
        self.db = db
        self.plugin_data_path = plugin_data_path
        self.app = web.Application()
        
        # 设置 Jinja2 模板
        template_dir = Path(__file__).parent / "templates"
        aiohttp_jinja2.setup(
            self.app,
            loader=jinja2.FileSystemLoader(str(template_dir))
        )
        
        # Tunnel 相关属性由外部（main.py）管理
        self.tunnel: FlareTunnel | None = None
        self.tunnel_url: str | None = None
        self.tunnel_ready = asyncio.Event()
        
        # 设置路由
        self._setup_routes()
        
        # 设置 startup/cleanup
        self.app.on_startup.append(self.on_startup)
        self.app.on_cleanup.append(self.on_cleanup)
        
        self.runner: web.AppRunner | None = None

    def _setup_routes(self):
        """设置所有路由"""
        self.app.router.add_get("/", self.route_game_list)
        self.app.router.add_get(r"/game/{game_id:\d+}", self.route_game_detail)
        self.app.router.add_get(r"/game/{game_id:\d+}/branch/{branch_name}/history", self.route_branch_history)
        self.app.router.add_get(r"/game/{game_id:\d+}/round/{round_id:\d+}", self.route_round_detail)
        self.app.router.add_get(r"/game/{game_id:\d+}/graph", self.route_graph_page)
        self.app.router.add_get(r"/game/{game_id:\d+}/graph-data", self.route_graph_data)

    async def on_startup(self, app: web.Application):
        """应用启动时的回调"""
        LOG.info("Web UI server is starting up...")

    async def on_cleanup(self, app: web.Application):
        """应用关闭时的回调"""
        LOG.info("Web UI server is shutting down...")

    async def run_in_background(self):
        """在后台运行 aiohttp 服务器"""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 8000)
        await site.start()
        LOG.info("Web UI server started on http://127.0.0.1:8000")
        
        # 保持服务器运行
        while True:
            await asyncio.sleep(3600)

    async def shutdown(self):
        """关闭服务器"""
        if self.runner:
            await self.runner.cleanup()

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

    @aiohttp_jinja2.template('game_list.html')
    async def route_game_list(self, request: web.Request):
        """游戏列表页面"""
        games = await self.db.get_all_games()
        return {"games": games}

    @aiohttp_jinja2.template('game_detail.html')
    async def route_game_detail(self, request: web.Request):
        """游戏详情页面"""
        game_id = int(request.match_info['game_id'])
        game = await self.db.get_game_by_game_id(game_id)
        if not game:
            raise web.HTTPNotFound(text="Game not found")
        
        branches = await self.db.get_all_branches_for_game(game_id)
        tags = await self.db.get_all_tags_for_game(game_id)
        return {"game": game, "branches": branches, "tags": tags}

    @aiohttp_jinja2.template('branch_history.html')
    async def route_branch_history(self, request: web.Request):
        """分支历史页面"""
        game_id = int(request.match_info['game_id'])
        branch_name = request.match_info['branch_name']
        
        branch = await self.db.get_branch_by_name(game_id, branch_name)
        if not branch or branch['tip_round_id'] is None:
            raise web.HTTPNotFound(text="Branch not found or empty")
        
        history = await self.db.get_round_ancestors(branch['tip_round_id'], limit=9999)
        return {"game_id": game_id, "branch": branch, "history": history}

    @aiohttp_jinja2.template('round_detail.html')
    async def route_round_detail(self, request: web.Request):
        """回合详情页面"""
        game_id = int(request.match_info['game_id'])
        round_id = int(request.match_info['round_id'])
        
        round_info = await self.db.get_round_info(round_id)
        if not round_info:
            raise web.HTTPNotFound(text="Round not found")
        
        # 查找上一个和下一个回合
        parent_id = round_info['parent_id']
        children = await self.db.get_child_rounds(round_id)
        next_round_id = children[0]['round_id'] if children else None

        return {
            "game_id": game_id,
            "round": round_info,
            "prev_round_id": parent_id if parent_id != -1 else None,
            "next_round_id": next_round_id
        }

    @aiohttp_jinja2.template('graph.html')
    async def route_graph_page(self, request: web.Request):
        """图表页面"""
        game_id = int(request.match_info['game_id'])
        return {"game_id": game_id}

    async def route_graph_data(self, request: web.Request):
        """图表数据 API"""
        game_id = int(request.match_info['game_id'])
        
        game = await self.db.get_game_by_game_id(game_id)
        if not game:
            raise web.HTTPNotFound(text="Game not found")

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

        # 添加分支和标签信息到节点
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
        
        return web.json_response({"nodes": nodes, "edges": edges})
