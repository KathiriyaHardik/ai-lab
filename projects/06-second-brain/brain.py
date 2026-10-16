#!/usr/bin/env python3
"""Second-Brain Agent: search your notes by meaning, ask them questions, and discover connections.

    uv run brain.py sync
    uv run brain.py ask "How can I use habits to keep up my German vocabulary?"
    uv run brain.py related "Spaced repetition"
    uv run brain.py ask "..." --vault ~/Documents/MyVault      # your own Markdown notes (Obsidian works)

How it works:
  * SYNC     every Markdown note is cut into chunks, each chunk gets an embedding (EmbeddingGemma),
             and they go into a vector database (ChromaDB) on this computer. Only new or changed notes
             are processed again, and deleted notes are removed
  * ASK      the agent plans 1-3 search queries for your question (one per topic), the vector database
             finds the closest chunks for each, and the AI answers from those only, quoting them.
             Python checks every quote. If the notes don't cover it, it says so
  * RELATED  the database finds the notes closest to one note. The AI says in one sentence how each one
             connects, with a quote from both notes (checked by Python), and you see which connections
             you haven't linked yet
  * LINK     you choose which new connections to add; the note gets a [[link]] under "## Related".
             Notes are only ever added to, never changed or deleted

Everything runs on this computer with Ollama and ChromaDB. Nothing is sent to the internet.
"""
import argparse
import hashlib
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import chromadb
import numpy as np
import ollama
from chromadb.config import Settings
from pydantic import BaseModel, Field, ValidationError, create_model

CHAT_MODEL = os.environ.get("BRAIN_MODEL", "gemma4:e4b")
EMBED_MODEL = os.environ.get("BRAIN_EMBED_MODEL", "embeddinggemma")
HERE = Path(__file__).resolve().parent
SAMPLE_VAULT = HERE / "samples" / "vault"
SAMPLE_COPY = HERE / "my-sample-vault"  # the web page works on a copy, so adding links never changes the samples
DB_DIR = HERE / ".vectordb"  # the vector database (git-ignored)
NOTE_TYPES = (".md", ".markdown", ".txt")

CHUNK_CHARS = 1200  # most notes are one chunk; long ones are cut at headings and paragraphs
PER_QUERY = 4  # chunks per search query
MAX_QUERIES = 3
MAX_EXCERPTS = 8  # chunks the AI reads to answer
EMBED_BATCH = 16
CONTEXT_TOKENS = 16384
MAX_ANSWER_TOKENS = 1536
STUCK_WHITESPACE = 200  # this much blank space in a row means the model is stuck (see project 05)
TEMPERATURES = (0, 0.3, 0.7)

PLAN_PROMPT = """You plan searches in a person's notes. Write 1 to 3 short search queries that together find
every note needed to answer the question. If the question is about two topics, write one query per topic.
Use the important words of the question. Don't answer the question."""

ANSWER_PROMPT = """You answer a question from a person's own notes. You get numbered excerpts (each with the
note's title) and the question.

Use ONLY the excerpts. Don't add anything you know from elsewhere.
- found: true if the excerpts contain the answer, false if they don't.
- citations: for every excerpt you use, its number and the exact words from it that support the answer,
  at most 25 words, copied character for character. Empty when found is false.
- answer: the answer in 1 to 5 sentences, in the same language as the question. If the answer combines
  two notes, say how they fit together. Empty when found is false."""

CONNECT_PROMPT = """You look at two of a person's notes and find how they connect.

- connected: true if the two notes really share an idea or can be used together, false if they only
  share general words.
- quote_first: exact words from the FIRST note that the connection is about, at most 15 words.
- quote_second: exact words from the SECOND note that the connection is about, at most 15 words.
- idea: one sentence in plain English: what from one note helps with the other, or how to use the two
  together. Refer to the notes by their titles.
Quote content, not lines that are only links like "See [[...]]". Copy the quotes character for character.
If they are not connected, leave the quotes and the idea empty."""


class BrainError(Exception):
    """A problem the user can fix (no notes, Ollama not running, a model missing...)."""


# ----------------------------------------------------------------------------- reading notes
@dataclass
class Note:
    path: str  # relative to the vault, with "/"
    title: str
    text: str  # without the front matter
    tags: set
    links: set  # [[link]] targets, lower case
    hash: str

    @property
    def stem(self):
        return Path(self.path).stem


