"""Readable presentation of saved native retrieval results, not a second search engine."""
import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=Path("doc/lab4/quality.json"))
    parser.add_argument("--query", default="q10", help="query ID; default demonstrates Windows/window")
    args = parser.parse_args()
    data = json.loads(args.results.read_text(encoding="utf-8"))
    query = next((q for q in data["queries"] if q["id"] == args.query), None)
    if query is None:
        parser.error("Query ID not present in results")
    docs = {d["id"]: d for d in data["documents"]}
    print("SAVED EXPERIMENT (not a live search)")
    print(f"Query: {query['text']}\nIntent: {query['intent']}")
    print(f"Pool: {data['pool_documents']} real publications; field={data['field']}")
    for mode in ("exact", "lemma", "hybrid"):
        print("\n" + mode.upper())
        score = query["metrics"][mode]
        print(" ".join(f"{key}@{data['k']}={value:.3f}" for key, value in score.items()))
        for rank, i in enumerate(query["rankings"][mode], 1):
            grade = query["relevant"].get(str(i), 0)
            print(f"{rank}. [rel={grade}] #{i} {docs[i]['title']}\n   {docs[i]['url']}")
    print("\nProvisional author judgments; diagnostic pool, not a representative production benchmark.")


if __name__ == "__main__":
    main()
