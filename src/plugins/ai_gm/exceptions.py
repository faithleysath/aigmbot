# Base exception for all AI GM plugin errors
class AIGMError(Exception):
    """AI GM 插件的基础异常类"""
    pass


# Database related exceptions
class DatabaseError(AIGMError):
    """数据库操作相关的异常"""
    pass


class GameNotFoundError(DatabaseError):
    """游戏未找到"""
    pass


class BranchNotFoundError(DatabaseError):
    """分支未找到"""
    pass


class RoundNotFoundError(DatabaseError):
    """回合未找到"""
    pass


class TagNotFoundError(DatabaseError):
    """标签未找到"""
    pass


# Game state exceptions
class GameStateError(AIGMError):
    """游戏状态相关的异常"""
    pass


class GameFrozenError(GameStateError):
    """游戏处于冻结状态"""
    pass


class TipChangedError(GameStateError):
    """在游戏状态推进期间，分支的 tip round id 发生变化时引发此异常。"""
    pass


class InvalidBranchOperationError(GameStateError):
    """无效的分支操作（如删除 HEAD 分支）"""
    pass


# Validation exceptions
class ValidationError(AIGMError):
    """输入验证相关的异常"""
    pass


class InvalidNameError(ValidationError):
    """无效的名称（分支名、标签名等）"""
    pass


# Permission exceptions
class AIGMPermissionError(AIGMError):
    """权限不足"""
    pass


# LLM API exceptions
class LLMError(AIGMError):
    """LLM API 相关的异常"""
    pass


class LLMTimeoutError(LLMError):
    """LLM API 超时"""
    pass


class LLMRateLimitError(LLMError):
    """LLM API 速率限制"""
    pass
