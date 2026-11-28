"""Standalone food extension for BOTIK.

Loaded after the legacy bot so the new food is registered without editing the
large bot.py file. The decorator keeps future custom foods easy to add.
"""

def register_food(**item):
    def decorator(fn):
        try:
            items = getattr(fn.__globals__.get("bot_module"), "FOOD_ITEMS", None)
        except Exception:
            items = None
        if items is not None:
            items.append(dict(item))
        return fn
    return decorator


def install(bot_module):
    # Prevent duplicate registration on Vercel warm invocations.
    items = getattr(bot_module, "FOOD_ITEMS", None)
    if not isinstance(items, list):
        return False
    if any(str(x.get("id")) == "bug" for x in items if isinstance(x, dict)):
        return True

    @register_food(
        id="bug",
        name="Баг",
        category="fruit",
        favorite_of="Glitch Slime",
        edible_by_slimes=True,
        notes="Любимая еда Глитч-слайма.",
    )
    def _bug_food():
        pass

    return True
