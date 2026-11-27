"""Async Telegram webhook entrypoint for Vercel + aiogram 3.

The large game logic in bot.py is kept intact. aiogram owns the webhook
transport and update lifecycle; legacy TeleBot handlers are executed in worker
threads so synchronous game/database code cannot block the async event loop.
Persistent runtime state lives in Neon PostgreSQL through the existing
sitecustomize/pg_backend compatibility layer; Vercel's filesystem is never
used as the live database.
"""

import asyncio
import hashlib
import json
import os
import sys
import traceback

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

import sitecustomize  # noqa: F401,E402
import bot as legacy_bot  # noqa: E402

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

        # Wait for the complete update. No detached queue and no fire-and-forget.
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
