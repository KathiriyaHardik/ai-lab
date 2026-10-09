"""Fast tests for everything except the AI itself (a fake AI client stands in for Ollama).

    uv run pytest

The real model is tested separately by evaluate.py.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import ollama
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jobfit  # noqa: E402

SAMPLES = Path(__file__).resolve().parents[1] / "samples"

CV = """Alex Example
- Built a RAG chatbot in Python with LangChain,
  answering questions from 2,000 PDF pages
- Packaged the service with Docker
Skills: JavaScript, SQL, Git and GitHub
Languages: English C1, German B1"""
JOB_AD = "Junior AI Engineer. Must have: Python, RAG, Docker, Java. Nice to have: Kubernetes. " * 3


def req(skill, importance="must", details=None):
    return {"skill": skill, "details": details or skill, "importance": importance}


def job(*requirements, job_title="Junior AI Engineer", language="English"):
    """Step 1 reply: what the job ad asks for."""
    return json.dumps({"job_title": job_title, "language": language, "requirements": list(requirements)})


def found(status="yes", evidence=""):
    return {"status": status, "evidence": evidence}


def cv_answer(*assessments, why_me="I built a RAG chatbot in Python."):
    """Step 2 reply: one slot per requirement, then the "Why me?" sentence."""
    return json.dumps({**{f"r{i}": a for i, a in enumerate(assessments, 1)}, "why_me": why_me})


class FakeClient:
    """Plays back prepared replies (JSON strings or exceptions) and records every call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(message=SimpleNamespace(content=reply))


def checks(*rows, cv=CV):
    """rows: (skill, importance, status, evidence)"""
    requirements = [jobfit.JobRequirement(skill=s, details=s, importance=i) for s, i, _, _ in rows]
    assessments = [jobfit.Assessment(status=st, evidence=e) for _, _, st, e in rows]
    return jobfit.build_checks(requirements, assessments, cv)


def row(skill, importance="must", status="yes", evidence=""):
    return skill, importance, status, evidence


# ----------------------------------------------------------------------------- evidence check
def test_evidence_survives_line_breaks_bullets_and_case():
    assert jobfit.evidence_in_cv("built a RAG chatbot in python with langchain, answering questions", CV)


def test_evidence_with_different_quotes_and_dashes():
    cv = "Wrote “clean” code — always tested."
    assert jobfit.evidence_in_cv('wrote "clean" code - always tested', cv)


def test_made_up_evidence_is_rejected():
    assert not jobfit.evidence_in_cv("Deployed models on Kubernetes", CV)


def test_evidence_must_match_whole_words():
    assert not jobfit.evidence_in_cv("Java", CV)  # the CV says JavaScript
    assert jobfit.evidence_in_cv("JavaScript", CV)


def test_evidence_can_skip_words_with_an_ellipsis():
    assert jobfit.evidence_in_cv("Built a RAG chatbot ... Packaged the service with Docker", CV)
    assert jobfit.evidence_in_cv("Built a RAG chatbot … with Docker", CV)
    assert not jobfit.evidence_in_cv("Built a RAG chatbot ... on Kubernetes", CV)


def test_empty_evidence_is_not_proof():
    assert not jobfit.evidence_in_cv("", CV)
    assert not jobfit.evidence_in_cv(" ... ", CV)


# ----------------------------------------------------------------------------- scoring
def test_all_yes_is_100_and_all_no_is_0():
    assert jobfit.match_percent(checks(row("Python", evidence="Python"), row("Git", "nice", evidence="Git"))) == 100
    assert jobfit.match_percent(checks(row("Kubernetes", status="no"), row("AWS", "nice", status="no"))) == 0


def test_must_have_counts_twice_as_much_as_nice_to_have():
    # must (2 points) earned, nice (1 point) missed -> 2 of 3 points
    assert jobfit.match_percent(checks(row("Python", evidence="Python"), row("AWS", "nice", status="no"))) == 67
    # must missed, nice earned -> 1 of 3 points
    assert jobfit.match_percent(checks(row("AWS", status="no"), row("Git", "nice", evidence="Git"))) == 33


def test_partly_earns_half():
    assert jobfit.match_percent(checks(row("SQL databases", status="partly", evidence="SQL"))) == 50


def test_claim_without_proof_counts_as_missing():
    result = checks(row("Kubernetes", evidence="Deployed on Kubernetes"), row("Python", evidence="Python"))
    k8s = result[0]
    assert (k8s.status, k8s.verified, k8s.counted) == ("yes", False, "no")
    assert jobfit.match_percent(result) == 50


def test_no_status_needs_no_proof():
    (c,) = checks(row("Kubernetes", status="no"))
    assert c.verified and c.counted == "no"


