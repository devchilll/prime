# USCIS AAO EB-1A Decisions Pipeline

An incremental scraper, PDF metadata extractor, analytics engine, and static
dashboard for **USCIS Administrative Appeals Office (AAO) non-precedent
decisions** in the **EB-1A** (extraordinary ability) category, covering
**January 2020 → present**. One monthly command discovers newly posted
decisions, downloads only the new PDFs, extracts structured metadata (outcome,
case type, which of the ten regulatory criteria were claimed and met,
final-merits determinations), and regenerates aggregate analytics plus a
self-contained HTML dashboard. NIW (`uri_1=18`) is supported but off by
default.

## Why

EB-1A petitioners and their attorneys make high-stakes decisions with almost
no public data on how the AAO actually rules: which criteria survive scrutiny,
how often appeals are sustained, how outcomes trend over time. The decisions
are public US government records, but they are published as thousands of
individual PDFs behind a month-by-month listing — effectively unanalyzable.
This project turns them into an open, reproducible dataset and dashboard.
It is a data-transparency tool, **not legal advice**; extraction is automated
and imperfect (see limitations below).

## Quickstart

```bash
cd uscis_eb1a_scraper
uv sync --group dev
uv run python -m uscis_eb1a_scraper update -v
```

The first run backfills 2020 → now (hours, resumable); subsequent runs are
incremental and take minutes. See **[INSTRUCTIONS.md](INSTRUCTIONS.md)** — the
operator runbook — for the monthly procedure, recovery, QA review, and CI
automation.

## Architecture

```
 USCIS listing pages                      data/manifest.jsonl
 (uri_1/m/y filters)  --- scrape --->     (state: every known PDF,
                                           download/extract status)
                                                |
                                            download (new only)
                                                v
                                          data/pdfs/<YYYY>/*.pdf
                                                |
                                            extract (pypdf + regex)
                                                v
                                          data/text/<YYYY>/  -->  data/cases.jsonl
                                                                  (per-case records)
                                                                        |
                                                                     analyze
                                                                        v
                                                                 data/analytics.json
                                                                        |
                                                                      render
                                                                        v
                                                                 dashboard/index.html
```

Each stage is a CLI subcommand (`scrape`, `download`, `extract`, `analyze`,
`render`); `update` chains them incrementally, and `status` inspects progress.
Only `scrape`/`download` touch the network.

## How the USCIS site is structured

The AAO publishes non-precedent decisions at:

> https://www.uscis.gov/administrative-appeals/aao-decisions/aao-non-precedent-decisions

### Listing URL parameters

| Parameter | Meaning | Example |
|-----------|---------|---------|
| `uri_1`   | Case **category** (numeric code) | `19` |
| `m`       | **Month** (1–12)                 | `5`  |
| `y`       | **Year**                         | `2025` |
| `page`    | Pagination (0-based)             | `1`  |

So EB-1A decisions published in May 2025:

```
https://www.uscis.gov/administrative-appeals/aao-decisions/aao-non-precedent-decisions?uri_1=19&m=5&y=2025
```

### Category codes (`uri_1`)

| `uri_1` | Category | USCIS name | PDF folder / file code |
|---------|----------|------------|------------------------|
| **19**  | **EB-1A** *(this project's target)* | Aliens with Extraordinary Ability | `B2 - Aliens with Extraordinary Ability/` → `…B2203.pdf` |
| 18      | NIW | Members of the Professions holding Advanced Degrees or Aliens of Exceptional Ability | `B5 - Members of the Professions…/` → `…B5203.pdf` |

### Decision PDFs

Each decision is a PDF at a predictable path:

```
/sites/default/files/err/<CATEGORY DIR>/Decisions_Issued_in_<YEAR>/<FILE>.pdf
```

e.g. `…/err/B2 - Aliens with Extraordinary Ability/Decisions_Issued_in_2025/MAY282025_05B2203.pdf`.
The filename encodes the decision date, a per-day sequence number, and the
category file code: `MAY282025_05B2203.pdf` → May 28 2025, sequence 05, code
B2203 (EB-1A).

## Operations

Day-to-day operation lives in **[INSTRUCTIONS.md](INSTRUCTIONS.md)**: monthly
runs, exit codes, what to commit, first-time backfill, failure recovery,
extraction QA, troubleshooting, and a GitHub Actions workflow
(`ci/monthly-update.yml.example`) for full automation.

## Data and licensing

The underlying decisions are **public records of the United States
government** (works of the federal government are not subject to copyright).
This repository's committed dataset (`data/*.jsonl`, `data/*.json`) is derived
from those records via automated extraction. Decisions are published by USCIS
with personal identifiers redacted. Nothing here is legal advice; verify any
individual data point against the linked source PDF before relying on it.

## Current limitations

- **Regex extraction is imperfect.** Outcomes taken from explicit `ORDER:`
  lines are high-confidence; narrative inference is medium/low. Criteria
  determinations are tri-state (`met: true/false/null`) and unknowns are
  excluded from analytics denominators, but misreads still happen — see the
  QA procedure in INSTRUCTIONS.md.
- **Live-site validation is pending in restricted sandboxes.** The scraper was
  built and unit-tested against captured fixtures in an environment that
  blocks `www.uscis.gov`; run one live month from an unrestricted network to
  confirm before trusting a first production run.
- Scanned-image PDFs (no text layer) yield `empty_text` and are excluded.

## Repository layout

```
uscis_eb1a_scraper/
├── README.md                  # this file
├── INSTRUCTIONS.md            # operator runbook (start here for operations)
├── pyproject.toml             # package metadata; installs `uscis-eb1a` CLI
├── uscis_eb1a_scraper/        # the package
│   ├── config.py              #   categories, URLs, rate limits, data layout
│   ├── client.py              #   polite HTTP client (browser UA, retries, CA bundle)
│   ├── scraper.py             #   listing scraper (fetch_month / fetch_range)
│   ├── parser.py              #   stdlib HTML parsing of listing pages
│   ├── models.py              #   Decision model, filename parsing
│   ├── state.py               #   manifest read/write, locking
│   ├── download.py            #   incremental PDF downloads
│   ├── extract.py             #   PDF text -> case records
│   ├── analytics.py           #   case records -> analytics.json
│   ├── render.py              #   analytics.json -> dashboard
│   ├── pipeline.py            #   the `update` orchestration
│   └── cli.py                 #   subcommands: update/scrape/download/extract/analyze/render/status
├── tests/                     # offline unit tests + captured fixtures
├── ci/monthly-update.yml.example  # opt-in GitHub Actions monthly workflow
├── data/                      # manifest + cases + analytics (partly committed)
└── dashboard/index.html       # generated static dashboard (committed)
```
