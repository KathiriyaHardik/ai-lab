#!/usr/bin/env python3
"""Company Research Agent: a team of AI agents researches a company and drafts a personal message.

    uv run team.py https://www.example-company.com samples/cv_ai_student.txt
    uv run team.py https://... my-cv.pdf --goal business --channel email --language Deutsch --contact "Frau Beispiel"

The team (each agent is the same local model with its own job, instructions and answer shape):
  SCOUT         reads up to 8 public pages of the company's own website (crawler.py)
  RESEARCHER    writes down facts about the company, each with its page and an exact quote
  FACT-CHECKER  a second opinion on every single fact: does the page really say that?
  YOU           untick the facts you don't want used
  STRATEGIST    picks 2-3 hooks: a company fact plus your matching experience, with proof from your profile
  WRITER        writes a LinkedIn note or an email; Python checks it, the writer fixes problems (up to 3 rounds)
  YOU           approve it, or say what should change
Python checks every quote and every number. Nothing is ever sent: you copy the message and send it yourself.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import ollama
from pydantic import BaseModel, Field, ValidationError, create_model

import crawler

MODEL = os.environ.get("TEAM_MODEL", "gemma4:e4b")
MESSAGES_DIR = Path(__file__).resolve().parent / "my-messages"  # approved messages (git-ignored)
CONTEXT_TOKENS = 16384
MAX_ANSWER_TOKENS = 2048
PAGE_CHARS_FOR_AI = 3000  # per page, so eight pages fit the model's window
MAX_FACTS = 15
MAX_HOOKS = 3
MAX_FIX_ROUNDS = 3
STUCK_WHITESPACE = 200  # this much blank space in a row means the model is stuck (see project 05)
TEMPERATURES = (0, 0.3, 0.7)
CATEGORIES = ("what they do", "products", "customers", "size and places", "news", "jobs", "values", "other")

GOALS = {
    "job": "ask about working there, for example an open position that fits",
    "networking": "start a conversation and learn about their work, without asking for a job directly",
    "business": "offer the sender's services in a way that is clearly useful for this company",
}
CHANNELS = {  # what Python accepts; the writer is asked for a bit less, because it overshoots
    "linkedin": dict(name="LinkedIn connection note", unit="characters", limits=(100, 300), aim=(150, 250),
                     shape="one short paragraph, no subject, no closing and no name (LinkedIn shows the name)"),
    "email": dict(name="short email", unit="words", limits=(60, 160), aim=(80, 130),
                  shape="2 or 3 short paragraphs and a short subject line; no closing and no name, they are added automatically"),
}
LANGUAGES = {
    "English": dict(greetings=("dear", "hello", "hi"), sign_off="Best regards",
                    greeting="'Hello <name>,' if a contact person is given, otherwise 'Hello,'",
                    style="Plain, warm, professional English."),
    "Deutsch": dict(greetings=("sehr geehrte", "guten tag", "hallo"), sign_off="Viele Grüße",
                    greeting="'Guten Tag Frau <Name>,' / 'Guten Tag Herr <Name>,' if a contact person is given, otherwise 'Guten Tag,'",
                    style="Natural German. Address the reader formally with 'Sie'."),
}
CLICHES = ("i hope this message finds you well", "i hope this email finds you well", "i came across your profile",
           "ich hoffe, es geht ihnen gut", "ich bin auf ihr profil gestoßen")
STOP = {"English": {"the", "and", "i", "my", "to", "of", "with", "for", "in", "is", "a", "your", "you"},
        "Deutsch": {"der", "die", "das", "und", "ich", "mit", "für", "in", "ist", "sie", "ihre", "meine", "zu"}}
LEGAL_FORMS = r"\b(gmbh|mbh|ag|se|kg|ohg|ug|e\s?v|inc|ltd|llc|co)\b\.?|&"

RESEARCH_PROMPT = """You research a company for a person who wants to contact it. You get pages of the company's
own website, each with an id (p1, p2, ...).

- company: the company's name as written on the website.
- facts: up to 15 useful facts about the company: what it does, its products, customers, size and places,
  news, open jobs, what it cares about. Most useful first. For each fact:
  category: one of the given categories. text: the fact in one short English sentence.
  page: the id of the page it is from. quote: the exact words from that page that show the fact, at most
  25 words, copied character for character.

