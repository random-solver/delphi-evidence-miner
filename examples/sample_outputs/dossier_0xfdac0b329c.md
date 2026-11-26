# Evidence Dossier — Will Thomas Aeschi (SVP) be elected to the Swiss Federal Council by the Federal Assembly on Dec 9, 2026, succeeding Guy Parmelin, per parlament.ch?

- **Market Address:** `0xfdac0b329c1eeccb338d7acc0ec4b537dcfc58c2`
- **Delphi URL:** https://delphi.fyi/market/a47fa3b7-d426-49c8-af9a-2a1bd70a5bf1
- **Category:** Votes and government (`votes_government`)
- **Current Odds:** YES `51.50%` / NO `48.50%`
- **Reward Pots:** Judgment `$20.00` + Settlement `$30.00` USDC
- **Evidence Cutoff:** `2026-12-08T05:00:00.000Z` | **Closes:** `2026-12-09T05:00:00.000Z`
- **Generated At:** `2026-11-26T14:09:44Z`

## 1. Claim Decomposition
- **Entities:** Thomas Aeschi, SVP, Swiss Council, Assembly, Guy Parmelin
- **Thresholds:** None
- **Resolution Window:** `2026-11-24T09:57:51.224Z` .. `2026-12-09T13:00:00.000Z`
- **Target Official Domains:** parlament.ch, bk.admin.ch, reuters.com

### Sub-Claims
- **`C1-primary-outcome`** (primary_outcome): Primary outcome verification: determine whether evidence indicates YES or NO for 'Will Thomas Aeschi (SVP) be elected to the Swiss Federal Council by the Federal Assembly on Dec 9, 2026, succeeding Guy Parmelin, per parlament.ch?' (thresholds: binary outcome condition).
- **`C2-official-source`** (official_source_check): Designated resolution source check across ['parlament.ch', 'bk.admin.ch', 'reuters.com'] for official notices/releases inside window [2026-11-24T09:57:51.224Z .. 2026-12-09T13:00:00.000Z].
- **`C3-leading-indicator`** (leading_indicator): Leading indicator / pre-release check: polls, regional/preliminary prints, ballot/tracker status, standings, or technical commits tied to Thomas Aeschi, SVP, Swiss Council, Assembly.
- **`C4-criteria-edge-guard`** (edge_guard): Resolution criteria edge-case guard: verify exact metric definition (preliminary vs final, valid+blank ballots, single vs double majority, UTC cutoff).

## 2. Multi-Source Corroboration & Contradiction Analysis
- **Recommended Stance:** `NEUTRAL_CONTEXT`
- **Prior YES Odds:** `51.50%` -> **Implied YES Probability:** `51.50%` (**Delta:** `0.00%`)
- **Corroboration Score:** `0.0000` | **Contradiction Index:** `0.0000`
- **Domain Weights:** YES=`0.0000` | NO=`0.0000` | NEUTRAL=`0.4140` across `2` distinct domain(s)
- **Passes Submission Gate:** `False`
- **Summary:** Fetched 4 signal(s) across 2 domain(s); no decisive directional shift over market baseline yet.

## 3. Scored Evidence Signals & Citation Provenance
### Signal 1: 2026 Swiss Federal Council election
- **Claim:** `C3-leading-indicator` | **Stance:** `NEUTRAL_CONTEXT` | **Confidence:** `0.2700`
- **Source:** https://en.wikipedia.org/wiki/2026_Swiss_Federal_Council_election (`wikipedia`, domain `en.wikipedia.org`)
- **Published:** `2026-11-25T19:42:38Z` | **Fetched:** `2026-11-26T14:09:41Z`
- **Content SHA-256:** `5a0a3ff9abdc868919a67ca2589f977f7438de82135ac2321af9455eb59538a9`
- **Metrics:** relevance=`0.900`, authority=`0.750`, recency=`1.000`
- **Rationale:** Informational context without decisive directional threshold or polarity signal.
- **Excerpt:** > The announcement of President Guy Parmelin's resignation from the Federal Council on 2 October, effective on 31 December, triggered a by-election that will take place on 9 December 2026.

### Signal 2: Thomas Aeschi
- **Claim:** `C3-leading-indicator` | **Stance:** `NEUTRAL_CONTEXT` | **Confidence:** `0.1800`
- **Source:** https://en.wikipedia.org/wiki/Thomas_Aeschi (`wikipedia`, domain `en.wikipedia.org`)
- **Published:** `2026-11-22T21:20:12Z` | **Fetched:** `2026-11-26T14:09:41Z`
- **Content SHA-256:** `990059914023724090a7c3ce351690648652ca697563364defd391dbb7a82b09`
- **Metrics:** relevance=`0.800`, authority=`0.750`, recency=`0.750`
- **Rationale:** Informational context without decisive directional threshold or polarity signal.
- **Excerpt:** > Thomas Aeschi is a Swiss businessman, politician and former banker.

