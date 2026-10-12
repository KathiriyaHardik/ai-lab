"""Chat with Your Notes web page. Start it from this folder with:

    uv run streamlit run app.py

then open http://localhost:8501 in your browser.
"""
import re

import streamlit as st

import notes

SAMPLES = notes.HERE / "samples"
AVATAR = {"user": "🧑‍🎓", "assistant": "📚"}


def md(text):
    """Show text exactly as written: Streamlit would read * _ $ [ ] and friends as formatting."""
    return re.sub(r"([\\`*_{}\[\]<>#|~$])", r"\\\1", text)


def add(library, name, data):
    bar = st.sidebar.progress(0.0, text=f"Reading {name}…")
    try:
        library.add_pdf(name, data, progress=lambda done: bar.progress(done, text=f"Reading {name}…"))
    finally:
        bar.empty()


def load_library():
    """One library per browser tab. PDFs added earlier wait in my-notes/ with their embeddings cached."""
    if "library" not in st.session_state:
        library = notes.Library()
        for pdf in sorted(notes.NOTES_DIR.glob("*.pdf")):
            add(library, pdf.name, pdf.read_bytes())
        st.session_state.library = library
        st.session_state.chat = []
        st.session_state.upload_key = 0
    return st.session_state.library


def show(answer):
    if not answer.found:
        st.markdown("I couldn't find this in your notes. Try other words, or add the PDF that covers it.")
    else:
        st.markdown(md(answer.text))
        for s in answer.sources:
            st.caption(f"📄 **{md(s.chunk.file)}**, page {s.chunk.page}: “{md(s.quote)}”")
        if not answer.sources:
            st.warning("None of the AI's quotes could be found in your notes, so check this answer yourself.")
    with st.expander("🔎 What the search found"):
        for h in answer.hits:
            st.markdown(f"**{md(h.chunk.file)}**, page {h.chunk.page} · similarity {h.score:.2f}")
            st.caption(md(h.chunk.text[:300]) + ("…" if len(h.chunk.text) > 300 else ""))


st.set_page_config(page_title="Chat with Your Notes", page_icon="📚", layout="wide")
st.title("📚 Chat with Your Notes")
st.caption(
    "Ask questions about your lecture PDFs. Every answer shows the file and page it comes from. "
    f"It runs on your own computer with Ollama ({notes.CHAT_MODEL}), so nothing is sent to the internet."
)

try:
    library = load_library()
except notes.NotesError as e:
    st.error(str(e))
    st.stop()

with st.sidebar:
    st.header("Your notes")
    uploads = st.file_uploader(
        "Add lecture PDFs", type="pdf", accept_multiple_files=True, key=f"upload-{st.session_state.upload_key}"
    )
    if uploads:
        notes.NOTES_DIR.mkdir(exist_ok=True)
        try:
            for upload in uploads:
                (notes.NOTES_DIR / upload.name).write_bytes(upload.getvalue())
                add(library, upload.name, upload.getvalue())
        except notes.NotesError as e:
            st.error(str(e))
        else:
            st.session_state.upload_key += 1  # empties the upload box
            st.rerun()

    if st.button("Add the sample lecture notes"):
        try:
            for pdf in sorted(SAMPLES.glob("*.pdf")):
                add(library, pdf.name, pdf.read_bytes())
        except notes.NotesError as e:
            st.error(str(e))

    for name in list(library.files):
        text, remove = st.columns([5, 1], vertical_alignment="center")
        # a zero-width space after each _ lets long file names wrap there instead of mid-word
        text.markdown(f"**{md(name).replace('_', '_' + chr(0x200B))}**  \n{library.pages(name)} pages")
        if remove.button("", icon=":material/delete:", key=f"remove-{name}", help=f"Remove {name}"):
            library.remove(name)
            (notes.NOTES_DIR / name).unlink(missing_ok=True)
            st.rerun()

    if st.session_state.chat and st.button("Clear the chat"):
        st.session_state.chat = []
        st.rerun()
    st.caption("Each question is answered on its own, so ask complete questions.")

if not library.files:
    st.info("Add your lecture PDFs on the left, or try the sample lecture notes first.")

for question, answer in st.session_state.chat:
    with st.chat_message("user", avatar=AVATAR["user"]):
        st.markdown(md(question))
    with st.chat_message("assistant", avatar=AVATAR["assistant"]):
        show(answer)

if question := st.chat_input("Ask a question about your notes", disabled=not library.files):
    with st.chat_message("user", avatar=AVATAR["user"]):
        st.markdown(md(question))
    with st.chat_message("assistant", avatar=AVATAR["assistant"]):
        try:
            with st.spinner("Searching your notes…"):
                answer = notes.ask(library, question)
        except notes.NotesError as e:
            st.error(str(e))
        else:
            show(answer)
            # saved together and only now: a question interrupted by the next one leaves no half entry
            st.session_state.chat.append((question, answer))
