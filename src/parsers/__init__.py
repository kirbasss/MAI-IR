from functools import partial

from src.parsers.common import parse_semantic_article
from src.parsers.igromania import parse_igromania
from src.parsers.ixbt_games import parse_ixbt_games
from src.parsers.playground import parse_playground
from src.parsers.stopgame import parse_stopgame


PARSERS = {
    "playground": parse_playground,
    "stopgame": parse_stopgame,
    "ixbt_games": parse_ixbt_games,
    "igromania": parse_igromania,
    # These sources are enabled only to gather and inspect fixtures.  Their
    # semantic fallback is deliberately marked as requiring selector
    # validation; it will be replaced by source-specific parsers afterwards.
    "gamemag": partial(parse_semantic_article, "gamemag"),
    "gamingonlinux": partial(parse_semantic_article, "gamingonlinux"),
    "pcgamer": partial(parse_semantic_article, "pcgamer"),
    "eurogamer": partial(parse_semantic_article, "eurogamer"),
}
