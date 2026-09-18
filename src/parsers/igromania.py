from src.parsers.common import parse_semantic_article
from src.model import ParsedDocument


def parse_igromania(html: bytes, url: str) -> ParsedDocument:
    return parse_semantic_article("igromania", html, url)
