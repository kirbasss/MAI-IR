"""Show the C++ tokenizer on unchanged excerpts from a real corpus snapshot."""

from __future__ import annotations

import argparse
import csv
import io
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from benchmark_tokenizer import SOURCE_NAMES
from build_tokenizer import DEFAULT_BINARY, ROOT, ensure_built
from src.token_corpus import snapshot_records


VERSION = re.compile(r"(?<![\w.])(?:[vV])?[0-9]+(?:\.[0-9]+)+(?:-[A-Za-z0-9]+)?(?!\w)")
EXACT_VERSION = re.compile(r"(?<![\w.])3\.0\.1(?!\w|\.[0-9])")
LANGUAGE = re.compile(r"(?<![\w+#])[Cc](?:\+\+|#)[0-9]*(?![\w+#])")
STALKER = re.compile(r"(?<!\w)S\.T\.A\.L\.K\.E\.R\.?", re.IGNORECASE)
HALF_LIFE = re.compile(r"(?<!\w)Half[-\u2010\u2011]Life(?!\w)", re.IGNORECASE)


@dataclass(frozen=True)
class Example:
    document_id: int
    source: str
    title: str
    field: str
    text: str
    start: int  # Character offsets of the interesting expression, not TSV offsets.
    end: int
    kind: str


def latest_snapshot(root: Path = ROOT / "data" / "tokenization") -> Path:
    candidates = []
    for path in root.rglob("manifest.json"):
        # Do not select an export that has not been atomically published yet.
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            if manifest.get("format") == "MAIIR3-v1" and (path.parent / "corpus.bin").is_file():
                candidates.append((manifest["created_at"], path.parent))
        except (OSError, ValueError, KeyError):
            continue
    if not candidates:
        raise ValueError("Нет снимка корпуса. Сначала выполните python export_corpus.py "
                         "--config crawler.large.yaml --output data/tokenization/snapshot_001")
    return max(candidates, key=lambda item: (item[0], str(item[1])))[1]


def language_span(text: str) -> tuple[int, int, bool] | None:
    first = None
    previous = {}
    for match in LANGUAGE.finditer(text):
        if first is None:
            first = match
        kind = "plus" if "+" in match.group() else "sharp"
        other = previous.get("sharp" if kind == "plus" else "plus")
        if other is not None and match.end() - other.start() <= 160:
            return other.start(), match.end(), True
        previous[kind] = match
    return (first.start(), first.end(), False) if first is not None else None


def select_examples(snapshot: Path, *, document_id: int | None = None,
                    find: str | None = None, field: str = "text") -> list[Example]:
    best: dict[str, tuple[tuple, Example]] = {}
    fallback = None
    custom = document_id is not None or find is not None
    needle = re.compile(re.escape(find), re.IGNORECASE) if find is not None else None
    for current_id, source, title_bytes, text_bytes in snapshot_records(snapshot / "corpus.bin"):
        title = title_bytes.decode("utf-8")
        for name, raw in (("title", title_bytes), ("text", text_bytes)):
            if not raw:
                continue
            text = raw.decode("utf-8")
            if fallback is None or (fallback.field == "title" and name == "text"):
                fallback = Example(current_id, source, title, name, text, 0, 0, "custom")
            if custom:
                if name != field or (document_id is not None and current_id != document_id):
                    continue
                match = needle.search(text) if needle else None
                if needle and match is None:
                    continue
                return [Example(current_id, source, title, name, text,
                                match.start() if match else 0, match.end() if match else 0, "custom")]

            def keep(kind: str, start: int, end: int, priority: tuple) -> None:
                candidate = Example(current_id, source, title, name, text, start, end, kind)
                if kind not in best or priority > best[kind][0]:
                    best[kind] = priority, candidate

            version = EXACT_VERSION.search(text) or VERSION.search(text)
            if version:
                keep("version", version.start(), version.end(),
                     (version.group() == "3.0.1", source == "gamemag", name == "text", -current_id))
            languages = language_span(text)
            if languages:
                keep("language", languages[0], languages[1],
                     (languages[2], source == "pcgamer", name == "text", -current_id))
            for kind, pattern in (("stalker", STALKER), ("half_life", HALF_LIFE)):
                match = pattern.search(text)
                if match:
                    keep(kind, match.start(), match.end(), (name == "text", -current_id))
        if custom and document_id == current_id:
            break
    if custom:
        raise ValueError("Документ или указанный фрагмент не найден в выбранном поле снимка")
    result = [best[key][1] for key in ("version", "language") if key in best]
    limitation = best.get("stalker") or best.get("half_life")
    if limitation:
        result.append(limitation[1])
    return result or ([fallback] if fallback else [])


def tokenize_field(binary: Path, text: str) -> tuple[list[dict], dict]:
    result = subprocess.run([str(binary), "text"], input=text.encode("utf-8"), capture_output=True)
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", errors="replace"))
    rows = list(csv.DictReader(io.StringIO(result.stdout.decode("utf-8")), delimiter="\t"))
    return rows, json.loads(result.stderr)


