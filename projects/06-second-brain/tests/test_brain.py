"""Fast tests for everything except the AI itself: a fake Ollama client stands in for it, and a real
ChromaDB runs in a temporary folder.

    uv run pytest

The real models are tested separately by evaluate.py.
"""
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import ollama
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import brain  # noqa: E402

STOP = set("a an and are as at be by for from how in is it of on or the this to what when which with i my do does "
           "task search result query title none text".split())


def fake_vector(text, dims=4096):
    """Bag-of-words embedding: texts that share words point in similar directions."""
    v = np.zeros(dims)
    v[0] = 0.01
    for w in brain.words(text).split():
        if w not in STOP:
            v[1 + int(hashlib.md5(w.encode()).hexdigest(), 16) % (dims - 1)] += 1
    return v.tolist()


class FakeClient:
    """Stands in for ollama.Client: fake embeddings, prepared chat replies (streamed), a record of every call."""

    def __init__(self, *replies, embed_error=None):
        self.replies, self.chats, self.embedded, self.embed_error = list(replies), [], [], embed_error

    def embed(self, model, input):
        if self.embed_error:
            raise self.embed_error
        self.embedded += input
        return SimpleNamespace(embeddings=[fake_vector(t) for t in input])

    def chat(self, **kwargs):
        self.chats.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return iter(SimpleNamespace(message=SimpleNamespace(content=reply[i:i + 9])) for i in range(0, len(reply), 9))


def plan(*queries):
    return json.dumps({"queries": list(queries)})


def answer(*citations, found=True, text="Use habit stacking."):
    return json.dumps({"found": found, "citations": [{"source": s, "quote": q} for s, q in citations], "answer": text})


def link(first="", second="", idea="They fit together.", connected=True):
    return json.dumps({"connected": connected, "quote_first": first, "quote_second": second, "idea": idea})


@pytest.fixture
def vault(tmp_path):
    folder = tmp_path / "vault"
    shutil.copytree(HERE / "samples" / "vault", folder)
    return folder


@pytest.fixture
def mind(vault, tmp_path):
    b = brain.Brain(vault, db_dir=tmp_path / "db", client=FakeClient())
    b.sync()
    b.client.embedded.clear()
    return b


# ----------------------------------------------------------------------------- reading notes
def test_front_matter_inline_tags_title_and_links():
    raw = ("---\nsample: x\ntags: [German, learning]\n---\n# My Note\n\nText with #anki and #learning/spaced, "
           "not `#code` or a page#anchor.\nSee [[Spaced repetition]], [[Tax ID|the tax ID]] and [[Overfitting#Remedies]].")
    n = brain.parse_note("folder/My Note.md", raw)
    assert n.title == "My Note" and n.path == "folder/My Note.md" and n.stem == "My Note"
    assert n.tags == {"german", "learning", "anki", "learning/spaced"}
    assert n.links == {"spaced repetition", "tax id", "overfitting"}
    assert not n.text.startswith("---")


def test_yaml_list_tags_and_title_from_the_file_name():
    n = brain.parse_note("Ideas.md", "---\ntags:\n  - idea\n  - ai\n---\nJust some text.")
    assert n.tags == {"idea", "ai"} and n.title == "Ideas"


def test_the_hash_follows_the_content():
    assert brain.parse_note("a.md", "one").hash != brain.parse_note("a.md", "two").hash


def test_vault_reading_skips_hidden_folders_and_other_files(vault):
    (vault / ".obsidian").mkdir()
    (vault / ".obsidian" / "workspace.md").write_text("# Settings")
    (vault / "picture.png").write_bytes(b"x")
    (vault / "Projects").mkdir()
    (vault / "Projects" / "Plan.md").write_text("# Plan\nA nested note.")
    notes = brain.read_vault(vault)
    assert len(notes) == 25 and "Projects/Plan.md" in notes and ".obsidian/workspace.md" not in notes


