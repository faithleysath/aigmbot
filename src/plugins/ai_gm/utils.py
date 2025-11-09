import base64

# 导入常量，保持向后兼容
from .constants import EMOJI


def bytes_to_base64(b: bytes) -> str:
    """将字节数据转换为Base64字符串"""
    return base64.b64encode(b).decode("utf-8")
