from __future__ import annotations

import json
from typing import Any


def _escape_toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        raise ValueError("None values should be omitted before serializing.")
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        return _escape_toml_string(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"Unsupported TOML value type: {type(value).__name__!r}")


def _is_list_of_dicts(value: list[Any]) -> bool:
    return bool(value) and all(isinstance(item, dict) for item in value)


def _write_toml_section(
    lines: list[str],
    section: dict[str, Any],
    section_path: tuple[str, ...],
    *,
    emit_header: bool,
) -> None:
    scalar_items: list[tuple[str, Any]] = []
    table_items: list[tuple[str, list[dict[str, Any]]]] = []
    nested_items: list[tuple[str, dict[str, Any]]] = []

    for key, value in section.items():
        if value is None:
            continue
        if isinstance(value, dict):
            nested_items.append((key, value))
        elif isinstance(value, list) and _is_list_of_dicts(value):
            table_items.append((key, value))
        else:
            scalar_items.append((key, value))

    if emit_header and section_path:
        lines.append(f"[{'.'.join(section_path)}]")

    for key, value in scalar_items:
        lines.append(f"{key} = {_toml_value(value)}")

    for key, value in nested_items:
        _write_toml_table(lines, value, section_path + (key,))

    for key, value in table_items:
        for item in value:
            lines.append(f"[[{'.'.join(section_path + (key,))}]]")
            if item:
                _write_toml_section(lines, item, section_path + (key,), emit_header=False)
            lines.append("")


def _write_toml_table(
    lines: list[str],
    table: dict[str, Any],
    path: tuple[str, ...],
) -> None:
    _write_toml_section(lines, table, path, emit_header=True)


def _dump_toml(data: dict[str, Any]) -> str:
    lines: list[str] = []
    root_order = (
        "engine",
        "language",
        "local",
        "audio",
        "server",
        "overlay",
        "providers",
        "obs",
        "text",
        "export",
    )
    seen_root: set[str] = set()
    ordered_keys: list[str] = []
    for key in root_order:
        if key in data:
            ordered_keys.append(key)
            seen_root.add(key)
    for key in data:
        if key not in seen_root:
            ordered_keys.append(key)

    root_scalars: dict[str, Any] = {}
    root_tables: list[tuple[str, Any]] = []
    for key in ordered_keys:
        value = data[key]
        if value is None:
            continue
        if isinstance(value, dict) or (isinstance(value, list) and _is_list_of_dicts(value)):
            root_tables.append((key, value))
        else:
            root_scalars[key] = value

    for key, value in root_scalars.items():
        lines.append(f"{key} = {_toml_value(value)}")
    if root_scalars:
        lines.append("")

    for key, value in root_tables:
        if isinstance(value, dict):
            _write_toml_table(lines, value, (key,))
        else:
            for item in value:
                lines.append(f"[[{key}]]")
                if item:
                    _write_toml_section(lines, item, (key,), emit_header=False)
                lines.append("")

    return "\n".join(lines).rstrip() + "\n"
