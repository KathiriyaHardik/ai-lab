#!/usr/bin/env python3
"""Chat with Your Notes: ask questions about your lecture PDFs, get answers with page numbers.

    uv run notes.py "When is the exam?" samples/*.pdf

How a question is answered (RAG, retrieval-augmented generation):
  1. every PDF page is cut into short pieces (chunks) and each chunk gets an embedding:
     768 numbers that describe what the text is about. They are saved in .index/, so a PDF
     is only processed once
  2. the question gets an embedding too. The chunks whose embeddings point in the most similar
     direction (cosine similarity) are the best candidates
  3. the AI answers from those chunks only and quotes them word for word. If they don't hold
     the answer, it has to say so instead of guessing
  4. Python checks that every quote really is in the chunk the AI names. Only checked quotes
     are shown as sources (file + page)

Everything runs on this computer: the PDFs and questions are only sent to Ollama on localhost.
"""
import argparse
import hashlib
import io
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import ollama
from pydantic import BaseModel, Field, ValidationError

CHAT_MODEL = os.environ.get("NOTES_MODEL", "gemma4:e4b")
EMBED_MODEL = os.environ.get("NOTES_EMBED_MODEL", "embeddinggemma")
HERE = Path(__file__).resolve().parent
INDEX_DIR = HERE / ".index"  # saved embeddings, one file per PDF (git-ignored)
NOTES_DIR = HERE / "my-notes"  # PDFs added in the web page (git-ignored)

TOP_K = 5  # chunks the AI gets to read for each question
CHUNK_CHARS = 1500  # a slide or half a book page: one idea per chunk, well inside embeddinggemma's 2k tokens
CHUNK_OVERLAP = 200  # repeated at the start of the next chunk, so a sentence cut in two is still found
EMBED_BATCH = 16
CONTEXT_TOKENS = 16384
MAX_ANSWER_TOKENS = 1024  # hard stop, so a stuck model can't run forever (see project 02)

ANSWER_PROMPT = """You help a student with their lecture notes. You get numbered excerpts from the notes
and one question.

Use ONLY the excerpts. Do not add anything you know from elsewhere.
- found: true if the excerpts contain the answer, false if they don't.
- citations: for every excerpt you use, its number and the exact words from it that support the
  answer, at most 25 words, copied character for character. Empty when found is false.
- answer: the answer in 1 to 4 sentences, in the same language as the question.
  Empty when found is false."""


class NotesError(Exception):
    """A problem the user can fix (no PDFs, scanned PDF, Ollama not running, model missing...)."""


# ----------------------------------------------------------------------------- reading PDFs
@dataclass
class Chunk:
    file: str
    page: int  # 1-based, as printed in a PDF viewer
    text: str


def read_pdf(data, name):
    """[(page number, text)] for every page that has text."""
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(n, (page.extract_text() or "").strip()) for n, page in enumerate(reader.pages, 1)]
    except Exception as e:  # pypdf raises many different errors for broken files
        raise NotesError(f"Could not read {name} ({e}).") from e
    pages = [(n, text) for n, text in pages if text]
    if not pages:
        raise NotesError(f"{name} has no text in it. It is probably scanned images, which this tool can't read yet.")
    return pages


