"""Vercel entrypoint for the Telegram bot."""
import os

# Vercel: prefer the stable production URL, then the deployment URL.
# The explicit WEBHOOK_URL env var still has highest priority.
if not os.environ.get("WEBHOOK_URL"):
    prod = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")
    vercel = os.environ.get("VERCEL_URL")
    if prod:
        os.environ["WEBHOOK_URL"] = prod if prod.startswith(("http://", "https://")) else "https://" + prod
    elif vercel:
        os.environ["WEBHOOK_URL"] = "https://" + vercel
    else:
        # Stable fallback for this Vercel project.
        os.environ["WEBHOOK_URL"] = "https://botik-vercel.vercel.app"

# Vercel's function filesystem is not persistent. Use /tmp unless a database
# connection is configured or the user explicitly supplied DB_PATH.
if not (
    os.environ.get("DATABASE_URL")
    or os.environ.get("POSTGRES_URL")
    or os.environ.get("POSTGRES_PRISMA_URL")
):
    os.environ.setdefault("DB_PATH", "/tmp/data.db")

# Patch the existing bot's sqlite3 calls to use PostgreSQL when Neon is connected.
import sitecustomize  # noqa: F401

from bot import app  # noqa: E402,F401

if (
    os.environ.get("DATABASE_URL")
    or os.environ.get("POSTGRES_URL")
    or os.environ.get("POSTGRES_PRISMA_URL")
):
    from pg_migrate import migrate_once
    migrate_once()
