"""Live public source fetchers (Delphi markets, RSS/Atom, SEC EDGAR, arXiv, GitHub, Wikipedia, HTML/PDF/JSON)."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote_plus, urljoin, urlparse, urlunparse

import pymupdf
import httpx

from delphi_miner.ledger import compute_sha256
from delphi_miner.models import MarketSpec, SourceDocument, utc_now_iso

DEFAULT_USER_AGENT = "DelphiEvidenceMiner/0.1.0 (+https://github.com/random-solver/delphi-evidence-miner)"
SEC_USER_AGENT = "DelphiEvidenceMiner/0.1.0 contact@random-solver.example"
DELPHI_MARKETS_URL = "https://competition.delphi.fyi/markets.json"


def normalize_url(url: str) -> str:
    """Normalize URL by stripping fragments and trailing whitespace."""
    parsed = urlparse(url.strip())
    clean_path = parsed.path or "/"
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), clean_path, parsed.params, parsed.query, ""))


def extract_domain(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def normalize_timestamp(raw: str | None) -> str | None:
    """Parse RFC-2822 or ISO-8601 date strings into UTC ISO-8601 Z format."""
    if not raw or not raw.strip():
        return None
    s = raw.strip()
    try:
        if s.endswith("Z"):
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except (TypeError, ValueError):
        return None


class _HTMLTextExtractor(HTMLParser):
    """Lightweight HTML parser extracting clean visible text, metadata, and feed links."""

    _SKIP_TAGS = {"script", "style", "noscript", "svg", "template"}

    def __init__(self, base_url: str = "") -> None:
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self._skip_depth = 0
        self._in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.published_at: str | None = None
        self.feed_urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if t in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if t == "title" and self._skip_depth == 0:
            self._in_title = True
        elif t == "meta":
            prop = (attr_map.get("property") or attr_map.get("name") or "").lower()
            if prop in {
                "article:published_time",
                "og:updated_time",
                "publish_date",
                "pubdate",
                "date",
                "dc.date",
                "dc.date.issued",
            } and not self.published_at:
                self.published_at = normalize_timestamp(attr_map.get("content"))
        elif t == "time" and not self.published_at:
            self.published_at = normalize_timestamp(attr_map.get("datetime"))
        elif t == "link":
            rel = attr_map.get("rel", "").lower()
            ltype = attr_map.get("type", "").lower()
            href = attr_map.get("href", "").strip()
            if href and "alternate" in rel and ("rss" in ltype or "atom" in ltype):
                self.feed_urls.append(urljoin(self.base_url, href))

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif t == "title":
            self._in_title = False
        elif t in {"p", "div", "li", "h1", "h2", "h3", "h4", "tr", "br"} and self._skip_depth == 0:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self.title_parts.append(data)
        self.text_parts.append(data)

    def get_title(self) -> str:
        return re.sub(r"\s+", " ", "".join(self.title_parts)).strip()

    def get_text(self) -> str:
        raw = "".join(self.text_parts)
        lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in raw.splitlines()]
        return "\n".join(ln for ln in lines if ln)


def _strip_html(raw: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(unescape(raw))
    parser.close()
    return parser.get_text()


def parse_feed_xml(
    xml_bytes: bytes,
    feed_url: str,
    http_status: int = 200,
    fetched_at: str | None = None,
    max_items: int = 10,
) -> list[SourceDocument]:
    """Parse RSS 2.0 or Atom 1.0 XML bytes into SourceDocument entries."""
    now = fetched_at or utc_now_iso()
    root = ET.fromstring(xml_bytes)
    tag_low = root.tag.lower()
    docs: list[SourceDocument] = []

    def _local(el: ET.Element) -> str:
        return el.tag.split("}", 1)[-1].lower()

    if tag_low.endswith("feed"):
        # Atom 1.0
        for entry in [el for el in root if _local(el) == "entry"][:max_items]:
            title = ""
            link = feed_url
            pub = None
            body_parts: list[str] = []
            for child in entry:
                name = _local(child)
                txt = (child.text or "").strip()
                if name == "title":
                    title = re.sub(r"\s+", " ", txt)
                elif name == "link":
                    rel = child.attrib.get("rel", "alternate")
                    href = child.attrib.get("href", "").strip()
                    if href and (rel == "alternate" or link == feed_url):
                        link = urljoin(feed_url, href)
                elif name in {"published", "updated"} and not pub:
                    pub = normalize_timestamp(txt)
                elif name in {"summary", "content"} and txt:
                    body_parts.append(_strip_html(txt))
            full_text = f"{title}\n" + "\n".join(body_parts)
            raw_b = full_text.encode("utf-8")
            docs.append(
                SourceDocument(
                    url=link,
                    normalized_url=normalize_url(link),
                    domain=extract_domain(link),
                    source_type="atom",
                    title=title,
                    published_at=pub,
                    fetched_at=now,
                    http_status=http_status,
                    content_sha256=compute_sha256(raw_b),
                    raw_byte_len=len(raw_b),
                    text_content=full_text.strip(),
                    metadata={"feed_url": feed_url},
                )
            )
    else:
        # RSS 2.0 / RDF
        channel = None
        for child in root:
            if _local(child) == "channel":
                channel = child
                break
        container = channel if channel is not None else root
        items = [el for el in container if _local(el) == "item"][:max_items]
        for item in items:
            title = ""
            link = feed_url
            pub = None
            body_parts = []
            for child in item:
                name = _local(child)
                txt = (child.text or "").strip()
                if name == "title":
                    title = re.sub(r"\s+", " ", txt)
                elif name == "link" and txt:
                    link = urljoin(feed_url, txt)
                elif name in {"pubdate", "date", "published"} and not pub:
                    pub = normalize_timestamp(txt)
                elif name in {"description", "encoded"} and txt:
                    body_parts.append(_strip_html(txt))
            full_text = f"{title}\n" + "\n".join(body_parts)
            raw_b = full_text.encode("utf-8")
            docs.append(
                SourceDocument(
                    url=link,
                    normalized_url=normalize_url(link),
                    domain=extract_domain(link),
                    source_type="rss",
                    title=title,
                    published_at=pub,
                    fetched_at=now,
                    http_status=http_status,
                    content_sha256=compute_sha256(raw_b),
                    raw_byte_len=len(raw_b),
                    text_content=full_text.strip(),
                    metadata={"feed_url": feed_url},
                )
            )
    return docs


def extract_pdf_text(pdf_bytes: bytes, max_pages: int = 20) -> str:
    """Extract text from PDF bytes using PyMuPDF, capped at max_pages (Delphi limit: 20)."""
    parts: list[str] = []
    with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
        for idx, page in enumerate(doc):
            if idx >= max_pages:
                break
            txt = page.get_text("text").strip()
            if txt:
                parts.append(txt)
    return "\n\n".join(parts)


class PublicSourceFetcher:
    """Unified resilient fetcher for Delphi markets and live public evidence sources."""

    def __init__(self, timeout_seconds: float = 12.0, client: httpx.Client | None = None) -> None:
        self.timeout_seconds = timeout_seconds
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> PublicSourceFetcher:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def fetch_delphi_markets(self, url: str = DELPHI_MARKETS_URL) -> list[MarketSpec]:
        """Fetch live Delphi competition markets from competition.delphi.fyi/markets.json."""
        resp = self.client.get(url)
        resp.raise_for_status()
        payload = resp.json()
        raw_list = payload.get("markets", payload) if isinstance(payload, dict) else payload
        return [MarketSpec.from_delphi_json(item) for item in raw_list]

    def fetch_url(self, url: str, max_chars: int = 12000) -> tuple[list[SourceDocument], list[str]]:
        """
        Fetch any public HTTP(S) URL (HTML, JSON, RSS/Atom, or PDF).
        Returns (documents, discovered_feed_urls).
        """
        fetched_at = utc_now_iso()
        resp = self.client.get(url)
        raw_bytes = resp.content
        ctype = (resp.headers.get("content-type") or "").lower()
        final_url = str(resp.url)
        sha = compute_sha256(raw_bytes)
        discovered_feeds: list[str] = []

        if resp.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {resp.status_code} fetching {url}",
                request=resp.request,
                response=resp,
            )

        # RSS / Atom XML detection
        stripped_head = raw_bytes[:300].lstrip().lower()
        if (
            "xml" in ctype
            or "rss" in ctype
            or "atom" in ctype
            or stripped_head.startswith((b"<?xml", b"<rss", b"<feed"))
        ):
            try:
                docs = parse_feed_xml(raw_bytes, final_url, http_status=resp.status_code, fetched_at=fetched_at)
                if docs:
                    return docs, []
            except ET.ParseError:
                pass

        # PDF detection
        if "application/pdf" in ctype or final_url.lower().endswith(".pdf") or raw_bytes[:5] == b"%PDF-":
            text = extract_pdf_text(raw_bytes)[:max_chars]
            doc = SourceDocument(
                url=final_url,
                normalized_url=normalize_url(final_url),
                domain=extract_domain(final_url),
                source_type="pdf",
                title=final_url.rsplit("/", 1)[-1] or "PDF Document",
                published_at=None,
                fetched_at=fetched_at,
                http_status=resp.status_code,
                content_sha256=sha,
                raw_byte_len=len(raw_bytes),
                text_content=text,
            )
            return [doc], []

        # JSON detection
        if "application/json" in ctype or final_url.lower().endswith(".json"):
            try:
                parsed = json.loads(raw_bytes.decode("utf-8", errors="replace"))
                pretty = json.dumps(parsed, indent=2, ensure_ascii=False)[:max_chars]
                doc = SourceDocument(
                    url=final_url,
                    normalized_url=normalize_url(final_url),
                    domain=extract_domain(final_url),
                    source_type="web_json",
                    title=f"JSON API: {final_url}",
                    published_at=None,
                    fetched_at=fetched_at,
                    http_status=resp.status_code,
                    content_sha256=sha,
                    raw_byte_len=len(raw_bytes),
                    text_content=pretty,
                )
                return [doc], []
            except json.JSONDecodeError:
                pass

        # HTML / plain text fallback
        html_str = raw_bytes.decode("utf-8", errors="replace")
        parser = _HTMLTextExtractor(base_url=final_url)
        parser.feed(html_str)
        parser.close()
        discovered_feeds = parser.feed_urls
        text = parser.get_text()[:max_chars]
        title = parser.get_title() or extract_domain(final_url)

        doc = SourceDocument(
            url=final_url,
            normalized_url=normalize_url(final_url),
            domain=extract_domain(final_url),
            source_type="web_html",
            title=title,
            published_at=parser.published_at,
            fetched_at=fetched_at,
            http_status=resp.status_code,
            content_sha256=sha,
            raw_byte_len=len(raw_bytes),
            text_content=text,
            metadata={"discovered_feeds": discovered_feeds},
        )
        return [doc], discovered_feeds

    def fetch_wikipedia(self, query: str, max_results: int = 3) -> list[SourceDocument]:
        """Search Wikipedia via MediaWiki API and retrieve summaries."""
        if not query.strip():
            return []
        url = (
            "https://en.wikipedia.org/w/api.php"
            f"?action=query&list=search&srsearch={quote_plus(query)}&format=json&srlimit={max_results}"
        )
        fetched_at = utc_now_iso()
        resp = self.client.get(url)
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("query", {}).get("search", [])
        docs: list[SourceDocument] = []
        for hit in hits[:max_results]:
            title = hit.get("title", "")
            if not title:
                continue
            page_url = f"https://en.wikipedia.org/wiki/{quote_plus(title.replace(' ', '_'))}"
            snippet = _strip_html(hit.get("snippet", ""))
            summary_text = snippet
            pub = normalize_timestamp(hit.get("timestamp"))
            # Try REST summary for richer excerpt
            try:
                sresp = self.client.get(
                    f"https://en.wikipedia.org/api/rest_v1/page/summary/{quote_plus(title.replace(' ', '_'))}"
                )
                if sresp.status_code == 200:
                    sdata = sresp.json()
                    extract = sdata.get("extract", "")
                    if extract:
                        summary_text = extract
                    pub = normalize_timestamp(sdata.get("timestamp")) or pub
            except httpx.HTTPError:
                pass

            body = f"{title}\n{summary_text}".strip()
            raw_b = body.encode("utf-8")
            docs.append(
                SourceDocument(
                    url=page_url,
                    normalized_url=normalize_url(page_url),
                    domain="en.wikipedia.org",
                    source_type="wikipedia",
                    title=title,
                    published_at=pub,
                    fetched_at=fetched_at,
                    http_status=resp.status_code,
                    content_sha256=compute_sha256(raw_b),
                    raw_byte_len=len(raw_b),
                    text_content=body,
                    metadata={"query": query, "pageid": hit.get("pageid")},
                )
            )
        return docs

    def fetch_arxiv(self, query: str, max_results: int = 3) -> list[SourceDocument]:
        """Query the arXiv Atom API for relevant preprints."""
        if not query.strip():
            return []
        url = (
            "https://export.arxiv.org/api/query"
            f"?search_query=all:{quote_plus(query)}&start=0&max_results={max_results}&sortBy=submittedDate&sortOrder=descending"
        )
        fetched_at = utc_now_iso()
        resp = self.client.get(url)
        resp.raise_for_status()
        docs = parse_feed_xml(resp.content, url, http_status=resp.status_code, fetched_at=fetched_at, max_items=max_results)
        for d in docs:
            d.source_type = "arxiv"
        return docs

    def fetch_github(self, query: str, max_results: int = 3) -> list[SourceDocument]:
        """Search public GitHub repositories via REST API."""
        if not query.strip():
            return []
        url = f"https://api.github.com/search/repositories?q={quote_plus(query)}&sort=updated&per_page={max_results}"
        fetched_at = utc_now_iso()
        resp = self.client.get(url, headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        items = resp.json().get("items", [])
        docs: list[SourceDocument] = []
        for repo in items[:max_results]:
            html_url = repo.get("html_url", "")
            full_name = repo.get("full_name", "")
            desc = repo.get("description") or ""
            updated_at = normalize_timestamp(repo.get("updated_at") or repo.get("pushed_at"))
            body = (
                f"GitHub Repo: {full_name}\n"
                f"Stars: {repo.get('stargazers_count', 0)} | Updated: {updated_at or 'unknown'}\n"
                f"Description: {desc}"
            )
            raw_b = body.encode("utf-8")
            docs.append(
                SourceDocument(
                    url=html_url,
                    normalized_url=normalize_url(html_url),
                    domain="github.com",
                    source_type="github",
                    title=full_name,
                    published_at=updated_at,
                    fetched_at=fetched_at,
                    http_status=resp.status_code,
                    content_sha256=compute_sha256(raw_b),
                    raw_byte_len=len(raw_b),
                    text_content=body,
                    metadata={"stars": repo.get("stargazers_count", 0)},
                )
            )
        return docs

    def fetch_sec_edgar(self, query: str, max_results: int = 3) -> list[SourceDocument]:
        """Search SEC EDGAR full-text index (EFTS) with compliant SEC User-Agent."""
        if not query.strip():
            return []
        url = f"https://efts.sec.gov/LATEST/search-index?q={quote_plus(query)}"
        fetched_at = utc_now_iso()
        resp = self.client.get(
            url,
            headers={"User-Agent": SEC_USER_AGENT, "Accept": "application/json"},
        )
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])
        docs: list[SourceDocument] = []
        for hit in hits[:max_results]:
            src = hit.get("_source", {})
            ciks = src.get("ciks") or []
            adsh = str(src.get("adsh", "")).replace("-", "")
            cik = str(ciks[0]).lstrip("0") if ciks else ""
            doc_url = (
                f"https://www.sec.gov/Archives/edgar/data/{cik}/{adsh}/{src.get('adsh', '')}-index.htm"
                if cik and adsh
                else "https://www.sec.gov/edgar/searchedgar/companysearch"
            )
            display = ", ".join(src.get("display_names") or []) or "SEC Filing"
            form = src.get("form", "")
            file_date = normalize_timestamp(src.get("file_date"))
            body = f"SEC Form {form} | Entity: {display} | Filed: {src.get('file_date', '')} | ADSh: {src.get('adsh', '')}"
            raw_b = body.encode("utf-8")
            docs.append(
                SourceDocument(
                    url=doc_url,
                    normalized_url=normalize_url(doc_url),
                    domain="sec.gov",
                    source_type="sec_edgar",
                    title=f"{form}: {display}",
                    published_at=file_date,
                    fetched_at=fetched_at,
                    http_status=resp.status_code,
                    content_sha256=compute_sha256(raw_b),
                    raw_byte_len=len(raw_b),
                    text_content=body,
                    metadata={"form": form, "file_date": src.get("file_date")},
                )
            )
        return docs
