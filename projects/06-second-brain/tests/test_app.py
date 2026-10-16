"""Tests for the web page, using Streamlit's built-in AppTest (no browser, fake AI, real ChromaDB in a temp folder)."""
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import brain  # noqa: E402
from test_brain import FakeClient, answer, link, plan  # noqa: E402

APP = str(HERE / "app.py")


@pytest.fixture
def fake(monkeypatch, tmp_path):
    client = FakeClient()
    monkeypatch.setattr(brain.ollama, "Client", lambda: client)
    monkeypatch.setattr(brain, "DB_DIR", tmp_path / "db")
    monkeypatch.setattr(brain, "SAMPLE_COPY", tmp_path / "my-sample-vault")
    return client


def with_samples():
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.button[0].click().run()  # "Use the sample notes"
    assert not at.exception and not at.error
    return at


def test_start_explains_what_to_do(fake):
    at = AppTest.from_file(APP, default_timeout=60).run()
    assert "Choose the folder with your notes" in at.info[0].value


def test_sample_notes_are_copied_and_synced(fake):
    at = with_samples()
    assert (brain.SAMPLE_COPY / "Tax ID.md").exists()
    assert "**24 notes** in the vector database. Last sync: 24 new" in at.sidebar.caption[0].value


def test_ask_shows_the_plan_the_answer_and_the_sources(fake):
    at = with_samples()
    fake.replies += [plan("habit stacking"), answer((1, "attach the new habit to one you already have"))]
    at.text_input(key="question").input("How can habits help with my German vocabulary?")
    next(b for b in at.button if b.label == "Ask").click().run()  # the form's submit button
    page = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
    assert "Use habit stacking." in page
    assert "“habit stacking”" in page and "attach the new habit to one you already have" in page


def test_a_missing_folder(fake):
    at = AppTest.from_file(APP, default_timeout=60).run()
    at.sidebar.text_input[0].input("/no/such/folder").run()
    assert "doesn't exist" in at.error[0].value


def test_find_connections_and_add_a_link(fake):
    at = with_samples()
    at.selectbox(key="pick").select("Kanban board").run()
    fake.replies += [link("track job applications", "5 good applications per week")] + [link(connected=False, idea="")] * 3
    next(b for b in at.button if b.label == "Find connections").click().run()
    new = [c for c in at.checkbox if c.label.startswith("Link “")]
    assert new, "a new connection should be offered"
    new[0].check().run()
    next(b for b in at.button if b.label.startswith("Add 1 link")).click().run()
    assert "Added under “## Related”" in at.success[0].value
    assert "## Related\n- [[" in (brain.SAMPLE_COPY / "Kanban board.md").read_text()
