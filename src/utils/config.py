"""Configuration management using Pydantic Settings with YAML support."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class UserConfig(BaseModel):
    """User-specific configuration."""

    name: str = "Grace"
    playlist_name: str = "Grace's Station"
    playlist_id: str | None = None


class SeedsConfig(BaseModel):
    """Seed artists and songs for discovery."""

    artists: list[str] = Field(default_factory=list)
    songs: list[str] = Field(default_factory=list)


class FiltersConfig(BaseModel):
    """Filtering criteria for artist discovery."""

    gender: str = "Male"
    countries: list[str] = Field(default_factory=lambda: ["GB", "US", "IE", "AU", "CA"])
    min_release_year: int = 2020


class AlgorithmWeights(BaseModel):
    """Weights for playlist composition categories."""

    favorites: float = 0.40
    hits: float = 0.30
    discovery: float = 0.20
    wildcard: float = 0.10

    @field_validator("favorites", "hits", "discovery", "wildcard")
    @classmethod
    def validate_weight(cls, v: float) -> float:
        if not 0 <= v <= 1:
            raise ValueError("Weight must be between 0 and 1")
        return v


class AlgorithmConfig(BaseModel):
    """Algorithm configuration."""

    playlist_size: int = 50
    weights: AlgorithmWeights = Field(default_factory=AlgorithmWeights)

    # Learning parameters
    hot_zone_size: int = 10  # Positions 1-10 for negative signal detection
    hot_zone_hours: int = 48  # Hours in hot zone with no plays = negative signal
    decay_days: int = 14  # Days without play before weight decay starts
    new_release_days: int = 30  # Max age for "wildcard" tracks


class ScheduleConfig(BaseModel):
    """Scheduling configuration."""

    refresh_time: str = "03:00"
    timezone: str = "America/Los_Angeles"


class AppleMusicConfig(BaseModel):
    """Apple Music API configuration."""

    team_id: str = ""
    key_id: str = ""
    private_key_path: str = "~/.secrets/apple_music_key.p8"
    storefront: str = "us"

    @property
    def private_key_path_resolved(self) -> Path:
        return Path(self.private_key_path).expanduser()


class DatabaseConfig(BaseModel):
    """Database configuration."""

    path: str = "curator.db"

    @property
    def url(self) -> str:
        return f"sqlite+aiosqlite:///{self.path}"


class Settings(BaseSettings):
    """Main application settings."""

    model_config = SettingsConfigDict(
        env_prefix="CURATOR_",
        env_nested_delimiter="__",
    )

    user: UserConfig = Field(default_factory=UserConfig)
    seeds: SeedsConfig = Field(default_factory=SeedsConfig)
    filters: FiltersConfig = Field(default_factory=FiltersConfig)
    algorithm: AlgorithmConfig = Field(default_factory=AlgorithmConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    apple_music: AppleMusicConfig = Field(default_factory=AppleMusicConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)


def load_config(config_path: Path | str = "config.yaml") -> Settings:
    """Load configuration from YAML file with environment variable overrides."""
    path = Path(config_path)

    if path.exists():
        with open(path) as f:
            yaml_config = yaml.safe_load(f) or {}
    else:
        yaml_config = {}

    # Expand environment variables in the YAML config
    yaml_config = _expand_env_vars(yaml_config)

    return Settings(**yaml_config)


def _expand_env_vars(obj: Any) -> Any:
    """Recursively expand ${VAR} patterns in config values."""
    import os
    import re

    if isinstance(obj, str):
        pattern = re.compile(r"\$\{([^}]+)\}")
        matches = pattern.findall(obj)
        for var in matches:
            value = os.environ.get(var, "")
            obj = obj.replace(f"${{{var}}}", value)
        return obj
    elif isinstance(obj, dict):
        return {k: _expand_env_vars(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_expand_env_vars(item) for item in obj]
    return obj


def save_config(settings: Settings, config_path: Path | str = "config.yaml") -> None:
    """Save configuration to YAML file."""
    path = Path(config_path)

    # Convert to dict, excluding defaults that match
    config_dict = settings.model_dump(mode="json")

    with open(path, "w") as f:
        yaml.dump(config_dict, f, default_flow_style=False, sort_keys=False)
