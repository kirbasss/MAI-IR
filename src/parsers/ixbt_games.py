from src.parsers.common import parse_semantic_article
from src.model import ParsedDocument


def parse_ixbt_games(html: bytes, url: str) -> ParsedDocument:
    return parse_semantic_article("ixbt_games", html, url)
