"""Async engine and sessionmaker.

expire_on_commit=False is a requirement, not a preference. With the default True,
any attribute access after a commit triggers an implicit refresh; under asyncio that
refresh has no greenlet context and raises MissingGreenlet. The mandated payment
sequence (reserve -> commit -> charge -> commit) reads order attributes after a
commit constantly, so this setting is what makes it expressible at all.

Connection budget: pool_size + max_overflow is 15 connections *per worker process*,
not 15 in total. Against a default max_connections=100 that supports roughly six
uvicorn workers before the pool alone can exhaust the server.
"""

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_size=10,
    max_overflow=5,
    pool_pre_ping=True,
    pool_recycle=1800,
    echo=settings.sql_echo,
)

Session = async_sessionmaker(engine, expire_on_commit=False)
