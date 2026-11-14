"""Resolve .env + config when mindos runs outside the repo cwd (global install)."""

from __future__ import annotations

import os
from pathlib import Path

from backend.common.ssl_env import fix_ssl_cert_env


def bootstrap_env() -> Path | None:
    """Load MINDOS_HOME/.env into os.environ and set config path. Returns home or None."""
    home_raw = os.environ.get("MINDOS_HOME", "").strip()
    if not home_raw:
        cwd = Path.cwd()
        if (cwd / "backend" / "config.json").is_file() and (cwd / ".env").is_file():
            home_raw = str(cwd)
        elif (cwd.parent / "backend" / "config.json").is_file():
            home_raw = str(cwd.parent)
    if not home_raw:
        return None
    home = Path(home_raw).expanduser().resolve()
    os.environ.setdefault("MINDOS_HOME", str(home))
    os.environ.setdefault("MINDOS_CONFIG_PATH", str(home / "backend" / "config.json"))
    env_path = home / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, val = stripped.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)
    vault = home / "data" / "vault"
    os.environ.setdefault("MINDOS_VAULT_PATH", str(vault))
    fix_ssl_cert_env()
    return home
