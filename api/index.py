"""Vercel entrypoint for the Telegram bot."""
import os

# Neon integration variables on Vercel use the STORAGE_* prefix.
# Normalize them before sitecustomize/pg_backend inspect the environment.
if not os.environ.get("DATABASE_URL"):
    for _key in (
        "STORAGE_POSTGRES_URL",
        "STORAGE_DATABASE_URL",
        "STORAGE_POSTGRES_URL_NO_SSL",
        "STORAGE_DATABASE_URL_UNPOOLED",
        "STORAGE_POSTGRES_URL_NON_POOLING",
    ):
        if os.environ.get(_key):
            os.environ["DATABASE_URL"] = os.environ[_key]
            break


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

# Persistent PostgreSQL is mandatory. Never silently fall back to local or /tmp SQLite.
if not os.environ.get("DATABASE_URL"):
    raise RuntimeError("Persistent database is not configured: Neon DATABASE_URL is missing")

# Patch SQLite calls before importing the existing bot.
import sitecustomize  # noqa: F401

from bot import app  # noqa: E402,F401

# The live bot uses Neon only. The bundled 24-Nov SQLite snapshot is NOT read
# during Vercel startup: the deployment filesystem is read-only, and importing
# a recovery snapshot on every cold start would be unsafe for current data.

# Vercel serverless functions can terminate background threads as soon as the
# webhook response is returned. The bot's normal executor is fine for light
# updates, but /case must finish while the HTTP invocation is still alive.
import bot as _bot_module  # noqa: E402

_real_executor = _bot_module.UPDATE_EXECUTOR

class _VercelCaseExecutor:
    def __init__(self, real_executor):
        self._real = real_executor

    def submit(self, fn, update):
        try:
            msg = getattr(update, "message", None)
            text_value = str(getattr(msg, "text", "") or "").strip().lower()
            is_case = (
                text_value in {"кейс", "case", "/case"}
                or text_value.startswith("/case@")
            )
        except Exception:
            is_case = False

        if is_case:
            print("[vercel] /case: synchronous processing", flush=True)
            fn(update)
            return None

        return self._real.submit(fn, update)

_bot_module.UPDATE_EXECUTOR = _VercelCaseExecutor(_real_executor)

