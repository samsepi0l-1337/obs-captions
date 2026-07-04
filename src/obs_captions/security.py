from __future__ import annotations

import os
import secrets
from pathlib import Path


def _ensure_private_directory(directory: Path) -> None:
    already_exists = directory.exists()
    directory.mkdir(parents=True, exist_ok=True)
    if not already_exists:
        os.chmod(directory, 0o700)


def ensure_private_file(path: Path, *, mode: int = 0o600) -> None:
    path = Path(path)
    _ensure_private_directory(path.parent)
    if not path.exists():
        os.close(os.open(str(path), os.O_CREAT | os.O_WRONLY, mode))
    os.chmod(path, mode)


def resolve_session_token_path(home_dir: Path | None = None) -> Path:
    base = Path.home() if home_dir is None else Path(home_dir)
    return base / ".obs-captions" / "session-token"


def load_or_create_session_token(home_dir: Path | None = None) -> str:
    token_path = resolve_session_token_path(home_dir)
    if token_path.exists():
        token = token_path.read_text(encoding="utf-8").strip()
        if token:
            os.chmod(token_path, 0o600)
            return token
    ensure_private_file(token_path)
    token = secrets.token_urlsafe(32)
    token_path.write_text(token, encoding="utf-8")
    os.chmod(token_path, 0o600)
    return token
