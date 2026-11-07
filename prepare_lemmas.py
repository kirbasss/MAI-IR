"""Prepare DATA dictionaries; runtime morphology is implemented independently in C++."""
from __future__ import annotations

import argparse
import bz2
import hashlib
import importlib.metadata
import itertools
import json
import shutil
import sqlite3
import struct
import tarfile
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

MAGIC = b"MAIIL4\0\1"
SOURCES = {
    "opencorpora": "https://opencorpora.org/files/export/dict/dict.opcorpora.xml.bz2",
    "wordnet": "https://wordnetcode.princeton.edu/3.0/WordNet-3.0.tar.gz",
}
PYMORPHY_DATA_FILES = ("meta.json", "paradigms.array", "suffixes.json", "words.dawg")


def pymorphy_source() -> tuple[Path, dict]:
    """Locate installed DATA and fingerprint exactly the files used for conversion."""
    try:
        import pymorphy3_dicts_ru
        import dawg_python  # noqa: F401 -- format reader, not a morphological analyzer
        package_version = importlib.metadata.version("pymorphy3-dicts-ru")
        reader_version = importlib.metadata.version("DAWG2-Python")
    except (ImportError, importlib.metadata.PackageNotFoundError) as exc:
        raise ValueError("Install preparation dependencies: python -m pip install -r requirements-lab4.txt") from exc
    root = Path(pymorphy3_dicts_ru.get_path())
    meta = dict(json.loads((root / "meta.json").read_text(encoding="utf-8")))
    files = {}
    names = list(PYMORPHY_DATA_FILES)
    if meta.get("compile_options", {}).get("paradigm_prefixes") is None:
        names.append("paradigm-prefixes.json")
    for name in names:
        with (root / name).open("rb") as stream:
            files[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    return root, {
        "name": "opencorpora", "distribution": "pymorphy3-dicts-ru",
        "distribution_version": package_version,
        "url": "https://pypi.org/project/pymorphy3-dicts-ru/" + package_version + "/",
        "source_version": meta.get("source_version"), "source_revision": meta.get("source_revision"),
        "compiled_at": meta.get("compiled_at"), "files_sha256": files,
        "sha256": hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest(),
        "decoder": {"distribution": "DAWG2-Python", "version": reader_version},
    }


def pymorphy_pairs(root: Path, *, records=None, progress: bool = False):
    """Decode known word records and their stored paradigms. No MorphAnalyzer/prediction."""
    meta = dict(json.loads((root / "meta.json").read_text(encoding="utf-8")))
    if meta.get("format_version") != "2.4" or meta.get("language_code") != "ru":
        raise ValueError("Expected Russian pymorphy dictionary format 2.4")
    suffixes = json.loads((root / "suffixes.json").read_text(encoding="utf-8"))
    prefixes = meta.get("compile_options", {}).get("paradigm_prefixes")
    if prefixes is None:
        prefixes = json.loads((root / "paradigm-prefixes.json").read_text(encoding="utf-8"))
    if (not isinstance(suffixes, list) or not isinstance(prefixes, list)
            or not all(isinstance(s, str) for s in suffixes + prefixes)):
        raise ValueError("Invalid paradigm affixes")
    # Explicit little-endian decoding, independent of the machine's byte order.
    data = (root / "paradigms.array").read_bytes()
    offset = 0
    def read_short() -> int:
        nonlocal offset
        if offset + 2 > len(data):
            raise ValueError("Truncated paradigms file")
        value = struct.unpack_from("<H", data, offset)[0]
        offset += 2
        return value
    paradigms = []
    for _ in range(read_short()):
        size = read_short()
        if not size or size % 3 or offset + size * 2 > len(data):
            raise ValueError("Invalid paradigm length")
        values = struct.unpack_from(f"<{size}H", data, offset)
        offset += size * 2
        width = size // 3
        if (any(i >= len(suffixes) for i in values[:width])
                or any(i >= len(prefixes) for i in values[width * 2:])):
            raise ValueError("Invalid paradigm affix reference")
        paradigms.append(values)
    if offset != len(data) or not paradigms:
        raise ValueError("Empty or extended paradigms file")
    if records is None:
        try:
            from dawg_python import RecordDAWG
        except ImportError as exc:
            raise ValueError("Install DAWG2-Python==0.9.0 (offline data decoder)") from exc
        records = RecordDAWG(">HH").load(str(root / "words.dawg")).iteritems()
    processed = yielded = 0
    for word, (paradigm_id, form_id) in records:
        processed += 1
        if progress and processed % 250000 == 0:
            print(f"[pymorphy-data] records={processed:,}; accepted={yielded:,}", flush=True)
        if not russian(word):
            continue  # protect names/compounds consistently with the C++ runtime
        if not 0 <= paradigm_id < len(paradigms):
            raise ValueError("Invalid dictionary paradigm ID")
        values = paradigms[paradigm_id]
        width = len(values) // 3
        if not 0 <= form_id < width:
            raise ValueError("Invalid dictionary form ID")
        prefix = prefixes[values[width * 2 + form_id]]
        suffix = suffixes[values[form_id]]
        if (not word.startswith(prefix) or not word.endswith(suffix)
                or len(prefix) + len(suffix) > len(word)):
            raise ValueError("Word does not match its stored paradigm")
        stop = len(word) - len(suffix) if suffix else len(word)
        stem = word[len(prefix):stop]
        lemma = prefixes[values[width * 2]] + stem + suffixes[values[0]]
        if russian(lemma):
            yielded += 1
            yield "ru:" + word, lemma
    if not yielded:
        raise ValueError("No Russian word records in the dictionary")
    if progress:
        print(f"[pymorphy-data] complete: records={processed:,}; accepted={yielded:,}", flush=True)


def fnv(data: bytes, value: int = 14695981039346656037) -> int:
    for byte in data:
        value = ((value ^ byte) * 1099511628211) & 0xffffffffffffffff
    return value


def russian(word: str) -> bool:
    return bool(word) and all("а" <= c <= "я" or c == "ё" for c in word)


def english(word: str) -> bool:
    return bool(word) and all("a" <= c <= "z" for c in word)


def opencorpora_pairs(path: Path):
    """Stream XML lemmas. Do not collapse derivational links or verb aspect."""
    opener = bz2.open if path.suffix == ".bz2" else open
    with opener(path, "rb") as stream:
        context = ET.iterparse(stream, events=("start", "end"))
        parents = []
        for event, elem in context:
            if event == "start":
                parents.append(elem)
                continue
            if elem.tag == "lemma":
                lemma_node = elem.find("l")
                if lemma_node is None or not lemma_node.get("t"):
                    raise ValueError("OpenCorpora lemma without l/@t")
                lemma = lemma_node.attrib["t"].lower()
                if russian(lemma):
                    yield "ru:" + lemma, lemma
                    for form in elem.findall("f"):
                        word = form.attrib["t"].lower()
                        if russian(word):
                            yield "ru:" + word, lemma
                if len(parents) > 1:
                    parents[-2].remove(elem)
                elem.clear()
            elif elem.tag in {"grammeme", "link", "type", "restriction"}:
                if len(parents) > 1:
                    parents[-2].remove(elem)
                elem.clear()
            parents.pop()


def wordnet_pairs(path: Path):
    names = {"noun": "n", "verb": "v", "adj": "a", "adv": "r"}
    with tarfile.open(path, "r:gz") as archive:
        members = {Path(m.name).name: m for m in archive.getmembers() if m.isfile()}
        for name, pos in names.items():
            bases = set()
            for filename in ("index." + name, name + ".exc"):
                if filename not in members:
                    raise ValueError(f"WordNet missing {filename}")
                with archive.extractfile(members[filename]) as stream:
                    for raw in stream:
                        if not raw.strip() or raw.startswith(b" "):
                            continue
                        words = raw.decode("ascii").split()
                        word = words[0].lower()
                        if not english(word):
                            continue
                        if filename.startswith("index."):
                            bases.add(word)
                            yield pos + ":" + word, word
                        else:
                            for lemma in words[1:]:
                                if english(lemma) and lemma in bases:
                                    yield "x:" + pos + ":" + word, lemma


def build_dictionary(pairs, output: Path, sources: list[dict] | None = None) -> dict:
    """SQLite is an OFFLINE conversion aid, never used by the C++ lookup."""
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise FileExistsError(output)
    staging = Path(tempfile.mkdtemp(prefix=".lemmas-", dir=output.parent))
    try:
        db = sqlite3.connect(staging / "pairs.sqlite")
        try:
            db.execute("PRAGMA journal_mode=OFF")
            db.execute("PRAGMA synchronous=OFF")
            db.execute("PRAGMA cache_size=-32768")
            db.execute("CREATE TABLE pairs (key TEXT, lemma TEXT, PRIMARY KEY(key,lemma)) WITHOUT ROWID")
            def checked():
                for key, lemma in pairs:
                    if (not key or not lemma or any(ord(c) < 32 or ord(c) == 127 for c in key + lemma)
                            or len(key.encode()) > 4096 or len(lemma.encode()) > 4096):
                        raise ValueError("Invalid dictionary pair")
                    yield key, lemma
            db.executemany("INSERT OR IGNORE INTO pairs VALUES (?,?)", checked())
            db.commit()
            rows = db.execute("SELECT key,lemma FROM pairs ORDER BY key,lemma")
            record_bytes = bytearray()
            string_bytes = bytearray()
            lemma_pool: dict[bytes, int] = {}
            count = total_analyses = max_ambiguity = 0
            for key, group in itertools.groupby(rows, key=lambda row: row[0]):
                values = [row[1] for row in group]
                if len(values) > 64:
                    raise ValueError(f"Excessive dictionary ambiguity: {key}")
                encoded = key.encode("utf-8")
                key_offset = len(string_bytes)
                string_bytes.extend(encoded)
                lemmas = b"".join(value.encode("utf-8") + b"\0" for value in values)
                if lemmas not in lemma_pool:
                    lemma_pool[lemmas] = len(string_bytes)
                    string_bytes.extend(lemmas)
                record_bytes.extend(struct.pack("<IIII", key_offset, len(encoded), lemma_pool[lemmas], len(lemmas)))
                count += 1
                total_analyses += len(values)
                max_ambiguity = max(max_ambiguity, len(values))
            if not count:
                raise ValueError("Empty dictionary")
            checksum = fnv(string_bytes, fnv(record_bytes))
            destination = staging / "dictionary.bin"
            with destination.open("xb") as stream:
                stream.write(MAGIC + struct.pack("<QQQ", count, len(string_bytes), checksum))
                stream.write(record_bytes)
                stream.write(string_bytes)
        finally:
            db.close()
        with destination.open("rb") as stream:
            binary_digest = hashlib.file_digest(stream, "sha256").hexdigest()
        manifest = {
            "format": "MAIIL4-v1", "created_at": datetime.now(UTC).isoformat(),
            "normalization": "gaming-v3: lowercase; preserve yo; alphabetic single-word entries only",
            "entries": count, "analyses": total_analyses, "max_ambiguity": max_ambiguity,
            "bytes": destination.stat().st_size,
            "sha256": binary_digest,
            "sources": sources or [],
        }
        # Move only complete files, never overwrite a published directory.
        published = staging / "published"
        published.mkdir()
        destination.rename(published / "dictionary.bin")
        (published / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if sources:
            notices = []
            if any(source["name"] == "opencorpora" for source in sources):
                notices.append("OpenCorpora dictionary: https://opencorpora.org/?page=downloads ; CC BY-SA 3.0.\n"
                               "Binary dictionary is a converted derivative; retain attribution and share-alike for its OpenCorpora data.\n")
                for source in sources:
                    if source.get("distribution") == "pymorphy3-dicts-ru":
                        notices.append(f"OpenCorpora data redistributed by pymorphy3-dicts-ru {source['distribution_version']}; "
                                       f"revision {source.get('source_revision')}; {source['url']}\n"
                                       "Converted known dictionary entries only. No prediction models or MorphAnalyzer used.\n")
            for source in sources:
                if source["name"] == "wordnet":
                    with tarfile.open(source["path"], "r:gz") as archive:
                        license_member = next(m for m in archive.getmembers() if Path(m.name).name == "LICENSE")
                        notices.append(archive.extractfile(license_member).read().decode("utf-8"))
            (published / "NOTICE.txt").write_text("\n".join(notices), encoding="utf-8")
        published.rename(output)
        return manifest
    finally:
        shutil.rmtree(staging)


def download(name: str, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / SOURCES[name].rsplit("/", 1)[1]
    if target.exists():
        print(f"Using existing archive: {target}", flush=True)
        return target
    # Exclusive creation: never clobber an earlier partial download.
    partial = target.with_name(target.name + ".part")
    print(f"Downloading {SOURCES[name]}", flush=True)
    try:
        with partial.open("xb") as out:
            try:
                with urllib.request.urlopen(SOURCES[name], timeout=45) as response:
                    shutil.copyfileobj(response, out)
            except BaseException:
                out.close()
                partial.unlink()
                raise
        partial.rename(target)
    except FileExistsError:
        raise FileExistsError(f"Archive or partial file already exists: {target}")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true", help="fetch official source archives (~28 MB total)")
    parser.add_argument("--archives", type=Path, default=Path("data/lemma_sources"))
    russian_input = parser.add_mutually_exclusive_group()
    russian_input.add_argument("--opencorpora", type=Path, help="original OpenCorpora XML/.bz2")
    russian_input.add_argument("--pymorphy", action="store_true", help="use installed pymorphy3-dicts-ru data instead of XML")
    parser.add_argument("--wordnet", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/lemmas/ru_en_v1"))
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise FileExistsError(args.output)
        packaged_root = None
        packaged_source = None
        if args.pymorphy:
            packaged_root, packaged_source = pymorphy_source()
        ru = args.opencorpora or (download("opencorpora", args.archives) if args.download and not args.pymorphy else None)
        en = args.wordnet or (download("wordnet", args.archives) if args.download else None)
        if not ru and not packaged_root and not en:
            raise ValueError("Supply source archives or use --download")
        if not (ru or packaged_root) or not en:
            print("NOTE: building a single-language dictionary; absent languages pass through unchanged.", flush=True)
        sources = [packaged_source] if packaged_source else []
        for name, path in (("opencorpora", ru), ("wordnet", en)):
            if path is None:
                continue
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            sources.append({"name": name, "path": str(path.resolve()), "url": SOURCES[name], "sha256": digest})
        print("Converting dictionary data (streamed records, disk-backed deduplication)...", flush=True)
        russian_pairs = pymorphy_pairs(packaged_root, progress=True) if packaged_root else (opencorpora_pairs(ru) if ru else ())
        result = build_dictionary(itertools.chain(russian_pairs,
                                                 wordnet_pairs(en) if en else ()), args.output, sources)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, ET.ParseError, sqlite3.Error, tarfile.TarError) as exc:
        parser.exit(1, f"[prepare-lemmas] {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