Only facts the company states about itself. Not claims it quotes from others or disagrees with.
Skip names and personal details of individual people."""

CHECK_PROMPT = """You check one fact about a company against the web page it comes from.

- reason: one short sentence comparing the fact with what the page says.
- said_by: who says it on the page: "the company" itself, or "someone else" (a quote, a rumour, a claim the
  company only repeats or disagrees with), or "unclear".
- verdict: "supported" if the page clearly says what the fact says. "partly" if the page says something close
  but the fact changes or adds something: another number, a stronger claim, a plan described as done.
  "unsupported" if the page doesn't say it, or says the opposite, or the company only quotes someone else.

Look closely at numbers, dates, names and who is saying what."""

STRATEGY_PROMPT = """You help a person write a short personal message to a company. The goal: {goal}.
You get facts about the company (f1, f2, ...) and the person's profile.

Pick the 2 or 3 best hooks: a company fact that the person can honestly connect to their own experience.
For each hook:
- fact: the id of the company fact.
- proof: the exact words from the profile that show the matching experience, at most 20 words, copied
  character for character.
- strength: that experience in a few words.
- angle: one sentence on why this makes the person interesting for the company.

Never invent experience that is not in the profile."""

WRITE_PROMPT = """You write a {channel} in {language} from {name} to someone at {company}. The goal: {goal}.
Use ONLY the hooks, the company facts, the profile and the notes. Never invent facts, numbers or experience.

Rules:
- Length: {low} to {high} {unit}. Shape: {shape}.
- Mention {company} and at least one specific fact from the research (a product, a news item, an open job).
- Use the first hook: say what in the sender's own experience matches that fact.
- Greeting: {greeting}.
- Sound like a real person: specific and friendly. No empty phrases like "I hope this message finds you well".
- {style}

Fill in: subject ("" for LinkedIn), greeting, paragraphs (one string per paragraph, without the greeting)."""


class AgentError(Exception):
    """A problem the user can fix (website unreadable, Ollama not running, model missing...)."""


# ----------------------------------------------------------------------------- what the team works with
@dataclass
class Fact:
    id: str  # f1, f2...
    category: str
    text: str
    page: str  # p1, p2...
    url: str
    quote: str
    verdict: str = ""  # the fact-checker's: supported | partly | unsupported
    reason: str = ""
    use: bool = True  # you can switch it off


@dataclass
class Hook:
    fact: str  # a fact id
    strength: str
    proof: str  # exact words from the profile, checked by Python
    angle: str


class DraftAnswer(BaseModel):  # what the writer fills in; Python adds the closing (see project 05)
    subject: str
    greeting: str
    paragraphs: list[str] = Field(min_length=1)


class Draft(DraftAnswer):
    closing: str = ""

    def text(self, channel):
        if channel == "linkedin":  # the greeting on its own line, then one short paragraph
            body = " ".join(p.strip() for p in self.paragraphs if p.strip())
            return f"{self.greeting.strip()}\n{body}" if self.greeting.strip() else body
        body = "\n\n".join(p.strip() for p in [self.greeting, *self.paragraphs, self.closing] if p.strip())
        return f"Subject: {self.subject.strip()}\n\n{body}" if self.subject.strip() else body


@dataclass
class Version:
    draft: Draft
    problems: list
    why: str


@dataclass
class Project:
    url: str
    profile: str = ""
    name: str = ""
    goal: str = "job"
    channel: str = "linkedin"
    language: str = "English"
    contact: str = ""  # who you write to, if you know (you type it; the team doesn't look for people)
    notes: str = ""
    company: str = ""
    crawl: crawler.Crawl | None = None
    facts: list = field(default_factory=list)
    unverified: int = 0  # facts whose quote wasn't on the page (dropped)
    hooks: list = field(default_factory=list)
    versions: list = field(default_factory=list)
    log: list = field(default_factory=list)  # (agent, what it did): the team's conversation, for you to read
    approved: bool = False

    @property
    def latest(self):
        return self.versions[-1] if self.versions else None

    @property
    def chosen(self):
        return [f for f in self.facts if f.use]

    def page(self, page_id):
        return next(p for p in self.crawl.pages if p.id == page_id)

    def say(self, agent, text):
        self.log.append((agent, text))


# ----------------------------------------------------------------------------- small helpers
def words(text):
    """Lower-case words only, so line breaks, quotes and dashes don't block a match."""
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).lower()))


