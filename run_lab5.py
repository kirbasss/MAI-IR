"""Run the C++ Zipf counter on an immutable snapshot and plot its actual results."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent
DEFAULT_BINARY = ROOT / "build/zipf-native/zipf.exe"
LABELS = {
    "all": "Весь корпус", "ru": "Русскоязычные источники", "en": "Англоязычные источники",
    "news": "Новости", "article": "Статьи", "review": "Обзоры", "blog": "Блоги", "unknown": "Неизвестно",
    "stopgame": "StopGame", "playground": "PlayGround", "ixbt_games": "IXBT.games",
    "igromania": "Игромания", "gamemag": "GameMAG", "pcgamer": "PC Gamer",
    "eurogamer": "Eurogamer", "gamingonlinux": "GamingOnLinux",
}


def file_hash(path: Path) -> str:
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def verify_snapshot(snapshot: Path) -> dict:
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("format") != "MAIIR3-v1" or file_hash(snapshot / "corpus.bin") != manifest["corpus_sha256"]:
        raise ValueError("Snapshot format or corpus SHA-256 mismatch")
    if "groups_sha256" in manifest:
        if file_hash(snapshot / "groups.tsv") != manifest["groups_sha256"]:
            raise ValueError("Publication groups SHA-256 mismatch")
    return manifest


def sample_curves(path: Path, groups: list[dict]) -> tuple[dict, dict]:
    """Sample for drawing only; all counting, sorting and model fitting is C++."""
    curves = {g["name"]: [] for g in groups}
    tops = {g["name"]: [] for g in groups}
    next_rank = {g["name"]: 1 for g in groups}
    seen = {g["name"]: 0 for g in groups}
    totals = {g["name"]: 0 for g in groups}
    last = {}
    with path.open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file, delimiter="\t"):
            name, rank, count = row["group"], int(row["rank"]), int(row["frequency"])
            if name not in curves:
                raise ValueError("Unexpected frequency group")
            if rank != seen[name] + 1 or count <= 0:
                raise ValueError("Invalid frequency/rank sequence")
            seen[name] += 1
            totals[name] += count
            last[name] = (rank, count)
            if rank <= 20:
                tops[name].append({"rank": rank, "term": row["term"], "frequency": count})
            if rank <= 50 or rank >= next_rank[name]:
                curves[name].append((rank, count))
                next_rank[name] = max(rank + 1, int(rank * 1.025))
    for group in groups:
        name = group["name"]
        if last.get(name, (0, 0))[0] != group["vocabulary"]:
            raise ValueError("Rank table and vocabulary statistics disagree")
        if totals[name] != group["tokens"]:
            raise ValueError("Frequency sums and C++ token statistics disagree")
        if name in last and curves[name][-1] != last[name]:
            curves[name].append(last[name])
    return curves, tops


def plots(stats: dict, curves: dict, tops: dict, target: Path):
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    groups = {g["name"]: g for g in stats["groups"]}

    def save(fig, name):
        fig.savefig(target / (name + ".png"), dpi=170)
        fig.savefig(target / (name + ".pdf"))
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(8.5, 5.2), layout="constrained")
    g = groups["all"]
    points = curves["all"]
    if points:
        x, y = zip(*points)
        ax.loglog(x, y, label="Наблюдаемые частоты", linewidth=2)
        ax.loglog(x, [g["zipf_c"] / r for r in x], "--", label=r"Ципф: $N/(H_V r)$")
        if g["alpha"] is not None and g["power_c"] is not None:
            ax.loglog(x, [g["power_c"] / r ** g["alpha"] for r in x], ":",
                      label=f"Аппроксимация C/r^α: α={g['alpha']:.3f}, r={stats['fit_min']}–{stats['fit_max']}")
            if min(stats["fit_max"], g["vocabulary"]) > stats["fit_min"]:
                ax.axvspan(stats["fit_min"], min(stats["fit_max"], g["vocabulary"]), color="black", alpha=.035)
        ax.legend()
    kind = "лемм (неоднозначные формы сохранены)" if stats.get("representation") == "lemma_unambiguous" else "словоформ"
    ax.set(xlabel="Ранг термина r", ylabel="Число вхождений f(r)",
           title=f"Распределение {kind}\n{stats['documents']:,} документов")
    ax.grid(alpha=.25, which="both")
    save(fig, "zipf_all")
    for dimension, title in (("source", "Сайты"), ("language", "Языки источников"), ("type", "Типы публикаций")):
        fig, ax = plt.subplots(figsize=(8.5, 5.2), layout="constrained")
        drawn = False
        for name, g in groups.items():
            if not name.startswith(dimension + ":") or not curves[name] or not g["tokens"]:
                continue
            drawn = True
            x, y = zip(*curves[name])
            label = LABELS.get(name.split(":", 1)[1], name.split(":", 1)[1])
            ax.loglog(x, [f / g["tokens"] for f in y], label=f"{label} (N={g['tokens']:,})", alpha=.85)
        ax.set(xlabel="Ранг внутри группы", ylabel="Доля вхождений f(r)/N группы", title=title)
        ax.grid(alpha=.25, which="both")
        if drawn:
            ax.legend(fontsize=8)
        else:
            ax.text(.5, .5, "Нет данных для этого разреза", ha="center", transform=ax.transAxes)
        save(fig, "zipf_" + dimension)
    fig, ax = plt.subplots(figsize=(8.5, 5.5), layout="constrained")
    top = tops["all"][:16][::-1]
    ax.barh([row["term"] for row in top], [row["frequency"] for row in top], color="#2563eb")
    ax.set(xlabel="Число вхождений", title="Самые частые токены (стоп-слова сохранены)")
    ax.grid(axis="x", alpha=.25)
    save(fig, "top_terms")


def write_results(target: Path, stats: dict, manifest: dict, curves: dict, tops: dict, provenance: dict):
    summary = {"statistics": stats, "snapshot": manifest, "provenance": provenance, "top_terms": tops}
    (target / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (target / "curves.tsv").open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file, delimiter="\t")
        writer.writerow(("group", "rank", "frequency"))
        for group, points in curves.items():
            for rank, count in points:
                writer.writerow((group, rank, count))
    all_group = next(g for g in stats["groups"] if g["name"] == "all")
    hapax_share = all_group["hapax"] / all_group["vocabulary"] if all_group["vocabulary"] else 0
    def tex_number(value, digits=0):
        return f"{value:,.{digits}f}".replace(",", r"\,").replace(".", ",")
    def tex_escape(value):
        return value.replace("_", r"\_").replace("&", r"\&").replace("#", r"\#")
    macros = {
        "ZipfDate": datetime.fromisoformat(provenance["created_at"]).strftime("%d.%m.%Y"),
        "ZipfDocuments": tex_number(stats["documents"]), "ZipfBytesMB": tex_number(stats["input_utf8_bytes"]/1e6, 2),
        "ZipfTokens": tex_number(all_group["tokens"]), "ZipfVocabulary": tex_number(all_group["vocabulary"]),
        "ZipfHapax": tex_number(all_group["hapax"]), "ZipfHapaxPercent": tex_number(100*hapax_share, 2),
        "ZipfSeconds": tex_number(stats["elapsed_ms"]/1000, 2),
        "ZipfMemory": tex_number(stats["counting_buffer_bytes"]//1048576),
        "ZipfAlpha": "---" if all_group["alpha"] is None else tex_number(all_group["alpha"], 4),
        "ZipfFitMin": str(stats["fit_min"]), "ZipfFitMax": tex_number(stats["fit_max"]),
        "ZipfCountingRuns": str(stats["counting_runs"]), "ZipfRankingRuns": str(stats["ranking_runs"]),
        "ZipfTitleOnly": tex_number(manifest["title_only_documents"]),
    }
    for language, macro in (("ru", "ZipfRuAlpha"), ("en", "ZipfEnAlpha")):
        group = next((g for g in stats["groups"] if g["name"] == "language:" + language), None)
        macros[macro] = "---" if not group or group["alpha"] is None else tex_number(group["alpha"], 3)
    group_rows = []
    order = {"all": 0, "language": 1, "source": 2, "type": 3}
    for g in sorted(stats["groups"], key=lambda g: (order.get(g["name"].split(":")[0], 4), g["name"])):
        # Formal report covers the corpus, source languages and sites only.
        # Publication-type statistics remain in the standalone experiment results.
        if g["name"].startswith("type:"):
            continue
        kind, _, key = g["name"].partition(":")
        label = LABELS.get(key or kind, key or kind)
        if kind == "language":
            label = "RU-источники" if key == "ru" else ("EN-источники" if key == "en" else label)
        alpha = "---" if g["alpha"] is None else tex_number(g["alpha"], 3)
        group_rows.append(tex_escape(label) + " & " + " & ".join(
            (tex_number(g["documents"]), tex_number(g["tokens"]), tex_number(g["vocabulary"]), alpha)) + r" \\")
    (target / "report_data.tex").write_text(
        "".join("\\newcommand{\\" + name + "}{" + value + "}\n" for name, value in macros.items()) +
        "\\newcommand{\\ZipfGroupRows}{%\n" + "\n".join(group_rows) + "\n}\n", encoding="utf-8")
    rows = ["# ЛР5: закон Ципфа", "", "Данные получены собственным C++-счётчиком, а не Python Counter.", "",
            f"Снимок: **{stats['documents']:,} документов**, {stats['input_utf8_bytes']/1e9:.3f} ГБ текста. "
            f"C++ обработал его за {stats['elapsed_ms']/1000:.2f} с с буфером счётчика {stats['counting_buffer_bytes']//1048576} МиБ. "
            "Это один запуск, не медиана бенчмарка; загрузка MongoDB и построение рисунков в это время не включены.", "",
            f"Однократные токены составляют **{hapax_share:.2%} словаря**. "
            "Большая доля редких слов объясняет ступенчатый хвост; число hapax не является оценкой числа ошибок текста.", "",
            "| Группа | Документы | Токены N | Словарь V | Hapax | α |",
            "|---|---:|---:|---:|---:|---:|"]
    for g in stats["groups"]:
        alpha = "—" if g["alpha"] is None else f"{g['alpha']:.3f}"
        rows.append(f"| {g['name']} | {g['documents']:,} | {g['tokens']:,} | {g['vocabulary']:,} | {g['hapax']:,} | {alpha} |")
    rows += ["", "![Общий график](zipf_all.png)", "", "![Сайты](zipf_source.png)", "",
             "![Языки](zipf_language.png)", "", "![Типы](zipf_type.png)", "", "![Частые слова](top_terms.png)", "",
             "## Как читать результаты", "",
             "Частота — число вхождений токена, а не число документов с ним. Ранги начинаются с 1; равенства частот разрешаются лексикографически по UTF-8.",
             ("Применяется консервативная лемматизация: однозначные разборы объединяются, неоднозначные и неизвестные словоформы сохраняются. Каждое исходное вхождение учитывается ровно один раз."
              if stats.get("representation") == "lemma_unambiguous" else
              "Учитываются нормализованные словоформы. Лемматизация не применяется."),
             "Теоретическая кривая f(r)=C/r использует C=N/H_V, H_V=Σ(1/r): её сумма по всему словарю равна N.",
             f"Отдельная степень C/r^α подобрана C++ методом наименьших квадратов в log-координатах для рангов {stats['fit_min']}–{stats['fit_max']} (в пределах каждой группы). Это другая модель, а не замена закона Ципфа.",
             "Язык назначается по сайту, не определяется по каждому тексту. В русских публикациях остаются английские названия; это влияет на смешанную кривую.",
             "Тип берётся из parsed.category. Неподдержанные или отсутствующие категории отмечаются unknown; отсутствующие группы не выдумываются.",
             "На графиках сравнения частоты нормированы на число токенов группы. Корпуса разных размеров имеют разные длины хвоста; нормировка этого не устраняет.",
             "Отклонения ожидаемы из-за смешения языков, ограниченной игровой тематики, словоформ, собственных имён, новостей разной длины и конечного размера корпуса. Редкий хвост имеет ступени частот 1, 2, 3 и не образует идеальную прямую.",
             "Близость к прямой не доказывает качество парсинга и не является оценкой релевантности поиска.",
             "Целиком хранится только текущий текстовый буфер; таблица частот ограничена заданным бюджетом и сбрасывается отсортированными частями. Слияние открывает не более 24 входных файлов. Бюджет counting_buffer_bytes не включает поле документа, буферы слияния и токенизатор.",
             "Графики используют логарифмическую выборку точек для отрисовки; полные частоты сохранены в counted/frequencies.tsv локального каталога запуска.",
             "Снимок фиксирован файлом и SHA-256. Сам экспорт MongoDB курсором не является транзакционным снимком коллекции.", ""]
    (target / "README.md").write_text("\n".join(rows).rstrip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--snapshot", type=Path)
    source.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="new directory")
    parser.add_argument("--binary", type=Path, default=DEFAULT_BINARY)
    parser.add_argument("--dict", dest="dictionary", type=Path, help="C++ unambiguous lemmatization; each token still counts once")
    parser.add_argument("--memory-mib", type=int, default=64)
    parser.add_argument("--field", choices=("title", "text", "both"), default="both")
    parser.add_argument("--report-dir", type=Path, help="copy compact results/figures here; never corpus or full frequencies")
    args = parser.parse_args()
    if not 1 <= args.memory_mib <= 2048:
        parser.error("--memory-mib must be in 1..2048")
    try:
        binary = args.binary.resolve(strict=True)
        dictionary = args.dictionary.resolve(strict=True) if args.dictionary else None
        dictionary_hash = file_hash(dictionary) if dictionary else None
        args.output.mkdir(parents=True, exist_ok=False)
        marker = args.output / "INCOMPLETE"
        marker.write_text("Experiment has not finished.\n", encoding="utf-8")
        snapshot = args.snapshot.resolve() if args.snapshot else args.output.resolve() / "snapshot"
        if args.config:
            subprocess.run([sys.executable, str(ROOT / "export_corpus.py"), "--config", str(args.config.resolve()),
                            "--output", str(snapshot)], cwd=ROOT, check=True)
        manifest = verify_snapshot(snapshot)
        command = [str(binary), "--corpus", str(snapshot / "corpus.bin"), "--output", str(args.output.resolve() / "counted"),
                   "--memory-mib", str(args.memory_mib), "--field", args.field]
        if "groups_sha256" in manifest:
            command += ["--groups", str(snapshot / "groups.tsv")]
        else:
            print("Snapshot has no publication types: type comparison is unavailable.", flush=True)
        if dictionary:
            command += ["--dict", str(dictionary)]
        print(f"C++ Zipf: {manifest['documents']:,} documents; {args.memory_mib} MiB counting buffer", flush=True)
        result = subprocess.run(command, cwd=ROOT, capture_output=True)
        (args.output / "counter.log").write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise ValueError(result.stderr.decode("utf-8", errors="replace"))
        stats = json.loads((args.output / "counted/statistics.json").read_text(encoding="utf-8"))
        if stats["documents"] != manifest["documents"]:
            raise ValueError("C++ document count differs from snapshot manifest")
        print("Counting complete; preparing plots", flush=True)
        curves, tops = sample_curves(args.output / "counted/frequencies.tsv", stats["groups"])
        provenance = {"binary_sha256": file_hash(binary), "frequencies_sha256": file_hash(args.output / "counted/frequencies.tsv"),
                      "dictionary_sha256": dictionary_hash,
                      "created_at": datetime.now(timezone(timedelta(hours=3))).isoformat()}
        plots(stats, curves, tops, args.output)
        write_results(args.output, stats, manifest, curves, tops, provenance)
        if args.report_dir:
            if args.report_dir.resolve() == args.output.resolve():
                raise ValueError("Report directory must differ from experiment directory")
            args.report_dir.mkdir(parents=True, exist_ok=True)
            for file in args.output.iterdir():
                if file.suffix in {".png", ".pdf"} or file.name in {"README.md", "summary.json", "curves.tsv", "report_data.tex"}:
                    shutil.copy2(file, args.report_dir / file.name)
        marker.unlink()
        print(f"Results: {args.output.resolve()}")
        return 0
    except (OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"[lab5] {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
