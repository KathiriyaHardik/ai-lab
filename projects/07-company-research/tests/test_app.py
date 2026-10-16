"""Tests for the web page, using Streamlit's built-in AppTest (no browser, fake AI, fake web)."""
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import team  # noqa: E402
from test_crawler import BASE, fake_web  # noqa: E402
from test_team import CV, FACTS, NOTE, FakeClient, answer, hooks, research_reply, scouted, verdict  # noqa: E402

APP = str(HERE / "app.py")


@pytest.fixture
def fake(monkeypatch, tmp_path):
    pages = scouted()
    client = FakeClient(research_reply(pages, *FACTS), verdict("partly", "42, not 420."), verdict(), verdict(),
                        hooks(("f2", "Built a RAG chatbot in Python with LangChain", "RAG chatbots")), answer(NOTE))
    real_crawl = team.crawler.crawl
    monkeypatch.setattr(team.ollama, "Client", lambda: client)
    monkeypatch.setattr(team.crawler, "crawl", lambda start, client=None, **kw: real_crawl(start, fake_web(), **{**kw, "pause": 0}))
    monkeypatch.setattr(team, "MESSAGES_DIR", tmp_path)
    return client


def started():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.text_input(key="url").input(BASE)
    at.text_area(key="profile").input(CV)
    next(b for b in at.button if b.label == "Start the research").click().run()
    assert not at.exception and not at.error
    return at


def button(at, label):
    return next(b for b in at.button if b.label == label)


def test_research_then_facts_then_message_then_done(fake, tmp_path):
    at = started()
    assert "**2 · Check the facts ✋**" in at.markdown[0].value
    assert [c.value for c in at.checkbox] == [False, True, True]  # the partly supported fact is not ticked
    assert any("42, not 420." in c.value for c in at.caption)

    button(at, "Use the ticked facts and write").click().run()
    assert not at.exception and not at.error
    assert "**3 · Check the message ✋**" in at.markdown[0].value
    assert at.code[0].value.startswith("Hello,\nI built a RAG chatbot")
    assert "All checks passed" in at.success[0].value
    assert "42 people" not in fake.calls[4]["messages"][1]["content"]  # the unticked fact stayed out

    button(at, "✅ Approve this message").click().run()
    assert "Nothing was sent" in at.success[0].value and len(list(tmp_path.glob("*.txt"))) == 1


def test_the_business_goal_shows_the_uwg_reminder(fake):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.radio[0].set_value("business").run()
    assert "§7 UWG" in at.warning[0].value


def test_empty_inputs(fake):
    at = AppTest.from_file(APP, default_timeout=60).run()
    button(at, "Start the research").click().run()
    assert "website address" in at.error[0].value and fake.calls == []
