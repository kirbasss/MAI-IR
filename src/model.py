from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ParsedDocument:
    """Common representation of a gaming-media publication.

    Missing metadata is represented by ``None`` or an empty list; a parser
    must never infer it from the page text.
    """

    source: str
    url: str
    title: str | None = None
    author: str | None = None
    published_at: str | None = None
    category: str | None = None
    tags: list[str] = field(default_factory=list)
    games: list[str] = field(default_factory=list)
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    parse_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
