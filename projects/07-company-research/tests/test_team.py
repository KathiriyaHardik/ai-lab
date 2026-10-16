"""Fast tests for the agent team: a fake AI plays every agent, and a fake web serves the sample websites."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import ollama
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import team  # noqa: E402
from test_crawler import BASE, fake_web  # noqa: E402

CV = (HERE / "samples" / "cv_ai_student.txt").read_text()


class FakeClient:
    """Plays back prepared replies (strings or exceptions), streamed like Ollama, and records every call."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return iter(SimpleNamespace(message=SimpleNamespace(content=reply[i:i + 9])) for i in range(0, len(reply), 9))


def scouted(**options):
    project = team.start(BASE, CV, **options)
    return team.scout(project, fake_web(), pause=0)


def page_id(project, ending):
    return next(p.id for p in project.crawl.pages if p.url.endswith(ending))


def research_reply(project, *facts, company="Brightline Analytics GmbH"):
    """facts: (page ending, text, quote, category)"""
    return json.dumps({"company": company, "facts": [
        {"category": c, "text": t, "page": page_id(project, end), "quote": q} for end, t, q, c in facts]})


FACTS = [
    ("about.html", "42 people work at Brightline.", "Today 42 people work at Brightline", "size and places"),
    ("careers.html", "Brightline is hiring a Junior AI Engineer.", "Junior AI Engineer (m/f/d), Stuttgart or remote", "jobs"),
    ("products.html", "Brightline Ask answers questions from manuals.", "Brightline Ask answers questions from manuals", "products"),
]


def verdict(v="supported", reason="The page says so.", said_by="the company"):
    return json.dumps({"reason": reason, "said_by": said_by, "verdict": v})


def hooks(*items):
    return json.dumps({"hooks": [{"fact": f, "proof": p, "strength": s, "angle": "It fits."} for f, p, s in items]})


def answer(paragraphs, greeting="Hello", subject=""):
    return json.dumps({"subject": subject, "greeting": greeting, "paragraphs": paragraphs})


NOTE = ["I built a RAG chatbot in Python with LangChain and saw that Brightline Analytics is hiring a Junior AI Engineer. "
        "Brightline Ask sounds close to my work. Could we talk briefly?"]


def researched(*verdicts, **options):
    project = scouted(**options)
    client = FakeClient(research_reply(project, *FACTS), *(verdicts or [verdict()] * len(FACTS)))
    team.researcher(project, client)
    team.fact_checker(project, client)
    return project


def with_hooks(**options):
    project = researched(**options)
    team.strategist(project, FakeClient(hooks(("f2", "Built a RAG chatbot in Python with LangChain", "RAG chatbots"))))
    return project


# ----------------------------------------------------------------------------- starting
def test_start_checks_the_inputs_and_guesses_the_name():
    assert team.start(BASE, CV).name == "Alex Muster"
    with pytest.raises(team.AgentError, match="website address"):
        team.start(" ", CV)
    with pytest.raises(team.AgentError, match="profile or CV is empty"):
        team.start(BASE, "short")
    with pytest.raises(team.AgentError, match="Unknown goal"):
        team.start(BASE, CV, goal="spam")


@pytest.mark.parametrize("profile, name", [("ALEX MUSTER\nCV", "Alex Muster"), ("Jordan Beispiel\n...", "Jordan Beispiel"),
                                           ("Curriculum Vitae 2026\n", ""), ("PROFILE: I build things.", "")])
def test_guess_name(profile, name):
    assert team.guess_name(profile) == name


# ----------------------------------------------------------------------------- the researcher
def test_facts_need_a_quote_that_is_really_on_their_page():
    project = scouted()
    reply = research_reply(project, *FACTS,
                           ("about.html", "Brightline has 420 people.", "Today 420 people work at Brightline", "size and places"),
                           ("careers.html", "Brightline is hiring a Junior AI Engineer!", "Junior AI Engineer (m/f/d)", "jobs"))
    team.researcher(project, FakeClient(reply))
    assert [f.text for f in project.facts] == [t for _, t, _, _ in FACTS]  # the repeat ("!" instead of ".") is dropped
    assert project.unverified == 1 and project.company == "Brightline Analytics GmbH"
    assert [f.id for f in project.facts] == ["f1", "f2", "f3"]
    assert project.facts[0].url.endswith("about.html")


def test_the_researcher_only_cites_pages_that_exist_and_reads_them_all():
    project = scouted()
    client = FakeClient(research_reply(project, *FACTS))
    team.researcher(project, client)
    schema = json.dumps(client.calls[0]["format"])
    assert all(f'"{p.id}"' in schema for p in project.crawl.pages) and '"p99"' not in schema
    prompt = client.calls[0]["messages"][1]["content"]
    assert all(f"[{p.id}]" in prompt for p in project.crawl.pages)


def test_no_provable_facts():
    project = scouted()
    with pytest.raises(team.AgentError, match="no facts it could prove"):
        team.researcher(project, FakeClient(research_reply(project, ("about.html", "x", "words not on the page", "other"))))


