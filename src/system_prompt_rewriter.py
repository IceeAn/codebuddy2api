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


def _rewrite_text(text: str) -> str:
    return text.replace(
        _CLAUDE_CODE_IDENTITY,
        _NEUTRAL_CLI_IDENTITY,
    ).replace(
        _MAIN_BRANCH_FINGERPRINT,
        "Main branch:",
    )


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
