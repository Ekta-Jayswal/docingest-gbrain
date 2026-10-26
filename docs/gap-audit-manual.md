# Gap audit: user manual

The gap audit shows how well one document has been turned into linked pages in the brain. For each section it lists the items the section names, the typed links those items make, and the tables and prose that no item was taken from. Use it before you trust a document's graph, and again after you fix a gap.

The audit reads the same manifest that ingestion writes, so its numbers match what the brain would receive. It does not write to the brain.

## Run an audit

```bash
./ingest.sh gap-audit "<file>"
```

Useful options:

| Option | What it does |
|---|---|
| `--kind brd` | Force the document kind (`brd`, `prd`, `sow`, ...) when detection gets it wrong. |
| `--find "text"` | Also list every section where this text appears. Use it to check a detail that is stated in two places. |
| `--llm` | Also run the AI extraction and the cross-section link pass. Needs the LiteLLM proxy and costs tokens. |
| `--llm-effort low\|medium\|high` | How hard the AI thinks when `--llm` is used. |
| `--out <path>` | Write the page somewhere else. |

Several files can go on one page: pass them all in one command.

The page is written to `work/reports/gap-audit/<document>.html` unless `--out` is given. Open it in a browser.

## Read the page

The page has a section table and an item table.

**Section table**: one row per section, with these columns.

- **Items named**: how many items the section mentions.
- **Typed**: how many of those items have a typed link.
- **Links out**: how many typed links the section's items make.
- **Which items**: the item IDs.
- **Tables (header → became)**: each table and the kind of entity its rows became.
- **Gap**: why the section is a gap, if it is one.

A row marked in red is a gap. A gap is one of these:

- **Prose with no items**: a section of 300 characters or more where no item was taken from the text. The Executive Summary in the sample document is an example.
- **Table with no entities**: a table whose rows did not become items.
- **Items with no typed links**: items the section names but that link to nothing.

**Item table**: one row per item, with the sections that name it, its typed links out and in, and whether it has any typed link. An item with no typed link in or out is a gap.

Items named in more than one section show the first and last section they appear in.

## Fill the gaps

Work through the gaps in this order. Re-run the audit after each change.

1. **Prose with no items.** The rules did not find anything in the text. Either write the items explicitly in the source document (one row per item, with its ID), or run the audit with `--llm` to let the AI read the low-confidence sections.
2. **Items with no typed links.** The source does not state the relationship in a form the rules read. Say it explicitly, for example "depends on AS-2", or use `--find` to check whether the detail is written in another section.
3. **Unknown ID prefixes or table columns.** These are schema gaps. They go to the review board, where a reviewer approves, maps or rejects them. Use `./ingest.sh review list` to see them and `./ingest.sh review show <id>` to see one.
4. **Gaps that are in the document itself.** For example, a requirement with no owner, or a table nobody linked. These are findings for the document's author. Send the page to them.

After a change, re-ingest the document and run the audit again. Compare the gap counts with the earlier page. Ingestion scores the document against its answer key automatically (unless you pass `--no-eval`). Check the scores to make sure nothing that passed before has broken.

## Good practice

- Run the audit before you ingest a new version of a document, and keep the page with the run.
- Do not use `--llm` on every run. Use it when rules leave prose uncovered, and check its result by hand.
- Treat the gap count as a checklist, not a score. A document with zero gaps can still be wrong, so keep checking the answer keys.