def quote_in(quote, text):
    """True if every part of the quote (split at "...") appears word for word in the text."""
    parts = [p for p in (words(p) for p in re.split(r"\.\.\.|…", quote)) if p]
    haystack = f" {words(text)} "
    return bool(parts) and all(f" {p} " in haystack for p in parts)


def core_name(name):
    """'Brightline Analytics GmbH' -> 'brightline analytics'."""
    return words(re.sub(LEGAL_FORMS, " ", re.sub(r"\(.*?\)", " ", name.lower())))


def numbers(text):
    """Every number, digits only: '1,200' and '1.200' both become '1200'."""
    return {re.sub(r"\D", "", n) for n in re.findall(r"\d+(?:[.,]\d+)*", text)}


def keywords(text, company=""):
    """Distinctive words of a fact (names, products, long words), to see if a message uses it."""
    skip = set(core_name(company).split()) | STOP["English"] | STOP["Deutsch"]
    return {w for w in words(text).split() if (len(w) >= 6 or any(c.isdigit() for c in w)) and w not in skip}


def ollama_call(model, call):
    """Run one Ollama request and turn its errors into messages the user can act on."""
    try:
        return call()
    except ConnectionError as e:
        raise AgentError("Ollama is not running. Open the Ollama app, then try again.") from e
    except ollama.ResponseError as e:
        if e.status_code == 404:
            raise AgentError(f"The AI model {model} is not downloaded yet. Run:  ollama pull {model}") from e
        if "structured output" in str(e.error):
            raise AgentError("This Ollama install can't force the answer into JSON. Install the official app "
                             "from ollama.com (Homebrew: brew install --cask ollama-app).") from e
        raise AgentError(f"Ollama returned an error: {e.error}") from e


def stream(client, model, messages, shape, temperature):
    """The reply, read as it arrives. None if the model gets stuck writing blank space: stop at once."""
    text = ""
    chunks = ollama_call(model, lambda: client.chat(
        model=model, messages=messages, format=shape.model_json_schema(), think=False, stream=True,
        options={"temperature": temperature, "num_ctx": CONTEXT_TOKENS, "num_predict": MAX_ANSWER_TOKENS},
    ))
    for chunk in chunks:
        text += chunk.message.content or ""
        if len(text) - len(text.rstrip()) > STUCK_WHITESPACE:
            return None
    return text


def ask(client, model, system, user, shape):
    """One agent's turn: its reply is forced into the JSON shape of the pydantic model `shape`."""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    problem = None
    for temperature in TEMPERATURES:  # a broken answer at temperature 0 would break the same way again
        text = ollama_call(model, lambda: stream(client, model, messages, shape, temperature))
        if text is None:
            problem = "the model got stuck writing blank lines"
            continue
        try:
            return shape.model_validate_json(text)
        except ValidationError as e:
            problem = e
    raise AgentError(f"The AI's answer was not in the expected format, {len(TEMPERATURES)} times. Try again.\n\n{problem}")


# ----------------------------------------------------------------------------- the agents
def scout(project, http_client=None, pause=crawler.PAUSE, progress=None):
    try:
        project.crawl = crawler.crawl(project.url, http_client, pause=pause, progress=progress)
    except crawler.CrawlError as e:
        raise AgentError(str(e)) from e
    skipped = len(project.crawl.skipped)
    project.say("🕵️ Scout", f"Read {len(project.crawl.pages)} pages of {project.crawl.start}. "
                           f"Left out {skipped} link{'s' if skipped != 1 else ''} (other websites, legal pages, robots.txt).")
    return project


