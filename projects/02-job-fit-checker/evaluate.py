#!/usr/bin/env python3
"""Test the real AI model on the sample job ads and CVs (needs Ollama running).

    uv run evaluate.py                      # the default model
    uv run evaluate.py --model qwen3.5:9b   # try another model with the same checks
    uv run evaluate.py --think              # let the model "think" before answering

Exact scores depend on the model, so this checks what any good fit checker must get right:
  * a matching CV scores clearly higher than a partly matching one, which beats an unrelated one
  * the matching CV lands at least in "Moderate", the unrelated one in "Low"
  * known facts: the AI student's CV has Python, and is missing Kubernetes
  * a German job ad gets a German "Why me?" sentence
  * the same input gives (almost) the same score every time
  * the AI's "yes" and "partly" claims almost always come with proof that is really in the CV
"""
import argparse
import re
import sys
import time
from pathlib import Path

import jobfit

SAMPLES = Path(__file__).with_name("samples")
CASES = {  # name -> (job ad, CV)
    "strong": ("job_ai_engineer_en.txt", "cv_ai_student.txt"),
    "partial": ("job_ai_engineer_en.txt", "cv_data_analyst.txt"),
    "unrelated": ("job_ai_engineer_en.txt", "cv_nurse.txt"),
    "german": ("job_werkstudent_genai_de.txt", "cv_ai_student.txt"),
}
REPEATS = 3  # runs of the "strong" case, to measure how stable the score is
MIN_GAP = 10  # points between strong > partial > unrelated
MAX_SPREAD = 5  # points between the highest and lowest of the repeated runs
MIN_PROOF_RATE = 0.8  # share of "yes"/"partly" claims whose proof is found in the CV

GERMAN_WORDS = {"ich", "und", "mit", "der", "die", "das", "für", "habe", "bei", "von", "zu", "ein", "eine", "meine"}
ENGLISH_WORDS = {"i", "and", "with", "the", "for", "have", "at", "of", "to", "a", "an", "my"}

FAILS, CHECKED = [], [0]


def check(ok, label):
    CHECKED[0] += 1
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok:
        FAILS.append(label)


def find(result, skill):
    return [c for c in result.checks if skill in c.skill.lower()]


def looks_german(text):
    tokens = re.findall(r"\w+", text.lower())
    return sum(t in GERMAN_WORDS for t in tokens) > sum(t in ENGLISH_WORDS for t in tokens)


def run(name, model, think):
    job_file, cv_file = CASES[name]
    start = time.perf_counter()
    r = jobfit.check_fit(jobfit.read_file(SAMPLES / job_file), jobfit.read_file(SAMPLES / cv_file), model, think=think)
    seconds = time.perf_counter() - start
    print(
        f"  {name:<10} {r.match_percent:>3}% {r.band:<9} have {len(r.have):>2} · partly {len(r.partly):>2} · "
        f"missing {len(r.missing):>2} · claims without proof {len(r.unverified)} · {seconds:4.0f} s"
    )
    for c in r.unverified:
        print(f"             ⚠️  {c.skill}: {c.evidence!r} is not in the CV")
    return r, seconds


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=jobfit.MODEL)
    parser.add_argument("--think", action="store_true", help="let the model think before answering")
    args = parser.parse_args()
    model, think = args.model, args.think or jobfit.THINK

    print(f"Model: {model} · thinking {'on' if think else 'off'}\n\nRuns:")
    try:
        results = {name: run(name, model, think) for name in CASES}
        repeats = [results["strong"]] + [run("strong", model, think) for _ in range(REPEATS - 1)]
    except jobfit.JobFitError as e:
        sys.exit(f"Error: {e}")

    strong, partial, unrelated, german = (results[n][0] for n in CASES)
    every = [r for r, _ in list(results.values()) + repeats[1:]]

    print("\nChecks:")
    check(strong.match_percent >= partial.match_percent + MIN_GAP,
          f"matching CV beats partly matching CV by {MIN_GAP}+ points ({strong.match_percent} vs {partial.match_percent})")
    check(partial.match_percent >= unrelated.match_percent + MIN_GAP,
          f"partly matching CV beats unrelated CV by {MIN_GAP}+ points ({partial.match_percent} vs {unrelated.match_percent})")
    check(strong.match_percent >= 60, f"matching CV is Moderate or better ({strong.match_percent}% {strong.band})")
    check(unrelated.band == "Low", f"unrelated CV is Low ({unrelated.match_percent}% {unrelated.band})")

    python = find(strong, "python")
    check(bool(python) and all(c.counted == "yes" for c in python), "Python is found in the AI student's CV")
    kubernetes = find(strong, "kubernetes")
    check(bool(kubernetes) and all(c.counted == "no" for c in kubernetes), "Kubernetes is listed as missing")
    check(not find(unrelated, "python") or all(c.counted == "no" for c in find(unrelated, "python")),
          "the nurse's CV is not credited with Python")

    check(looks_german(german.why_me), f"German job ad gets a German 'Why me?': {german.why_me!r}")
    check(all(len(r.checks) >= 3 and r.why_me for r in every), "every run lists 3+ requirements and a 'Why me?'")

    scores = [r.match_percent for r, _ in repeats]
    check(max(scores) - min(scores) <= MAX_SPREAD, f"same input, same score ({REPEATS} runs: {scores})")

    claims = [c for r in every for c in r.checks if c.status != "no"]
    proved = sum(c.verified for c in claims)
    check(proved >= MIN_PROOF_RATE * len(claims),
          f"{MIN_PROOF_RATE:.0%}+ of the AI's claims have real proof in the CV ({proved} of {len(claims)})")

    seconds = [s for _, s in list(results.values()) + repeats[1:]]
    print(f"\nAverage time per check: {sum(seconds) / len(seconds):.0f} seconds")
    if FAILS:
        sys.exit(f"{len(FAILS)} of {CHECKED[0]} checks failed.")
    print(f"All {CHECKED[0]} checks passed.")


if __name__ == "__main__":
    main()
