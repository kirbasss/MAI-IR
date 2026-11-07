"""Build positional files in C++, verify them, and save phrase-search measurements."""
from __future__ import annotations
import argparse
from datetime import datetime
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import threading
import time
from run_lab7 import execute, file_hash, peak_sampler

ROOT = Path(__file__).resolve().parent


def measure(command, queries, target, space, repeats=3):
    start = time.perf_counter()
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stop, peak = threading.Event(), [0]
    sampler = threading.Thread(target=peak_sampler, args=(process.pid, stop, peak), daemon=True)
    sampler.start()
    try:
        output, errors = process.communicate(("\n".join(queries*repeats)+"\n").encode("utf-8"), timeout=300)
    except BaseException:
        process.kill(); process.wait()
        raise
    finally:
        stop.set(); sampler.join(timeout=2)
    if process.returncode:
        raise ValueError(errors.decode("utf-8", errors="replace")+output.decode("utf-8", errors="replace"))
    (target/(space+".jsonl")).write_bytes(output.replace(b"\r\n", b"\n"))
    rows = [json.loads(line) for line in output.decode("utf-8").splitlines()]
    if len(rows) != repeats*len(queries):
        raise ValueError("Unexpected number of search responses")
    result = []
    for i,q in enumerate(queries):
        samples = rows[i::len(queries)]
        if any(s["query"] != q or s["total"] != samples[0]["total"] or s["results"] != samples[0]["results"] for s in samples):
            raise ValueError("Responses differ between repetitions")
        result.append({"query":q,"total":samples[0]["total"],"first_ids":[d["id"] for d in samples[0]["results"]],
                       "samples_ms":[s["elapsed_ms"] for s in samples],"median_ms":statistics.median(s["elapsed_ms"] for s in samples)})
    return {"queries":result,"wall_ms":(time.perf_counter()-start)*1000,"peak_working_set_bytes":peak[0] or None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--dict", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--bin-dir", type=Path, default=ROOT/"build/boolean-native")
    parser.add_argument("--memory-mib", type=int, default=64)
    parser.add_argument("--reserve-mib", type=int, default=4608)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output/"INCOMPLETE").write_text("Lab 10 experiment has not finished\n", encoding="utf-8")
    with (args.output/"tests.txt").open("wb") as log:
        code = subprocess.run([sys.executable, "run_tests.py", "--lab", "10"], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
    if code:
        parser.exit(1, "Lab10 tests failed; see tests.txt\n")
    extension = ".exe" if sys.platform == "win32" else ""
    builder = (args.bin_dir/("positional_index"+extension)).resolve()
    search = (args.bin_dir/("boolean_search"+extension)).resolve()
    positions = (args.output/"positions").resolve()
    command = [str(builder),"build","--index",str(args.index.resolve()),"--corpus",str(args.corpus.resolve()),
               "--output",str(positions),"--dict",str(args.dict.resolve()),"--memory-mib",str(args.memory_mib),"--reserve-mib",str(args.reserve_mib)]
    build = execute(command, args.output/"build.log")
    stats = next(json.loads(line) for line in reversed((args.output/"build.log").read_text(encoding="utf-8").splitlines()) if line.startswith('{'))
    verify = execute([str(builder),"verify","--index",str(args.index.resolve()),"--positions",str(positions)],args.output/"verify.log")
    queries = (ROOT/"tests/fixtures/lab10_queries.txt").read_text(encoding="utf-8").splitlines()
    modes = {}
    for space in ("surface", "lemma"):
        command = [str(search),"--index",str(args.index.resolve()),"--positions",str(positions),"--space",space,"--limit","5"]
        if space == "lemma":
            command += ["--dict", str(args.dict.resolve())]
        modes[space] = measure(command, queries, args.output, space)
        print(space+": query measurements saved", flush=True)
    for measurement in modes.values():
        rows = {q["query"]:q["total"] for q in measurement["queries"]}
        if rows['"doom eternal"'] > rows["doom AND eternal"] or rows['"god of war"'] > rows["god AND of AND war"]:
            raise ValueError("Phrase result exceeds conjunction")
    summary = {"date":datetime.now().isoformat(),"statistics":stats,"build_measurement":build,"verification_measurement":verify,
               "binary_sha256":file_hash(builder),"search_binary_sha256":file_hash(search),"dictionary_sha256":file_hash(args.dict),
               "corpus_sha256":file_hash(args.corpus),"files":{name:{"bytes":(positions/name).stat().st_size,"sha256":file_hash(positions/name)}
                                                              for name in ("position_terms.bin","positions.bin")},"modes":modes}
    (args.output/"summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    command = [str(builder),"inspect","--index",str(args.index.resolve()),"--positions",str(positions),"--term","doom","--limit","10"]
    example = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True).stdout
    (args.output/"coordinates.txt").write_bytes(example.replace(b"\r\n",b"\n"))
    lines = ["# ЛР10: цитатный поиск", "", "| Запрос | Словоформы | Леммы | Формы, мс | Леммы, мс |", "|---|---:|---:|---:|---:|"]
    latex_rows = []
    for a,b in zip(modes["surface"]["queries"], modes["lemma"]["queries"]):
        lines.append(f"| `{a['query']}` | {a['total']} | {b['total']} | {a['median_ms']:.2f} | {b['median_ms']:.2f} |")
        label = a['query'].replace('"',r'\textquotedbl{}')
        latex_rows.append(label+" & "+str(a['total'])+" & "+str(b['total'])+" & "+f"{a['median_ms']:.2f}".replace('.',',')+" & "+f"{b['median_ms']:.2f}".replace('.',',')+r" \\")
    lines += ["", "Медианы трёх повторений в одном процессе каждого режима. Время C++ исключает загрузку словаря и вывод метаданных.",
              f"Документов: {stats['documents']}; координат: {stats['coordinates']}; начальных частей: {stats['initial_runs']}; проходов слияния: {stats['merge_passes']}.",
              "Координаты разделены по полям. Регистр и пунктуация нормализуются токенизатором; это совпадение последовательности токенов, а не байтов текста."]
    (args.output/"README.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    spaced = lambda n: f"{n:,}".replace(",", r"\,")
    macros = {"PositionDocuments":spaced(stats['documents']),"PositionTokens":spaced(stats['tokens']),"PositionCount":spaced(stats['coordinates']),
              "PositionMB":f"{sum(v['bytes'] for v in summary['files'].values())/1e6:.2f}","PositionSeconds":f"{stats['elapsed_ms']/1000:.2f}",
              "PositionVerifySeconds":f"{verify['wall_ms']/1000:.2f}","PositionPeakMiB":f"{build['peak_working_set_bytes']/2**20:.2f}" if build['peak_working_set_bytes'] else "---"}
    tex = "".join("\\newcommand{\\"+k+"}{"+v.replace('.',',')+"}\n" for k,v in macros.items())
    tex += "\\newcommand{\\PhraseRows}{%\n"+"\n".join(latex_rows)+"\n}\n"
    (args.output/"report_data.tex").write_text(tex, encoding="utf-8")
    (args.output/"INCOMPLETE").unlink()
    if args.report_dir:
        args.report_dir.mkdir(parents=True, exist_ok=True)
        for name in ("summary.json","surface.jsonl","lemma.jsonl","README.md","report_data.tex","tests.txt","coordinates.txt"):
            shutil.copy2(args.output/name,args.report_dir/name)
    print("Complete positional index: "+str(positions), flush=True)


if __name__ == "__main__":
    main()
