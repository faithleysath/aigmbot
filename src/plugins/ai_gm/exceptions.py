
class TipChangedError(RuntimeError):
    """在游戏状态推进期间，分支的 tip round id 发生变化时引发此异常。"""
    pass
