"""Read and write .env file safely while preserving chmod 600 permissions."""

import os
import subprocess
from pathlib import Path
from typing import Dict
from dotenv import dotenv_values

from app.config import find_env_file


def read_env_dict(env_path: str | None = None) -> Dict[str, str]:
    target = Path(env_path or find_env_file())
    if not target.is_file():
        return {}
    raw = dotenv_values(str(target))
    return {k: (v or "") for k, v in raw.items() if k is not None}


def write_env_key(key: str, value: str, env_path: str | None = None) -> Path:
    target = Path(env_path or find_env_file())
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    found = False
    if target.is_file():
        for line in target.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                k, _ = stripped.split("=", 1)
                if k.strip() == key:
                    lines.append(f"{key}={value}")
                    found = True
                    continue
            lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(target, 0o600)
    except OSError:
        pass
    return target


def restart_systemd_service_if_active(service_name: str = "anonbot") -> bool:
    try:
        res = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 or Path(f"/etc/systemd/system/{service_name}.service").is_file():
            subprocess.run(["systemctl", "restart", service_name], check=False)
            return True
    except FileNotFoundError:
        pass
    return False
