import graphviz
from ncatbot.utils import get_log
from .db import Database
import html

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
            all_tags = await self.db.get_all_tags_for_game(game_id)
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
            tags_by_round = {}
            for tag in all_tags:
                tags_by_round.setdefault(tag["round_id"], []).append(tag["name"])

            key_nodes.update(branch_tips.keys())
            key_nodes.update(tags_by_round.keys())
            
            fork_points = {node for node, children in adj.items() if len(children) > 1}
            key_nodes.update(fork_points)

            # 3. 构建简化图
            dot = graphviz.Digraph(comment=f'Game {game_id} Branch Graph')
            dot.attr('node', shape='plaintext') # 使用 plaintext 以支持 HTML-like labels
            dot.attr(bgcolor='white', rankdir='TB')

            processed_nodes = set()

            # 3.1 绘制所有关键节点
            for node_id in key_nodes:
                if node_id in processed_nodes:
                    continue

                label_parts = [f'<b>Round {node_id}</b>']
                if node_id == root_node:
                    label_parts = ['<b>Initial</b>']
                
                # 添加分支信息
                node_branches = [b for b in all_branches if b['tip_round_id'] == node_id]
                for branch in node_branches:
                    is_head = (branch['branch_id'] == head_branch_id)
                    branch_name_escaped = html.escape(branch['name'])
                    branch_label = f"{branch_name_escaped} (HEAD)" if is_head else branch_name_escaped
                    label_parts.append(f'🌿 {branch_label}')

                # 添加标签信息
                if node_id in tags_by_round:
                    for tag_name in tags_by_round[node_id]:
                        label_parts.append(f'🏷️ {html.escape(tag_name)}')

                # 使用 HTML-like label
                html_label = '<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="4"><TR><TD>{}</TD></TR></TABLE>>'.format(
                    '<BR/>'.join(label_parts)
                )
                dot.node(str(node_id), label=html_label)
                processed_nodes.add(node_id)

            # 3.2 向上回溯绘制边
            for node_id in key_nodes:
                if node_id == root_node:
                    continue
                
                path_len = 0
                curr = node_id
                while curr in parent_map and curr != root_node:
                    parent = parent_map[curr]
                    path_len += 1
                    
                    if parent in key_nodes:
                        edge_label = f" {path_len} round{'s' if path_len > 1 else ''} "
                        dot.edge(str(parent), str(curr), label=edge_label)
                        break
                    curr = parent

            # 渲染为 PNG 字节
            return dot.pipe(format='png')

        except Exception as e:
            LOG.error(f"创建分支图失败: {e}", exc_info=True)
            return None

    async def create_full_branch_graph(self, game_id: int) -> bytes | None:
        """为指定游戏创建并渲染一个包含所有 round 节点的完整分支图"""
        try:
            game = await self.db.get_game_by_game_id(game_id)
            if not game:
                return None

            all_rounds = await self.db.get_all_rounds_for_game(game_id)
            all_branches = await self.db.get_all_branches_for_game(game_id)
            all_tags = await self.db.get_all_tags_for_game(game_id)
            head_branch_id = game["head_branch_id"]

            if not all_rounds:
                return None

            dot = graphviz.Digraph(comment=f'Game {game_id} Full Branch Graph')
            dot.attr('node', shape='plaintext')
            dot.attr(bgcolor='white', rankdir='TB')

            tags_by_round = {}
            for tag in all_tags:
                tags_by_round.setdefault(tag["round_id"], []).append(tag["name"])

            # 1. 添加所有 round 节点
            for r in all_rounds:
                round_id = r["round_id"]
                
                label_parts = [f'<b>Round {round_id}</b>']
                if r['parent_id'] == -1:
                    label_parts = [f'<b>Initial (Round {round_id})</b>']

                # 添加分支信息
                node_branches = [b for b in all_branches if b['tip_round_id'] == round_id]
                for branch in node_branches:
                    is_head = (branch['branch_id'] == head_branch_id)
                    branch_name_escaped = html.escape(branch['name'])
                    branch_label = f"{branch_name_escaped} (HEAD)" if is_head else branch_name_escaped
                    label_parts.append(f'🌿 {branch_label}')

                # 添加标签信息
                if round_id in tags_by_round:
                    for tag_name in tags_by_round[round_id]:
                        label_parts.append(f'🏷️ {html.escape(tag_name)}')
                
                html_label = '<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="4"><TR><TD>{}</TD></TR></TABLE>>'.format(
                    '<BR/>'.join(label_parts)
                )
                dot.node(str(round_id), label=html_label)

            # 2. 添加所有边
            for r in all_rounds:
                if r["parent_id"] != -1:
                    dot.edge(str(r["parent_id"]), str(r["round_id"]))

            return dot.pipe(format='png')

        except Exception as e:
            LOG.error(f"创建完整分支图失败: {e}", exc_info=True)
            return None
