"""Load and save GUI settings across ``config.toml`` and ``.env``.

The GUI works with a *flat* dict keyed by :class:`~obs_captions.settings_schema.FieldSpec`
keys (dotted config paths, e.g. ``"local.model_size"``). Secret fields are keyed
``"env:<VAR>"`` and are persisted only to ``.env`` — never to the TOML file.
"""

from __future__ import annotations

import re
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import BaseModel

from obs_captions.config import AppConfig, load_config
from obs_captions.settings_schema import FIELDS

# Match ``KEY=...`` allowing whitespace around ``=`` so upsert/delete lines up
# with the whitespace-tolerant reader in :func:`_read_env`.
_ENV_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=")


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


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``overlay`` into ``base`` (overlay wins on leaves)."""
    for key, value in overlay.items():
        existing = base.get(key)
        if isinstance(value, dict) and isinstance(existing, dict):
            _deep_merge(existing, value)
        else:
            base[key] = value
    return base


def _prune_path(node: dict[str, Any], parts: list[str]) -> None:
    """Remove the dotted-path leaf ``parts`` from ``node``, if present.

    Cleans up any parent dict left empty by the removal, so a fully-GUI-owned
    branch (e.g. every ``providers.deepgram.*`` key) disappears entirely
    rather than surviving as ``{}``.
    """
    if not parts:
        return
    head, *rest = parts
    if head not in node:
        return
    if not rest:
        del node[head]
        return
    child = node[head]
    if not isinstance(child, dict):
        return
    _prune_path(child, rest)
    if not child:
        del node[head]


def _prune_gui_managed_paths(existing: dict[str, Any]) -> dict[str, Any]:
    """Strip every non-secret FIELDS path out of ``existing``.

    The GUI fully owns these paths: a value it left blank means "reset to
    default", not "leave whatever was on disk". What remains after pruning is
    exactly the set of config fields the GUI never exposes (e.g.
    ``providers.deepgram.region``), which must survive a save untouched.
    """
    pruned = deepcopy(existing)
    for field in FIELDS:
        if field.widget == "secret":
            continue
        _prune_path(pruned, field.key.split("."))
    return pruned


def _write_config(values: dict[str, Any], config_path: str | Path) -> None:
    nested = _unflatten(values)
    path = Path(config_path)
    if path.is_file():
        with path.open("rb") as fh:
            existing = tomllib.load(fh)
    else:
        existing = {}
    # Base = only the fields the GUI does not manage (preserved as-is).
    # GUI-managed fields come exclusively from `nested` — a blank GUI field
    # is an explicit reset to default, not a carry-over of the old value.
    base = _prune_gui_managed_paths(existing)
    merged = _deep_merge(base, nested)
    # Validate + normalise through AppConfig so the written TOML is always
    # loadable and only carries non-default values.
    cfg = AppConfig(**merged)
    data = cfg.model_dump(mode="json", exclude_defaults=True)
    with path.open("wb") as fh:
        tomli_w.dump(data, fh)


def _write_env(values: dict[str, Any], env_path: str | Path) -> None:
    # A secret field present in ``values`` with a value = upsert; present but
    # empty = delete that key from ``.env``; absent = leave untouched.
    upserts: dict[str, str] = {}
    deletes: set[str] = set()
    for field in FIELDS:
        if field.widget != "secret" or field.env_var is None:
            continue
        key = f"env:{field.env_var}"
        if key not in values:
            continue
        if _is_empty(values[key]):
            deletes.add(field.env_var)
        else:
            upserts[field.env_var] = str(values[key])
    if not upserts and not deletes:
        return
    path = Path(env_path)
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    out: list[str] = []
    seen: set[str] = set()
    for line in existing.splitlines():
        match = _ENV_LINE.match(line)
        if match and match.group(1) in deletes:
            continue
        if match and match.group(1) in upserts:
            key = match.group(1)
            out.append(f"{key}={upserts[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in upserts.items():
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
