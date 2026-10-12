"""German Letter Explainer web page. Start it from this folder with:

    uv run streamlit run app.py

then open http://localhost:8501 in your browser.
"""
import re
from pathlib import Path

import streamlit as st

import letters

SAMPLES = Path(__file__).with_name("samples")
EXAMPLES = {
    "Tax office: tax to pay (photo)": "letter1_finanzamt_photo.jpg",
    "Energy company: payment reminder (photo)": "letter2_stadtwerke_photo.jpg",
    "Immigration office: appointment (photo)": "letter3_auslaenderbehoerde_photo.jpg",
    "Health insurance: send documents (photo)": "letter4_krankenkasse_photo.jpg",
    "Health insurance: new card, nothing to do (photo)": "letter5_gesundheitskarte_photo.jpg",
}


def md(text):
    """Show text exactly as written: Streamlit would read * _ $ [ ] and friends as formatting."""
    return re.sub(r"([\\`*_{}\[\]<>#|~$])", r"\\\1", text)


def show_deadline(d):
    if d.before_event:
        when = f"**{d.period} before** the date it refers to"
    elif d.due is None:
        when = f"**within {d.period}**, counted from the day you received the letter"
    else:
        left = d.days_left
        left = "**today!**" if left == 0 else f"{left} days left" if left > 0 else f"**{-left} days ago!**"
        when = f"**{d.due:%d.%m.%Y}** · {left}"
        if d.safe_date:
            when += f"  \nTo be safe: within {d.period}, counted from the date on the letter. The legal deadline may be a few days later."
    title = ("Only if you disagree: " if d.kind == "objection" else "") + md(d.what or d.kind)
    body = f"{title}  \n{when}"
    if d.kind == "objection" or d.due is None:
        box = st.info
    elif d.days_left <= 7:
        box = st.error
    elif d.days_left <= 21:
        box = st.warning
    else:
        box = st.info
    box(body, icon="⏰")
    st.caption(f"From your letter: “{md(d.context)}”")


def show(r):
    st.subheader(f"{md(r.sender)}: {md(r.topic)}")
    to_pay, reference, dated = st.columns(3)
    to_pay.metric("💶 To pay", r.amount.text if r.amount else "nothing")
    reference.metric("🔖 Reference number", r.reference or "none found")
    dated.metric("📅 Letter date", f"{r.letter_date:%d.%m.%Y}" if r.letter_date else "not found")
    if r.reference and not r.reference_checked:
        st.warning("I couldn't find this reference number in the letter's text. Check it on your letter.")

    left, right = st.columns([3, 2], gap="large")
    with left:
        st.markdown("#### In simple words")
        st.markdown(md(r.summary))
        st.markdown("#### What you need to do")
        st.markdown("\n".join(f"{n}. {md(a)}" for n, a in enumerate(r.actions, 1)) if r.actions else "Nothing.")
        if r.if_ignored:
            st.markdown("#### If you ignore it")
            st.markdown(md(r.if_ignored))
    with right:
        st.markdown("#### Deadlines")
        if not r.deadlines:
            st.success("No deadline in this letter.")
        for d in r.deadlines:
            show_deadline(d)
        for what, date, _ in r.valid_until:
            st.caption(f"📅 {md(what)}: valid until **{date:%d.%m.%Y}**")

    with st.expander("📄 What the tool read from your letter"):
        st.caption("Compare this with your letter. Dates and amounts above come from this text, never from the AI's memory.")
        for page in r.pages:
            picture, text = st.columns(2) if page.image else (None, st.container())
            if picture:
                picture.image(page.image, caption=f"Page {page.number}")
            text.markdown(f"**Page {page.number}** ({'read by the AI' if page.read_by_ai else 'text from the PDF'})")
            text.code(page.text, language=None, wrap_lines=True)


st.set_page_config(page_title="German Letter Explainer", page_icon="✉️", layout="wide")
st.title("✉️ German Letter Explainer")
st.caption(
    "Take a photo of an official German letter. You get what it says, what to do and the deadline, in simple words. "
    f"It runs on your own computer with Ollama ({letters.MODEL}), so your letter is not sent to the internet."
)

upload, options = st.columns([3, 2], gap="large")
with upload:
    files = st.file_uploader(
        "Photos or a PDF of your letter (all pages, in order)", type=[*letters.IMAGE_TYPES, "pdf"],
        accept_multiple_files=True,
    )
with options:
    language = st.selectbox("Explain it in", list(letters.LANGUAGES))
    example = st.selectbox("…or try an example letter", ["", *EXAMPLES], format_func=lambda x: x or "No example")

if st.button("Explain the letter", type="primary", width="stretch"):
    chosen = [(f.name, f.getvalue()) for f in files] if files else []
    if not chosen and example:
        chosen = [(EXAMPLES[example], (SAMPLES / EXAMPLES[example]).read_bytes())]
    try:
        with st.status("Reading your letter…") as status:
            result = letters.check_letter(chosen, language, progress=lambda message: status.update(label=message))
            status.update(label="Done", state="complete")
    except letters.LetterError as e:
        st.error(str(e))
    else:
        st.session_state.result = result  # stays when you open the expander or change a menu

if "result" in st.session_state:
    show(st.session_state.result)

st.divider()
st.caption(
    "⚖️ This is not legal advice. The AI can be wrong. For important letters (tax office, immigration office, court, "
    "debt collection), get help from a counselling service such as a Migrationsberatung or Verbraucherzentrale, "
    "a Lohnsteuerhilfeverein for tax, or a lawyer."
)
