from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from ncatbot.utils import get_log
import asyncio
import random
import time
from collections import OrderedDict
from .llm_config import LLMPreset

try:
    # 优先使用新版 SDK 的异常路径
    from openai import (
        APIStatusError,
        RateLimitError,
        APIConnectionError,
        APITimeoutError,
    )
except ImportError:
    # 兼容旧版 SDK
    from openai.errors import (  # type: ignore
        APIStatusError,
        RateLimitError,
        APIConnectionError,
        APITimeoutError,
    )

LOG = get_log(__name__)


class LLM_API:
    def __init__(
        self,
        # 这些默认参数用于 fallback 或者配置未提供时
        max_retries: int = 2,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        timeout: float = 60.0,
        max_pool_size: int = 50,
        client_idle_timeout: float = 3600.0,
    ):
        """
        初始化 LLM API 管理器。
        
        Args:
            max_retries: 最大重试次数
            base_delay: 基础延迟时间（秒）
            max_delay: 最大延迟时间（秒）
            timeout: API 调用超时时间（秒）
            max_pool_size: 连接池最大大小
            client_idle_timeout: 客户端空闲超时时间（秒），超时后自动清理
        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.timeout = timeout
        self.max_pool_size = max_pool_size
        self.client_idle_timeout = client_idle_timeout
        
        # Client Pool with LRU: (api_key, base_url) -> AsyncOpenAI
        self._client_pool: OrderedDict[tuple[str, str], AsyncOpenAI] = OrderedDict()
        self._client_last_used: dict[tuple[str, str], float] = {}
        self._pool_lock = asyncio.Lock()

    async def _get_client(self, api_key: str, base_url: str) -> AsyncOpenAI:
        """获取或创建 OpenAI 客户端（使用 LRU 策略）"""
        key = (api_key, base_url)
        async with self._pool_lock:
            # 清理过期的空闲连接
            await self._cleanup_idle_clients()
            
            if key in self._client_pool:
                # LRU: 将访问的客户端移到末尾（最近使用）
                self._client_pool.move_to_end(key)
                self._client_last_used[key] = time.time()
                return self._client_pool[key]
            
            # 达到池大小限制，移除最久未使用的（OrderedDict 最前面的）
            if len(self._client_pool) >= self.max_pool_size:
                oldest_key = next(iter(self._client_pool))
                client = self._client_pool.pop(oldest_key)
                await client.close()
                self._client_last_used.pop(oldest_key, None)
                LOG.debug(f"Removed LRU client from pool (size={self.max_pool_size})")

            # 创建新客户端
            self._client_pool[key] = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=self.timeout,
            )
            self._client_last_used[key] = time.time()
            return self._client_pool[key]
    
    async def _cleanup_idle_clients(self):
        """清理超过空闲时间的客户端（内部方法，调用时需已获取锁）"""
        if not self.client_idle_timeout:
            return
        
        now = time.time()
        keys_to_remove = []
        
        for key, last_used in self._client_last_used.items():
            if now - last_used > self.client_idle_timeout:
                keys_to_remove.append(key)
        
        for key in keys_to_remove:
            client = self._client_pool.pop(key, None)
            if client:
                await client.close()
            self._client_last_used.pop(key, None)
        
        if keys_to_remove:
            LOG.debug(f"Cleaned up {len(keys_to_remove)} idle clients")

    async def get_completion(
        self, 
        messages: list[ChatCompletionMessageParam],
        preset: LLMPreset | None = None
    ) -> tuple[str | None, dict | None, str]:
        """
        调用 OpenAI API 获取聊天完成结果，支持自动重试。
        
        支持动态传入 preset。如果没有传入 preset，将抛出错误（因为去除了全局默认）。

        Args:
            messages: 对话历史列表
            preset: LLM 配置预设

        Returns:
            (content, usage, model_name)
        """
        if not preset:
            raise ValueError("No LLM preset provided.")

        model_name = preset["model"]
        base_url = preset["base_url"]
        api_key = preset["api_key"]
        
        client = await self._get_client(api_key, base_url)

        for attempt in range(self.max_retries):
            try:
                response = await client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                )
                content = (
                    response.choices[0].message.content if response.choices else None
                )
                usage = None
                if getattr(response, "usage", None):
                    usage = {
                        "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
                        "completion_tokens": getattr(
                            response.usage, "completion_tokens", None
                        ),
                        "total_tokens": getattr(response.usage, "total_tokens", None),
                    }
                return content, usage, model_name
            except (RateLimitError, APITimeoutError, APIConnectionError, APIStatusError) as e:
                # 判断是否可重试
                status_code = getattr(e, "status_code", 0)
                retriable = (
                    isinstance(e, RateLimitError)  # 429 单独处理
                    or isinstance(e, APITimeoutError)  # 超时
                    or isinstance(e, APIConnectionError)  # 连接错误
                    or (isinstance(e, APIStatusError) and (
                        status_code >= 500  # 5xx 服务器错误
                        or status_code == 429  # 速率限制（备用检查）
                        or status_code == 408  # 请求超时
                    ))
                )
                
                # 如果可重试且未达到最大重试次数
                if retriable and attempt < self.max_retries - 1:
                    base_delay = self.base_delay * (2 ** attempt)
                    jitter = random.uniform(0, base_delay * 0.2)
                    delay = min(base_delay + jitter, self.max_delay)
                    
                    error_type = e.__class__.__name__
                    LOG.warning(
                        f"LLM API Error ({error_type}, {status_code}) using model {model_name}. "
                        f"Retry {attempt + 1}/{self.max_retries - 1} in {delay:.2f}s..."
                    )
                    
                    try:
                        await asyncio.sleep(delay)
                    except asyncio.CancelledError:
                        LOG.info("LLM call cancelled during retry wait")
                        raise
                    
                    continue
                
                # 不可重试或已达最大重试次数
                LOG.error(f"OpenAI API failed (model={model_name}): {e}")
                raise
            except asyncio.CancelledError:
                LOG.info("LLM call cancelled by user")
                raise
            except ValueError as e:
                LOG.error(f"OpenAI API configuration error (model={model_name}): {e}")
                raise
            except TypeError as e:
                LOG.error(f"OpenAI API type error (model={model_name}): {e}")
                raise

        # This point should not be reachable if logic is correct,
        # but to satisfy static analysis (and handle any potential edge case):
        return None, None, model_name
