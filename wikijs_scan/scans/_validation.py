"""scan 选项的基础校验，不包含任何扫描规则。"""

from collections.abc import Set


def validate_keys(options: dict, allowed: Set[str]) -> None:
    """拒绝错误类型与拼错的配置键，避免配置看似生效却被忽略。"""
    if not isinstance(options, dict):
        raise ValueError("options 必须是 JSON 对象")
    unknown = options.keys() - allowed
    if unknown:
        raise ValueError(f"不支持的 options 字段：{', '.join(sorted(unknown))}")
