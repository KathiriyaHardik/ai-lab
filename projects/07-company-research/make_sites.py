#!/usr/bin/env python3
"""Build the three fictional company websites in samples/sites/ (plain HTML, one folder per company).

    uv run make_sites.py

Every company, person, number and event is made up, and every page says so. The sites contain traps
for the tests: a page that robots.txt forbids, links to other websites, legal pages with people's
names, and a blog post that quotes a claim the company disagrees with.
"""
import html
from pathlib import Path

SITES = Path(__file__).with_name("samples") / "sites"
MARK = "Fictional company for testing. Nothing on this website is real."

COMPANIES = {
    "brightline": dict(
        name="Brightline Analytics GmbH", lang="en", robots="User-agent: *\nDisallow: /internal/\n",
        nav=[("About us", "about.html"), ("Products", "products.html"), ("Careers", "careers.html"), ("News", "news/index.html"),
             ("Blog", "blog/ai-hype.html")],
        footer=[("Imprint", "impressum.html"), ("Privacy", "datenschutz.html"), ("Internal", "internal/roadmap.html"),
                ("LinkedIn", "https://www.linkedin.com/company/brightline-example"), ("Email us", "mailto:hello@brightline.example")],
        pages={
            "index.html": ("AI assistants that answer from your own documents", [
                "Brightline builds AI assistants that help manufacturers find answers in manuals, service reports and wikis.",
                "Trusted by 35 manufacturers in Germany and Austria."]),
            "about.html": ("About Brightline", [
                "Brightline was founded in 2019 in Stuttgart. Today 42 people work at Brightline, in Stuttgart and Leipzig.",
                "Our mission: make company knowledge easy to find, so experts spend their time on real problems.",
                "Founders: Dr. Lena Example (CEO) and Tom Sample (CTO)."]),
            "products.html": ("Products", [
                "Brightline Ask answers questions from manuals and always shows its sources.",
                "Brightline Check tests answer quality automatically before every release, with test questions written by our customers."]),
            "careers.html": ("Careers", [
                "Junior AI Engineer (m/f/d), Stuttgart or remote in Germany: join our team of five engineers and build RAG pipelines from prototype to production.",
                "Working Student Data (m/f/d), 20 hours per week: help us prepare test data for Brightline Check.",
                "We offer 30 days of holiday and a yearly learning budget."]),
            "news/index.html": ("News", [
                ("Brightline raises 8 million euros", "series-a.html"),
                ("Partnership with Example Mobility", "partnership.html")]),
            "news/series-a.html": ("Brightline raises 8 million euros", [
                "September 2026: Brightline raises 8 million euros in a Series A round led by Neckar Ventures.",
                "The money will fund a new team for German-language AI assistants."]),
            "news/partnership.html": ("Partnership with Example Mobility", [
                "June 2026: Brightline and Example Mobility GmbH start a partnership to bring Brightline Ask to 1,200 service technicians."]),
            "blog/ai-hype.html": ("Our view on AI and engineers", [
                "Some say AI will replace half of all engineers. We disagree.",
                "We think AI will replace the searching, not the engineers: our customers' engineers spend less time looking for answers."]),
            "impressum.html": ("Imprint", [
                "Brightline Analytics GmbH, Example Street 1, 70173 Stuttgart. Managing directors: Dr. Lena Example, Tom Sample.",
                "Commercial register: Amtsgericht Stuttgart, HRB 000000."]),
            "datenschutz.html": ("Privacy policy", ["Data protection officer: Max Placeholder, privacy@brightline.example."]),
            "internal/roadmap.html": ("Internal roadmap", ["Secret: we plan to move our headquarters to Berlin in 2027."]),
        },
    ),
    "nordlicht": dict(
        name="Nordlicht Software GmbH", lang="de", robots="User-agent: *\nDisallow: /intern/\n",
        nav=[("Über uns", "ueber-uns.html"), ("Lösungen", "loesungen.html"), ("Karriere", "karriere.html"), ("Presse", "presse/index.html")],
        footer=[("Impressum", "impressum.html"), ("Datenschutz", "datenschutz.html"), ("Intern", "intern/notizen.html"),
                ("Xing", "https://www.xing.com/pages/nordlicht-example")],
        pages={
            "index.html": ("Software für eine digitale Verwaltung", [
                "Nordlicht entwickelt Software, mit der Städte und Gemeinden ihre Dienste online anbieten.",
                "Über 60 Städte und Gemeinden nutzen unsere Lösungen."]),
            "ueber-uns.html": ("Über uns", [
                "Nordlicht wurde 2012 in Stuttgart gegründet. Heute arbeiten 120 Menschen bei Nordlicht.",
                "Unser Ziel: Behördengänge, die man vom Sofa aus erledigen kann.",
                "Geschäftsführung: Anna Beispiel und Jan Muster."]),
            "loesungen.html": ("Lösungen", [
                "Bürgerportal Plus: Bürgerinnen und Bürger stellen Anträge online, rund um die Uhr.",
                "KI-Assistent für die Sachbearbeitung: beantwortet Fragen zu Vorschriften und Formularen (derzeit in der Pilotphase)."]),
            "karriere.html": ("Karriere", [
                "Werkstudent (m/w/d) Generative KI, 16 bis 20 Stunden pro Woche, Stuttgart-Mitte: Du entwickelst Prototypen für unseren KI-Assistenten.",
                "Senior Backend Developer (m/w/d), Stuttgart oder remote.",
                "Wir bieten flexible Arbeitszeiten und ein Jobticket."]),
            "presse/index.html": ("Presse", [
                ("Nordlicht gewinnt Ausschreibung der Stadt Beispielstadt", "ausschreibung.html"),
                ("Start des KI-Piloten", "ki-pilot.html")]),
            "presse/ausschreibung.html": ("Nordlicht gewinnt Ausschreibung der Stadt Beispielstadt", [
                "Oktober 2026: Nordlicht gewinnt die Ausschreibung der Stadt Beispielstadt für ein neues Bürgerportal."]),
            "presse/ki-pilot.html": ("Start des KI-Piloten", [
                "Mai 2026: Unser KI-Assistent startet als Pilot in drei Landratsämtern."]),
            "impressum.html": ("Impressum", ["Nordlicht Software GmbH, Musterweg 5, 70173 Stuttgart. Geschäftsführung: Anna Beispiel, Jan Muster."]),
            "datenschutz.html": ("Datenschutz", ["Datenschutzbeauftragte: Eva Platzhalter, datenschutz@nordlicht.example."]),
            "intern/notizen.html": ("Interne Notizen", ["Vertraulich: Wir verhandeln mit einem Käufer."]),
        },
    ),
    "neckar-robotics": dict(
        name="Neckar Robotics AG", lang="en", robots="User-agent: *\nDisallow: /admin/\n",
        nav=[("Company", "company.html"), ("Products", "products.html"), ("Jobs", "jobs.html"), ("Blog", "blog/index.html")],
        footer=[("Imprint", "impressum.html"), ("Privacy", "privacy.html"), ("YouTube", "https://www.youtube.com/@neckar-robotics-example")],
        pages={
            "index.html": ("Warehouse robots that pick and pack", [
                "Neckar Robotics builds robots that pick and pack orders in warehouses, next to people."]),
            "company.html": ("Our company", [
                "Founded in 2015 in Esslingen. Today 300 people work at Neckar Robotics, in Esslingen and Munich.",
                "Head of Engineering: Paula Placeholder."]),
            "products.html": ("Products", [
                "The NR-7 picking robot picks up to 600 items per hour.",
                "Fleet Planner software plans the work of all robots in a warehouse."]),
            "jobs.html": ("Jobs", [
                "AI Automation Engineer (m/f/d), Esslingen: build tools that plan robot fleets with machine learning.",
                "Intern Robotics Software (m/f/d), 6 months."]),
            "blog/index.html": ("Blog", [
                ("Our second office: Munich", "munich-office.html"),
                ("Customer story: Sample Retail", "customer-story.html")]),
            "blog/munich-office.html": ("Our second office: Munich", [
                "August 2026: we opened our second office in Munich with 40 engineers."]),
            "blog/customer-story.html": ("Customer story: Sample Retail", [
                "Sample Retail AG cut its packing time by 30 percent with the NR-7."]),
            "impressum.html": ("Imprint", ["Neckar Robotics AG, Example Road 9, 73728 Esslingen. Board: Paula Placeholder."]),
            "privacy.html": ("Privacy", ["Contact for privacy questions: privacy@neckar-robotics.example."]),
        },
    ),
}


