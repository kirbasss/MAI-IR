"""Conservative URL exclusions for known listing pages in publication maps."""

from urllib.parse import urlsplit


PCGAMER_GAME_LISTINGS = {
    "action", "adventure", "city-builder", "fps", "guides", "mmo",
    "news", "puzzle", "quizzes", "reviews", "roguelike", "rpg",
    "strategy", "survival-crafting",
}

# Verified section pages; do not treat every multi-segment article URL as a category.
PCGAMER_SECTION_LISTINGS = {
    ("gaming-industry",), ("hardware",), ("software",),
    ("hardware", "controllers", "game-pads"),
    ("software", "platforms"), ("games", "rpg", "dragon-age"),
}


def is_listing_url(source: str, url: str) -> bool:
    """Return true only for page shapes verified as non-publications."""
    parsed = urlsplit(url)
    parts = [part.casefold() for part in parsed.path.split("/") if part]
    if not parts:
        return bool(parsed.hostname) and source in {
            "eurogamer", "gamingonlinux", "pcgamer", "stopgame",
            "playground", "ixbt_games", "igromania", "gamemag",
        }
    if source == "stopgame":
        return parts[:2] == ["games", "compilation"]
    if source == "eurogamer":
        return parts[0] == "authors" or (parts[0] == "games" and len(parts) <= 2)
    if source == "gamingonlinux":
        return len(parts) >= 2 and parts[:2] == ["articles", "category"]
    if source == "pcgamer":
        if parts[0] in {"author", "tag", "archive"} or parts[:2] == ["news", "archive"]:
            return True
        if len(parts) >= 3 and parts[-2] == "page" and parts[-1].isdigit():
            parts = parts[:-2]
        return tuple(parts) in PCGAMER_SECTION_LISTINGS or parts in (["news"], ["games"]) or (
            len(parts) == 2 and parts[0] == "games"
            and parts[1] in PCGAMER_GAME_LISTINGS
        )
    return False
