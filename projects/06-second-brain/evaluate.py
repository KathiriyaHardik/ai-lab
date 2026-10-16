#!/usr/bin/env python3
"""Test the real models on the sample notes (needs Ollama running). Uses a fresh, temporary database.

    uv run evaluate.py
    uv run evaluate.py --model qwen3.5:9b --embed-model bge-m3   # compare other models

Written before the first run:
  * 10 questions answered by one note, 5 that need two notes, 3 the notes don't answer
  * the two-note questions are asked twice: with the agent's search plan, and with one plain search
  * connections for 6 notes, including 4 links I left out of the notes on purpose
"""
import argparse
import re
import sys
import tempfile
import time

import brain

SINGLE = [  # question, the note that answers it, words the answer must contain (any one)
    ("How often should I review my flashcards?", "Spaced repetition.md", ["14 days", "7 days"]),
    ("How many new German words do I learn every day?", "German vocabulary routine.md", ["20"]),
    ("What is the STAR method?", "Interview preparation.md", ["Situation"]),
    ("How long is a sprint in Scrum?", "Scrum basics.md", ["4 weeks"]),
    ("How do I calculate a risk?", "Risk management.md", ["probability"]),
    ("How soon after moving do I have to register my address?", "Anmeldung checklist.md", ["14 days"]),
    ("When does the tax ID arrive?", "Tax ID.md", ["few weeks"]),
    ("How long is one Pomodoro?", "Pomodoro technique.md", ["25 minutes"]),
    ("Which values of k are common in cross-validation?", "Cross-validation.md", ["5", "10"]),
    ("How long should my CV be?", "CV feedback from the career service.md", ["2 pages", "two pages"]),
]
MULTI = [  # question, the two notes needed, words the answer must contain (any one)
    ("How can I use what I learned about habits to keep up my German vocabulary?",
     ["Book notes - Atomic Habits.md", "German vocabulary routine.md"], ["stack", "Anki"]),
    ("Which project management method could I use to keep track of my job applications?",
     ["Kanban board.md", "Job search plan 2026.md"], ["Kanban"]),
    ("What do I have to do before my tax ID arrives, and what do I need to bring?",
     ["Tax ID.md", "Anmeldung checklist.md"], ["Wohnungsgeberbestätigung", "passport"]),
    ("How should I test a RAG chatbot?", ["RAG basics.md", "Evaluating LLM answers.md"],
     ["several times", "expected answers", "sources", "quote"]),
    ("How should I prepare for an exam, and how often should I review the material?",
     ["Exam preparation strategy.md", "Spaced repetition.md"], ["active recall", "practice exams", "1 day"]),
]
NOT_IN_NOTES = ["What is the capital of Canada?", "What did I write about Kubernetes?",
                "Which salary should I ask for in my first job?"]
RELATED = {  # a note, and the notes a good second brain should put close to it (one in the top 3 is enough)
    "Spaced repetition": {"German vocabulary routine", "Exam preparation strategy", "Book notes: Atomic Habits"},
    "Kanban board": {"Scrum basics", "Job search plan 2026"},
    "Tax ID": {"Anmeldung checklist", "Idea: German letter helper"},
    "Precision and recall": {"Evaluating LLM answers", "Cross-validation", "Overfitting"},
    "Book notes: Atomic Habits": {"German vocabulary routine", "Pomodoro technique", "Weekly review", "Spaced repetition"},
    "Interview preparation": {"Job search plan 2026", "Networking notes", "CV feedback from the career service"},
}
HIDDEN_LINKS = [("Spaced repetition", "Exam preparation strategy"), ("Spaced repetition", "Book notes: Atomic Habits"),
                ("Tax ID", "Idea: German letter helper"), ("Kanban board", "Job search plan 2026")]
MIN_SEARCH, MIN_BOTH, MIN_FACT, MIN_CITED, MIN_PROOF, MIN_RELATED, MIN_LINK_PROOF, MIN_HIDDEN = .9, .8, .8, .8, .9, 5, .8, 3

FAILS, CHECKED = [], [0]


def check(ok, label):
    CHECKED[0] += 1
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok:
        FAILS.append(label)