TAG_RE = re.compile(r"(?<![\w&#/])#([A-Za-zÄÖÜäöüß][\w/-]*)")
LINK_RE = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")


def parse_note(path, raw):
    """Title, text, tags (front matter and #tags) and [[links]] of one Markdown note."""
    text, tags = raw.replace("\r\n", "\n"), set()
    front = re.match(r"---\n(.*?)\n---\n?", text, re.DOTALL)
    if front:
        text = text[front.end():]
        lines = front.group(1).splitlines()
        for n, line in enumerate(lines):
            key, _, value = line.partition(":")
            if key.strip().lower() not in ("tags", "tag"):
                continue
            items = re.findall(r"[\w/-]+", value)
            items += [item.strip(" -") for item in lines[n + 1:] if re.match(r"\s*-\s", item)] if not value.strip() else []
            tags |= {t.lower().lstrip("#") for t in items if t}
    tags |= {t.lower() for t in TAG_RE.findall(re.sub(r"`[^`]*`", "", text))}
    heading = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    title = heading.group(1).strip() if heading else Path(path).stem
    links = {m.strip().lower() for m in LINK_RE.findall(text)}
    return Note(path, title, text.strip(), tags, links, hashlib.sha256(raw.encode()).hexdigest()[:16])


def read_vault(folder):
    folder = Path(folder).expanduser()
    if not folder.is_dir():
        raise BrainError(f"The folder {folder} doesn't exist.")
    notes = {}
    for file in sorted(folder.rglob("*")):
        relative = file.relative_to(folder)
        if file.suffix.lower() in NOTE_TYPES and file.is_file() and not any(p.startswith(".") for p in relative.parts):
            notes[relative.as_posix()] = parse_note(relative.as_posix(), file.read_text(errors="replace"))
    return notes


def chunks(note, size=CHUNK_CHARS):
    """Short notes stay whole. Long ones are cut at headings, then paragraphs, into pieces of at most `size`."""
    if len(note.text) <= size:
        return [note.text]
    pieces, current = [], ""
    for block in re.split(r"\n(?=#{1,6}\s)|\n\s*\n", note.text):
        block = block.strip()
        while len(block) > size:  # one very long paragraph: cut at a sentence end if possible
            cut = block.rfind(". ", 0, size)
            cut = cut + 1 if cut > size // 2 else size
            pieces.append(block[:cut].strip())
            block = block[cut:].strip()
        if current and len(current) + len(block) + 2 > size:
            pieces.append(current)
            current = ""
        current = f"{current}\n\n{block}".strip()
    if current:
        pieces.append(current)
    return [p for p in pieces if p]


# ----------------------------------------------------------------------------- text checks
def words(text):
    """Lower-case words only, so line breaks, quotes and dashes don't block a match."""
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).lower()))


def quote_in(quote, text):
    """True if every part of the quote (split at "...") appears word for word in the text."""
    parts = [p for p in (words(p) for p in re.split(r"\.\.\.|…", quote)) if p]
    haystack = f" {words(text)} "
    return bool(parts) and all(f" {p} " in haystack for p in parts)


# ----------------------------------------------------------------------------- talking to Ollama
def ollama_call(model, call):
    """Run one Ollama request and turn its errors into messages the user can act on."""
    try:
        return call()
    except ConnectionError as e:
        raise BrainError("Ollama is not running. Open the Ollama app, then try again.") from e
    except ollama.ResponseError as e:
        if e.status_code == 404:
            raise BrainError(f"The AI model {model} is not downloaded yet. Run:  ollama pull {model}") from e
        if "structured output" in str(e.error):
            raise BrainError("This Ollama install can't force the answer into JSON. Install the official app "
                             "from ollama.com (Homebrew: brew install --cask ollama-app).") from e
        raise BrainError(f"Ollama returned an error: {e.error}") from e


def embedding_input(text, kind, model, title="none"):
    """EmbeddingGemma was trained with these task prefixes; other models get the plain text."""
    if not model.startswith("embeddinggemma"):
        return text
    return f"task: search result | query: {text}" if kind == "query" else f"title: {title} | text: {text}"


