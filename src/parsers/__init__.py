from src.parsers.eurogamer import parse_eurogamer
from src.parsers.gamemag import parse_gamemag
from src.parsers.gamingonlinux import parse_gamingonlinux
from src.parsers.igromania import parse_igromania
from src.parsers.ixbt_games import parse_ixbt_games
from src.parsers.pcgamer import parse_pcgamer
from src.parsers.playground import parse_playground
from src.parsers.stopgame import parse_stopgame


PARSERS = {
    "playground": parse_playground,
    "stopgame": parse_stopgame,
    "ixbt_games": parse_ixbt_games,
    "igromania": parse_igromania,
    "gamemag": parse_gamemag,
    "gamingonlinux": parse_gamingonlinux,
    "pcgamer": parse_pcgamer,
    "eurogamer": parse_eurogamer,
}