def split_text(text, size=CHUNK_CHARS, overlap=CHUNK_OVERLAP):
    """Cut text into pieces of at most `size` characters, preferably at a line or sentence end."""
    text = re.sub(r"[ \t]+", " ", text).strip()
    chunks, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = max(text.rfind("\n", start + size // 2, end), text.rfind(". ", start + size // 2, end))
            if cut > start:
                end = cut + 1
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [c for c in chunks if c]


def chunk_pdf(data, name):
    return [Chunk(name, page, piece) for page, text in read_pdf(data, name) for piece in split_text(text)]


# ----------------------------------------------------------------------------- talking to Ollama
def ollama_call(model, call):
    """Run one Ollama request and turn its errors into messages the user can act on."""
    try:
        return call()
    except ConnectionError as e:
        raise NotesError("Ollama is not running. Open the Ollama app, then try again.") from e
    except ollama.ResponseError as e:
        if e.status_code == 404:
            raise NotesError(f"The AI model {model} is not downloaded yet. Run:  ollama pull {model}") from e
        if "structured output" in str(e.error):
            raise NotesError(
                "This Ollama install can't force the answer into JSON. Install the official app "
                "from ollama.com (Homebrew: brew install --cask ollama-app)."
            ) from e
        raise NotesError(f"Ollama returned an error: {e.error}") from e


def embedding_input(text, kind, model):
    """EmbeddingGemma was trained with these task prefixes; other models get the plain text."""
    if not model.startswith("embeddinggemma"):
        return text
    return f"task: search result | query: {text}" if kind == "query" else f"title: none | text: {text}"


def embed(client, model, texts, kind, progress=None):
    """Unit-length embeddings (rows), so a dot product is the cosine similarity."""
    rows = []
    for start in range(0, len(texts), EMBED_BATCH):
        batch = [embedding_input(t, kind, model) for t in texts[start:start + EMBED_BATCH]]
        rows += ollama_call(model, lambda: client.embed(model=model, input=batch)).embeddings
        if progress:
            progress(min(start + EMBED_BATCH, len(texts)) / len(texts))
    vectors = np.array(rows, dtype=np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


# ----------------------------------------------------------------------------- the library of notes
@dataclass
class Hit:
    chunk: Chunk
    score: float  # cosine similarity, -1..1


class Library:
    """The PDFs you added: their chunks and embeddings, searchable by meaning."""

    def __init__(self, client=None, embed_model=None, index_dir=None):
        self.client = client or ollama.Client()
        self.embed_model = embed_model or EMBED_MODEL
        self.index_dir = Path(index_dir or INDEX_DIR)
        self.files = {}  # name -> (chunks, vectors)

    def add_pdf(self, name, data, progress=None):
        """Add (or replace) one PDF. Embeddings are cached by file content and model."""
        key = hashlib.sha256(data + self.embed_model.encode()).hexdigest()[:20]
        cache = self.index_dir / f"{key}.json"
        if cache.exists():
            saved = json.loads(cache.read_text())
            chunks = [Chunk(name, c["page"], c["text"]) for c in saved["chunks"]]
            vectors = np.array([c["embedding"] for c in saved["chunks"]], dtype=np.float32)
        else:
            chunks = chunk_pdf(data, name)
            vectors = embed(self.client, self.embed_model, [c.text for c in chunks], "document", progress)
            self.index_dir.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps({"model": self.embed_model, "chunks": [
                {"page": c.page, "text": c.text, "embedding": v.round(6).tolist()} for c, v in zip(chunks, vectors)
            ]}))
        self.files[name] = (chunks, vectors)
        return len(chunks)

    def remove(self, name):
        self.files.pop(name, None)

    def pages(self, name):
        return len({c.page for c in self.files[name][0]})

    def search(self, question, k=TOP_K):
        if not self.files:
            raise NotesError("Add at least one PDF first.")
        chunks = [c for cs, _ in self.files.values() for c in cs]
        vectors = np.vstack([vs for _, vs in self.files.values()])
        scores = vectors @ embed(self.client, self.embed_model, [question], "query")[0]
        best = np.argsort(-scores)[:k]
        return [Hit(chunks[i], float(scores[i])) for i in best]


# ----------------------------------------------------------------------------- answering
class Citation(BaseModel):
    source: int = Field(description="Number of the excerpt, for example 2 for [2]")
    quote: str = Field(description="Exact words copied from that excerpt")


class AIAnswer(BaseModel):
    found: bool
    citations: list[Citation]  # before the answer, so the AI picks its evidence first
    answer: str


@dataclass
class Source:
    chunk: Chunk
    quote: str


@dataclass
class Answer:
    question: str
    found: bool
    text: str
    sources: list = field(default_factory=list)  # citations whose quote is really in the excerpt
    unverified: int = 0  # citations that failed that check
    hits: list = field(default_factory=list)  # what the search found

    @property
    def checked(self):
        """An answer we can stand behind: found, with at least one quote that was checked."""
        return self.found and bool(self.sources)


def words(text):
    """Lower-case words only, so line breaks, bullets, quotes and dashes don't block a match."""
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).lower()))


