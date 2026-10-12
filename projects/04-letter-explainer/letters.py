#!/usr/bin/env python3
"""German Letter Explainer: what does this official letter say, what do I have to do, and by when?

    uv run letters.py samples/letter2_stadtwerke_photo.jpg
    uv run letters.py my-letter.pdf --language "Einfaches Deutsch"

How a letter is checked:
  1. read it: a PDF with real text is used as it is. A photo or scan is read by the AI,
     which writes out every word (you can compare that text with your paper letter)
  2. find: Python finds every date, time span ("innerhalb von 14 Tagen") and euro amount in the
     text with simple patterns, so none can be missed or misread by the AI
  3. explain: the AI says what each of them means (deadline, appointment, letter date...), which
     amount is the total to pay, and explains the letter in simple words, in a fixed JSON shape
  4. count: Python, not the AI, turns deadlines into days left. For "within 14 days" it counts
     from the date on the letter, which is earlier than the legal deadline, so it's safe

Everything runs on this computer: the letter is only sent to Ollama on localhost.
This is not legal advice.
"""
import argparse
import calendar
import datetime as dt
import io
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Literal

import ollama
from pydantic import BaseModel, Field, ValidationError, create_model

MODEL = os.environ.get("LETTERS_MODEL", "gemma4:e4b")
MAX_PAGES = 6
IMAGE_SIDE = 1600  # longest side sent to the AI; more doesn't help it read better
MIN_PDF_TEXT = 200  # a PDF page with less text than this is treated as a scan
CONTEXT_TOKENS = 16384
MAX_READ_TOKENS = 3072  # one page of text; hard stop so a stuck model can't run forever
MAX_ANSWER_TOKENS = 2048
MAX_FOUND = 25  # dates, time spans or amounts passed to the AI, each
LANGUAGES = {  # name in the menu -> how the AI is asked to write
    "English": "simple English",
    "Einfaches Deutsch": "einfaches Deutsch (kurze Sätze, keine Fachwörter)",
    "Türkçe": "simple Turkish",
    "Українська": "simple Ukrainian",
    "العربية": "simple Arabic",
}
IMAGE_TYPES = ("jpg", "jpeg", "png", "heic", "heif", "webp")
DUE_KINDS = ("deadline", "appointment", "objection")

READ_PROMPT = """This is page {page} of an official letter. Write out ALL of its text exactly as printed,
line by line, in the original language. Do not translate, summarise or correct anything.
Write [unreadable] for anything you can't read."""

EXPLAIN_PROMPT = """You explain official German letters to people who don't read German well. You get the
text of one letter, and lists of the dates (d1, d2...), time spans (p1, p2...) and euro amounts
(a1, a2...) found in it, each with the words around it.

Fill in the JSON fields. Write topic, what, summary, actions and if_ignored in {language}, in your own
simple words: translate and explain, never copy German sentences.
- sender: who sent the letter, as written in the letter.
- topic: what the letter is about, in one short line.
- d1, d2... and p1, p2...: what each date or time span means for the reader. kind is one of:
  "deadline": something has to be done by then (pay, send, reply, register...)
  "appointment": the reader has to go somewhere then
  "objection": the time limit to object (Einspruch, Widerspruch, Klage). Only needed if the reader disagrees
  "letter_date": the date the letter was written
  "valid_until": something stops being valid then (a permit, a card, a contract)
  "other": anything else, like the date of an old invoice or "the last three months"
  what: for deadline, appointment and objection, what the reader has to do, in a few words starting
  with a verb, not when (for example "Pay the tax", "Object to the decision", "Go to the appointment at
  9:30 in room 2.14"). For valid_until, what stops being valid. Empty for letter_date and other.
- amount_to_pay: the id of the amount the reader has to pay in total (for example "a3"), or ""
  if the reader doesn't have to pay anything.
- reference: value = the most important number to give when replying or paying, quote = the exact words
  where it appears. Letters usually label it Aktenzeichen, Geschäftszeichen, Steuernummer, Kundennummer,
  Versichertennummer, Mitgliedsnummer, Vertragsnummer or Rechnungsnummer. Both empty only if there is none.
- summary: 2 to 4 short sentences.
- actions: what the reader has to do, one step per item, in order. A step that is only needed in some
  cases starts with "Only if" (for example "Only if you disagree: object within one month").
  Empty if nothing has to be done.
- if_ignored: what the letter says happens if the reader does nothing. Empty if the letter doesn't say.

Never add anything that is not in the letter."""