# ----------------------------------------------------------------------------- the fact-checker
def test_one_check_per_fact_and_only_supported_facts_are_ticked():
    project = researched(verdict(), verdict("partly", "The page says 42, not 420."), verdict("unsupported"))
    assert [(f.verdict, f.use) for f in project.facts] == [("supported", True), ("partly", False), ("unsupported", False)]
    assert project.facts[1].reason == "The page says 42, not 420."
    assert "1 supported, 1 only partly, 1 not supported" in project.log[-1][1]


def test_a_claim_someone_else_makes_is_not_supported():
    project = researched(verdict(said_by="someone else"), verdict(), verdict())
    assert (project.facts[0].verdict, project.facts[0].use) == ("partly", False)
    assert project.facts[0].reason.endswith("(The page says someone else claims this.)")


def test_the_fact_checker_sees_the_whole_page():
    project = scouted()
    client = FakeClient(research_reply(project, FACTS[0]), verdict())
    team.researcher(project, client)
    team.fact_checker(project, client)
    prompt = client.calls[1]["messages"][1]["content"]
    assert prompt.startswith('FACT: 42 people work at Brightline.\nQUOTE: "Today 42 people work at Brightline"')
    assert "Our mission: make company knowledge easy to find" in prompt


# ----------------------------------------------------------------------------- the strategist
def test_hooks_need_proof_from_your_profile():
    project = researched()
    client = FakeClient(hooks(("f2", "Built a RAG chatbot in Python with LangChain", "RAG chatbots"),
                              ("f3", "Ten years at Google", "big tech"),  # not in the CV
                              ("f2", "Packaged the service with Docker", "Docker")))  # the same fact again
    team.strategist(project, client)
    assert [(h.fact, h.strength) for h in project.hooks] == [("f2", "RAG chatbots")]
    assert "dropped 1 whose proof wasn't in your profile" in project.log[-1][1]
    assert json.dumps(client.calls[0]["format"]).count('"f') >= 3


def test_the_strategist_only_sees_the_facts_you_kept():
    project = researched()
    project.facts[0].use = False
    client = FakeClient(hooks(("f2", "Built a RAG chatbot", "RAG")))
    team.strategist(project, client)
    assert "42 people" not in client.calls[0]["messages"][1]["content"]
    assert '"f1"' not in json.dumps(client.calls[0]["format"])
    for f in project.facts:
        f.use = False
    with pytest.raises(team.AgentError, match="Tick at least one fact"):
        team.strategist(project, FakeClient())


# ----------------------------------------------------------------------------- Python's checks on a message
def check(project, paragraphs, greeting="Hello", subject="Hi"):
    return team.check_message(project, team.finish(project, team.DraftAnswer(subject=subject, greeting=greeting,
                                                                              paragraphs=paragraphs)))


def test_a_good_linkedin_note_passes():
    assert check(with_hooks(), NOTE) == []


def test_linkedin_notes_have_a_character_limit():
    found = check(with_hooks(), NOTE * 3)
    assert found[0].startswith("It is too long:") and "characters. Write at most 300" in found[0]


def test_emails_have_a_word_limit_and_a_closing():
    project = with_hooks(channel="email")
    draft = team.finish(project, team.DraftAnswer(subject="Junior AI Engineer", greeting="Hello", paragraphs=NOTE * 2))
    assert draft.closing == "Best regards\nAlex Muster" and draft.text("email").startswith("Subject: Junior AI Engineer\n\nHello,")
    assert team.check_message(project, draft) == []
    assert check(project, NOTE)[0].startswith("It is too short")


def test_it_must_be_specific_and_personal():
    project = with_hooks()
    found = check(project, ["Hello there, I would like to work at Brightline Analytics. It looks like a great place. Could we "
                            "talk briefly about my background and your team, please?"])
    assert any(p.startswith("Mention something specific from the research") for p in found)
    assert any(p.startswith("Say what in your own experience matches") for p in found)


def test_invented_numbers_placeholders_cliches_and_language():
    project = with_hooks()
    found = check(project, [NOTE[0].replace("Could we", "I hope this message finds you well. With 10 years of [skill], could we")])
    assert any("numbers are not in the research" in p and "10" in p for p in found)
    assert any('placeholder "[skill]"' in p for p in found) and any("empty phrase" in p for p in found)
    german = ["Ich habe einen RAG Chatbot mit LangChain gebaut und die Stelle als Junior AI Engineer bei Brightline "
              "Analytics gesehen. Das passt zu mir und ich würde gern mit Ihnen sprechen."]
    assert "Write the whole message in English." in check(project, german)


def test_the_company_must_be_named_and_the_greeting_is_checked():
    found = check(with_hooks(), [NOTE[0].replace("Brightline Analytics", "your company")], greeting="Yo")
    assert 'Mention the company "Brightline Analytics GmbH".' in found and any(p.startswith("Start with a greeting") for p in found)


# ----------------------------------------------------------------------------- the writer and you
def test_problems_go_back_to_the_writer():
    project = with_hooks()
    client = FakeClient(answer(NOTE * 3), answer(NOTE))
    team.writer(project, client)
    assert [v.why[:6] for v in project.versions] == ["first ", "fixed:"] and project.latest.problems == []
    assert "FIX ONLY THESE PROBLEMS" in client.calls[1]["messages"][1]["content"]
    assert "fixed it 1 time" in project.log[-1][1]


