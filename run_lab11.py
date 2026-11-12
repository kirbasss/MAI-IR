"""Verify a Lab 11 compressed sidecar and compare its search results with the raw index."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time
from run_lab7 import file_hash
from run_lab10 import measure

ROOT = Path(__file__).resolve().parent
FILES = ("compact_posting_terms.bin", "compact_postings.bin", "compact_position_terms.bin", "compact_positions.bin")
PACKED_FILES = FILES + ("compact_vocabulary_blocks.bin", "compact_vocabulary.bin")
QUERIES = ("doom", "doom AND eternal", '"doom eternal"', '"eternal doom"',
           '"god of war"', "god AND of AND war", '"даты выхода"',
           "игры AND NOT doom", '"red dead redemption"')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--positions", type=Path, required=True)
    parser.add_argument("--compact", type=Path, required=True)
    parser.add_argument("--packed", type=Path, required=True, help="self-contained compressed index root created by compact_index pack")
    parser.add_argument("--dict", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, default=ROOT/"doc/lab11")
    parser.add_argument("--bin-dir", type=Path, default=ROOT/"build/boolean-native")
    parser.add_argument("--skip-verify-packed", action="store_true",
                        help="reuse a packed index after independently verifying the compressed sidecar")
    args = parser.parse_args()
    target = args.report_dir.resolve(); target.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if sys.platform == "win32" else ""
    compact_binary = (args.bin_dir/("compact_index"+suffix)).resolve()
    search_binary = (args.bin_dir/("boolean_search"+suffix)).resolve()
    base = ["--index",str(args.index.resolve()),"--positions",str(args.positions.resolve())]
    if args.skip_verify_packed:
        check = {"status": "skipped", "wall_ms": None}
        (target/"verify.txt").write_text("Separate verify-packed skipped; compressed sidecar verified before pack.\n",
                                          encoding="utf-8")
    else:
        verify_start = time.perf_counter()
        verified = subprocess.run([str(compact_binary),"verify-packed",*base,"--packed",str(args.packed.resolve())],
                                  stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        (target/"verify.txt").write_bytes(verified.stdout.replace(b"\r\n",b"\n"))
        if verified.returncode:
            raise ValueError(verified.stdout.decode("utf-8",errors="replace"))
        check = {"status": "passed", "wall_ms": (time.perf_counter()-verify_start)*1000,
                 "exit_code": verified.returncode}
    modes = {}
    for space in ("surface","lemma"):
        common = [str(search_binary),*base,"--space",space,"--limit","5"]
        if space == "lemma": common += ["--dict",str(args.dict.resolve())]
        for variant in (("raw","compressed","packed") if args.packed else ("raw","compressed")):
            if variant == "packed":
                cmd = [str(search_binary),"--index",str((args.packed/"index").resolve()),"--positions",str((args.packed/"positions").resolve()),
                       "--compact",str((args.packed/"compact").resolve()),"--space",space,"--limit","5"]
                if space == "lemma": cmd += ["--dict",str(args.dict.resolve())]
            else:
                cmd = common+(["--compact",str(args.compact.resolve())] if variant == "compressed" else [])
            modes[f"{space}_{variant}"] = measure(cmd,list(QUERIES),target,f"{space}_{variant}",repeats=5)
    for space in ("surface","lemma"):
        raw = modes[f"{space}_raw"]["queries"]
        compressed = modes[f"{space}_compressed"]["queries"]
        for variant in (("compressed","packed") if args.packed else ("compressed",)):
            for a,b in zip(raw,modes[f"{space}_{variant}"]["queries"]):
                if a["query"] != b["query"] or a["total"] != b["total"] or a["first_ids"] != b["first_ids"]:
                    raise ValueError(f"Search results differ: {space} {variant} {a['query']}")
    for variant in (("raw","compressed","packed") if args.packed else ("raw","compressed")):
        for space in ("surface","lemma"):
            data = modes[f"{space}_{variant}"]["queries"]
            print(f"{space} {variant}: median query {statistics.median(q['median_ms'] for q in data):.3f} ms",flush=True)
    raw_postings = (args.index/"postings.bin").stat().st_size
    raw_positions = (args.positions/"positions.bin").stat().st_size
    compact_files = {name:{"bytes":(args.compact/name).stat().st_size,"sha256":file_hash(args.compact/name)} for name in FILES}
    total_raw = raw_postings+raw_positions
    total_compact = sum(v["bytes"] for v in compact_files.values())
    raw_files = ("terms.bin","postings.bin","documents.bin","strings.bin")
    position_files = ("position_terms.bin","positions.bin")
    whole_raw = sum((args.index/name).stat().st_size for name in raw_files)+sum((args.positions/name).stat().st_size for name in position_files)
    with (args.index/"terms.bin").open("rb") as term_file, (args.index/"documents.bin").open("rb") as document_file:
        term_file.seek(8); document_file.seek(8)
        term_count = int.from_bytes(term_file.read(8),"little")
        document_count = int.from_bytes(document_file.read(8),"little")
    summary = {"date":datetime.now().isoformat(),"documents":document_count,"terms":term_count,
               "raw_posting_bytes":raw_postings,"raw_coordinate_bytes":raw_positions,
               "raw_total_bytes":total_raw,"whole_raw_index_bytes":whole_raw,"compressed_files":compact_files,
               "compressed_total_bytes":total_compact,"total_ratio":total_raw/total_compact,
               "space_saved_percent":100*(1-total_compact/total_raw),"verification":check,
               "compact_binary_sha256":file_hash(compact_binary),"search_binary_sha256":file_hash(search_binary),
               "dictionary_sha256":file_hash(args.dict),"modes":modes}
    if args.packed:
        packed_files = {"index/"+name:(args.packed/"index"/name).stat().st_size for name in raw_files}
        packed_files.update({"positions/"+name:(args.packed/"positions"/name).stat().st_size for name in position_files})
        packed_files.update({"compact/"+name:(args.packed/"compact"/name).stat().st_size for name in PACKED_FILES})
        packed_total = sum(packed_files.values())
        document_string_bytes = packed_files["index/strings.bin"]-80
        original_vocabulary_bytes = (args.index/"terms.bin").stat().st_size + (args.index/"strings.bin").stat().st_size-80-document_string_bytes
        packed_vocabulary_bytes = packed_files["compact/compact_vocabulary_blocks.bin"]+packed_files["compact/compact_vocabulary.bin"]
        summary.update(packed_files=packed_files,packed_total_bytes=packed_total,
                       whole_index_saved_percent=100*(1-packed_total/whole_raw),
                       original_vocabulary_bytes=original_vocabulary_bytes,packed_vocabulary_bytes=packed_vocabulary_bytes,
                       vocabulary_saved_percent=100*(1-packed_vocabulary_bytes/original_vocabulary_bytes))
    (target/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    fmt = lambda n:f"{n:,}".replace(",","\\,")
    tex = "".join(f"\\newcommand{{\\{name}}}{{{value}}}\n" for name,value in {
        "LabElevenRawMB":f"{total_raw/1e6:.2f}".replace(".",","),
        "LabElevenCompactMB":f"{total_compact/1e6:.2f}".replace(".",","),
        "LabElevenSavings":f"{summary['space_saved_percent']:.1f}".replace(".",","),
        "LabElevenDocs":fmt(summary["documents"]),"LabElevenTerms":fmt(summary["terms"]),
        "LabElevenVerifySec":f"{check['wall_ms']/1000:.1f}".replace(".",",") if check["wall_ms"] is not None else "---",
        "LabElevenWholeRawMB":f"{whole_raw/1e6:.2f}".replace(".",","),
        "LabElevenPackedMB":f"{summary.get('packed_total_bytes',0)/1e6:.2f}".replace(".",","),
        "LabElevenWholeSavings":f"{summary.get('whole_index_saved_percent',0):.1f}".replace(".",","),
        "LabElevenVocabRawMB":f"{summary.get('original_vocabulary_bytes',0)/1e6:.2f}".replace(".",","),
        "LabElevenVocabCompactMB":f"{summary.get('packed_vocabulary_bytes',0)/1e6:.2f}".replace(".",","),
        "LabElevenVocabSavings":f"{summary.get('vocabulary_saved_percent',0):.1f}".replace(".",","),
    }.items())
    (target/"report_data.tex").write_text(tex,encoding="utf-8")
    lines = ["# ЛР11: измерения сжатия", "", f"Документов: {summary['documents']:,}; терминов: {summary['terms']:,}.",
             f"Исходные списки и координаты: {total_raw:,} байт; сжатые данные с таблицами смещений: {total_compact:,} байт.",
             f"Экономия: {summary['space_saved_percent']:.1f}%. " +
             (f"Полная проверка декодирования: {check['wall_ms']/1000:.1f} с."
              if check["wall_ms"] is not None else "Отдельный verify-packed пропущен; sidecar проверен до упаковки."),
             f"Полный индекс: {whole_raw:,} байт; отдельная сжатая рабочая копия: {summary.get('packed_total_bytes',0):,} байт; экономия {summary.get('whole_index_saved_percent',0):.1f}%.",
             f"Словарь терминов (таблица и строки): {summary.get('original_vocabulary_bytes',0):,} → {summary.get('packed_vocabulary_bytes',0):,} байт; экономия {summary.get('vocabulary_saved_percent',0):.1f}%.",
             "", "| Пространство | Запрос | Совпадений | Исходный, мс | Сжатый, мс | Рабочая копия, мс |", "|---|---|---:|---:|---:|---:|"]
    for space in ("surface","lemma"):
        for raw,compact in zip(modes[f"{space}_raw"]["queries"],modes[f"{space}_compressed"]["queries"]):
            packed = modes[f"{space}_packed"]["queries"][list(QUERIES).index(raw["query"])] if args.packed else compact
            lines.append(f"| {space} | `{raw['query']}` | {raw['total']} | {raw['median_ms']:.2f} | {compact['median_ms']:.2f} | {packed['median_ms']:.2f} |")
    lines += ["", "Медианы пяти повторений в одном процессе. Время исключает открытие индекса и загрузку словаря.",
              "Для каждого запроса проверено совпадение полного числа результатов и показанных ID.",
              "Полная проверка каждого posting ID и каждой координаты выполнена отдельно командой verify."]
    (target/"README.md").write_text("\n".join(lines)+"\n",encoding="utf-8")


if __name__ == "__main__":
    main()
