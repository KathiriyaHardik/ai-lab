"""Tests for the web page, using Streamlit's built-in AppTest (no browser, fake AI)."""
import sys
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import jobfit  # noqa: E402
from test_jobfit import FakeClient, cv_answer, found, job, req  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "app.py")


@pytest.fixture
def fake_ai(monkeypatch):
    """Route every check_fit call from the page through a FakeClient."""
    client = FakeClient()
    real_check_fit = jobfit.check_fit
    monkeypatch.setattr(jobfit, "check_fit", lambda job_ad, cv: real_check_fit(job_ad, cv, client=client))
    return client


def test_example_then_check_shows_the_result(fake_ai):
    fake_ai.replies += [
        job(req("Python"), req("Kubernetes", "nice"), req("AWS")),
        cv_answer(
            found(evidence="Python (4 years)"),
            found("no"),
            found(evidence="Deployed on AWS"),  # not in the sample CV
            why_me="I built a RAG chatbot in Python.",
        ),
    ]
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.button[0].click().run()  # "Try it with an example"
    assert at.text_area(key="job_ad").value.startswith("Junior AI Engineer")
    assert at.text_area(key="cv_text").value.startswith("ALEX MUSTER")

    at.button[1].click().run()  # "Check my fit"
    assert not at.exception and not at.error
    page = " ".join(m.value for m in at.markdown)
    assert "40% · Low match" in page  # Python 2 of 5 points (AWS has no proof, Kubernetes missing)
    assert "AWS" in at.warning[0].value
    assert at.code[0].value == "I built a RAG chatbot in Python."
    assert len(at.dataframe) == 1


def test_empty_inputs_show_a_friendly_error(fake_ai):
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.button[1].click().run()
    assert "job ad is empty" in at.error[0].value
    assert fake_ai.calls == []
