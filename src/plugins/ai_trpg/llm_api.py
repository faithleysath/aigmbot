from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from ncatbot.utils import get_log
import asyncio
from openai import APIStatusError

LOG = get_log(__name__)


class LLM_API:
    def __init__(self, api_key: str, base_url: str, model_name: str):
        if not api_key:
            raise ValueError("OpenAI API key is not configured.")

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
        )
        self.model_name = model_name

    async def get_completion(
        self, messages: list[ChatCompletionMessageParam]
    ) -> tuple[str | None, dict | None]:
        """
        调用 OpenAI API 获取聊天完成结果。

        :param messages: 对话历史列表，格式为 [{"role": "user", "content": "..."}, ...]
        :return: 一个元组，包含 (AI 返回的内容字符串, usage 字典)，如果出错则返回 (None, None)
        """
        max_retries = 3
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
                return content, usage
            except APIStatusError as e:
                if e.status_code >= 500 and attempt < max_retries - 1:
                    delay = base_delay * (2**attempt)
                    LOG.warning(
                        f"LLM API 返回服务器错误 (状态码 {e.status_code})。将在 {delay:.2f} 秒后重试..."
                    )
                    await asyncio.sleep(delay)
                else:
                    LOG.error(f"调用 OpenAI API 时出错: {e}")
                    raise
            except Exception as e:
                LOG.error(f"调用 OpenAI API 时出现意外错误: {e}")
                raise

        return None, None