### Signal 3: Schweizer Parlament - Startseite
- **Claim:** `C2-official-source` | **Stance:** `NEUTRAL_CONTEXT` | **Confidence:** `0.1440`
- **Source:** https://www.parlament.ch/de (`web_html`, domain `parlament.ch`)
- **Published:** `unknown` | **Fetched:** `2026-11-26T14:09:33Z`
- **Content SHA-256:** `2fb82bc31682060b166626b18da7351ae368177bbe09d1a0fe21e3535e7b10e5`
- **Metrics:** relevance=`0.600`, authority=`1.000`, recency=`0.600`
- **Rationale:** Informational context without decisive directional threshold or polarity signal.
- **Excerpt:** > Ratsmitglieder seit 1848

### Signal 4: Die Bundeskanzlei BK
- **Claim:** `C2-official-source` | **Stance:** `NEUTRAL_CONTEXT` | **Confidence:** `0.0240`
- **Source:** https://www.bk.admin.ch/de (`web_html`, domain `bk.admin.ch`)
- **Published:** `unknown` | **Fetched:** `2026-11-26T14:09:38Z`
- **Content SHA-256:** `8072c73ace5a4fe819f71832961a8d61eca54119bf39bf8f21dcda35e7832c6d`
- **Metrics:** relevance=`0.100`, authority=`1.000`, recency=`0.600`
- **Rationale:** Low term overlap with market entities/keywords.
- **Excerpt:** > Viktor Rossi (GLP) leitet die Bundeskanzlei seit 2024.

## 4. Provenance Ledger Verification Hashes
- `record#8` `C3-leading-indicator` -> `d2184ec6ac162664886233ea3f4f25c191520a41bf77386683e71e6116cbc2e8` (source sha256 `5a0a3ff9abdc8689...`)
- `record#9` `C3-leading-indicator` -> `b000ab9fbee26c26b4be13f5e4e1746d582c13413fabc0d42190d9fe81b6c54f` (source sha256 `9900599140237240...`)
- `record#10` `C2-official-source` -> `a3b572c5b7466d7f906c31b867f9ef888f4723788e6e54e14f37a6a65366e10a` (source sha256 `2fb82bc31682060b...`)
- `record#11` `C2-official-source` -> `d8cc57f3b87596ea41b8c6ffafb1fd0a8a9ebbc7e7d73e5a13911dc80b994cf1` (source sha256 `8072c73ace5a4fe8...`)

## 5. Delphi Submission Packet Preview
- **Packet Valid (<=3 items, <=8000 chars):** `True`
- **Links (2):** https://en.wikipedia.org/wiki/2026_Swiss_Federal_Council_election, https://en.wikipedia.org/wiki/Thomas_Aeschi

```text
Market: Will Thomas Aeschi (SVP) be elected to the Swiss Federal Council by the Federal Assembly on Dec 9, 2026, succeeding Guy Parmelin, per parlament.ch?
Assessment: NEUTRAL_CONTEXT | Prior YES=51.5% -> Implied YES=51.5% (Corroboration=0.00, Contradiction=0.00)

Dated Verifiable Evidence:
1. [NEUTRAL_CONTEXT | conf=0.27 | 2026-11-25T19:42:38Z] 2026 Swiss Federal Council election
   Excerpt: The announcement of President Guy Parmelin's resignation from the Federal Council on 2 October, effective on 31 December, triggered a by-election that will take place on 9 December 2026.
   Source: https://en.wikipedia.org/wiki/2026_Swiss_Federal_Council_election (sha256:5a0a3ff9abdc8689)
2. [NEUTRAL_CONTEXT | conf=0.18 | 2026-11-22T21:20:12Z] Thomas Aeschi
   Excerpt: Thomas Aeschi is a Swiss businessman, politician and former banker.
   Source: https://en.wikipedia.org/wiki/Thomas_Aeschi (sha256:9900599140237240)
3. [NEUTRAL_CONTEXT | conf=0.14 | fetched 2026-11-26T14:09:33Z] Schweizer Parlament - Startseite
   Excerpt: Ratsmitglieder seit 1848
   Source: https://www.parlament.ch/de (sha256:2fb82bc31682060b)
```
