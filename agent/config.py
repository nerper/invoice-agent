"""Settings: config.yaml + .env loaded once into a frozen pydantic model."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parent.parent


class UISettings(BaseModel):
    model_config = ConfigDict(frozen=True)
    host: str = "127.0.0.1"
    port: int = 8765


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    workspace_dir: str = "workspace"
    approval_limit_php: int = 100000
    price_tolerance_pct: float = 1.0
    qty_tolerance: int = 0
    max_steps_per_invoice: int = 8
    max_cost_usd_per_invoice: float = 0.02
    max_cost_usd_per_run: float = 0.50
    temperature: float = 0
    llm_timeout_s: int = 60
    llm_max_retries: int = 2
    fallback_usd_per_mtok_in: float = 1.0
    fallback_usd_per_mtok_out: float = 4.0
    watch_interval_s: float = 2
    ui: UISettings = UISettings()
    api_key: str = ""
    model: str = ""

    @property
    def workspace(self) -> Path:
        p = Path(self.workspace_dir)
        return p if p.is_absolute() else ROOT / p


def load_settings(config_path: Path | None = None) -> Settings:
    load_dotenv(ROOT / ".env")
    raw = yaml.safe_load((config_path or ROOT / "config.yaml").read_text()) or {}
    raw["api_key"] = os.environ.get("OPENROUTER_API_KEY", "")
    raw["model"] = os.environ.get("MODEL", "")
    return Settings(**raw)
