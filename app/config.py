"""Load and validate config.yaml into typed models."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, Field

# Project root = parent of the app/ package.
ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"


class Search(BaseModel):
    query: str
    location: str = ""
    country: str = "usa"
    sites: List[str] = Field(default_factory=lambda: ["indeed"])
    results_wanted: int = 40
    hours_old: Optional[int] = None
    is_remote: bool = False


class SeekSearch(BaseModel):
    query: str
    location: str = ""
    pages: int = 2


class SeekConfig(BaseModel):
    enabled: bool = False
    searches: List[SeekSearch] = Field(default_factory=list)


class AtsConfig(BaseModel):
    enabled: bool = False
    greenhouse: List[str] = Field(default_factory=list)
    lever: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)


class ScheduleConfig(BaseModel):
    enabled: bool = False
    time: str = "07:00"
    timezone: str = "UTC"


class Config(BaseModel):
    database: str = "jobs.db"
    searches: List[Search] = Field(default_factory=list)
    seek: SeekConfig = Field(default_factory=SeekConfig)
    ats: AtsConfig = Field(default_factory=AtsConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)

    @property
    def database_path(self) -> Path:
        p = Path(self.database)
        return p if p.is_absolute() else ROOT / p


def load_config(path: Path = CONFIG_PATH) -> Config:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return Config(**raw)


@lru_cache(maxsize=1)
def get_config() -> Config:
    return load_config()
