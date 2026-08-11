"""A deliberately latency-shaped system under test.

A static response would produce a degenerate distribution and make the merged
percentile demonstration in slice 4 meaningless, so each endpoint has its own
latency band and a small error rate.
"""

from __future__ import annotations

import asyncio
import random

from fastapi import FastAPI, HTTPException

LATENCY_PROFILE_MS: dict[str, tuple[int, int]] = {
    "login": (40, 180),
    "browse": (25, 120),
    "cart": (60, 260),
    "checkout": (120, 900),
}

ERROR_RATE = 0.01

app = FastAPI(title="Plimsoll demo target")


async def _serve(name: str) -> dict[str, str]:
    low, high = LATENCY_PROFILE_MS[name]
    await asyncio.sleep(random.uniform(low, high) / 1000)  # noqa: S311
    if random.random() < ERROR_RATE:  # noqa: S311
        raise HTTPException(status_code=500, detail="Simulated downstream failure.")
    return {"transaction": name, "status": "ok"}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/login")
async def login() -> dict[str, str]:
    return await _serve("login")


@app.get("/browse")
async def browse() -> dict[str, str]:
    return await _serve("browse")


@app.get("/cart")
async def cart() -> dict[str, str]:
    return await _serve("cart")


@app.get("/checkout")
async def checkout() -> dict[str, str]:
    return await _serve("checkout")
