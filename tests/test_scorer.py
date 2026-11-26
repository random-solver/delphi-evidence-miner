"""Tests for corroboration, contradiction, and information-gain scoring."""

from delphi_miner.decomposer import decompose_market
from delphi_miner.ledger import compute_sha256
from delphi_miner.models import MarketOdds, MarketSpec, SourceDocument, Stance
from delphi_miner.scorer import aggregate_corroboration, score_document


def _make_doc(url: str, domain: str, title: str, text: str, pub: str = "2026-11-26T10:00:00Z") -> SourceDocument:
    return SourceDocument(
        url=url,
        normalized_url=url,
        domain=domain,
        source_type="web_html",
        title=title,
        published_at=pub,
        fetched_at="2026-11-26T12:00:00Z",
        http_status=200,
        content_sha256=compute_sha256(text),
        raw_byte_len=len(text),
        text_content=text,
    )


def test_corroboration_increases_implied_probability_when_sources_agree() -> None:
    market = MarketSpec(
        address="0x1234",
        question="Will Destatis's preliminary German CPI annual rate for Nov 2026 be 2.8% or higher?",
        odds=MarketOdds(yes=0.50, no=0.50),
        evidence_cutoff_at="2026-11-29T08:00:00.000Z",
        resolution_criteria="Published strictly after 2026-11-24T09:00:00Z and at or before 2026-11-30T19:00:00Z.",
        data_sources=["https://www.destatis.de/EN/Themes/Economy/Prices/Consumer-Price-Index/_node.html"],
    )
    decomp = decompose_market(market)

    doc1 = _make_doc(
        "https://www.destatis.de/EN/Press/2026-nov",
        "destatis.de",
        "Destatis Preliminary German CPI Nov 2026",
        "Destatis preliminary German CPI annual rate rose and reached 3.0% in November 2026, higher than expected.",
    )
    doc2 = _make_doc(
        "https://www.reuters.com/markets/german-cpi-nov-2026",
        "reuters.com",
        "German inflation rises to 3.0%",
        "Official Destatis data confirmed German CPI inflation rose to 3.0% in Nov 2026.",
    )

    sig1 = score_document(doc1, decomp)
    sig2 = score_document(doc2, decomp)
    assert sig1.stance == Stance.SUPPORT_YES
    assert sig2.stance == Stance.SUPPORT_YES
    assert sig1.authority_weight == 1.0

    report = aggregate_corroboration(market, [sig1, sig2])
    assert report.recommended_stance == Stance.SUPPORT_YES
    assert report.contradiction_index == 0.0
    assert report.corroboration_score > 0.80
    assert report.implied_yes_probability > 0.65
    assert report.passes_submission_threshold is True


def test_contradiction_index_flags_conflicting_sources_and_blocks_submission() -> None:
    market = MarketSpec(
        address="0x5678",
        question="Will Swiss voters approve the War Materiel Act amendment in Nov 2026?",
        odds=MarketOdds(yes=0.52, no=0.48),
        evidence_cutoff_at="2026-11-28T06:00:00.000Z",
        data_sources=["https://www.admin.ch/votes"],
    )
    decomp = decompose_market(market)

    yes_doc = _make_doc(
        "https://www.srf.ch/news/poll-yes",
        "srf.ch",
        "Poll shows Swiss War Materiel Act approved",
        "Swiss voters support the War Materiel Act amendment; latest survey shows majority approved.",
    )
    no_doc = _make_doc(
        "https://www.swissinfo.ch/eng/poll-no",
        "swissinfo.ch",
        "Survey shows War Materiel Act rejected",
        "Latest Swiss voter poll indicates the War Materiel Act amendment will be rejected and defeated.",
    )

    sig_yes = score_document(yes_doc, decomp)
    sig_no = score_document(no_doc, decomp)
    assert sig_yes.stance == Stance.SUPPORT_YES
    assert sig_no.stance == Stance.SUPPORT_NO

    report = aggregate_corroboration(market, [sig_yes, sig_no])
    assert report.contradiction_index > 0.85
    assert report.passes_submission_threshold is False


def test_same_domain_spam_does_not_inflate_diversity() -> None:
    market = MarketSpec(
        address="0x9999",
        question="Will Vox win >=60 of 350 Congress seats in Spain's Nov 29, 2026 general election?",
        odds=MarketOdds(yes=0.50, no=0.50),
        data_sources=["https://infoelectoral.interior.gob.es/en/inicio"],
    )
    decomp = decompose_market(market)

    docs = [
        _make_doc(
            f"https://randomblog.example/post-{i}",
            "randomblog.example",
            f"Spain Vox poll {i}",
            "Vox Congress seats poll shows 65 seats won in Spain election.",
        )
        for i in range(5)
    ]
    sigs = [score_document(d, decomp) for d in docs]
    report = aggregate_corroboration(market, sigs)
    assert report.distinct_domains_count == 1
    # Diversity factor caps single-domain corroboration at (1 + 0.5)/3 = 0.50
    assert report.corroboration_score <= 0.50


def test_irrelevant_integer_does_not_trigger_percent_threshold() -> None:
    market = MarketSpec(
        address="0xjapan",
        question="Will Japan's ESRI second preliminary Jul-Sep 2026 real GDP show q/q annualized growth >= 0.0%?",
        odds=MarketOdds(yes=0.49, no=0.51),
        data_sources=["https://www.esri.cao.go.jp/en/news/index.html"],
    )
    decomp = decompose_market(market)
    offtopic = _make_doc(
        "https://arxiv.org/abs/2611.99999",
        "arxiv.org",
        "Historical Wood Study in Japan",
        "We examined 49 wood specimens in Japan under higher illumination.",
    )
    sig = score_document(offtopic, decomp)
    assert sig.stance == Stance.NEUTRAL_CONTEXT
    assert sig.extracted_numbers == []
