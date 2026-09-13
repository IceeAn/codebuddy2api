"""Kimi K2 官方 TypeScript 工具声明格式的本地实现。"""

import json

from .tokenizer_engine import TokenizerError


def description(text, indent=""):
    return "\n".join(f"{indent}// {line}" if line else "" for line in text.split("\n"))


def _docs(schema, indent):
    if isinstance(schema, bool):
        return ""
    output = (
        description(schema["description"], indent) + "\n"
        if schema.get("description")
        else ""
    )
    kind = schema.get("type")
    keys = {
        "string": ("maxLength", "minLength", "pattern"),
        "number": ("maximum", "minimum"),
        "integer": ("maximum", "minimum"),
        "array": ("minItems", "maxItems"),
    }.get(str(kind), ())
    constraints = ", ".join(
        f"{key}: {schema[key]}" for key in sorted(keys) if key in schema
    )
    if constraints:
        output += indent + "// " + constraints + "\n"
    return output


class TypeScriptSchema:
    def __init__(self):
        self.definitions = {}
        self.self_ref = False

    def fields(self, schema, indent, *, sort_required=True):
        self.definitions.update(schema.get("$defs", {}))
        required = schema.get("required", [])
        properties = schema.get("properties", {})
        names = (
            sorted(properties, key=lambda key: (key not in required, key))
            if sort_required
            else list(properties)
        )
        fields = []
        for name in names:
            prop = properties[name]
            default = prop.get("default") if isinstance(prop, dict) else None
            text = _docs(prop, indent)
            if default is not None:
                value = (
                    repr(default)
                    if isinstance(default, (int, float, bool))
                    else json.dumps(default, ensure_ascii=False)
                )
                text += f"{indent}// Default: {value}\n"
            text += f"{indent}{name}{'' if name in required else '?'}: {self.render(prop, indent)}"
            fields.append(text)
        if (
            "additionalProperties" in schema
            and schema["additionalProperties"] is not None
        ):
            additional = schema["additionalProperties"]
            kind = (
                "any"
                if additional is True
                else "never" if additional is False else self.render(additional, indent)
            )
            fields.append(f"{indent}[k: string]: {kind}")
        return fields

    def render(self, schema, indent=""):
        if isinstance(schema, bool):
            return "any" if schema else "null"
        if "$ref" in schema:
            reference = schema["$ref"]
            if reference == "#":
                self.self_ref = True
                return "parameters"
            if (
                not reference.startswith("#/$defs/")
                or reference.split("/")[-1] not in self.definitions
            ):
                raise TokenizerError("工具 schema 引用无法解析")
            return reference.split("/")[-1]
        if "anyOf" in schema:
            return " | ".join(self.render(item, indent) for item in schema["anyOf"])
        if "enum" in schema:
            return " | ".join(
                f'"{item}"' if isinstance(item, str) else str(item)
                for item in schema["enum"]
            )
        kind = schema.get("type")
        if isinstance(kind, list):
            mapping = {
                "string": "string",
                "number": "number",
                "integer": "number",
                "boolean": "boolean",
                "null": "null",
                "object": "{}",
                "array": "Array<any>",
            }
            return " | ".join(mapping[item] for item in kind)
        if kind == "object":
            fields = self.fields(schema, indent + "  ")
            return "{\n" + ",\n".join(fields) + "\n" + indent + "}" if fields else "{}"
        if kind == "array":
            item = schema.get("items") or {}
            docs = _docs(item, indent + "  ")
            if docs:
                return (
                    "Array<\n"
                    + docs
                    + indent
                    + "  "
                    + self.render(item, indent + "  ")
                    + "\n"
                    + indent
                    + ">"
                )
            return "Array<" + self.render(item, indent) + ">"
        if kind is not None:
            return "number" if kind == "integer" else kind
        if schema == {}:
            return "any"
        raise TokenizerError("Kimi 工具声明包含无法转换的 schema")


def encode_tools(tools):
    functions = []
    for tool in tools:
        function = tool["function"]
        parameters = function.get("parameters") or {}
        registry = TypeScriptSchema()
        rendered = registry.render({**parameters, "type": "object"})
        interfaces = []
        root_name = "parameters" if registry.self_ref else None
        if root_name:
            fields = registry.fields(parameters, "  ", sort_required=False)
            body = "\n" + ",\n".join(fields) + "\n" if fields else ""
            interfaces.append("interface parameters {" + body + "}")
        for name, schema in list(registry.definitions.items()):
            prefix = (
                description(schema["description"]) + "\n"
                if schema.get("description")
                else ""
            )
            interfaces.append(prefix + f"interface {name} " + registry.render(schema))
        if function.get("description"):
            interfaces.append(description(function["description"]))
        interfaces.append(
            f'type {function["name"]} = (_: {root_name or rendered}) => any;'
        )
        functions.append("\n".join(interfaces))
    return (
        "# Tools\n\n## functions\nnamespace functions {\n"
        + "\n".join(functions)
        + "\n}\n"
        if functions
        else ""
    )
