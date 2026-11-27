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


def migrate_bigint_ids(pg):
    """Widen Telegram/user/chat identifier columns from 32-bit INTEGER to BIGINT."""
    if pg.execute(
        "SELECT value FROM bot_migration_meta WHERE key='bigint_ids_v1'"
    ).fetchone():
        return

    fks = pg.execute("""
        SELECT c.conrelid::regclass::text AS table_name,
               c.conname,
               pg_get_constraintdef(c.oid) AS definition
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_namespace n ON n.oid = t.relnamespace
        WHERE c.contype = 'f' AND n.nspname = 'public'
    """).fetchall()

    for fk in fks:
        pg.execute(
            f'ALTER TABLE "{fk["table_name"]}" '
            f'DROP CONSTRAINT "{fk["conname"]}"'
        )

    cols = pg.execute("""
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema='public'
          AND data_type='integer'
          AND (column_name = 'id' OR column_name LIKE '%_id')
    """).fetchall()

    for col in cols:
        pg.execute(
            f'ALTER TABLE "{col["table_name"]}" '
            f'ALTER COLUMN "{col["column_name"]}" TYPE BIGINT '
            f'USING "{col["column_name"]}"::bigint'
        )

    for fk in fks:
        pg.execute(
            f'ALTER TABLE "{fk["table_name"]}" '
            f'ADD CONSTRAINT "{fk["conname"]}" {fk["definition"]}'
        )

    pg.execute(
        "INSERT INTO bot_migration_meta(key,value) "
        "VALUES('bigint_ids_v1','done') "
        "ON CONFLICT (key) DO NOTHING"
    )
    pg.commit()


def migrate_once():
    if not (
        os.environ.get("DATABASE_URL")
        or os.environ.get("POSTGRES_URL")
        or os.environ.get("POSTGRES_PRISMA_URL")
    ):
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
            migrate_bigint_ids(pg)
            return

        db_path = Path(__file__).with_name("database.db")

        # The live database is Neon PostgreSQL. The bundled SQLite database
        # is only a bootstrap source and may be absent from a Vercel bundle.
        # If Neon already has the bot schema, continue with schema migration
        # instead of trying to open SQLite.
        if not db_path.exists():
            has_users = pg.execute(
                'SELECT 1 FROM information_schema.tables '
                "WHERE table_schema='public' AND table_name='users'"
            ).fetchone()

            if has_users:
                pg.execute(
                    "INSERT INTO bot_migration_meta(key,value) "
                    "VALUES('sqlite_bundle_v1','done') "
                    "ON CONFLICT (key) DO NOTHING"
                )
                pg.commit()
                migrate_bigint_ids(pg)
                return

            raise RuntimeError("Bundled database.db is missing")

        original_connect = getattr(sqlite3, "_original_connect", None)
        if original_connect is None:
            original_connect = sqlite3.connect

        src = original_connect(
            f"file:{db_path}?mode=ro&immutable=1",
            uri=True,
        )
        try:
            src.row_factory = sqlite3.Row

            for table in TABLES:
                if not src.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (table,),
                ).fetchone():
                    continue

                cols = [
                    r[1] for r in src.execute(
                        f'PRAGMA table_info("{table}")'
                    ).fetchall()
                ]
                if not cols:
                    continue

                quoted = ", ".join(
                    '"' + c.replace('"', '""') + '"' for c in cols
                )
                rows = src.execute(
                    f'SELECT {quoted} FROM "{table}"'
                ).fetchall()
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
            migrate_bigint_ids(pg)
        finally:
            src.close()
    except Exception:
        pg.rollback()
        raise
    finally:
        pg.close()

    print("[db] database.db migrated to PostgreSQL", flush=True)
