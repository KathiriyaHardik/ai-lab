#!/usr/bin/env python3
"""Build the fictional sample letters in samples/: a clean scan (.png) and a phone photo (.jpg) of each.

    uv run make_samples.py

The letters look like real German official letters (DIN 5008 layout), but every sender,
person, number and bank account is made up, and each page is marked MUSTER. Letter 1 is also
saved as a PDF with real text, to test the PDF path.
"""
import random
from pathlib import Path

import pypdfium2 as pdfium
from fpdf import FPDF
from PIL import Image, ImageDraw, ImageFilter

SAMPLES = Path(__file__).with_name("samples")
RECIPIENT = ["Herrn", "Alex Muster", "Musterweg 12", "12345 Beispielstadt"]
MARK = "MUSTER - fiktives Beispiel zum Testen, kein echtes Schreiben. Alle Namen und Nummern sind erfunden."

LETTERS = [
    {
        "key": "letter1_finanzamt",
        "sender": ["Finanzamt Beispielstadt", "Steuerstraße 1", "12345 Beispielstadt", "Telefon 01234 567-0"],
        "info": [("Steuernummer", "12/345/67890"), ("Datum", "06.10.2026")],
        "subject": "Bescheid für 2025 über Einkommensteuer und Solidaritätszuschlag",
        "body": [
            "Sehr geehrter Herr Muster,",
            "die Einkommensteuer für das Jahr 2025 wird auf 6.812,00 € festgesetzt. "
            "Bereits gezahlt (Lohnsteuer): 5.577,44 €.",
            "Abrechnung: Sie müssen noch 1.234,56 € zahlen. Bitte zahlen Sie den Betrag bis zum 09.11.2026 "
            "auf das unten genannte Konto und geben Sie dabei Ihre Steuernummer an. Wenn Sie nicht rechtzeitig "
            "zahlen, entstehen Säumniszuschläge.",
            "Rechtsbehelfsbelehrung: Gegen diesen Bescheid ist der Einspruch zulässig. Der Einspruch ist "
            "innerhalb eines Monats nach Bekanntgabe dieses Bescheids beim Finanzamt Beispielstadt schriftlich "
            "oder elektronisch einzulegen.",
            "Mit freundlichen Grüßen\nIhr Finanzamt Beispielstadt",
        ],
        "footer": "Bankverbindung: Landeskasse Beispielstadt, IBAN DE00 1234 5678 9012 3456 78",
    },
    {
        "key": "letter2_stadtwerke",
        "sender": ["Stadtwerke Beispielstadt GmbH", "Kundenservice", "Energieweg 5", "12345 Beispielstadt"],
        "info": [("Kundennummer", "4711-0815"), ("Rechnungsnummer", "R-2026-08812"), ("Datum", "01.10.2026")],
        "subject": "Zahlungserinnerung zu Ihrer Rechnung vom 28.08.2026",
        "body": [
            "Sehr geehrter Herr Muster,",
            "für die oben genannte Rechnung für Strom konnten wir bis heute leider keinen Zahlungseingang "
            "feststellen. Sicher haben Sie die Zahlung nur übersehen.",
            "Offener Rechnungsbetrag: 86,40 €\nMahngebühr: 5,00 €\nGesamtbetrag: 91,40 €",
            "Bitte überweisen Sie den Gesamtbetrag von 91,40 € bis spätestens 20.10.2026 unter Angabe Ihrer "
            "Kundennummer. Sollte die Zahlung bis dahin nicht bei uns eingehen, müssen wir die Forderung an ein "
            "Inkassounternehmen abgeben. Dadurch entstehen Ihnen weitere Kosten.",
            "Haben Sie in den letzten Tagen bereits gezahlt? Dann betrachten Sie dieses Schreiben bitte als "
            "gegenstandslos.",
            "Mit freundlichen Grüßen\nIhre Stadtwerke Beispielstadt",
        ],
        "footer": "Stadtwerke Beispielstadt GmbH, IBAN DE00 9876 5432 1098 7654 32",
    },
    {
        "key": "letter3_auslaenderbehoerde",
        "sender": ["Stadt Beispielstadt", "Ausländerbehörde", "Rathausplatz 3", "12345 Beispielstadt"],
        "info": [("Aktenzeichen", "AB-2026-31415"), ("Datum", "29.09.2026")],
        "subject": "Verlängerung Ihres Aufenthaltstitels - Terminbestätigung",
        "body": [
            "Sehr geehrter Herr Muster,",
            "Ihr Aufenthaltstitel ist gültig bis zum 31.12.2026. Für die Verlängerung haben wir für Sie folgenden "
            "Termin vereinbart: Donnerstag, 03.12.2026, 09:30 Uhr, Raum 2.14.",
            "Bitte bringen Sie folgende Unterlagen mit: Ihren gültigen Reisepass, ein aktuelles biometrisches "
            "Passfoto, Ihren Mietvertrag, Ihre Gehaltsnachweise der letzten drei Monate und einen Nachweis über "
            "Ihre Krankenversicherung.",
            "Die Gebühr für die Verlängerung beträgt 100,00 € und ist beim Termin zu zahlen.",
            "Wenn Sie den Termin nicht wahrnehmen können, sagen Sie ihn bitte spätestens drei Werktage vorher ab.",
            "Mit freundlichen Grüßen\nIm Auftrag\nB. Beispiel",
        ],
        "footer": "Öffnungszeiten: Montag bis Freitag 8:00 - 12:00 Uhr",
    },
    {
        "key": "letter4_krankenkasse",
        "sender": ["Gesundheitskasse Beispiel", "Mitgliederservice", "Postfach 10 20 30", "12345 Beispielstadt"],
        "info": [("Versichertennummer", "X123456789"), ("Datum", "05.10.2026")],
        "subject": "Ihre Beiträge als freiwillig versichertes Mitglied",
        "body": [
            "Sehr geehrter Herr Muster,",
            "für die Berechnung Ihrer Beiträge benötigen wir Angaben zu Ihrem aktuellen Einkommen.",
            "Bitte senden Sie uns den beigefügten Fragebogen ausgefüllt und unterschrieben zusammen mit Ihrem "
            "letzten Einkommensteuerbescheid innerhalb von 14 Tagen nach Erhalt dieses Schreibens zurück.",
            "Wenn wir die Unterlagen nicht rechtzeitig erhalten, sind wir gesetzlich verpflichtet, Ihre Beiträge "
            "nach dem Höchstbetrag festzusetzen.",
            "Bei Fragen erreichen Sie uns telefonisch unter 01234 98765.",
            "Mit freundlichen Grüßen\nIhre Gesundheitskasse Beispiel",
        ],
        "footer": "Anlage: Fragebogen zum Einkommen",
    },
    {
        "key": "letter5_gesundheitskarte",
        "sender": ["Gesundheitskasse Beispiel", "Mitgliederservice", "Postfach 10 20 30", "12345 Beispielstadt"],
        "info": [("Versichertennummer", "X123456789"), ("Datum", "07.10.2026")],
        "subject": "Ihre neue elektronische Gesundheitskarte",
        "body": [
            "Sehr geehrter Herr Muster,",
            "anbei erhalten Sie Ihre neue elektronische Gesundheitskarte. Die Karte ist ab sofort gültig. "
            "Bitte zeigen Sie sie bei jedem Arztbesuch vor.",
            "Ihre alte Karte ist nicht mehr gültig. Sie können sie selbst unbrauchbar machen, zum Beispiel "
            "durch Zerschneiden des Chips.",
            "Sie müssen nichts weiter tun.",
            "Mit freundlichen Grüßen\nIhre Gesundheitskasse Beispiel",
        ],
        "footer": "Anlage: Elektronische Gesundheitskarte",
    },
]


