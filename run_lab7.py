"""Build and verify the native Boolean index; Python handles metadata and provenance only."""
from __future__ import annotations
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import threading
import time
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent
INDEX_FILES = ("terms.bin", "postings.bin", "documents.bin", "strings.bin")
CATALOG_MAGIC = b"MAIIU7\0\1"


def file_hash(path: Path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def write_catalog(snapshot: Path, target: Path):
    """Preserve snapshot IDs, never generate new IDs or build term structures."""
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    count, previous = 0, 0
    with (snapshot / "documents.jsonl").open(encoding="utf-8") as source, target.open("xb") as output:
        output.write(CATALOG_MAGIC)
        for line in source:
            row = json.loads(line)
            identifier, url = row.get("document_id"), row.get("url")
            if type(identifier) is not int or not previous < identifier < 2**64:
                raise ValueError("Metadata IDs must be strictly increasing positive uint64 values")
            if not isinstance(url, str) or not url or "\0" in url:
                raise ValueError("Metadata URL must be a nonempty UTF-8 string without NUL")
            encoded = url.encode("utf-8")
            if len(encoded) > 1024*1024:
                raise ValueError("URL is larger than 1 MiB")
            output.write(struct.pack("<QI", identifier, len(encoded)))
            output.write(encoded)
            previous = identifier
            count += 1
        if count != manifest["documents"]:
            raise ValueError("Metadata count differs from corpus manifest")
    return count


def peak_sampler(pid, stop, result):
    if os.name == "nt":
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    "PeakWorkingSetSize", "WorkingSetSize", "QuotaPeakPagedPoolUsage", "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage", "QuotaNonPagedPoolUsage", "PagefileUsage", "PeakPagefileUsage")]
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        psapi.GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
        handle = kernel.OpenProcess(0x400 | 0x10, False, pid)
        if not handle:
            return
        try:
            while not stop.is_set():
                counters = Counters(); counters.cb = ctypes.sizeof(counters)
                if psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                    result[0] = max(result[0], counters.PeakWorkingSetSize)
                stop.wait(.2)
        finally:
            kernel.CloseHandle(handle)
    else:
        while not stop.is_set():
            try:
                for line in Path(f"/proc/{pid}/status").read_text().splitlines():
                    if line.startswith("VmHWM:"):
                        result[0] = max(result[0], int(line.split()[1])*1024)
            except OSError:
                return
            stop.wait(.2)


def execute(command: list[str], log: Path):
    start = time.perf_counter()
    process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    stop, peak = threading.Event(), [0]
    thread = threading.Thread(target=peak_sampler, args=(process.pid, stop, peak), daemon=True)
    thread.start()
    try:
        with log.open("xb") as output:
            for line in process.stdout:
                output.write(line); output.flush()
                print(line.decode("utf-8", errors="replace"), end="", flush=True)
        code = process.wait()
    finally:
        stop.set(); thread.join(timeout=2)
        if process.poll() is None:
            process.terminate(); process.wait()
    if code:
        raise ValueError(f"Native command failed with code {code}; see {log}")
    return {"wall_ms": (time.perf_counter()-start)*1000,
            "peak_working_set_bytes": peak[0] or None}


