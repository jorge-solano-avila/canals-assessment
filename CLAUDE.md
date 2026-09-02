# Order Service — Canals backend assessment

Backend service that creates orders, selects a fulfilling warehouse and
charges a payment provider. Single endpoint: POST /orders.

## Stack (fixed — do not propose alternatives)
Python 3.12, FastAPI, SQLAlchemy 2.0 (async), asyncpg, Alembic,
PostgreSQL + PostGIS, GeoAlchemy2, Redis (geocoding cache only),
pytest + testcontainers, docker compose.
All code, comments, docs and commit messages in English.

## Project structure

src/<pkg>/
api/ FastAPI routes, dependencies, exception handlers
services/ orchestration: state machine, saga, idempotency
repositories/ SQLAlchemy data access
ports/ Protocol definitions (GeocodingProvider, PaymentProvider)
adapters/ Mock and Redis implementations of the ports
models/ SQLAlchemy tables
schemas/ Pydantic request/response
domain/ domain exceptions and result types
db/ engine, session factory


### Structural rules
- Transaction boundaries are owned by services. Repositories receive a
  session, never create one. The FastAPI dependency must not open a
  transaction.
- services/ imports ports/, never adapters/. Wiring happens in api/.
- services/ contains no FastAPI imports.
- No dependency injection container — FastAPI `Depends` only.
- No separate domain entities mirroring the SQLAlchemy models. Models are
  the persistence representation and are used directly.

## Architectural decisions — do not deviate

### Idempotency
- `idempotency_keys` table in Postgres. Redis is NOT involved.
- `INSERT ... ON CONFLICT DO NOTHING ... RETURNING`, in the SAME
  transaction as the order insert.
- States: in_progress / completed / failed.
  - in_progress → concurrent request with same key returns 409
  - completed → return stored response_body and response_status
  - failed → retry allowed
- Store request_hash; same key with a different body returns 422.
- Cleanup job for keys older than 24-48h, backed by an index on created_at.

### Stock reservation
- Single atomic conditional UPDATE:
  `WHERE on_hand - reserved >= qty ... RETURNING`.
  No row returned means insufficient stock.
- NEVER read-check-write.
- The reservation is the authority on availability. Warehouse selection is
  advisory and may be stale by the time reservation runs.

### Order state machine

pending -> reserved -> paid -> confirmed
| |
cancelled payment_failed (compensate: release reservation)


### Payment
- The payment call ALWAYS happens outside any open DB transaction.
- Sequence: reserve → commit → charge → commit.
- Send an idempotency key to the payment provider.
- Timeout means unknown outcome: reconciliation job queries provider state.
  Document this even if only partially implemented.

### Warehouse selection
- ONE SQL statement, built with SQLAlchemy Core (not raw text, not an ORM loop).
- `GROUP BY warehouse_id HAVING count(DISTINCT product_id) = <n_items>`,
  `ORDER BY` PostGIS distance to shipping point, `LIMIT 1`.
- Availability is `on_hand - reserved`.
- Deterministic tie-break when warehouses are equidistant.
- Must use the GiST index — verify with EXPLAIN.

### Data
- Money as integer minor units + currency column. Never Numeric/Float.
- `order_items` snapshots unit_price at purchase time — never join back to
  products.
- CHECK constraints: quantity > 0, on_hand >= reserved, reserved >= 0.
- Foreign keys everywhere with deliberate ON DELETE behaviour.
- `stock_reservations.expires_at` plus a sweeper job for expired holds.
- Transactional outbox for the order_confirmed event.

### SQLAlchemy specifics
- 2.0 declarative: DeclarativeBase + Mapped[] / mapped_column, fully typed.
- `lazy="raise"` on every relationship, no exceptions.
- `expire_on_commit=False` on the sessionmaker.
- Enum types reuse the DB types created by migrations (`create_type=False`).
- Alembic only. Never `Base.metadata.create_all`.

### Validation and secrets
- Pydantic v2, separate module tree from the models. No SQLModel-style merging.
- Card number: Luhn check + length + normalisation, as SecretStr, excluded
  from repr and serialisation. Never logged.
- Domain exceptions in domain/, mapped to HTTP codes in api/.
- Consistent error response shape for every non-2xx.

## Testing
- Targeted, not exhaustive. The spec says tests are optional; write the few
  that prove correctness under concurrency.
- Real Postgres + PostGIS via testcontainers. SQLite is not acceptable.
- Migrations run against the test DB; suite fails if they don't apply.
- Truncate between tests, not a shared rolled-back transaction —
  concurrency tests need separate connections.
- Must-have tests:
  - N concurrent reservations of the last unit → exactly one succeeds
  - Same idempotency key in parallel → one order, one charge
  - Payment timeout → stock released, order in payment_failed
- pytest-cov, fail_under=80 scoped to services/ and repositories/.
  Coverage is a floor, not a goal.

## Forbidden
CQRS, event sourcing, external brokers/queues, microservices, SQLModel,
`Base.metadata.create_all`, implicit lazy loading, DI containers,
separate domain entities with mappers, retry/fallback logic beyond what is
specified.

## Working method
- Plan mode per phase. Do not implement beyond the current phase.
- If a constraint here seems wrong, say so before planning — do not silently
  work around it.
- Phases:

1) infra + migrations + seeds
2) models + schemas
3) warehouse selection + geocoding
4) reservation + state machine
5) idempotency + payment + outbox
6) README