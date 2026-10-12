#!/usr/bin/env python3
"""Test the real model on the sample letters (needs Ollama running).

    uv run evaluate.py
    uv run evaluate.py --model qwen3.5:9b   # try another model with the same checks

11 runs: each of the 5 fictional letters as a clean scan and as a phone photo, plus letter 1
as a PDF with text. The expected answers were written down before the first run.
"""
import argparse
import difflib
import re
import sys
import time
from pathlib import Path

import pypdfium2 as pdfium

import letters
import make_samples

SAMPLES = Path(__file__).with_name("samples")
D = lambda s: letters.dt.date(int(s[6:]), int(s[3:5]), int(s[:2]))  # noqa: E731  "09.11.2026" -> date

# dates that must be deadlines ("date") or safe dates of time spans ("safe"); dates that must NOT be deadlines
EXPECTED = {
    "letter1_finanzamt": dict(letter_date="06.10.2026", due=[("date", "09.11.2026")], objection="06.11.2026",
                              not_due=["06.10.2026"], amount="1234.56", reference=["12/345/67890"]),
    "letter2_stadtwerke": dict(letter_date="01.10.2026", due=[("date", "20.10.2026")],
                               not_due=["01.10.2026", "28.08.2026"], amount="91.40", reference=["4711-0815", "R-2026-08812"]),
    "letter3_auslaenderbehoerde": dict(letter_date="29.09.2026", due=[("date", "03.12.2026")],
                                       not_due=["29.09.2026"], amount="100.00", reference=["AB-2026-31415"]),
    "letter4_krankenkasse": dict(letter_date="05.10.2026", due=[("safe", "19.10.2026")],
                                 not_due=["05.10.2026"], amount=None, reference=["X123456789"]),
    "letter5_gesundheitskarte": dict(letter_date="07.10.2026", due=[], no_deadlines=True,
                                     not_due=["07.10.2026"], amount=None, reference=None),
}
MIN_SCAN_READ, MIN_PHOTO_READ, MIN_AMOUNT, MIN_DATE, MIN_REFERENCE, MIN_LANGUAGE = 0.95, 0.90, 0.9, 0.9, 0.8, 0.9
GERMAN = {"der", "die", "das", "und", "sie", "ist", "nicht", "mit", "ihre", "ihr", "bitte", "wir", "zu", "den", "für"}

FAILS, CHECKED = [], [0]


def check(ok, label):
    CHECKED[0] += 1
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok:
        FAILS.append(label)


def truth(key):
    letter = next(item for item in make_samples.LETTERS if item["key"] == key)
    return pdfium.PdfDocument(make_samples.letter_pdf(letter))[0].get_textpage().get_text_range()


def reading_score(read, true):
    return difflib.SequenceMatcher(None, letters.words(true).split(), letters.words(read).split()).ratio()