def letter_pdf(letter):
    pdf = FPDF(format="A4")
    pdf.core_fonts_encoding = "windows-1252"  # € and German quotes
    pdf.set_auto_page_break(False)
    pdf.add_page()
    pdf.set_text_color(20, 20, 20)
    # sender, top right
    pdf.set_xy(125, 18)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 5.5, letter["sender"][0], new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9.5)
    for line in letter["sender"][1:]:
        pdf.set_x(125)
        pdf.cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
    # return address and recipient (address window)
    pdf.set_xy(25, 48)
    pdf.set_font("Helvetica", "", 7)
    pdf.cell(0, 4, " · ".join([letter["sender"][0]] + letter["sender"][-2:]))
    pdf.set_font("Helvetica", "", 11)
    pdf.set_xy(25, 55)
    for line in RECIPIENT:
        pdf.set_x(25)
        pdf.cell(0, 5.5, line, new_x="LMARGIN", new_y="NEXT")
    # info block
    pdf.set_font("Helvetica", "", 9.5)
    pdf.set_xy(125, 55)
    for label, value in letter["info"]:
        pdf.set_x(125)
        pdf.cell(32, 5.5, f"{label}:")
        pdf.cell(0, 5.5, value, new_x="LMARGIN", new_y="NEXT")
    # subject and body
    pdf.set_xy(25, 98)
    pdf.set_font("Helvetica", "B", 11.5)
    pdf.multi_cell(160, 6, letter["subject"])
    pdf.set_font("Helvetica", "", 10.5)
    pdf.set_y(pdf.get_y() + 6)
    for paragraph in letter["body"]:
        pdf.set_x(25)
        pdf.multi_cell(160, 5.6, paragraph)
        pdf.set_y(pdf.get_y() + 4)
    # footer
    pdf.set_font("Helvetica", "", 8)
    pdf.set_xy(25, 270)
    pdf.cell(0, 4, letter["footer"], new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(25)
    pdf.set_text_color(150, 30, 30)
    pdf.cell(0, 4, MARK)
    return bytes(pdf.output())


def scan(pdf_bytes):
    """The page as a clean 150 dpi image, like a flatbed scan."""
    return pdfium.PdfDocument(pdf_bytes)[0].render(scale=150 / 72).to_pil().convert("RGB")


def phone_photo(page, seed):
    """Make a scan look like a quick phone photo: on a table, tilted, uneven light, a bit soft."""
    rnd = random.Random(seed)
    w, h = page.size
    table = Image.new("RGB", (int(w * 1.16), int(h * 1.12)), (122, 104, 86))
    table.paste(page, ((table.width - w) // 2, (table.height - h) // 2))
    photo = table.rotate(rnd.uniform(-3, 3), resample=Image.Resampling.BICUBIC, fillcolor=(122, 104, 86))
    # perspective: map a slightly skewed four-corner shape onto the whole picture
    tw, th = photo.size
    j = lambda: rnd.uniform(0.0, 0.035)  # noqa: E731
    quad = (tw * j(), th * j(), tw * j(), th * (1 - j()), tw * (1 - j()), th * (1 - j()), tw * (1 - j()), th * j())
    photo = photo.transform(photo.size, Image.Transform.QUAD, quad, Image.Resampling.BICUBIC)
    # light from the top left, darker towards the bottom right
    shade = Image.new("L", photo.size)
    ImageDraw.Draw(shade).rectangle((0, 0, *photo.size), fill=0)
    for y in range(0, photo.height, 8):
        for x in range(0, photo.width, 8):
            level = int(255 * (0.78 + 0.22 * (1 - (x / photo.width + y / photo.height) / 2)))
            shade.paste(level, (x, y, x + 8, y + 8))
    photo = Image.composite(photo, Image.new("RGB", photo.size, (0, 0, 0)), shade.filter(ImageFilter.GaussianBlur(40)))
    photo = photo.filter(ImageFilter.GaussianBlur(0.9))
    return photo.resize((1500, round(1500 * photo.height / photo.width)), Image.Resampling.LANCZOS)


if __name__ == "__main__":
    SAMPLES.mkdir(exist_ok=True)
    for n, letter in enumerate(LETTERS, 1):
        pdf_bytes = letter_pdf(letter)
        if n == 1:
            (SAMPLES / f"{letter['key']}.pdf").write_bytes(pdf_bytes)
        page = scan(pdf_bytes)
        page.save(SAMPLES / f"{letter['key']}_scan.png", optimize=True)
        phone_photo(page, seed=n).save(SAMPLES / f"{letter['key']}_photo.jpg", quality=72)
        print(f"{letter['key']}: scan + photo")
