from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from ncatbot.utils import get_log
from typing import List

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

    async def get_completion(self, messages: List[ChatCompletionMessageParam]) -> str | None:
        """
        调用 OpenAI API 获取聊天完成结果。

        :param messages: 对话历史列表，格式为 [{"role": "user", "content": "..."}, ...]
        :return: AI 返回的内容字符串，如果出错则返回 None
        """
        try:
            response = await self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
            )
            if response.choices and response.choices[0].message.content:
                return response.choices[0].message.content
            else:
                LOG.warning("OpenAI API did not return any choices or content.")
                return None
        except Exception as e:
            LOG.error(f"调用 OpenAI API 时出错: {e}")
            # 在实际应用中，可能需要更复杂的错误处理和重试逻辑
            raise