def quote_in(quote, text):
    """True if every part of the quote (split at "...") appears word for word in the text."""
    parts = [p for p in (words(p) for p in re.split(r"\.\.\.|…", quote)) if p]
    haystack = f" {words(text)} "
    return bool(parts) and all(f" {p} " in haystack for p in parts)


def excerpts(hits):
    return "\n\n".join(f"[{i}] {h.chunk.file}, page {h.chunk.page}\n{h.chunk.text}" for i, h in enumerate(hits, 1))


def ask_ai(client, model, question, hits):
    messages = [
        {"role": "system", "content": ANSWER_PROMPT},
        {"role": "user", "content": f"EXCERPTS:\n\n{excerpts(hits)}\n\nQUESTION: {question}"},
    ]
    problem = None
    for temperature in (0, 0.3):  # a broken answer at temperature 0 would break the same way again
        response = ollama_call(model, lambda: client.chat(
            model=model,
            messages=messages,
            format=AIAnswer.model_json_schema(),  # Ollama forces the reply into this JSON shape
            think=False,
            options={"temperature": temperature, "num_ctx": CONTEXT_TOKENS, "num_predict": MAX_ANSWER_TOKENS},
        ))
        try:
            return AIAnswer.model_validate_json(response.message.content)
        except ValidationError as e:
            problem = e
    raise NotesError(f"The AI's answer was not in the expected format, twice. Try again.\n\n{problem}")


def ask(library, question, model=CHAT_MODEL, k=TOP_K):
    question = (question or "").strip()
    if not question:
        raise NotesError("Type a question first.")
    hits = library.search(question, k)
    reply = ask_ai(library.client, model, question, hits)
    if not reply.found:
        return Answer(question, False, "", hits=hits)

    sources, seen, unverified = [], set(), 0
    for c in reply.citations:
        if not (1 <= c.source <= len(hits) and quote_in(c.quote, hits[c.source - 1].chunk.text)):
            unverified += 1
            continue
        chunk = hits[c.source - 1].chunk
        if (chunk.file, chunk.page, words(c.quote)) not in seen:
            seen.add((chunk.file, chunk.page, words(c.quote)))
            sources.append(Source(chunk, c.quote.strip()))
    return Answer(question, True, reply.answer.strip(), sources, unverified, hits)


# ----------------------------------------------------------------------------- command line
def print_answer(a):
    if not a.found:
        print("\nI couldn't find this in your notes.\n")
    else:
        print(f"\n{a.text}\n")
        for s in a.sources:
            print(f"  📄 {s.chunk.file}, page {s.chunk.page}: \"{s.quote}\"")
        if not a.sources:
            print("  ⚠️  The AI gave no quote that could be found in your notes. Check this answer yourself.")
        print()
    print("Searched:", ", ".join(f"{h.chunk.file} p.{h.chunk.page} ({h.score:.2f})" for h in a.hits))


def main():
    parser = argparse.ArgumentParser(description="Ask a question about your lecture PDFs.")
    parser.add_argument("question")
    parser.add_argument("pdfs", nargs="+", help="one or more PDF files")
    parser.add_argument("--model", default=CHAT_MODEL, help=f"Ollama chat model (default: {CHAT_MODEL})")
    args = parser.parse_args()
    try:
        library = Library()
        for pdf in map(Path, args.pdfs):
            library.add_pdf(pdf.name, pdf.read_bytes())
        print_answer(ask(library, args.question, args.model))
    except NotesError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
