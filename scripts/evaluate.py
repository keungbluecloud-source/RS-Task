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
        sources = [item.chunk.source for item in retrieved[:5]]
        expected = set(record["expected_sources"])
        try:
            response = pipeline.ask(record["question"], use_llm=False)
            answer_text = response.text
            refused = response.refused
            validation_error = None
            backend = response.backend
            answer_sources = [
                {
                    "citation_id": f"S{source_id}",
                    "document": chunk.source,
                    "page": chunk.page,
                    "section": chunk.section,
                    "excerpt": " ".join(chunk.text.split())[:300],
                }
                for source_id, chunk in zip(response.source_ids, response.sources)
            ]
        except ValueError as error:
            answer_text = ""
            refused = False
            validation_error = str(error)
            backend = "validation"
            answer_sources = []
        expected_behavior = record.get("expected_behavior")
        behavior_pass = ((expected_behavior == "validation_error" and validation_error is not None)
                         or (expected_behavior in {"answer", "answer_with_conflict"}
                             and validation_error is None and not refused)
                         or expected_behavior is None)
        results.append({**record, "retrieved_sources": sources,
                        "hit_at_5": expected.issubset(sources),
                        "answer": answer_text, "refused": refused,
                        "validation_error": validation_error,
                        "backend": backend,
                        "answer_sources": answer_sources,
                        "behavior_pass": behavior_pass})
    args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    print(f"Recorded {len(results)} evaluations in {args.output}")


if __name__ == "__main__":
    main()