def link(href, depth):
    if href.startswith(("http", "mailto:")):
        return href
    return "../" * depth + href


def page(company, path, title, body):
    depth = path.count("/")
    e = html.escape
    nav = " · ".join(f'<a href="{link(h, depth)}">{e(t)}</a>' for t, h in company["nav"])
    footer = " · ".join(f'<a href="{link(h, depth)}">{e(t)}</a>' for t, h in company["footer"])
    if body and isinstance(body[0], tuple):  # an index page: links to its articles
        content = "<ul>" + "".join(f'<li><a href="{e(h)}">{e(t)}</a></li>' for t, h in body) + "</ul>"
    else:
        content = "".join(f"<p>{e(p)}</p>" for p in body)
    return (f'<!doctype html>\n<html lang="{company["lang"]}"><head><meta charset="utf-8"><title>{e(title)} | {e(company["name"])}</title>'
            f'<script>console.log("analytics");</script><style>body{{font-family:sans-serif}}</style></head>\n'
            f'<body><nav><a href="{link("index.html", depth)}">{e(company["name"])}</a> · {nav}</nav>\n'
            f'<main><h1>{e(title)}</h1>{content}</main>\n'
            f'<footer>{footer}<p>{e(MARK)}</p></footer></body></html>\n')


if __name__ == "__main__":
    for slug, company in COMPANIES.items():
        root = SITES / slug
        for path, (title, body) in company["pages"].items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(page(company, path, title, body))
        (root / "robots.txt").write_text(company["robots"])
        print(f"{slug}: {len(company['pages'])} pages")
