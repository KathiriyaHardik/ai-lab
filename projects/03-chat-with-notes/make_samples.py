#!/usr/bin/env python3
"""Build the two fictional sample lecture PDFs in samples/ (slide-style, one topic per page).

    uv run make_samples.py

Every slide holds a few specific facts (numbers, dates, formulas), so evaluate.py can check
that a question finds the right page. The courses, people and dates are made up.
"""
from pathlib import Path

from fpdf import FPDF

SAMPLES = Path(__file__).with_name("samples")

ML_LECTURE = (
    "ml_lecture_evaluating_models.pdf",
    "DS-204 Introduction to Machine Learning",
    [
        ("Lecture 4: Evaluating Models", [
            "DS-204 Introduction to Machine Learning, winter term 2026/27",
            "Example University (fictional course, sample notes for testing)",
            "Lecturer: Dr. A. Example",
            "Today: train/test split, cross-validation, confusion matrix, precision, recall, F1, ROC/AUC, regression metrics",
        ]),
        ("Train/test split", [
            "We hold back data the model never sees during training: the test set.",
            "A typical split is 80% training data and 20% test data.",
            "Never tune hyperparameters on the test set. Use a separate validation set instead.",
            "The test set is used only once, at the very end, to report the final score.",
        ]),
        ("Cross-validation", [
            "k-fold cross-validation splits the data into k equal parts (folds).",
            "Each fold is used once as validation data while the other k-1 folds train the model.",
            "Common choices are k = 5 or k = 10.",
            "Stratified k-fold keeps the class proportions the same in every fold. Use it for imbalanced classes.",
        ]),
        ("The confusion matrix", [
            "True positive (TP): predicted positive, actually positive.",
            "False positive (FP): predicted positive, actually negative. Also called a type I error.",
            "False negative (FN): predicted negative, actually positive. Also called a type II error.",
            "True negative (TN): predicted negative, actually negative.",
        ]),
        ("Precision and recall", [
            "Precision = TP / (TP + FP): of everything we flagged, how much was right?",
            "Recall = TP / (TP + FN): of everything we should have found, how much did we find?",
            "Example spam filter: precision 0.9 means 9 out of 10 emails marked as spam really are spam.",
            "The same filter has a recall of 0.6: it catches 6 out of 10 spam emails.",
        ]),
        ("The F1 score", [
            "F1 is the harmonic mean of precision and recall: F1 = 2 * P * R / (P + R).",
            "It is only high when both precision and recall are high.",
            "Spam filter example: F1 = 2 * 0.9 * 0.6 / (0.9 + 0.6) = 0.72.",
            "Use F1 instead of accuracy when the classes are imbalanced.",
        ]),
        ("Overfitting and underfitting", [
            "Overfitting: very high training accuracy but much lower test accuracy. The model memorised noise.",
            "Underfitting: low accuracy on both training and test data. The model is too simple.",
            "Remedies for overfitting: more training data, L1 or L2 regularization, early stopping, dropout.",
            "Remedies for underfitting: a more complex model or better features.",
        ]),
        ("ROC curve and AUC", [
            "The ROC curve plots the true positive rate against the false positive rate for every threshold.",
            "AUC is the area under the ROC curve.",
            "AUC = 0.5 means the model is no better than random guessing.",
            "AUC = 1.0 means the model separates the two classes perfectly.",
        ]),
        ("Metrics for regression", [
            "MAE (mean absolute error): the average size of the errors.",
            "MSE (mean squared error): the average of the squared errors.",
            "RMSE is the square root of MSE and has the same unit as the target.",
            "RMSE punishes large errors more than MAE, because errors are squared before averaging.",
        ]),
        ("Exam information", [
            "The written exam for DS-204 takes place on 12 February 2027.",
            "It lasts 90 minutes.",
            "You may bring one handwritten A4 sheet with notes (both sides). No calculators.",
            "Topics: lectures 1 to 6, with a focus on model evaluation.",
        ]),
    ],
)

