#!/usr/bin/env python3
"""Test the real model on the three fictional company websites (needs Ollama running; the sites run locally).

    uv run evaluate.py
    uv run evaluate.py --model qwen3.5:9b   # try another model with the same checks

Written before the first run:
  * Scout: never legal pages, forbidden pages or other websites; always the about, careers and news pages
  * Researcher: real quotes, 5 known key facts per company, no people's names, no trap claim
  * Fact-checker: 6 wrong facts planted on purpose must be caught; the Researcher's facts mostly pass
  * Writer: a LinkedIn note (job, English), an email (job, German), an email (business, English)
"""
import argparse
import re
import sys
import time
from pathlib import Path

import crawler
import team

SAMPLES = Path(__file__).with_name("samples")
CASES = {  # company: profile, goal, channel, language, notes, key facts (any keyword), people's names, pages it must read
    "brightline": dict(profile="cv_ai_student.txt", goal="job", channel="linkedin", language="English", notes="",
                       key=[["42"], ["brightline ask"], ["junior ai engineer"], ["8 million"], ["example mobility"]],
                       people=["lena example", "tom sample"], must_read=["about.html", "careers.html", "series-a.html"]),
    "nordlicht": dict(profile="cv_ai_student.txt", goal="job", channel="email", language="Deutsch", notes="",
                      key=[["120"], ["werkstudent", "working student"], ["beispielstadt"], ["60"], ["landratsämtern", "district", "pilot"]],
                      people=["anna beispiel", "jan muster"], must_read=["ueber-uns.html", "karriere.html", "ausschreibung.html"]),
    "neckar-robotics": dict(profile="profile_agency.txt", goal="business", channel="email", language="English",
                            notes="We could help find customers for the NR-7 in a new industry.",
                            key=[["300"], ["nr 7"], ["ai automation engineer"], ["munich"], ["30 percent"]],
                            people=["paula placeholder"], must_read=["company.html", "jobs.html", "munich-office.html"]),
}
PLANTED = {  # wrong facts, each with the real words they twist: the Fact-checker must not call them supported
    "brightline": [("Brightline has 420 employees.", "Today 42 people work at Brightline", "about.html"),
                   ("Brightline believes AI will replace half of all engineers.", "Some say AI will replace half of all engineers.", "ai-hype.html")],
    "nordlicht": [("Nordlicht has 1,200 employees.", "Heute arbeiten 120 Menschen bei Nordlicht.", "ueber-uns.html"),
                  ("Nordlicht's AI assistant is used by all of its customers.", "KI-Assistent für die Sachbearbeitung", "loesungen.html")],
    "neckar-robotics": [("The NR-7 picks up to 6,000 items per hour.", "The NR-7 picking robot picks up to 600 items per hour.", "products.html"),
                        ("Neckar Robotics opened an office in Berlin.", "we opened our second office in Munich", "munich-office.html")],
}
NEVER_READ = ("impressum", "datenschutz", "privacy", "internal", "intern/")
MIN_QUOTES, MIN_KEY, MIN_CAUGHT, MAX_DOUBTED, MIN_CLEAN = 0.9, 12, 5, 0.25, 2

FAILS, CHECKED = [], [0]


