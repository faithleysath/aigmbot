# File: src/plugins/ai_trpg/cache.py
import json
from datetime import datetime
from pathlib import Path
import aiofiles
import aiofiles.os as aio_os
from typing import TypedDict, NotRequired
import asyncio

from ncatbot.utils import get_log

LOG = get_log(__name__)


class VoteCacheItem(TypedDict):
    content: NotRequired[str]
    votes: dict[str, set[str]]


class CacheManager:
    def __init__(self, cache_path: Path):
        self.cache_path = cache_path
        self.pending_new_games: dict[str, dict] = {}
        self.vote_cache: dict[str, dict[str, VoteCacheItem]] = {}
        self._io_lock = asyncio.Lock()
        self._cache_lock = asyncio.Lock()  # 保护内存缓存并发
        self._last_save_ts = 0.0

    # --- Pending Games ---
    async def add_pending_game(self, message_id: str, game_data: dict):
        async with self._cache_lock:
            self.pending_new_games[message_id] = game_data
        await self.save_to_disk()

    async def get_pending_game(self, message_id: str) -> dict | None:
        async with self._cache_lock:
            return self.pending_new_games.get(message_id)

    async def remove_pending_game(self, message_id: str):
        async with self._cache_lock:
            self.pending_new_games.pop(message_id, None)
        await self.save_to_disk()

    # --- Vote Cache ---
    async def update_vote(
        self, group_id: str, message_id: str, emoji_id: str, user_id: str, is_add: bool
    ):
        async with self._cache_lock:
            group_cache = self.vote_cache.setdefault(group_id, {})
            message_votes = group_cache.setdefault(message_id, {"votes": {}})
            if "votes" not in message_votes:
                message_votes["votes"] = {}
            vote_set = message_votes["votes"].setdefault(emoji_id, set())
            if is_add:
                vote_set.add(user_id)
            else:
                vote_set.discard(user_id)
        await self.save_to_disk()

    async def set_custom_input_content(
        self, group_id: str, message_id: str, content: str
    ):
        async with self._cache_lock:
            group_cache = self.vote_cache.setdefault(group_id, {})
            entry = group_cache.setdefault(message_id, {"votes": {}})
            entry["content"] = content
        await self.save_to_disk(force=True)

    async def get_vote_item(
        self, group_id: str, message_id: str
    ) -> VoteCacheItem | None:
        async with self._cache_lock:
            return self.vote_cache.get(group_id, {}).get(message_id)

    async def get_group_vote_cache(self, group_id: str) -> dict[str, VoteCacheItem]:
        async with self._cache_lock:
            return self.vote_cache.get(group_id, {})

    async def clear_group_vote_cache(self, group_id: str):
        async with self._cache_lock:
            if group_id in self.vote_cache:
                self.vote_cache[group_id] = {}
        await self.save_to_disk()

    async def load_from_disk(self):
        """从磁盘加载缓存文件"""
        if not self.cache_path or not await aio_os.path.exists(str(self.cache_path)):
            return

        # 先只做 IO 与反序列化（仅持有 _io_lock）
        async with self._io_lock:
            try:
                async with aiofiles.open(self.cache_path, "r", encoding="utf-8") as f:
                    content = await f.read()
                    payload = json.loads(content)

                # 先在本地变量里“组装好”恢复结果
                pending_new_games_restored = payload.get("pending_new_games", {})
                for key, game in pending_new_games_restored.items():
                    if "create_time" in game and isinstance(game["create_time"], str):
                        game["create_time"] = datetime.fromisoformat(game["create_time"])

                raw_vote_cache = payload.get("vote_cache", {})
                vote_cache_restored: dict[str, dict[str, VoteCacheItem]] = {}
                for group_id, messages in raw_vote_cache.items():
                    vote_cache_restored[group_id] = {}
                    for msg_id, item_payload in messages.items():
                        item: VoteCacheItem = {"votes": {}}
                        if "content" in item_payload and item_payload["content"] is not None:
                            item["content"] = item_payload["content"]
                        if "votes" in item_payload:
                            item["votes"] = {str(k): set(v) for k, v in item_payload["votes"].items()}
                        vote_cache_restored[group_id][msg_id] = item
            except Exception as e:
                LOG.error(f"从磁盘加载缓存失败: {e}", exc_info=True)
                return

        # 再切换内存引用（仅持有 _cache_lock，避免与 save_to_disk 形成反转）
        async with self._cache_lock:
            self.pending_new_games = pending_new_games_restored
            self.vote_cache = vote_cache_restored
        LOG.info("成功从磁盘加载缓存。")

    async def save_to_disk(self, force: bool = False):
        """将当前缓存保存到磁盘"""
        if not self.cache_path:
            return

        # 先在 _cache_lock 下做“稳定快照/序列化材料”，避免读到半更新状态
        async with self._cache_lock:
            # 构造可序列化的 pending_new_games
            serializable_pending = {}
            for key, game in self.pending_new_games.items():
                game_data = game.copy()
                if "create_time" in game_data and isinstance(game_data["create_time"], datetime):
                    game_data["create_time"] = game_data["create_time"].isoformat()
                serializable_pending[key] = game_data

            # 构造可序列化的 vote_cache（set -> list）
            serializable_votes = {
                group_id: {
                    msg_id: {
                        "content": item.get("content"),
                        "votes": {emoji_id: list(users) for emoji_id, users in item.get("votes", {}).items()},
                    }
                    for msg_id, item in messages.items()
                }
                for group_id, messages in self.vote_cache.items()
            }

            data = {
                "pending_new_games": serializable_pending,
                "vote_cache": serializable_votes,
            }

        # 再拿 _io_lock 做节流与写盘（注意锁顺序统一：先 _cache_lock 再 _io_lock）
        async with self._io_lock:
            now = asyncio.get_running_loop().time()
            if not force and (now - self._last_save_ts) < 0.3:
                return
            try:
                async with aiofiles.open(self.cache_path, "w", encoding="utf-8") as f:
                    await f.write(json.dumps(data, indent=4, ensure_ascii=False))
                self._last_save_ts = now
            except Exception as e:
                LOG.error(f"保存缓存到磁盘失败: {e}", exc_info=True)
