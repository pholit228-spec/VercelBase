import os
import sqlite3

# Keep the real SQLite connector so migration code can still read the bundled database.db.
_ORIGINAL_SQLITE_CONNECT = sqlite3.connect
sqlite3._original_connect = _ORIGINAL_SQLITE_CONNECT

if os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or os.environ.get("POSTGRES_PRISMA_URL"):
    from pg_backend import connect
    import psycopg

    def _connect(*args, **kwargs):
        return connect()

    sqlite3.connect = _connect
    # pg_backend already returns mapping-compatible rows; do not replace sqlite3.Row with object().
    from pg_backend import CompatRow
    sqlite3.Row = CompatRow
    sqlite3.OperationalError = psycopg.OperationalError
    sqlite3.IntegrityError = psycopg.IntegrityError
