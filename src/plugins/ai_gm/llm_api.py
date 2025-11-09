from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from ncatbot.utils import get_log
import asyncio, random
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
        api_key: str,
        base_url: str,
        model_name: str,
        max_retries: int = 2,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        timeout: float = 60.0,
    ):
        """
        初始化 LLM API 客户端。
        
        Args:
            api_key: OpenAI API 密钥
            base_url: API 基础 URL
            model_name: 使用的模型名称
            max_retries: 最大重试次数
            base_delay: 基础延迟时间（秒）
            max_delay: 最大延迟时间（秒），防止指数退避过大
            timeout: API 调用超时时间（秒）
        """
        if not api_key:
            raise ValueError("OpenAI API key is not configured.")

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout,
        )
        self.model_name = model_name
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay

    async def get_completion(
        self, messages: list[ChatCompletionMessageParam]
    ) -> tuple[str | None, dict | None, str]:
        """
        调用 OpenAI API 获取聊天完成结果，支持自动重试。

        改进版本：使用比例抖动策略和支持取消。

        Args:
            messages: 对话历史列表，格式为 [{"role": "user", "content": "..."}, ...]

        Returns:
            一个元组，包含 (AI 返回的内容字符串, usage 字典, 模型名称)

        Raises:
            RateLimitError: 速率限制错误（重试后仍失败）
            APITimeoutError: 超时错误（重试后仍失败）
            APIConnectionError: 连接错误（重试后仍失败）
            APIStatusError: 服务端错误（重试后仍失败）
            asyncio.CancelledError: 操作被取消
            Exception: 其他意外错误
        """
        for attempt in range(self.max_retries):
            try:
                response = await self.client.chat.completions.create(
                    model=self.model_name,
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
                return content, usage, self.model_name
            except (
                RateLimitError,
                APITimeoutError,
                APIConnectionError,
                APIStatusError,
            ) as e:
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
                    # 改进的延迟计算：基础延迟 + 指数退避 + 比例抖动
                    base_delay = self.base_delay * (2 ** attempt)
                    jitter = random.uniform(0, base_delay * 0.2)  # 20% 的抖动
                    delay = min(base_delay + jitter, self.max_delay)
                    
                    # 根据错误类型提供更详细的日志
                    error_type = e.__class__.__name__
                    if isinstance(e, RateLimitError) or status_code == 429:
                        LOG.warning(
                            f"LLM API 速率限制（{error_type}），"
                            f"第 {attempt + 1}/{self.max_retries - 1} 次重试，"
                            f"等待 {delay:.2f}s..."
                        )
                    else:
                        LOG.warning(
                            f"LLM 调用失败（{error_type}, status={status_code}），"
                            f"第 {attempt + 1}/{self.max_retries - 1} 次重试，"
                            f"等待 {delay:.2f}s..."
                        )
                    
                    # 支持取消的等待
                    try:
                        await asyncio.sleep(delay)
                    except asyncio.CancelledError:
                        LOG.info("LLM 调用被取消")
                        raise
                    
                    continue
                
                # 不可重试或已达最大重试次数
                if retriable:
                    LOG.error(f"调用 OpenAI API 失败，已达最大重试次数: {e}")
                else:
                    LOG.error(f"调用 OpenAI API 时出现不可重试的错误 (status={status_code}): {e}")
                raise
            except asyncio.CancelledError:
                LOG.info("LLM 调用被用户取消")
                raise
            except Exception as e:
                # 非 API 错误，不重试，直接抛出
                LOG.error(f"调用 OpenAI API 时出现意外错误: {e}")
                raise

        # 理论上不会到达这里，但为了类型安全
        return None, None, self.model_name