def test_missing_folder():
    with pytest.raises(brain.BrainError, match="doesn't exist"):
        brain.read_vault("/no/such/folder")


def fake_window(monkeypatch, returncode=0, stdout="", stderr="", error=None):
    """Stands in for the computer's folder window: records the command, gives back a prepared result."""
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if error:
            raise error
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)
    monkeypatch.setattr(brain.subprocess, "run", run)
    return calls


def test_browse_on_a_mac_opens_the_folder_window_where_you_are(monkeypatch, vault):
    monkeypatch.setattr(brain.sys, "platform", "darwin")
    calls = fake_window(monkeypatch, stdout="/Users/me/Notes/\n")
    assert brain.pick_folder(vault) == "/Users/me/Notes"
    assert calls[0][0] == "osascript" and calls[0][-1] == str(vault)


def test_browse_cancel_and_problems(monkeypatch):
    monkeypatch.setattr(brain.sys, "platform", "darwin")
    calls = fake_window(monkeypatch, returncode=1, stderr="execution error: User canceled. (-128)")
    assert brain.pick_folder("/no/such/folder") is None
    assert calls[0][-1] == str(Path.home())  # a folder that doesn't exist starts the window at home
    fake_window(monkeypatch, returncode=1, stderr="Not authorised to send Apple events.")
    with pytest.raises(brain.BrainError, match="Type the folder's path instead"):
        brain.pick_folder()
    fake_window(monkeypatch, error=FileNotFoundError("osascript"))
    with pytest.raises(brain.BrainError, match="couldn't be opened"):
        brain.pick_folder()


def test_browse_on_windows_and_linux_uses_tk_in_its_own_process(monkeypatch):
    monkeypatch.setattr(brain.sys, "platform", "linux")
    calls = fake_window(monkeypatch, stdout="\n")  # Tk prints an empty line when you cancel
    assert brain.pick_folder() is None
    assert calls[0][:2] == [sys.executable, "-c"] and "askdirectory" in calls[0][2]


