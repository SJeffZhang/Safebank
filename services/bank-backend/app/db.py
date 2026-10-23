"""Small explicit SQLAlchemy Core connection boundary; no banking logic."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine


def engine(url: str, **kwargs) -> AsyncEngine:
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if not url.startswith("postgresql+asyncpg://"):
        raise ValueError("PostgreSQL with asyncpg is required")
    return create_async_engine(
        url, isolation_level="READ COMMITTED", pool_pre_ping=True, hide_parameters=True, **kwargs
    )


async def ready(db: AsyncEngine) -> bool:
    async with db.connect() as conn:
        clock_present = await conn.scalar(text("SELECT 1 FROM clock WHERE id=1"))
        version = await conn.scalar(text("SELECT version_num FROM alembic_version"))
        return clock_present == 1 and version == "0003_obligations"
