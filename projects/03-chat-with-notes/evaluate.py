#!/usr/bin/env python3
"""Test the real models on the sample lecture notes (needs Ollama running).

    uv run evaluate.py
    uv run evaluate.py --model qwen3.5:9b --embed-model bge-m3   # compare other models

15 questions with known answers, written before the first run:
  * 12 that the notes answer (English, German, and English questions about the German notes):
    is the right page among the search results, does the answer contain the fact,
    and does it quote the right page?
  * 3 that the notes don't answer: does it say so instead of guessing?
"""
import argparse
import sys
import time
from pathlib import Path

import notes

SAMPLES = Path(__file__).with_name("samples")
ML, PM = "ml_lecture_evaluating_models.pdf", "projektmanagement_kapitel2.pdf"

# question, (file, page) that answers it, words the answer must contain (any one of them)
ANSWERABLE = [
    ("What is the F1 score if precision is 0.9 and recall is 0.6?", (ML, 6), ["0.72"]),
    ("How many folds are commonly used in k-fold cross-validation?", (ML, 3), ["5", "10"]),
    ("What does an AUC of 0.5 mean?", (ML, 8), ["random"]),
    ("When is the DS-204 exam and what am I allowed to bring?", (ML, 10), ["12 February 2027"]),
    ("Which metric punishes large errors more, MAE or RMSE?", (ML, 9), ["RMSE"]),
    ("How can I reduce overfitting?", (ML, 7), ["regularization", "early stopping", "dropout", "more training data"]),
    ("Was besagt die 8/80-Regel?", (PM, 3), ["80"]),
    ("Wie berechnet man ein Risiko?", (PM, 7), ["Eintrittswahrscheinlichkeit"]),
    ("Wie lange dauert das Daily Scrum höchstens?", (PM, 8), ["15"]),
    ("Bis wann muss die Hausarbeit abgegeben werden?", (PM, 9), ["31. März 2027", "31 März 2027"]),
    ("How long is a sprint in Scrum?", (PM, 8), ["4 weeks", "4 Wochen"]),
    ("What float do activities on the critical path have?", (PM, 4), ["0", "zero"]),
]
NOT_IN_NOTES = [
    "Who won the 2014 football World Cup?",
    "What is the capital of Australia?",
    "Which learning rate does the course recommend for gradient descent?",
]
MIN_SEARCH, MIN_FACT, MIN_PAGE, MIN_PROOF = 0.9, 0.8, 0.8, 0.9

FAILS, CHECKED = [], [0]


def check(ok, label):
    CHECKED[0] += 1
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok:
        FAILS.append(label)


def contains(text, options):
    return any(notes.words(o) in notes.words(text) for o in options)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=notes.CHAT_MODEL)
    parser.add_argument("--embed-model", default=notes.EMBED_MODEL)
    parser.add_argument("--k", type=int, default=notes.TOP_K, help="excerpts per question")
    args = parser.parse_args()

    print(f"Chat model: {args.model} · embedding model: {args.embed_model} · {args.k} excerpts per question\n")
    try:
        library = notes.Library(embed_model=args.embed_model)
        for pdf in sorted(SAMPLES.glob("*.pdf")):
            library.add_pdf(pdf.name, pdf.read_bytes())

        rows, seconds, cited = [], [], 0
        for question, page, facts in ANSWERABLE + [(q, None, None) for q in NOT_IN_NOTES]:
            start = time.perf_counter()
            a = notes.ask(library, question, args.model, args.k)
            seconds.append(time.perf_counter() - start)
            searched = page in [(h.chunk.file, h.chunk.page) for h in a.hits]
            quoted = page in [(s.chunk.file, s.chunk.page) for s in a.sources]
            cited += len(a.sources) + a.unverified
            rows.append((question, page, facts, a, searched, quoted))
            if page:
                print(f"  {'✅' if searched else '❌'} search  {'✅' if a.found and contains(a.text, facts) else '❌'} fact  "
                      f"{'✅' if quoted else '❌'} page {page[1]:<2}  {seconds[-1]:4.1f}s  {question}")
                if not (a.found and contains(a.text, facts) and quoted):
                    print(f"        answer: {a.text or '(not found)'}")
            else:
                print(f"  {'✅' if not a.found else '❌'} says \"not found\"           {seconds[-1]:4.1f}s  {question}")
                if a.found:
                    print(f"        answer: {a.text}")
    except notes.NotesError as e:
        sys.exit(f"Error: {e}")

    answerable = [r for r in rows if r[1]]
    n = len(answerable)
    search_ok = sum(r[4] for r in answerable)
    fact_ok = sum(r[3].found and contains(r[3].text, r[2]) for r in answerable)
    page_ok = sum(r[5] for r in answerable)
    proved = sum(len(r[3].sources) for r in rows)

    print("\nChecks:")
    check(search_ok >= MIN_SEARCH * n, f"the right page is among the search results: {search_ok} of {n} (need {MIN_SEARCH:.0%})")
    check(fact_ok >= MIN_FACT * n, f"the answer contains the fact: {fact_ok} of {n} (need {MIN_FACT:.0%})")
    check(page_ok >= MIN_PAGE * n, f"the answer quotes the right page: {page_ok} of {n} (need {MIN_PAGE:.0%})")
    check(all(not r[3].found for r in rows if not r[1]),
          f"says \"not found\" for questions the notes don't answer: "
          f"{sum(not r[3].found for r in rows if not r[1])} of {len(NOT_IN_NOTES)}")
    check(proved >= MIN_PROOF * cited, f"quotes really are in the notes: {proved} of {cited} (need {MIN_PROOF:.0%})")

    first = sum((r[3].hits[0].chunk.file, r[3].hits[0].chunk.page) == r[1] for r in answerable)
    print(f"\nThe right page was the search's #1 result for {first} of {n} questions.")
    print(f"Average time per question: {sum(seconds) / len(seconds):.1f} seconds")
    if FAILS:
        sys.exit(f"{len(FAILS)} of {CHECKED[0]} checks failed.")
    print(f"All {CHECKED[0]} checks passed.")


if __name__ == "__main__":
    main()
