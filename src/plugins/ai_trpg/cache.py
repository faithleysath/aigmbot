# src/plugins/ai_trpg/cache.py
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
        self._cache_lock = asyncio.Lock()  # 新增：保护内存缓存的并发更新
        self._last_save_ts = 0.0

    async def load_from_disk(self):
        """从磁盘加载缓存文件"""
        if not self.cache_path or not await aio_os.path.exists(str(self.cache_path)):
            return
        async with self._io_lock:
            try:
                async with aiofiles.open(self.cache_path, "r", encoding="utf-8") as f:
                    content = await f.read()
                    payload = json.loads(content)

                # 恢复 pending_new_games
                self.pending_new_games = payload.get("pending_new_games", {})
                for key, game in self.pending_new_games.items():
                    if "create_time" in game and isinstance(game["create_time"], str):
                        game["create_time"] = datetime.fromisoformat(game["create_time"])

                # 恢复 vote_cache
                raw_vote_cache = payload.get("vote_cache", {})
                self.vote_cache = {}
                for group_id, messages in raw_vote_cache.items():
                    self.vote_cache[group_id] = {}
                    for msg_id, item_payload in messages.items():
                        item: VoteCacheItem = {"votes": {}}
                        if "content" in item_payload and item_payload["content"] is not None:
                            item["content"] = item_payload["content"]
                        if "votes" in item_payload:
                            item["votes"] = {str(k): set(v) for k, v in item_payload["votes"].items()}
                        self.vote_cache[group_id][msg_id] = item
                LOG.info("成功从磁盘加载缓存。")
            except Exception as e:
                LOG.error(f"从磁盘加载缓存失败: {e}", exc_info=True)

    async def save_to_disk(self, force: bool = False):
        """将当前缓存保存到磁盘"""
        if not self.cache_path:
            return

        async with self._io_lock:
            # —— 把节流判断移动到锁内，并支持 force 直通 ——
            now = asyncio.get_running_loop().time()  # 或者 time.monotonic()
            if not force and (now - self._last_save_ts) < 0.3:
                return

            try:
                serializable_pending = {}
                for key, game in self.pending_new_games.items():
                    game_data = game.copy()
                    if "create_time" in game_data and isinstance(game_data["create_time"], datetime):
                        game_data["create_time"] = game_data["create_time"].isoformat()
                    serializable_pending[key] = game_data

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

                async with aiofiles.open(self.cache_path, "w", encoding="utf-8") as f:
                    await f.write(json.dumps(data, indent=4, ensure_ascii=False))

                # —— 只有真正写成功后才更新时间戳 ——
                self._last_save_ts = now

            except Exception as e:
                LOG.error(f"保存缓存到磁盘失败: {e}", exc_info=True)