class LetterError(Exception):
    """A problem the user can fix (unreadable photo, too many pages, Ollama not running...)."""


# ----------------------------------------------------------------------------- what Python finds in the text
@dataclass
class Found:
    id: str  # d1, p2, a3: how the AI refers to it
    text: str  # as written in the letter: "09.11.2026", "innerhalb eines Monats", "1.234,56 €"
    context: str  # the words around it, straight from the letter
    date: dt.date | None = None  # dates
    amount: int = 0  # time spans: 14 ...
    unit: str = ""  # ... "days" | "weeks" | "months"
    before_event: bool = False  # "drei Werktage vorher": counted back from some other date
    euros: Decimal | None = None  # amounts


MONTHS = {"januar": 1, "jänner": 1, "februar": 2, "märz": 3, "april": 4, "mai": 5, "juni": 6, "juli": 7,
          "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12}
NUMBERS = {"ein": 1, "eine": 1, "einen": 1, "einem": 1, "einer": 1, "eines": 1, "zwei": 2, "drei": 3, "vier": 4,
           "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10, "elf": 11, "zwölf": 12,
           "vierzehn": 14, "einundzwanzig": 21, "dreißig": 30}
UNITS = {"tag": "days", "kalendertag": "days", "werktag": "days", "arbeitstag": "days", "woche": "weeks", "monat": "months"}
UNIT_NAMES = {"days": ("day", "days"), "weeks": ("week", "weeks"), "months": ("month", "months")}
BEFORE_WORDS = ("vor", "vorher", "zuvor", "davor")  # "drei Werktage vorher", "zwei Wochen vor dem Termin"

DATE_RE = re.compile(r"\b(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4}|\d{2})\b")
LONG_DATE_RE = re.compile(r"\b(\d{1,2})\.\s*(" + "|".join(MONTHS) + r")\s+(\d{4})\b", re.IGNORECASE)
PERIOD_RE = re.compile(
    r"\b(\d{1,3}|" + "|".join(sorted(NUMBERS, key=len, reverse=True)) + r")\s+"
    r"(kalendertag|werktag|arbeitstag|tag|woche|monat)(?:e|en|es|s|n)?\b", re.IGNORECASE)
NUMBER = r"(\d{1,3}(?:\.\d{3})+|\d+)(?:,(\d{2}|-))?"
AMOUNT_RES = [re.compile(NUMBER + r"\s*(?:€|EUR\b|Euro\b)"), re.compile(r"(?:€|EUR)\s*" + NUMBER)]


def words(text):
    """Lower-case words only, so line breaks, quotes and dashes don't block a match."""
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).lower()))


def quote_in(quote, text):
    """True if every part of the quote (split at "...") appears word for word in the text."""
    parts = [p for p in (words(p) for p in re.split(r"\.\.\.|…", quote)) if p]
    haystack = f" {words(text)} "
    return bool(parts) and all(f" {p} " in haystack for p in parts)


def context(text, start, end, before=110, after=90):
    a, b = max(0, start - before), min(len(text), end + after)
    a = text.rfind(" ", 0, a) + 1 if a > 0 else 0  # widen to whole words
    b = text.find(" ", b) if b < len(text) and text.find(" ", b) != -1 else len(text)
    return ("…" if a > 0 else "") + text[a:b].strip() + ("…" if b < len(text) else "")


def add_context(item, more):
    if more.strip("…") not in item.context:  # skip a second place that is already in the first one's words
        item.context += " " + more


