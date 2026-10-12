"""Fast tests for everything except the AI itself (a fake Ollama client stands in for it).

    uv run pytest

The real models are tested separately by evaluate.py.
"""
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import ollama
import pytest
from fpdf import FPDF

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import notes  # noqa: E402

ML = HERE / "samples" / "ml_lecture_evaluating_models.pdf"
PM = HERE / "samples" / "projektmanagement_kapitel2.pdf"
STOP = set("a an and are as at be by for from how in is it of on or the this to what when which with".split())
PREFIX_WORDS = set("task search result query title none text".split())


def fake_vector(text, dims=4096):  # big enough that two words rarely share a slot
    """Bag-of-words embedding: texts that share words point in similar directions."""
    v = np.zeros(dims)
    v[0] = 0.01  # never a zero vector
    for w in notes.words(text).split():
        if w not in STOP and w not in PREFIX_WORDS:
            v[1 + int(hashlib.md5(w.encode()).hexdigest(), 16) % (dims - 1)] += 1
    return v.tolist()


class FakeClient:
    """Stands in for ollama.Client: fake embeddings, prepared chat replies, a record of every call."""

    def __init__(self, *replies, embed_error=None):
        self.replies = list(replies)
        self.chats, self.embedded = [], []
        self.embed_error = embed_error

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
        return SimpleNamespace(message=SimpleNamespace(content=reply))


def reply(*citations, found=True, answer="F1 is 0.72."):
    return json.dumps({"found": found, "citations": [{"source": s, "quote": q} for s, q in citations], "answer": answer})


def make_pdf(*pages):
    pdf = FPDF()
    for text in pages:
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        if text:
            pdf.multi_cell(0, 8, text)
    return bytes(pdf.output())


@pytest.fixture
def library(tmp_path):
    lib = notes.Library(client=FakeClient(), index_dir=tmp_path)
    lib.add_pdf(ML.name, ML.read_bytes())
    lib.add_pdf(PM.name, PM.read_bytes())
    return lib


# ----------------------------------------------------------------------------- reading PDFs
def test_pages_are_numbered_like_a_pdf_viewer():
    pages = notes.read_pdf(ML.read_bytes(), ML.name)
    assert [n for n, _ in pages] == list(range(1, 11))
    assert "harmonic mean" in dict(pages)[6]


def test_german_text_survives():
    assert "Schadenshöhe" in dict(notes.read_pdf(PM.read_bytes(), PM.name))[7]


def test_empty_pages_are_skipped_but_keep_their_number():
    pages = notes.read_pdf(make_pdf("First page text", "", "Third page text"), "x.pdf")
    assert [n for n, _ in pages] == [1, 3]


def test_scanned_pdf_without_text():
    with pytest.raises(notes.NotesError, match="scanned images"):
        notes.read_pdf(make_pdf("", ""), "scan.pdf")


def test_broken_pdf():
    with pytest.raises(notes.NotesError, match="Could not read broken.pdf"):
        notes.read_pdf(b"not a pdf", "broken.pdf")


# ----------------------------------------------------------------------------- chunking
def test_short_text_is_one_chunk():
    assert notes.split_text("Line one.\nLine   two.") == ["Line one.\nLine two."]


def test_long_text_is_cut_at_sentence_ends_with_overlap():
    sentences = [f"Sentence number {i} talks about topic {i}." for i in range(200)]
    chunks = notes.split_text(" ".join(sentences), size=500, overlap=100)
    assert len(chunks) > 10
    assert all(len(c) <= 500 for c in chunks)
    assert all(c.endswith(".") for c in chunks)
    for before, after in zip(chunks, chunks[1:]):
        assert before[-60:] in after or after[:40] in before  # neighbours share some text
    for s in sentences:  # nothing is lost
        assert any(s in c for c in chunks)


def test_text_without_breaks_is_still_cut():
    chunks = notes.split_text("x" * 3100, size=1000, overlap=100)
    assert all(len(c) <= 1000 for c in chunks) and len(chunks) == 4


def test_chunks_remember_file_and_page():
    chunks = notes.chunk_pdf(ML.read_bytes(), "lecture.pdf")
    assert {c.file for c in chunks} == {"lecture.pdf"}
    assert {c.page for c in chunks} == set(range(1, 11))


# ----------------------------------------------------------------------------- embeddings and search
def test_embeddinggemma_gets_its_task_prefixes():
    assert notes.embedding_input("When?", "query", "embeddinggemma") == "task: search result | query: When?"
    assert notes.embedding_input("Notes", "document", "embeddinggemma:300m") == "title: none | text: Notes"
    assert notes.embedding_input("When?", "query", "bge-m3") == "When?"


def test_embeddings_have_unit_length_and_report_progress():
    done = []
    vectors = notes.embed(FakeClient(), "m", [f"text {i}" for i in range(40)], "document", done.append)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1)
    assert done == [16 / 40, 32 / 40, 1.0]


def test_search_finds_the_right_page(library):
    best = library.search("harmonic mean of precision and recall")[0].chunk
    assert (best.file, best.page) == (ML.name, 6)
    best = library.search("Eintrittswahrscheinlichkeit Schadenshöhe")[0].chunk
    assert (best.file, best.page) == (PM.name, 7)


def test_search_returns_k_hits_best_first(library):
    hits = library.search("exam minutes", k=3)
    assert len(hits) == 3
    assert hits[0].score >= hits[1].score >= hits[2].score


def test_search_needs_at_least_one_pdf(tmp_path):
    with pytest.raises(notes.NotesError, match="Add at least one PDF"):
        notes.Library(client=FakeClient(), index_dir=tmp_path).search("anything")


