from __future__ import annotations

from fastapi import FastAPI

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
    app.middleware("http")(request_id_middleware)
    register_error_handlers(app)

    from plimsoll_api.db.session import get_engine
    from plimsoll_api.readiness import ObjectStoreCheck, PostgresCheck, RedisCheck
    from plimsoll_api.routers import auth, credentials, health, projects
    from plimsoll_api.routers.health import set_readiness_checks

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(projects.router)
    app.include_router(credentials.router)
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
