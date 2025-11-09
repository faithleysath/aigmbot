import graphviz
from ncatbot.utils import get_log
from .db import Database

LOG = get_log(__name__)


class Visualizer:
    def __init__(self, db: Database):
        self.db = db

    async def create_branch_graph(self, game_id: int) -> bytes | None:
        """为指定游戏创建并渲染分支图"""
        try:
            game = await self.db.get_game_by_game_id(game_id)
            if not game:
                return None

            all_rounds = await self.db.get_all_rounds_for_game(game_id)
            all_branches = await self.db.get_all_branches_for_game(game_id)
            head_branch_id = game["head_branch_id"]

            if not all_rounds:
                return None

            # 1. 构建邻接表和父节点映射
            adj: dict[int, list[int]] = {r["round_id"]: [] for r in all_rounds}
            parent_map: dict[int, int] = {}
            root_node = -1
            for r in all_rounds:
                parent_id = r["parent_id"]
                round_id = r["round_id"]
                parent_map[round_id] = parent_id
                if parent_id != -1:
                    adj.setdefault(parent_id, []).append(round_id)
                else:
                    root_node = round_id
            
            if root_node == -1:
                return None

            # 2. 识别关键节点
            key_nodes = {root_node}
            branch_tips = {b["tip_round_id"]: (b["name"], b["branch_id"]) for b in all_branches}
            key_nodes.update(branch_tips.keys())
            
            fork_points = {node for node, children in adj.items() if len(children) > 1}
            key_nodes.update(fork_points)

            # 3. 构建简化图
            dot = graphviz.Digraph(comment=f'Game {game_id} Branch Graph')
            dot.attr('node', shape='box', style='rounded')
            dot.attr(bgcolor='transparent', rankdir='LR')

            processed_nodes = set()

            for tip_id, (branch_name, branch_id) in branch_tips.items():
                # 设置分支节点的样式
                is_head = (branch_id == head_branch_id)
                color = 'green' if is_head else 'white'
                fontcolor = 'black' if not is_head else 'white'
                style = 'rounded,filled' if is_head else 'rounded'
                label = f"{branch_name} (HEAD)" if is_head else branch_name
                dot.node(str(tip_id), label, color=color, fontcolor=fontcolor, style=style)

                # 向上回溯
                path_len = 0
                curr = tip_id
                while curr in parent_map and curr != root_node:
                    parent = parent_map[curr]
                    path_len += 1
                    
                    if parent in key_nodes:
                        edge_label = f" {path_len} round{'s' if path_len > 1 else ''} "
                        dot.edge(str(parent), str(curr), label=edge_label)
                        break
                    curr = parent

            # 4. 单独处理其他关键节点（分叉点和根节点）
            for node_id in key_nodes:
                if node_id in processed_nodes:
                    continue
                
                label = f"Round {node_id}"
                if node_id == root_node:
                    label = "Initial"
                elif node_id in fork_points:
                    label = f"Fork Point\n(Round {node_id})"

                if node_id not in branch_tips: # 避免覆盖分支节点的自定义样式
                     dot.node(str(node_id), label)
                
                processed_nodes.add(node_id)

            # 渲染为 PNG 字节
            return dot.pipe(format='png')

        except Exception as e:
            LOG.error(f"创建分支图失败: {e}", exc_info=True)
            return None
