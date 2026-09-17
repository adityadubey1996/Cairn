"""One connection per request. Localhost Postgres, internal traffic.

# ponytail: no pool — connect() is ~1ms on localhost; add psycopg_pool if this
# ever serves more than a handful of concurrent users.
"""
import psycopg
from psycopg.rows import dict_row

from . import config


def connect() -> psycopg.Connection:
    return psycopg.connect(config.DATABASE_URL, row_factory=dict_row)