def observation(example: Example, focus: list[dict]) -> str:
    terms = [row["term"] for row in focus]
    if example.kind == "version":
        return f"Версия сохранена одним токеном: {terms[0]}." if len(terms) == 1 else f"Фрагмент дал токены: {', '.join(terms)}."
    if example.kind == "language":
        names = [term for term in terms if term.startswith("c++") or term.startswith("c#")]
        return "Специальные обозначения сохраняются и отличаются от c: " + ", ".join(names) + "."
    if example.kind == "stalker":
        if terms == ["stalker"]:
            return "Точки удалены, название сохранено одним токеном: stalker. Написание STALKER даст тот же терм."
        return "Фрагмент дал токены: " + ", ".join(terms) + ". Полная цепочка не соответствует правилу одиночных букв."
    if example.kind == "half_life":
        return "Название с дефисом сохранено. Написание Half Life по-прежнему даст два отдельных токена."
    return "Позиции отсчитываются от начала выбранного поля; original взят по байтовым границам из исходного текста."


def render_example(example: Example, url: str, binary: Path, *, context: int = 5) -> str:
    rows, summary = tokenize_field(binary, example.text)
    raw = example.text.encode("utf-8")
    begin = len(example.text[:example.start].encode("utf-8"))
    end = len(example.text[:example.end].encode("utf-8"))
    indices = [i for i, row in enumerate(rows) if int(row["end_byte"]) > begin and int(row["begin_byte"]) < end]
    if not indices:
        indices = [0] if rows else []
    selected = rows[max(0, indices[0] - context):indices[-1] + context + 1] if indices else []
    focus = [rows[i] for i in indices]
    start_byte = int(selected[0]["begin_byte"]) if selected else 0
    end_byte = int(selected[-1]["end_byte"]) if selected else 0
    displayed = []
    for row in selected:
        start, stop = int(row["begin_byte"]), int(row["end_byte"])
        original = raw[start:stop].decode("utf-8")
        displayed.append([row["position"], f"[{start},{stop})", original, row["term"]])
    headers = ["position", "bytes", "original", "normalized"]
    widths = [max(len(headers[column]), *(len(row[column]) for row in displayed))
              for column in range(4)] if displayed else list(map(len, headers))

    def table_line(values):
        return "  ".join(value.ljust(width) for value, width in zip(values, widths)).rstrip()

    lines = ["=" * 76, f"SOURCE: {SOURCE_NAMES.get(example.source, example.source)}",
             f"DOCUMENT: {example.document_id}", f"TITLE: {example.title}", f"URL: {url}",
             f"FIELD: {example.field}    RULES: {summary['rules_version']}", "",
             f"ORIGINAL [UTF-8 bytes {start_byte}:{end_byte}]:", raw[start_byte:end_byte].decode("utf-8"),
             "", "TOKENS:", table_line(headers), "-" * (sum(widths) + 6),
             *(table_line(row) for row in displayed), "", "OBSERVATION:", observation(example, focus), ""]
    return "\n".join(lines)


def demonstration(snapshot: Path, binary: Path, *, document_id: int | None = None,
                  find: str | None = None, field: str = "text", context: int = 5) -> str:
    snapshot = snapshot.resolve()
    examples = select_examples(snapshot, document_id=document_id, find=find, field=field)
    if not examples:
        raise ValueError("Снимок не содержит текстов для демонстрации")
    wanted = {example.document_id for example in examples}
    urls = {}
    with (snapshot / "documents.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            document = json.loads(line)
            if document["document_id"] in wanted:
                urls[document["document_id"]] = document["url"]
    if urls.keys() != wanted:
        raise ValueError("Не найдены метаданные выбранных документов")
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    header = (f"SNAPSHOT: {snapshot}\nDOCUMENTS: {manifest['documents']}\n"
              "ID относится к снимку. Позиции с нуля и байтовые границы [начало,конец) "
              "относятся к целому полю статьи, а не к показанному фрагменту.\n\n")
    return header + "\n".join(render_example(example, urls[example.document_id], binary, context=context)
                                for example in examples)


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, help="default: latest published snapshot")
    parser.add_argument("--binary", type=Path, help="default: automatically build current C++ sources")
    parser.add_argument("--document-id", type=int)
    parser.add_argument("--find", help="literal fragment to locate; Python only selects text, C++ tokenizes it")
    parser.add_argument("--field", choices=("title", "text"), default="text")
    parser.add_argument("--context", type=int, default=5, help="tokens on each side of the interesting expression")
    parser.add_argument("--output", type=Path, help="also save the UTF-8 transcript to a new file")
    args = parser.parse_args()
    if not 0 <= args.context <= 20:
        parser.error("--context must be between 0 and 20")
    if args.document_id is not None and args.document_id < 1:
        parser.error("--document-id must be positive")
    if args.find == "":
        parser.error("--find must not be empty")
    try:
        binary = args.binary.resolve() if args.binary else ensure_built(DEFAULT_BINARY)
        text = demonstration(args.snapshot or latest_snapshot(), binary, document_id=args.document_id,
                             find=args.find, field=args.field, context=args.context)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
        print(text, end="")
        return 0
    except (OSError, ValueError, RuntimeError, UnicodeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"[demo] {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
