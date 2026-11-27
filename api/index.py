"""Async Telegram webhook entrypoint for Vercel + aiogram 3.

The legacy game logic remains in bot.py. aiogram owns the HTTP webhook and
update lifecycle, while synchronous TeleBot handlers run in a worker thread.

IMPORTANT: runtime state is stored in Neon PostgreSQL. Vercel's filesystem is
never used as the live database. database.db is only a one-time migration
source and is never written to.
"""

import asyncio
import hashlib
import json
import os
import re
import sys
import traceback
import importlib.util
from pathlib import Path

from flask import Flask, abort, request
from aiogram import Bot, Dispatcher
from aiogram.types import Update

if not os.environ.get("DATABASE_URL"):
    for key in (
        "STORAGE_POSTGRES_URL",
        "STORAGE_DATABASE_URL",
        "STORAGE_POSTGRES_URL_NO_SSL",
        "STORAGE_DATABASE_URL_UNPOOLED",
        "STORAGE_POSTGRES_URL_NON_POOLING",
        "POSTGRES_URL",
        "POSTGRES_PRISMA_URL",
    ):
        value = os.environ.get(key)
        if value:
            os.environ["DATABASE_URL"] = value
            break

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

WEBHOOK_SECRET = (
    os.environ.get("WEBHOOK_SECRET")
    or hashlib.sha256(BOT_TOKEN.encode("utf-8")).hexdigest()
)

if not os.environ.get("DATABASE_URL"):
    raise RuntimeError("DATABASE_URL is missing: connect the Vercel project to Neon")

# Install the SQLite->PostgreSQL compatibility layer before loading the old
# bot. This keeps the existing synchronous game code pointed at Neon.
import sitecustomize  # noqa: F401,E402


def _load_legacy_bot():
    """
    Load bot.py without executing its old PythonAnywhere startup() call.

    bot.py is a 3.6 MB legacy application, so rewriting the whole file just to
    remove one import-time side effect would be risky. We load the exact file
    shipped with this deployment and remove only a standalone final startup()
    statement. The rest of the game logic stays untouched.
    """
    path = Path(__file__).resolve().parent.parent / "bot.py"
    source = path.read_text(encoding="utf-8")

    cleaned, removed = re.subn(
        r"\n[ \t]*startup\(\)[ \t]*(?=\n?\Z)",
        "\n",
        source,
        count=1,
    )
    if removed != 1:
        raise RuntimeError("Could not disable legacy startup() in bot.py")

    spec = importlib.util.spec_from_file_location("bot", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not create loader for bot.py")

    module = importlib.util.module_from_spec(spec)
    module.__name__ = "bot"
    module.__file__ = str(path)
    sys.modules["bot"] = module

    code = compile(cleaned, str(path), "exec")
    exec(code, module.__dict__)
    return module


# Import first so the existing bot can create/upgrade its PostgreSQL-compatible
# schema. Then copy the old SQLite data into that persistent database exactly
# once. The migration has its own completion marker in PostgreSQL.
import pg_migrate  # noqa: E402
legacy_bot = _load_legacy_bot()
pg_migrate.migrate_once()

app = Flask(__name__)
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

LEGACY_TIMEOUT = 55


def _legacy_update(raw: dict):
    return legacy_bot.telebot.types.Update.de_json(json.dumps(raw))


async def _process_legacy(raw: dict) -> None:
    update = _legacy_update(raw)
    await asyncio.wait_for(
        asyncio.to_thread(legacy_bot.bot.process_new_updates, [update]),
        timeout=LEGACY_TIMEOUT,
    )


@dp.update()
async def handle_update(update: Update) -> None:
    update_id = getattr(update, "update_id", "unknown")
    print(f"[aiogram] start update_id={update_id}", flush=True)
    raw = update.model_dump(by_alias=True, exclude_none=True)
    await _process_legacy(raw)
    print(f"[aiogram] done update_id={update_id}", flush=True)


async def dispatch(raw: dict) -> None:
    update = Update.model_validate(raw, context={"bot": bot})
    await dp.feed_update(bot, update)


def _check_secret() -> None:
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        abort(403)


@app.route("/", methods=["GET", "HEAD"])
def health():
    return "OK", 200


@app.route("/webhook", methods=["POST"])
async def webhook():
    _check_secret()
    try:
        raw = request.get_json(force=True, silent=False)
        if not isinstance(raw, dict):
            return "Invalid update", 400

        update_id = raw.get("update_id", "unknown")
        print(f"[webhook] received update_id={update_id}", flush=True)

        await dispatch(raw)

        print(f"[webhook] completed update_id={update_id}", flush=True)
        return "", 200
    except asyncio.TimeoutError:
        print("[webhook] legacy handler timed out", file=sys.stderr, flush=True)
        return "Handler timeout", 504
    except Exception:
        print("[webhook] ERROR", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        return "Webhook processing failed", 500