def test_duplicate_and_empty_requirements_are_dropped():
    reqs = [jobfit.JobRequirement(**req(s)) for s in ("Python", "python ", "  ", "!!", "Docker")]
    assert [r.skill for r in jobfit.unique_requirements(reqs)] == ["Python", "Docker"]


def test_requirement_list_is_capped():
    reqs = [jobfit.JobRequirement(**req(f"Skill {i}")) for i in range(30)]
    assert len(jobfit.unique_requirements(reqs)) == jobfit.MAX_REQUIREMENTS


@pytest.mark.parametrize(
    "percent, expected",
    [(100, "Excellent"), (90, "Excellent"), (89, "Strong"), (75, "Strong"), (74, "Moderate"),
     (60, "Moderate"), (59, "Low"), (0, "Low")],
)
def test_bands_match_the_job_tracker(percent, expected):
    assert jobfit.band(percent) == expected


# ----------------------------------------------------------------------------- full check with a fake AI
FIVE_REQUIREMENTS = job(
    req("Python"), req("Kubernetes", "nice"), req("Java"), req("Docker"), req("German", "nice"),
    job_title="  Junior AI Engineer \n",
)
FIVE_ANSWERS = cv_answer(
    found(evidence="Built a RAG chatbot in Python"),
    found("no"),
    found(evidence="Java"),  # wrong: the CV only has JavaScript
    found(evidence="Packaged the service with Docker"),
    found("partly", "German B1"),
)


def test_check_fit_end_to_end():
    r = jobfit.check_fit(JOB_AD, CV, client=FakeClient(FIVE_REQUIREMENTS, FIVE_ANSWERS))

    # must: Python 2 + Docker 2 earned, Java 0 of 2 ; nice: German 0.5 of 1, Kubernetes 0 of 1 -> 4.5 / 8
    assert r.match_percent == 56 and r.band == "Low"
    assert r.job_title == "Junior AI Engineer"
    assert [c.skill for c in r.have] == ["Python", "Docker"]
    assert [c.skill for c in r.partly] == ["German"]
    assert [c.skill for c in r.missing] == ["Java", "Kubernetes"]  # must-haves first
    assert [c.skill for c in r.unverified] == ["Java"]
    assert r.why_me == "I built a RAG chatbot in Python."


def test_step_1_sees_only_the_job_ad():
    client = FakeClient(FIVE_REQUIREMENTS, FIVE_ANSWERS)
    jobfit.check_fit(JOB_AD, CV, model="some-model", client=client)
    step1, step2 = client.calls
    for call in client.calls:
        assert call["model"] == "some-model"
        assert call["think"] is False
        assert call["options"] == {
            "temperature": 0, "num_ctx": jobfit.CONTEXT_TOKENS, "num_predict": jobfit.MAX_ANSWER_TOKENS,
        }

    assert step1["format"] == jobfit.JobAd.model_json_schema()
    assert JOB_AD.strip() in step1["messages"][-1]["content"]
    assert "JavaScript" not in str(step1["messages"])  # the CV must not influence the requirement list

    prompt = step2["messages"][-1]["content"]
    assert CV.strip() in prompt and JOB_AD.strip() not in prompt
    assert "r3: Java: Java (must-have)" in prompt and "r5: German: German (nice-to-have)" in prompt
    assert "written in English" in step2["messages"][0]["content"]


def test_step_2_has_one_required_slot_per_requirement():
    schema = jobfit.cv_answer_model(3).model_json_schema()
    assert schema["required"] == ["r1", "r2", "r3", "why_me"]


def test_step_2_answer_with_a_missing_slot_is_rejected():
    short = cv_answer(found(evidence="Python"))  # 1 answer for 5 requirements
    client = FakeClient(FIVE_REQUIREMENTS, short, short)
    with pytest.raises(jobfit.JobFitError, match="not in the expected format"):
        jobfit.check_fit(JOB_AD, CV, client=client)


def test_why_me_follows_the_job_ad_language():
    client = FakeClient(job(req("Python"), language="German"), cv_answer(found(evidence="Python")))
    jobfit.check_fit(JOB_AD, CV, client=client)
    assert "written in German" in client.calls[1]["messages"][0]["content"]


def test_one_bad_answer_is_retried_with_a_little_randomness():
    stuck = '{"job_title": "Junior AI Engineer", "language": "English", "requirements": [' + "\n" * 50  # cut off
    client = FakeClient(stuck, job(req("Python")), cv_answer(found(evidence="Python")))
    assert jobfit.check_fit(JOB_AD, CV, client=client).match_percent == 100
    assert [c["options"]["temperature"] for c in client.calls] == [0, 0.3, 0]


