# Delphi Evidence Miner

Autonomous, verifiable evidence-mining agent for [Delphi Agent Arena](https://competition.delphi.fyi/participate) forecasting markets (`https://delphi.fyi/v1`).

Designed for the **Delphi Evidence Mining Competition** (`delphi-agent-competition2`): decomposes live prediction markets into verifiable sub-claims, fetches primary public records and domain feeds, computes multi-source corroboration/contradiction scores and Bayesian log-odds shifts, records SHA-256 citation provenance in SQLite, and formats Delphi-compliant multipart/form-data submissions.

---

## Architecture

```text
  competition.delphi.fyi/markets.json
                 │
                 ▼
       ┌───────────────────┐
       │ Market Ranker     │  Filters open markets before evidenceCutoffAt;
       │ (pipeline.py)     │  ranks by uncertainty (peak at 50%) & USDC pot
       └─────────┬─────────┘
                 ▼
       ┌───────────────────┐
       │ Claim Decomposer  │  Extracts entities, numeric thresholds (>=, <=, <),
       │ (decomposer.py)   │  resolution windows, target domains, & SubClaims
       └─────────┬─────────┘
                 ▼
       ┌───────────────────┐
       │ Public Fetchers   │  Official dataSources + RSS/Atom autodiscovery,
       │ (fetchers.py)     │  PDF (PyMuPDF, 20-page cap), Wikipedia, arXiv,
       └─────────┬─────────┘  GitHub REST, & SEC EDGAR EFTS
                 ▼
       ┌───────────────────┐
       │ Scorer & Aggreg.  │  Domain-deduplicated corroboration, contradiction
       │ (scorer.py)       │  index, authority/recency weighting, log-odds shift
       └─────────┬─────────┘
                 ▼
       ┌───────────────────┐
       │ Provenance Ledger │  SHA-256 content hashes + deterministic record
       │ (ledger.py)       │  hashes + submission fingerprint deduplication
       └─────────┬─────────┘
                 ▼
       ┌───────────────────┐
       │ Dossier & Client  │  JSON/Markdown dossiers (dossier.py) +
       │ (delphi_client.py)│  multipart POST /v1/submissions (<=3 items, <=8000 chars)
       └───────────────────┘
```

### Core Modules (`src/delphi_miner/`)

| Module | Responsibility |
|---|---|
| `models.py` | Pydantic v2 schemas (`MarketSpec`, `ClaimDecomposition`, `SourceDocument`, `EvidenceSignal`, `CorroborationReport`, `ProvenanceRecord`, `EvidenceDossier`). |
| `decomposer.py` | Parses numeric thresholds (`>=`, `<=`, `%`, `seats`), settlement time windows, entities, and structured sub-claims (`C1`–`C4`). |
| `fetchers.py` | Unified `httpx` fetcher for `markets.json`, HTML, JSON, RSS 2.0 / Atom 1.0 feeds, PDFs (`pymupdf`, capped at Delphi's 20-page limit), Wikipedia, arXiv, GitHub, and SEC EDGAR. |
| `scorer.py` | Evaluates relevance, source authority, recency relative to `evidenceCutoffAt`, threshold/lexical stance, domain-deduplicated corroboration, contradiction index, and damped Bayesian probability shift. |
| `ledger.py` | Local SQLite provenance store (`fetched_sources`, `evidence_claims`, `submissions`) with tamper-evident SHA-256 `record_hash` verification and duplicate-note guarding. |
| `dossier.py` | Builds Delphi-valid submission notes (`<= 8000` chars, `<= 3` total items across note + links + files) and writes JSON/Markdown audit dossiers. |
| `delphi_client.py` | Delphi Evidence API v1 client (`https://delphi.fyi/v1`) supporting `/limits`, `/terms`, `/terms/acceptance`, `/markets/{address}/entry-state`, `/submissions` (`multipart/form-data`), `/submissions/{id}`, and `/submissions/{id}/retry`. |
| `cli.py` | Typer CLI (`markets`, `mine`, `verify-ledger`, `status`). |

---

## Delphi Evidence API & Zero-Capital Flow

This project uses the **zero-capital, API-key-only** Delphi Evidence API flow documented in `https://competition.delphi.fyi/skill.md` and `https://docs.delphi.fyi/tools/sdk/evidence`:

1. **Account & Embedded Wallet**: Sign in at [delphi.fyi](https://delphi.fyi) (e.g., via Google/Email) to create your Delphi account and gas-sponsored embedded wallet, and register your Delphi wallet address on DoraHacks (`delphi-agent-competition2`).
2. **Mainnet API Key**: Generate a Mainnet API key at [api-access.delphi.fyi](https://api-access.delphi.fyi) signed in with the same account. Submitting evidence requires **only** this API key — no private key, ETH, USDC, or trading position is needed.
3. **Environment Variable**:
   ```bash
   export DELPHI_API_ACCESS_KEY="<your-mainnet-delphi-api-key>"
   ```
   *(Never commit API keys or local `*.db` ledger files.)*
4. **Terms Acceptance (One-Time per Version)**:
   - Check status & terms: `delphi-miner status` (calls `GET /v1/limits` and `GET /v1/terms`).
   - If `acceptedAt` is `null`, review the terms URL from `/v1/terms` and accept once via `DelphiEvidenceClient.accept_terms(version)` (`POST /v1/terms/acceptance`).
5. **Submission Constraints Enforced Client-Side**:
   - At most **3 total items** (`1` if `note` is present + `len(links)` + `len(files)` `<= 3`).
   - `note` capped at **8,000 characters**.
   - Attached `files` capped at **10 MB** total and **20 pages** per PDF during local extraction.
   - Automatic `Idempotency-Key` header and agent identity headers (`X-Agent-Id`, `X-Agent-Harness`, `X-Agent-Model`, `User-Agent`).

---

## Installation & CLI Usage

Requires Python `>= 3.11`.

```bash
pip install -e ".[dev]"
```

### Commands

```bash
# 1. Check API key, rate limits, and terms status (offline-safe if key is unset)
delphi-miner status

# 2. Fetch and rank live open markets from competition.delphi.fyi/markets.json
delphi-miner markets --limit 10 --save-json outputs/markets.json

# 3. Mine top markets in dry-run mode (generates JSON/Markdown dossiers + SQLite provenance)
delphi-miner mine --limit 3 --output-dir outputs --ledger delphi_ledger.db

# 4. Mine a specific market with extra primary sources and submit live (requires DELPHI_API_ACCESS_KEY)
delphi-miner mine --market 0x... --source https://example.org/release --submit

# 5. Verify SHA-256 provenance integrity of the local SQLite ledger
delphi-miner verify-ledger --ledger delphi_ledger.db
```

Sample generated dossiers and a live `markets.json` snapshot are included under `examples/sample_outputs/`.

---

## Testing

Run the full test suite (all HTTP calls in tests use `httpx.MockTransport` and temporary `tmp_path` SQLite ledgers — no network access or API keys required):

```bash
python -m pytest -p no:cacheprovider -v
```

---

## License

[MIT](LICENSE)
