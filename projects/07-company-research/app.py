"""Company Research Agent web page. Start it from this folder with:

    uv run streamlit run app.py

then open http://localhost:8501 in your browser.
"""
import io
import re
from pathlib import Path

import streamlit as st

import crawler
import team

SAMPLES = Path(__file__).with_name("samples")
EXAMPLES = {"Brightline Analytics (AI, English)": "brightline", "Nordlicht Software (public sector, German)": "nordlicht",
            "Neckar Robotics (robots, English)": "neckar-robotics"}
STEPS = ["1 · Company and you", "2 · Check the facts ✋", "3 · Check the message ✋", "4 · Done"]
VERDICT = {"supported": "✅ supported", "partly": "⚠️ only partly", "unsupported": "❌ not supported", "": ""}
UWG = ("In Germany, advertising emails to businesses usually need their consent first (§7 UWG). A LinkedIn note, or a "
       "person who asked to hear from you, is the safer way. This is not legal advice.")


def md(text):
    """Show text exactly as written: Streamlit would read * _ $ [ ] and friends as formatting."""
    return re.sub(r"([\\`*_{}\[\]<>#|~$])", r"\\\1", text)


@st.cache_resource
def sample_sites():
    """The three fictional company websites, served on this computer (started once)."""
    return crawler.serve_samples()


def read_upload(upload):
    if upload.name.lower().endswith(".pdf"):
        from pypdf import PdfReader

        return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(upload.getvalue())).pages)
    return upload.getvalue().decode("utf-8", errors="replace")


def use_example():
    choice = st.session_state.get("example")
    if choice:
        st.session_state.url = sample_sites()[EXAMPLES[choice]]
        st.session_state.profile = (SAMPLES / "cv_ai_student.txt").read_text()


def run(step, label):
    try:
        with st.status(label) as status:
            step(lambda message: status.update(label=message))
            status.update(label="Done", state="complete")
    except team.AgentError as e:
        st.error(str(e))
        return False
    return True


def start_over():
    st.session_state.pop("project", None)


def show_steps(current):
    st.markdown("  →  ".join(f"**{s}**" if n == current else s for n, s in enumerate(STEPS)))
    st.divider()


def show_log(project):
    with st.expander(f"🤖 What the team did ({len(project.log)} steps)", expanded=False):
        for agent, text in project.log:
            st.markdown(f"**{md(agent)}** · {md(text)}")


def step_input():
    left, right = st.columns(2, gap="large")
    with left:
        st.text_input("**The company's website**", key="url", placeholder="https://www.company.example")
        st.selectbox("…or try an example company (fictional, runs on your computer)", ["", *EXAMPLES], key="example",
                     on_change=use_example, format_func=lambda x: x or "No example")
        goal = st.radio("What do you want?", list(team.GOALS), horizontal=True,
                        format_func={"job": "A job", "networking": "Networking", "business": "Business"}.get)
        channel = st.radio("Message type", list(team.CHANNELS), horizontal=True,
                           format_func={"linkedin": "LinkedIn note (300 characters)", "email": "Email"}.get)
        language = st.radio("Language", list(team.LANGUAGES), horizontal=True)
        if goal == "business":
            st.warning(UWG)
    with right:
        upload = st.file_uploader("**Your CV or profile** (PDF or text)", type=["pdf", "txt", "md"])
        st.text_area("…or paste it", key="profile", height=150, disabled=upload is not None)
        name = st.text_input("Your name (for the email signature)", placeholder="taken from your CV if empty")
        contact = st.text_input("Who you write to, if you know (optional)", placeholder="e.g. Ms Example, Frau Beispiel")
        notes = st.text_input("Anything the agents may use (optional)", placeholder="e.g. I can start on 1 March 2027")
    if st.button("Start the research", type="primary", width="stretch"):
        def step(progress):
            profile = read_upload(upload) if upload else st.session_state.get("profile", "")
            project = team.start(st.session_state.get("url", ""), profile, name, goal, channel, language, contact, notes)
            local = project.url.startswith("http://127.0.0.1")
            st.session_state.project = team.research(project, pause=0 if local else crawler.PAUSE, progress=progress)
        if run(step, "The Scout is reading the website…"):
            st.rerun()