def find_all(letter):
    """Every date, time span and euro amount in the letter, each once, in reading order."""
    text = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", letter))

    dates = {}
    for match in sorted([*DATE_RE.finditer(text), *LONG_DATE_RE.finditer(text)], key=lambda m: m.start()):
        d, m, y = match.groups()
        month = int(m) if m.isdigit() else MONTHS[m.lower()]
        try:
            day = dt.date(int(y) + (2000 if len(y) == 2 else 0), month, int(d))
        except ValueError:
            continue
        if day in dates:  # the same date twice: keep both places, the meaning may be in either
            add_context(dates[day], context(text, match.start(), match.end(), 60, 60))
        else:
            dates[day] = Found("", match.group(0), context(text, *match.span()), date=day)

    periods = []
    for match in PERIOD_RE.finditer(text):
        number, unit = match.group(1).lower(), match.group(2).lower()
        following = words(text[match.end():match.end() + 40]).split()[:3]
        periods.append(Found("", match.group(0), context(text, *match.span()),
                             amount=int(number) if number.isdigit() else NUMBERS[number], unit=UNITS[unit],
                             before_event=any(w in BEFORE_WORDS for w in following)))

    amounts = {}
    for match in sorted([m for r in AMOUNT_RES for m in r.finditer(text)], key=lambda m: m.start()):
        whole, cents = match.groups()
        value = Decimal(whole.replace(".", "") + "." + (cents if cents and cents.isdigit() else "00"))
        if value in amounts:
            add_context(amounts[value], context(text, match.start(), match.end(), 60, 60))
        else:
            amounts[value] = Found("", euros(value), context(text, *match.span()), euros=value)

    found = []
    for prefix, items in (("d", list(dates.values())), ("p", periods), ("a", list(amounts.values()))):
        for n, item in enumerate(items[:MAX_FOUND], 1):
            item.id = f"{prefix}{n}"
            found.append(item)
    return found


def euros(amount):
    whole, cents = f"{amount:.2f}".split(".")
    return f"{int(whole):,}".replace(",", ".") + f",{cents} €"


def add_period(start, amount, unit):
    if unit == "days":
        return start + dt.timedelta(days=amount)
    if unit == "weeks":
        return start + dt.timedelta(weeks=amount)
    month = start.month - 1 + amount
    year, month = start.year + month // 12, month % 12 + 1
    return dt.date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


# ----------------------------------------------------------------------------- the AI's answer
class Label(BaseModel):
    kind: Literal["deadline", "appointment", "objection", "letter_date", "valid_until", "other"]
    what: str = Field(description="What it is about, for deadline, appointment, objection and valid_until")


class Quoted(BaseModel):
    value: str
    quote: str = Field(description="Exact words from the letter")


def answer_shape(found):
    """JSON shape for the AI: one required label per date and time span, amount ids as a fixed list."""
    labels = {f.id: (Label, ...) for f in found if f.id[0] in "dp"}
    amount_ids = tuple([""] + [f.id for f in found if f.id[0] == "a"])
    return create_model(
        "Explanation",
        sender=(str, ...), topic=(str, ...), **labels,
        amount_to_pay=(Literal[amount_ids], ...),  # only an amount Python really found, or ""
        reference=(Quoted, ...), summary=(str, ...), actions=(list[str], ...), if_ignored=(str, ...),
    )


# ----------------------------------------------------------------------------- the result
@dataclass
class Page:
    number: int
    text: str
    read_by_ai: bool  # False when the text came straight from a PDF
    image: bytes = b""  # what the AI looked at (JPEG), for showing next to the result


@dataclass
class DueDate:
    kind: str  # deadline | appointment | objection
    what: str
    context: str  # the words around it in the letter
    date: dt.date | None = None  # a date printed in the letter
    period: str = ""  # or a time span, e.g. "14 days"
    before_event: bool = False  # "3 working days before (the appointment)": no countdown possible
    safe_date: dt.date | None = None  # time span counted from the letter date
    days_left: int | None = None

    @property
    def due(self):
        return self.date or self.safe_date


@dataclass
class Result:
    pages: list
    sender: str
    topic: str
    summary: str
    actions: list
    if_ignored: str
    letter_date: dt.date | None = None
    deadlines: list = field(default_factory=list)  # DueDate, soonest first
    valid_until: list = field(default_factory=list)  # (what, date, context)
    amount: Found | None = None
    reference: str = ""
    reference_checked: bool = False  # the AI's quote is in the letter and contains the number