def researcher(project, client, model=MODEL):
    pages = project.crawl.pages
    shape = create_model("Research", company=(str, ...), facts=(list[create_model(
        "FactAnswer", category=(Literal[CATEGORIES], ...), text=(str, ...),
        page=(Literal[tuple(p.id for p in pages)], ...), quote=(str, ...))], Field(min_length=1)))
    text = "\n\n".join(f"[{p.id}] {p.title} ({p.url})\n{p.text[:PAGE_CHARS_FOR_AI]}" for p in pages)
    answer = ask(client, model, RESEARCH_PROMPT, f"CATEGORIES: {', '.join(CATEGORIES)}\n\nPAGES:\n\n{text}", shape)
    project.company = answer.company.strip()
    project.facts, seen, project.unverified = [], set(), 0
    for f in answer.facts:
        if not quote_in(f.quote, project.page(f.page).text):
            project.unverified += 1
            continue
        if words(f.text) in seen:
            continue
        seen.add(words(f.text))
        project.facts.append(Fact(f"f{len(project.facts) + 1}", f.category, f.text.strip(), f.page,
                                  project.page(f.page).url, f.quote.strip()))
        if len(project.facts) == MAX_FACTS:
            break
    if not project.facts:
        raise AgentError("The Researcher found no facts it could prove with a quote from the website.")
    project.say("🔬 Researcher", f"Wrote down {len(project.facts)} facts about {project.company}, each with a quote. "
                                 f"Dropped {project.unverified} whose quote wasn't on the page.")
    return project


class Verdict(BaseModel):
    reason: str
    said_by: Literal["the company", "someone else", "unclear"]  # a small question of its own, asked first
    verdict: Literal["supported", "partly", "unsupported"]


def check_fact(client, model, fact, page):
    """The fact-checker's view of one fact: one call per fact, so no fact gets another fact's verdict."""
    v = ask(client, model, CHECK_PROMPT, f"FACT: {fact.text}\nQUOTE: \"{fact.quote}\"\n\n"
                                         f"THE PAGE ({page.url}):\n<<<\n{page.text[:PAGE_CHARS_FOR_AI]}\n>>>", Verdict)
    if v.said_by == "someone else" and v.verdict == "supported":  # a claim the company only quotes isn't its own
        return "partly", f"{v.reason.strip()} (The page says someone else claims this.)"
    return v.verdict, v.reason.strip()


def fact_checker(project, client, model=MODEL, progress=None):
    for n, fact in enumerate(project.facts, 1):
        if progress:
            progress(f"Checking fact {n} of {len(project.facts)}")
        fact.verdict, fact.reason = check_fact(client, model, fact, project.page(fact.page))
        fact.use = fact.verdict == "supported"
    counts = {v: sum(f.verdict == v for f in project.facts) for v in ("supported", "partly", "unsupported")}
    project.say("⚖️ Fact-checker", f"{counts['supported']} supported, {counts['partly']} only partly, "
                                   f"{counts['unsupported']} not supported. Only supported facts are ticked.")
    return project


def strategist(project, client, model=MODEL):
    chosen = project.chosen
    if not chosen:
        raise AgentError("Tick at least one fact for the Strategist to work with.")
    shape = create_model("Strategy", hooks=(list[create_model(
        "HookAnswer", fact=(Literal[tuple(f.id for f in chosen)], ...), proof=(str, ...), strength=(str, ...),
        angle=(str, ...))], Field(min_length=1)))
    facts = "\n".join(f"{f.id}: {f.text}" for f in chosen)
    answer = ask(client, model, STRATEGY_PROMPT.format(goal=GOALS[project.goal]),
                 f"COMPANY: {project.company}\nFACTS:\n{facts}\n\nPROFILE:\n<<<\n{project.profile}\n>>>\n\n"
                 f"NOTES:\n{project.notes or '(none)'}", shape)
    project.hooks, used, dropped = [], set(), 0
    for h in answer.hooks:
        if h.fact in used:
            continue
        if not quote_in(h.proof, f"{project.profile}\n{project.notes}"):
            dropped += 1
            continue
        used.add(h.fact)
        project.hooks.append(Hook(h.fact, h.strength.strip(), h.proof.strip(), h.angle.strip()))
    project.hooks = project.hooks[:MAX_HOOKS]
    project.say("🎯 Strategist", f"Picked {len(project.hooks)} hook{'s' if len(project.hooks) != 1 else ''}"
                                 + (f", dropped {dropped} whose proof wasn't in your profile." if dropped else "."))
    return project


