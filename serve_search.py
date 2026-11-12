"""Lab 8 HTML interface. All query parsing, morphology and search run in C++."""
from __future__ import annotations
import argparse
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import struct
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
SOURCES = ("gamemag", "eurogamer", "gamingonlinux", "igromania", "ixbt_games", "pcgamer", "playground", "stopgame")
TYPES = (("news", "Новости"), ("article", "Статьи"), ("review", "Обзоры"),
         ("blog", "Блоги"), ("unknown", "Без типа"))


def filter_options(path: Path):
    data = path.read_bytes()
    if len(data) < 16 or data[:8] != b"MAIFLT1\0" or len(data) != 16 + 4 * struct.unpack_from("<Q", data, 8)[0]:
        raise ValueError("Повреждён каталог фильтров")
    source_ids, type_ids, years = set(), set(), set()
    for year, kind, source in struct.iter_unpack("<HBB", memoryview(data)[16:]):
        if not 1 <= kind <= len(TYPES) or not 1 <= source <= len(SOURCES):
            raise ValueError("Повреждён каталог фильтров")
        source_ids.add(source)
        type_ids.add(kind)
        if year:
            years.add(year)
    return {"sources": [SOURCES[i - 1] for i in sorted(source_ids)],
            "types": [TYPES[i - 1] for i in sorted(type_ids)], "years": sorted(years, reverse=True)}