PM_SCRIPT = (
    "projektmanagement_kapitel2.pdf",
    "BWL-110 Grundlagen Projektmanagement",
    [
        ("Kapitel 2: Projektplanung", [
            "Modul BWL-110 Grundlagen Projektmanagement, Wintersemester 2026/27",
            "Beispiel-Hochschule (fiktiver Kurs, Beispielskript zum Testen)",
            "Dozentin: Prof. B. Beispiel",
            "Inhalte: magisches Dreieck, Projektstrukturplan, Netzplan, Gantt-Diagramm, Meilensteine, Risiken, Scrum",
        ]),
        ("Das magische Dreieck", [
            "Jedes Projekt bewegt sich zwischen drei Zielgrößen: Zeit, Kosten und Qualität.",
            "Ändert sich eine Größe, wirkt sich das auf die beiden anderen aus.",
            "Beispiel: Ein früherer Termin bei gleicher Qualität erhöht meist die Kosten.",
            "Manche Lehrbücher nennen statt Qualität den Leistungsumfang.",
        ]),
        ("Der Projektstrukturplan (PSP)", [
            "Der PSP zerlegt das Projekt in Teilprojekte und Arbeitspakete.",
            "Ein Arbeitspaket ist die kleinste Einheit im PSP und hat genau eine verantwortliche Person.",
            "Faustregel (8/80-Regel): Ein Arbeitspaket umfasst zwischen 8 und 80 Arbeitsstunden.",
            "Der PSP ist die Grundlage für Termin-, Kosten- und Ressourcenplanung.",
        ]),
        ("Netzplan und kritischer Pfad", [
            "Der Netzplan zeigt, welche Vorgänge voneinander abhängen.",
            "Der kritische Pfad ist die längste Kette von Vorgängen durch das Projekt.",
            "Vorgänge auf dem kritischen Pfad haben eine Pufferzeit von 0 Tagen.",
            "Verzögert sich ein Vorgang auf dem kritischen Pfad, verschiebt sich das Projektende.",
        ]),
        ("Das Gantt-Diagramm", [
            "Das Gantt-Diagramm stellt Vorgänge als Balken auf einer Zeitachse dar.",
            "Es ist nach Henry Gantt benannt, der es um 1910 bekannt machte.",
            "Gut geeignet, um Termine und Fortschritt zu kommunizieren.",
            "Abhängigkeiten zwischen Vorgängen sind im Netzplan oft besser zu erkennen.",
        ]),
        ("Meilensteine", [
            "Ein Meilenstein ist ein wichtiges Ereignis im Projekt, zum Beispiel eine Abnahme.",
            "Meilensteine haben keine Dauer: Sie sind ein Zeitpunkt, kein Zeitraum.",
            "Im Gantt-Diagramm werden sie meist als Raute dargestellt.",
            "An Meilensteinen wird geprüft, ob das Projekt wie geplant weiterlaufen kann.",
        ]),
        ("Risikomanagement", [
            "Risiko = Eintrittswahrscheinlichkeit x Schadenshöhe.",
            "Beispiel: 20 % Wahrscheinlichkeit x 50.000 Euro Schaden = 10.000 Euro Risikowert.",
            "Strategien: vermeiden, vermindern, übertragen (zum Beispiel Versicherung) oder bewusst tragen.",
            "Risiken werden in einer Risikoliste gesammelt und regelmäßig neu bewertet.",
        ]),
        ("Agil oder klassisch?", [
            "Klassische Projekte planen am Anfang alles im Detail (Wasserfallmodell).",
            "Scrum arbeitet in Sprints mit einer festen Länge von 1 bis 4 Wochen.",
            "Das Daily Scrum ist ein tägliches Treffen von höchstens 15 Minuten.",
            "Rollen in Scrum: Product Owner, Scrum Master und Developers.",
        ]),
        ("Hinweise zur Prüfung", [
            "Die Prüfungsleistung ist eine Hausarbeit mit 15 Seiten Umfang.",
            "Abgabe bis zum 31. März 2027 über das Lernportal.",
            "Thema: Planung eines eigenen Beispielprojekts mit PSP, Gantt-Diagramm und Risikoliste.",
            "Bewertung: 60 % Inhalt, 25 % Methodik, 15 % Form.",
        ]),
    ],
)


def build(filename, course, slides):
    pdf = FPDF(orientation="landscape", format="A4")
    pdf.set_auto_page_break(False)
    for number, (title, bullets) in enumerate(slides, 1):
        pdf.add_page()
        pdf.set_fill_color(37, 99, 235)
        pdf.rect(0, 0, 297, 6, style="F")
        pdf.set_xy(20, 22)
        pdf.set_font("Helvetica", "B", 26)
        pdf.set_text_color(15, 23, 42)
        pdf.multi_cell(257, 12, title)
        pdf.set_y(pdf.get_y() + 8)
        pdf.set_font("Helvetica", "", 16)
        pdf.set_text_color(30, 41, 59)
        for bullet in bullets:
            pdf.set_x(24)
            pdf.multi_cell(249, 9, f"- {bullet}")
            pdf.set_y(pdf.get_y() + 4)
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(100, 116, 139)
        pdf.set_xy(20, 196)
        pdf.cell(200, 6, course)
        pdf.set_xy(237, 196)
        pdf.cell(40, 6, str(number), align="R")
    pdf.output(SAMPLES / filename)
    print(f"{filename}: {len(slides)} pages")


if __name__ == "__main__":
    SAMPLES.mkdir(exist_ok=True)
    for lecture in (ML_LECTURE, PM_SCRIPT):
        build(*lecture)
