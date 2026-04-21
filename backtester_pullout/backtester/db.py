"""Standalone Tortoise init for the backtester.

Connection resolution order:
  1. BACKTESTER_DB_URL env var (postgres://user:pass@host:port/db) — takes precedence
  2. JOBS_POSTGRES_* env vars (same as validator)

Does NOT generate_schemas — pool event tables are populated externally.
"""
from __future__ import annotations

import os

from dotenv import load_dotenv
from tortoise import Tortoise

_MODELS = ["backtester_pullout.backtester.models"]


def _env(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None:
        raise RuntimeError(f"Missing env var: {name}")
    return val


def tortoise_config() -> dict:
    return {
        "connections": {
            "default": {
                "engine": "tortoise.backends.asyncpg",
                "credentials": {
                    "host": _env("JOBS_POSTGRES_HOST"),
                    "port": int(_env("JOBS_POSTGRES_PORT", "5432")),
                    "user": _env("JOBS_POSTGRES_USER"),
                    "password": _env("JOBS_POSTGRES_PASSWORD"),
                    "database": _env("JOBS_POSTGRES_DB"),
                    "schema": os.environ.get("JOBS_POSTGRES_SCHEMA", "public"),
                },
            }
        },
        "apps": {
            "models": {
                "models": _MODELS,
                "default_connection": "default",
            }
        },
    }


async def init_db() -> None:
    load_dotenv()
    url = os.environ.get("BACKTESTER_DB_URL")
    if url:
        # Tortoise expects postgres:// or postgresql://; asyncpg backend used.
        if url.startswith("postgresql+asyncpg://"):
            url = url.replace("postgresql+asyncpg://", "postgres://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgres://", 1)
        await Tortoise.init(db_url=url, modules={"models": _MODELS})
    else:
        await Tortoise.init(config=tortoise_config())


async def close_db() -> None:
    await Tortoise.close_connections()
