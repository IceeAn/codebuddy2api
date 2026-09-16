"""计数准备、用户资源快照与协议无关结果。"""

from starlette.concurrency import run_in_threadpool

from .anthropic_compat import translate_anthropic_count_request
from .request_processor import apply_request_policies, strip_model_namespace
from .tokenizer_engine import TokenizerError
from .tokenizer_runtime import tokenizer_runtime
from .tokenizer_store import get_tokenizer_store


async def count_tokens(body, user, *, messages=False, visualize=False):
    import config

    if not isinstance(body, dict):
        raise TokenizerError("请求体必须是 JSON 对象")
    if messages:
        payload = translate_anthropic_count_request(body)
        apply_request_policies(payload, user)
        model, value = payload["model"], payload
    else:
        model = body.get("model")
        if not isinstance(model, str) or not model.strip():
            raise TokenizerError("model 必须是非空字符串")
        if config.get_strip_model_namespace(user):
            model = strip_model_namespace(model)
        value = body.get("text")
        if not isinstance(value, str):
            raise TokenizerError("text 必须是字符串")
        if (
            visualize
            and len(value.encode("utf-8")) > config.get_tokenizer_limits()["visualize_max_bytes"]
        ):
            raise TokenizerError("可视化输入文本超过允许的字节上限", 413)
    snapshot = {}

    def resolve():
        snapshot.update(get_tokenizer_store().resolve(user.username, model))
        snapshot["cache_key"] = (user.username, snapshot["revision"])
        return snapshot

    count, method = await run_in_threadpool(
        tokenizer_runtime.execute,
        "encode" if visualize else "messages" if messages else "text",
        resolve,
        value,
    )
    return count if visualize else {"input_tokens": count}, {
        "X-Tokenizer-Method": method,
        "X-Tokenizer-Source": snapshot["source"],
        "X-Tokenizer-Revision": snapshot["revision"],
    }
