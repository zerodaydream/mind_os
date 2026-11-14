from functools import lru_cache
import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ServiceEntry(BaseModel):
    name: str
    enabled: bool = True
    required: bool = False
    settings: dict[str, Any] = Field(default_factory=dict)


class ConfigResponse(BaseModel):
    environment: str = "local"
    services: list[ServiceEntry] = Field(default_factory=list)

    def enabled_names(self) -> set[str]:
        return {entry.name for entry in self.services if entry.enabled}

    def is_enabled(self, name: str) -> bool:
        return name in self.enabled_names()

    @model_validator(mode="before")
    @classmethod
    def _from_legacy_bools(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if "services" in data and isinstance(data["services"], list):
            return data
        legacy = data.get("services")
        if not isinstance(legacy, dict):
            return data
        names = (
            "llm",
            "retrieval",
            "graph",
            "agents",
            "integrations",
            "ingest",
            "commitments",
            "tensions",
            "memories",
        )
        return {
            "environment": data.get("environment", "local"),
            "services": [
                {
                    "name": name,
                    "enabled": bool(
                        legacy.get(
                            name, name in ("ingest", "commitments", "tensions", "memories", "llm")
                        )
                    ),
                }
                for name in names
                if name in legacy or name in ("ingest", "commitments", "tensions", "memories", "llm")
            ],
        }


def _default_config_path() -> Path:
    if env := os.getenv("MINDOS_CONFIG_PATH"):
        return Path(env)
    return Path(__file__).resolve().parents[3] / "config.json"


@lru_cache
def get_config() -> ConfigResponse:
    path = _default_config_path()
    if not path.is_file():
        return ConfigResponse(
            services=[
                ServiceEntry(name="llm", enabled=True),
                ServiceEntry(name="ingest", enabled=True),
                ServiceEntry(name="commitments", enabled=True),
                ServiceEntry(name="retrieval", enabled=True),
            ]
        )
    return ConfigResponse.model_validate(json.loads(path.read_text(encoding="utf-8")))


def reload_config() -> ConfigResponse:
    get_config.cache_clear()
    return get_config()
