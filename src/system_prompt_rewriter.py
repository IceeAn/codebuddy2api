"""对上游已确认误判的 system 提示词指纹执行精确改写。"""

from typing import Any


_CLAUDE_CODE_IDENTITY = (
    "You are Claude Code, Anthropic's official CLI for Claude."
)
_NEUTRAL_CLI_IDENTITY = (
    "You are an interactive software engineering assistant operating in a "
    "command-line environment."
)
_MAIN_BRANCH_FINGERPRINT = (
    "Main branch (you will usually use this for PRs):"
)
_ATTRIBUTION_HEADER_PREFIX = "x-anthropic-billing-header:"


def _rewrite_text(text: str) -> str:
    return text.replace(
        _CLAUDE_CODE_IDENTITY,
        _NEUTRAL_CLI_IDENTITY,
    ).replace(
        _MAIN_BRANCH_FINGERPRINT,
        "Main branch:",
    )


def _strip_attribution_header_line(text: str) -> tuple[str, bool]:
    if not text.startswith(_ATTRIBUTION_HEADER_PREFIX):
        return text, False
    line_end = text.find("\n")
    if line_end < 0:
        return "", True
    return text[line_end + 1:], True


def _strip_attribution_header_content(content: Any) -> tuple[Any, bool]:
    if isinstance(content, str):
        text, stripped = _strip_attribution_header_line(content)
        return text, stripped and not text
    if not isinstance(content, list):
        return content, False

    stripped_any = False
    retained_blocks = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            retained_blocks.append(block)
            continue
        text = block.get("text")
        if not isinstance(text, str):
            retained_blocks.append(block)
            continue
        text, stripped = _strip_attribution_header_line(text)
        if not stripped:
            retained_blocks.append(block)
            continue
        stripped_any = True
        if text:
            block["text"] = text
            retained_blocks.append(block)

    content[:] = retained_blocks
    return content, stripped_any and not content


def rewrite_system_prompt_content(content: Any) -> Any:
    """精确改写 system 字符串或文本块，其他内容原样保留。"""
    if isinstance(content, str):
        return _rewrite_text(content)
    if not isinstance(content, list):
        return content

    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        text = block.get("text")
        if isinstance(text, str):
            block["text"] = _rewrite_text(text)
    return content


def rewrite_system_prompt_messages(messages: list[Any]) -> None:
    """改写 system 消息，并移除独立的 Claude Code attribution 块。"""
    retained_messages = []
    for message in messages:
        if message.get("role") != "system":
            retained_messages.append(message)
            continue
        content, remove_message = _strip_attribution_header_content(
            message.get("content")
        )
        if remove_message:
            continue
        message["content"] = rewrite_system_prompt_content(content)
        retained_messages.append(message)
    messages[:] = retained_messages
