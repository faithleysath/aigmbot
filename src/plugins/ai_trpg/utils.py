# src/plugins/ai_trpg/utils.py
from typing import Any
import base64


EMOJI = {
    # 主贴选项
    "A": 127822,
    "B": 9973,
    "C": 128663,
    "D": 128054,
    "E": 127859,
    "F": 128293,
    "G": 128123,
    # 管理员确认/否决（主贴）
    "CONFIRM": 127881,  # 🎉
    "DENY": 128560,  # 😰
    "RETRACT": 10060,  # ❌
    # 自定义输入投票
    "YAY": 127881,  # 🎉
    "NAY": 128560,  # 😰
    "CANCEL": 10060,  # ❌
    # 频道繁忙
    "COFFEE": 9749,  # ☕
}


def bytes_to_base64(b: bytes) -> str:
    """将字节数据转换为Base64字符串"""
    return base64.b64encode(b).decode("utf-8")
