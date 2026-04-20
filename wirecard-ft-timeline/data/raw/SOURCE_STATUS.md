# SOURCE_STATUS

Log of any URL or date reference that could not be verified against a primary source
(Wayback snapshot with article body present). Entries added as encountered.

| retrieval_ts | candidate_url | issue | action_taken |
|---|---|---|---|
| 2026-04-20T13:28Z | https://web.archive.org/cdx/search/cdx?url=ft.com/wirecard* | HTTP 403, `x-block-reason: hostname_blocked` — sandbox egress policy blocks web.archive.org | Halted pipeline; awaiting allowlist or offline CDX dump |
| 2026-04-20T13:28Z | https://archive.org/ | HTTP 403, `x-block-reason: hostname_blocked` | Same — blocked |
| 2026-04-20T13:28Z | https://stooq.com/q/d/?s=wdi.de | HTTP 503 from sandbox proxy | Cannot fetch price series from here |
| 2026-04-20T13:28Z | https://www.ft.com/ , https://ftalphaville.ft.com/ | HTTP 503 | Cannot verify URLs from here (not needed for article bodies; flagged for transparency) |
| 2026-04-20T13:28Z | https://pressgazette.co.uk/ | HTTP 503 | Cross-reference source unavailable from here |
