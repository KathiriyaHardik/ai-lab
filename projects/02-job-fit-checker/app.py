"""Job-Fit Checker web page. Start it from this folder with:

    uv run streamlit run app.py

then open http://localhost:8501 in your browser.
"""
from pathlib import Path

import streamlit as st

import jobfit

SAMPLES = Path(__file__).with_name("samples")
BAND_COLOUR = {"Excellent": "green", "Strong": "green", "Moderate": "orange", "Low": "red"}
POINTS_TEXT = {"yes": "✅ yes", "partly": "🟡 partly", "no": "❌ no"}


def load_example():
    st.session_state.job_ad = (SAMPLES / "job_ai_engineer_en.txt").read_text()
    st.session_state.cv_text = (SAMPLES / "cv_ai_student.txt").read_text()


def show(r):
    st.divider()
    st.subheader(r.job_title)
    st.markdown(f"## :{BAND_COLOUR[r.band]}[{r.match_percent}% · {r.band} match]")
    st.progress(r.match_percent / 100)
    st.caption(
        "Bands are the same as the Match % column in the Job Application Tracker (project 01): "
        "Excellent 90+ · Strong 75+ · Moderate 60+ · Low below 60."
    )

    for column, (title, items) in zip(
        st.columns(3), (("✅ You have", r.have), ("🟡 Partly", r.partly), ("❌ Missing", r.missing))
    ):
        with column:
            st.markdown(f"**{title}** ({len(items)})")
            if not items:
                st.caption("Nothing here")
            else:  # one markdown block, so the list stays compact
                st.markdown("\n".join(f"- {c.skill}" + (" · *must-have*" if c.importance == "must" else "") for c in items))

    if r.unverified:
        names = ", ".join(f"**{c.skill}**" for c in r.unverified)
        st.warning(
            f"The AI said your CV shows {names}, but the words it quoted as proof are not in your CV, "
            "so they count as missing. If you really have these skills, write them clearly in your CV."
        )

    st.markdown("**✍️ Why me?** A sentence for your cover letter. Use the copy button on the right.")
    st.code(r.why_me, language=None, wrap_lines=True)

    with st.expander("🔍 How the score was calculated"):
        st.markdown(
            "Each **must-have** is worth **2 points** and each **nice-to-have** **1 point**. "
            "You get all the points for ✅, half for 🟡 and none for ❌. "
            "**Match % = points earned ÷ points possible.** "
            "The AI only reads the texts. Python checks its proof and does the maths."
        )
        st.dataframe(
            [
                {
                    "Requirement": c.skill,
                    "Type": "must-have" if c.importance == "must" else "nice-to-have",
                    "Your CV": POINTS_TEXT[c.counted],
                    "Points": f"{jobfit.WEIGHT[c.importance] * jobfit.CREDIT[c.counted]:g} of {jobfit.WEIGHT[c.importance]}",
                    "Proof from your CV": c.evidence if c.verified else f"⚠️ not found in your CV: {c.evidence}",
                }
                for c in r.checks
            ],
            hide_index=True,
            height=35 * (len(r.checks) + 1) + 3,  # every row visible, no inner scrolling
            column_config={
                "Type": st.column_config.TextColumn(width="small"),
                "Your CV": st.column_config.TextColumn(width="small"),
                "Points": st.column_config.TextColumn(width="small"),
                "Proof from your CV": st.column_config.TextColumn(width="large"),
            },
        )


st.set_page_config(page_title="Job-Fit Checker", page_icon="🎯", layout="wide")
st.title("🎯 Job-Fit Checker")
st.caption(
    "Paste a job ad and your CV to see how well you match. It runs on your own computer with "
    f"Ollama ({jobfit.MODEL}), so nothing is sent to the internet."
)
st.button("Try it with an example", on_click=load_example)

left, right = st.columns(2)
with left:
    job_ad = st.text_area(
        "**1. The job ad**", key="job_ad", height=420, placeholder="Paste the full job ad here, in English or German."
    )
with right:
    upload = st.file_uploader(
        "**2. Your CV**", type=["pdf", "txt", "md"], help="A PDF with real text (not a scanned image) or a text file."
    )
    cv_text = st.text_area("…or paste your CV as text", key="cv_text", height=260, disabled=upload is not None)

if st.button("Check my fit", type="primary", width="stretch"):
    try:
        cv = jobfit.extract_text(upload.getvalue(), upload.name) if upload else cv_text
        with st.spinner("Reading the job ad and your CV. This takes up to a minute…"):
            result = jobfit.check_fit(job_ad, cv)
    except jobfit.JobFitError as e:
        st.error(str(e))
    else:
        show(result)
