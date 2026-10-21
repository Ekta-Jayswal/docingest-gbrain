# Ingestion verification — FAIL

- Run: 2026-10-21T10:22:29+0000
- Brain: /home/ubuntu/gbrain-ingestion-pipeline/docingest-gbrain/brain
- Documents: 1 (1 failed)
- Pages: 67, chunks: 69, links: 180
- Took 7.3 s

## FAIL Brd

`/home/ubuntu/gbrain-ingestion-pipeline/docingest-gbrain/brd_doc/brd.md` · kind **brd** · key `brd` · 40 pages, 42 chunks, 79 links

| Check | Result | Detail |
|---|---|---|
| conversion | PASS | 100.00% of source words present in markdown (markdown) |
| sections | PASS | 6 sections cover 17985 chars exactly |
| tables | INFO | 13 tables; every ID row became an entity; 7 table(s) kept as section text only (no ID column) |
| references | PASS | All 33 defined IDs resolve; no dangling references |
| pages | PASS | 40/40 pages present with expected types |
| content | PASS | Stored text and attributes match what was written |
| chunks | PASS | 42 chunks cover every byte of 40 pages |
| links | PASS | 79/79 expected edges present |
| search | FAIL | 4/5 sections found by keyword search |
| rule confidence | INFO | 5 of 6 sections hold content rules cannot extract (stored, chunked and searchable, but entities in it are not extracted); run with --llm to extract them |

**search** (1):

- 'novemberdecember' did not find docs/sections/brd/2-part-2

**rule confidence** (5):

- section 1 (Part 1): 376 words of prose or list text the rules do not parse; 2 table(s) without ID or field columns (8 rows) left as text
- section 2 (Part 2): 370 words of prose or list text the rules do not parse; 1 table(s) without ID or field columns (6 rows) left as text
- section 3 (Part 3): 285 words of prose or list text the rules do not parse; 2 table(s) without ID or field columns (15 rows) left as text
- section 5 (Part 5): 186 words of prose or list text the rules do not parse; 2 table(s) without ID or field columns (12 rows) left as text
- section 6 (Part 6): 23 words of prose or list text the rules do not parse

## Global

| Check | Result | Detail |
|---|---|---|
| shared pages | PASS | 27/27 pages present with expected types |
| shared content | PASS | Stored text and attributes match what was written |
| shared chunks | PASS | 27 chunks cover every byte of 27 pages |
| shared links | PASS | 101/101 expected edges present |
| type coverage | PASS | 347/347 pages typed; 0 untyped, 0 with undeclared types |
| embeddings | INFO | 0 of 69 verified chunks embedded (ingestion runs with --no-embed) |
| stale pages | PASS | No stale pages from earlier runs |
