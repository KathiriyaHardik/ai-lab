#!/usr/bin/env python3
"""Job-Fit Checker: compare a job ad with a CV using a local AI model (Ollama).

    uv run jobfit.py samples/job_ai_engineer_en.txt samples/cv_ai_student.txt

How a check works:
  1. the AI reads the job ad on its own and lists the requirements. It does not see the CV yet,
     so it can't quietly leave out the skills the candidate doesn't have
  2. the AI gets that list plus the CV and answers every requirement with yes / partly / no,
     copying the exact words from the CV as proof. The JSON shape has one slot per requirement,
     so none can be skipped
  3. Python looks for every proof in the CV; a claim whose proof isn't there counts as missing
  4. Python, not the AI, turns those answers into the Match %, so the score is explainable
     and the same answers always give the same number

Everything runs on this computer: the job ad and the CV are only sent to Ollama on localhost.
"""
import argparse
import io
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import ollama
from pydantic import BaseModel, Field, ValidationError, create_model

MODEL = os.environ.get("JOBFIT_MODEL", "gemma4:e4b")
CONTEXT_TOKENS = 16384  # prompt + job ad or CV + answer; Ollama's default window can cut long CVs off
MAX_INPUT_CHARS = 30000  # job ad + CV together (~8k tokens), so everything fits in CONTEXT_TOKENS
MIN_INPUT_CHARS = 100  # anything shorter is not a real job ad or CV
MAX_REQUIREMENTS = 20
# "Thinking" first made a check ~4x slower (45 s instead of 12) and twice ended in endless blank lines (see README).
THINK = os.environ.get("JOBFIT_THINK") == "1"
# Hard stop for one answer, so a stuck model can't run forever. A full answer needs ~800 tokens.
MAX_ANSWER_TOKENS = 2048

WEIGHT = {"must": 2, "nice": 1}  # a must-have counts twice as much as a nice-to-have
CREDIT = {"yes": 1.0, "partly": 0.5, "no": 0.0}
# same bands as the Match % column in 01-ai-job-tracker, so the number can be copied straight over
BANDS = [(90, "Excellent"), (75, "Strong"), (60, "Moderate"), (0, "Low")]

JOB_PROMPT = f"""You read job ads and list what the employer asks for.

Fill in the JSON fields like this:
- job_title: the job title from the ad.
- language: the language the ad is written in, for example "English" or "German".
- requirements: every skill, tool, qualification, language and kind of experience the ad asks for,
  from the required list AND from the nice-to-have list. One requirement per item.
  At most {MAX_REQUIREMENTS} items, most important first. Leave out benefits and the company's offer.
- skill: a short name for the requirement, 1 to 6 words.
- details: what the ad says about it in the ad's own words, at most 20 words, including any
  alternatives the ad accepts (for example "Computer Science, Information Systems or a similar field").
- importance: "must" if the ad requires it. "nice" if the ad calls it a plus, preferred, ideal,
  nice to have, or similar (in German for example "wünschenswert", "von Vorteil", "idealerweise")."""

CV_PROMPT = """You compare a CV with a numbered list of job requirements.

For every requirement (r1, r2, ...) fill in:
- status: "yes" if the CV clearly shows it, "partly" if the CV shows something related but weaker,
  "no" if the CV does not show it.
- evidence: the exact words copied from the CV that prove the status, at most 15 words.
  Copy them character for character, do not rephrase. Leave it empty when status is "no".

Then write why_me: one or two sentences the candidate could put in a cover letter for this job,
in the first person ("I ..."), written in {language}. Use only facts that are in the CV.

Never invent anything that is not written in the CV."""


class JobFitError(Exception):
    """A problem the user can fix (bad input, Ollama not running, model missing...)."""


# ----------------------------------------------------------------------------- the AI's answers
class JobRequirement(BaseModel):
    skill: str = Field(description="Short name, 1-6 words")
    details: str = Field(description="What the ad says about it, in the ad's words")
    importance: Literal["must", "nice"]


class JobAd(BaseModel):  # step 1: the job ad alone
    job_title: str
    language: str
    requirements: list[JobRequirement] = Field(min_length=1)