@pytest.mark.skipif(not shutil.which("osacompile"), reason="needs macOS")
def test_the_mac_folder_window_script_is_valid_applescript(tmp_path):
    lines = [a for line in brain.MAC_PICKER for a in ("-e", line)]
    done = subprocess.run(["osacompile", "-o", str(tmp_path / "picker.scpt"), *lines], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_short_notes_stay_whole_and_long_ones_are_cut():
    short = brain.parse_note("s.md", "# S\nA short note.")
    assert brain.chunks(short) == [short.text]
    sections = "\n\n".join(f"## Part {i}\n" + f"Sentence {i} about topic {i}. " * 30 for i in range(6))
    long = brain.parse_note("l.md", "# Long\n\n" + sections)
    pieces = brain.chunks(long, size=1200)
    assert len(pieces) > 3 and all(len(p) <= 1200 for p in pieces)
    for i in range(6):
        assert any(f"Sentence {i} about topic {i}." in p for p in pieces)


# ----------------------------------------------------------------------------- the vector database
def test_sync_adds_then_only_changes(mind, vault):
    assert mind.col.count() == 24 and len(mind.notes) == 24
    again = mind.sync()
    assert (again.added, again.updated, again.removed, again.unchanged) == ([], [], [], 24)
    assert mind.client.embedded == []  # nothing processed twice

    (vault / "Scrum basics.md").write_text("# Scrum basics\nSprints last 2 weeks in our team.")
    (vault / "Weekly review.md").unlink()
    (vault / "New idea.md").write_text("---\ntags: [idea]\n---\n# New idea\nA quiz app for German articles.")
    report = mind.sync()
    assert report.added == ["New idea.md"] and report.updated == ["Scrum basics.md"] and report.removed == ["Weekly review.md"]
    assert len(mind.client.embedded) == 2  # only the new and the changed note
    assert mind.col.count() == 24
    assert mind.col.get(where={"path": "Scrum basics.md"})["documents"] == ["# Scrum basics\nSprints last 2 weeks in our team."]


def test_tags_are_stored_for_filtering(mind):
    metadata = mind.col.get(where={"path": "German vocabulary routine.md"})["metadatas"][0]
    assert metadata["tag_german"] is True and metadata["tag_learning"] is True and metadata["title"] == "German vocabulary routine"
    assert "german" in mind.tags and "pm" in mind.tags


def test_search_by_meaning_and_by_tag(mind):
    assert mind.search("Steuer-ID employer salary")[0].title == "Tax ID"
    hits = mind.search("review flashcards Anki", tag="productivity")
    assert hits and all(h.path in {"Book notes - Atomic Habits.md", "Pomodoro technique.md", "Weekly review.md"} for h in hits)


def test_search_before_sync(vault, tmp_path):
    with pytest.raises(brain.BrainError, match="Sync first"):
        brain.Brain(vault, db_dir=tmp_path / "empty", client=FakeClient()).search("anything")


def test_each_vault_and_model_gets_its_own_collection(vault, tmp_path):
    a = brain.Brain(vault, db_dir=tmp_path / "db", client=FakeClient())
    b = brain.Brain(vault, db_dir=tmp_path / "db", client=FakeClient(), embed_model="bge-m3")
    assert a.col.name != b.col.name


def test_related_notes_exclude_the_note_itself(mind):
    found = mind.related("Kanban board", k=4)
    assert len(found) == 4 and all(n.title != "Kanban board" for n, _ in found)
    assert [s for _, s in found] == sorted((s for _, s in found), reverse=True)


def test_notes_by_title_path_or_file_name(mind):
    assert mind.note("book notes: atomic habits").path == mind.note("Book notes - Atomic Habits").path
    with pytest.raises(brain.BrainError, match="no note called"):
        mind.note("Nothing like this")


# ----------------------------------------------------------------------------- asking
def test_ask_plans_searches_and_checks_quotes(mind):
    mind.client.replies += [
        plan("habit stacking", "German vocabulary Anki", "habit stacking"),  # a repeat is dropped
        answer((1, "attach the new habit to one you already have"), (1, "invented words")),
    ]
    a = brain.ask(mind, "How can habits help with my German vocabulary?")
    assert a.queries == ["habit stacking", "German vocabulary Anki"]
    assert a.checked and a.unverified == 1
    assert [s.title for s in a.sources] == [a.hits[0].title]
    assert len({h.id for h in a.hits}) == len(a.hits) <= brain.MAX_EXCERPTS
    plan_call, answer_call = mind.client.chats
    assert plan_call["format"] == brain.SearchPlan.model_json_schema() and plan_call["stream"] and plan_call["think"] is False
    assert answer_call["messages"][1]["content"].startswith(f"EXCERPTS:\n\n[1] {a.hits[0].title}\n")


def test_ask_without_a_plan_searches_the_question_only(mind):
    mind.client.replies.append(answer(found=False, text=""))
    a = brain.ask(mind, "What is the capital of Canada?", plan=False)
    assert not a.found and a.queries == [] and len(mind.client.chats) == 1


def test_ask_with_a_tag_only_searches_those_notes(mind):
    mind.client.replies += [plan("sprint length"), answer(found=False, text="")]
    a = brain.ask(mind, "How long are sprints?", tag="pm")
    assert {h.path for h in a.hits} <= {"Scrum basics.md", "Risk management.md", "Kanban board.md"}


def test_empty_question(mind):
    with pytest.raises(brain.BrainError, match="Type a question"):
        brain.ask(mind, "  ")


def test_a_stuck_model_is_stopped_and_retried(mind):
    stuck = '{"queries": ["a"]' + "\n   " * 300
    mind.client.replies += [stuck, plan("habits"), answer(found=False, text="")]
    brain.ask(mind, "habits?")
    assert [c["options"]["temperature"] for c in mind.client.chats] == [0, 0.3, 0]


# ----------------------------------------------------------------------------- connecting and linking
def test_connections_check_quotes_and_mark_new_ones(mind):
    related = [n.title for n, _ in mind.related("Spaced repetition", 3)]
    replies = {
        "German vocabulary routine": link("I use the free app Anki for this.", "Every day I add 20 new words to Anki"),
        "Exam preparation strategy": link("Active recall works better than rereading",  # swapped on purpose
                                          "My review intervals: after 1 day, 3 days, 7 days and 14 days."),
    }
    mind.client.replies += [replies.get(t, link(connected=False, idea="")) for t in related]
    note, found = brain.connections(mind, "Spaced repetition", k=3)
    by_title = {c.note.title: c for c in found}
    assert note.title == "Spaced repetition" and len(found) == 3
    if "German vocabulary routine" in by_title:
        c = by_title["German vocabulary routine"]
        assert c.already_linked and c.checked
    if "Exam preparation strategy" in by_title:
        c = by_title["Exam preparation strategy"]
        assert not c.already_linked and c.checked and c.quote_here.startswith("My review intervals")  # swapped back
    assert all(c.connected == (c.note.title in replies) for c in found)
    first_prompt = mind.client.chats[0]["messages"][1]["content"]
    assert first_prompt.startswith("FIRST NOTE: Spaced repetition\n<<<\n# Spaced repetition")


def test_swapped_quotes_are_put_back_and_made_up_ones_fail(mind):
    note, other = mind.note("Spaced repetition"), mind.note("Exam preparation strategy")
    mind.client.replies += [
        link("Active recall works better than rereading", "My review intervals: after 1 day, 3 days"),  # swapped
        link("Review everything the night before", "Active recall works better than rereading"),  # invented
        link(connected=False, idea=""),
    ]
    connected, idea, here, there, checked = brain.connect(mind, note, other)
    assert checked and here.startswith("My review intervals") and there.startswith("Active recall")
    assert brain.connect(mind, note, other)[4] is False
    connected, idea, here, there, checked = brain.connect(mind, note, other)
    assert not connected and not checked


def test_links_count_in_both_directions_and_with_aliases(mind):
    assert brain.linked(mind.note("Tax ID"), mind.note("Anmeldung checklist"))  # [[Anmeldung checklist|Anmeldung]]
    assert brain.linked(mind.note("Anmeldung checklist"), mind.note("Tax ID"))  # the other way round
    assert not brain.linked(mind.note("Kanban board"), mind.note("Job search plan 2026"))


def test_adding_links_only_appends(mind, vault):
    before = (vault / "Kanban board.md").read_text()
    added = brain.add_links(mind, "Kanban board", ["Job search plan 2026", "Scrum basics"])
    after = (vault / "Kanban board.md").read_text()
    assert added == ["Job search plan 2026", "Scrum basics"]
    assert after.startswith(before.rstrip()) and after.endswith("## Related\n- [[Job search plan 2026]]\n- [[Scrum basics]]\n")
    assert brain.add_links(mind, "Kanban board", ["Scrum basics", "Risk management"]) == ["Risk management"]
    assert (vault / "Kanban board.md").read_text().endswith("- [[Scrum basics]]\n- [[Risk management]]\n")  # same section
    assert brain.linked(mind.note("Kanban board"), mind.note("Risk management"))  # synced again


def test_no_link_to_itself_or_to_something_already_linked(mind):
    assert brain.add_links(mind, "Tax ID", ["Tax ID", "Anmeldung checklist"]) == []


# ----------------------------------------------------------------------------- Ollama problems
def test_ollama_not_running(vault, tmp_path):
    b = brain.Brain(vault, db_dir=tmp_path / "db", client=FakeClient(embed_error=ConnectionError("refused")))
    with pytest.raises(brain.BrainError, match="Open the Ollama app"):
        b.sync()


def test_model_not_downloaded(mind):
    mind.client.replies.append(ollama.ResponseError("not found", 404))
    with pytest.raises(brain.BrainError, match="ollama pull gemma4:e4b"):
        brain.ask(mind, "habits?")
