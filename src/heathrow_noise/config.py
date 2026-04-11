"""Configuration loading — YAML file with environment variable overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).parent.parent.parent
DEFAULT_CONFIG = BASE_DIR / "config" / "config.yaml"


class Config:
    """Dot-notation config with env var overrides (HEATHROW_NOISE__KEY__SUBKEY).

    Exposes a .data property (raw dict) so ha_mqtt_publisher Entity/Device
    constructors that expect config.data work correctly.
    """

    def __init__(self, path: Path | None = None) -> None:
        env_cfg = os.environ.get("HEATHROW_NOISE_CONFIG", str(DEFAULT_CONFIG))
        config_path = path or Path(env_cfg)
        with open(config_path) as f:
            self._data: dict[str, Any] = yaml.safe_load(f) or {}

    @property
    def data(self) -> dict[str, Any]:
        """Raw config dict — required by ha_mqtt_publisher Entity constructor."""
        return self._data

    def get(self, key: str, default: Any = None) -> Any:
        """Fetch by dot-notation key, e.g. 'mqtt.broker_url'."""
        env_key = "HEATHROW_NOISE__" + key.upper().replace(".", "__")
        if env_key in os.environ:
            return os.environ[env_key]
        parts = key.split(".")
        obj: Any = self._data
        for part in parts:
            if not isinstance(obj, dict) or part not in obj:
                return default
            obj = obj[part]
        return obj

    def get_int(self, key: str, default: int = 0) -> int:
        return int(self.get(key, default))

    def get_float(self, key: str, default: float = 0.0) -> float:
        return float(self.get(key, default))

    def get_bool(self, key: str, default: bool = False) -> bool:
        val = self.get(key, default)
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("true", "1", "yes")
