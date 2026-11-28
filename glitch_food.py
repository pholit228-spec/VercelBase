"""Standalone food extension for BOTIK.

Loaded after the legacy bot so the new food is registered without editing
the large bot.py file.
"""

def install(bot_module):
    items = getattr(bot_module, "FOOD_ITEMS", None)
    if not isinstance(items, list):
        return False

    def register_food(**food):
        def decorator(fn):
            items.append(dict(food))
            return fn
        return decorator

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
