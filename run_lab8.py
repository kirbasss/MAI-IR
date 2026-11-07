"""Save Lab 8 tests and native search measurements on an existing Lab 7 index."""
from __future__ import annotations
import argparse
from datetime import datetime
import json
from pathlib import Path
import statistics
import subprocess
import sys
import threading
import time
from run_lab7 import file_hash, peak_sampler

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--dict", type=Path, required=True)
    parser.add_argument("--binary", type=Path, default=ROOT/"build/boolean-native/boolean_search.exe")
    parser.add_argument("--queries", type=Path, default=ROOT/"tests/fixtures/lab8_queries.txt")
    parser.add_argument("--output", type=Path, required=True, help="new output directory")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output/"INCOMPLETE").write_text("Lab 8 run has not completed\n", encoding="utf-8")
    with (args.output/"tests.txt").open("wb") as log:
        checked = subprocess.run([sys.executable, "run_tests.py", "--lab", "8"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    if checked.returncode:
        parser.exit(1, "Tests failed; see tests.txt\n")
    queries = args.queries.read_text(encoding="utf-8").splitlines()
    if not queries or any(not q.strip() for q in queries):
        parser.error("Query file must have nonempty lines")
    modes = {}
    for space in ("surface", "lemma"):
        command = [str(args.binary.resolve()), "--index", str(args.index.resolve()), "--space", space, "--limit", "5"]
        if space == "lemma":
            command += ["--dict", str(args.dict.resolve())]
        start = time.perf_counter()
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stop, peak = threading.Event(), [0]
        thread = threading.Thread(target=peak_sampler, args=(process.pid, stop, peak), daemon=True)
        thread.start()
        try:
            output, errors = process.communicate(("\n".join(queries*args.repeats)+"\n").encode("utf-8"), timeout=300)
        except BaseException:
            process.kill(); process.wait()
            raise
        finally:
            stop.set(); thread.join(timeout=2)
        if process.returncode:
            raise RuntimeError(errors.decode("utf-8", errors="replace")+output.decode("utf-8", errors="replace"))
        wall_ms = (time.perf_counter()-start)*1000
        rows = [json.loads(line) for line in output.decode("utf-8").splitlines()]
        if len(rows) != len(queries)*args.repeats:
            raise ValueError("Unexpected response count")
        (args.output/(space+".jsonl")).write_bytes(output.replace(b"\r\n", b"\n"))
        summary = []
        for i, query in enumerate(queries):
            samples = rows[i::len(queries)]
            if any(row["query"] != query or row["total"] != samples[0]["total"] or row["results"] != samples[0]["results"] for row in samples):
                raise ValueError("Unstable response across repetitions")
            summary.append({"query":query, "total":samples[0]["total"], "first_ids":[r["id"] for r in samples[0]["results"]],
                            "median_ms":statistics.median(row["elapsed_ms"] for row in samples),
                            "samples_ms":[row["elapsed_ms"] for row in samples]})
        modes[space] = {"queries":summary,"wall_ms":wall_ms,"peak_working_set_bytes":peak[0] or None}
        print(f"{space}: {len(rows)} queries, {wall_ms/1000:.2f}s, peak {peak[0]/2**20:.2f} MiB", flush=True)
    # These Boolean identities require no relevance judgements.
    for space, measurement in modes.items():
        q = {row["query"]:row["total"] for row in measurement["queries"]}
        if all(k in q for k in ("doom", "doom AND quake", "doom OR quake")):
            if not q["doom AND quake"] <= q["doom"] <= q["doom OR quake"]:
                raise ValueError("Set inclusion violated")
        if "doom AND zzzmissingterm12345" in q and q["doom AND zzzmissingterm12345"]:
            raise ValueError("Unknown-term intersection must be empty")
    summary = {"date":datetime.now().isoformat(),"repeats":args.repeats,
               "binary_sha256":file_hash(args.binary),"dictionary_sha256":file_hash(args.dict),
               "index":str(args.index),"modes":modes}
    # Read only format headers, never load the index payload into Python memory.
    summary["index_headers"] = {}
    for name in ("terms.bin","postings.bin","documents.bin","strings.bin"):
        with (args.index/name).open("rb") as file:
            summary["index_headers"][name] = file.read(80).hex()
    (args.output/"summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    def number(n):
        return str(n).replace(".", ",")
    table = []
    for a, b in zip(modes["surface"]["queries"], modes["lemma"]["queries"]):
        table.append(a["query"].replace("_",r"\_")+" & "+str(a["total"])+" & "+str(b["total"])+" & "+
                     number(round(a["median_ms"],2))+" & "+number(round(b["median_ms"],2))+r" \\")
    macros = "\\newcommand{\\SearchRows}{%\n"+"\n".join(table)+"\n}\n"
    for space, label in (("surface","Surface"),("lemma","Lemma")):
        peak = modes[space]["peak_working_set_bytes"]
        macros += "\\newcommand{\\Search"+label+"MiB}{"+(number(round(peak/2**20,2)) if peak else "---")+"}\n"
    macros += "\\newcommand{\\SearchRepeats}{"+str(args.repeats)+"}\n"
    (args.output/"report_data.tex").write_text(macros, encoding="utf-8")
    lines = ["# ЛР8: булев поиск", "", "| Запрос | Словоформы | Леммы | Словоформы, мс | Леммы, мс |", "|---|---:|---:|---:|---:|"]
    for a,b in zip(modes["surface"]["queries"],modes["lemma"]["queries"]):
        lines.append(f"| `{a['query']}` | {a['total']} | {b['total']} | {a['median_ms']:.2f} | {b['median_ms']:.2f} |")
    lines += ["",f"Медианы {args.repeats} повторений в одном процессе каждого режима. Время C++ включает нормализацию, разбор и полный подсчёт результатов; не включает загрузку словаря и вывод метаданных.",
              "Выдача упорядочена по ID, без ранжирования. Число найденных документов не является оценкой релевантности.",
              "Первые ID и заголовки сохранены в surface.jsonl и lemma.jsonl. Журнал автотестов: tests.txt."]
    (args.output/"README.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    (args.output/"INCOMPLETE").unlink()


if __name__ == "__main__":
    main()
