"""Fast tests for everything except the AI itself (a fake Ollama client stands in for it).

    uv run pytest

The real model is tested separately by evaluate.py.
"""
import datetime as dt
import io
import json
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import ollama
import pytest
from fpdf import FPDF
from PIL import Image

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import letters  # noqa: E402

SAMPLES = HERE / "samples"
TODAY = dt.date(2026, 10, 12)
LETTER = """Finanzamt Beispielstadt
Datum: 06.10.2026
Sie müssen noch 1.234,56 € zahlen. Bitte zahlen Sie den Betrag bis zum 09.11.2026.
Bereits gezahlt: 5.577,44 €. Steuernummer: 12/345/67890
Der Einspruch ist innerhalb eines Monats nach Bekanntgabe dieses Bescheids einzulegen.
Ihr Ausweis ist gültig bis zum 31.12.2026. Sagen Sie den Termin spätestens drei Werktage vorher ab."""


class FakeClient:
    """Plays back prepared replies (strings or exceptions) and records every call."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return SimpleNamespace(message=SimpleNamespace(content=reply))


def answer(labels=None, amount="", reference=("", ""), **extra):
    """An explanation as the AI would send it. Labels: {id: (kind, what)}, missing ids are "other"."""
    found = letters.find_all(extra.pop("letter", LETTER))
    labels = labels or {}
    body = {
        "sender": "Finanzamt Beispielstadt", "topic": "Tax notice",
        **{f.id: {"kind": labels.get(f.id, ("other", ""))[0], "what": labels.get(f.id, ("other", ""))[1]}
           for f in found if f.id[0] in "dp"},
        "amount_to_pay": amount, "reference": {"value": reference[0], "quote": reference[1]},
        "summary": "You have to pay tax.", "actions": ["Pay the tax.", " "], "if_ignored": "Late fees.",
    }
    body.update(extra)
    return json.dumps(body)


TAX_LABELS = {
    "d1": ("letter_date", ""), "d2": ("deadline", "Pay the tax"), "d3": ("valid_until", "ID card"),
    "p1": ("objection", "Object to the notice"), "p2": ("deadline", "Cancel the appointment"),
}


def page(text=LETTER):
    return [letters.Page(1, text, read_by_ai=False)]


def explain(*replies, today=TODAY, **kwargs):
    client = FakeClient(*replies)
    return letters.explain(page(), client=client, today=today, **kwargs), client


def jpeg(size=(800, 1100), exif_orientation=None):
    image = Image.new("RGB", size, "white")
    out = io.BytesIO()
    exif = Image.Exif()
    if exif_orientation:
        exif[0x0112] = exif_orientation
    image.save(out, "JPEG", exif=exif)
    return out.getvalue()


def text_pdf(*pages):
    pdf = FPDF()
    pdf.core_fonts_encoding = "windows-1252"
    for text in pages:
        pdf.add_page()
        pdf.set_font("Helvetica", size=11)
        if text:
            pdf.multi_cell(0, 6, text)
    return bytes(pdf.output())


# ----------------------------------------------------------------------------- finding dates, spans, amounts
def test_finds_every_date_once_in_reading_order():
    found = letters.find_all("Datum 06.10.2026, zahlen bis 9.11.26 oder bis zum 09.11.2026; Termin am 3. Dezember 2026.")
    dates = [(f.id, f.date) for f in found if f.id[0] == "d"]
    assert dates == [("d1", dt.date(2026, 10, 6)), ("d2", dt.date(2026, 11, 9)), ("d3", dt.date(2026, 12, 3))]
    assert found[1].context.count("09.11.2026") + found[1].context.count("9.11.26") == 2  # both places kept


def test_impossible_dates_and_lookalikes_are_ignored():
    found = letters.find_all("Am 31.02.2026 in Raum 2.14 um 09:30 Uhr, Steuernummer 12/345/67890, Version 1.2.3")
    assert [f for f in found if f.id[0] == "d"] == []


@pytest.mark.parametrize("text, amount, unit, before", [
    ("innerhalb eines Monats nach Bekanntgabe", 1, "months", False),
    ("innerhalb von 14 Tagen nach Erhalt", 14, "days", False),
    ("binnen zwei Wochen", 2, "weeks", False),
    ("spätestens drei Werktage vorher absagen", 3, "days", True),
    ("zwei Wochen vor dem Termin", 2, "weeks", True),
    ("innerhalb von 30 Kalendertagen", 30, "days", False),
])
def test_finds_time_spans(text, amount, unit, before):
    (span,) = [f for f in letters.find_all(text) if f.id[0] == "p"]
    assert (span.amount, span.unit, span.before_event) == (amount, unit, before)


@pytest.mark.parametrize("text, value", [
    ("Gesamtbetrag: 1.234,56 €", "1234.56"), ("91,40 EUR", "91.40"), ("€ 5,00", "5.00"),
    ("Gebühr 100 Euro", "100.00"), ("12,- €", "12.00"), ("1.000.000,00 €", "1000000.00"),
])
def test_finds_euro_amounts(text, value):
    (amount,) = [f for f in letters.find_all(text) if f.id[0] == "a"]
    assert amount.euros == Decimal(value)


def test_numbers_without_a_currency_are_not_amounts():
    assert [f for f in letters.find_all("Kundennummer 4711-0815, 86,40 Kilowattstunden") if f.id[0] == "a"] == []


def test_same_amount_twice_is_found_once():
    amounts = [f for f in letters.find_all("Gesamtbetrag: 91,40 €. Bitte zahlen Sie 91,40 € bis morgen.") if f.id[0] == "a"]
    assert len(amounts) == 1 and amounts[0].text == "91,40 €"


def test_long_lists_are_capped(monkeypatch):
    monkeypatch.setattr(letters, "MAX_FOUND", 3)
    text = " ".join(f"{d:02d}.01.2027" for d in range(1, 10))
    assert [f.id for f in letters.find_all(text)] == ["d1", "d2", "d3"]


def test_the_sample_letters_contain_what_the_ai_needs():
    import make_samples
    import pypdfium2 as pdfium

    for letter in make_samples.LETTERS:
        text = pdfium.PdfDocument(make_samples.letter_pdf(letter))[0].get_textpage().get_text_range()
        found = letters.find_all(text)
        assert any(f.id[0] == "d" for f in found), letter["key"]


# ----------------------------------------------------------------------------- counting
@pytest.mark.parametrize("start, amount, unit, expected", [
    (dt.date(2026, 10, 6), 1, "months", dt.date(2026, 11, 6)),
    (dt.date(2026, 1, 31), 1, "months", dt.date(2026, 2, 28)),
    (dt.date(2028, 1, 31), 1, "months", dt.date(2028, 2, 29)),
    (dt.date(2026, 11, 30), 3, "months", dt.date(2027, 2, 28)),
    (dt.date(2026, 10, 5), 14, "days", dt.date(2026, 10, 19)),
    (dt.date(2026, 12, 28), 2, "weeks", dt.date(2027, 1, 11)),
])
def test_add_period(start, amount, unit, expected):
    assert letters.add_period(start, amount, unit) == expected


def test_euro_formatting():
    assert letters.euros(Decimal("1234.5")) == "1.234,50 €"
    assert letters.euros(Decimal("91.4")) == "91,40 €"


# ----------------------------------------------------------------------------- the AI's answer and the result
def test_answer_shape_has_a_required_label_per_date_and_span():
    found = letters.find_all(LETTER)
    schema = letters.answer_shape(found).model_json_schema()
    assert {"d1", "d2", "d3", "p1", "p2"} <= set(schema["required"])
    assert schema["properties"]["amount_to_pay"]["enum"] == ["", "a1", "a2"]


def test_full_result():
    r, _ = explain(answer(TAX_LABELS, amount="a1", reference=("12/345/67890", "Steuernummer: 12/345/67890")))
    assert r.letter_date == dt.date(2026, 10, 6)
    assert [(d.kind, d.due, d.days_left) for d in r.deadlines] == [
        ("objection", dt.date(2026, 11, 6), 25),  # one month from the letter date, so safe
        ("deadline", dt.date(2026, 11, 9), 28),
        ("deadline", None, None),  # three working days before something: no countdown
    ]
    assert r.deadlines[0].period == "1 month" and r.deadlines[2].before_event
    assert "09.11.2026" in r.deadlines[1].context
    assert r.valid_until == [("ID card", dt.date(2026, 12, 31), r.valid_until[0][2])]
    assert r.amount.text == "1.234,56 €"
    assert (r.reference, r.reference_checked) == ("12/345/67890", True)
    assert r.actions == ["Pay the tax."]  # empty steps dropped


def test_overdue_and_today():
    r, _ = explain(answer(TAX_LABELS), today=dt.date(2026, 11, 9))
    assert [d.days_left for d in r.deadlines if d.due] == [-3, 0]


def test_time_span_without_a_letter_date_has_no_countdown():
    labels = {**TAX_LABELS, "d1": ("other", "")}
    r, _ = explain(answer(labels))
    objection = next(d for d in r.deadlines if d.kind == "objection")
    assert objection.due is None and objection.period == "1 month"


def test_no_deadlines_and_nothing_to_pay():
    r, _ = explain(answer())
    assert r.deadlines == [] and r.amount is None and r.reference == ""


def test_reference_that_is_not_in_the_letter_is_flagged():
    r, _ = explain(answer(reference=("99/999/99999", "Steuernummer: 99/999/99999")))
    assert r.reference == "99/999/99999" and not r.reference_checked


def test_ai_gets_the_letter_and_numbered_lists():
    _, client = explain(answer(), language="Einfaches Deutsch", model="some-model")
    (call,) = client.calls
    assert call["model"] == "some-model" and call["think"] is False
    assert call["options"] == {"temperature": 0, "num_ctx": letters.CONTEXT_TOKENS, "num_predict": letters.MAX_ANSWER_TOKENS}
    assert "in einfaches Deutsch" in call["messages"][0]["content"]
    prompt = call["messages"][1]["content"]
    assert prompt.startswith("LETTER:\n<<<\n--- page 1 ---\nFinanzamt Beispielstadt")
    assert "DATES:\nd1: 06.10.2026 · " in prompt and "TIME SPANS:\np1: eines Monats · " in prompt
    assert "AMOUNTS:\na1: 1.234,56 € · " in prompt


def test_an_amount_the_letter_doesnt_have_is_rejected_and_retried():
    _, client = explain(answer(amount="a9"), answer(amount="a1"))
    assert [c["options"]["temperature"] for c in client.calls] == [0, 0.3]


def test_two_bad_answers_give_a_clear_error():
    with pytest.raises(letters.LetterError, match="not in the expected format"):
        explain("{}", answer(amount="a9"))


# ----------------------------------------------------------------------------- reading the letter
def test_pdf_with_text_is_used_as_it_is():
    client = FakeClient()
    pages = letters.read_letter([("letter.pdf", (SAMPLES / "letter1_finanzamt.pdf").read_bytes())], client)
    assert client.calls == [] and not pages[0].read_by_ai
    assert "1.234,56 €" in pages[0].text


def test_photos_are_read_by_the_ai_page_by_page():
    client = FakeClient("Finanzamt Beispielstadt " * 5, "Seite zwei mit genug Text für die Prüfung, ganz sicher")
    pages = letters.read_letter([("p1.jpg", jpeg()), ("p2.png", jpeg())], client, model="some-model")
    assert [p.number for p in pages] == [1, 2] and all(p.read_by_ai for p in pages)
    first = client.calls[0]
    assert first["model"] == "some-model" and first["think"] is False
    assert first["messages"][0]["content"].startswith("This is page 1 of an official letter")
    assert first["messages"][0]["images"] == [pages[0].image]


def test_scanned_pdf_page_is_read_by_the_ai():
    client = FakeClient("Ein gescannter Brief mit genug Wörtern für die Prüfung hier")
    pages = letters.read_letter([("scan.pdf", text_pdf(""))], client)
    assert pages[0].read_by_ai and len(client.calls) == 1


def test_unreadable_photo():
    with pytest.raises(letters.LetterError, match="couldn't read the text on page 1"):
        letters.read_letter([("blurry.jpg", jpeg())], FakeClient("[unreadable]"))


def test_photos_are_turned_upright_and_made_smaller():
    image = Image.open(io.BytesIO(letters.prepare_image(jpeg((3000, 2000), exif_orientation=6))))
    assert image.size == (1067, 1600)  # rotated to portrait and capped at 1600 px


def test_iphone_heic_photo():
    from pillow_heif import from_pillow

    out = io.BytesIO()
    from_pillow(Image.new("RGB", (400, 600), "white")).save(out, format="HEIF")
    assert Image.open(io.BytesIO(letters.prepare_image(out.getvalue()))).size == (400, 600)


@pytest.mark.parametrize("files, message", [
    ([], "Add a photo or PDF"),
    ([("letter.docx", b"x")], "use a photo"),
    ([("letter.jpg", b"not an image")], "not a photo this tool can open"),
    ([("letter.pdf", b"not a pdf")], "broken PDF"),
    ([(f"p{i}.jpg", jpeg((50, 50))) for i in range(7)], "reads at most 6"),
])
def test_bad_files(files, message):
    with pytest.raises(letters.LetterError, match=message):
        letters.read_letter(files, FakeClient())


def test_whole_check_from_photo_to_result():
    letter_text = LETTER
    client = FakeClient(letter_text, answer(TAX_LABELS, amount="a1"))
    r = letters.check_letter([("letter.jpg", jpeg())], client=client, today=TODAY)
    assert r.pages[0].read_by_ai and r.amount.text == "1.234,56 €" and len(r.deadlines) == 3


# ----------------------------------------------------------------------------- Ollama problems
def test_ollama_not_running():
    with pytest.raises(letters.LetterError, match="Open the Ollama app"):
        letters.read_letter([("letter.jpg", jpeg())], FakeClient(ConnectionError("refused")))


def test_model_not_downloaded():
    with pytest.raises(letters.LetterError, match="ollama pull gemma4:e4b"):
        explain(ollama.ResponseError("not found", 404))


def test_ollama_without_structured_output():
    with pytest.raises(letters.LetterError, match="official app"):
        explain(ollama.ResponseError("structured output is unavailable", 500))
