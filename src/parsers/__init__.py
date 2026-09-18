from src.parsers.igromania import parse_igromania
from src.parsers.ixbt_games import parse_ixbt_games
from src.parsers.playground import parse_playground
from src.parsers.stopgame import parse_stopgame


PARSERS = {
    "playground": parse_playground,
    "stopgame": parse_stopgame,
    "ixbt_games": parse_ixbt_games,
    "igromania": parse_igromania,
}
