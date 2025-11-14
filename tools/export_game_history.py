
import sqlite3
import json
import argparse
import os
from typing import Any
import datetime

# 注册新的时间戳转换器以解决 Python 3.12 中的 DeprecationWarning
def adapt_datetime_iso(val):
    """将 datetime.datetime 转换为 ISO 8601 格式的字符串。"""
    return val.isoformat()

def convert_timestamp(val):
    """将字节形式的 ISO 8601 字符串转换为 datetime 对象。"""
    return datetime.datetime.fromisoformat(val.decode())

sqlite3.register_adapter(datetime.datetime, adapt_datetime_iso)
sqlite3.register_converter("timestamp", convert_timestamp)

def get_db_connection(db_path: str) -> sqlite3.Connection | None:
    """建立并返回一个数据库连接。"""
    try:
        # 使用 detect_types 来让 aiosqlite 自动转换数据类型
        conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
        conn.row_factory = sqlite3.Row  # 像 aiosqlite.Row 一样通过列名访问
        print(f"✅ 成功连接到数据库: {db_path}")
        return conn
    except sqlite3.Error as e:
        print(f"❌ 数据库连接失败: {e}")
        return None

def get_games(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """获取所有游戏。"""
    cursor = conn.cursor()
    cursor.execute("SELECT game_id, channel_id, system_prompt, head_branch_id FROM games ORDER BY updated_at DESC")
    return cursor.fetchall()

def get_branches(conn: sqlite3.Connection, game_id: int) -> list[sqlite3.Row]:
    """获取指定游戏的所有分支。"""
    cursor = conn.cursor()
    cursor.execute("SELECT branch_id, name, tip_round_id FROM branches WHERE game_id = ? ORDER BY updated_at DESC", (game_id,))
    return cursor.fetchall()

def get_round_ancestors(conn: sqlite3.Connection, round_id: int) -> list[sqlite3.Row]:
    """获取一个回合及其所有祖先，按时间正序排列。"""
    query = """
    WITH RECURSIVE ancestors AS (
        SELECT *, 0 as depth 
        FROM rounds 
        WHERE round_id = ?
        
        UNION ALL
        
        SELECT r.*, a.depth + 1 
        FROM rounds r 
        JOIN ancestors a ON r.round_id = a.parent_id
        WHERE a.parent_id != -1
    )
    SELECT * FROM ancestors ORDER BY depth DESC;
    """
    cursor = conn.cursor()
    cursor.execute(query, (round_id,))
    return cursor.fetchall()


def select_game(games: list[sqlite3.Row]) -> sqlite3.Row | None:
    """让用户从列表中选择一个游戏。"""
    if not games:
        print("🤔 未找到任何游戏。")
        return None

    print("\n请选择一个游戏:")
    for i, game in enumerate(games):
        print(f"  [{i+1}] Game ID: {game['game_id']} (Channel: {game['channel_id']})")
    
    while True:
        try:
            choice = int(input(f"请输入选项 (1-{len(games)}): "))
            if 1 <= choice <= len(games):
                return games[choice - 1]
            else:
                print("⚠️ 无效输入，请输入列表中的数字。")
        except ValueError:
            print("⚠️ 无效输入，请输入一个数字。")

def select_branch(branches: list[sqlite3.Row], head_branch_id: int | None) -> sqlite3.Row | None:
    """让用户从列表中选择一个分支。"""
    if not branches:
        print("🤔 该游戏没有任何分支。")
        return None
        
    print("\n请选择一个分支:")
    for i, branch in enumerate(branches):
        is_head = " (HEAD)" if head_branch_id and branch['branch_id'] == head_branch_id else ""
        print(f"  [{i+1}] Branch: {branch['name']}{is_head}")

    while True:
        try:
            choice = int(input(f"请输入选项 (1-{len(branches)}): "))
            if 1 <= choice <= len(branches):
                return branches[choice-1]
            else:
                print("⚠️ 无效输入，请输入列表中的数字。")
        except ValueError:
            print("⚠️ 无效输入，请输入一个数字。")


def export_history_to_json(game: sqlite3.Row, rounds: list[sqlite3.Row]) -> dict[str, Any]:
    """将历史记录导出为指定的 JSON 格式。"""
    history = []
    for round_data in rounds:
        history.append({"role": "user", "content": round_data["player_choice"]})
        history.append({"role": "assistant", "content": round_data["assistant_response"]})

    return {
        "system_prompt": game["system_prompt"],
        "history": history
    }

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description="将会话历史导出为 JSON 文件。")
    parser.add_argument("db_path", nargs='?', default=None, help="SQLite 数据库文件的路径（可选）。")
    parser.add_argument("-o", "--output", help="输出 JSON 文件的路径。如果未提供，将根据游戏和分支名称自动生成。")
    args = parser.parse_args()

    db_path = args.db_path
    if db_path:
        db_path = db_path.strip().strip('\'"')

    # 如果没有提供路径或者路径不存在，则提示用户输入
    while not db_path or not os.path.exists(db_path):
        if db_path:
            print(f"❌ 错误: 找不到数据库文件 '{db_path}'")
        
        user_input = input("请输入 SQLite 数据库文件的路径 (或直接回车退出): ").strip()
        if not user_input:
            print("👋 已取消操作。")
            return
        db_path = user_input.strip('\'"')

    conn = get_db_connection(db_path)
    if not conn:
        return

    try:
        # 1. 选择游戏
        games = get_games(conn)
        selected_game = select_game(games)
        if not selected_game:
            return

        # 2. 选择分支
        branches = get_branches(conn, selected_game["game_id"])
        selected_branch = select_branch(branches, selected_game["head_branch_id"])
        if not selected_branch:
            return
            
        if not selected_branch["tip_round_id"]:
            print(f"❌ 分支 '{selected_branch['name']}' 没有起始回合 (tip_round_id is NULL)，无法导出。")
            return

        # 3. 获取并导出历史记录
        rounds = get_round_ancestors(conn, selected_branch["tip_round_id"])
        if not rounds:
            print("🤔 未能获取到任何回合历史。")
            return
        
        output_data = export_history_to_json(selected_game, rounds)
        
        # 4. 保存到文件
        output_path = args.output
        if not output_path:
            output_filename = f"game_{selected_game['game_id']}_branch_{selected_branch['name']}.json"
            output_path = os.path.join(os.getcwd(), output_filename)
            
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=4)
        
        print(f"\n✅ 成功将历史记录导出到: {output_path}")

    finally:
        if conn:
            conn.close()
            print("🔌 数据库连接已关闭。")


if __name__ == "__main__":
    main()