def limits(project):
    return CHANNELS[project.channel]["limits"], CHANNELS[project.channel]["aim"]


def size(project, draft):
    if project.channel == "linkedin":
        return len(draft.text("linkedin"))
    return len(re.findall(r"\w+", "\n".join([draft.greeting, *draft.paragraphs])))


def check_message(project, draft):
    """Everything Python can check about a message, as plain-language problems the writer can fix."""
    problems, channel, language = [], CHANNELS[project.channel], LANGUAGES[project.language]
    text = draft.text(project.channel)
    count, ((low, high), _) = size(project, draft), limits(project)
    if count < low:
        problems.append(f"It is too short: {count} {channel['unit']}. Write at least {low}.")
    if count > high:
        problems.append(f"It is too long: {count} {channel['unit']}. Write at most {high}: cut about {count - high + 10} "
                        f"{channel['unit']}.")
    if core_name(project.company) and f" {core_name(project.company)} " not in f" {words(text)} ":
        problems.append(f'Mention the company "{project.company}".')
    if not any(keywords(f.text + " " + f.quote, project.company) & set(words(text).split()) for f in project.chosen):
        problems.append("Mention something specific from the research, for example: " + project.chosen[0].text)
    if project.hooks and not any(keywords(f"{h.strength} {h.proof}") & set(words(text).split()) for h in project.hooks):
        problems.append(f"Say what in your own experience matches the company, for example: {project.hooks[0].proof}")
    if not words(draft.greeting).startswith(language["greetings"]):
        problems.append(f"Start with a greeting like {language['greeting']}.")
    sources = "\n".join([project.profile, project.notes, project.contact] + [f"{f.text} {f.quote}" for f in project.chosen])
    invented = sorted(numbers(text) - numbers(sources), key=int)
    if invented:
        problems.append(f"These numbers are not in the research, your profile or your notes: {', '.join(invented)}. "
                        "Remove them or use the real numbers.")
    placeholder = re.search(r"\[[^\]]*\]|\{[^}]*\}|<[^>]+>|\bX{3,}\b", text)
    if placeholder:
        problems.append(f'Replace the placeholder "{placeholder.group(0)}" with real content.')
    for cliche in CLICHES:
        if cliche in words(text):
            problems.append(f'Don\'t use the empty phrase "{cliche}".')
    tokens = words(text).split()
    other = "Deutsch" if project.language == "English" else "English"
    if sum(t in STOP[other] for t in tokens) > sum(t in STOP[project.language] for t in tokens):
        problems.append(f"Write the whole message in {project.language}.")
    return problems


def finish(project, answer):
    """Python adds the closing for emails, and the comma after the greeting."""
    greeting = answer.greeting.strip().rstrip(" ,:;!") + ","
    paragraphs = [p.strip() for p in answer.paragraphs if p.strip()]
    name = project.name.strip()
    while paragraphs and (words(paragraphs[-1]) == words(name) or
                          words(paragraphs[-1]).startswith(("best regards", "kind regards", "viele grüße", "mit freundlichen"))):
        paragraphs.pop()  # a closing the writer added anyway
    closing = f"{LANGUAGES[project.language]['sign_off']}\n{name}" if project.channel == "email" and name else ""
    subject = answer.subject.strip() if project.channel == "email" else ""
    return Draft(subject=subject, greeting=greeting, paragraphs=paragraphs or [""], closing=closing)