def build_result(answer, found, pages, today):
    labels = {f.id: getattr(answer, f.id) for f in found if f.id[0] in "dp"}
    letter_dates = [f.date for f in found if f.id[0] == "d" and labels[f.id].kind == "letter_date"]
    letter_date = letter_dates[0] if letter_dates else None

    deadlines, valid_until = [], []
    for f in found:
        label = labels.get(f.id)
        if not label:
            continue
        if label.kind == "valid_until" and f.date:
            valid_until.append((label.what.strip(), f.date, f.context))
        if label.kind not in DUE_KINDS:
            continue
        if f.date:
            deadlines.append(DueDate(label.kind, label.what.strip(), f.context, date=f.date))
        else:
            one, many = UNIT_NAMES[f.unit]
            # "within 14 days (after receiving this letter)" counted from the letter date is earlier than
            # the real start, so it's safe. "3 working days before the appointment" can't be counted like that.
            safe = add_period(letter_date, f.amount, f.unit) if letter_date and not f.before_event else None
            deadlines.append(DueDate(label.kind, label.what.strip(), f.context, period=f"{f.amount} {one if f.amount == 1 else many}",
                                     before_event=f.before_event, safe_date=safe))
    for d in deadlines:
        if d.due:
            d.days_left = (d.due - today).days
    deadlines.sort(key=lambda d: (d.due is None, d.due or dt.date.max))

    text = "\n".join(p.text for p in pages)
    reference = answer.reference.value.strip()
    checked = bool(words(reference)) and quote_in(answer.reference.quote, text) and words(reference) in words(answer.reference.quote)
    amount = next((f for f in found if f.id == answer.amount_to_pay), None)
    return Result(pages, answer.sender.strip(), answer.topic.strip(), answer.summary.strip(),
                  [a.strip() for a in answer.actions if a.strip()], answer.if_ignored.strip(),
                  letter_date, deadlines, valid_until, amount, reference, checked)


# ----------------------------------------------------------------------------- talking to Ollama
def ollama_call(model, call):
    """Run one Ollama request and turn its errors into messages the user can act on."""
    try:
        return call()
    except ConnectionError as e:
        raise LetterError("Ollama is not running. Open the Ollama app, then try again.") from e
    except ollama.ResponseError as e:
        if e.status_code == 404:
            raise LetterError(f"The AI model {model} is not downloaded yet. Run:  ollama pull {model}") from e
        if "structured output" in str(e.error):
            raise LetterError(
                "This Ollama install can't force the answer into JSON. Install the official app "
                "from ollama.com (Homebrew: brew install --cask ollama-app)."
            ) from e
        raise LetterError(f"Ollama returned an error: {e.error}") from e


def prepare_image(data):
    """Any photo (also iPhone .heic), turned upright, made smaller and saved as JPEG."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    from pillow_heif import register_heif_opener

    register_heif_opener()
    try:
        image = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    except (UnidentifiedImageError, OSError) as e:
        raise LetterError("This file is not a photo this tool can open. Use JPG, PNG, HEIC or PDF.") from e
    image.thumbnail((IMAGE_SIDE, IMAGE_SIDE))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=90)
    return out.getvalue()


def split_files(files):
    """[(name, bytes)] -> [(text or None, image or None)] per page, PDFs page by page."""
    import pypdfium2 as pdfium

    pages = []
    for name, data in files:
        if name.lower().endswith(".pdf"):
            try:
                pdf = pdfium.PdfDocument(data)
            except pdfium.PdfiumError as e:
                raise LetterError(f"Could not open {name}. Is it a broken PDF?") from e
            for page in pdf:
                text = page.get_textpage().get_text_range()
                if len(text.strip()) >= MIN_PDF_TEXT:
                    pages.append((text, None))
                else:  # a scanned page: turn it into a picture for the AI
                    picture = page.render(scale=200 / 72).to_pil()
                    out = io.BytesIO()
                    picture.convert("RGB").save(out, "PNG")
                    pages.append((None, prepare_image(out.getvalue())))
        elif name.lower().rsplit(".", 1)[-1] in IMAGE_TYPES:
            pages.append((None, prepare_image(data)))
        else:
            raise LetterError(f"{name}: use a photo (JPG, PNG, HEIC) or a PDF.")
    if not pages:
        raise LetterError("Add a photo or PDF of your letter first.")
    if len(pages) > MAX_PAGES:
        raise LetterError(f"That's {len(pages)} pages. This tool reads at most {MAX_PAGES}: add the most important ones.")
    return pages


def read_letter(files, client=None, model=MODEL, progress=None):
    """The letter's text, page by page. Photos and scans are read by the AI."""
    client = client or ollama.Client()
    pages = []
    for number, (text, image) in enumerate(split_files(files), 1):
        if progress:
            progress(f"Reading page {number}…")
        if text is not None:
            pages.append(Page(number, text.strip(), read_by_ai=False))
            continue
        reply = ollama_call(model, lambda: client.chat(
            model=model,
            messages=[{"role": "user", "content": READ_PROMPT.format(page=number), "images": [image]}],
            think=False,
            options={"temperature": 0, "num_ctx": CONTEXT_TOKENS, "num_predict": MAX_READ_TOKENS},
        ))
        text = reply.message.content.strip()
        if len(words(text).split()) < 10:
            raise LetterError(
                f"I couldn't read the text on page {number}. Take the photo from straight above, in good light, "
                "with the whole page in the picture."
            )
        pages.append(Page(number, text, read_by_ai=True, image=image))
    return pages


