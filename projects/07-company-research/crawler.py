"""The Scout: reads a few public pages of one company's own website, politely.

Rules it follows:
  * only pages on the same website (no links to LinkedIn, YouTube or other sites)
  * only what the site's robots.txt allows
  * no legal and privacy pages (Impressum, Datenschutz...): they are mostly about people, not the company
  * at most MAX_PAGES pages, one request at a time with a pause in between, and it says who it is
The most useful pages come first: about, careers, news, products.
"""
import http.server
import re
import threading
import time
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

MAX_PAGES = 8
MAX_DEPTH = 2  # the start page, the pages it links to, and articles behind a news or careers page
PAUSE = 1.0  # seconds between requests, so the Scout never stresses a website
MAX_PAGE_CHARS = 6000
USER_AGENT = "HardiksAILab-CompanyResearch/1.0 (personal research tool; +https://github.com/KathiriyaHardik/ai-lab)"

LEGAL = ("impressum", "imprint", "datenschutz", "privacy", "cookie", "agb", "terms", "legal", "disclaimer")
NOT_PAGES = ("login", "signin", "account", "cart", "warenkorb", "checkout", "wp-admin")
FILE_TYPES = (".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".zip", ".mp4", ".webp", ".ico", ".css", ".js", ".xml")
USEFUL = {  # words in a link -> how useful the page probably is
    "about": 6, "ueber-uns": 6, "über-uns": 6, "company": 6, "unternehmen": 6, "who-we-are": 6,
    "careers": 6, "career": 6, "karriere": 6, "jobs": 6, "stellen": 6,
    "news": 5, "presse": 5, "press": 5, "aktuelles": 5, "blog": 4,
    "products": 5, "produkte": 5, "solutions": 5, "loesungen": 5, "lösungen": 5, "services": 4, "leistungen": 4,
    "customers": 3, "kunden": 3, "references": 3, "referenzen": 3,
}


class CrawlError(Exception):
    """The website can't be read (wrong address, offline, robots.txt forbids it...)."""


@dataclass
class Page:
    id: str  # p1, p2... how the agents refer to it
    url: str
    title: str
    text: str


@dataclass
class Crawl:
    start: str
    pages: list = field(default_factory=list)
    skipped: list = field(default_factory=list)  # (url, why it wasn't read)


def page_text(html):
    """Title and readable text of a page, without menus, footers and scripts."""
    soup = BeautifulSoup(html, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    for tag in soup(["head", "script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe"]):
        tag.decompose()
    text = soup.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text))
    return title, text[:MAX_PAGE_CHARS]


def normal(url):
    """One spelling per page: no #fragment, and "/" and "/index.html" are the same page."""
    url, _ = urldefrag(url)
    url = re.sub(r"/index\.(html?|php)$", "/", url)
    return url + "/" if urlparse(url).path == "" else url


def usefulness(url, label=""):
    words = f"{urlparse(url).path} {label}".lower()
    return max((score for word, score in USEFUL.items() if word in words), default=1)


def host(url_or_netloc):
    """'https://www.Example.com:443/x' -> 'example.com': www. and the address after a redirect are the same site."""
    netloc = urlparse(url_or_netloc).netloc if "//" in url_or_netloc else url_or_netloc
    netloc = netloc.lower().split("@")[-1]
    netloc = re.sub(r":(80|443)$", "", netloc)
    return netloc[4:] if netloc.startswith("www.") else netloc


def why_not(url, site):
    """None if the Scout may read this link, otherwise the reason it doesn't."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return "not a web page"
    if host(parsed.netloc) != site:
        return "another website"
    path = parsed.path.lower()
    if path.endswith(FILE_TYPES):
        return "a file, not a page"
    if any(w in path for w in LEGAL):
        return "legal or privacy page (about people, not the company)"
    if any(w in path for w in NOT_PAGES):
        return "login or shop page"
    return None


def read_robots(client, start):
    robots = RobotFileParser()
    try:
        r = client.get(urljoin(start, "/robots.txt"))
        robots.parse(r.text.splitlines() if r.status_code == 200 else [])
    except httpx.HTTPError:
        robots.parse([])  # no robots.txt: everything is allowed
    return robots


def crawl(start, client=None, max_pages=MAX_PAGES, pause=PAUSE, progress=None):
    """Read up to max_pages pages of the website at `start`, most useful first."""
    start = start.strip()
    if not re.match(r"https?://", start):
        start = "https://" + start
    start = normal(start)
    site = host(urlparse(start).netloc)
    if not site:
        raise CrawlError("That doesn't look like a website address.")
    own_client = client is None
    client = client or httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=10)
    result, robots = Crawl(start), None
    try:
        robots = read_robots(client, start)
        if not robots.can_fetch(USER_AGENT, start):
            raise CrawlError("This website's robots.txt asks tools like this one not to read it, so the Scout stops here.")
        queue, seen = [(100, 0, start)], {start}  # (usefulness, depth, url)
        while queue and len(result.pages) < max_pages:
            queue.sort(key=lambda item: (-item[0], item[1]))
            _, depth, url = queue.pop(0)
            if result.pages or result.skipped:
                time.sleep(pause)
            if progress:
                progress(f"Reading {url}")
            try:
                r = client.get(url)
            except httpx.HTTPError as e:
                result.skipped.append((url, f"couldn't be loaded ({type(e).__name__})"))
                continue
            if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
                result.skipped.append((url, f"not a readable page (status {r.status_code})"))
                continue
            if not result.pages:  # the first page may redirect (example.com -> www.example.com): follow the site there
                site = host(str(r.url))
            landed = normal(str(r.url))
            if any(normal(p.url) == landed for p in result.pages):  # "/about" redirected to the "/about/" already read
                continue
            seen.add(landed)
            title, text = page_text(r.text)
            result.pages.append(Page(f"p{len(result.pages) + 1}", str(r.url), title, text))
            if depth >= MAX_DEPTH:
                continue
            for a in BeautifulSoup(r.text, "html.parser").find_all("a", href=True):
                link = normal(urljoin(str(r.url), a["href"]))
                if link in seen:
                    continue
                seen.add(link)
                reason = why_not(link, site) or (None if robots.can_fetch(USER_AGENT, link)
                                                 else "robots.txt asks robots not to read it")
                if reason:
                    result.skipped.append((link, reason))
                    continue
                score = usefulness(link, a.get_text(" ", strip=True))
                if depth == 1:  # articles behind a news, blog or careers page: useful, but after the main pages
                    score = min(score, 3)
                queue.append((score, depth + 1, link))
    finally:
        if own_client:
            client.close()
    if not result.pages:
        raise CrawlError(f"Couldn't read {start}. Check the address, or try the company's main page.")
    return result


# ----------------------------------------------------------------------------- the sample websites, served locally
def serve_samples(folder=None):
    """Serve each fictional company website on its own local port (each needs its own robots.txt). {slug: url}"""
    folder = Path(folder or Path(__file__).with_name("samples") / "sites")

    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    urls = {}
    for site in sorted(p for p in folder.iterdir() if p.is_dir()):
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(site)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        urls[site.name] = f"http://127.0.0.1:{server.server_address[1]}/"
    return urls
