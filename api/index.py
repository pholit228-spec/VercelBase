"""Telegram webhook entrypoint for Vercel."""

import asyncio
import ast
import hashlib
import json
import os
import re
import sys
import traceback
import importlib.util
from pathlib import Path

from flask import Flask, abort, request

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

LEGACY_TIMEOUT = 55
_legacy_bot = None


def _load_legacy_bot():
    """Load bot.py without running its old PythonAnywhere startup()."""
    path = Path(__file__).resolve().parent.parent / "bot.py"
    source = path.read_text(encoding="utf-8")

    tree = ast.parse(source, filename=str(path))

    # Remove only a top-level startup() call. The startup function itself
    # stays intact, and this works even if bot.py has trailing comments/newlines.
    removed = 0
    kept = []
    for node in tree.body:
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "startup"
            and not node.value.args
            and not node.value.keywords
        ):
            removed += 1
            continue
        kept.append(node)

    if removed != 1:
        raise RuntimeError(
            f"Could not disable legacy startup() in bot.py (found {removed})"
        )

    tree.body = kept
    ast.fix_missing_locations(tree)

    spec = importlib.util.spec_from_file_location("bot", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not create loader for bot.py")

    module = importlib.util.module_from_spec(spec)
    module.__name__ = "bot"
    module.__file__ = str(path)
    sys.modules["bot"] = module

    code = compile(tree, str(path), "exec")
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

    print("[legacy] dispatching update to TeleBot", flush=True)
    await asyncio.wait_for(
        asyncio.to_thread(legacy.bot.process_new_updates, [update]),
        timeout=LEGACY_TIMEOUT,
    )
    print("[legacy] TeleBot dispatch finished", flush=True)


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

        # IMPORTANT: don't feed the update through an empty aiogram
        # dispatcher. bot.py is the real handler registry.
        await _process_legacy(raw)

        print(f"[webhook] completed update_id={update_id}", flush=True)
        return "", 200

    except asyncio.TimeoutError:
        print("[webhook] legacy handler timed out", file=sys.stderr, flush=True)
        return "Handler timeout", 504

    except Exception:
        print("[webhook] ERROR", file=sys.stderr, flush=True)
        traceback.print_exc(file=sys.stderr)
        return "Webhook processing failed", 500


# Do NOT call setWebhook on every Vercel cold start. Telegram already has
# the webhook URL; repeated SetWebhook calls trigger flood control.