def test_thinking_gets_more_room():
    client = FakeClient(job(req("Python")), cv_answer(found(evidence="Python")))
    jobfit.check_fit(JOB_AD, CV, client=client, think=True)
    assert all(c["think"] is True for c in client.calls)
    assert all(c["options"]["num_predict"] == 3 * jobfit.MAX_ANSWER_TOKENS for c in client.calls)


def test_evidence_is_cleaned_up():
    (yes, no) = checks(row("Python", evidence='"Python (4 years)"'), row("AWS", status="no", evidence='"""'),
                       cv="Python (4 years)")
    assert (yes.evidence, yes.verified) == ("Python (4 years)", True)
    assert no.evidence == ""


def test_two_bad_answers_give_a_clear_error():
    client = FakeClient('{"job_title": "x"}', job())  # the second one has no requirements
    with pytest.raises(jobfit.JobFitError, match="not in the expected format"):
        jobfit.check_fit(JOB_AD, CV, client=client)


def test_no_usable_requirements():
    client = FakeClient(job(req("  "), req("!!")))
    with pytest.raises(jobfit.JobFitError, match="no requirements"):
        jobfit.check_fit(JOB_AD, CV, client=client)
    assert len(client.calls) == 1  # step 2 is skipped


def test_ollama_not_running():
    client = FakeClient(ConnectionError("Failed to connect to Ollama"))
    with pytest.raises(jobfit.JobFitError, match="Open the Ollama app"):
        jobfit.check_fit(JOB_AD, CV, client=client)


def test_model_not_downloaded():
    client = FakeClient(ollama.ResponseError("model not found", 404))
    with pytest.raises(jobfit.JobFitError, match="ollama pull some-model"):
        jobfit.check_fit(JOB_AD, CV, model="some-model", client=client)


def test_ollama_without_structured_output():
    client = FakeClient(ollama.ResponseError("structured output is unavailable", 500))
    with pytest.raises(jobfit.JobFitError, match="official app"):
        jobfit.check_fit(JOB_AD, CV, client=client)


def test_other_ollama_error_is_passed_on():
    client = FakeClient(ollama.ResponseError("out of memory", 500))
    with pytest.raises(jobfit.JobFitError, match="out of memory"):
        jobfit.check_fit(JOB_AD, CV, client=client)


# ----------------------------------------------------------------------------- input checks
@pytest.mark.parametrize("job_ad, cv, message", [
    ("", CV, "job ad is empty"),
    ("Python dev", CV, "job ad is empty"),
    (JOB_AD, "   ", "CV is empty"),
    (JOB_AD, None, "CV is empty"),
    (JOB_AD, "x" * jobfit.MAX_INPUT_CHARS, "too long"),
])
def test_bad_inputs_never_reach_the_ai(job_ad, cv, message):
    client = FakeClient()
    with pytest.raises(jobfit.JobFitError, match=message):
        jobfit.check_fit(job_ad, cv, client=client)
    assert client.calls == []


# ----------------------------------------------------------------------------- reading CV files
def make_pdf(lines):
    """A minimal one-page PDF with real text in it."""
    content = "BT /F1 11 Tf 72 740 Td " + " ".join(f"({line}) Tj 0 -14 Td" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = b"%PDF-1.4\n", []
    for number, obj in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{obj}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def test_text_is_read_from_a_pdf():
    lines = ["Alex Example, Python developer", "Built a RAG chatbot with LangChain",
             "Packaged the service with Docker", "English C1, German B1"]
    text = jobfit.extract_text(make_pdf(lines), "My_CV.PDF")
    for line in lines:
        assert line in text


def test_scanned_pdf_without_text():
    with pytest.raises(jobfit.JobFitError, match="scanned image"):
        jobfit.extract_text(make_pdf([]), "cv.pdf")


def test_broken_pdf():
    with pytest.raises(jobfit.JobFitError, match="Could not read this PDF"):
        jobfit.extract_text(b"this is not a pdf", "cv.pdf")


def test_text_file_with_umlauts():
    assert jobfit.extract_text("Kenntnisse: Python, Deutsch B1, Größe".encode(), "cv.txt").endswith("Größe")


def test_sample_files_are_valid_inputs():
    files = sorted(SAMPLES.glob("*.txt"))
    assert len(files) == 5
    jobs = [jobfit.read_file(f) for f in files if f.name.startswith("job_")]
    cvs = [jobfit.read_file(f) for f in files if f.name.startswith("cv_")]
    for job_ad in jobs:
        for cv in cvs:
            jobfit.check_inputs(job_ad, cv)
