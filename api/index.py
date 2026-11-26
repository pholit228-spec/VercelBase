"""Vercel entrypoint for the Telegram bot."""
import os

if not os.environ.get("WEBHOOK_URL"):
    prod = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")
    vercel = os.environ.get("VERCEL_URL")
    if prod:
        os.environ["WEBHOOK_URL"] = prod if prod.startswith(("http://", "https://")) else "https://" + prod
    elif vercel:
        os.environ["WEBHOOK_URL"] = "https://" + vercel

if not (os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or os.environ.get("POSTGRES_PRISMA_URL")):
    os.environ["DB_PATH"] = os.path.join(os.path.dirname(os.path.dirname(__file__)), "database.db")

# Patch the existing bot's sqlite3 calls to use PostgreSQL when Neon is connected.
import sitecustomize  # noqa: F401

from bot import app

if os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL") or os.environ.get("POSTGRES_PRISMA_URL"):
    from pg_migrate import migrate_once
    migrate_once()
