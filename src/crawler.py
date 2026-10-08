"""MongoDB-backed URL frontier and a polite, resumable single-worker crawler."""

from __future__ import annotations

import csv
import hashlib
import time
import uuid
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib import robotparser
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
import yaml
from pymongo import ReturnDocument
from pymongo.errors import BulkWriteError, DocumentTooLarge

from src.downloader import SOURCE_HOSTS, _response_class
from src.parsers import PARSERS
from src.utils import canonicalize_source_url, canonicalize_url


USER_AGENT = "MAI-IR-Crawler/1.0 (educational search project)"
MAX_REDIRECTS = 5


@dataclass(frozen=True)
class CrawlerConfig:
    db_uri: str
    db_name: str
    inventory: Path
    delay_seconds: float
    revisit_seconds: int
    retry_seconds: int
    lease_seconds: int
    request_timeout_seconds: float
    seed_batch_size: int
    max_documents: int | None
    run_forever: bool
    poll_seconds: float


def load_config(path: Path) -> CrawlerConfig:
    """Read and validate the sole command-line input."""
    path = path.resolve()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("YAML должен содержать объект с секциями db и logic.")
    db, logic = raw.get("db"), raw.get("logic")
    if not isinstance(db, dict) or not isinstance(logic, dict):
        raise ValueError("В YAML необходимы секции db и logic.")
    if not isinstance(db.get("uri"), str) or not db["uri"].strip():
        raise ValueError("db.uri должен содержать адрес MongoDB.")
    if not isinstance(db.get("name"), str) or not db["name"].strip():
        raise ValueError("db.name должен содержать имя базы данных.")
    if not isinstance(logic.get("inventory"), str) or not logic["inventory"].strip():
        raise ValueError("logic.inventory должен содержать путь к списку URL.")
    inventory = Path(logic["inventory"])
    if not inventory.is_absolute():
        inventory = path.parent / inventory
    inventory = inventory.resolve()
    if not inventory.is_file():
        raise ValueError(f"Список URL не найден: {inventory}")

    def number(key: str, default: int | float, *, positive: bool = False) -> int | float:
        value = logic.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"logic.{key} должен быть числом.")
        if (positive and value <= 0) or (not positive and value < 0):
            raise ValueError(f"logic.{key} имеет недопустимое значение: {value}.")
        return value

    max_documents = logic.get("max_documents")
    if max_documents is not None and (
        isinstance(max_documents, bool) or not isinstance(max_documents, int) or max_documents < 1
    ):
        raise ValueError("logic.max_documents должен быть положительным целым числом.")
    run_forever = logic.get("run_forever", False)
    if not isinstance(run_forever, bool):
        raise ValueError("logic.run_forever должен быть true или false.")
    seed_batch_size = number("seed_batch_size", 500, positive=True)
    lease_seconds = number("lease_seconds", 600, positive=True)
    revisit_seconds = number("revisit_seconds", 2_592_000, positive=True)
    retry_seconds = number("retry_seconds", 3600, positive=True)
    if any(not isinstance(value, int) for value in (
        seed_batch_size, lease_seconds, revisit_seconds, retry_seconds
    )):
        raise ValueError("Интервалы, lease_seconds и seed_batch_size должны быть целыми.")
    request_timeout_seconds = float(number("request_timeout_seconds", 30, positive=True))
    if lease_seconds <= request_timeout_seconds * (2 * MAX_REDIRECTS + 2):
        raise ValueError("logic.lease_seconds должен превышать время цепочки запросов.")
    return CrawlerConfig(
        db_uri=db["uri"], db_name=db["name"], inventory=inventory,
        delay_seconds=float(number("delay_seconds", 2)),
        revisit_seconds=revisit_seconds, retry_seconds=retry_seconds,
        lease_seconds=lease_seconds, request_timeout_seconds=request_timeout_seconds,
        seed_batch_size=seed_batch_size, max_documents=max_documents,
        run_forever=run_forever,
        poll_seconds=float(number("poll_seconds", 60, positive=True)),
    )


def inventory_rows(path: Path):
    """Stream either source/URL pairs or the four-column sitemap inventory."""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle, delimiter="\t")
        for line_no, fields in enumerate(reader, 1):
            if not fields or not any(fields) or fields[0].lstrip().startswith("#"):
                continue
            if fields[:4] == ["source", "category", "url", "sitemap_url"]:
                continue
            if len(fields) == 2:
                source, url = fields
                category = None
            elif len(fields) == 4:
                source, category, url, _ = fields
            else:
                raise ValueError(f"{path}:{line_no}: ожидаются 2 или 4 поля TSV.")
            source = source.strip().lower()
            url = canonicalize_source_url(source, url.strip())
            host = urlsplit(url).hostname
            if source not in PARSERS or host not in SOURCE_HOSTS[source]:
                raise ValueError(f"{path}:{line_no}: неизвестный источник или хост: {source} {url}")
            if urlsplit(url).scheme not in {"http", "https"}:
                raise ValueError(f"{path}:{line_no}: нужен HTTP(S) URL: {url}")
            yield source, url, category