def step_facts(project):
    st.subheader(f"Facts about {md(project.company)}")
    st.caption("Every fact has a quote that Python found on the page. The Fact-checker gave a second opinion; only "
               "supported facts are ticked. The Strategist and the Writer only use what you keep ticked.")
    for f in project.facts:
        f.use = st.checkbox(f"{md(f.text)}  ·  {VERDICT[f.verdict]}", value=f.use, key=f"fact-{f.id}")
        st.caption(f"“{md(f.quote)}” · [{md(f.url)}]({f.url})" + (f"  \nFact-checker: {md(f.reason)}" if f.verdict != "supported" else ""))
    with st.expander(f"🕵️ What the Scout read ({len(project.crawl.pages)} pages) and left out ({len(project.crawl.skipped)})"):
        for p in project.crawl.pages:
            st.markdown(f"- ✅ [{md(p.title or p.url)}]({p.url})")
        for url, why in project.crawl.skipped:
            st.markdown(f"- ⏭️ {md(url)}: {md(why)}")
    show_log(project)
    write, back = st.columns([3, 1])
    if write.button("Use the ticked facts and write", type="primary", width="stretch"):
        if run(lambda progress: team.draft(project, progress=progress), "The Strategist is thinking…"):
            st.rerun()
    back.button("Start over", on_click=start_over, width="stretch")


def step_message(project):
    v = project.latest
    left, right = st.columns([3, 2], gap="large")
    with left:
        st.subheader(f"Your {team.CHANNELS[project.channel]['name']}")
        st.code(v.draft.text(project.channel), language=None, wrap_lines=True)
        unit = team.CHANNELS[project.channel]["unit"]
        st.caption(f"{team.size(project, v.draft)} {unit} (limit {team.CHANNELS[project.channel]['limits'][1]})")
        if v.problems:
            st.warning("The Writer couldn't fix everything. Still wrong:\n" + "\n".join(f"- {md(p)}" for p in v.problems))
        else:
            st.success("All checks passed: length, company named, a specific fact from the research, your own matching "
                       "experience, greeting, language, no placeholders, no numbers that aren't in the research or your profile.")
        facts = {f.id: f for f in project.facts}
        with st.expander("🎯 Why this message: the Strategist's hooks"):
            for h in project.hooks:
                st.markdown(f"- **{md(facts[h.fact].text)}** → {md(h.strength)}: “{md(h.proof)}”  \n  {md(h.angle)}")
        show_log(project)
    with right:
        st.subheader("Your decision")
        feedback = st.text_area("What should change?", key="feedback", height=110, placeholder="e.g. more formal, mention Brightline Check")
        if st.button("Revise", width="stretch"):
            if run(lambda progress: team.revise(project, feedback), "The Writer is revising, Python is checking…"):
                st.rerun()
        if st.button("✅ Approve this message", type="primary", width="stretch"):
            st.session_state.saved = team.approve(project)
            st.rerun()
        st.button("Start over", on_click=start_over, width="stretch")
        if project.goal == "business" and project.channel == "email":
            st.warning(UWG)


def step_done(project):
    st.success(f"Approved and saved to {md(str(st.session_state.get('saved', team.MESSAGES_DIR)))} with its facts and "
               "sources. Nothing was sent: copy it and send it yourself.")
    st.code(project.latest.draft.text(project.channel), language=None, wrap_lines=True)
    stem = Path(str(st.session_state.get("saved", "message.txt"))).stem
    download, again = st.columns(2)
    download.download_button("Download .txt", project.latest.draft.text(project.channel), f"{stem}.txt", width="stretch")
    again.button("Research another company", on_click=start_over, width="stretch")


st.set_page_config(page_title="Company Research Agent", page_icon="🔎", layout="wide")
st.title("🔎 Company Research Agent")
st.caption(
    "A team of AI agents reads a company's website, checks every fact and drafts a personal LinkedIn note or email "
    f"for you to approve. The AI runs on your own computer with Ollama ({team.MODEL}). Only the company's public "
    "website is read from the internet, and nothing is ever sent."
)

project = st.session_state.get("project")
if project is None:
    show_steps(0)
    step_input()
elif not project.versions:
    show_steps(1)
    step_facts(project)
elif not project.approved:
    show_steps(2)
    step_message(project)
else:
    show_steps(3)
    step_done(project)