def embed(client, model, texts, kind, titles=None, progress=None):
    """Unit-length embeddings (rows), so a dot product is the cosine similarity."""
    titles = titles or ["none"] * len(texts)
    rows = []
    for start in range(0, len(texts), EMBED_BATCH):
        batch = [embedding_input(t, kind, model, title) for t, title in
                 zip(texts[start:start + EMBED_BATCH], titles[start:start + EMBED_BATCH])]
        rows += ollama_call(model, lambda: client.embed(model=model, input=batch)).embeddings
        if progress:
            progress(min(start + EMBED_BATCH, len(texts)) / len(texts))
    vectors = np.array(rows, dtype=np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def stream(client, model, messages, shape, temperature):
    """The reply, read as it arrives. None if the model gets stuck writing blank space: stop at once."""
    text = ""
    chunks_ = ollama_call(model, lambda: client.chat(
        model=model, messages=messages, format=shape.model_json_schema(), think=False, stream=True,
        options={"temperature": temperature, "num_ctx": CONTEXT_TOKENS, "num_predict": MAX_ANSWER_TOKENS},
    ))
    for chunk in chunks_:
        text += chunk.message.content or ""
        if len(text) - len(text.rstrip()) > STUCK_WHITESPACE:
            return None
    return text


def ask_json(client, model, system, user, shape):
    """One AI call whose reply is forced into the JSON shape of the pydantic model `shape`."""
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
    raise BrainError(f"The AI's answer was not in the expected format, {len(TEMPERATURES)} times. Try again.\n\n{problem}")


# ----------------------------------------------------------------------------- the brain: notes + vector database
@dataclass
class Hit:
    path: str
    title: str
    text: str
    score: float  # cosine similarity, -1..1
    id: str = ""


@dataclass
class SyncReport:
    added: list
    updated: list
    removed: list
    unchanged: int


class Brain:
    def __init__(self, vault=SAMPLE_VAULT, db_dir=None, client=None, embed_model=None):
        self.vault = Path(vault).expanduser().resolve()
        self.client = client or ollama.Client()
        self.embed_model = embed_model or EMBED_MODEL
        db = chromadb.PersistentClient(path=str(db_dir or DB_DIR), settings=Settings(anonymized_telemetry=False))
        # one collection per vault and embedding model, so switching either never mixes up vectors
        key = hashlib.sha256(f"{self.vault}|{self.embed_model}".encode()).hexdigest()[:12]
        self.col = db.get_or_create_collection(f"notes_{key}", embedding_function=None, metadata={"hnsw:space": "cosine"})
        self.notes = {}

    # -- keeping the database in step with the folder
    def sync(self, progress=None):
        notes = read_vault(self.vault)
        stored = {m["path"]: m["hash"] for m in self.col.get(include=["metadatas"])["metadatas"]}
        removed = [p for p in stored if p not in notes]
        changed = [p for p, n in notes.items() if stored.get(p) != n.hash]
        for path in removed + changed:
            self.col.delete(where={"path": path})
        items = [(notes[p], i, piece) for p in changed for i, piece in enumerate(chunks(notes[p]))]
        if items:
            vectors = embed(self.client, self.embed_model, [piece for _, _, piece in items], "document",
                            titles=[n.title for n, _, _ in items], progress=progress)
            self.col.add(
                ids=[f"{n.path}::{i}" for n, i, _ in items],
                embeddings=vectors.tolist(),
                documents=[piece for _, _, piece in items],
                metadatas=[{"path": n.path, "title": n.title, "hash": n.hash, "chunk": i,
                            **{f"tag_{t}": True for t in n.tags}} for n, i, _ in items],
            )
        self.notes = notes
        return SyncReport([p for p in changed if p not in stored], [p for p in changed if p in stored], removed,
                          len(notes) - len(changed))

    @property
    def tags(self):
        return sorted({t for n in self.notes.values() for t in n.tags})

    def note(self, name):
        """A note by path, title or file name (case doesn't matter)."""
        name = name.strip().lower()
        for n in self.notes.values():
            if name in (n.path.lower(), n.title.lower(), n.stem.lower()):
                return n
        raise BrainError(f'There is no note called "{name}".')

    # -- searching
    def search(self, query, k=PER_QUERY, tag=None):
        count = self.col.count()
        if not count:
            raise BrainError("There are no notes in the database yet. Sync first.")
        vector = embed(self.client, self.embed_model, [query], "query")
        found = self.col.query(query_embeddings=vector.tolist(), n_results=min(k, count),
                               where={f"tag_{tag}": True} if tag else None, include=["documents", "metadatas", "distances"])
        return [Hit(m["path"], m["title"], doc, 1 - dist, cid) for cid, m, doc, dist in
                zip(found["ids"][0], found["metadatas"][0], found["documents"][0], found["distances"][0])]

    def related(self, name, k=5):
        """The notes closest to one note: its chunks' average embedding against every chunk, best per note."""
        note = self.note(name)
        mine = self.col.get(where={"path": note.path}, include=["embeddings"])["embeddings"]
        if mine is None or len(mine) == 0:
            raise BrainError(f'"{note.title}" isn\'t in the database yet. Sync first.')
        center = np.mean(np.array(mine), axis=0)
        center = center / np.linalg.norm(center)
        found = self.col.query(query_embeddings=[center.tolist()], n_results=min(self.col.count(), 60),
                               include=["metadatas", "distances"])
        best = {}
        for m, dist in zip(found["metadatas"][0], found["distances"][0]):
            if m["path"] != note.path and m["path"] not in best:
                best[m["path"]] = 1 - dist
        return [(self.notes[p], score) for p, score in sorted(best.items(), key=lambda x: -x[1])[:k] if p in self.notes]


# ----------------------------------------------------------------------------- asking
class SearchPlan(BaseModel):
    queries: list[str] = Field(min_length=1, description="1 to 3 short search queries")


class Citation(BaseModel):
    source: int = Field(description="Number of the excerpt, for example 2 for [2]")
    quote: str = Field(description="Exact words copied from that excerpt")


class AIAnswer(BaseModel):
    found: bool
    citations: list[Citation]  # before the answer, so the AI picks its evidence first
    answer: str


@dataclass
class Source:
    path: str
    title: str
    quote: str


@dataclass
class Answer:
    question: str
    queries: list
    found: bool
    text: str
    sources: list = field(default_factory=list)  # checked quotes only
    unverified: int = 0
    hits: list = field(default_factory=list)

    @property
    def checked(self):
        return self.found and bool(self.sources)


def plan_queries(brain, question, model=CHAT_MODEL):
    plan = ask_json(brain.client, model, PLAN_PROMPT, f"QUESTION: {question}", SearchPlan)
    queries, seen = [], {words(question)}
    for q in plan.queries:
        if words(q) and words(q) not in seen:
            seen.add(words(q))
            queries.append(q.strip())
    return queries[:MAX_QUERIES]


def ask(brain, question, model=CHAT_MODEL, tag=None, plan=True):
    """Plan searches (or just search the question), answer from the notes found, check every quote."""
    question = (question or "").strip()
    if not question:
        raise BrainError("Type a question first.")
    queries = [question] + (plan_queries(brain, question, model) if plan else [])
    best = {}
    for q in queries:  # merge the results of every query, each chunk once with its best score
        for hit in brain.search(q, PER_QUERY, tag):
            if hit.id not in best or hit.score > best[hit.id].score:
                best[hit.id] = hit
    hits = sorted(best.values(), key=lambda h: -h.score)[:MAX_EXCERPTS]
    excerpts = "\n\n".join(f"[{i}] {h.title}\n{h.text}" for i, h in enumerate(hits, 1))
    reply = ask_json(brain.client, model, ANSWER_PROMPT, f"EXCERPTS:\n\n{excerpts}\n\nQUESTION: {question}", AIAnswer)
    if not reply.found:
        return Answer(question, queries[1:], False, "", hits=hits)
    sources, seen, unverified = [], set(), 0
    for c in reply.citations:
        if not (1 <= c.source <= len(hits) and quote_in(c.quote, hits[c.source - 1].text)):
            unverified += 1
            continue
        h = hits[c.source - 1]
        if (h.path, words(c.quote)) not in seen:
            seen.add((h.path, words(c.quote)))
            sources.append(Source(h.path, h.title, c.quote.strip()))
    return Answer(question, queries[1:], True, reply.answer.strip(), sources, unverified, hits)


# ----------------------------------------------------------------------------- connecting
class Link(BaseModel):  # one call per pair: with four related notes in one call, the model mixed them up
    connected: bool
    quote_first: str = Field(description="Exact words from the first note")
    quote_second: str = Field(description="Exact words from the second note")
    idea: str = Field(description="One sentence on how the two notes connect")


@dataclass
class Connection:
    note: Note
    score: float
    already_linked: bool  # one of the two notes already has a [[link]] to the other
    connected: bool = True  # the AI's judgement: a real connection, or only shared words
    idea: str = ""
    quote_here: str = ""
    quote_there: str = ""
    checked: bool = False  # both quotes are really in their notes


def linked(a, b):
    return bool({b.stem.lower(), b.title.lower()} & a.links or {a.stem.lower(), a.title.lower()} & b.links)


def connect(brain, note, other, model=CHAT_MODEL):
    """How two notes connect, in the AI's words, with a checked quote from each."""
    reply = ask_json(brain.client, model, CONNECT_PROMPT,
                     f"FIRST NOTE: {note.title}\n<<<\n{note.text[:2500]}\n>>>\n\n"
                     f"SECOND NOTE: {other.title}\n<<<\n{other.text[:2500]}\n>>>", Link)
    first, second = reply.quote_first.strip(), reply.quote_second.strip()
    if not (quote_in(first, note.text) and quote_in(second, other.text)) and \
            quote_in(second, note.text) and quote_in(first, other.text):
        first, second = second, first  # the model sometimes swaps the two; both are still checked word for word
    checked = reply.connected and quote_in(first, note.text) and quote_in(second, other.text)
    return reply.connected, reply.idea.strip(), first, second, checked


def connections(brain, name, k=4, model=CHAT_MODEL):
    """The closest notes to one note, each with how it connects, and whether you have linked them yet."""
    note = brain.note(name)
    result = []
    for other, score in brain.related(note.path, k):
        connected, idea, first, second, checked = connect(brain, note, other, model)
        result.append(Connection(other, score, linked(note, other), connected, idea, first, second, checked))
    return note, result


def add_links(brain, name, targets):
    """Add [[links]] under "## Related" at the end of a note. Only adds, never changes or removes anything."""
    note = brain.note(name)
    file = (brain.vault / note.path).resolve()
    if brain.vault not in file.parents:
        raise BrainError("That note is outside the notes folder.")
    new = [t for t in (brain.note(t) for t in targets) if t.path != note.path and not linked(note, t)]
    if not new:
        return []
    raw = file.read_text(errors="replace")
    lines = "\n".join(f"- [[{t.stem}]]" for t in new)
    if re.search(r"^## Related\s*$", raw, re.MULTILINE) and raw.rstrip().splitlines()[-1].startswith(("- [[", "## Related")):
        raw = raw.rstrip() + "\n" + lines + "\n"
    else:
        raw = raw.rstrip() + "\n\n## Related\n" + lines + "\n"
    file.write_text(raw)
    brain.sync()  # the changed note gets new embeddings
    return [t.title for t in new]


# ----------------------------------------------------------------------------- command line
def main():
    parser = argparse.ArgumentParser(description="Search, ask and connect your notes.")
    parser.add_argument("command", choices=["sync", "ask", "related"])
    parser.add_argument("text", nargs="?", default="", help="the question, or the note's title")
    parser.add_argument("--vault", default=str(SAMPLE_VAULT), help="folder with Markdown notes")
    parser.add_argument("--tag", default=None, help="only search notes with this tag")
    parser.add_argument("--model", default=CHAT_MODEL)
    args = parser.parse_args()
    try:
        brain = Brain(args.vault)
        report = brain.sync()
        print(f"{len(brain.notes)} notes: {len(report.added)} new, {len(report.updated)} changed, "
              f"{len(report.removed)} removed, {report.unchanged} unchanged.")
        if args.command == "ask":
            a = ask(brain, args.text, args.model, args.tag)
            print(f"\nSearched for: {a.question}" + "".join(f" | {q}" for q in a.queries))
            print(f"\n{a.text if a.found else 'I could not find this in your notes.'}\n")
            for s in a.sources:
                print(f'  📝 {s.title}: "{s.quote}"')
        elif args.command == "related":
            note, found = connections(brain, args.text, model=args.model)
            print(f"\nConnections of {note.title}:\n")
            for c in found:
                status = "linked already" if c.already_linked else "✨ new connection" if c.connected else "not really connected"
                warning = "  ⚠️ quotes not found" if c.connected and not c.checked else ""
                print(f"  {c.note.title} ({c.score:.2f}, {status}){warning}" + (f"\n     {c.idea}" if c.connected else ""))
    except BrainError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