def check(ok, label):
    CHECKED[0] += 1
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok:
        FAILS.append(label)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=team.MODEL)
    args = parser.parse_args()
    print(f"Model: {args.model}\n")
    sites = crawler.serve_samples()
    scout_ok, key_found, key_total, proposed, proved, people, trap = True, 0, 0, 0, 0, [], []
    caught, planted, doubted, checked_facts, clean, seconds = 0, 0, 0, 0, 0, []
    try:
        for slug, case in CASES.items():
            start = time.perf_counter()
            project = team.start(sites[slug], (SAMPLES / case["profile"]).read_text(), goal=case["goal"],
                                 channel=case["channel"], language=case["language"], notes=case["notes"])
            team.research(project, model=args.model, pause=0)
            read = [p.url for p in project.crawl.pages]
            ok = not any(w in u for u in read for w in NEVER_READ) and all(any(u.endswith(m) for u in read) for m in case["must_read"])
            scout_ok &= ok
            proposed += len(project.facts) + project.unverified
            proved += len(project.facts)
            facts_text = [team.words(f"{f.text} {f.quote}") for f in project.facts]
            found = [any(any(team.words(k) in t for k in keys) for t in facts_text) for keys in case["key"]]
            key_found, key_total = key_found + sum(found), key_total + len(found)
            people += [name for name in case["people"] if any(name in t for t in facts_text)]
            trap += [f.text for f in project.facts if "replace half" in team.words(f.text) and "disagree" not in team.words(f.text)
                     and " not " not in f" {team.words(f.text)} "]
            doubted += sum(f.verdict != "supported" for f in project.facts)
            checked_facts += len(project.facts)

            for text, quote, page_end in PLANTED[slug]:
                page = next(p for p in project.crawl.pages if p.url.endswith(page_end))
                wrong = team.Fact("x", "other", text, page.id, page.url, quote)
                verdict, reason = team.check_fact(project_client := team.ollama.Client(), args.model, wrong, page)
                planted += 1
                caught += verdict != "supported"
                print(f"     planted: {'✅ caught' if verdict != 'supported' else '❌ missed'} ({verdict}) {text}")

            team.draft(project, model=args.model)
            seconds.append(time.perf_counter() - start)
            clean += not project.latest.problems
            missing = [keys[0] for keys, f in zip(case["key"], found) if not f]
            print(f"  {slug}: read {len(read)} pages {'✅' if ok else '❌'} · {len(project.facts)} facts · key facts "
                  f"{sum(found)}/{len(found)}{' (missing ' + ', '.join(missing) + ')' if missing else ''} · "
                  f"{sum(f.verdict == 'supported' for f in project.facts)} supported · message "
                  f"{'all checks passed' if not project.latest.problems else 'still: ' + ' | '.join(project.latest.problems)} "
                  f"({len(project.versions)} version{'s' if len(project.versions) > 1 else ''}) · {seconds[-1]:.0f}s")
            print("     " + project.latest.draft.text(project.channel).replace("\n", "\n     ") + "\n")
    except team.AgentError as e:
        sys.exit(f"Error: {e}")

    print("Checks:")
    check(scout_ok, "the Scout read the about, careers and news pages, and never legal pages, forbidden pages or other websites")
    check(proved >= MIN_QUOTES * proposed, f"the Researcher's quotes are really on the page: {proved} of {proposed} (need {MIN_QUOTES:.0%})")
    check(key_found >= MIN_KEY, f"the Researcher found the key facts: {key_found} of {key_total} (need {MIN_KEY})")
    check(not people, "the Researcher wrote down no names of people" + (f": {people}" if people else ""))
    check(not trap, "the Researcher didn't report a claim the company disagrees with as its view" + (f": {trap}" if trap else ""))
    check(caught >= MIN_CAUGHT, f"the Fact-checker caught the wrong facts I planted: {caught} of {planted} (need {MIN_CAUGHT})")
    check(doubted <= MAX_DOUBTED * checked_facts,
          f"the Fact-checker doubted few of the Researcher's facts: {doubted} of {checked_facts} (at most {MAX_DOUBTED:.0%})")
    check(clean >= MIN_CLEAN, f"messages pass every Python check after the Writer's fixes: {clean} of {len(CASES)} (need {MIN_CLEAN})")

    print(f"\nAverage time per company (read, research, check, plan, write): {sum(seconds) / len(seconds):.0f} seconds")
    if FAILS:
        sys.exit(f"{len(FAILS)} of {CHECKED[0]} checks failed.")
    print(f"All {CHECKED[0]} checks passed.")


if __name__ == "__main__":
    main()
