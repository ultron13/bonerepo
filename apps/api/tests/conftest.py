import os

os.environ.setdefault("PLIMSOLL_DATABASE_URL", "postgresql+asyncpg://u:p@localhost/plimsoll")
os.environ.setdefault(
    "PLIMSOLL_MIGRATION_DATABASE_URL", "postgresql+asyncpg://o:p@localhost/plimsoll"
)
os.environ.setdefault("PLIMSOLL_REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("PLIMSOLL_S3_ENDPOINT", "http://localhost:9000")
# At least 32 bytes: RFC 7518 section 3.2 sets that as the minimum for
# HS256, and PyJWT warns below it.
os.environ.setdefault("PLIMSOLL_JWT_SECRET", "test-secret-not-for-production-0123456789")
