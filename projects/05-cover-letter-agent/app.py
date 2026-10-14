"""Cover Letter Agent web page. Start it from this folder with:

    uv run streamlit run app.py

then open http://localhost:8501 in your browser.
"""
import io
import re
from pathlib import Path

import streamlit as st

import agent

SAMPLES = Path(__file__).with_name("samples")
STEPS = ["1 · Job and CV", "2 · Check the plan ✋", "3 · Check the draft ✋", "4 · Done"]


def md(text):
    """Show text exactly as written: Streamlit would read * _ $ [ ] and friends as formatting."""
    return re.sub(r"([\\`*_{}\[\]<>#|~$])", r"\\\1", text)


def read_upload(upload):
    if upload.name.lower().endswith(".pdf"):
        from pypdf import PdfReader

        return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(upload.getvalue())).pages)
    return upload.getvalue().decode("utf-8", errors="replace")


def load_example():
    st.session_state.job_ad = (SAMPLES / "job_ai_engineer_en.txt").read_text()
    st.session_state.cv_text = (SAMPLES / "cv_ai_student.txt").read_text()


def run(step, label):
    """Run one agent step with a spinner; show problems the user can fix instead of crashing."""
    try:
        with st.spinner(label):
            step()
    except agent.AgentError as e:
        st.error(str(e))
        return False
    return True


def start_over():
    st.session_state.pop("app", None)


def show_steps(current):
    st.markdown("  →  ".join(f"**{s}**" if n == current else s for n, s in enumerate(STEPS)))
    st.divider()


def step_input():
    st.button("Try it with an example", on_click=load_example)
    left, right = st.columns(2, gap="large")
    with left:
        job_ad = st.text_area("**The job ad**", key="job_ad", height=330, placeholder="Paste the full job ad, in English or German.")
        kind = st.radio("What should the agent write?", list(agent.KINDS), horizontal=True,
                        format_func=lambda k: "Cover letter" if k == "cover_letter" else "Short recruiter message")
        language = st.radio("Language", list(agent.LANGUAGES), horizontal=True)
    with right:
        upload = st.file_uploader("**Your CV** (PDF or text file)", type=["pdf", "txt", "md"])
        cv_text = st.text_area("…or paste your CV as text", key="cv_text", height=180, disabled=upload is not None)
        notes = st.text_area("Anything else the agent may use? (optional)", height=90,
                             placeholder="For example: I can start on 1 March 2027. I'm moving to Stuttgart.")
    if st.button("Make a plan", type="primary", width="stretch"):
        def step():
            app = agent.start(job_ad, read_upload(upload) if upload else cv_text, kind, language, notes)
            st.session_state.app = agent.make_plan(app)
        if run(step, "The agent is reading the job ad and your CV…"):
            st.rerun()


def step_plan(app):
    p = app.plan
    st.subheader(f"Plan: {md(p.job_title)} at {md(p.company)}")
    st.caption("The agent will only write about what you keep here. Every strength comes with proof from your CV.")
    for n, s in enumerate(app.strengths):
        label = f"**{md(s.point)}** · the job asks for: {md(s.requirement)}"
        s.use = st.checkbox(label, value=s.use, key=f"use-{n}", disabled=not s.verified)
        st.caption(f"From your CV: “{md(s.evidence)}”" if s.verified else
                   f"⚠️ Not used: these words are not in your CV: “{md(s.evidence)}”")
    if p.gap:
        st.markdown(f"**Gap:** {md(p.gap)}")
        app.address_gap = st.checkbox("Address this gap honestly in the text", value=app.address_gap, key="keep-gap")
        p.gap_answer = st.text_input("How", value=p.gap_answer, key="gap-answer", disabled=not app.address_gap)
    if p.motivation:
        st.markdown(f"**Why this job:** {md(p.motivation)}")
    app.notes = st.text_area("Your notes for the agent", value=app.notes, key="plan-notes", height=80)
    approve, back = st.columns([3, 1])
    if approve.button("Approve the plan and write", type="primary", width="stretch"):
        if run(lambda: agent.write_draft(app), "The agent is writing, checking and fixing…"):
            st.rerun()
    back.button("Start over", on_click=start_over, width="stretch")


def step_draft(app):
    v = app.latest
    text_column, side = st.columns([3, 2], gap="large")
    with text_column:
        st.subheader("The draft")
        st.code(v.draft.text, language=None, wrap_lines=True)
        if v.problems:
            st.warning("The agent couldn't fix everything. Still wrong:\n" + "\n".join(f"- {md(p)}" for p in v.problems))
        else:
            st.success("All checks passed: length, company and job title, greeting and closing, language, "
                       "no placeholders, no numbers that aren't in your CV, the job ad or your notes.")
        with st.expander(f"🤖 What the agent did ({len(app.versions)} version{'s' if len(app.versions) > 1 else ''})"):
            for n, old in enumerate(app.versions, 1):
                st.markdown(f"**Version {n}** · {md(old.why)}")
                st.caption("Python's check: " + ("all passed" if not old.problems else " · ".join(md(p) for p in old.problems)))
    with side:
        st.subheader("Your decision")
        feedback = st.text_area("What should change?", key="feedback", height=120,
                                placeholder="For example: shorter, more formal, mention that I can start on 1 March 2027")
        if st.button("Revise", width="stretch"):
            if run(lambda: agent.revise(app, feedback), "The agent is revising, checking and fixing…"):
                st.rerun()
        if st.button("✅ Approve this version", type="primary", width="stretch"):
            st.session_state.saved = agent.approve(app)
            st.rerun()
        st.button("Start over", on_click=start_over, width="stretch")


def step_done(app):
    st.success(f"Approved and saved to {md(str(st.session_state.get('saved', agent.LETTERS_DIR)))}. "
               "Nothing was sent: copy it or download it, and send it yourself.")
    st.code(app.latest.draft.text, language=None, wrap_lines=True)
    stem = Path(str(st.session_state.get("saved", "letter.txt"))).stem
    txt, docx, again = st.columns(3)
    txt.download_button("Download .txt", app.latest.draft.text, f"{stem}.txt", width="stretch")
    docx.download_button("Download Word file", agent.to_docx(app), f"{stem}.docx", width="stretch")
    again.button("Write another", on_click=start_over, width="stretch")


st.set_page_config(page_title="Cover Letter Agent", page_icon="✍️", layout="wide")
st.title("✍️ Cover Letter Agent")
st.caption(
    "It plans, writes, checks and fixes a cover letter or recruiter message, and waits for your OK at every step. "
    f"It runs on your own computer with Ollama ({agent.MODEL}), and nothing is ever sent anywhere."
)

app = st.session_state.get("app")
if app is None:
    show_steps(0)
    step_input()
elif not app.versions:
    show_steps(1)
    step_plan(app)
elif not app.approved:
    show_steps(2)
    step_draft(app)
else:
    show_steps(3)
    step_done(app)