class NativeSearch:
    def __init__(self, binary: Path, index: Path, dictionary: Path | None, positions: Path | None = None, compact: Path | None = None, skips: Path | None = None, norms: Path | None = None, corpus: Path | None = None, snippet_index: Path | None = None, filters: Path | None = None):
        self.binary, self.index, self.dictionary = binary, index, dictionary
        self.positions = positions
        self.compact = compact
        self.skips = skips
        self.norms = norms
        self.corpus = corpus
        self.snippet_index = snippet_index
        self.filters = filters
        self.filter_choices = filter_options(filters) if filters else {"sources": [], "types": [], "years": []}

    def search(self, query: str, space: str, ranking: str = "boolean", *, source="", kind="", year=""):
        if ranking not in ("boolean", "tfidf"):
            raise ValueError("Неизвестный способ ранжирования")
        if ranking == "tfidf" and (not self.norms or not self.positions):
            raise ValueError("Для TF-IDF нужны координатный индекс и нормы документов")
        if space not in ("surface", "lemma"):
            raise ValueError("Неизвестный режим поиска")
        if not query.strip() or len(query.encode("utf-8")) > 8192 or "\0" in query:
            raise ValueError("Запрос должен содержать от 1 до 8192 байт UTF-8 без NUL")
        if space == "lemma" and not self.dictionary:
            raise ValueError("Для поиска по леммам нужен морфологический словарь")
        if (source or kind or year) and not self.filters:
            raise ValueError("Для фильтрации нужен каталог --filters")
        if source and source not in self.filter_choices["sources"]:
            raise ValueError("Неизвестный сайт")
        if kind and kind not in dict(self.filter_choices["types"]):
            raise ValueError("Неизвестный тип публикации")
        if year and (not year.isdecimal() or int(year) not in self.filter_choices["years"]):
            raise ValueError("Неизвестный год публикации")
        command = [str(self.binary), "--index", str(self.index), "--space", space, "--limit", "20"]
        if self.filters and (source or kind or year):
            command += ["--filters", str(self.filters)]
        for flag, value in (("--filter-source", source), ("--filter-type", kind), ("--filter-year", year)):
            if value:
                command += [flag, value]
        if ranking == "tfidf":
            command += ["--ranking", "tfidf", "--norms", str(self.norms)]
        if self.positions:
            command += ["--positions", str(self.positions)]
        if self.compact:
            command += ["--compact", str(self.compact)]
        if self.skips:
            command += ["--skips", str(self.skips)]
        if self.corpus:
            command += ["--corpus", str(self.corpus)]
        if self.snippet_index:
            command += ["--snippet-index", str(self.snippet_index)]
        if space == "lemma":
            command += ["--dict", str(self.dictionary)]
        # stdin preserves UTF-8 on Windows and avoids command-shell interpretation.
        if "\n" in query or "\r" in query:
            raise ValueError("Введите запрос в одну строку")
        try:
            result = subprocess.run(command, input=(query+"\n").encode("utf-8"),
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Превышено время выполнения запроса (60 с)") from exc
        if not result.stdout:
            print(result.stderr.decode("utf-8", errors="replace").strip(), file=sys.stderr)
            raise ValueError("Не удалось выполнить поиск. Проверьте доступность индекса и словаря.")
        row = json.loads(result.stdout.decode("utf-8"))
        if "error" in row:
            raise ValueError(row["error"])
        if result.returncode:
            raise ValueError("Ошибка выполнения поиска")
        return row


def render_snippet(doc):
    value = doc.get("snippet")
    if not isinstance(value, str) or not value:
        return ""
    data = value.encode("utf-8")
    parts = []
    previous = 0
    try:
        for pair in doc.get("snippet_matches", []):
            begin, end = pair
            if not isinstance(begin, int) or not isinstance(end, int) or not previous <= begin < end <= len(data):
                raise ValueError("Invalid snippet span")
            parts.append(escape(data[previous:begin].decode("utf-8")))
            parts.append("<mark>" + escape(data[begin:end].decode("utf-8")) + "</mark>")
            previous = end
        parts.append(escape(data[previous:].decode("utf-8")))
    except (ValueError, TypeError, UnicodeDecodeError):
        parts = [escape(value)]
    prefix = "… " if doc.get("snippet_left") else ""
    suffix = " …" if doc.get("snippet_right") else ""
    return '<p class="snippet">' + prefix + "".join(parts) + suffix + "</p>"


def page(query="", space="surface", result=None, error=None, ranking="boolean", *, source="", kind="", year="", choices=None):
    selected = lambda value: " selected" if value == space else ""
    choices = choices or {"sources": [], "types": [], "years": []}
    def option(value, label, active):
        return f'<option value="{escape(str(value), quote=True)}"{" selected" if str(value) == active else ""}>{escape(str(label))}</option>'
    source_options = option("", "Все сайты", source) + "".join(option(value, value, source) for value in choices["sources"])
    type_options = option("", "Все типы", kind) + "".join(option(value, label, kind) for value, label in choices["types"])
    year_options = option("", "Все годы", year) + "".join(option(value, value, year) for value in choices["years"])
    body = ""
    if error:
        body = '<p role="alert" class="error">'+escape(str(error))+'</p>'
    elif result is not None:
        order = "TF-IDF по убыванию" if result.get("ranking") == "tfidf" else "ID по возрастанию"
        body = f'<p>Найдено: <b>{int(result["total"]):,}</b>. Поиск: {float(result["elapsed_ms"]):.2f} мс. Порядок: {order}.</p><ol>'
        for doc in result["results"]:
            title = escape(doc["title"] or "Без заголовка")
            url = doc["url"]
            if urlsplit(url).scheme.lower() in ("http", "https"):
                title = '<a href="'+escape(url, quote=True)+'" rel="noreferrer">'+title+'</a>'
            score = f' · TF-IDF {float(doc["score"]):.5f}' if "score" in doc else ""
            body += '<li>'+title+render_snippet(doc)+'<p class="meta">ID '+str(int(doc["id"]))+' · '+escape(doc["source"])+score+'</p></li>'
        body += '</ol>'
        if result["total"] > len(result["results"]):
            body += '<p>Показаны первые '+str(len(result["results"]))+' документов.</p>'
    return f'''<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Поиск по игровым публикациям</title>
<style>body{{font:17px/1.5 system-ui,sans-serif;max-width:900px;margin:50px auto;padding:0 20px;color:#202b38;background:#f8fafc}}
h1{{font-size:30px}}form{{display:flex;gap:10px;flex-wrap:wrap}}input,select,button{{font:inherit;padding:10px;border:1px solid #acb8c6;border-radius:6px}}
input{{flex:1;min-width:260px}}button{{background:#194da0;color:white;cursor:pointer}}a{{color:#194da0}}.reset{{align-self:center}}li{{padding:9px 0}}.meta{{font-size:14px;color:#536273;margin:3px 0}}.snippet{{font-size:15px;color:#637181;margin:5px 0;line-height:1.45}}mark{{background:#ffea8a;color:#202b38;border-radius:2px;padding:0 1px}}.error{{color:#a62222}}</style>
<h1>Поиск по игровым публикациям</h1><form method="get" action="/">
<input name="q" aria-label="Запрос" value="{escape(query, quote=True)}" placeholder="doom AND (ремейк OR remake)">
<select name="space" aria-label="Режим"><option value="surface"{selected("surface")}>Словоформы</option><option value="lemma"{selected("lemma")}>Леммы</option></select>
<select name="ranking" aria-label="Порядок выдачи"><option value="boolean"{" selected" if ranking == "boolean" else ""}>Булев поиск</option><option value="tfidf"{" selected" if ranking == "tfidf" else ""}>TF-IDF</option></select>
<select name="source" aria-label="Сайт"{" disabled" if not choices["sources"] else ""}>{source_options}</select>
<select name="kind" aria-label="Тип публикации"{" disabled" if not choices["types"] else ""}>{type_options}</select>
<select name="year" aria-label="Год публикации"{" disabled" if not choices["years"] else ""}>{year_options}</select>
<button>Найти</button><a href="/" class="reset">Сбросить</a></form><p class="meta">Операторы: NOT → AND → OR, скобки. Между словами указывайте оператор.
Пример: (doom OR quake) AND NOT minecraft. Фразы в двойных кавычках требуют соседства слов в указанном порядке.
Поиск по леммам учитывает все словарные разборы.</p><p class="meta">В режиме TF-IDF обычные слова, например doom eternal, влияют на оценку релевантности.
Фраза в двойных кавычках обязательна целиком, например "doom eternal". Несколько фраз также обязательны; остальные слова влияют на оценку. AND, OR, NOT считаются словами запроса.</p>{body}</html>'''


def handler(backend):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path != "/":
                self.send_error(404)
                return
            query, space, result, error, status = "", "surface", None, None, 200
            ranking, source, kind, year = "boolean", "", "", ""
            try:
                if len(self.path) > 32768:
                    raise ValueError("Слишком длинный запрос")
                values = parse_qs(parsed.query, encoding="utf-8", errors="strict")
                query = values.get("q", [""])[0]
                space = values.get("space", ["surface"])[0]
                ranking = values.get("ranking", ["boolean"])[0]
                source = values.get("source", [""])[0]
                kind = values.get("kind", [""])[0]
                year = values.get("year", [""])[0]
                if ranking not in ("boolean", "tfidf"):
                    raise ValueError("Неизвестный способ ранжирования")
                if "q" in values:
                    if source or kind or year:
                        result = backend.search(query, space, ranking, source=source, kind=kind, year=year)
                    else:
                        result = backend.search(query, space, ranking) if ranking == "tfidf" else backend.search(query, space)
            except (ValueError, OSError) as exc:
                error, status = str(exc), 400
            encoded = page(query, space, result, error, ranking, source=source, kind=kind, year=year,
                           choices=getattr(backend, "filter_choices", None)).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(encoded)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--dict", type=Path)
    parser.add_argument("--positions", type=Path, help="positional index for quoted phrases")
    parser.add_argument("--compact", type=Path, help="compressed postings and positions from Lab 11")
    parser.add_argument("--skips", type=Path, help="skip checkpoints from Lab 12")
    parser.add_argument("--norms", type=Path, help="TF-IDF document norms from Lab 13")
    parser.add_argument("--corpus", type=Path, help="MAIIR3 snapshot for query-dependent snippets")
    parser.add_argument("--snippet-index", type=Path, help="snippet offsets from boolean_search --build-snippet-index")
    parser.add_argument("--filters", type=Path, help="catalog from build_search_filters.py")
    parser.add_argument("--binary", type=Path, default=ROOT/"build/boolean-native/boolean_search.exe")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if not args.binary.is_file() or not (args.index/"terms.bin").is_file():
        parser.error("Сначала соберите boolean_search и укажите готовый индекс ЛР7")
    if args.corpus and not args.corpus.is_file():
        parser.error("Укажите существующий MAIIR3 corpus.bin для сниппетов")
    if args.snippet_index and (not args.corpus or not args.snippet_index.is_file()):
        parser.error("Для смещений сниппетов нужны --corpus и готовый --snippet-index")
    if args.filters and not args.filters.is_file():
        parser.error("Укажите существующий каталог фильтров")
    backend = NativeSearch(args.binary.resolve(), args.index.resolve(), args.dict.resolve() if args.dict else None,
                           args.positions.resolve() if args.positions else None,
                           args.compact.resolve() if args.compact else None,
                           args.skips.resolve() if args.skips else None,
                           args.norms.resolve() if args.norms else None,
                           args.corpus.resolve() if args.corpus else None,
                           args.snippet_index.resolve() if args.snippet_index else None,
                           args.filters.resolve() if args.filters else None)
    # Sequential server bounds simultaneous native workers and dictionary memory.
    with HTTPServer((args.host, args.port), handler(backend)) as server:
        print(f"Поиск: http://{args.host}:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
