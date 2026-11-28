"""Garden harvest bridge for BOTIK.

Collects mature garden cycles before the legacy handlers run and sends a
private Telegram notification when food was actually added to inventory.
The collection itself stays in bot.py so its existing database logic remains
the source of truth.
"""


async def handle_message(message, legacy_bot) -> bool:
    if not getattr(message, "from_user", None):
        return False

    try:
        totals = legacy_bot.collect_garden(message.from_user.id)
    except Exception as exc:
        print(
            f"[garden_notify] collection failed for user={message.from_user.id}: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )
        return False

    if not totals:
        return False

    lines = ["🌾 <b>У вас созрел урожай!</b>", ""]
    for food_id, quantity in totals.items():
        title = legacy_bot._food_title(food_id)
        lines.append(f"🍽️ <b>{title}</b> ×{int(quantity)}")

    lines.append("")
    lines.append("Еда автоматически добавлена в ваш запас.")

    try:
        await message.bot.send_message(
            message.from_user.id,
            "\n".join(lines),
            parse_mode="HTML",
        )
    except Exception as exc:
        # Food is already safely stored; failure to notify must not roll it back.
        print(
            f"[garden_notify] DM failed for user={message.from_user.id}: "
            f"{type(exc).__name__}: {exc}",
            flush=True,
        )

    return False
