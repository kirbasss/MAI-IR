"""Lab 8 HTML interface. All query parsing, morphology and search run in C++."""
from __future__ import annotations
import argparse
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent


class NativeSearch:
    def __init__(self, binary: Path, index: Path, dictionary: Path | None, positions: Path | None = None):
        self.binary, self.index, self.dictionary = binary, index, dictionary
        self.positions = positions

    def search(self, query: str, space: str):
        if space not in ("surface", "lemma"):
            raise ValueError("Неизвестный режим поиска")
        if not query.strip() or len(query.encode("utf-8")) > 8192 or "\0" in query:
            raise ValueError("Запрос должен содержать от 1 до 8192 байт UTF-8 без NUL")
        if space == "lemma" and not self.dictionary:
            raise ValueError("Для поиска по леммам нужен морфологический словарь")
        command = [str(self.binary), "--index", str(self.index), "--space", space, "--limit", "20"]
        if self.positions:
            command += ["--positions", str(self.positions)]
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


def page(query="", space="surface", result=None, error=None):
    selected = lambda value: " selected" if value == space else ""
    body = ""
    if error:
        body = '<p role="alert" class="error">'+escape(str(error))+'</p>'
    elif result is not None:
        body = f'<p>Найдено: <b>{int(result["total"]):,}</b>. Поиск: {float(result["elapsed_ms"]):.2f} мс. Порядок: ID по возрастанию.</p><ol>'
        for doc in result["results"]:
            title = escape(doc["title"] or "Без заголовка")
            url = doc["url"]
            if urlsplit(url).scheme.lower() in ("http", "https"):
                title = '<a href="'+escape(url, quote=True)+'" rel="noreferrer">'+title+'</a>'
            body += '<li>'+title+'<p class="meta">ID '+str(int(doc["id"]))+' · '+escape(doc["source"])+'</p></li>'
        body += '</ol>'
        if result["total"] > len(result["results"]):
            body += '<p>Показаны первые '+str(len(result["results"]))+' документов.</p>'
    return f'''<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Поиск по игровым публикациям</title>
<style>body{{font:17px/1.5 system-ui,sans-serif;max-width:900px;margin:50px auto;padding:0 20px;color:#202b38;background:#f8fafc}}
h1{{font-size:30px}}form{{display:flex;gap:10px;flex-wrap:wrap}}input,select,button{{font:inherit;padding:10px;border:1px solid #acb8c6;border-radius:6px}}
input{{flex:1;min-width:260px}}button{{background:#194da0;color:white;cursor:pointer}}a{{color:#194da0}}li{{padding:9px 0}}.meta{{font-size:14px;color:#536273;margin:3px 0}}.error{{color:#a62222}}</style>
<h1>Поиск по игровым публикациям</h1><form method="get" action="/">
<input name="q" aria-label="Запрос" value="{escape(query, quote=True)}" placeholder="doom AND (ремейк OR remake)">
<select name="space" aria-label="Режим"><option value="surface"{selected("surface")}>Словоформы</option><option value="lemma"{selected("lemma")}>Леммы</option></select>
<button>Найти</button></form><p class="meta">Операторы: NOT → AND → OR, скобки. Между словами указывайте оператор.
Пример: (doom OR quake) AND NOT minecraft. Фразы в двойных кавычках требуют соседства слов в указанном порядке.
Поиск по леммам учитывает все словарные разборы.</p>{body}</html>'''


def handler(backend):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            if parsed.path != "/":
                self.send_error(404)
                return
            query, space, result, error, status = "", "surface", None, None, 200
            try:
                if len(self.path) > 32768:
                    raise ValueError("Слишком длинный запрос")
                values = parse_qs(parsed.query, encoding="utf-8", errors="strict")
                query = values.get("q", [""])[0]
                space = values.get("space", ["surface"])[0]
                if "q" in values:
                    result = backend.search(query, space)
            except (ValueError, OSError) as exc:
                error, status = str(exc), 400
            encoded = page(query, space, result, error).encode("utf-8")
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
    parser.add_argument("--binary", type=Path, default=ROOT/"build/boolean-native/boolean_search.exe")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    if not args.binary.is_file() or not (args.index/"terms.bin").is_file():
        parser.error("Сначала соберите boolean_search и укажите готовый индекс ЛР7")
    backend = NativeSearch(args.binary.resolve(), args.index.resolve(), args.dict.resolve() if args.dict else None,
                           args.positions.resolve() if args.positions else None)
    # Sequential server bounds simultaneous native workers and dictionary memory.
    with HTTPServer((args.host, args.port), handler(backend)) as server:
        print(f"Поиск: http://{args.host}:{server.server_port}/", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