class RobotsDenied(RuntimeError):
    pass


class RobotsUnavailable(RobotsDenied):
    """The rules could not be verified, so the whole source must wait."""


class HttpGate:
    """Check robots.txt for every target and space requests to each host."""

    def __init__(self, session: requests.Session, config: CrawlerConfig):
        self.session = session
        self.config = config
        self.rules: dict[str, robotparser.RobotFileParser] = {}
        self.last_request: dict[str, float] = {}

    def _send(self, url: str, *, headers: dict[str, str] | None = None):
        host = urlsplit(url).hostname or ""
        parser = self.rules.get(host)
        crawl_delay = parser.crawl_delay(USER_AGENT) if parser else None
        delay = max(self.config.delay_seconds, float(crawl_delay or 0))
        wait = self.last_request.get(host, 0) + delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        try:
            return self.session.get(
                url, headers=headers, timeout=self.config.request_timeout_seconds,
                allow_redirects=False,
            )
        finally:
            self.last_request[host] = time.monotonic()

    def _check_robots(self, source: str, url: str) -> None:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if host not in self.rules:
            robots_url = urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
            try:
                for _ in range(MAX_REDIRECTS + 1):
                    response = self._send(robots_url)
                    if response.status_code not in {301, 302, 303, 307, 308}:
                        break
                    location = response.headers.get("Location")
                    if not location:
                        raise RobotsUnavailable(f"robots.txt перенаправлен без Location: {robots_url}")
                    robots_url = urljoin(robots_url, location)
                    destination = urlsplit(robots_url)
                    if destination.hostname not in SOURCE_HOSTS[source] or destination.scheme not in {"http", "https"}:
                        raise RobotsUnavailable(f"robots.txt перенаправлен за пределы источника: {robots_url}")
                else:
                    raise RobotsUnavailable(f"Слишком много редиректов robots.txt: {robots_url}")
                response.raise_for_status()
                if response.status_code != 200:
                    raise RobotsUnavailable(f"Некорректный robots.txt: {robots_url}")
                # robots.txt is plain text, not an HTML page. The HTML challenge
                # heuristic would misread valid Clean-param rules containing
                # words like "captcha" as an anti-bot page.
                content_type = response.headers.get("Content-Type", "").lower()
                if "html" in content_type or response.text.lstrip().lower().startswith(
                    ("<!doctype html", "<html")
                ):
                    raise RobotsUnavailable(f"robots.txt вернул HTML вместо правил: {robots_url}")
                rules = robotparser.RobotFileParser()
                rules.set_url(robots_url)
                rules.parse(response.text.splitlines())
                self.rules[host] = rules
            except requests.RequestException as exc:
                raise RobotsUnavailable(f"robots.txt недоступен для {host}: {exc}") from exc
        if not self.rules[host].can_fetch(USER_AGENT, url):
            raise RobotsDenied(f"robots.txt запрещает {url}")

    def get(self, source: str, url: str, headers: dict[str, str]):
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            host = urlsplit(current).hostname
            if host not in SOURCE_HOSTS[source] or urlsplit(current).scheme not in {"http", "https"}:
                raise RobotsDenied(f"Редирект за пределы источника: {current}")
            self._check_robots(source, current)
            response = self._send(current, headers=headers)
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get("Location")
            if not location:
                raise requests.RequestException("Редирект без Location")
            current = canonicalize_url(urljoin(current, location))
        raise requests.RequestException("Слишком длинная цепочка редиректов")


