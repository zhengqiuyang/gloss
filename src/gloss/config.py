"""Configuration: ~/.gloss/config.toml, environment overrides, API key resolution."""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

import tomli_w

from .models import GlossError

# Every user-settable key, with its default. Nested dict = config sections.
DEFAULT_CONFIG: Dict[str, Dict[str, Any]] = {
    "llm": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "vision_model": "",
        "api_key": "",
        "timeout": 60,
    },
    "glossary": {
        "language": "中文",
        "detail_max_chars": 160,
        "max_known_terms": 200,
    },
}

# Flat dotted paths accepted by `gloss config set`, with value coercers.
_CONFIG_SCHEMA = {
    "llm.base_url": str,
    "llm.model": str,
    "llm.vision_model": str,
    "llm.api_key": str,
    "llm.timeout": int,
    "glossary.language": str,
    "glossary.detail_max_chars": int,
    "glossary.max_known_terms": int,
}


def gloss_home() -> Path:
    return Path(os.environ.get("GLOSS_HOME", "~/.gloss")).expanduser()


def db_path() -> Path:
    return gloss_home() / "gloss.db"


def config_path() -> Path:
    return gloss_home() / "config.toml"


def load_config() -> Dict[str, Dict[str, Any]]:
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    path = config_path()
    if path.exists():
        try:
            with open(path, "rb") as f:
                stored = tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            raise GlossError(f"配置文件解析失败 {path}: {exc}") from exc
        for section, values in stored.items():
            if isinstance(values, dict) and section in cfg:
                cfg[section].update(values)
    return cfg


def save_config(cfg: Dict[str, Dict[str, Any]]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        tomli_w.dump(cfg, f)


def resolve_api_key(cfg: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """Precedence: GLOSS_API_KEY env > OPENAI_API_KEY env > config file."""
    for env in ("GLOSS_API_KEY", "OPENAI_API_KEY"):
        value = os.environ.get(env)
        if value:
            return value
    inline = cfg["llm"].get("api_key", "")
    return inline or None


def set_config_value(dotted_key: str, raw_value: str) -> None:
    if dotted_key not in _CONFIG_SCHEMA:
        valid = "\n".join(f"  {k}" for k in _CONFIG_SCHEMA)
        raise GlossError(f"未知配置项 {dotted_key!r}。可用配置项:\n{valid}")
    coerce = _CONFIG_SCHEMA[dotted_key]
    try:
        value = coerce(raw_value)
    except ValueError as exc:
        raise GlossError(f"{dotted_key} 需要一个 {coerce.__name__} 类型的值: {raw_value!r}") from exc
    cfg = load_config()
    section, key = dotted_key.split(".", 1)
    cfg[section][key] = value
    save_config(cfg)


def mask_config(cfg: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    masked = copy.deepcopy(cfg)
    if masked["llm"].get("api_key"):
        key = masked["llm"]["api_key"]
        masked["llm"]["api_key"] = key[:4] + "…" + key[-4:] if len(key) > 8 else "…"
    return masked