def found_list(found, prefix, title):
    items = [f for f in found if f.id[0] == prefix]
    lines = "\n".join(f'{f.id}: {f.text} · "{f.context}"' for f in items) or "(none)"
    return f"{title}:\n{lines}"


def explain(pages, language="English", client=None, model=MODEL, today=None):
    client = client or ollama.Client()
    letter = "\n\n".join(f"--- page {p.number} ---\n{p.text}" for p in pages)
    found = find_all(letter)
    shape = answer_shape(found)
    messages = [
        {"role": "system", "content": EXPLAIN_PROMPT.format(language=LANGUAGES[language])},
        {"role": "user", "content": f"LETTER:\n<<<\n{letter}\n>>>\n\n" + "\n\n".join([
            found_list(found, "d", "DATES"), found_list(found, "p", "TIME SPANS"), found_list(found, "a", "AMOUNTS")])},
    ]
    problem = None
    for temperature in (0, 0.3):  # a broken answer at temperature 0 would break the same way again
        reply = ollama_call(model, lambda: client.chat(
            model=model,
            messages=messages,
            format=shape.model_json_schema(),  # Ollama forces the reply into this JSON shape
            think=False,
            options={"temperature": temperature, "num_ctx": CONTEXT_TOKENS, "num_predict": MAX_ANSWER_TOKENS},
        ))
        try:
            answer = shape.model_validate_json(reply.message.content)
        except ValidationError as e:
            problem = e
            continue
        return build_result(answer, found, pages, today or dt.date.today())
    raise LetterError(f"The AI's answer was not in the expected format, twice. Try again.\n\n{problem}")


def check_letter(files, language="English", client=None, model=MODEL, today=None, progress=None):
    client = client or ollama.Client()
    pages = read_letter(files, client, model, progress)
    if progress:
        progress("Explaining the letter…")
    return explain(pages, language, client, model, today)


# ----------------------------------------------------------------------------- command line
def when(d):
    if d.before_event:
        return f"{d.period} before the date it refers to"
    if d.due is None:
        return f"within {d.period}, counted from the day you received the letter"
    left = d.days_left
    left = "today!" if left == 0 else f"{left} days left" if left > 0 else f"{-left} days ago!"
    label = d.due.strftime("%d.%m.%Y")
    return f"{label} ({left})" if d.date else f"to be safe by {label} ({left}): within {d.period}"


def print_result(r):
    print(f"\n{r.sender}: {r.topic}\n\n{r.summary}\n")
    for d in r.deadlines:
        print(f"  ⏰ {d.what}: {when(d)}")
    for what, date, _ in r.valid_until:
        print(f"  📅 {what}: valid until {date:%d.%m.%Y}")
    if r.amount:
        print(f"  💶 To pay: {r.amount.text}")
    if r.reference:
        print(f"  🔖 Reference: {r.reference}" + ("" if r.reference_checked else "   ⚠️ check this on your letter"))
    if r.actions:
        print("\nWhat to do:")
        for n, action in enumerate(r.actions, 1):
            print(f"  {n}. {action}")
    if r.if_ignored:
        print(f"\nIf you ignore it: {r.if_ignored}")
    print("\nThis is not legal advice.\n")


def main():
    parser = argparse.ArgumentParser(description="Explain an official German letter in simple words.")
    parser.add_argument("files", nargs="+", help="photos (JPG, PNG, HEIC) or a PDF of the letter, in page order")
    parser.add_argument("--language", default="English", choices=list(LANGUAGES))
    parser.add_argument("--model", default=MODEL, help=f"Ollama model (default: {MODEL})")
    args = parser.parse_args()
    try:
        files = [(Path(f).name, Path(f).read_bytes()) for f in args.files]
        print_result(check_letter(files, args.language, model=args.model, progress=print))
    except LetterError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
