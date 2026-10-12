"""Tests for the web page, using Streamlit's built-in AppTest (no browser, fake AI)."""
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import letters  # noqa: E402
from test_letters import LETTER, TAX_LABELS, FakeClient, answer  # noqa: E402

APP = str(HERE / "app.py")


@pytest.fixture
def fake(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(letters.ollama, "Client", lambda: client)
    return client


def start():
    return AppTest.from_file(APP, default_timeout=30).run()


def explain_example(at):
    at.selectbox[1].select("Tax office: tax to pay (photo)")
    at.button[0].click().run()
    return at


def test_nothing_chosen_gives_a_friendly_error(fake):
    at = start()
    at.button[0].click().run()
    assert "Add a photo or PDF" in at.error[0].value
    assert fake.calls == []


def test_example_letter_is_explained(fake):
    fake.replies += [LETTER, answer(TAX_LABELS, amount="a1", reference=("12/345/67890", "Steuernummer: 12/345/67890"))]
    at = explain_example(start())
    assert not at.exception and not at.error
    assert [m.value for m in at.metric] == ["1.234,56 €", "12/345/67890", "06.10.2026"]
    boxes = [b.value for b in [*at.error, *at.warning, *at.info]]
    assert any("09.11.2026" in b and "Pay the tax" in b for b in boxes)
    assert any(b.startswith("Only if you disagree: Object to the notice") and "06.11.2026" in b for b in boxes)
    assert any("3 days before" in b for b in boxes)
    assert any("From your letter" in c.value and "09.11.2026" in c.value for c in at.caption)
    assert any("valid until **31.12.2026**" in c.value for c in at.caption)
    assert at.code[0].value == LETTER  # what the AI read, to compare with the paper


def test_letter_without_deadlines(fake):
    fake.replies += [LETTER, answer()]
    at = explain_example(start())
    assert "No deadline" in at.success[0].value
    assert at.metric[0].value == "nothing"


def test_result_stays_when_the_page_reruns(fake):
    fake.replies += [LETTER, answer(TAX_LABELS, amount="a1")]
    at = explain_example(start())
    at.selectbox[0].select("Einfaches Deutsch").run()
    assert at.metric[0].value == "1.234,56 €"
    assert len(fake.calls) == 2  # not explained again


def test_unknown_reference_is_flagged(fake):
    fake.replies += [LETTER, answer(reference=("99/999/99999", "Steuernummer: 99/999/99999"))]
    at = explain_example(start())
    assert "couldn't find this reference number" in at.warning[0].value


def test_ollama_not_running(fake):
    fake.replies.append(ConnectionError("refused"))
    at = explain_example(start())
    assert "Open the Ollama app" in at.error[0].value
