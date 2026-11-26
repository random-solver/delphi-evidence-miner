"""Tests for public source fetchers (RSS, Atom, HTML, PDF, Wikipedia, arXiv, GitHub, SEC)."""

import json
import httpx
import pymupdf

from delphi_miner.fetchers import (
    PublicSourceFetcher,
    extract_pdf_text,
    normalize_timestamp,
    parse_feed_xml,
)


def test_normalize_timestamp_rfc2822_and_iso() -> None:
    assert normalize_timestamp("Thu, 26 Nov 2026 12:30:00 GMT") == "2026-11-26T12:30:00Z"
    assert normalize_timestamp("2026-11-26T14:00:00+02:00") == "2026-11-26T12:00:00Z"
    assert normalize_timestamp("not-a-date") is None


def test_parse_rss_and_atom_feeds() -> None:
    rss_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0">
      <channel>
        <title>Test Feed</title>
        <item>
          <title>German CPI rises to 2.9% in November</title>
          <link>https://www.destatis.de/EN/Press/cpi-nov-2026</link>
          <pubDate>Thu, 26 Nov 2026 10:00:00 GMT</pubDate>
          <description>&lt;p&gt;Preliminary annual inflation rate 2.9%.&lt;/p&gt;</description>
        </item>
      </channel>
    </rss>"""
    rss_docs = parse_feed_xml(rss_xml, "https://www.destatis.de/rss.xml")
    assert len(rss_docs) == 1
    assert rss_docs[0].source_type == "rss"
    assert rss_docs[0].published_at == "2026-11-26T10:00:00Z"
    assert "2.9%" in rss_docs[0].text_content

    atom_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <title>New Evidence Mining Paper</title>
        <link rel="alternate" href="https://arxiv.org/abs/2611.00001"/>
        <published>2026-11-25T18:00:00Z</published>
        <summary>Forecasting with verifiable evidence.</summary>
      </entry>
    </feed>"""
    atom_docs = parse_feed_xml(atom_xml, "https://export.arxiv.org/api/query")
    assert len(atom_docs) == 1
    assert atom_docs[0].source_type == "atom"
    assert atom_docs[0].domain == "arxiv.org"
    assert atom_docs[0].published_at == "2026-11-25T18:00:00Z"


def test_extract_pdf_text_respects_page_cap() -> None:
    doc = pymupdf.open()
    for i in range(5):
        page = doc.new_page()
        page.insert_text((72, 72), f"Page {i + 1} official statistical figure 3.1%")
    pdf_bytes = doc.tobytes()
    doc.close()

    extracted_all = extract_pdf_text(pdf_bytes, max_pages=20)
    assert "Page 1" in extracted_all and "Page 5" in extracted_all

    extracted_cap = extract_pdf_text(pdf_bytes, max_pages=2)
    assert "Page 1" in extracted_cap and "Page 2" in extracted_cap
    assert "Page 3" not in extracted_cap


def test_public_source_fetcher_with_mock_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "example.org/report.html" in url:
            html = """
            <html>
              <head>
                <title>Official Turnout Bulletin</title>
                <meta property="article:published_time" content="2026-11-26T09:00:00Z"/>
                <link rel="alternate" type="application/rss+xml" href="/feed.xml"/>
              </head>
              <body>
                <script>var secret = "ignore";</script>
                <p>Preliminary turnout reached 52.4% across cantons.</p>
              </body>
            </html>
            """
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})
        if "en.wikipedia.org/w/api.php" in url:
            return httpx.Response(
                200,
                json={"query": {"search": [{"title": "Swiss Federal Chancellery", "snippet": "Federal body", "pageid": 42}]}},
            )
        if "en.wikipedia.org/api/rest_v1/page/summary" in url:
            return httpx.Response(
                200,
                json={"extract": "The Swiss Federal Chancellery publishes official vote turnout.", "timestamp": "2026-11-20T00:00:00Z"},
            )
        if "api.github.com/search/repositories" in url:
            return httpx.Response(
                200,
                json={"items": [{"full_name": "org/repo", "html_url": "https://github.com/org/repo", "description": "Test repo", "stargazers_count": 10, "updated_at": "2026-11-26T01:00:00Z"}]},
            )
        if "efts.sec.gov" in url:
            return httpx.Response(
                200,
                json={"hits": {"hits": [{"_source": {"ciks": ["0000320193"], "adsh": "0000320193-26-000010", "display_names": ["Apple Inc."], "form": "8-K", "file_date": "2026-11-25"}}]}},
            )
        return httpx.Response(404, text="not found")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with PublicSourceFetcher(client=client) as fetcher:
        docs, feeds = fetcher.fetch_url("https://example.org/report.html")
        assert len(docs) == 1
        assert docs[0].title == "Official Turnout Bulletin"
        assert docs[0].published_at == "2026-11-26T09:00:00Z"
        assert "ignore" not in docs[0].text_content
        assert "52.4%" in docs[0].text_content
        assert feeds == ["https://example.org/feed.xml"]

        wiki_docs = fetcher.fetch_wikipedia("Swiss Federal Chancellery")
        assert len(wiki_docs) == 1 and wiki_docs[0].source_type == "wikipedia"

        gh_docs = fetcher.fetch_github("arc-agi-3")
        assert len(gh_docs) == 1 and gh_docs[0].source_type == "github"

        sec_docs = fetcher.fetch_sec_edgar("Apple")
        assert len(sec_docs) == 1 and sec_docs[0].source_type == "sec_edgar"
        assert "320193" in sec_docs[0].url
