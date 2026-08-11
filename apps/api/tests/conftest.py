import os

os.environ.setdefault("PLIMSOLL_DATABASE_URL", "postgresql+asyncpg://u:p@localhost/plimsoll")
os.environ.setdefault("PLIMSOLL_REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("PLIMSOLL_S3_ENDPOINT", "http://localhost:9000")
os.environ.setdefault("PLIMSOLL_JWT_SECRET", "test-secret-not-for-production")
