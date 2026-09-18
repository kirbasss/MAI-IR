from src.parsers.common import parse_semantic_article
from src.model import ParsedDocument


def parse_playground(html: bytes, url: str) -> ParsedDocument:
    return parse_semantic_article("playground", html, url)
