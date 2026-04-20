# wirecard-ft-timeline

Forensic chronology of Financial Times coverage of Wirecard AG (April 2015 – June 2020),
overlaid on the WDI.DE share price.

Research scaffolding only. All analytical commentary will be authored separately by the
project owner per Yale SOM AI policy.

## Layout

- `data/raw/` — unprocessed Wayback CDX dumps, Stooq CSV, source-status log
- `data/interim/` — deduped article candidate sets, per-article snapshot HTML metadata
- `data/processed/` — `ft_wirecard_articles.csv` final chronology
- `outputs/charts/` — `ft_articles_vs_price.{png,svg}`
- `outputs/tables/` — `ft_wirecard_timeline.md`, `ft_article_count_by_year.md`
- `src/` — Python modules for CDX querying, snapshot fetching, parsing, plotting

## Ground rules (locked)

- No fabrication. Unretrievable URLs → logged to `data/raw/SOURCE_STATUS.md`.
- Every row cites: live URL + Wayback snapshot URL + retrieval timestamp.
- No more than 15 words of FT content reproduced per article.
- Dedupe by (headline, publication_date), not URL.
- Paywall-only snapshots flagged as `paywall_only` in `url_wayback`.

## Environment

Managed with `uv`. Core deps: pandas, requests, beautifulsoup4, lxml, matplotlib, tenacity.

```
uv sync
uv run python src/cdx_query.py
```
