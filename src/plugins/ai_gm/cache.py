import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import aiofiles
import aiofiles.os as aio_os
from typing import TypedDict, NotRequired
import asyncio
from copy import deepcopy

from ncatbot.utils import get_log
from .constants import CACHE_SAVE_DELAY_SECONDS

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
        self._loaded = False  # 防止运行期被重复加载导致状态回退
        self._pending_save_task: asyncio.Task | None = None  # 待执行的保存任务
        self._save_requested = False  # 标记是否有保存请求

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

    async def clear_pending_games(self):
        async with self._cache_lock:
            self.pending_new_games.clear()
        await self.save_to_disk(force=True)

    async def cleanup_expired_pending_games(self, timeout_seconds: int) -> set[str]:
        """清理所有过期的待处理游戏并返回被清理的 message_id 集合"""
        async with self._cache_lock:
            now = datetime.now(timezone.utc)
            expired_ids = set()
            for msg_id, game_data in self.pending_new_games.items():
                create_time = game_data.get("create_time")
                if isinstance(create_time, datetime) and (
                    now - create_time
                ) > timedelta(seconds=timeout_seconds):
                    expired_ids.add(msg_id)

            if not expired_ids:
                return set()

            LOG.info(f"清理 {len(expired_ids)} 个过期的待处理游戏...")
            for msg_id in expired_ids:
                self.pending_new_games.pop(msg_id, None)

        await self.save_to_disk(force=True)
        return expired_ids

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
        """
        获取指定消息的投票缓存项（深拷贝）。
        
        Args:
            group_id: 群组ID
            message_id: 消息ID
            
        Returns:
            VoteCacheItem | None: 投票缓存项的深拷贝，如果不存在则返回 None
        """
        async with self._cache_lock:
            item = self.vote_cache.get(group_id, {}).get(message_id)
            return deepcopy(item) if item else None

    async def get_group_vote_cache(self, group_id: str) -> dict[str, VoteCacheItem]:
        async with self._cache_lock:
            return deepcopy(self.vote_cache.get(group_id, {}))

    async def remove_vote_item(self, group_id: str, message_id: str):
        async with self._cache_lock:
            group_cache = self.vote_cache.get(group_id)
            if group_cache is not None:
                group_cache.pop(message_id, None)
        await self.save_to_disk()

    async def clear_group_vote_cache(self, group_id: str):
        async with self._cache_lock:
            if group_id in self.vote_cache:
                self.vote_cache[group_id].clear()
        await self.save_to_disk()

    async def load_from_disk(self):
        """从磁盘加载缓存文件"""
        # 防止运行期再次调用把内存状态覆盖回旧盘态
        if self._loaded:
            LOG.warning("缓存已加载过，重复加载被忽略。")
            return
        if not self.cache_path or not await aio_os.path.exists(str(self.cache_path)):
            self._loaded = True
            return

        # 统一锁顺序：_cache_lock -> _io_lock，避免与 save_to_disk 死锁
        async with self._cache_lock:
            async with self._io_lock:
                try:
                    async with aiofiles.open(self.cache_path, "r", encoding="utf-8") as f:
                        content = await f.read()
                        payload = json.loads(content)

                    # 先在本地变量里"组装好"恢复结果
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

                    # 直接更新内存状态（已在 _cache_lock 保护下）
                    self.pending_new_games = pending_new_games_restored
                    self.vote_cache = vote_cache_restored

                except Exception as e:
                    LOG.error(f"从磁盘加载缓存失败: {e}", exc_info=True)
                    # 即使失败也标为已尝试加载，避免运行中再次触发
                    self._loaded = True
                    return

        LOG.info("成功从磁盘加载缓存。")
        self._loaded = True

    async def _delayed_save_worker(self):
        """
        延迟保存工作协程。
        
        等待指定时间后执行实际保存，期间的所有保存请求会被合并。
        """
        await asyncio.sleep(CACHE_SAVE_DELAY_SECONDS)
        await self._do_save_to_disk()

    async def _do_save_to_disk(self):
        """执行实际的磁盘保存操作"""
        if not self.cache_path:
            return

        # 先在 _cache_lock 下做"稳定快照/序列化材料"，避免读到半更新状态
        async with self._cache_lock:
            self._save_requested = False  # 重置保存请求标记
            
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

        # 再拿 _io_lock 做写盘（注意锁顺序统一：先 _cache_lock 再 _io_lock）
        async with self._io_lock:
            try:
                async with aiofiles.open(self.cache_path, "w", encoding="utf-8") as f:
                    await f.write(json.dumps(data, indent=4, ensure_ascii=False))
                LOG.debug("缓存已成功保存到磁盘")
            except Exception as e:
                LOG.error(f"保存缓存到磁盘失败: {e}", exc_info=True)

    async def save_to_disk(self, force: bool = False):
        """
        请求保存缓存到磁盘。
        
        使用延迟+合并策略：
        - 非强制模式：设置一个延迟定时器，期间的所有保存请求会被合并
        - 强制模式：立即执行保存，取消待执行的延迟任务
        
        Args:
            force: 是否强制立即保存
        """
        if not self.cache_path:
            return

        if force:
            # 强制保存：取消待执行的任务并立即保存
            if self._pending_save_task and not self._pending_save_task.done():
                self._pending_save_task.cancel()
                try:
                    await self._pending_save_task
                except asyncio.CancelledError:
                    pass
            self._pending_save_task = None
            await self._do_save_to_disk()
        else:
            # 延迟保存：如果没有待执行的任务，创建一个
            if not self._pending_save_task or self._pending_save_task.done():
                self._save_requested = True
                self._pending_save_task = asyncio.create_task(self._delayed_save_worker())
            # 如果已有待执行的任务，本次请求会自动合并（无需操作）

    async def shutdown(self):
        """
        关闭缓存管理器，确保所有待保存的数据都被写入磁盘。
        
        应在程序退出前调用此方法，以防止数据丢失。
        """
        # 如果有待执行的保存任务，等待其完成
        if self._pending_save_task and not self._pending_save_task.done():
            LOG.info("等待待执行的缓存保存任务完成...")
            try:
                await self._pending_save_task
                LOG.info("待执行的缓存保存任务已完成")
            except asyncio.CancelledError:
                # 如果任务被取消，强制保存一次
                LOG.warning("缓存保存任务被取消，执行最终强制保存")
                await self._do_save_to_disk()
            except Exception as e:
                LOG.error(f"等待缓存保存任务时出错: {e}，执行最终强制保存")
                await self._do_save_to_disk()
        elif self._save_requested:
            # 如果有保存请求但任务已完成或不存在，执行最终保存
            LOG.info("执行最终缓存保存")
            await self._do_save_to_disk()
