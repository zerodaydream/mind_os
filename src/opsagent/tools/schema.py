"""JSON Schema for the model's view of a tool, with `$ref` inlined for provider compatibility."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

_DROP = frozenset({"title", "$defs", "additionalProperties"})


def model_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Inline JSON Schema for `model`. Titles and `additionalProperties` are dropped."""
    raw = model.model_json_schema()
    defs: dict[str, Any] = raw.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(v) for v in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return walk(defs[node["$ref"].rsplit("/", 1)[-1]])
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in _DROP:
                continue
            if key == "properties" and isinstance(value, dict):
                out[key] = {name: walk(sub) for name, sub in value.items()}  # names are data
            else:
                out[key] = walk(value)
        return out

    result: dict[str, Any] = walk(raw)
    return result
