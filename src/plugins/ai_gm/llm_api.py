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

        使用指数退避策略和抖动，防止雪崩效应。

        Args:
            messages: 对话历史列表，格式为 [{"role": "user", "content": "..."}, ...]

        Returns:
            一个元组，包含 (AI 返回的内容字符串, usage 字典, 模型名称)

        Raises:
            RateLimitError: 速率限制错误（重试后仍失败）
            APITimeoutError: 超时错误（重试后仍失败）
            APIConnectionError: 连接错误（重试后仍失败）
            APIStatusError: 服务端错误（重试后仍失败）
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
                retriable = (
                    isinstance(e, RateLimitError)
                    or isinstance(e, APITimeoutError)
                    or isinstance(e, APIConnectionError)
                    or (
                        isinstance(e, APIStatusError)
                        and getattr(e, "status_code", 500) >= 500
                    )
                )
                
                # 如果可重试且未达到最大重试次数
                if retriable and attempt < self.max_retries - 1:
                    # 指数退避 + 抖动，并限制最大延迟
                    delay = min(
                        self.base_delay * (2**attempt) + random.uniform(0, 0.5),
                        self.max_delay
                    )
                    LOG.warning(
                        f"LLM 调用失败（{e.__class__.__name__}），"
                        f"第 {attempt + 1}/{self.max_retries - 1} 次重试，"
                        f"等待 {delay:.2f}s..."
                    )
                    await asyncio.sleep(delay)
                    continue
                
                # 不可重试或已达最大重试次数
                LOG.error(f"调用 OpenAI API 时出错: {e}")
                raise
            except Exception as e:
                # 非 API 错误，不重试，直接抛出
                LOG.error(f"调用 OpenAI API 时出现意外错误: {e}")
                raise

        # 理论上不会到达这里，但为了类型安全
        return None, None, self.model_name
