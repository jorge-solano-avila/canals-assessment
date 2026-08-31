# Canals assessment — order management API

## Stack (fixed — do not propose alternatives)
Python 3.12, FastAPI, SQLAlchemy 2.0 (async), asyncpg, Alembic,
PostgreSQL + PostGIS, GeoAlchemy2, Redis (geocoding cache only),
docker compose. All code, comments and docs in English.

## Architectural decisions — do not deviate
- Idempotency: `idempotency_keys` table in Postgres.
  INSERT ... ON CONFLICT DO NOTHING, in the SAME transaction as the order.
  States: in_progress / completed / failed. Redis is NOT involved.
  Store request_hash; return 422 if the same key arrives with a different body.
- Stock reservation: single atomic conditional UPDATE
  (WHERE on_hand - reserved >= qty ... RETURNING).
  Never read-check-write.
- Order state machine: pending -> reserved -> paid -> confirmed,
  with payment_failed and cancelled as compensating paths.
- The payment call ALWAYS happens outside any open DB transaction.
  Sequence: reserve -> commit -> charge -> commit.
- Warehouse selection: one query. GROUP BY warehouse_id,
  HAVING count(DISTINCT product_id) = <n_items>,
  ORDER BY PostGIS distance to shipping point, LIMIT 1.
- Transactional outbox for the order_confirmed event.
- Money as integers (minor units) plus a currency column.
  Snapshot unit_price on order_items — never join back to products.
- lazy="raise" on every relationship. expire_on_commit=False on the sessionmaker.
- Geocoding and payments behind interfaces, with injected mock implementations.

## Forbidden
CQRS, event sourcing, external queues/brokers, microservices, SQLModel,
Base.metadata.create_all (Alembic only), implicit lazy loading.