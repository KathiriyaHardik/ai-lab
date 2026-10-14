#!/usr/bin/env python3
"""Test the real model on the sample job ads and CVs (needs Ollama running).

    uv run evaluate.py
    uv run evaluate.py --model qwen3.5:9b   # try another model with the same checks

5 applications (English and German, cover letters and recruiter messages, a strong and a weak fit),
each planned, written, checked and fixed by the agent, with the plan approved as suggested.
Then one round of feedback, like a person would give it. The checks were written before the first run.
"""
import argparse
import re
import sys
import time
from pathlib import Path

import agent

SAMPLES = Path(__file__).with_name("samples")
CASES = [  # name, job ad, CV, kind, language
    ("strong · cover letter · EN", "job_ai_engineer_en.txt", "cv_ai_student.txt", "cover_letter", "English"),
    ("strong · cover letter · DE", "job_werkstudent_genai_de.txt", "cv_ai_student.txt", "cover_letter", "Deutsch"),
    ("weak fit · cover letter · EN", "job_ai_engineer_en.txt", "cv_data_analyst.txt", "cover_letter", "English"),
    ("strong · message · EN", "job_ai_engineer_en.txt", "cv_ai_student.txt", "message", "English"),
    ("strong · message · DE", "job_werkstudent_genai_de.txt", "cv_ai_student.txt", "message", "Deutsch"),
]
FEEDBACK = "Make it shorter, at most 250 words, and mention that I can start on 1 March 2027."
# the data analyst's CV has none of these, so the letter may mention them only as something to learn or about the job
NOT_IN_WEAK_CV = ("llm", "llms", "rag", "langchain", "llamaindex", "docker", "chatbot", "vector", "openai", "retrieval")
HONEST_MARKERS = ("learn", "eager", "excited to", "keen to", "not yet", "new to", "look forward", "your", "you ",
                  "the role", "this role", "the team", "interest", "want to", "would like to", "grow")
MIN_PROOF, MIN_CLEAN = 0.9, 0.8

FAILS, CHECKED = [], [0]


def check(ok, label):
    CHECKED[0] += 1
    print(f"  {'✅' if ok else '❌'} {label}")
    if not ok:
        FAILS.append(label)


def dishonest_sentences(text):
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [s for s in sentences if set(agent.words(s).split()) & set(NOT_IN_WEAK_CV)
            and not any(m in s.lower() for m in HONEST_MARKERS)]


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=agent.MODEL)
    args = parser.parse_args()
    print(f"Model: {args.model}\n")

    apps, seconds = {}, []
    try:
        for name, job, cv, kind, language in CASES:
            start = time.perf_counter()
            app = agent.start((SAMPLES / job).read_text(), (SAMPLES / cv).read_text(), kind, language)
            agent.make_plan(app, model=args.model)
            agent.write_draft(app, model=args.model)
            seconds.append(time.perf_counter() - start)
            apps[name] = app
            v = app.latest
            words = len(re.findall(r"\w+", v.draft.body))
            print(f"  {name:<30} proof {sum(s.verified for s in app.strengths)}/{len(app.strengths)} · "
                  f"{len(app.versions)} version(s) · {words} words · "
                  f"{'all checks passed' if not v.problems else 'still: ' + ' | '.join(v.problems)} · {seconds[-1]:.0f}s")

        first = apps[CASES[0][0]]
        before = len(re.findall(r"\w+", first.latest.draft.body))
        start = time.perf_counter()
        agent.revise(first, FEEDBACK, model=args.model)
        seconds.append(time.perf_counter() - start)
        after = len(re.findall(r"\w+", first.latest.draft.body))
        print(f"  {'feedback on the first letter':<30} {before} → {after} words · "
              f"{'all checks passed' if not first.latest.problems else 'still: ' + ' | '.join(first.latest.problems)} · "
              f"{seconds[-1]:.0f}s")
    except agent.AgentError as e:
        sys.exit(f"Error: {e}")

    finals = [a.latest for a in apps.values()]
    strengths = [s for a in apps.values() for s in a.strengths]
    weak = apps["weak fit · cover letter · EN"].latest.draft.body
    lying = dishonest_sentences(weak)
    needed_fixing = sum(any(v.why.startswith("fixed") for v in a.versions) for a in apps.values())
    text = first.latest.draft.text

    print("\nChecks:")
    proved = sum(s.verified for s in strengths)
    check(proved >= MIN_PROOF * len(strengths), f"the plan's strengths have real proof in the CV: {proved} of {len(strengths)} (need {MIN_PROOF:.0%})")
    clean = sum(not v.problems for v in finals)
    check(clean >= MIN_CLEAN * len(finals), f"drafts pass every Python check after the agent's own fixing: {clean} of {len(finals)} (need {MIN_CLEAN:.0%})")
    check(not any(any("numbers are not in" in p for p in v.problems) for v in finals + [first.latest]),
          "no invented numbers in any final draft")
    check(not any(any(p.startswith("Write the whole text in") for p in v.problems) for v in finals),
          "every draft is in the language you chose")
    check(not lying, "the weak-fit letter doesn't claim skills the CV doesn't have"
          + ("" if not lying else ":\n      " + "\n      ".join(lying)))
    check("march 2027" in agent.words(text) or "1 3 2027" in agent.words(text), "your feedback is applied: the start date is in the letter")
    check(after <= 250 and not first.latest.problems, f"your feedback is applied: at most 250 words ({after}) and every check passes")
    check(after < before, f"your feedback is applied: shorter than before ({before} → {after} words)")

    print(f"\n{needed_fixing} of {len(apps)} first drafts needed the agent's fix loop.")
    print(f"Average time per application (plan + write + check + fix): {sum(seconds[:-1]) / len(apps):.0f} seconds")
    if FAILS:
        sys.exit(f"{len(FAILS)} of {CHECKED[0]} checks failed.")
    print(f"All {CHECKED[0]} checks passed.")


if __name__ == "__main__":
    main()