def grade(r, e):
    due_dates = {d.date for d in r.deadlines if d.date}
    safe_dates = {d.safe_date for d in r.deadlines if d.safe_date}
    g = {
        "deadlines": all(D(v) in (due_dates if kind == "date" else safe_dates) for kind, v in e["due"]),
        "invented": any(D(v) in due_dates for v in e["not_due"]) or (e.get("no_deadlines") and bool(r.deadlines)),
        "countdown_before_event": any(d.before_event and d.due for d in r.deadlines),
        "amount": (r.amount is None) if e["amount"] is None else (r.amount is not None and str(r.amount.euros) == e["amount"]),
        "letter_date": r.letter_date == D(e["letter_date"]),
        "reference": None if e["reference"] is None else any(ref in r.reference for ref in e["reference"]),
        "objection": None,
    }
    if e.get("objection"):
        g["objection"] = any(d.kind == "objection" and d.safe_date == D(e["objection"]) for d in r.deadlines)
    explanation = " ".join([r.summary, *r.actions, *(d.what for d in r.deadlines), r.if_ignored])
    tokens = re.findall(r"\w+", explanation.lower())
    g["english"] = sum(t in GERMAN for t in tokens) < 0.1 * max(len(tokens), 1)
    return g


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=letters.MODEL)
    args = parser.parse_args()
    print(f"Model: {args.model}\n")

    runs = []
    cases = [(key, kind) for key in EXPECTED for kind in ("scan", "photo")] + [("letter1_finanzamt", "pdf")]
    for key, kind in cases:
        name = f"{key}.pdf" if kind == "pdf" else f"{key}_{kind}.{'png' if kind == 'scan' else 'jpg'}"
        start = time.perf_counter()
        try:
            r = letters.check_letter([(name, (SAMPLES / name).read_bytes())], model=args.model)
        except letters.LetterError as e:
            sys.exit(f"Error: {e}")
        seconds = time.perf_counter() - start
        score = reading_score("\n".join(p.text for p in r.pages), truth(key))
        g = grade(r, EXPECTED[key])
        runs.append((key, kind, score, g, seconds))
        flag = lambda ok: "–" if ok is None else "✅" if ok else "❌"  # noqa: E731
        print(f"  {name:<38} read {score:6.1%} · deadlines {flag(g['deadlines'] and not g['invented'])} · "
              f"amount {flag(g['amount'])} · date {flag(g['letter_date'])} · ref {flag(g['reference'])} · {seconds:4.1f}s")
        if not (g["deadlines"] and not g["invented"] and g["amount"]):
            print(f"        deadlines: {[(d.kind, d.what, d.due, d.period) for d in r.deadlines]} · amount: {r.amount and r.amount.text}")

    n = len(runs)
    share = lambda key: sum(bool(g[key]) for *_, g, _ in runs if g[key] is not None) / sum(g[key] is not None for *_, g, _ in runs)  # noqa: E731
    scans = [s for _, kind, s, _, _ in runs if kind == "scan"]
    photos = [s for _, kind, s, _, _ in runs if kind == "photo"]

    print("\nChecks:")
    check(sum(scans) / len(scans) >= MIN_SCAN_READ, f"scans are read correctly: {sum(scans) / len(scans):.1%} of words (need {MIN_SCAN_READ:.0%})")
    check(sum(photos) / len(photos) >= MIN_PHOTO_READ, f"phone photos are read correctly: {sum(photos) / len(photos):.1%} of words (need {MIN_PHOTO_READ:.0%})")
    check(share("deadlines") == 1, f"every deadline is found with the right date: {share('deadlines'):.0%} of runs (need all)")
    check(share("objection") == 1, f"the objection deadline is found, with its safe date: {share('objection'):.0%} of runs (need all)")
    check(not any(g["invented"] for *_, g, _ in runs), "no invented deadlines (letter dates, old invoice dates, the info-only letter)")
    check(not any(g["countdown_before_event"] for *_, g, _ in runs), "no countdown for \"3 working days before\" deadlines")
    check(share("amount") >= MIN_AMOUNT, f"the right amount to pay, or none: {share('amount'):.0%} of runs (need {MIN_AMOUNT:.0%})")
    check(share("letter_date") >= MIN_DATE, f"the right letter date: {share('letter_date'):.0%} of runs (need {MIN_DATE:.0%})")
    check(share("reference") >= MIN_REFERENCE, f"the right reference number: {share('reference'):.0%} of runs (need {MIN_REFERENCE:.0%})")
    check(share("english") >= MIN_LANGUAGE, f"explained in English, not copied German: {share('english'):.0%} of runs (need {MIN_LANGUAGE:.0%})")

    print(f"\nAverage time per letter: {sum(r[4] for r in runs) / n:.1f} seconds")
    if FAILS:
        sys.exit(f"{len(FAILS)} of {CHECKED[0]} checks failed.")
    print(f"All {CHECKED[0]} checks passed.")


if __name__ == "__main__":
    main()
