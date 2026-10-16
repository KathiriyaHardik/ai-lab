"""Fast tests for the Scout: a fake web (httpx MockTransport) serves the fictional sample websites."""
import sys
from pathlib import Path

import httpx
import pytest

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE))
import crawler  # noqa: E402

SITES = HERE / "samples" / "sites"
BASE = "https://brightline.example/"


def fake_web(folder=SITES / "brightline", extra=None, log=None):
    """A website from a sample folder: every request is answered from the files, and recorded."""
    def handle(request):
        if log is not None:
            log.append(str(request.url))
        if request.url.host != "brightline.example":
            raise AssertionError(f"the Scout must not visit other websites: {request.url}")
        path = request.url.path.lstrip("/") or "index.html"
        if path.endswith("/"):
            path += "index.html"
        if extra and path in extra:
            return extra[path]
        file = folder / path
        if not file.is_file():
            return httpx.Response(404, text="not found")
        kind = "text/plain" if path.endswith(".txt") else "text/html; charset=utf-8"
        return httpx.Response(200, text=file.read_text(), headers={"content-type": kind})
    return httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True)


def paths(urls):
    return [httpx.URL(u).path for u in urls]


def test_reads_the_useful_pages_first():
    result = crawler.crawl(BASE, fake_web(), pause=0)
    read = paths(p.url for p in result.pages)
    assert read[0] == "/" and len(read) == crawler.MAX_PAGES
    assert set(read[1:4]) == {"/about.html", "/careers.html", "/products.html"}  # most useful pages next
    assert "/news/series-a.html" in read  # an article behind the news page


def test_skips_other_websites_legal_pages_and_what_robots_txt_forbids():
    log = []
    result = crawler.crawl(BASE, fake_web(log=log), pause=0)
    why = {httpx.URL(u).path if u.startswith("http") else u: reason for u, reason in result.skipped}
    assert why["/impressum.html"].startswith("legal or privacy page")
    assert why["/datenschutz.html"].startswith("legal or privacy page")
    assert why["/internal/roadmap.html"] == "robots.txt asks robots not to read it"
    assert any("linkedin.com" in u and r == "another website" for u, r in result.skipped)
    assert all("impressum" not in u and "internal" not in u for u in log)  # never even requested


def test_the_same_page_is_read_once():
    result = crawler.crawl(BASE + "index.html", fake_web(), pause=0)
    read = paths(p.url for p in result.pages)
    assert len(read) == len(set(read)) and read.count("/") == 1


def test_page_text_has_no_menus_scripts_or_footers():
    result = crawler.crawl(BASE, fake_web(), pause=0)
    about = next(p for p in result.pages if p.url.endswith("about.html"))
    assert about.title == "About Brightline | Brightline Analytics GmbH"
    assert about.text.startswith("About Brightline\nBrightline was founded in 2019 in Stuttgart.")
    assert "analytics" not in about.text and "Imprint" not in about.text and "Fictional company" not in about.text


def test_at_most_a_few_pages_and_a_pause_between_them(monkeypatch):
    pauses = []
    monkeypatch.setattr(crawler.time, "sleep", pauses.append)
    result = crawler.crawl(BASE, fake_web(), max_pages=3)
    assert len(result.pages) == 3 and pauses == [crawler.PAUSE] * 2


def test_a_site_whose_robots_txt_forbids_everything():
    web = fake_web(extra={"robots.txt": httpx.Response(200, text="User-agent: *\nDisallow: /\n")})
    with pytest.raises(crawler.CrawlError, match="robots.txt asks tools like this one not to read it"):
        crawler.crawl(BASE, web, pause=0)


def test_no_robots_txt_means_everything_may_be_read():
    web = fake_web(extra={"robots.txt": httpx.Response(404)})
    assert "/internal/roadmap.html" in paths(p.url for p in crawler.crawl(BASE, web, max_pages=20, pause=0).pages)


def test_addresses_without_https_and_broken_pages():
    web = fake_web(extra={"careers.html": httpx.Response(500)})
    result = crawler.crawl("brightline.example", web, pause=0)
    assert result.start == BASE
    assert any(u.endswith("careers.html") and r.startswith("not a readable page") for u, r in result.skipped)


def test_a_website_that_cant_be_reached():
    def down(request):
        raise httpx.ConnectError("no route")
    with pytest.raises(crawler.CrawlError, match="Couldn't read"):
        crawler.crawl(BASE, httpx.Client(transport=httpx.MockTransport(down)), pause=0)


@pytest.mark.parametrize("url, expected", [
    ("https://a.example/about.html", "https://a.example/about.html"), ("https://a.example/news/index.html", "https://a.example/news/"),
    ("https://a.example/#top", "https://a.example/"), ("https://a.example", "https://a.example/"),
])
def test_normal_addresses(url, expected):
    assert crawler.normal(url) == expected


def test_the_samples_can_be_served_locally():
    urls = crawler.serve_samples()
    assert set(urls) == {"brightline", "nordlicht", "neckar-robotics"}
    result = crawler.crawl(urls["nordlicht"], pause=0)
    assert any(p.url.endswith("karriere.html") for p in result.pages)


def test_www_and_a_redirect_count_as_the_same_website():
    def handle(request):
        if request.url.host == "brightline.example":  # the bare address redirects, like many real sites
            return httpx.Response(301, headers={"location": "https://www.brightline.example" + request.url.path})
        page = '<a href="https://www.brightline.example/about.html">About</a> <a href="/careers.html">Careers</a>'
        return httpx.Response(200, text="User-agent: *\nAllow: /" if request.url.path == "/robots.txt" else page,
                              headers={"content-type": "text/html"})
    web = httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True)
    result = crawler.crawl("brightline.example", web, pause=0)
    assert paths(p.url for p in result.pages) == ["/", "/about.html", "/careers.html"]
    assert crawler.host("https://WWW.Brightline.example:443/x") == "brightline.example"


def test_a_redirect_to_a_page_already_read_is_not_read_again():
    def handle(request):
        if request.url.path == "/about":
            return httpx.Response(301, headers={"location": "/about/"})
        page = '<a href="/about/">About</a> <a href="/about">About again</a> <a href="/jobs/">Jobs</a>'
        return httpx.Response(200, text="" if request.url.path == "/robots.txt" else page, headers={"content-type": "text/html"})
    web = httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True)
    assert paths(p.url for p in crawler.crawl(BASE, web, pause=0).pages) == ["/", "/about/", "/jobs/"]