def write(project, client, model, instruction=""):
    channel, language = CHANNELS[project.channel], LANGUAGES[project.language]
    _, (aim_low, aim_high) = limits(project)
    system = WRITE_PROMPT.format(channel=channel["name"], language=project.language, name=project.name or "the sender",
                                 company=project.company, goal=GOALS[project.goal], low=aim_low, high=aim_high,
                                 unit=channel["unit"], shape=channel["shape"], greeting=language["greeting"],
                                 style=language["style"])
    facts = {f.id: f for f in project.chosen}
    hooks = "\n".join(f"{n}. Company: {facts[h.fact].text} → You: {h.strength} (\"{h.proof}\"). Why it matters: {h.angle}"
                      for n, h in enumerate(project.hooks, 1) if h.fact in facts) or "(none)"
    # with the website's own words, so names and job titles stay right ("Werkstudent Generative KI")
    user = (f"HOOKS:\n{hooks}\n\nCOMPANY FACTS:\n" + "\n".join(f'- {f.text} (the website says: "{f.quote}")' for f in project.chosen)
            + f"\n\nCONTACT PERSON: {project.contact or 'unknown'}\nNOTES: {project.notes or '(none)'}"
            + f"\n\nPROFILE:\n<<<\n{project.profile}\n>>>")
    if project.latest:
        user += f"\n\nCURRENT DRAFT:\n<<<\n{project.latest.draft.text(project.channel)}\n>>>"
    if instruction:
        user += f"\n\n{instruction}"
    return finish(project, ask(client, model, system, user, DraftAnswer))


def writer(project, client, model=MODEL, instruction="", why="first draft"):
    """The writer drafts; Python checks; problems go back to the writer, up to MAX_FIX_ROUNDS times."""
    draft = write(project, client, model, instruction)
    problems = check_message(project, draft)
    project.versions.append(Version(draft, problems, why))
    for _ in range(MAX_FIX_ROUNDS):
        if not problems:
            break
        fix = "FIX ONLY THESE PROBLEMS, keep everything else:\n" + "\n".join(f"- {p}" for p in problems)
        draft = write(project, client, model, fix)
        problems = check_message(project, draft)
        project.versions.append(Version(draft, problems, "fixed: " + "; ".join(project.latest.problems)))
    fixes = sum(v.why.startswith("fixed") for v in project.versions[-(1 + MAX_FIX_ROUNDS):])
    project.say("✍️ Writer", f"{'Wrote' if why == 'first draft' else 'Revised'} the {CHANNELS[project.channel]['name']}"
                             + (f" and fixed it {fixes} time{'s' if fixes > 1 else ''} after Python's check" if fixes else "")
                             + (". All checks passed." if not problems else f". Still wrong: {'; '.join(problems)}"))
    return project


# ----------------------------------------------------------------------------- running the team
def start(url, profile, name="", goal="job", channel="linkedin", language="English", contact="", notes=""):
    if not (url or "").strip():
        raise AgentError("Type the company's website address first.")
    if len((profile or "").strip()) < 100:
        raise AgentError("Your profile or CV is empty or very short. The Strategist needs it to find what you have in common.")
    if goal not in GOALS or channel not in CHANNELS or language not in LANGUAGES:
        raise AgentError(f"Unknown goal, channel or language: {goal}, {channel}, {language}.")
    return Project(url.strip(), profile.strip(), (name or guess_name(profile)).strip(), goal, channel, language,
                   contact.strip(), notes.strip())


def guess_name(profile):
    """The first line of a CV is usually the name: 'ALEX MUSTER' -> 'Alex Muster'."""
    first = next((line.strip() for line in profile.splitlines() if line.strip()), "")
    headings = {"curriculum", "vitae", "lebenslauf", "resume", "cv", "profile", "profil"}
    if (1 < len(first.split()) <= 4 and re.fullmatch(r"[^\W\d_]+(?:[ .'-]+[^\W\d_]+)*\.?", first)
            and not set(words(first).split()) & headings):
        return first.title() if first.isupper() else first
    return ""


def research(project, client=None, model=MODEL, http_client=None, pause=crawler.PAUSE, progress=None):
    """Scout, Researcher and Fact-checker, then the team stops for you."""
    client = client or ollama.Client()
    scout(project, http_client, pause, progress)
    if progress:
        progress("The Researcher is reading the pages…")
    researcher(project, client, model)
    fact_checker(project, client, model, progress)
    return project


def draft(project, client=None, model=MODEL, progress=None):
    """Strategist and Writer, after you approved the facts."""
    client = client or ollama.Client()
    if progress:
        progress("The Strategist is matching the company with your profile…")
    strategist(project, client, model)
    if progress:
        progress("The Writer is writing, Python is checking…")
    return writer(project, client, model)


