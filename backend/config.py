from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


ROOT_DIR = Path(__file__).resolve().parents[1]


def load_env_file(path: Optional[Path] = None) -> None:
    """Load a local .env without overriding explicitly supplied environment values."""
    env_path = path or ROOT_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def resolve_sqlite_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError("Stage 01 only supports sqlite:/// database URLs")
    raw_path = database_url.removeprefix(prefix)
    candidate = Path(raw_path)
    return candidate if candidate.is_absolute() else ROOT_DIR / candidate


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///data/baochuang.db"
    frontend_dist: Path = ROOT_DIR / "dist"

    @property
    def database_path(self) -> Path:
        return resolve_sqlite_path(self.database_url)

    @classmethod
    def from_environment(cls) -> "Settings":
        load_env_file()
        return cls(database_url=os.getenv("APP_DATABASE_URL", cls.database_url))
