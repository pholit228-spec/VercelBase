"""Vercel entrypoint for the Telegram bot.

Incoming Telegram updates are dispatched through aiogram 3.
The existing bot.py handlers remain intact for now; aiogram is the
async webhook/dispatch layer, while the legacy TeleBot handlers are
called synchronously inside an awaited worker thread.

Important:
- Neon PostgreSQL is the only persistent database.
- No /tmp or local SQLite storage is used for runtime state.
- Webhook processing is awaited; updates are not put into a detached
  ThreadPoolExecutor queue.
"""

import asyncio
import json
import os
import sys
import traceback

from flask import Flask, abort, request
from aiogram import Bot, Dispatcher
from aiogram.types import Update

# Vercel Marketplace/Neon may expose the same database under STORAGE_* names.
# Normalize one of those to the variable used by pg_backend/sitecustomize.
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

if not os.environ.get("WEBHOOK_URL"):
    prod = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL")
    vercel = os.environ.get("VERCEL_URL")
    if prod:
        os.environ["WEBHOOK_URL"] = (
            prod if prod.startswith(("http://", "https://")) else "https://" + prod
        )
    elif vercel:
        os.environ["WEBHOOK_URL"] = "https://" + vercel
    else:
        os.environ["WEBHOOK_URL"] = "https://botik-vercel.vercel.app"

if not os.environ.get("DATABASE_URL"):
    raise RuntimeError("Persistent database is not configured: Neon DATABASE_URL is missing")

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

WEBHOOK_SECRET = (
    os.environ.get("WEBHOOK_SECRET")
    or __import__("hashlib").sha256(BOT_TOKEN.encode()).hexdigest()
)

WEBHOOK_PATH = "/webhook"

# Import after DATABASE_URL is normalized so sitecustomize redirects the
# existing sqlite3-compatible bot code to Neon PostgreSQL.
import sitecustomize  # noqa: F401
import bot as legacy_bot

# The old Flask app and its detached executor are intentionally not used here.
# We create a separate Vercel app and feed each request directly into aiogram.
app = Flask(__name__)

aiogram_bot = Bot(BOT_TOKEN)
dispatcher = Dispatcher()


async def _run_legacy_handler(update: Update) -> None:
    """Bridge one aiogram Update into the existing TeleBot handlers."""
    raw = update.model_dump(by_alias=True, exclude_none=True)
    telebot_update = legacy_bot.telebot.types.Update.de_json(json.dumps(raw))

    # TeleBot was created with threaded=False, so this call has no internal
    # handler queue. Await the worker thread so the webhook request itself
    # does not finish before the update is actually processed.
    await asyncio.to_thread(
        legacy_bot.bot.process_new_updates,
        [telebot_update],
    )


@dispatcher.update()
async def _all_updates(update: Update) -> None:
    update_id = getattr(update, "update_id", "unknown")
    print(f"[aiogram] processing update_id={update_id}", flush=True)
    try:
        await _run_legacy_handler(update)
        print(f"[aiogram] finished update_id={update_id}", flush=True)
    except Exception:
        print(
            f"[aiogram] ERROR processing update_id={update_id}",
            file=sys.stderr,
            flush=True,
        )
        traceback.print_exc(file=sys.stderr)
        raise


async def _dispatch(raw_update: dict) -> None:
    update = Update.model_validate(raw_update, context={"bot": aiogram_bot})
    await dispatcher.feed_update(aiogram_bot, update)


def _verify_secret() -> None:
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        abort(403)


@app.route("/", methods=["GET", "HEAD"])
def health():
    return "OK", 200


@app.route(WEBHOOK_PATH, methods=["POST"])
async def telegram_webhook():
    _verify_secret()

    try:
        raw = request.get_json(force=True, silent=False)
        update_id = raw.get("update_id", "unknown") if isinstance(raw, dict) else "unknown"
        print(f"[webhook] received update_id={update_id}", flush=True)

        # Crucial difference from the old implementation:
        # there is NO detached executor and NO fire-and-forget processing.
        # The HTTP request waits for aiogram -> handler processing to finish.
        # Flask is running this view as an async request. Await aiogram
        # directly instead of creating a new event loop for every update.
        await _dispatch(raw)

        print(f"[webhook] completed update_id={update_id}", flush=True)
        return "", 200
    except Exception:
        print("[webhook] ERROR", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        return "Webhook processing failed", 500
