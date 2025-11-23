import json
import asyncio
import os
import aiofiles
from pathlib import Path
from typing import TypedDict
from urllib.parse import urlparse
from datetime import datetime, timezone
from ncatbot.utils import get_log

from cryptography.fernet import Fernet

LOG = get_log(__name__)

class LLMPreset(TypedDict):
    model: str
    base_url: str
    api_key: str

class BindingInfo(TypedDict):
    owner_id: str
    preset_name: str
    bound_at: float
    expire_at: float | None  # None means permanent

class GroupConfig(TypedDict):
    active: BindingInfo | None
    fallback: BindingInfo | None

class LLMConfigData(TypedDict):
    user_presets: dict[str, dict[str, LLMPreset]]  # user_id -> preset_name -> preset
    group_bindings: dict[str, GroupConfig]  # group_id -> config

class LLMConfigManager:
    def __init__(self, data_path: Path):
        self.config_file = data_path / "llm_presets.json"
        self._data: LLMConfigData = {
            "user_presets": {},
            "group_bindings": {}
        }
        self._lock = asyncio.Lock()
        self._loaded = False
        
        # Initialize encryption
        try:
            key = self._load_or_create_key(data_path)
            self._fernet = Fernet(key)
        except Exception as e:
            LOG.error(f"Failed to initialize encryption: {e}")
            raise

    def _ensure_secure_permissions(self, key_file: Path):
        """确保密钥文件只有所有者可读写 (0o600)"""
        try:
            # 直接尝试修改权限，避免检查文件存在的竞态条件
            os.chmod(key_file, 0o600)
        except FileNotFoundError:
            pass  # 文件不存在，安全忽略
        except PermissionError as e:
            LOG.warning(f"无法设置密钥文件权限 (PermissionError): {e}")
        except OSError as e:
            LOG.warning(f"设置密钥文件权限失败 (OSError): {e}")

    def _load_or_create_key(self, data_path: Path) -> bytes:
        key_file = data_path / ".secret.key"
        
        # 每次访问都确保权限安全
        if key_file.exists():
            self._ensure_secure_permissions(key_file)
            with open(key_file, "rb") as f:
                return f.read()
        else:
            key = Fernet.generate_key()
            # Ensure directory exists
            data_path.mkdir(parents=True, exist_ok=True)
            with open(key_file, "wb") as f:
                f.write(key)
            
            # 设置初始权限
            self._ensure_secure_permissions(key_file)
            LOG.info(f"Created new encryption key at {key_file} with secure permissions (0600)")
            return key

    def _encrypt(self, text: str) -> str:
        if text:
            return self._fernet.encrypt(text.encode()).decode()
        return text

    def _decrypt(self, text: str) -> str:
        if text:
            try:
                return self._fernet.decrypt(text.encode()).decode()
            except Exception as e:
                LOG.error(f"Decryption failed: {e}")
                raise ValueError("API Key 解密失败,密钥可能已损坏或被篡改")
        return text

    def _validate_preset_params(self, name: str, model: str, base_url: str, api_key: str):
        """
        FIX: Validate preset parameters before adding
        
        Raises:
            ValueError: If any parameter is invalid
        """
        # Validate preset name
        if not name or not name.strip():
            raise ValueError("预设名称不能为空")
        
        if len(name) > 50:
            raise ValueError("预设名称过长（最多50个字符）")
        
        # Validate model name
        if not model or not model.strip():
            raise ValueError("模型名称不能为空")
        
        # Validate base_url format
        if not base_url or not base_url.strip():
            raise ValueError("API 地址不能为空")
        
        parsed = urlparse(base_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError("API 地址格式无效（需要完整的 URL，如 https://api.example.com）")
        
        if parsed.scheme not in ["http", "https"]:
            raise ValueError("API 地址必须使用 http 或 https 协议")
        
        # Validate API key
        if not api_key or not api_key.strip():
            raise ValueError("API Key 不能为空")
        
        if len(api_key) < 10:
            raise ValueError("API Key 过短（至少需要10个字符）")
        
        if len(api_key) > 500:
            raise ValueError("API Key 过长（最多500个字符）")

    async def load(self):
        """加载配置文件"""
        async with self._lock:
            if self._loaded:
                return

            if self.config_file.exists():
                try:
                    async with aiofiles.open(self.config_file, 'r', encoding='utf-8') as f:
                        content = await f.read()
                        loaded_data = json.loads(content)
                        # 简单的数据迁移/校验
                        if "user_presets" in loaded_data:
                            self._data["user_presets"] = loaded_data["user_presets"]
                        if "group_bindings" in loaded_data:
                            self._data["group_bindings"] = loaded_data["group_bindings"]
                except Exception as e:
                    LOG.error(f"加载 LLM 配置文件失败: {e}")
            
            self._loaded = True

    async def _save(self):
        """保存配置文件 (内部使用，假设已获取锁) - 使用原子写入
        
        Raises:
            Exception: 保存失败时抛出异常
        """
        # FIX: Use atomic write with temporary file
        tmp_file = self.config_file.with_suffix('.tmp')
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            async with aiofiles.open(tmp_file, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(self._data, indent=2, ensure_ascii=False))
            # Atomic replace
            os.replace(tmp_file, self.config_file)
            # FIX: Set secure file permissions after save
            os.chmod(self.config_file, 0o600)
        except Exception as e:
            LOG.error(f"保存 LLM 配置文件失败: {e}")
            # Clean up temp file if it exists
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except Exception:
                    pass
            raise

    # --- User Presets CRUD ---

    async def add_preset(self, user_id: str, name: str, model: str, base_url: str, api_key: str):
        """
        添加新的 LLM 预设
        
        Raises:
            ValueError: 参数验证失败
        """
        # FIX: Validate all parameters before adding
        self._validate_preset_params(name, model, base_url, api_key)
        
        async with self._lock:
            if user_id not in self._data["user_presets"]:
                self._data["user_presets"][user_id] = {}
            
            # Store encrypted API key
            encrypted_key = self._encrypt(api_key)
            
            self._data["user_presets"][user_id][name] = {
                "model": model.strip(),
                "base_url": base_url.strip(),
                "api_key": encrypted_key
            }
            await self._save()

    async def remove_preset(self, user_id: str, name: str) -> tuple[bool, list[str]]:
        """返回 (是否成功, 使用此预设的群组列表)"""
        async with self._lock:
            # 检查是否有群组正在使用
            using_groups = []
            for group_id, config in self._data["group_bindings"].items():
                active = config.get("active")
                if active and active["owner_id"] == user_id and active["preset_name"] == name:
                    using_groups.append(group_id)
                
                fallback = config.get("fallback")
                if fallback and fallback["owner_id"] == user_id and fallback["preset_name"] == name:
                    if group_id not in using_groups:
                        using_groups.append(group_id)
            
            if using_groups:
                return False, using_groups
            
            # 安全删除
            if user_id in self._data["user_presets"] and name in self._data["user_presets"][user_id]:
                del self._data["user_presets"][user_id][name]
                await self._save()
                return True, []
            return False, []

    async def get_user_presets(self, user_id: str) -> dict[str, LLMPreset]:
        async with self._lock:
            presets = self._data["user_presets"].get(user_id, {})
            # Decrypt keys for display/usage
            decrypted_presets = {}
            for name, preset in presets.items():
                p = preset.copy()
                try:
                    p["api_key"] = self._decrypt(p["api_key"])
                    decrypted_presets[name] = p
                except ValueError as e:
                    LOG.error(f"Failed to decrypt preset '{name}' for user {user_id}: {e}")
                    # Skip corrupted presets
                    continue
            return decrypted_presets

    async def get_user_presets_safe(self, user_id: str) -> dict[str, dict]:
        """获取用户预设（脱敏显示，用于 UI/查询）"""
        # Re-use get_user_presets which handles locking and decryption
        presets = await self.get_user_presets(user_id)
        return {
            name: {
                "model": preset["model"],
                "base_url": preset["base_url"],
                "api_key": f"***{preset['api_key'][-4:]}" if len(preset['api_key']) > 4 else "***"
            }
            for name, preset in presets.items()
        }

    def _get_preset_locked(self, user_id: str, name: str) -> LLMPreset | None:
        """内部方法，假设调用者已持有锁"""
        preset = self._data["user_presets"].get(user_id, {}).get(name)
        if preset:
            p = preset.copy()
            try:
                p["api_key"] = self._decrypt(p["api_key"])
                return p
            except ValueError as e:
                LOG.error(f"Failed to decrypt preset '{name}' for user {user_id}: {e}")
                return None
        return None

    async def get_preset(self, user_id: str, name: str) -> LLMPreset | None:
        async with self._lock:
            return self._get_preset_locked(user_id, name)

    # --- Group Bindings ---

    async def bind_active(self, group_id: str, owner_id: str, preset_name: str, duration_seconds: int | None = None) -> tuple[bool, str]:
        """
        尝试设置 Active Binding。
        遵循 First Come First Served 原则。
        
        Returns:
            (是否成功, 错误信息或成功提示)
        """
        async with self._lock:
            group_conf = self._data["group_bindings"].setdefault(group_id, {"active": None, "fallback": None})
            
            # FIX: Check for race condition - if another user has already bound
            current_active = group_conf.get("active")
            if current_active:
                if self._is_binding_valid(current_active):
                    # Already occupied by another user
                    if current_active["owner_id"] != owner_id:
                        return False, f"该群已被用户 {current_active['owner_id']} 绑定"
                    # Same user re-binding - allow update
            
            # 验证预设是否存在
            preset = self._get_preset_locked(owner_id, preset_name)
            if not preset:
                return False, f"预设 '{preset_name}' 不存在"
            
            # 设置新绑定 - FIX: Use UTC timestamp
            now = datetime.now(timezone.utc).timestamp()
            expire_at = (now + duration_seconds) if duration_seconds else None
            group_conf["active"] = {
                "owner_id": owner_id,
                "preset_name": preset_name,
                "bound_at": now,
                "expire_at": expire_at
            }
            await self._save()
            return True, "绑定成功"

    async def unbind_active(self, group_id: str):
        async with self._lock:
            if group_id in self._data["group_bindings"]:
                self._data["group_bindings"][group_id]["active"] = None
                await self._save()

    async def set_fallback(self, group_id: str, owner_id: str, preset_name: str):
        """
        设置保底预设
        
        Raises:
            ValueError: 预设不存在
        """
        async with self._lock:
            # 验证预设是否存在
            preset = self._get_preset_locked(owner_id, preset_name)
            if not preset:
                raise ValueError(f"预设 '{preset_name}' 不存在")
            
            group_conf = self._data["group_bindings"].setdefault(group_id, {"active": None, "fallback": None})
            group_conf["fallback"] = {
                "owner_id": owner_id,
                "preset_name": preset_name,
                "bound_at": datetime.now(timezone.utc).timestamp(),
                "expire_at": None # Fallback 默认为永久
            }
            await self._save()

    async def clear_fallback(self, group_id: str):
        async with self._lock:
            if group_id in self._data["group_bindings"]:
                self._data["group_bindings"][group_id]["fallback"] = None
                await self._save()

    async def get_group_binding(self, group_id: str) -> BindingInfo | None:
        """获取当前有效的绑定信息 (Active > Fallback)
        
        注意：此方法会清理过期的 active 绑定，但为了性能考虑，
        不会立即保存到磁盘。过期绑定会在下次修改操作时自然清除。
        """
        async with self._lock:
            group_conf = self._data["group_bindings"].get(group_id)
            if not group_conf:
                return None

            # 1. Check Active
            active = group_conf.get("active")
            if active:
                if self._is_binding_valid(active):
                    return active
                else:
                    # 过期，标记清理（但不立即保存，减少 I/O）
                    group_conf["active"] = None
                    # NOTE: 不在这里调用 _save()，会在下次写操作时自然保存
            
            # 2. Check Fallback
            fallback = group_conf.get("fallback")
            if fallback:
                return fallback
            
            return None

    def _is_binding_valid(self, binding: BindingInfo) -> bool:
        """检查绑定是否有效（未过期）- FIX: Use UTC timestamp"""
        if binding["expire_at"] is None:
            return True
        return datetime.now(timezone.utc).timestamp() < binding["expire_at"]

    async def resolve_preset(self, binding: BindingInfo) -> LLMPreset | None:
        """根据绑定信息解析出实际的 preset 数据"""
        return await self.get_preset(binding["owner_id"], binding["preset_name"])

    async def get_binding_status(self, group_id: str) -> GroupConfig:
        """获取群绑定的完整状态（用于查询）"""
        async with self._lock:
            # Cast the default dict to GroupConfig to satisfy type checker if needed, 
            # or just rely on structural typing. Here we change return type to GroupConfig.
            return self._data["group_bindings"].get(group_id, {"active": None, "fallback": None}).copy()  # type: ignore

    async def test_preset(self, preset: LLMPreset, llm_api=None, timeout: int = 30) -> tuple[bool, str]:
        """
        测试 LLM 预设是否可用
        
        Args:
            preset: 要测试的预设
            llm_api: LLM_API 实例（如果提供）
            timeout: 测试超时时间（秒，默认 10）
            
        Returns:
            (是否可用, 错误信息)
        """
        if not llm_api:
            return False, "LLM API 未初始化"
        
        try:
            # 使用简单的测试消息
            test_messages = [
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": "Hello"}
            ]
            
            response, _, _ = await asyncio.wait_for(
                llm_api.get_completion(test_messages, preset=preset),
                timeout=timeout
            )
            
            if response:
                return True, "预设可用"
            else:
                return False, "未收到响应"
        
        except asyncio.TimeoutError:
            return False, f"测试超时（{timeout}秒）"
        except Exception as e:
            error_msg = str(e)
            # 提取关键错误信息（安全清理，不泄露完整错误）
            if "401" in error_msg or "authentication" in error_msg.lower():
                return False, "API Key 无效"
            elif "404" in error_msg or "not found" in error_msg.lower():
                return False, "API 端点不存在"
            elif "timeout" in error_msg.lower():
                return False, "连接超时"
            elif "rate" in error_msg.lower() and "limit" in error_msg.lower():
                return False, "速率限制"
            else:
                # Generic error - sanitized to avoid leaking sensitive info
                return False, f"测试失败 ({e.__class__.__name__})"

    def parse_duration(self, duration_str: str) -> int | None:
        """解析时长字符串，返回秒数。如果解析失败返回 None。
        
        支持格式：
        - Nm: N分钟
        - Nh: N小时
        - Nd: N天
        
        限制：最多 90 天
        """
        if not duration_str:
            return None
            
        duration_str = duration_str.lower().strip()
        
        # FIX: Add maximum duration limit (90 days)
        MAX_DAYS = 90
        
        try:
            if duration_str.endswith("m"):
                minutes = int(duration_str[:-1])
                if minutes > MAX_DAYS * 24 * 60:
                    return None
                return minutes * 60
            elif duration_str.endswith("h"):
                hours = int(duration_str[:-1])
                if hours > MAX_DAYS * 24:
                    return None
                return hours * 3600
            elif duration_str.endswith("d"):
                days = int(duration_str[:-1])
                if days > MAX_DAYS:
                    return None
                return days * 86400
        except ValueError:
            pass
            
        return None