class Assessment(BaseModel):  # step 2: one per requirement
    status: Literal["yes", "partly", "no"]
    evidence: str = Field(description="Exact words copied from the CV, or empty")


def cv_answer_model(count):
    """JSON shape for step 2: required slots r1..rN, one per requirement, then why_me."""
    slots = {f"r{i}": (Assessment, ...) for i in range(1, count + 1)}
    return create_model("CVAnswer", **slots, why_me=(str, ...))


# ----------------------------------------------------------------------------- the checked result
@dataclass
class Check:
    skill: str
    importance: str  # must | nice
    status: str  # what the AI said: yes | partly | no
    evidence: str
    verified: bool  # the evidence was found in the CV (always True for "no")

    @property
    def counted(self):
        """The status used for the score: a claim without proof counts as "no"."""
        return self.status if self.verified else "no"


@dataclass
class FitResult:
    job_title: str
    match_percent: int
    band: str
    checks: list
    why_me: str

    def _with(self, status):
        # must-haves first, so the most important gaps lead every list
        return sorted((c for c in self.checks if c.counted == status), key=lambda c: c.importance != "must")

    @property
    def have(self):
        return self._with("yes")

    @property
    def partly(self):
        return self._with("partly")

    @property
    def missing(self):
        return self._with("no")

    @property
    def unverified(self):
        return [c for c in self.checks if not c.verified]


# ----------------------------------------------------------------------------- pure logic (no AI)
def words(text):
    """Lower-case words only, so line breaks, bullets, quotes and dashes don't block a match."""
    text = unicodedata.normalize("NFKC", text).lower()
    return " ".join(re.findall(r"\w+", text))


def evidence_in_cv(evidence, cv):
    """True if every part of the evidence (split at "...") appears word for word in the CV."""
    parts = [words(p) for p in re.split(r"\.\.\.|…", evidence)]
    parts = [p for p in parts if p]
    haystack = f" {words(cv)} "
    return bool(parts) and all(f" {p} " in haystack for p in parts)


def unique_requirements(requirements):
    """Drop empty and repeated requirements (the AI sometimes lists a skill twice) and cap the list."""
    kept, seen = [], set()
    for r in requirements:
        key = words(r.skill)
        if key and key not in seen:
            seen.add(key)
            kept.append(r)
    return kept[:MAX_REQUIREMENTS]


def build_checks(requirements, assessments, cv):
    checks = []
    for r, a in zip(requirements, assessments, strict=True):
        evidence = "" if a.status == "no" else a.evidence.strip().strip("\"'“”„")  # the AI sometimes adds quotes
        verified = a.status == "no" or evidence_in_cv(evidence, cv)
        checks.append(Check(r.skill.strip(), r.importance, a.status, evidence, verified))
    return checks


def match_percent(checks):
    total = sum(WEIGHT[c.importance] for c in checks)
    earned = sum(WEIGHT[c.importance] * CREDIT[c.counted] for c in checks)
    return int(100 * earned / total + 0.5)


def band(percent):
    return next(name for floor, name in BANDS if percent >= floor)


def check_inputs(job_ad, cv):
    job_ad, cv = (job_ad or "").strip(), (cv or "").strip()
    if len(job_ad) < MIN_INPUT_CHARS:
        raise JobFitError("The job ad is empty or very short. Paste the full job ad.")
    if len(cv) < MIN_INPUT_CHARS:
        raise JobFitError("The CV is empty or very short. Upload your CV or paste it as text.")
    if len(job_ad) + len(cv) > MAX_INPUT_CHARS:
        raise JobFitError(
            f"The job ad and CV are too long together ({len(job_ad) + len(cv):,} characters, "
            f"limit {MAX_INPUT_CHARS:,}). Remove parts that don't matter, like the company's history."
        )
    return job_ad, cv


def extract_text(data, filename):
    """Text of an uploaded CV: PDF, or a plain .txt / .md file."""
    if filename.lower().endswith(".pdf"):
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(data))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as e:  # pypdf raises many different errors for broken files
            raise JobFitError(f"Could not read this PDF ({e}). Paste your CV as text instead.") from e
        if len(text.strip()) < MIN_INPUT_CHARS:
            raise JobFitError(
                "This PDF has (almost) no text in it. It is probably a scanned image. Paste your CV as text instead."
            )
        return text
    return data.decode("utf-8", errors="replace")


