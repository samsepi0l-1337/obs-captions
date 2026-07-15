"""Load and save GUI settings across ``config.toml`` and ``.env``.

The GUI works with a *flat* dict keyed by :class:`~obs_captions.settings_schema.FieldSpec`
keys (dotted config paths, e.g. ``"local.model_size"``). Secret fields are keyed
``"env:<VAR>"`` and are persisted only to ``.env`` — never to the TOML file.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import BaseModel

from obs_captions.config import AppConfig, load_config
from obs_captions.settings_schema import FIELDS

_ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")


def _load_appconfig(config_path: str | Path | None) -> AppConfig:
    if config_path is None:
        return AppConfig()
    path = Path(config_path)
    return load_config(str(path)) if path.is_file() else AppConfig()


def _get_by_path(root: Any, dotted: str) -> Any:
    """Resolve ``dotted`` against nested models/dicts; missing -> ``None``."""
    node: Any = root
    for part in dotted.split("."):
        if node is None:
            return None
        if isinstance(node, dict):
            node = node.get(part)
        else:
            node = getattr(node, part, None)
    if isinstance(node, BaseModel):
        return node.model_dump(mode="json")
    if isinstance(node, list):
        return [x.model_dump(mode="json") if isinstance(x, BaseModel) else x for x in node]
    return node


def _read_env(env_path: str | Path | None) -> dict[str, str]:
    values: dict[str, str] = {}
    if env_path is None:
        return values
    path = Path(env_path)
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def load_settings(
    config_path: str | Path | None, env_path: str | Path | None
) -> dict[str, Any]:
    """Return a flat ``{key: value}`` dict for every schema field.

    Missing files fall back to :class:`AppConfig` defaults. Secret fields
    resolve from ``.env`` (empty string when unset).
    """
    cfg = _load_appconfig(config_path)
    env = _read_env(env_path)
    values: dict[str, Any] = {}
    for field in FIELDS:
        if field.widget == "secret":
            assert field.env_var is not None
            values[f"env:{field.env_var}"] = env.get(field.env_var, "")
            continue
        resolved = _get_by_path(cfg, field.key)
        values[field.key] = "" if resolved is None else resolved
    return values


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def _unflatten(values: dict[str, Any]) -> dict[str, Any]:
    """Build a nested dict from non-secret, non-empty flat values."""
    nested: dict[str, Any] = {}
    for field in FIELDS:
        if field.widget == "secret":
            continue
        value = values.get(field.key)
        if _is_empty(value):
            continue
        node = nested
        parts = field.key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return nested


def _write_config(values: dict[str, Any], config_path: str | Path) -> None:
    nested = _unflatten(values)
    # Validate + normalise through AppConfig so the written TOML is always
    # loadable and only carries non-default values.
    cfg = AppConfig(**nested)
    data = cfg.model_dump(mode="json", exclude_defaults=True)
    with Path(config_path).open("wb") as fh:
        tomli_w.dump(data, fh)


def _write_env(values: dict[str, Any], env_path: str | Path) -> None:
    secrets = {
        field.env_var: str(values[f"env:{field.env_var}"])
        for field in FIELDS
        if field.widget == "secret"
        and field.env_var is not None
        and not _is_empty(values.get(f"env:{field.env_var}"))
    }
    if not secrets:
        return
    path = Path(env_path)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    out: list[str] = []
    seen: set[str] = set()
    for line in existing.splitlines():
        match = _ENV_LINE.match(line)
        if match and match.group(1) in secrets:
            key = match.group(1)
            out.append(f"{key}={secrets[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in secrets.items():
        if key not in seen:
            out.append(f"{key}={value}")
    text = "\n".join(out)
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


def save_settings(
    values: dict[str, Any],
    config_path: str | Path | None,
    env_path: str | Path | None,
) -> None:
    """Persist ``values``: non-secrets to TOML, secrets to ``.env``."""
    if config_path is not None:
        _write_config(values, config_path)
    if env_path is not None:
        _write_env(values, env_path)


__all__ = ["load_settings", "save_settings"]
