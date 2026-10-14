"""Tests for the web page, using Streamlit's built-in AppTest (no browser, fake AI)."""
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import agent  # noqa: E402
from test_agent import GOOD_PARAGRAPHS, FakeClient, draft, plan, strength  # noqa: E402

APP = str(HERE / "app.py")
PLAN = plan(
    strength("RAG", "Built a RAG chatbot in Python with LangChain"),
    strength("Kubernetes", "Ran production clusters on Kubernetes"),  # not in the CV
    strength("Docker", "Packaged the service with Docker"),
    gap="Kubernetes", gap_answer="I learned Docker on the job",
)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    client = FakeClient()
    monkeypatch.setattr(agent.ollama, "Client", lambda: client)
    monkeypatch.setattr(agent, "LETTERS_DIR", tmp_path)
    return client


def at_plan(fake):
    fake.replies.append(PLAN)
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.button[0].click().run()  # "Try it with an example"
    at.button[1].click().run()  # "Make a plan"
    assert not at.exception and not at.error
    return at


def test_the_whole_flow_with_you_in_the_loop(fake, tmp_path):
    at = at_plan(fake)
    assert "**2 · Check the plan ✋**" in at.markdown[0].value
    assert [c.value for c in at.checkbox][:3] == [True, False, True]
    assert at.checkbox(key="use-1").disabled  # its proof isn't in the CV
    assert any("Not used: these words are not in your CV" in c.value for c in at.caption)

    at.checkbox(key="use-2").uncheck().run()  # you leave Docker out
    fake.replies += [draft(greeting="Yo,"), draft()]  # the first draft has a problem, the fix doesn't
    at.button[0].click().run()  # "Approve the plan and write"
    writer_prompt = fake.calls[1]["messages"][1]["content"]
    assert "1. RAG" in writer_prompt and "Docker (the job" not in writer_prompt
    assert "**3 · Check the draft ✋**" in at.markdown[0].value
    assert at.code[0].value.startswith("Application: Junior AI Engineer")
    assert "All checks passed" in at.success[0].value
    assert "first draft" in " ".join(m.value for m in at.markdown) and "fixed: Start with a greeting" in " ".join(m.value for m in at.markdown)

    new = GOOD_PARAGRAPHS[:-1] + ["I can start on 1 March 2027 and would be glad to talk in an interview."]
    fake.replies.append(draft(paragraphs=new))
    at.text_area(key="feedback").input("Mention that I can start on 1 March 2027.").run()
    next(b for b in at.button if b.label == "Revise").click().run()
    assert "1 March 2027" in at.code[0].value

    next(b for b in at.button if b.label == "✅ Approve this version").click().run()
    assert "**4 · Done**" in at.markdown[0].value
    assert "Nothing was sent" in at.success[0].value
    assert len(list(tmp_path.glob("*.txt"))) == 1


def test_problems_the_agent_couldnt_fix_are_shown(fake):
    at = at_plan(fake)
    fake.replies += [draft(greeting="Yo,")] * (1 + agent.MAX_FIX_ROUNDS)
    at.button[0].click().run()
    assert "Still wrong" in at.warning[0].value and "Start with a greeting" in at.warning[0].value


def test_the_gap_can_be_left_out(fake):
    at = at_plan(fake)
    at.checkbox(key="keep-gap").uncheck().run()
    fake.replies.append(draft())
    at.button[0].click().run()
    assert "Gap to address" not in fake.calls[1]["messages"][1]["content"]


def test_start_over(fake):
    at = at_plan(fake)
    next(b for b in at.button if b.label == "Start over").click().run()
    assert "**1 · Job and CV**" in at.markdown[0].value


def test_empty_inputs(fake):
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.button[1].click().run()
    assert "job ad is empty" in at.error[0].value and fake.calls == []


def test_ollama_not_running(fake):
    fake.replies.append(ConnectionError("refused"))
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.button[0].click().run()
    at.button[1].click().run()
    assert "Open the Ollama app" in at.error[0].value
