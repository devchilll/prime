# Operator Runbook — USCIS AAO EB-1A Decisions Pipeline

> **Keep this document updated.** This is the living runbook for operating the
> pipeline. Any code change that alters commands, file layouts, schemas, exit
> codes, or the update workflow **must** update this document in the same
> change. Treat a stale runbook as a bug.

---

## 1. Overview and what gets produced

The pipeline incrementally scrapes **USCIS Administrative Appeals Office (AAO)
non-precedent decisions** in the **EB-1A** category (extraordinary ability,
`uri_1=19`; NIW `uri_1=18` is supported but off by default), downloads the
decision PDFs, extracts per-case metadata (outcome, case type, the ten
regulatory criteria, final-merits determination), aggregates analytics, and
renders a static HTML dashboard. The dataset covers **January 2020 → present**.

One command drives everything month to month: `update`. It re-scans a trailing
window of listing months, diffs against the manifest, and only downloads and
extracts what is new.

### Files produced

| Path | What it is | In git? |
|------|------------|---------|
| `data/manifest.jsonl` | Pipeline state: one line per discovered PDF, with download/extraction status | **Committed** |
| `data/pdfs/<YYYY>/` | Raw decision PDFs | Ignored |
| `data/text/<YYYY>/` | Extracted plain text per PDF | Ignored |
| `data/cases.jsonl` | One structured case record per decision (see Appendix) | **Committed** |
| `data/analytics.json` | Aggregates powering the dashboard (see Appendix) | **Committed** |
| `data/extraction_qa.json` | QA report of extraction samples for human spot-checks | **Committed** |
| `dashboard/index.html` | Self-contained static dashboard | **Committed** |
| `data/.lock` | Concurrency guard while a run is live (stale after 6 h) | Ignored |

PDFs and text are regenerable from the manifest, so they stay out of git; the
manifest, case records, analytics, QA report, and dashboard are the durable
outputs and are committed after each run.

---

## 2. One-time setup

1. **Install uv** (Python package manager): https://docs.astral.sh/uv/getting-started/installation/
2. **Install dependencies** (runtime + dev/test):

   ```bash
   cd uscis_eb1a_scraper
   uv sync --group dev
   ```

3. **Sanity-check** the offline test suite (no network needed):

   ```bash
   uv run pytest tests/ -q
   ```

### Network notes (read before the first live run)

- **USCIS returns HTTP 403 to non-browser clients.** The HTTP client sends a
  realistic desktop browser User-Agent by default, so no action is normally
  needed. If you see 403 on every request, this is the first thing to check.
- **Restricted egress environments** (sandboxes, CI with egress policies):
  `www.uscis.gov` must be allowlisted or every request will fail. In
  *Claude Code on the web*, add `www.uscis.gov` in the environment's network
  policy settings. Exit code 3 ("network unreachable") usually means this.
- **Inspecting proxies / custom CAs:** the client honors `REQUESTS_CA_BUNDLE`
  (and `SSL_CERT_FILE`). If your environment intercepts TLS, point
  `REQUESTS_CA_BUNDLE` at the proxy's CA bundle. Never disable TLS
  verification.

---

## 3. Monthly run

Run this once a month (USCIS posts decisions within roughly a month of
issuance and backfills older months, so the run re-scans a trailing window):

```bash
cd uscis_eb1a_scraper && uv run python -m uscis_eb1a_scraper update -v
```

(Equivalently, `uv run uscis-eb1a update -v` — the package installs a
`uscis-eb1a` console script.)

### What `update` does

1. Re-scans the trailing **4-month** listing window (`--window 4` to change)
   plus any months never scanned before.
2. Diffs discovered PDFs against `data/manifest.jsonl`.
3. Downloads only **new** PDFs (politely rate-limited, 1.5 s between requests).
4. Extracts metadata for only **new** cases.
5. Regenerates `data/analytics.json` and `dashboard/index.html`.

Useful flags: `--start YYYY-MM` / `--end YYYY-MM` to bound the scan,
`--no-download` to only refresh the manifest, `-v` for progress logging.

### Expected output

With `-v` you will see per-month scan lines and per-PDF download/extract
progress. Every run ends with a single summary line:

```
update: discovered=N new=N downloaded=N failed=N extracted=N cases=N dashboard=written
```

In a typical steady-state month expect `new` in the tens, `failed=0`, and
`dashboard=written`.

### Exit codes

