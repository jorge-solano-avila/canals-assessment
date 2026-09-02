"""Request-scoped dependencies.

TRANSACTION OWNERSHIP: this dependency deliberately does NOT open a transaction.

CLAUDE.md mandates the sequence reserve -> commit -> charge -> commit, so the
payment call happens *between* two transactions. A request-scoped transaction
could not express that, and it would hold row locks on `inventory` open across an
external HTTP call to the payment provider. Transaction boundaries belong to the
services (phase 4+), which open tightly-scoped `async with session.begin():`
blocks around the database work and nothing else.

CLEANUP ON EXCEPTION is already correct without an explicit rollback. When a route
raises, the exception is thrown into this generator; `async with Session()` then
runs AsyncSession.__aexit__ -> close(), which releases the connection and rolls
back any uncommitted work. An `except: await session.rollback()` here would be
dead code implying the context manager cannot be trusted.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import Session


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield one session per request. No transaction is started here."""
    async with Session() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
