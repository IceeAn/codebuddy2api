"""
关键词替换工具模块 - 统一处理关键词替换逻辑
防止CodeBuddy检测到竞争对手关键词
"""
import logging
from typing import Any

logger = logging.getLogger(__name__)


def apply_keyword_replacement(text: Any) -> Any:
    """
    统一的关键词替换函数

    Args:
        text: 需要处理的文本内容

    Returns:
        Any: 字符串会被替换，其他类型原样返回
    """
    if not isinstance(text, str):
        return text

    # 定义替换规则
    replacements = {
        "Claude Code": "CodeBuddy Code",
        "Anthropic's official CLI for Claude": "Tencent's official CLI for CodeBuddy",
        "Claude": "CodeBuddy",
        "Anthropic": "Tencent",
        "https://github.com/anthropics/claude-code/issues": "https://cnb.cool/codebuddy/codebuddy-code/-/issues"
    }

    original_text = text

    # 应用所有替换规则
    for old_keyword, new_keyword in replacements.items():
        text = text.replace(old_keyword, new_keyword)

    # 记录替换日志（仅在调试模式下）
    if text != original_text:
        logger.debug(
            f"[KEYWORD_REPLACE] Applied keyword replacements, original length: {len(original_text)}, new length: {len(text)}")

    return text


def apply_keyword_replacement_to_system_message(content: Any) -> Any:
    """
    专门用于处理系统消息的关键词替换
    支持字符串和复杂结构的content

    Args:
        content: 消息内容，可能是字符串或列表结构

    Returns:
        Any: 处理后的内容，保留原始 content 类型
    """
    if isinstance(content, str):
        return apply_keyword_replacement(content)
    elif isinstance(content, list):
        # 处理复杂结构的系统消息
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                item["text"] = apply_keyword_replacement(item.get("text", ""))
        return content
    else:
        return content