# ----------------------------------------------------------------------------- talking to the AI
def ask(client, model, think, system, user, shape):
    """One AI call whose reply is forced into the JSON shape of the pydantic model `shape`."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    problem = None
    # Temperature 0 gives the same answer every time. If that answer is broken (cut off after endless
    # blank lines, for example), the same request would break the same way, so the retry adds a little randomness.
    for temperature in (0, 0.3):
        try:
            response = client.chat(
                model=model,
                messages=messages,
                format=shape.model_json_schema(),  # Ollama forces the reply into this JSON shape
                think=think,
                options={
                    "temperature": temperature,
                    "num_ctx": CONTEXT_TOKENS,
                    "num_predict": MAX_ANSWER_TOKENS * (3 if think else 1),  # thinking needs room too
                },
            )
        except ConnectionError as e:
            raise JobFitError("Ollama is not running. Open the Ollama app, then try again.") from e
        except ollama.ResponseError as e:
            if e.status_code == 404:
                raise JobFitError(f"The AI model is not downloaded yet. Run:  ollama pull {model}") from e
            if "structured output" in str(e.error):
                raise JobFitError(
                    "This Ollama install can't force the answer into JSON. Install the official app "
                    "from ollama.com (Homebrew: brew install --cask ollama-app)."
                ) from e
            raise JobFitError(f"Ollama returned an error: {e.error}") from e
        try:
            return shape.model_validate_json(response.message.content)
        except ValidationError as e:
            problem = e
    raise JobFitError(f"The AI's answer was not in the expected format, twice. Try again.\n\n{problem}")


def check_fit(job_ad, cv, model=MODEL, client=None, think=THINK):
    job_ad, cv = check_inputs(job_ad, cv)
    client = client or ollama.Client()

    job = ask(client, model, think, JOB_PROMPT, f"JOB AD:\n<<<\n{job_ad}\n>>>", JobAd)
    requirements = unique_requirements(job.requirements)
    if not requirements:
        raise JobFitError("The AI found no requirements in the job ad. Check that you pasted the whole ad.")

    numbered = "\n".join(
        f"r{i}: {r.skill.strip()}: {r.details.strip()} ({'must-have' if r.importance == 'must' else 'nice-to-have'})"
        for i, r in enumerate(requirements, 1)
    )
    language = job.language.strip() or "the same language as the job ad"
    answer = ask(
        client, model, think, CV_PROMPT.format(language=language),
        f"JOB: {job.job_title.strip()}\n\nREQUIREMENTS:\n{numbered}\n\nCV:\n<<<\n{cv}\n>>>",
        cv_answer_model(len(requirements)),
    )

    assessments = [getattr(answer, f"r{i}") for i in range(1, len(requirements) + 1)]
    checks = build_checks(requirements, assessments, cv)
    percent = match_percent(checks)
    return FitResult(job.job_title.strip(), percent, band(percent), checks, answer.why_me.strip())


# ----------------------------------------------------------------------------- command line
def read_file(path):
    path = Path(path)
    return extract_text(path.read_bytes(), path.name)


def print_result(r):
    print(f"\n{r.job_title}\nMatch: {r.match_percent}% ({r.band})\n")
    for title, items in (("You have", r.have), ("Partly", r.partly), ("Missing", r.missing)):
        if items:
            print(f"{title}:")
            for c in items:
                note = "" if c.verified else "   (AI claimed it, but the proof is not in the CV)"
                print(f"  - {c.skill} [{c.importance}]{note}")
            print()
    print(f"Why me?\n  {r.why_me}\n")


def main():
    parser = argparse.ArgumentParser(description="Compare a job ad with a CV using a local AI model.")
    parser.add_argument("job_ad", help="job ad as a .txt file")
    parser.add_argument("cv", help="CV as a .pdf, .txt or .md file")
    parser.add_argument("--model", default=MODEL, help=f"Ollama model (default: {MODEL})")
    args = parser.parse_args()
    try:
        print_result(check_fit(read_file(args.job_ad), read_file(args.cv), args.model))
    except JobFitError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
