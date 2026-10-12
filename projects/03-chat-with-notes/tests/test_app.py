"""Tests for the web page, using Streamlit's built-in AppTest (no browser, fake AI)."""
import shutil
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import notes  # noqa: E402
from test_notes import ML, FakeClient, reply  # noqa: E402

APP = str(HERE / "app.py")


@pytest.fixture
def fake(monkeypatch, tmp_path):
    """A fake Ollama, and an empty notes folder and index for every test."""
    client = FakeClient()
    monkeypatch.setattr(notes.ollama, "Client", lambda: client)
    monkeypatch.setattr(notes, "INDEX_DIR", tmp_path / "index")
    monkeypatch.setattr(notes, "NOTES_DIR", tmp_path / "my-notes")
    return client


def start():
    return AppTest.from_file(APP, default_timeout=30).run()


def sidebar_text(at):
    return " ".join(m.value for m in at.sidebar.markdown)


def test_empty_start_explains_what_to_do(fake):
    at = start()
    assert "Add your lecture PDFs" in at.info[0].value
    assert at.chat_input[0].disabled


def test_sample_notes_then_a_question(fake):
    fake.replies.append(reply((1, "F1 = 2 * 0.9 * 0.6 / (0.9 + 0.6) = 0.72"), answer="F1 = 2 * 0.9 * 0.6 / 1.5 = 0.72"))
    at = start()
    at.sidebar.button[0].click().run()  # "Add the sample lecture notes"
    assert "10 pages" in sidebar_text(at) and "9 pages" in sidebar_text(at)
    assert not at.info and not at.chat_input[0].disabled

    at.chat_input[0].set_value("What is the F1 score for precision 0.9 and recall 0.6?").run()
    assert not at.exception and not at.error
    question, answer = at.chat_message
    assert answer.markdown[0].value == r"F1 = 2 \* 0.9 \* 0.6 / 1.5 = 0.72"  # shown as written, not as italics
    assert "page 6" in answer.caption[0].value


def test_answer_without_proof_gets_a_warning(fake):
    fake.replies.append(reply((1, "F1 is always 1.0")))
    at = start()
    at.sidebar.button[0].click().run()
    at.chat_input[0].set_value("What is the F1 score?").run()
    assert "check this answer yourself" in at.chat_message[1].warning[0].value


def test_not_found(fake):
    fake.replies.append(reply(found=False, answer=""))
    at = start()
    at.sidebar.button[0].click().run()
    at.chat_input[0].set_value("Who won the 2014 World Cup?").run()
    assert "couldn't find this in your notes" in at.chat_message[1].markdown[0].value


def test_chat_history_keeps_question_and_answer_pairs(fake):
    fake.replies += [reply(found=False, answer=""), reply((1, "F1 = 2 * P * R / (P + R)"))]
    at = start()
    at.sidebar.button[0].click().run()
    at.chat_input[0].set_value("Who won the 2014 World Cup?").run()
    at.chat_input[0].set_value("What is the F1 formula?").run()
    assert [m.name for m in at.chat_message] == ["user", "assistant", "user", "assistant"]
    assert at.chat_message[2].markdown[0].value == "What is the F1 formula?"
    assert "page 6" in at.chat_message[3].caption[0].value


def test_failed_question_is_not_kept(fake):
    at = start()
    at.sidebar.button[0].click().run()
    fake.replies.append(ConnectionError("Ollama stopped"))
    at.chat_input[0].set_value("What is the F1 formula?").run()
    assert "Open the Ollama app" in at.error[0].value
    at.run()  # next rerun: the failed question is gone
    assert len(at.chat_message) == 0


def test_pdfs_added_earlier_are_loaded_and_can_be_removed(fake):
    notes.NOTES_DIR.mkdir()
    shutil.copy(ML, notes.NOTES_DIR / ML.name)
    at = start()
    assert "10 pages" in sidebar_text(at)

    at.sidebar.button(key=f"remove-{ML.name}").click().run()
    assert "10 pages" not in sidebar_text(at)
    assert not (notes.NOTES_DIR / ML.name).exists()


def test_ollama_not_running(fake):
    fake.embed_error = ConnectionError("refused")
    notes.NOTES_DIR.mkdir()
    shutil.copy(ML, notes.NOTES_DIR / ML.name)
    at = start()
    assert "Open the Ollama app" in at.error[0].value
