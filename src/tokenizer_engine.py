"""仅加载数据文件的本地分词引擎；调用方负责隔离执行资源。"""

import base64
import copy
import json

import tiktoken
from jinja2 import TemplateError, meta
from jinja2.sandbox import ImmutableSandboxedEnvironment
from tokenizers import AddedToken, Tokenizer, decoders

ENCODERS = (
    "auto",
    "jinja",
    "deepseek_v4",
    "deepseek_v41",
    "kimi_k2",
    "kimi_k3",
    "hy3",
    "hy4",
)
ALLOWED_FILES = frozenset(
    {
        "tokenizer.json",
        "tokenizer_config.json",
        "chat_template.jinja",
        "tiktoken.model",
        "special_tokens_map.json",
        "added_tokens.json",
        "LICENSE",
        "LICENSE-MODEL",
    }
)


class TokenizerError(ValueError):
    """可返回客户端的受控错误，不包含输入正文或底层异常。"""

    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


def canonical_json(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _is_bytelevel_decoder(decoder):
    """仅展开不改变语义的单元素 Sequence，不能跳过其他解码操作。"""
    if isinstance(decoder, decoders.ByteLevel):
        return True
    if not isinstance(decoder, decoders.Sequence):
        return False
    state = json.loads(decoder.__getstate__())
    while state["type"] == "Sequence" and len(state["decoders"]) == 1:
        state = state["decoders"][0]
    return state["type"] == "ByteLevel"


def _raise_template(message):
    # 上传模板的异常文案也可能包含提示词，因此不透传。
    raise TokenizerError("消息不符合所选模板要求")


def template_environment():
    env = ImmutableSandboxedEnvironment(
        trim_blocks=True, lstrip_blocks=True, extensions=["jinja2.ext.loopcontrols"]
    )
    env.globals = {
        key: value
        for key, value in env.globals.items()
        if key in {"range", "namespace", "dict"}
    }
    env.globals.update(raise_exception=_raise_template)
    env.filters["tojson"] = lambda value, **kwargs: json.dumps(
        value, **{"ensure_ascii": False, **kwargs}
    )
    return env


def text_messages(payload):
    """保留顺序并将纯文本块转换为模板可消费的字符串。"""
    result = copy.deepcopy(payload)
    for message in result["messages"]:
        content = message.get("content", "")
        if isinstance(content, list):
            if any(
                not isinstance(block, dict)
                or block.get("type") != "text"
                or not isinstance(block.get("text"), str)
                for block in content
            ):
                raise TokenizerError("本地计数暂不支持图片、音视频或文件内容")
            message["content"] = "".join(block["text"] for block in content)
        elif content is not None and not isinstance(content, str):
            raise TokenizerError("消息内容必须是文本")
    return result


def _configuration(files):
    config = json.loads(files.get("tokenizer_config.json", b"{}"))
    if not isinstance(config, dict):
        raise TokenizerError("tokenizer_config.json 必须是对象")
    special = json.loads(files.get("special_tokens_map.json", b"{}"))
    if not isinstance(special, dict):
        raise TokenizerError("special_tokens_map.json 必须是对象")
    return {**special, **config}


def _template(files, config, selected):
    if "chat_template.jinja" in files:
        return files["chat_template.jinja"].decode("utf-8")
    template = config.get("chat_template", "")
    if isinstance(template, list):
        template = {entry["name"]: entry["template"] for entry in template}
    if isinstance(template, dict):
        name = selected or ("default" if "default" in template else "")
        if name not in template:
            raise TokenizerError("配置包含多个模板，请明确选择模板名称")
        template = template[name]
    if not isinstance(template, str):
        raise TokenizerError("聊天模板必须是字符串")
    return template


class TokenizerEngine:
    def __init__(self, files, profile, limits=None):
        from config import get_tokenizer_limits

        self.limits = limits if limits is not None else get_tokenizer_limits()
        self.profile = profile
        self.files = files
        self.encoder = profile.get("encoder", "auto")
        if self.encoder not in ENCODERS:
            raise TokenizerError("不支持的内置消息编码器")
        try:
            self.config = _configuration(files)
            self.template_text = _template(
                files, self.config, profile.get("template_name")
            )
            self.environment = template_environment()
            self.template = (
                self.environment.from_string(self.template_text)
                if self.template_text
                else None
            )
            self.template_variables = meta.find_undeclared_variables(
                self.environment.parse(self.template_text)
            )
            self.hf = None
            self.kimi = None
            if "tokenizer.json" in files:
                self.hf = Tokenizer.from_str(files["tokenizer.json"].decode("utf-8"))
                self.hf.no_truncation()
                self.hf.no_padding()
                self._added_tokens(files)
            elif "tiktoken.model" in files:
                if profile.get("format") != "kimi":
                    raise TokenizerError("tiktoken.model 必须选择 Kimi 分词配置")
                self.kimi = self._load_kimi(files["tiktoken.model"])
            else:
                raise TokenizerError("请上传 tokenizer.json 或 tiktoken.model")
            if (
                self.encoder in {"jinja", "hy3", "hy4", "kimi_k2"}
                and self.template is None
            ):
                raise TokenizerError("所选编码器需要聊天模板")
        except TokenizerError:
            raise
        except Exception as error:
            raise TokenizerError("分词资源或模板无效") from error

    def _added_tokens(self, files):
        entries = {
            int(index): entry
            for index, entry in self.config.get("added_tokens_decoder", {}).items()
        }
        for content, index in json.loads(files.get("added_tokens.json", b"{}")).items():
            if index in entries and entries[index]["content"] != content:
                raise TokenizerError("新增 token 编号冲突")
            entries.setdefault(index, {"content": content})
        for index, entry in sorted(entries.items()):
            token = AddedToken(
                **{
                    key: value
                    for key, value in entry.items()
                    if key
                    in {
                        "content",
                        "single_word",
                        "lstrip",
                        "rstrip",
                        "normalized",
                        "special",
                    }
                }
            )
            self.hf.add_tokens([token])
            if self.hf.token_to_id(entry["content"]) != index:
                raise TokenizerError("新增 token 编号与词表不一致")

    def _load_kimi(self, data):
        ranks = {}
        for line in data.splitlines():
            token, rank = line.split()
            raw = base64.b64decode(token, validate=True)
            rank = int(rank)
            if raw in ranks or rank < 0:
                raise TokenizerError("tiktoken 词表存在重复或非法条目")
            ranks[raw] = rank
        if set(ranks.values()) != set(range(len(ranks))) or any(
            bytes([i]) not in ranks for i in range(256)
        ):
            raise TokenizerError("tiktoken 词表必须包含完整字节表和连续排名")
        specials = {
            entry["content"]: int(index)
            for index, entry in self.config.get("added_tokens_decoder", {}).items()
        }
        if any(index < len(ranks) for index in specials.values()) or len(
            set(specials.values())
        ) != len(specials):
            raise TokenizerError("特殊 token 编号与词表冲突")
        for index in range(len(ranks), len(ranks) + 256):
            if index not in specials.values():
                specials[f"<|reserved_token_{index}|>"] = index
        # Kimi 官方分词器的固定预分词规则；不接受上传的可执行逻辑。
        pattern = "|".join(
            [
                r"[\p{Han}]+",
                r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}&&[^\p{Han}]]*[\p{Ll}\p{Lm}\p{Lo}\p{M}&&[^\p{Han}]]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
                r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}&&[^\p{Han}]]+[\p{Ll}\p{Lm}\p{Lo}\p{M}&&[^\p{Han}]]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
                r"\p{N}{1,3}",
                r" ?[^\s\p{L}\p{N}]+[\r\n]*",
                r"\s*[\r\n]+",
                r"\s+(?!\S)",
                r"\s+",
            ]
        )
        return tiktoken.Encoding(
            name="local_kimi",
            pat_str=pattern,
            mergeable_ranks=ranks,
            special_tokens=specials,
        )

    def count_text(self, text, *, allow_special=True):
        if not isinstance(text, str):
            raise TokenizerError("text 必须是字符串")
        if self.hf is not None:
            return len(self.hf.encode(text, add_special_tokens=False).ids)
        return len(
            self.kimi.encode(
                text,
                allowed_special="all" if allow_special else set(),
                disallowed_special=(),
            )
        )

    def encode_text(self, text):
        """返回 UTF-8 字节边界；不能单独解码可能只含半个字符的 token。"""
        if not isinstance(text, str):
            raise TokenizerError("text 必须是字符串")
        if self.hf is None:
            ids = self.kimi.encode(text, allowed_special="all", disallowed_special=())
            chunks = [self.kimi.decode_single_token_bytes(index) for index in ids]
        else:
            encoded = self.hf.encode(text, add_special_tokens=False)
            ids = encoded.ids
            if not _is_bytelevel_decoder(self.hf.decoder):
                # 非字节词表使用原文偏移，未编码的空白不归给任何 token。
                offsets = [0]
                for character in text:
                    offsets.append(offsets[-1] + len(character.encode("utf-8")))
                tokens, previous = [], 0
                for index, (start, end) in zip(ids, encoded.offsets):
                    if start < previous:
                        raise TokenizerError("所选分词器无法提供精确的 token 字节边界")
                    tokens.append({"id": index, "start": offsets[start], "end": offsets[end]})
                    previous = end
                return {"text": text, "input_tokens": len(ids), "tokens": tokens}
            # ByteLevel 使用 GPT-2 的可逆字节字母表，新增 token 不经过此映射。
            values = list(range(33, 127)) + list(range(161, 173)) + list(range(174, 256))
            alphabet = {chr(value): value for value in values}
            for value in range(256):
                if value not in values:
                    alphabet[chr(256 + len(alphabet) - len(values))] = value
            added = self.hf.get_added_tokens_decoder()
            try:
                chunks = [
                    token.encode("utf-8") if index in added
                    else bytes(alphabet[character] for character in token)
                    for index, token in zip(ids, encoded.tokens)
                ]
            except KeyError as error:
                raise TokenizerError("分词资源无法还原 token 字节") from error
        tokens, offset = [], 0
        for index, chunk in zip(ids, chunks):
            tokens.append({"id": index, "start": offset, "end": offset + len(chunk)})
            offset += len(chunk)
        try:
            decoded = b"".join(chunks).decode("utf-8")
        except UnicodeError as error:
            raise TokenizerError("分词资源无法还原有效的 UTF-8 字节") from error
        return {"text": decoded, "input_tokens": len(ids), "tokens": tokens}

    def count_messages(self, payload):
        payload = text_messages(payload)
        if self.encoder in {"deepseek_v4", "deepseek_v41", "kimi_k3"}:
            from .tokenizer_renderers import render_segments

            segments = render_segments(payload, self.encoder)
            if (
                sum(len(text.encode("utf-8")) for text, _ in segments)
                > self.limits["render_max_bytes"]
            ):
                raise TokenizerError("模板输出超过允许上限", 413)
            return (
                sum(
                    self.count_text(text, allow_special=special)
                    for text, special in segments
                ),
                "template",
            )
        if self.template is None:
            proxy = {"messages": payload["messages"]}
            if payload.get("tools"):
                proxy["tools"] = payload["tools"]
            return self.count_text(canonical_json(proxy)), "budget_v1"
        if payload.get("tools") and "tools" not in self.template_variables:
            raise TokenizerError("所选模板不支持工具定义")
        from .tokenizer_renderers import template_arguments

        kwargs = template_arguments(payload, self.encoder, self.config)
        try:
            parts, size = [], 0
            for part in self.template.generate(**kwargs):
                size += len(part.encode("utf-8"))
                if size > self.limits["render_max_bytes"]:
                    raise TokenizerError("模板输出超过允许上限", 413)
                parts.append(part)
            return self.count_text("".join(parts)), "template"
        except TemplateError as error:
            raise TokenizerError("消息模板执行失败") from error


def validate_files(files, profile=None, limits=None):
    from config import get_tokenizer_limits

    limits = limits if limits is not None else get_tokenizer_limits()
    profile = dict(profile or {"encoder": "auto"})
    if not files or not set(files).issubset(ALLOWED_FILES):
        raise TokenizerError("上传包含不支持的文件名")
    if any(not isinstance(value, bytes) for value in files.values()):
        raise TokenizerError("资源必须是文件数据")
    if sum(map(len, files.values())) > limits["upload_max_bytes"]:
        raise TokenizerError("上传资源超过配置的字节上限", 413)
    if "tokenizer.json" in files and "tiktoken.model" in files:
        raise TokenizerError("一次只能上传一种分词格式")
    engine = TokenizerEngine(files, profile, limits)
    engine.count_text("验证 tokenizer")
    profile["format"] = "hf" if engine.hf is not None else "kimi"
    profile["method"] = (
        "template"
        if engine.template
        or engine.encoder in {"deepseek_v4", "deepseek_v41", "kimi_k3"}
        else "budget_v1"
    )
    return profile