def test_embeddings_are_cached_per_file_and_model(tmp_path):
    first = FakeClient()
    notes.Library(client=first, index_dir=tmp_path).add_pdf(ML.name, ML.read_bytes())
    assert len(first.embedded) >= 10

    again = FakeClient()
    lib = notes.Library(client=again, index_dir=tmp_path)
    lib.add_pdf("renamed.pdf", ML.read_bytes())  # same content, new name: no new embeddings
    assert again.embedded == []
    assert lib.search("harmonic mean")[0].chunk.file == "renamed.pdf"

    other_model = FakeClient()
    notes.Library(client=other_model, embed_model="bge-m3", index_dir=tmp_path).add_pdf(ML.name, ML.read_bytes())
    assert len(other_model.embedded) == len(first.embedded)


def test_adding_a_file_again_replaces_it_and_remove_works(library):
    library.add_pdf(ML.name, ML.read_bytes())
    assert list(library.files) == [ML.name, PM.name]
    assert library.pages(ML.name) == 10
    library.remove(ML.name)
    assert list(library.files) == [PM.name]


# ----------------------------------------------------------------------------- answers and their proof
def test_quote_matching():
    text = "F1 is the harmonic mean of precision and recall:\nF1 = 2 * P * R / (P + R)."
    assert notes.quote_in("harmonic mean of precision and recall", text)
    assert notes.quote_in("F1 = 2*P*R / (P+R)", text)  # spacing and symbols don't matter
    assert notes.quote_in("F1 is the harmonic mean … (P + R)", text)
    assert not notes.quote_in("harmonic mean of accuracy", text)
    assert not notes.quote_in("harmon", text)  # whole words only
    assert not notes.quote_in("", text)


def test_checked_answer_with_file_and_page(library):
    library.client.replies.append(reply((1, "F1 = 2 * 0.9 * 0.6 / (0.9 + 0.6) = 0.72")))
    a = notes.ask(library, "What is the F1 score for precision 0.9 and recall 0.6?")
    assert a.checked and a.text == "F1 is 0.72." and a.unverified == 0
    assert [(s.chunk.file, s.chunk.page) for s in a.sources] == [(ML.name, 6)]


def test_made_up_quote_is_dropped(library):
    library.client.replies.append(reply((1, "F1 is always 1.0"), (9, "page that doesn't exist")))
    a = notes.ask(library, "What is the F1 score for precision 0.9 and recall 0.6?")
    assert a.found and not a.checked
    assert a.sources == [] and a.unverified == 2


def test_the_same_quote_is_shown_once(library):
    quote = (1, "F1 = 2 * P * R / (P + R)")
    library.client.replies.append(reply(quote, quote))
    assert len(notes.ask(library, "F1 harmonic mean formula").sources) == 1


def test_not_found(library):
    library.client.replies.append(reply(found=False, answer=""))
    a = notes.ask(library, "Who won the 2014 World Cup?")
    assert not a.found and not a.checked and a.text == ""
    assert len(a.hits) == notes.TOP_K  # the search still ran and can be shown


def test_ai_gets_numbered_excerpts_with_file_and_page(library):
    library.client.replies.append(reply(found=False, answer=""))
    notes.ask(library, "harmonic mean of precision and recall", model="some-model")
    (call,) = library.client.chats
    assert call["model"] == "some-model" and call["think"] is False
    assert call["format"] == notes.AIAnswer.model_json_schema()
    assert call["options"] == {"temperature": 0, "num_ctx": notes.CONTEXT_TOKENS, "num_predict": notes.MAX_ANSWER_TOKENS}
    prompt = call["messages"][-1]["content"]
    assert prompt.startswith(f"EXCERPTS:\n\n[1] {ML.name}, page 6\n")
    assert prompt.endswith("QUESTION: harmonic mean of precision and recall")
    assert prompt.count("\n\n[") == notes.TOP_K  # [1] to [5]


def test_bad_answer_is_retried_with_a_little_randomness(library):
    library.client.replies += ['{"found": true, "citations": [' + "\n" * 40, reply(found=False, answer="")]
    assert not notes.ask(library, "harmonic mean").found
    assert [c["options"]["temperature"] for c in library.client.chats] == [0, 0.3]


def test_two_bad_answers_give_a_clear_error(library):
    library.client.replies += ["nope", '{"found": true}']
    with pytest.raises(notes.NotesError, match="not in the expected format"):
        notes.ask(library, "harmonic mean")


def test_empty_question_never_reaches_the_ai(library):
    with pytest.raises(notes.NotesError, match="Type a question"):
        notes.ask(library, "   ")
    assert library.client.chats == []


# ----------------------------------------------------------------------------- Ollama problems
def test_ollama_not_running(tmp_path):
    lib = notes.Library(client=FakeClient(embed_error=ConnectionError("refused")), index_dir=tmp_path)
    with pytest.raises(notes.NotesError, match="Open the Ollama app"):
        lib.add_pdf(ML.name, ML.read_bytes())


def test_embedding_model_not_downloaded(tmp_path):
    lib = notes.Library(client=FakeClient(embed_error=ollama.ResponseError("not found", 404)), index_dir=tmp_path)
    with pytest.raises(notes.NotesError, match="ollama pull embeddinggemma"):
        lib.add_pdf(ML.name, ML.read_bytes())


def test_chat_model_not_downloaded(library):
    library.client.replies.append(ollama.ResponseError("not found", 404))
    with pytest.raises(notes.NotesError, match="ollama pull gemma4:e4b"):
        notes.ask(library, "harmonic mean")


def test_ollama_without_structured_output(library):
    library.client.replies.append(ollama.ResponseError("structured output is unavailable", 500))
    with pytest.raises(notes.NotesError, match="official app"):
        notes.ask(library, "harmonic mean")
