import asyncio
import json
import aiofiles
from pathlib import Path
from datetime import datetime, timezone
from ncatbot.utils import get_log

LOG = get_log(__name__)


class ChannelConfigManager:
    """频道配置管理器，使用JSON文件存储频道级别的高级模式设置"""

    def __init__(self, plugin_data_path: Path):
        self.config_file = plugin_data_path / "channel_config.json"
        self._config_cache = dict()
        self._load_lock = asyncio.Lock()

    async def _load_config(self):
        """异步加载配置文件"""
        async with self._load_lock:
            if self.config_file.exists():
                try:
                    async with aiofiles.open(self.config_file, 'r', encoding='utf-8') as f:
                        content = await f.read()
                        config = json.loads(content)
                        self._config_cache = config
                        LOG.debug(f"已加载频道配置文件: {self.config_file}")
                        return config
                except (json.JSONDecodeError, Exception) as e:
                    LOG.error(f"加载频道配置文件失败: {e}")
                    # 如果配置文件损坏，使用默认配置
                    default_config = {"channel_configs": {}}
                    self._config_cache = default_config
                    return default_config
            else:
                # 配置文件不存在，创建默认配置
                default_config = {"channel_configs": {}}
                await self._save_config(default_config)
                self._config_cache = default_config
                return default_config

    async def _save_config(self, config):
        """异步保存配置文件"""
        try:
            # 确保目录存在
            self.config_file.parent.mkdir(parents=True, exist_ok=True)

            async with aiofiles.open(self.config_file, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(config, indent=2, ensure_ascii=False))

            self._config_cache = config
            LOG.debug(f"已保存频道配置文件: {self.config_file}")
            return True
        except Exception as e:
            LOG.error(f"保存频道配置文件失败: {e}")
            return False

    async def is_advanced_mode_enabled(self, channel_id: str) -> bool:
        """检查频道是否启用了高级模式"""
        config = await self._load_config()
        channel_config = config.get("channel_configs", {}).get(channel_id, {})
        return channel_config.get("advanced_mode", False)

    async def enable_advanced_mode(self, channel_id: str, user_id: str) -> bool:
        """启用频道的高级模式"""
        config = await self._load_config()

        if "channel_configs" not in config:
            config["channel_configs"] = {}

        if channel_id not in config["channel_configs"]:
            config["channel_configs"][channel_id] = {}

        config["channel_configs"][channel_id].update({
            "advanced_mode": True,
            "enabled_at": datetime.now(timezone.utc).isoformat(),
            "enabled_by": user_id
        })

        success = await self._save_config(config)
        if success:
            LOG.info(f"频道 {channel_id} 已启用高级模式，操作者: {user_id}")
        return success

    async def disable_advanced_mode(self, channel_id: str) -> bool:
        """禁用频道的高级模式"""
        config = await self._load_config()

        if "channel_configs" not in config:
            return True  # 如果没有配置，已经是禁用状态

        if channel_id in config["channel_configs"]:
            config["channel_configs"][channel_id]["advanced_mode"] = False
            # 保留其他配置信息，只禁用高级模式

        success = await self._save_config(config)
        if success:
            LOG.info(f"频道 {channel_id} 已禁用高级模式")
        return success

    async def get_channel_config(self, channel_id: str):
        """获取频道的完整配置"""
        config = await self._load_config()
        return config.get("channel_configs", {}).get(channel_id, {})

    async def get_all_advanced_channels(self):
        """获取所有启用高级模式的频道"""
        config = await self._load_config()
        channel_configs = config.get("channel_configs", {})

        advanced_channels = {}
        for channel_id, channel_config in channel_configs.items():
            if channel_config.get("advanced_mode", False):
                advanced_channels[channel_id] = channel_config

        return advanced_channels