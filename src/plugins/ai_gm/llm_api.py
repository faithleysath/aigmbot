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
    def __init__(self, api_key: str, base_url: str, model_name: str):
        if not api_key:
            raise ValueError("OpenAI API key is not configured.")

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=60.0,
        )
        self.model_name = model_name

    async def get_completion(
        self, messages: list[ChatCompletionMessageParam]
    ) -> tuple[str | None, dict | None, str]:
        """
        调用 OpenAI API 获取聊天完成结果。

        :param messages: 对话历史列表，格式为 [{"role": "user", "content": "..."}, ...]
        :return: 一个元组，包含 (AI 返回的内容字符串, usage 字典, 模型名称)，如果出错则返回 (None, None, model_name)
        """
        max_retries = 2
        base_delay = 1.0  # seconds

        for attempt in range(max_retries):
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
                retriable = (
                    isinstance(e, RateLimitError)
                    or isinstance(e, APITimeoutError)
                    or isinstance(e, APIConnectionError)
                    or (
                        isinstance(e, APIStatusError)
                        and getattr(e, "status_code", 500) >= 500
                    )
                )
                if retriable and attempt < max_retries - 1:
                    delay = base_delay * (2**attempt) + random.uniform(0, 0.5)
                    LOG.warning(
                        f"LLM 调用失败（{e.__class__.__name__}），第 {attempt+1} 次重试，等待 {delay:.2f}s ..."
                    )
                    await asyncio.sleep(delay)
                    continue
                LOG.error(f"调用 OpenAI API 时出错: {e}")
                raise
            except Exception as e:
                LOG.error(f"调用 OpenAI API 时出现意外错误: {e}")
                raise

        return None, None, self.model_name
