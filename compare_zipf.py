"""Compare C++ word-form and lemma counts from the same immutable snapshot."""
from __future__ import annotations
import argparse
import csv
import json
from pathlib import Path
from run_lab5 import LABELS, file_hash


def validate(surface: dict, lemmas: dict):
    a, b = surface["statistics"], lemmas["statistics"]
    if a.get("representation", "surface") != "surface" or b.get("representation") != "lemma_unambiguous":
        raise ValueError("Expected surface and unambiguous-lemma experiments")
    for key in ("corpus_sha256", "groups_sha256"):
        if surface["snapshot"].get(key) != lemmas["snapshot"].get(key):
            raise ValueError("Snapshot checksum mismatch: " + key)
    for key in ("documents", "input_utf8_bytes", "field", "tokenizer", "fit_min", "fit_max"):
        if a[key] != b[key]:
            raise ValueError("Incompatible experiment settings: " + key)
    groups_a = {g["name"]: g for g in a["groups"]}
    groups_b = {g["name"]: g for g in b["groups"]}
    if groups_a.keys() != groups_b.keys():
        raise ValueError("Group sets differ")
    for name, g in groups_a.items():
        other = groups_b[name]
        if (g["tokens"], g["documents"]) != (other["tokens"], other["documents"]):
            raise ValueError("Token mass/document count changed in " + name)
        if other["vocabulary"] > g["vocabulary"]:
            raise ValueError("Single-valued lemmatization cannot increase vocabulary: " + name)


def load_curves(directory: Path, groups: list[dict]) -> dict:
    curves = {g["name"]: [] for g in groups}
    with (directory / "curves.tsv").open(encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file, delimiter="\t"):
            name = row["group"]
            if name not in curves:
                raise ValueError("Unexpected curve group")
            curves[name].append((int(row["rank"]), int(row["frequency"])))
    for g in groups:
        points = curves[g["name"]]
        if g["vocabulary"] and (not points or points[-1][0] != g["vocabulary"]):
            raise ValueError("Curve tail differs from vocabulary")
    return curves


