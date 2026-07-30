# USCIS AAO EB-1A Decision Scraper

Discover and download **USCIS Administrative Appeals Office (AAO) non-precedent
decisions**, filtered to **EB-1A (extraordinary ability)** cases by month and
year.

The AAO publishes these decisions here:

> https://www.uscis.gov/administrative-appeals/aao-decisions/aao-non-precedent-decisions

## How the USCIS site is structured

### The listing page and its URL parameters

The listing on the page above is driven by three query-string parameters:

| Parameter | Meaning | Example |
|-----------|---------|---------|
| `uri_1`   | Case **category** (numeric code) | `19` |
| `m`       | **Month** (1–12)                 | `5`  |
| `y`       | **Year**                         | `2025` |

So to list **EB-1A** decisions published in **May 2025**:

```
https://www.uscis.gov/administrative-appeals/aao-decisions/aao-non-precedent-decisions?uri_1=19&m=5&y=2025
```

### Category codes (`uri_1`)

| `uri_1` | Category | USCIS name | PDF folder / file code |
|---------|----------|------------|------------------------|
| **19**  | **EB-1A** *(this project's target)* | Aliens with Extraordinary Ability | `B2 - Aliens with Extraordinary Ability/` → `…B2203.pdf` |
| 18      | NIW | Members of the Professions holding Advanced Degrees or Aliens of Exceptional Ability | `B5 - Members of the Professions…/` → `…B5203.pdf` |

### Individual decision PDFs

Each decision is a PDF served from a predictable path:

```
/sites/default/files/err/<CATEGORY DIR>/Decisions_Issued_in_<YEAR>/<FILE>.pdf
```

e.g.

```
.../err/B2 - Aliens with Extraordinary Ability/Decisions_Issued_in_2025/MAY282025_05B2203.pdf
```

The filename encodes the decision date, a per-day sequence number, and the
category file code: `MAY282025_05B2203.pdf` → **May 28, 2025**, sequence **05**,
code **B2203** (EB-1A). The scraper parses all of this into a `Decision` object.

## Install

The only hard dependency is `requests` (HTML parsing uses the standard
library — no BeautifulSoup needed):

```bash
pip install -r requirements.txt
```

## Usage

### Command line

```bash
# EB-1A decisions for May 2025 (eb1a is the default category):
python -m uscis_eb1a_scraper --year 2025 --month 5

# A whole date range, as JSON:
python -m uscis_eb1a_scraper --start 2024-01 --end 2025-12 --json

# Download the PDFs:
python -m uscis_eb1a_scraper --year 2025 --month 5 --download --out-dir decisions

# NIW instead of EB-1A:
python -m uscis_eb1a_scraper --category niw --year 2025 --month 5
```

### Python API

```python
from uscis_eb1a_scraper import AAOScraper

with AAOScraper() as scraper:
    # All EB-1A decisions published in May 2025:
    decisions = scraper.fetch_month("eb1a", year=2025, month=5)

    # Every EB-1A decision over a range (de-duplicated):
    decisions = scraper.fetch_range("eb1a", start=(2024, 1), end=(2025, 12))

    for d in decisions:
        print(d.decision_date, d.filename, d.url)
        scraper.download(d, out_dir="decisions")
```

## Design notes

- **Browser User-Agent.** USCIS returns `403 Forbidden` to clients that do not
  look like a browser, so the HTTP client sends a realistic desktop
  User-Agent by default.
- **Polite + resilient.** Requests are rate-limited (default 1.5s apart) and
  retried with exponential backoff on transient errors (429/5xx, network
  failures).
- **CA bundle aware.** If `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE` is set (e.g.
  behind an inspecting proxy), the client uses it automatically.

## Tests

Offline unit tests cover URL building, HTML link extraction, filename parsing,
and date-range iteration (no network access required):

```bash
cd uscis_eb1a_scraper
pytest tests/ -q
```

## ⚠️ Live-site validation still needed

The scraper was built and unit-tested in an environment whose egress policy
**blocks `www.uscis.gov`**, so the exact HTML of the live listing page could
**not** be fetched and parsed end-to-end here. The category codes, URL
parameters, and PDF path/filename patterns are taken from real USCIS decision
URLs (confirmed via search), and the parser keys off the stable
`/sites/default/files/err/.../*.pdf` link pattern.

Before relying on results, run one live query from an unrestricted network and
confirm the listing page returns the expected `…/err/…B2203.pdf` links:

```bash
python -m uscis_eb1a_scraper --year 2025 --month 5 -v
```

If USCIS ever changes the page so decision links are loaded via a separate
AJAX/JSON endpoint rather than embedded in the HTML, only
`parser.extract_decision_links` (and possibly the listing URL) would need
adjusting — the rest of the pipeline is unaffected.
