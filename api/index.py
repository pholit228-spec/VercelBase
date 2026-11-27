"""Async Telegram webhook entrypoint for Vercel + aiogram 3."""

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

import sitecustomize  # noqa: F401,E402

app = Flask(__name__)
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

LEGACY_TIMEOUT = 55
_legacy_bot = None


async def _configure_webhook():
    base = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") or os.environ.get("VERCEL_URL")
    if not base:
        print("[webhook] Vercel URL unavailable; keeping existing webhook", flush=True)
        return
    if not base.startswith("http"):
        base = "https://" + base
    url = base.rstrip("/") + "/webhook"
    try:
        await bot.set_webhook(
            url=url,
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=False,
            allowed_updates=dp.resolve_used_update_types(),
        )
        print(f"[webhook] configured Telegram -> {url}", flush=True)
    except Exception:
        print("[webhook] failed to configure Telegram webhook", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)


def _load_legacy_bot():
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


def _get_legacy_bot():
    global _legacy_bot
    if _legacy_bot is None:
        print("[startup] loading legacy bot.py", flush=True)
        _legacy_bot = _load_legacy_bot()
        import pg_migrate
        pg_migrate.migrate_once()
        print("[startup] legacy bot loaded; Neon migration checked", flush=True)
    return _legacy_bot


def _legacy_update(raw: dict):
    legacy = _get_legacy_bot()
    return legacy.telebot.types.Update.de_json(json.dumps(raw))


async def _process_legacy(raw: dict) -> None:
    legacy = _get_legacy_bot()
    update = _legacy_update(raw)
    await asyncio.wait_for(
        asyncio.to_thread(legacy.bot.process_new_updates, [update]),
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


# Vercel can initialize a function without a request, so configure Telegram
# explicitly when this module is loaded. The production URL is injected by Vercel.
try:
    asyncio.run(_configure_webhook())
except Exception:
    print("[webhook] startup webhook setup failed", file=sys.stderr, flush=True)
    traceback.print_exc(file=sys.stderr)