def write_report(target: Path, summary: dict):
    target.mkdir(parents=True, exist_ok=True)
    (target / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    s = summary["statistics"]
    peak = summary["build_measurement"]["peak_working_set_bytes"]
    def number(value, digits=0):
        return f"{value:,.{digits}f}".replace(",", r"\,").replace(".", ",")
    verify_peak = summary["verification_measurement"]["peak_working_set_bytes"]
    macros = {"IndexDate":datetime.fromisoformat(summary["created_at"]).strftime("%d.%m.%Y"),
              "IndexDocuments":number(s["documents"]),"IndexTokens":number(s["tokens"]),
              "IndexSurfaceTerms":number(s["surface_terms"]),"IndexLemmaTerms":number(s["lemma_terms"]),
              "IndexSurfacePostings":number(s["surface_postings"]),"IndexLemmaPostings":number(s["lemma_postings"]),
              "IndexBuffer":number(s["buffer_bytes"]/2**20),"IndexDictionaryMB":number(s["dictionary_memory_bytes"]/1e6,2),
              "IndexPeakMiB":number(peak/2**20,2) if peak else "---",
              "IndexSeconds":number(s["elapsed_ms"]/1000,2),
              "IndexVerifySeconds":number(summary["verification_measurement"]["wall_ms"]/1000,2),
              "IndexVerifyMiB":number(verify_peak/2**20,2) if verify_peak else "---",
              "IndexMB":number(sum(summary["files"][n]["bytes"] for n in INDEX_FILES)/1e6,2),
              "IndexRuns":number(s["initial_runs"]),"IndexMergePasses":number(s["merge_passes"])}
    size_rows = [name.replace("_",r"\_")+" & "+number(summary["files"][name]["bytes"]/1e6,2)+r" \\" for name in INDEX_FILES]
    example_rows=[]
    for text in summary.get("examples",[]):
        fields={line.split("\t",1)[0]:line.split("\t",1)[1] for line in text.splitlines() if "\t" in line}
        if "document_ids" not in fields:
            fields["document_ids"]=""
        label="Словоформы" if fields["space"]=="surface" else "Леммы"
        identifiers=[v for v in fields["document_ids"].split("\t")[:5] if v]
        ids=", ".join(identifiers)
        if int(fields["document_frequency"])>len(identifiers):
            ids+=(", " if ids else "")+r"\ldots"
        ids="["+ids+"]"
        example_rows.append(fields["term"]+" & "+label+" & "+number(int(fields["document_frequency"]))+" & "+ids+r" \\")
    (target/"report_data.tex").write_text("".join("\\newcommand{\\"+name+"}{"+value+"}\n" for name,value in macros.items())+
                                        "\\newcommand{\\IndexSizeRows}{%\n"+"\n".join(size_rows)+"\n}\n"+
                                        "\\newcommand{\\IndexExampleRows}{%\n"+"\n".join(example_rows)+"\n}\n",encoding="utf-8")
    lines = ["# ЛР7: булев индекс", "", "| Показатель | Значение |", "|---|---:|",
             f"| Документы | {s['documents']:,} |", f"| Токены исходного корпуса | {s['tokens']:,} |",
             f"| Термины словоформ | {s['surface_terms']:,} |", f"| Термины лемм | {s['lemma_terms']:,} |",
             f"| Пары словоформа–документ | {s['surface_postings']:,} |", f"| Пары лемма–документ | {s['lemma_postings']:,} |",
             f"| Буфер сортировки, МиБ | {s['buffer_bytes']/2**20:.0f} |",
             f"| Словарь лемматизатора, МБ | {s['dictionary_memory_bytes']/1e6:.2f} |",
             f"| Пиковый рабочий набор процесса, МиБ | {peak/2**20:.2f} |" if peak else "| Пиковый рабочий набор | Не измерен |",
             f"| Время построения C++, с | {s['elapsed_ms']/1000:.2f} |",
             f"| Начальные части / проходы слияния | {s['initial_runs']} / {s['merge_passes']} |",
             f"| Основные файлы индекса, МБ | {sum(summary['files'][n]['bytes'] for n in INDEX_FILES)/1e6:.2f} |", "",
             "Индекс содержит отдельные пространства словоформ и лемм. Повторные вхождения не дублируют ID документа.",
             "При морфологической неоднозначности документ добавляется в список каждой возможной леммы; это расширение кандидатов, а не выбор значения по контексту.",
             "Построение, сортировка, слияние, словарь и чтение списков реализованы на C++ без STL-контейнеров.",
             "Словарь фиксированных записей ищется бинарным поиском на диске. Списки читаются независимыми потоковыми курсорами.",
             "Заголовки, источники и URL сохраняются внутри индекса: для их чтения исходный корпус и MongoDB не нужны.",
             "Полная проверка C++ сверяет контрольные суммы, порядок терминов, уникальность ID и их принадлежность корпусу.",
             "Частоты df в индексе означают число документов. Они отличаются от числа вхождений, подсчитанного в ЛР5.",
             "Это построение индекса и инспекция отдельных списков. Разбор булевых выражений и поисковый интерфейс относятся к ЛР8.", ""]
    (target / "README.md").write_text("\n\n".join(lines[:2])+"\n"+"\n".join(lines[2:]).rstrip()+"\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new directory")
    parser.add_argument("--binary", type=Path, default=ROOT/"build/boolean-native/boolean_index.exe")
    parser.add_argument("--dict", dest="dictionary", type=Path)
    parser.add_argument("--memory-mib", type=int, default=64)
    parser.add_argument("--reserve-mib", type=int, default=4608)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--check-zipf", type=Path, help="optional Zipf summary from the same snapshot for N/V comparison")
    args = parser.parse_args()
    if not 1 <= args.memory_mib <= 2048 or not 1 <= args.reserve_mib <= 1048576:
        parser.error("Invalid counting-buffer size or disk reserve")
    try:
        binary = args.binary.resolve(strict=True); snapshot = args.snapshot.resolve(strict=True)
        dictionary = args.dictionary.resolve(strict=True) if args.dictionary else None
        manifest = json.loads((snapshot/"manifest.json").read_text(encoding="utf-8"))
        if manifest.get("format") != "MAIIR3-v1" or file_hash(snapshot/"corpus.bin") != manifest["corpus_sha256"]:
            raise ValueError("Corpus format/SHA-256 mismatch")
        free = shutil.disk_usage(args.output.resolve().parent if args.output.parent.exists() else ROOT).free
        if free < args.reserve_mib*2**20 + 2*manifest["input_utf8_bytes"]:
            raise ValueError("Insufficient disk headroom for the configured reserve and temporary parts")
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output/"INCOMPLETE").write_text("Index experiment has not finished.\n", encoding="utf-8")
        catalog = args.output/"urls.bin"
        write_catalog(snapshot, catalog)
        index = args.output.resolve()/"index"
        command = [str(binary),"build","--corpus",str(snapshot/"corpus.bin"),"--urls",str(catalog.resolve()),
                   "--output",str(index),"--memory-mib",str(args.memory_mib),"--reserve-mib",str(args.reserve_mib)]
        if dictionary:
            command += ["--dict",str(dictionary)]
        print(f"Building index for {manifest['documents']:,} documents", flush=True)
        measurement = execute(command, args.output/"build.log")
        verification = execute([str(binary),"verify","--index",str(index)],args.output/"verify.log")
        stats = json.loads((index/"statistics.json").read_text(encoding="utf-8"))
        if (stats["documents"],stats["input_utf8_bytes"]) != (manifest["documents"],manifest["input_utf8_bytes"]):
            raise ValueError("Index statistics differ from corpus manifest")
        if args.check_zipf:
            z = json.loads(args.check_zipf.read_text(encoding="utf-8"))
            if z["snapshot"]["corpus_sha256"] != manifest["corpus_sha256"]:
                raise ValueError("Zipf reference is from another snapshot")
            all_terms = next(g for g in z["statistics"]["groups"] if g["name"] == "all")
            if (stats["tokens"],stats["surface_terms"]) != (all_terms["tokens"],all_terms["vocabulary"]):
                raise ValueError("Surface token/vocabulary counts differ from Zipf reference")
        summary = {"statistics":stats,"snapshot":manifest,"build_measurement":measurement,"verification_measurement":verification,
                   "build_options":{"memory_mib":args.memory_mib,"reserve_mib":args.reserve_mib,"lemma_policy":"all_dictionary_alternatives" if dictionary else "surface_only"},
                   "binary_sha256":file_hash(binary),"dictionary_sha256":file_hash(dictionary) if dictionary else None,
                   "catalog_sha256":file_hash(catalog),"metadata_sha256":file_hash(snapshot/"documents.jsonl"),
                   "created_at":datetime.now(timezone(timedelta(hours=3))).isoformat(),
                   "files":{name:{"bytes":(index/name).stat().st_size,"sha256":file_hash(index/name)} for name in INDEX_FILES}}
        summary["examples"]=[]
        samples=[("doom","surface"),("minecraft","surface"),("skyrim","surface")]
        for term,space in samples:
            sample=subprocess.run([str(binary),"inspect","--index",str(index),"--term",term,"--space",space,"--limit","5"],
                                  stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
            summary["examples"].append(sample.stdout.decode("utf-8").replace("\r\n", "\n"))
        write_report(args.output,summary)
        if args.report_dir:
            write_report(args.report_dir,summary)
            for name in ("build.log","verify.log"):
                shutil.copy2(args.output/name,args.report_dir/name)
        (args.output/"INCOMPLETE").unlink()
        print(f"Complete index: {index}")
        print(f"Peak working set: {measurement['peak_working_set_bytes']} bytes")
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        parser.exit(1,f"[lab7] {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