def compare(surface_dir: Path, lemma_dir: Path, output: Path):
    surface = json.loads((surface_dir / "summary.json").read_text(encoding="utf-8"))
    lemmas = json.loads((lemma_dir / "summary.json").read_text(encoding="utf-8"))
    validate(surface, lemmas)
    a, b = surface["statistics"], lemmas["statistics"]
    ga = {g["name"]: g for g in a["groups"]}
    gb = {g["name"]: g for g in b["groups"]}
    ca, cb = load_curves(surface_dir, a["groups"]), load_curves(lemma_dir, b["groups"])
    output.mkdir(parents=True, exist_ok=False)
    marker = output / "INCOMPLETE"
    marker.write_text("Incomplete comparison.\n", encoding="utf-8")
    rows = []
    for name, g in ga.items():
        h = gb[name]
        reduction = 100 * (1 - h["vocabulary"] / g["vocabulary"]) if g["vocabulary"] else 0
        rows.append({"group": name, "tokens": g["tokens"], "surface_vocabulary": g["vocabulary"],
                     "lemma_vocabulary": h["vocabulary"], "vocabulary_reduction_percent": reduction,
                     "surface_hapax": g["hapax"], "lemma_hapax": h["hapax"],
                     "surface_alpha": g["alpha"], "lemma_alpha": h["alpha"]})
    total = ga["all"]["tokens"]
    summary = {"snapshot_sha256": surface["snapshot"]["corpus_sha256"],
               "dictionary_sha256": lemmas["provenance"].get("dictionary_sha256"),
               "surface_summary_sha256": file_hash(surface_dir / "summary.json"),
               "lemma_summary_sha256": file_hash(lemma_dir / "summary.json"),
               "surface_curves_sha256": file_hash(surface_dir / "curves.tsv"),
               "lemma_curves_sha256": file_hash(lemma_dir / "curves.tsv"),
               "known_tokens": b["known_tokens"], "changed_tokens": b["changed_tokens"],
               "ambiguous_tokens": b["ambiguous_tokens"], "tokens": total,
               "policy": "unambiguous_only; ambiguous and unknown forms preserved; one count per token", "groups": rows}
    (output / "comparison.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    def draw(ax, name, normalized=False, theory=False):
        n = ga[name]["tokens"] if normalized else 1
        if not n:
            return
        for curve, group, label, color in ((ca, ga, "Словоформы", "#2563eb"),
                                            (cb, gb, "Однозначная лемматизация", "#d97706")):
            if not curve[name]:
                continue
            x, y = zip(*curve[name])
            ax.loglog(x, [v/n for v in y], label=label, color=color)
            if theory:
                ax.loglog(x, [group[name]["zipf_c"]/r/n for r in x], "--", color=color, alpha=.6,
                          label="Ципф: " + label.lower())
        ax.set(xlabel="Ранг внутри распределения", ylabel="f(r)/N" if normalized else "Число вхождений f(r)")
        ax.grid(which="both", alpha=.2)

    def save(fig, name):
        fig.savefig(output / (name + ".png"), dpi=170)
        fig.savefig(output / (name + ".pdf"))
        plt.close(fig)

    fig, ax = plt.subplots(figsize=(8.5, 5.2), layout="constrained")
    draw(ax, "all", theory=True)
    ax.set_title("Словоформы и однозначная лемматизация\nОдин корпус, одинаковое число вхождений")
    ax.legend(fontsize=9)
    save(fig, "forms_vs_lemmas")
    for dimension, columns in (("language", 2), ("type", 3), ("source", 2)):
        names = [name for name in ga if name.startswith(dimension + ":")]
        if not names:
            continue
        columns = min(columns, len(names))
        count_rows = (len(names)+columns-1)//columns
        fig, axes = plt.subplots(count_rows, columns, figsize=(5*columns, 3.5*count_rows),
                                 layout="constrained", squeeze=False)
        for ax, name in zip(axes.flat, names):
            draw(ax, name, normalized=True)
            key = name.split(":", 1)[1]
            ax.set_title(LABELS.get(key, key), fontsize=11)
        for ax in list(axes.flat)[len(names):]:
            ax.set_visible(False)
        axes.flat[0].legend(fontsize=8)
        save(fig, "forms_vs_lemmas_" + dimension)
    def num(value, digits=0):
        return f"{value:,.{digits}f}".replace(",", r"\,").replace(".", ",")
    macros = {"LemmaVocabulary": num(gb["all"]["vocabulary"]), "LemmaHapax": num(gb["all"]["hapax"]),
              "LemmaHapaxPercent": num(100*gb["all"]["hapax"]/gb["all"]["vocabulary"], 2) if gb["all"]["vocabulary"] else "0",
              "LemmaReduction": num(rows[0]["vocabulary_reduction_percent"], 2),
              "LemmaAlpha": "---" if gb["all"]["alpha"] is None else num(gb["all"]["alpha"], 4),
              "LemmaChanged": num(b["changed_tokens"]), "LemmaAmbiguous": num(b["ambiguous_tokens"]),
              "LemmaAmbiguousPercent": num(100*b["ambiguous_tokens"]/total, 2) if total else "0",
              "LemmaKnownPercent": num(100*b["known_tokens"]/total, 2) if total else "0"}
    (output / "comparison_data.tex").write_text("".join(
        "\\newcommand{\\" + name + "}{" + value + "}\n" for name, value in macros.items()), encoding="utf-8")
    text = ["# Словоформы и лемматизация", "",
            "Одинаковый снимок, поля и группы; одно исходное вхождение даёт ровно одно вхождение в обоих вариантах.", "",
            "Однозначные разборы заменяются леммой; неоднозначные и неизвестные слова сохраняются. Контекстное снятие омонимии не выполняется.", "",
            f"Изменены {b['changed_tokens']:,} вхождений. Неоднозначные разборы: {b['ambiguous_tokens']:,}; они не размножаются по вариантам.", "",
            "| Группа | Словарь словоформ | После лемматизации | Сокращение, % | α словоформ | α после |",
            "|---|---:|---:|---:|---:|---:|"]
    for row in rows:
        def alpha(value):
            return "—" if value is None else f"{value:.4f}"
        text.append(f"| {row['group']} | {row['surface_vocabulary']:,} | {row['lemma_vocabulary']:,} | "
                    f"{row['vocabulary_reduction_percent']:.2f} | {alpha(row['surface_alpha'])} | {alpha(row['lemma_alpha'])} |")
    text += ["", "![Весь корпус](forms_vs_lemmas.png)", "", "![Языки](forms_vs_lemmas_language.png)", "",
             "![Типы](forms_vs_lemmas_type.png)", "", "![Сайты](forms_vs_lemmas_source.png)", "",
             "Пунктир — теоретическая модель Ципфа с фиксированной степенью 1 и нормировкой N/H_V отдельно для каждого словаря.",
             "α оценена по данным на рангах 10–10000. При изменении единицы подсчёта состав терминов на этих рангах меняется.",
             "Результат характеризует указанную консервативную политику и данный словарь; его нельзя выдавать за контекстно разрешённую лемматизацию всего корпуса.", ""]
    (output / "README.md").write_text("\n".join(text), encoding="utf-8")
    marker.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--surface", type=Path, required=True)
    parser.add_argument("--lemmas", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="new comparison directory")
    args = parser.parse_args()
    try:
        compare(args.surface, args.lemmas, args.output)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, f"[compare-zipf] {exc}\n")
    print(f"Comparison: {args.output.resolve()}")


if __name__ == "__main__":
    main()