def contains(text, options):
    return any(brain.words(o) in brain.words(text) for o in options)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=brain.CHAT_MODEL)
    parser.add_argument("--embed-model", default=brain.EMBED_MODEL)
    args = parser.parse_args()
    print(f"Chat model: {args.model} · embedding model: {args.embed_model}\n")

    with tempfile.TemporaryDirectory() as db:
        try:
            mind = brain.Brain(brain.SAMPLE_VAULT, db_dir=db, embed_model=args.embed_model)
            start = time.perf_counter()
            mind.sync()
            print(f"Synced {len(mind.notes)} notes in {time.perf_counter() - start:.1f} s\n\nQuestions:")
            rows, seconds, quotes = [], [], 0
            for question, notes, facts in [(q, [n], f) for q, n, f in SINGLE] + MULTI + [(q, [], None) for q in NOT_IN_NOTES]:
                start = time.perf_counter()
                a = brain.ask(mind, question, args.model)
                seconds.append(time.perf_counter() - start)
                single = brain.ask(mind, question, args.model, plan=False) if len(notes) == 2 else None
                found = {h.path for h in a.hits}
                cited = {s.path for s in a.sources}
                quotes += len(a.sources) + a.unverified
                rows.append(dict(notes=notes, a=a, searched=all(n in found for n in notes),
                                 searched_single=single and all(n in {h.path for h in single.hits} for n in notes),
                                 fact=bool(notes) and a.found and contains(a.text, facts), cited=bool(set(notes) & cited)))
                r = rows[-1]
                mark = lambda ok: "✅" if ok else "❌"  # noqa: E731
                if notes:
                    extra = "" if single is None else f" (one plain search: {mark(r['searched_single'])})"
                    print(f"  {mark(r['searched'])} search{extra}  {mark(r['fact'])} fact  {mark(r['cited'])} cited  "
                          f"{seconds[-1]:4.1f}s  {question}")
                    if not (r["fact"] and r["cited"]):
                        print(f"        answer: {a.text or '(not found)'}\n        cited: {sorted(cited)}")
                else:
                    print(f"  {mark(not a.found)} says \"not found\"  {seconds[-1]:4.1f}s  {question}")

            print("\nConnections:")
            related_ok, link_claims, link_proved, hidden = 0, 0, 0, set()
            for title, expected in RELATED.items():
                top = [n.title for n, _ in mind.related(title, 3)]
                related_ok += bool(set(top) & expected)
                note, found = brain.connections(mind, title, k=4, model=args.model)
                for c in found:
                    if c.connected:
                        link_claims += 1
                        link_proved += c.checked
                    if c.connected and c.checked and not c.already_linked:
                        hidden.add((title, c.note.title))
                new = [c.note.title for c in found if c.connected and not c.already_linked]
                print(f"  {'✅' if set(top) & expected else '❌'} {title}: top 3 {top} · new connections {new}")
        except brain.BrainError as e:
            sys.exit(f"Error: {e}")

    answerable = [r for r in rows if r["notes"]]
    single_rows = [r for r in answerable if len(r["notes"]) == 1]
    multi_rows = [r for r in answerable if len(r["notes"]) == 2]
    share = lambda rs, key: sum(bool(r[key]) for r in rs) / len(rs)  # noqa: E731
    proved = sum(len(r["a"].sources) for r in rows)
    found_hidden = [pair for pair in HIDDEN_LINKS if pair in hidden]

    print("\nChecks:")
    check(share(single_rows, "searched") >= MIN_SEARCH,
          f"one-note questions: the right note is found: {share(single_rows, 'searched'):.0%} (need {MIN_SEARCH:.0%})")
    check(share(multi_rows, "searched") >= MIN_BOTH,
          f"two-note questions: both notes are found with the agent's plan: {share(multi_rows, 'searched'):.0%} "
          f"(need {MIN_BOTH:.0%}; one plain search: {share(multi_rows, 'searched_single'):.0%})")
    check(share(answerable, "fact") >= MIN_FACT, f"the answer contains the fact: {share(answerable, 'fact'):.0%} (need {MIN_FACT:.0%})")
    check(share(answerable, "cited") >= MIN_CITED, f"the answer quotes the right note: {share(answerable, 'cited'):.0%} (need {MIN_CITED:.0%})")
    check(all(not r["a"].found for r in rows if not r["notes"]),
          f"says \"not found\" when the notes don't answer: {sum(not r['a'].found for r in rows if not r['notes'])} of {len(NOT_IN_NOTES)}")
    check(proved >= MIN_PROOF * quotes, f"quotes really are in the notes: {proved} of {quotes} (need {MIN_PROOF:.0%})")
    check(related_ok >= MIN_RELATED, f"related notes make sense (an expected note in the top 3): {related_ok} of {len(RELATED)} (need {MIN_RELATED})")
    check(link_claims and link_proved >= MIN_LINK_PROOF * link_claims,
          f"connections come with real quotes from both notes: {link_proved} of {link_claims} (need {MIN_LINK_PROOF:.0%})")
    check(len(found_hidden) >= MIN_HIDDEN, f"links I left out on purpose are discovered: {len(found_hidden)} of {len(HIDDEN_LINKS)} "
          f"(need {MIN_HIDDEN}) {[b for _, b in found_hidden]}")

    print(f"\nAverage time per question (plan + search + answer): {sum(seconds) / len(seconds):.1f} seconds")
    if FAILS:
        sys.exit(f"{len(FAILS)} of {CHECKED[0]} checks failed.")
    print(f"All {CHECKED[0]} checks passed.")


if __name__ == "__main__":
    main()
