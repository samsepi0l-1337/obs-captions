"""Core schema types shared by :mod:`obs_captions.settings_fields` and
:mod:`obs_captions.settings_schema`.

Split out on its own (no dependency on either sibling module) so that
``settings_fields`` and ``settings_schema`` can both import from here without
ever importing each other — a strict one-way dependency graph that cannot
circular-import regardless of which module a caller imports first.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Widget = Literal["text", "choice", "int", "float", "bool", "path", "secret", "list"]


@dataclass(frozen=True)
class FieldSpec:
    """Describe how one application setting is presented.

    ``help`` is a beginner-friendly one-line hint (Korean). ``engines`` marks a
    field as engine-specific: when empty the field is always shown; when set the
    field is only relevant for those STT engines (used by the GUI to show/hide
    provider fields and API keys as the selected engine changes).
    """

    key: str
    label: str
    widget: Widget
    section: str
    applies_to: frozenset[str]
    choices: tuple[str, ...] = ()
    env_var: str | None = None
    help: str = ""
    engines: tuple[str, ...] = ()


ENGINES: tuple[str, ...] = (
    "local",
    "openai",
    "elevenlabs",
    "google",
    "xai",
    "deepgram",
    "assemblyai",
    "azure",
    "openrouter",
    "replicate",
    "groq",
)
LOCAL_MODEL_SIZES: tuple[str, ...] = ("tiny", "base", "small", "medium", "large-v3")

__all__ = ["ENGINES", "FieldSpec", "LOCAL_MODEL_SIZES", "Widget"]