def test_the_writer_gets_the_hooks_and_stops_after_three_fixes():
    project = with_hooks()
    client = FakeClient(*[answer(NOTE * 3)] * (1 + team.MAX_FIX_ROUNDS))
    team.writer(project, client)
    user = client.calls[0]["messages"][1]["content"]
    assert user.startswith("HOOKS:\n1. Company: Brightline is hiring a Junior AI Engineer. → You: RAG chatbots")
    assert '- Brightline is hiring a Junior AI Engineer. (the website says: "Junior AI Engineer (m/f/d)' in user
    assert len(project.versions) == 1 + team.MAX_FIX_ROUNDS and project.latest.problems


def test_feedback_is_applied_and_its_facts_may_be_used():
    project = with_hooks()
    team.writer(project, FakeClient(answer(NOTE)))
    with_date = [NOTE[0].replace("Could we talk briefly?", "I can start on 1 March 2027. Could we talk?")]
    client = FakeClient(answer(with_date))
    team.revise(project, "Say that I can start on 1 March 2027.", client)
    assert project.latest.problems == [] and "1 March 2027" in project.latest.draft.text("linkedin")
    assert project.log[-2] == ("✋ You", "Say that I can start on 1 March 2027.")
    with pytest.raises(team.AgentError, match="Write what should change"):
        team.revise(project, " ", client)


def test_approve_saves_the_message_with_its_sources(tmp_path):
    project = with_hooks()
    team.writer(project, FakeClient(answer(NOTE)))
    path = team.approve(project, tmp_path)
    assert path.name.endswith("-brightline-analytics-linkedin.txt") and path.read_text().startswith("Hello,\nI built")
    saved = json.loads(path.with_suffix(".json").read_text())
    assert saved["facts"][0]["source"].endswith("about.html") and saved["team_log"][0][0] == "🕵️ Scout"


# ----------------------------------------------------------------------------- the whole team
def test_research_runs_scout_researcher_and_fact_checker(monkeypatch):
    project = team.start(BASE, CV)
    pages = team.scout(team.start(BASE, CV), fake_web(), pause=0)
    client = FakeClient(research_reply(pages, *FACTS), *[verdict()] * len(FACTS))
    team.research(project, client, http_client=fake_web(), pause=0)
    assert [agent for agent, _ in project.log] == ["🕵️ Scout", "🔬 Researcher", "⚖️ Fact-checker"]
    assert all(f.use for f in project.facts)


def test_you_approve_in_the_terminal(monkeypatch, tmp_path, capsys):
    pages = scouted()
    client = FakeClient(research_reply(pages, *FACTS), *[verdict()] * 3,
                        hooks(("f2", "Built a RAG chatbot in Python with LangChain", "RAG")), answer(NOTE), answer(NOTE))
    answers = iter(["1", "Shorter please.", ""])  # switch fact 1 off, give feedback once, approve
    monkeypatch.setattr(team.ollama, "Client", lambda: client)
    real_crawl = team.crawler.crawl
    monkeypatch.setattr(team.crawler, "crawl", lambda start, client=None, **kw: real_crawl(start, fake_web(), **{**kw, "pause": 0}))
    monkeypatch.setattr(team, "MESSAGES_DIR", tmp_path)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr(sys, "argv", ["team.py", BASE, str(HERE / "samples" / "cv_ai_student.txt")])
    team.main()
    assert "42 people" not in client.calls[4]["messages"][1]["content"]  # fact 1 was switched off
    assert "Nothing was sent" in capsys.readouterr().out and len(list(tmp_path.glob("*.txt"))) == 1


# ----------------------------------------------------------------------------- problems you can fix
def test_ollama_not_running():
    project = scouted()
    with pytest.raises(team.AgentError, match="Open the Ollama app"):
        team.researcher(project, FakeClient(ConnectionError("refused")))


def test_model_not_downloaded():
    project = scouted()
    with pytest.raises(team.AgentError, match="ollama pull gemma4:e4b"):
        team.researcher(project, FakeClient(ollama.ResponseError("not found", 404)))


def test_a_website_that_cant_be_read():
    with pytest.raises(team.AgentError, match="robots.txt"):
        team.scout(team.start(BASE, CV), fake_web(extra={"robots.txt": __import__("httpx").Response(200, text="User-agent: *\nDisallow: /")}), pause=0)


def test_a_cv_can_be_a_pdf(tmp_path):
    """A one-page PDF with real text, built by hand, read the same way as an uploaded CV."""
    text = "Alex Muster built a RAG chatbot in Python"
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET"
    objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
               f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    pdf, offsets = b"%PDF-1.4\n", []
    for n, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += f"{n} 0 obj\n{obj}\nendobj\n".encode()
    xref = len(pdf)
    pdf += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode() + "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    pdf += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    (tmp_path / "cv.pdf").write_bytes(pdf)
    assert text in team.read_file(tmp_path / "cv.pdf")
