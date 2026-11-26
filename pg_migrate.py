import os
import sqlite3
from pathlib import Path

from pg_backend import connect

TABLES = [
    "users", "collections", "cooldowns", "secret_styles", "slime_instances",
    "food_meta", "food_inventory", "grounds", "chats", "role_members",
    "bot_meta", "promo_codes", "promo_activations", "plort_inventory",
    "plort_market", "quests"
]

def migrate_once():
    if not (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or os.environ.get("POSTGRES_PRISMA_URL")):
        return

    pg = connect()
    try:
        pg.execute(
            "CREATE TABLE IF NOT EXISTS bot_migration_meta "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        if pg.execute(
            "SELECT value FROM bot_migration_meta WHERE key='sqlite_bundle_v1'"
        ).fetchone():
            pg.commit()
            return

        db_path = Path(__file__).with_name("database.db")
        if not db_path.exists():
            raise RuntimeError("Bundled database.db is missing")

        original_connect = getattr(sqlite3, "_original_connect", None)
        if original_connect is None:
            original_connect = sqlite3.connect
        src = original_connect(str(db_path))
        try:
            src.row_factory = sqlite3.Row
            for table in TABLES:
                if not src.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone():
                    continue

                cols = [r[1] for r in src.execute(
                    f'PRAGMA table_info("{table}")'
                ).fetchall()]
                if not cols:
                    continue

                quoted = ", ".join('"' + c.replace('"', '""') + '"' for c in cols)
                rows = src.execute(f'SELECT {quoted} FROM "{table}"').fetchall()
                if not rows:
                    continue

                placeholders = ", ".join(["%s"] * len(cols))
                sql = (
                    f'INSERT INTO "{table}" ({quoted}) VALUES ({placeholders}) '
                    "ON CONFLICT DO NOTHING"
                )
                cur = pg._conn.cursor()
                cur.executemany(sql, [tuple(r) for r in rows])
                cur.close()

            pg.execute(
                "INSERT INTO bot_migration_meta(key,value) "
                "VALUES('sqlite_bundle_v1','done') "
                "ON CONFLICT (key) DO NOTHING"
            )
            pg.commit()
        finally:
            src.close()
    except Exception:
        pg.rollback()
        raise
    finally:
        pg.close()

    print("[db] database.db migrated to PostgreSQL", flush=True)
