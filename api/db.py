"""Read-only connection pool. DSN must be the skynyc_ro role (spec §3.2) —
the API's read-only-ness is enforced by the role's grants, not by convention."""

from __future__ import annotations

import os

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            os.environ["PG_RO_DSN"],
            min_size=1,
            max_size=4,
            kwargs={"row_factory": dict_row},
        )
    return _pool


def rows(sql: str, params: dict | None = None) -> list[dict]:
    with pool().connection() as conn:
        return conn.execute(sql, params or {}).fetchall()
