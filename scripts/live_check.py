"""Run one transcript through the real Gemini model and print what came back.

A hand-driven alternative to `pytest -m live`. The test suite asserts; this
prints, which is what you want when you are trying to see whether an extraction
looks right rather than to prove a known case still passes.

    python scripts/live_check.py --keys              # probe every configured key
    python scripts/live_check.py --list              # show fixture names
    python scripts/live_check.py normal_worker       # run one fixture, live
    python scripts/live_check.py --all               # run every fixture, live
    python scripts/live_check.py --file talk.txt     # run your own transcript
    ... | python scripts/live_check.py -             # read a transcript on stdin
    python scripts/live_check.py --batch --all       # the batch path: answers,
                                                     # categories, and counts

Every mode that reaches the model costs a real API call. Nothing here writes to
the database or the network beyond Gemini itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.intelligence import analysis, categorize, questions as questions_module  # noqa: E402
from app.intelligence.batch_engine import build_batch_intelligence  # noqa: E402
from app.intelligence.engine import build_default_engine  # noqa: E402
from app.intelligence.providers.base import ProviderError  # noqa: E402
from app.intelligence.providers.gemini import (  # noqa: E402
    DEFAULT_MODEL,
    GeminiProvider,
    api_keys_from_environment,
    mask_key,
)

FIXTURE_DIR = ROOT / "tests" / "fixtures" / "transcripts"


def fixtures() -> dict[str, dict[str, Any]]:
    return {
        json.loads(path.read_text(encoding="utf-8"))["fixture"]: json.loads(
            path.read_text(encoding="utf-8")
        )
        for path in sorted(FIXTURE_DIR.glob("*.json"))
    }


def probe_keys() -> int:
    """Call the model once per key. The only way to prove a key works.

    A key that has never used a gated model still lists it in ``models.list()``
    and only fails on a real request, so listing models proves nothing.
    """

    keys = api_keys_from_environment()
    if not keys:
        print("no GEMINI_API_KEY configured (check .env)")
        return 1

    print(f"probing {len(keys)} key(s) against {DEFAULT_MODEL}\n")
    bad = 0
    for index, key in enumerate(keys, start=1):
        label = f"{index:2}. {mask_key(key)}"
        try:
            GeminiProvider(api_keys=[key]).complete_json(
                system="Reply with JSON only.",
                user='Return exactly {"ok": true}',
            )
        except Exception as exc:  # noqa: BLE001 - report every key, do not stop at the first
            bad += 1
            print(f"{label}  FAIL  {str(exc)[:110]}")
        else:
            print(f"{label}  ok")

    print(f"\n{len(keys) - bad} of {len(keys)} usable")
    return 1 if bad else 0


def show(name: str, transcript: str, worker_id: str, claims: Any, expected: str | None) -> bool:
    """Extract, evaluate, and print. Returns True when the verdict is as expected."""

    engine = build_default_engine()
    print(f"=== {name} ===")
    try:
        evidence = engine.extract_evidence(transcript, worker_id)
    except ProviderError as exc:
        print(f"provider failed: {exc}")
        return False

    if evidence["extraction_error"]:
        print("extraction_error: the model call itself failed")
        return False

    print(f"provider   {evidence['provider']}  model {DEFAULT_MODEL}")
    print(f"consent    {evidence['consent']}")
    print(f"language   {evidence['language']}")
    if evidence["interview_stopped"]:
        print(f"stopped    {evidence['stop_reason']}")

    print("\nfacts the model extracted:")
    for fact, entry in evidence["facts"].items():
        value = entry.get("value")
        quote = (entry.get("evidence") or "").replace("\n", " ")
        if len(quote) > 60:
            quote = quote[:57] + "..."
        print(f"  {fact:<34} {str(value):<10} {entry.get('state'):<10} {quote}")

    result = engine.evaluate_clauses(evidence)
    if claims is not None:
        result = engine.compare_with_employer(result, claims)

    print("\nclauses the rules derived:")
    for clause, entry in result.clauses.items():
        payload = entry.model_dump() if hasattr(entry, "model_dump") else entry
        print(f"  {clause:<34} {str(payload.get('value')):<10} {payload.get('status')}")

    if result.contradictions:
        print("\ncontradictions against the employer claim:")
        for item in result.contradictions:
            payload = item.model_dump() if hasattr(item, "model_dump") else item
            flag = "material" if payload.get("material") else "immaterial"
            print(f"  [{flag}] {payload.get('type')}: {payload.get('description')}")

    print(f"\nsafeguarding_flag  {result.safeguarding_flag}")
    print(f"VERDICT            {result.overall_verdict}")
    if expected:
        agreed = result.overall_verdict == expected
        print(f"expected           {expected}  {'match' if agreed else 'MISMATCH'}")
        return agreed
    return True


#: The question set used by --batch unless --questions says otherwise. Typed the
#: way an admin types it, abbreviations and all, because that is the input.
DEFAULT_QUESTIONS = "1 - what is ur age? 2 - Do u get enough compensation?"


def run_batch(question_text: str, transcripts: dict[str, str]) -> int:
    """Run the batch path over several transcripts and print what it produced.

    The fastest loop for the question that unit tests cannot answer: whether the
    categories the model derives across a whole batch are ones a person would
    actually count. One extraction call per transcript, then a single
    categorization call for the batch, then the counts, which are computed here.
    """

    parsed = questions_module.parse_questions(question_text)
    if not parsed:
        print("no questions found in that text")
        return 1
    question_set = questions_module.build_question_set(parsed)

    print(f"{len(question_set)} question(s), age first:")
    for question in question_set:
        print(f"  {question['index']}. {question['text']}   [{question['slug']}]")
    print(f"\n{len(transcripts)} transcript(s), {DEFAULT_MODEL}\n")

    engine = build_batch_intelligence()
    records: list[dict[str, Any]] = []
    for worker_id, transcript in transcripts.items():
        print(f"=== {worker_id} ===")
        try:
            extraction = engine.extract_answers(transcript, worker_id, question_set)
        except ProviderError as exc:
            print(f"provider failed: {exc}\n")
            continue
        for slug, entry in extraction["answers"].items():
            quote = (entry.get("evidence") or "").replace("\n", " ")
            if len(quote) > 52:
                quote = quote[:49] + "..."
            print(f"  {slug:<30} {str(entry.get('value')):<14} {entry.get('state'):<10} {quote}")
        print(f"  consent {extraction['consent']}   age {extraction['age_assessment']}")
        if extraction["excluded"]:
            # Printed loudly: this record is about to disappear from every count,
            # and that is the intended behaviour, not a bug in the categories.
            print(f"  EXCLUDED  {extraction['exclusion_reason']}")
        print()
        records.append(
            {
                "worker_id": worker_id,
                "name": worker_id,
                "answers": extraction["answers"],
                "consent": extraction["consent"],
                "excluded": extraction["excluded"],
                "exclusion_reason": extraction["exclusion_reason"],
            }
        )

    if not records:
        print("nothing extracted, so no categories to derive")
        return 1

    counted = [record for record in records if not record["excluded"]]
    categories = engine.categorize(question_set, counted)
    categorize.apply_categories(counted, categories)

    print("categories the model derived for this batch:")
    for slug, section in categories.items():
        print(f"  {slug}: {', '.join(section['categories']) or '(none)'}")

    summary = analysis.summarize(question_set, records, categories)
    print(f"\n{analysis.headline(summary)}")
    for slug, block in summary["questions"].items():
        print(f"\n  {block['question']}")
        for name, total in block["category_counts"].items():
            print(f"    {name:<28} {total}")
        if not block["category_counts"]:
            print("    (nobody categorised)")
    for record in summary["excluded"]:
        print(f"\n  excluded: {record['name']} — {record['reason']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run a transcript through the live Gemini model and print the result.",
    )
    parser.add_argument(
        "target",
        nargs="?",
        help="a fixture name, or '-' to read a transcript from stdin",
    )
    parser.add_argument("--file", help="read the transcript from this file")
    parser.add_argument("--worker-id", default="W020", help="worker id to attribute (default W020)")
    parser.add_argument("--keys", action="store_true", help="probe every configured key and exit")
    parser.add_argument("--list", action="store_true", help="list fixture names and exit")
    parser.add_argument("--all", action="store_true", help="run every fixture")
    parser.add_argument("--json", action="store_true", help="dump raw extraction JSON instead")
    parser.add_argument(
        "--batch",
        action="store_true",
        help="run the batch path (question set, categories, counts) instead of the clause path",
    )
    parser.add_argument(
        "--questions",
        default=DEFAULT_QUESTIONS,
        help="the questions to ask, typed as an admin would type them",
    )
    args = parser.parse_args()

    if args.list:
        for name, fixture in fixtures().items():
            print(f"{name:<34} {fixture['expected_result']['overall_verdict']:<20} {fixture['description']}")
        return 0

    if args.keys:
        return probe_keys()

    if not api_keys_from_environment():
        print("no GEMINI_API_KEY configured. Put keys in .env; see .env.example.")
        return 1

    if args.batch:
        available = fixtures()
        if args.all:
            chosen = {name: fixture["transcript"] for name, fixture in available.items()}
        elif args.file or args.target == "-":
            source = _transcript(args)
            if not source or not source.strip():
                print("empty transcript")
                return 1
            chosen = {args.worker_id: source}
        elif args.target in available:
            chosen = {available[args.target]["worker_id"]: available[args.target]["transcript"]}
        else:
            print(f"--batch needs --all, a fixture name, --file, or '-'\navailable: {', '.join(available)}")
            return 1
        return run_batch(args.questions, chosen)

    if args.json:
        source = _transcript(args)
        if source is None:
            parser.error("--json needs a fixture name, --file, or '-'")
        print(json.dumps(build_default_engine().extract_evidence(source, args.worker_id), indent=2))
        return 0

    if args.all:
        results = {
            name: show(
                name,
                fixture["transcript"],
                fixture["worker_id"],
                fixture["employer_claims"],
                fixture["expected_result"]["overall_verdict"],
            )
            for name, fixture in fixtures().items()
        }
        agreed = sum(results.values())
        print(f"\n{'=' * 40}\n{agreed} of {len(results)} fixtures produced the expected verdict")
        for name, ok in results.items():
            if not ok:
                print(f"  MISMATCH {name}")
        return 0 if agreed == len(results) else 1

    if args.file or args.target == "-":
        text = (
            Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
        )
        if not text.strip():
            print("empty transcript")
            return 1
        return 0 if show("your transcript", text, args.worker_id, None, None) else 1

    if not args.target:
        parser.print_help()
        print("\nfixtures:")
        for name in fixtures():
            print(f"  {name}")
        return 1

    available = fixtures()
    if args.target not in available:
        print(f"unknown fixture: {args.target}\navailable: {', '.join(available)}")
        return 1

    fixture = available[args.target]
    ok = show(
        args.target,
        fixture["transcript"],
        fixture["worker_id"],
        fixture["employer_claims"],
        fixture["expected_result"]["overall_verdict"],
    )
    return 0 if ok else 1


def _transcript(args: argparse.Namespace) -> str | None:
    if args.file:
        return Path(args.file).read_text(encoding="utf-8")
    if args.target == "-":
        return sys.stdin.read()
    if args.target and args.target in fixtures():
        return fixtures()[args.target]["transcript"]
    return None


if __name__ == "__main__":
    raise SystemExit(main())
