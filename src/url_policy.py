"""Conservative URL exclusions for known listing pages in publication maps."""

from urllib.parse import urlsplit


PCGAMER_GAME_LISTINGS = {
    "action", "adventure", "city-builder", "fps", "guides", "mmo",
    "news", "puzzle", "quizzes", "reviews", "roguelike", "rpg",
    "strategy", "survival-crafting",
}


def is_listing_url(source: str, url: str) -> bool:
    """Return true only for page shapes verified as non-publications."""
    parts = [part.casefold() for part in urlsplit(url).path.split("/") if part]
    if source == "eurogamer":
        return bool(parts and parts[0] == "games" and len(parts) <= 2)
    if source == "gamingonlinux":
        return len(parts) >= 2 and parts[:2] == ["articles", "category"]
    if source == "pcgamer":
        return parts == ["news"] or parts == ["games"] or (
            len(parts) == 2 and parts[0] == "games"
            and parts[1] in PCGAMER_GAME_LISTINGS
        )
    return False
