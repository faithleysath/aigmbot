# src/plugins/ai_trpg/cache.py
import json
from datetime import datetime
from pathlib import Path
import aiofiles
import aiofiles.os as aio_os
from typing import TypedDict, NotRequired

from ncatbot.utils import get_log

from .utils import _normalize_emoji_id

LOG = get_log(__name__)


class VoteCacheItem(TypedDict):
    content: NotRequired[str]
    votes: dict[str, set[str]]


class CacheManager:
    def __init__(self, cache_path: Path):
        self.cache_path = cache_path
        self.pending_new_games: dict[str, dict] = {}
        self.vote_cache: dict[str, dict[str, VoteCacheItem]] = {}

    async def load_from_disk(self):
        """从磁盘加载缓存文件"""
        if not self.cache_path or not await aio_os.path.exists(self.cache_path):
            return
        try:
            async with aiofiles.open(self.cache_path, "r", encoding="utf-8") as f:
                content = await f.read()
                data = json.loads(content)

                # 恢复 pending_new_games，转换时间字符串
                self.pending_new_games = data.get("pending_new_games", {})
                for key, game in self.pending_new_games.items():
                    if "create_time" in game and isinstance(game["create_time"], str):
                        game["create_time"] = datetime.fromisoformat(
                            game["create_time"]
                        )

                # 恢复 vote_cache，转换 set
                raw_vote_cache = data.get("vote_cache", {})
                self.vote_cache = {}
                for group_id, messages in raw_vote_cache.items():
                    self.vote_cache[group_id] = {}
                    for msg_id, data in messages.items():
                        item: VoteCacheItem = {"votes": {}}
                        if "content" in data and data["content"] is not None:
                            item["content"] = data["content"]

                        if "votes" in data:
                            item["votes"] = {
                                _normalize_emoji_id(k): set(v)
                                for k, v in data["votes"].items()
                            }
                        self.vote_cache[group_id][msg_id] = item
            LOG.info("成功从磁盘加载缓存。")
        except Exception as e:
            LOG.error(f"从磁盘加载缓存失败: {e}", exc_info=True)

    async def save_to_disk(self):
        """将当前缓存保存到磁盘"""
        if not self.cache_path:
            return
        try:
            # 准备待序列化的数据
            serializable_pending = {
                key: {**game, "create_time": game["create_time"].isoformat()}
                for key, game in self.pending_new_games.items()
            }
            serializable_votes = {
                group_id: {
                    msg_id: {
                        "content": item.get("content"),
                        "votes": {
                            emoji_id: list(users)
                            for emoji_id, users in item.get("votes", {}).items()
                        },
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
        except Exception as e:
            LOG.error(f"保存缓存到磁盘失败: {e}", exc_info=True)
