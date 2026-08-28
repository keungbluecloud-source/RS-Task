import argparse
import json
from pathlib import Path

from nist_rag.pipeline import Pipeline


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index", type=Path, default=Path("data/index.json"))
    parser.add_argument("--questions", type=Path, default=Path("evals/questions.json"))
    parser.add_argument("--output", type=Path, default=Path("evals/results.json"))
    args = parser.parse_args()
    pipeline = Pipeline.open(args.index)
    records = json.loads(args.questions.read_text())
    results = []
    for record in records:
        retrieved = pipeline.index.search(record["question"])
        response = pipeline.ask(record["question"], use_llm=False)
        sources = [item.chunk.source for item in retrieved[:5]]
        expected = set(record["expected_sources"])
        results.append({**record, "retrieved_sources": sources, "hit_at_5": expected.issubset(sources),
                        "answer": response.text, "refused": response.refused})
    args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    print(f"Recorded {len(results)} evaluations in {args.output}")


if __name__ == "__main__":
    main()
