"""Second-Brain Agent web page. Start it from this folder with:

    uv run streamlit run app.py

then open http://localhost:8501 in your browser.
"""
import re
import shutil
from pathlib import Path

import streamlit as st

import brain

BADGE = {"linked": "🔗 linked already", "new": "✨ new connection", "none": "➖ not really connected"}


def md(text):
    """Show text exactly as written: Streamlit would read * _ $ [ ] and friends as formatting."""
    return re.sub(r"([\\`*_{}\[\]<>#|~$])", r"\\\1", text)


def use_samples():
    if not brain.SAMPLE_COPY.exists():
        shutil.copytree(brain.SAMPLE_VAULT, brain.SAMPLE_COPY)
    st.session_state.vault = str(brain.SAMPLE_COPY)


def open_brain(folder):
    """One Brain per notes folder, synced when it's opened. Unchanged notes cost nothing."""
    if st.session_state.get("brain_folder") != folder:
        mind = brain.Brain(folder)
        with st.spinner("Reading your notes…"):
            st.session_state.report = mind.sync()
        st.session_state.brain, st.session_state.brain_folder = mind, folder
        st.session_state.pop("answer", None)
        st.session_state.pop("found", None)
    return st.session_state.brain


def sync_now(mind):
    bar = st.sidebar.progress(0.0, text="Syncing…")
    try:
        st.session_state.report = mind.sync(progress=lambda done: bar.progress(done, text="Syncing…"))
    finally:
        bar.empty()


def show_answer(a):
    if a.queries:
        st.caption("The agent searched for: " + " · ".join(f"“{md(q)}”" for q in [a.question, *a.queries]))
    if not a.found:
        st.info("I couldn't find this in your notes. Try other words, or add a note about it.")
    else:
        st.markdown(md(a.text))
        for s in a.sources:
            st.caption(f"📝 **{md(s.title)}**: “{md(s.quote)}”")
        if not a.sources:
            st.warning("None of the AI's quotes could be found in your notes, so check this answer yourself.")
    with st.expander("🔎 What the search found"):
        for h in a.hits:
            st.markdown(f"**{md(h.title)}** · similarity {h.score:.2f}")
            st.caption(md(h.text[:300]) + ("…" if len(h.text) > 300 else ""))


def tab_ask(mind, tag):
    with st.form("ask"):
        question = st.text_input("Ask your notes", key="question", placeholder="For example: How can I use habits to keep up my German vocabulary?")
        planned = st.checkbox("Let the agent plan extra searches (better for questions about two topics)", value=True)
        asked = st.form_submit_button("Ask", type="primary")
    if asked:
        try:
            with st.spinner("Searching your notes…"):
                st.session_state.answer = brain.ask(mind, question, tag=tag, plan=planned)
        except brain.BrainError as e:
            st.error(str(e))
    if "answer" in st.session_state:
        show_answer(st.session_state.answer)


def tab_connections(mind):
    titles = sorted(n.title for n in mind.notes.values())
    title = st.selectbox("Pick a note", titles, key="pick")
    note = mind.note(title)
    with st.expander("Show the note"):
        st.markdown(note.text)
    if st.button("Find connections", type="primary"):
        try:
            with st.spinner("The agent is comparing this note with the closest ones…"):
                st.session_state.found = brain.connections(mind, note.path)
        except brain.BrainError as e:
            st.error(str(e))
    found = st.session_state.get("found")
    if not found or found[0].path != note.path:
        return
    chosen = []
    for n, c in enumerate(found[1]):
        kind = "linked" if c.already_linked else "new" if c.connected else "none"
        with st.container(border=True):
            st.markdown(f"**{md(c.note.title)}** · similarity {c.score:.2f} · {BADGE[kind]}")
            if c.connected and c.idea:
                st.markdown(md(c.idea))
                if c.checked:
                    st.caption(f"From “{md(note.title)}”: “{md(c.quote_here)}”  \nFrom “{md(c.note.title)}”: “{md(c.quote_there)}”")
                else:
                    st.caption("⚠️ The AI's quotes couldn't be found in the notes, so read both notes yourself.")
            if kind == "new" and st.checkbox(f"Link “{c.note.title}” from this note", key=f"link-{note.path}-{n}"):
                chosen.append(c.note.path)
    if chosen and st.button(f"Add {len(chosen)} link{'s' if len(chosen) > 1 else ''} to “{note.title}”"):
        added = brain.add_links(mind, note.path, chosen)
        st.session_state.pop("found", None)
        st.success(f"Added under “## Related” in {note.path}: " + ", ".join(f"[[{t}]]" for t in added)
                   + ". Nothing else in the note was changed.")


st.set_page_config(page_title="Second-Brain Agent", page_icon="🧠", layout="wide")
st.title("🧠 Second-Brain Agent")
st.caption(
    "Search your notes by meaning, ask them questions and discover connections between ideas. It runs on your "
    f"own computer with a local vector database (ChromaDB) and Ollama ({brain.CHAT_MODEL}), so nothing is sent anywhere."
)

with st.sidebar:
    st.header("Your notes")
    folder = st.text_input("Folder with Markdown notes (an Obsidian vault works)", key="vault",
                           placeholder="/Users/you/Documents/Notes")
    st.button("Use the sample notes", on_click=use_samples)

if not folder:
    st.info("Choose the folder with your notes on the left, or try the sample notes first.")
    st.stop()

try:
    mind = open_brain(str(Path(folder).expanduser()))
except brain.BrainError as e:
    st.error(str(e))
    st.stop()

with st.sidebar:
    if st.button("Sync again", help="After you add, change or delete notes"):
        sync_now(mind)
    r = st.session_state.report
    st.caption(f"**{len(mind.notes)} notes** in the vector database. Last sync: {len(r.added)} new, "
               f"{len(r.updated)} changed, {len(r.removed)} removed, {r.unchanged} unchanged.")
    tag = st.selectbox("Only search notes tagged", ["", *mind.tags], format_func=lambda t: f"#{t}" if t else "all notes")

ask_tab, connect_tab = st.tabs(["💬 Ask your notes", "🔗 Connections"])
with ask_tab:
    tab_ask(mind, tag or None)
with connect_tab:
    tab_connections(mind)
