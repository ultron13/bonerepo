from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from plimsoll_api.config import get_settings
from plimsoll_api.errors import register_error_handlers
from plimsoll_api.logging import configure_logging
from plimsoll_api.middleware import request_id_middleware


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title="Plimsoll",
        version=settings.version,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
    )
    # The interface is a separate origin from the API on purpose: one place a
    # token lives, and no server-side copy of a credential that belongs to the
    # person holding the tab. That makes CORS load-bearing rather than
    # incidental, so the origins are listed rather than opened.
    origins = [origin.strip() for origin in settings.cors_origins.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["authorization", "content-type"],
    )
    app.middleware("http")(request_id_middleware)
    register_error_handlers(app)

    from plimsoll_api.db.session import get_engine
    from plimsoll_api.readiness import ObjectStoreCheck, PostgresCheck, RedisCheck
    from plimsoll_api.routers import (
        agent,
        auth,
        credentials,
        health,
        live,
        performance_tests,
        pools,
        projects,
        runs,
        script_repos,
        target_policy,
    )
    from plimsoll_api.routers.health import set_readiness_checks

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(projects.router)
    app.include_router(credentials.router)
    app.include_router(pools.router)
    app.include_router(target_policy.router)
    app.include_router(script_repos.router)
    app.include_router(performance_tests.router)
    app.include_router(runs.router)
    app.include_router(agent.router)
    app.include_router(live.router)
    set_readiness_checks(
        app,
        [
            PostgresCheck(get_engine()),
            RedisCheck(settings.redis_url),
            ObjectStoreCheck(settings.s3_endpoint),
        ],
    )
    return app


app = create_app()