class Crawler:
    def __init__(self, config: CrawlerConfig, db, *, session=None, now=None):
        self.config = config
        self.frontier = db["frontier"]
        self.documents = db["documents"]
        self.raw_pages = db["raw_pages"]
        self.meta = db["crawler_meta"]
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "ru,en;q=0.8"})
        self.gate = HttpGate(self.session, config)
        self.now = now or time.time
        self.source_cooldowns: dict[str, int] = {}

    def ensure_indexes(self) -> None:
        # claim() sorts by all three fields. A single-field index would still
        # need to sort a large set of equally available sitemap URLs in memory.
        self.frontier.create_index([("available_at", 1), ("queue_order", 1), ("_id", 1)])
        self.frontier.create_index([("state", 1), ("source", 1)])

    def prepare(self) -> None:
        self.ensure_indexes()
        # The laboratory robot has one worker. A restarted process owns all
        # items left in progress by its predecessor and picks them first.
        self.frontier.update_many(
            {"state": "in_progress"},
            {"$set": {"state": "pending", "available_at": -1},
             "$unset": {"lease_token": ""}},
        )
        self.source_cooldowns = {}
        for source in SOURCE_HOSTS:
            pause = self.meta.find_one({"_id": f"cooldown:{source}"}, {"until": 1})
            if pause:
                self.source_cooldowns[source] = int(pause.get("until", 0))

    def seed(self) -> int:
        """Populate the durable frontier without loading the inventory into RAM."""
        stat = self.config.inventory.stat()
        marker = f"{self.config.inventory}:{stat.st_size}:{stat.st_mtime_ns}"
        if self.meta.find_one({"_id": "inventory", "marker": marker}):
            return 0
        batch: dict[str, dict] = {}
        added = 0
        for source, url, category in inventory_rows(self.config.inventory):
            batch[url] = {
                "_id": url, "url": url, "source": source,
                "category_hint": category, "state": "pending",
                "available_at": 0, "attempts": 0,
                # Stable shuffle distributes a batch across sites rather
                # than exhausting one alphabetically sorted sitemap first.
                "queue_order": int.from_bytes(
                    hashlib.sha256(url.encode("utf-8")).digest()[:8], "big"
                ) & 0x7fff_ffff_ffff_ffff,
            }
            if len(batch) >= self.config.seed_batch_size:
                added += self._insert_batch(list(batch.values()))
                batch.clear()
        if batch:
            added += self._insert_batch(list(batch.values()))
        self.meta.update_one(
            {"_id": "inventory"}, {"$set": {"marker": marker, "seeded_at": int(self.now())}},
            upsert=True,
        )
        return added

    def _insert_batch(self, rows: list[dict]) -> int:
        try:
            return len(self.frontier.insert_many(rows, ordered=False).inserted_ids)
        except BulkWriteError as exc:
            # Replaying a partially seeded inventory is expected after an
            # interrupted run. Only duplicate _id errors may be ignored.
            details = exc.details or {}
            if any(error.get("code") != 11000 for error in details.get("writeErrors", [])):
                raise
            return int(details.get("nInserted", 0))

    def claim(self, now: int):
        token = uuid.uuid4().hex
        query = {"available_at": {"$lte": now}}
        cooling = [source for source, until in self.source_cooldowns.items() if until > now]
        if cooling:
            query["source"] = {"$nin": cooling}
        return self.frontier.find_one_and_update(
            query,
            {"$set": {
                "state": "in_progress", "available_at": now + self.config.lease_seconds,
                "lease_token": token,
            }, "$inc": {"attempts": 1}},
            sort=[("available_at", 1), ("queue_order", 1), ("_id", 1)],
            return_document=ReturnDocument.AFTER,
        )

    def _finish(self, task: dict, now: int, *, state: str, delay: int,
                fields: dict | None = None, unset: dict | None = None) -> None:
        values = {
            "state": state, "available_at": now + delay,
            "last_checked_at": now, **(fields or {}),
        }
        self.frontier.update_one(
            {"_id": task["_id"], "lease_token": task["lease_token"]},
            {"$set": values, "$unset": {"lease_token": "", **(unset or {})}},
        )

    def _retry_delay(self, response, now: int) -> int:
        delay = self.config.retry_seconds
        if response.status_code not in {429, 503}:
            return delay
        value = response.headers.get("Retry-After", "").strip()
        try:
            return max(delay, int(value))
        except ValueError:
            try:
                date = parsedate_to_datetime(value).astimezone(UTC)
            except (TypeError, ValueError, OverflowError):
                return delay
            return max(delay, int(date.timestamp()) - now)

    def _cooldown_source(self, source: str, until: int) -> None:
        until = max(self.source_cooldowns.get(source, 0), until)
        self.source_cooldowns[source] = until
        self.meta.update_one(
            {"_id": f"cooldown:{source}"}, {"$max": {"until": until}}, upsert=True,
        )

    def process(self, task: dict) -> str:
        url, source = task["url"], task["source"]
        previous = self.documents.find_one(
            {"_id": url}, {"content_sha256": 1, "raw_page_id": 1}
        )
        raw_previous = self.raw_pages.find_one({"_id": url}, {"content_sha256": 1})
        raw_matches_previous = bool(
            previous and raw_previous and previous.get("raw_page_id") == url
            and previous.get("content_sha256") == raw_previous.get("content_sha256")
        )
        headers = {}
        if previous:
            if task.get("etag"):
                headers["If-None-Match"] = task["etag"]
            if task.get("last_modified"):
                headers["If-Modified-Since"] = task["last_modified"]
        try:
            response = self.gate.get(source, url, headers)
            if response.status_code == 304 and not raw_matches_previous:
                response = self.gate.get(source, url, {})
        except RobotsUnavailable as exc:
            now = int(self.now())
            self._cooldown_source(source, now + self.config.retry_seconds)
            self._finish(task, now, state="retry", delay=self.config.retry_seconds,
                         fields={"last_error": str(exc)})
            return "retry"
        except RobotsDenied as exc:
            now = int(self.now())
            self._finish(task, now, state="blocked", delay=self.config.revisit_seconds,
                         fields={"last_error": str(exc)})
            return "blocked"
        except requests.RequestException as exc:
            now = int(self.now())
            self._finish(task, now, state="retry", delay=self.config.retry_seconds,
                         fields={"last_error": str(exc)})
            return "retry"

        now = int(self.now())
        if response.status_code == 304:
            if not raw_matches_previous:
                self._finish(task, now, state="retry", delay=self.config.retry_seconds,
                             fields={"last_error": "304 без согласованного HTML и разбора"},
                             unset={"etag": "", "last_modified": ""})
                return "retry"
            self._finish(task, now, state="done", delay=self.config.revisit_seconds,
                         fields={"last_http_status": 304, "last_error": None})
            return "unchanged"
        if response.status_code in {404, 410}:
            self._finish(task, now, state="gone", delay=self.config.revisit_seconds,
                         fields={"last_http_status": response.status_code,
                                 "last_error": f"HTTP {response.status_code}"})
            return "gone"
        response_class = _response_class(response.status_code, response.text[:100_000])
        content_type = response.headers.get("Content-Type", "").lower()
        if response_class != "valid_content_page" or (
            content_type and "html" not in content_type
        ):
            delay = self._retry_delay(response, now)
            if response.status_code in {403, 429, 503} or response_class == "anti_bot_or_challenge":
                self._cooldown_source(source, now + delay)
            self._finish(task, now, state="retry", delay=delay,
                         fields={"last_http_status": response.status_code,
                                 "last_error": response_class if "html" in content_type or not content_type
                                 else f"Не HTML: {content_type}"})
            return "retry"

        raw = response.content
        digest = hashlib.sha256(raw).hexdigest()
        validators = {
            "etag": response.headers.get("ETag"),
            "last_modified": response.headers.get("Last-Modified"),
            "last_http_status": response.status_code, "last_error": None,
        }
        if raw_matches_previous and previous.get("content_sha256") == digest:
            self._finish(task, now, state="done", delay=self.config.revisit_seconds,
                         fields=validators)
            return "unchanged"
        try:
            parsed = PARSERS[source](raw, url).to_dict()
        except Exception as exc:
            parsed = {"parse_error": f"{type(exc).__name__}: {exc}"}
        raw_page = {
            "_id": url, "url": url, "source": source, "html": response.text,
            "fetched_at": now, "content_sha256": digest,
            "final_url": canonicalize_url(response.url), "http_status": response.status_code,
            "content_type": content_type,
        }
        document = {
            "_id": url, "url": url, "source": source, "raw_page_id": url,
            "fetched_at": now, "content_sha256": digest,
            "final_url": raw_page["final_url"], "http_status": response.status_code,
            "content_type": content_type, "parsed": parsed,
        }
        # Save raw HTML before its parsed counterpart. If the process crashes
        # between writes, the hash check above forces a fresh response next time.
        try:
            self.raw_pages.replace_one({"_id": url}, raw_page, upsert=True)
            self.documents.replace_one({"_id": url}, document, upsert=True)
        except DocumentTooLarge:
            self._finish(task, now, state="retry", delay=self.config.revisit_seconds,
                         fields={"last_error": "HTML превышает предел документа MongoDB"})
            return "retry"
        self._finish(task, now, state="done", delay=self.config.revisit_seconds,
                     fields=validators)
        return "changed"

    def run(self) -> dict[str, int]:
        self.prepare()
        added = self.seed()
        print(f"[crawler] Очередь пополнена: {added} URL")
        counters = {"changed": 0, "unchanged": 0, "retry": 0,
                    "blocked": 0, "gone": 0}
        processed = 0
        while self.config.max_documents is None or processed < self.config.max_documents:
            now = int(self.now())
            task = self.claim(now)
            if task is None:
                if self.config.run_forever:
                    next_cooldown = min(
                        (until for until in self.source_cooldowns.values() if until > now),
                        default=now + self.config.poll_seconds,
                    )
                    time.sleep(min(self.config.poll_seconds, max(1, next_cooldown - now)))
                    continue
                break
            result = self.process(task)
            counters[result] += 1
            processed += 1
            print(f"[{processed}] {task['source']:13s} {result:9s} {task['url']}")
        print(f"[crawler] {counters}")
        return counters