def revise(project, feedback, client=None, model=MODEL):
    feedback = (feedback or "").strip()
    if not feedback:
        raise AgentError("Write what should change, or approve the message.")
    if not project.latest:
        raise AgentError("There is no message to revise yet.")
    project.notes = f"{project.notes}\n{feedback}".strip()  # new facts in it ("I start in March") may be used
    project.say("✋ You", feedback)
    return writer(project, client or ollama.Client(), model, f"THE SENDER'S FEEDBACK, apply it:\n{feedback}",
                  why=f"your feedback: {feedback}")


def approve(project, folder=None):
    """Saved in my-messages/ with the facts and sources it's based on. Nothing is sent."""
    if not project.latest:
        raise AgentError("There is no message to approve yet.")
    project.approved = True
    folder = Path(folder or MESSAGES_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", core_name(project.company) or "company").strip("-")[:40]
    stem = f"{dt.date.today():%Y-%m-%d}-{slug}-{project.channel}"
    (folder / f"{stem}.txt").write_text(project.latest.draft.text(project.channel) + "\n")
    (folder / f"{stem}.json").write_text(json.dumps({
        "company": project.company, "website": project.url, "goal": project.goal, "channel": project.channel,
        "facts": [{"fact": f.text, "quote": f.quote, "source": f.url} for f in project.chosen],
        "hooks": [h.__dict__ for h in project.hooks], "team_log": project.log,
        "versions": [{"why": v.why, "problems": v.problems, "text": v.draft.text(project.channel)} for v in project.versions],
    }, ensure_ascii=False, indent=2))
    return folder / f"{stem}.txt"


def read_file(path):
    path = Path(path)
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        return "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    return path.read_text(errors="replace")


# ----------------------------------------------------------------------------- command line (you approve in the terminal)
def main():
    parser = argparse.ArgumentParser(description="Research a company and draft a personal message, with you in the loop.")
    parser.add_argument("url", help="the company's website")
    parser.add_argument("profile", help="your CV or profile as a .txt or .md file")
    parser.add_argument("--name", default="", help="your name (default: the first line of your CV)")
    parser.add_argument("--goal", default="job", choices=list(GOALS))
    parser.add_argument("--channel", default="linkedin", choices=list(CHANNELS))
    parser.add_argument("--language", default="English", choices=list(LANGUAGES))
    parser.add_argument("--contact", default="", help='who you write to, e.g. "Frau Beispiel"')
    parser.add_argument("--notes", default="")
    parser.add_argument("--model", default=MODEL)
    args = parser.parse_args()
    try:
        project = start(args.url, read_file(args.profile), args.name, args.goal, args.channel, args.language, args.contact, args.notes)
        research(project, model=args.model, progress=print)
        print(f"\nFACTS about {project.company}:")
        for n, f in enumerate(project.facts, 1):
            print(f"  {n}. [{'x' if f.use else ' '}] {f.text}  ({f.verdict}: {f.reason})\n         \"{f.quote}\" — {f.url}")
        change = input("\nPress Enter to use the ticked facts, or type numbers to switch on or off (e.g. 3 7): ")
        for n in re.findall(r"\d+", change):
            if 1 <= int(n) <= len(project.facts):
                project.facts[int(n) - 1].use = not project.facts[int(n) - 1].use
        draft(project, model=args.model, progress=print)
        while True:
            v = project.latest
            print(f"\n{'-' * 70}\n{v.draft.text(project.channel)}\n{'-' * 70}")
            print("all checks passed" if not v.problems else "still: " + " | ".join(v.problems))
            feedback = input("\nPress Enter to approve, or type what should change: ").strip()
            if not feedback:
                break
            revise(project, feedback, model=args.model)
        print(f"\nApproved and saved to {approve(project)}. Nothing was sent.")
    except AgentError as e:
        sys.exit(f"Error: {e}")
    except (KeyboardInterrupt, EOFError):
        sys.exit("\nStopped. Nothing was saved or sent.")


if __name__ == "__main__":
    main()
