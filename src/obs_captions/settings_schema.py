"""Presentation metadata shared by the desktop GUI and OBS plugin.

Pure facade module: ``FieldSpec``/``Widget``/``ENGINES``/``LOCAL_MODEL_SIZES``
live in :mod:`obs_captions.settings_types`; the (large) concrete ``FIELDS``
list lives in :mod:`obs_captions.settings_fields`. Both are re-exported here
for backward compatibility with existing consumers
(``from obs_captions.settings_schema import FIELDS, FieldSpec, ...``).

This module imports from both siblings but neither sibling imports this
module, so importing ``settings_fields`` or ``settings_types`` directly
(before ``settings_schema``) is always safe — no circular import.
"""

from __future__ import annotations

from obs_captions.settings_fields import FIELDS
from obs_captions.settings_types import ENGINES, LOCAL_MODEL_SIZES, FieldSpec, Widget

__all__ = ["ENGINES", "FIELDS", "LOCAL_MODEL_SIZES", "FieldSpec", "Widget"]