| Code | Meaning | What to do |
|------|---------|------------|
| 0 | Full success | Commit outputs |
| 2 | Completed, but some items failed (e.g. a PDF 404'd or failed extraction) | Commit what succeeded; rerun `update` or `download --retry-failed` later — it heals |
| 3 | Network unreachable (could not reach `www.uscis.gov` at all) | Check egress/allowlist/proxy (Section 2); nothing was corrupted |
| 1 | Fatal error (bug, bad arguments, corrupt state) | Read the traceback; see Section 9 |

### Commit after the run

```bash
git add data/*.jsonl data/*.json dashboard/index.html
git commit -m "chore: monthly AAO EB-1A data update"
```

Do **not** add `data/pdfs/` or `data/text/` — they are gitignored by design.

---

## 4. First-time backfill

The very first `update` run backfills **2020-01 → now**. Plan for it:

- **It takes hours.** Requests are rate-limited to 1.5 s apart out of
  politeness, and there are thousands of decision PDFs since 2020. Do not
  remove the rate limit.
- **It is safe to interrupt.** All progress is recorded in
  `data/manifest.jsonl` as it happens. Rerunning `update` resumes where it
  left off — already-scanned months are not re-fetched (outside the trailing
  window) and already-downloaded PDFs are skipped.
- **Watch progress** from another terminal:

  ```bash
  uv run python -m uscis_eb1a_scraper status
  ```

  `status` summarizes manifest counts by download/extraction status and shows
  which months have been scanned.

Run the backfill somewhere with stable, unrestricted egress to
`www.uscis.gov` if possible; CI runners and laptops both work.

---

## 5. Recovery

Every discovered PDF has a manifest line with two status fields (full schema
in the Appendix):

- `download_status`: `pending` → not attempted yet; `ok` → on disk, checksummed;
  `failed` → attempted and errored (see `last_error`); `not_pdf` → the server
  returned something that is not a PDF (usually an HTML error page).
- `extraction_status`: `pending` → not attempted; `ok` → case record written;
  `empty_text` → PDF opened but yielded no usable text (often a scanned image);
  `error` → extractor raised (see `last_error`).

Recovery commands:

| Situation | Command |
|-----------|---------|
| Some downloads failed (exit 2) | `uv run python -m uscis_eb1a_scraper download --retry-failed` |
| Re-extract everything (e.g. after improving `extract.py`) | `uv run python -m uscis_eb1a_scraper extract --force` |
| Just re-run the whole increment | `uv run python -m uscis_eb1a_scraper update -v` (idempotent) |

### The lock file

`data/.lock` guards against two runs mutating state concurrently. It is
considered **stale after 6 hours** and will be broken automatically. If a run
crashed and you get a lock error sooner, first confirm **no run is live**
(check your terminals / CI jobs), then delete it:

```bash
rm data/.lock
```

Never delete the lock while a run may still be in flight.

---

## 6. Regenerating outputs offline

Only `scrape`/`download` (and therefore `update`) touch the network.
Everything downstream works from local files, so you can iterate on
extraction, analytics, or dashboard rendering with no connectivity:

```bash
uv run python -m uscis_eb1a_scraper extract        # PDFs/text -> data/cases.jsonl
uv run python -m uscis_eb1a_scraper analyze        # cases.jsonl -> data/analytics.json
uv run python -m uscis_eb1a_scraper render         # analytics.json -> dashboard/index.html
```

Run them in that order when regenerating from scratch; each reads the previous
stage's output.

---

## 7. Extraction QA review procedure

Extraction is **regex-based** over `pypdf` text and will occasionally
misclassify. Review it periodically (at minimum after any change to
`extract.py`, and quarterly otherwise):

1. Generate the QA report:

   ```bash
   uv run python -m uscis_eb1a_scraper extract --qa
   ```

2. Open `data/extraction_qa.json`. It contains sampled cases with the
   extracted fields and source URLs.
3. For each sample, open the linked PDF and check the extracted `outcome`,
   `case_type`, and criteria determinations against the actual ORDER line and
   criteria discussion in the decision.
4. **Sanity checks:**
   - Roughly **~90% of decisions should be `dismissed`** — AAO sustains few
     EB-1A appeals. A materially different ratio suggests an extraction
     regression, not a real-world shift.
   - `outcome_confidence: high` cases (outcome taken from an explicit
     `ORDER:` line) should essentially never be wrong. Errors concentrate in
     `medium`/`low` confidence cases (narrative inference).
   - Cases with `flags` set deserve a manual look.
5. If you find systematic errors, fix the patterns in `extract.py`, rerun
   `extract --force`, then `analyze` and `render`.

Note that unknowns are handled honestly downstream: a criterion with
`met: null` is **excluded from analytics denominators** rather than counted
either way.

---

## 8. Automating with GitHub Actions

A ready-made workflow lives at `ci/monthly-update.yml.example`. It is an
`.example` file (not live) **deliberately**: the repo-root `.github/` directory
is shared with another project, so enabling the workflow is an explicit opt-in.

To enable:

```bash
mkdir -p .github/workflows
cp uscis_eb1a_scraper/ci/monthly-update.yml.example .github/workflows/uscis-monthly-update.yml
git add .github/workflows/uscis-monthly-update.yml
git commit -m "ci: enable monthly AAO EB-1A update workflow"
```

What it does: on the 3rd of each month (and on manual dispatch) it checks out
the repo, installs uv, runs `update -v` (treating exit code 2 as success —
per-item failures heal on the next run), and commits
`data/*.jsonl`, `data/*.json`, and `dashboard/index.html` back to the repo.

GitHub-hosted runners have unrestricted egress, so **no network allowlisting
or proxy setup is needed** there. The workflow needs `permissions:
contents: write` to push the data commit (already declared in the file).

---

## 9. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| **403 Forbidden on every request** | User-Agent not browser-like, or an egress proxy is rewriting/blocking requests | Confirm `DEFAULT_USER_AGENT` in `config.py` is intact; check whether your environment's proxy blocks `www.uscis.gov` |
| **TLS / certificate verify errors** | Inspecting proxy with a private CA | `export REQUESTS_CA_BUNDLE=/path/to/ca-bundle.crt` and rerun. Never disable verification |
| **Exit code 3** | `www.uscis.gov` unreachable | Check connectivity; in restricted sandboxes, allowlist `www.uscis.gov` (Claude Code on the web: environment network policy settings) |
| **`0 discovered` for a month known to have decisions** | USCIS changed the listing page structure | See below |
| **Lock error (`data/.lock` exists)** | Another run live, or a crashed run left the lock | If truly no run is live, `rm data/.lock` (auto-stale after 6 h). See Section 5 |
| **Exit 2 every run for the same items** | A listed PDF genuinely 404s or is not a PDF (`download_status: not_pdf`) | Inspect the manifest line's `last_error`; these can be left failed — they are excluded from analytics |
| **Many `empty_text` extraction statuses** | Scanned-image PDFs (no text layer) | Expected for a small minority; they are flagged and excluded. A sudden spike means a pypdf or extractor regression |

### "0 discovered": diagnosing a page-structure change

The parser (`uscis_eb1a_scraper/parser.py`) keys off anchor `href`s matching
`/sites/default/files/err/.../*.pdf` in the listing HTML. If USCIS changes the
markup (or moves the links behind an AJAX/JSON endpoint):

1. Grab the live HTML for a known-good month:

   ```bash
   curl -A "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36" \
     "https://www.uscis.gov/administrative-appeals/aao-decisions/aao-non-precedent-decisions?uri_1=19&m=5&y=2025" \
     > tests/fixtures/listing_new.html
   ```

2. Inspect it: are the `…B2203.pdf` links present? Under what markup?
3. Adjust `parser.py` (and, if the URL scheme changed, `config.py`/`scraper.py`),
   add the captured HTML as a fixture in `tests/fixtures/`, and extend
   `tests/test_scraper.py` to cover the new shape.
4. Only `parser.extract_decision_links` (and possibly the listing URL) should
   need changing — the rest of the pipeline is insulated behind the manifest.

---

## 10. Changing scope

- **NIW instead of / in addition to EB-1A:** pass `--category niw` to the
  relevant subcommands. NIW is `uri_1=18`, PDF directory
  `B5 - Members of the Professions holding Advanced Degrees or Aliens of
  Exceptional Ability`, file code `B5203`. Category definitions live in
  `CATEGORIES` in `uscis_eb1a_scraper/config.py`.
- **Dataset start date:** `DEFAULT_START = (2020, 1)` in `config.py`. Changing
  it only affects months not yet in the manifest (moving it earlier triggers a
  backfill of the added months on the next `update`).
- **Trailing re-scan window:** `RESCAN_MONTHS = 4` in `config.py` (or per-run
  via `update --window N`). Widen it if you notice USCIS backfilling further
  back than 4 months.

---

## 11. Appendix: schemas and criteria

These schemas are the **contract** between pipeline stages. Changing any field
is a breaking change: update the writers, the readers, this appendix, and bump
`schema_version` in analytics.

### A. Manifest line (`data/manifest.jsonl`, one JSON object per line)

```
{
  id,                     // stable case id, filename without .pdf (e.g. "MAY282025_05B2203")
  filename,               // PDF filename (e.g. "MAY282025_05B2203.pdf")
  url,                    // absolute PDF URL on www.uscis.gov
  category,               // "eb1a" | "niw"
  decision_date,          // ISO date parsed from the filename, or null
  listing_months,         // ["YYYY-MM", ...] every listing month this PDF appeared under
  first_seen_at,          // ISO timestamp when first discovered
  sha256,                 // hex digest of the downloaded PDF (null until downloaded)
  size_bytes,             // PDF size (null until downloaded)
  downloaded_at,          // ISO timestamp of successful download, or null
  download_status,        // "pending" | "ok" | "failed" | "not_pdf"
  extraction_status,      // "pending" | "ok" | "empty_text" | "error"
  extracted_at,           // ISO timestamp of last extraction attempt, or null
  last_error              // string describing the most recent failure, or null
}
```

### B. Case record (`data/cases.jsonl`, one JSON object per line)

```
{
  id, url, filename,      // as in the manifest
  category,               // "eb1a" | "niw"
  decision_date,          // ISO date
  listing_month,          // "YYYY-MM" the decision was listed under
  sha256,                 // PDF checksum (ties the record to an exact PDF)
  pages,                  // page count
  text_chars,             // characters of extracted text
  extraction: { status, extractor, extracted_at },
  in_re_number,           // "In Re:" number from the decision header, or null
  form,                   // petition form, e.g. "I-140"
  case_type,              // "appeal" | "motion_reopen" | "motion_reconsider"
                          //   | "motion_combined" | "unknown"
  outcome,                // "dismissed" | "sustained" | "remanded"
                          //   | "dismissed_in_part" | "rejected" | "withdrawn" | "unknown"
  outcome_confidence,     // "high" (explicit ORDER: line) | "medium" (narrative) | "low"
  service_center,         // originating service center, or null
  criteria: {             // one entry per regulatory criterion (table below):
    awards:                { discussed, claimed, met },   // met: true | false | null
    membership:            { discussed, claimed, met },
    published_material:    { discussed, claimed, met },
    judging:               { discussed, claimed, met },
    original_contributions:{ discussed, claimed, met },
    scholarly_articles:    { discussed, claimed, met },
    artistic_exhibitions:  { discussed, claimed, met },
    leading_critical_role: { discussed, claimed, met },
    high_salary:           { discussed, claimed, met },
    commercial_success:    { discussed, claimed, met }
  },
  criteria_met_count,     // count of criteria with met == true
  final_merits: {         // Kazarian step two
    reached,              // bool: did the AAO reach a final merits determination
    outcome               // e.g. "met" / "not_met", or null when not reached
  },
  flags                   // [] of strings marking anomalies for QA attention
}
```

Semantics of the criterion tri-state: `discussed` — the decision discusses the
criterion at all; `claimed` — the petitioner asserted it; `met` — the AAO's
determination (`true`/`false`), or `null` when not determinable from the text.
**`met: null` entries are excluded from analytics denominators.**

### C. Analytics (`data/analytics.json`, single JSON object)

Top-level keys:

```
{
  generated_at,             // ISO timestamp
  schema_version,           // integer; bump on any breaking shape change
  coverage,                 // months scanned, case counts, date range
  headline,                 // headline numbers for the dashboard (totals, rates)
  monthly,                  // per-month case counts / outcomes time series
  yearly_outcomes,          // outcome breakdown per year
  case_type_outcomes,       // outcome breakdown per case_type
  criteria,                 // per-criterion discussed/claimed/met aggregates
  cooccurrence_favorable,   // which criteria are met together in favorable cases
  criteria_met_distribution,// histogram of criteria_met_count
  final_merits,             // how often step two is reached, and its outcomes
  lag_histogram,            // decision_date -> listing_month publication lag
  takeaways,                // generated plain-language findings
  qa                        // extraction-quality stats (confidence mix, flags, unknown rates)
}
```

### D. The ten EB-1A criteria (8 CFR 204.5(h)(3))

| Key | CFR cite | Label |
|-----|----------|-------|
| `awards` | 8 CFR 204.5(h)(3)(i) | Lesser nationally or internationally recognized prizes or awards |
| `membership` | 8 CFR 204.5(h)(3)(ii) | Membership in associations requiring outstanding achievement |
| `published_material` | 8 CFR 204.5(h)(3)(iii) | Published material about the person in professional or major trade media |
| `judging` | 8 CFR 204.5(h)(3)(iv) | Participation as a judge of the work of others in the field |
| `original_contributions` | 8 CFR 204.5(h)(3)(v) | Original contributions of major significance |
| `scholarly_articles` | 8 CFR 204.5(h)(3)(vi) | Authorship of scholarly articles |
| `artistic_exhibitions` | 8 CFR 204.5(h)(3)(vii) | Display of work at artistic exhibitions or showcases |
| `leading_critical_role` | 8 CFR 204.5(h)(3)(viii) | Leading or critical role for organizations with distinguished reputations |
| `high_salary` | 8 CFR 204.5(h)(3)(ix) | High salary or remuneration relative to others in the field |
| `commercial_success` | 8 CFR 204.5(h)(3)(x) | Commercial success in the performing arts |
