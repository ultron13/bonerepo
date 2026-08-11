from __future__ import annotations

import httpx
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


class PostgresCheck:
    name = "postgres"

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def check(self) -> bool:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
            return True
        except Exception:
            return False


class RedisCheck:
    name = "redis"

    def __init__(self, url: str) -> None:
        self._url = url

    async def check(self) -> bool:
        try:
            client: aioredis.Redis = aioredis.from_url(self._url)
            await client.ping()
            await client.aclose()
            return True
        except Exception:
            return False


class ObjectStoreCheck:
    name = "objectstore"

    def __init__(self, endpoint: str) -> None:
        self._endpoint = endpoint

    async def check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(f"{self._endpoint}/minio/health/live")
            return response.status_code == 200
        except Exception:
            return False
