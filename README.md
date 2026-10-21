# docingest

`docingest` ingests business documents into a [gbrain](https://github.com/garrytan/gbrain) brain:

- BRDs, PRDs and SOWs
- contracts
- emails
- meeting transcripts
- chat exports
- spreadsheets
- slide decks and plain notes

For each document it:

1. converts the file to markdown,
2. splits it into sections,
3. extracts entities (requirements, risks, people, systems, …) and typed relationships between them,
4. imports everything into gbrain, chunked and without embeddings,
5. verifies that nothing was lost.

You can pass one file or a whole folder; the files are processed one after another. Extraction is rule-based first. An optional AI pass (Claude) reads only the sections the rules could not fully handle.

## Contents

1. [Quick start](#1-quick-start)
2. [Installation and requirements](#2-installation-and-requirements)
3. [How the pipeline works](#3-how-the-pipeline-works)
4. [docingest commands](#4-docingest-commands)
5. [Supported input formats](#5-supported-input-formats)
6. [Extraction: rules first, AI where needed](#6-extraction-rules-first-ai-where-needed)
7. [What ends up in the brain](#7-what-ends-up-in-the-brain)
8. [Verification](#8-verification)
9. [Sample input](#9-sample-input)
10. [Sample output](#10-sample-output)
11. [Exploring the brain with gbrain](#11-exploring-the-brain-with-gbrain)
12. [File reference](#12-file-reference)
13. [Customizing](#13-customizing)
14. [Docker](#14-docker)
15. [Troubleshooting and limitations](#15-troubleshooting-and-limitations)

---

## 1. Quick start

```bash
cd docingest
./ingest.sh ingest samples/inputs          # ingest every sample document, then verify
open work/reports/latest.md                # read the verification report
```

Other common runs:

```bash
./ingest.sh ingest path/to/contract.pdf    # one file
./ingest.sh ingest path/to/folder          # every supported file under a folder (recursive)
./ingest.sh plan path/to/folder            # preview: which sections need AI, estimated cost
./ingest.sh ingest path/to/folder --llm    # rules + AI for low-confidence sections
./ingest.sh verify                         # re-verify everything ingested so far
```

The exit code is `0` when every file converted and every check passed. It is `1` when any file failed to convert or any verification check failed.

---

## 2. Installation and requirements

### What the pipeline needs

| Component | Version | Needed for | How to get it |
|---|---|---|---|
| Python | 3.12 or newer | everything | python.org, or the Docker image |
| Python packages | see `requirements.txt` | everything | `pip install -r requirements.txt` |
| gbrain | v0.60.281.0 | importing, linking, verification | `git clone` + `bun install` (below) |
| Bun | 1.4.3 | running gbrain | https://bun.sh |
| LibreOffice (`soffice`) | any recent | legacy `.doc` files only | OS package manager; included in the Docker image |
| `ANTHROPIC_API_KEY` | — | `--llm` / `--llm-all` only | Claude Console |

### Python dependencies

There are two dependency files:

- **`requirements.txt`** lists the direct dependencies, pinned. Use it for local installs.
- **`requirements.lock`** pins every transitive dependency for Linux and Python 3.12 (62 packages, identical on x86_64 and arm64). The Docker image installs from it, so builds are reproducible.

| Package | Used for |
|---|---|
| `anthropic` | AI extraction (`--llm`) |
| `beautifulsoup4`, `markdownify` | HTML → markdown |
| `mammoth`, `python-docx` | `.docx` → markdown, plus an independent text extraction for the conversion check |
| `pdfplumber` | PDF text, headings (by font size) and tables |
| `openpyxl` | `.xlsx` |
| `python-pptx` | `.pptx` |
| `extract-msg` | Outlook `.msg` |
| `mcp` | fast reads from `gbrain serve` during verification |
| `PyYAML` | `vocabulary.yaml` |

`requirements-dev.txt` adds `reportlab`, which only `samples/make_samples.py` needs to generate the sample PDF.

Both files were checked:

- A fresh Python 3.12 environment installed from `requirements.txt` alone ran `plan` and `convert` on every sample. Its output matched the project venv.
- Every package in `requirements.lock` has a Linux wheel for x86_64 and arm64, except `red-black-tree-mod`. That one is pure Python and pip builds it during install.

### Local setup (what this machine uses)

```bash
# Python environment (the project venv lives one level up, in ../.venv)
python3 -m venv ../.venv
../.venv/bin/pip install --prefer-binary -r requirements.txt

# Bun + gbrain
curl -fsSL https://bun.sh/install | bash
git clone --branch v0.60.281.0 https://github.com/garrytan/gbrain.git ../gbrain
(cd ../gbrain && bun install && bun link)        # puts `gbrain` in ~/.bun/bin

# Shell setup (already in ~/.zshrc on this machine)
export BUN_INSTALL="$HOME/.bun"
export PATH="$BUN_INSTALL/bin:$PATH"
export GBRAIN_HOME="$HOME/docingest-brain"
```

`./ingest.sh` creates the brain on first run if it doesn't exist (PGLite, embeddings off), so `gbrain init` is optional.

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `GBRAIN_HOME` | `./brain` (set by `ingest.sh`) | Brain directory; the database lives in `$GBRAIN_HOME/.gbrain` |
| `DOCINGEST_WORK` | `$GBRAIN_HOME/work` | Working directory for generated pages, manifests, state and reports |
| `ANTHROPIC_API_KEY` | unset | Needed only for `--llm` / `--llm-all` |
| `GBRAIN_BIN` | `gbrain` on `PATH`, then `~/.bun/bin/gbrain` | Override the gbrain executable |

The global options `--brain <dir>` and `--work <dir>` override the first two variables.

---

## 3. How the pipeline works

```
 input file or folder
        │  (one file at a time; email attachments are queued as their own documents)
        ▼
 1. convert      convert.py     any format → markdown + an independent plain-text extraction
 2. classify     classify.py    brd / prd / sow / email / transcript / chat / ... 
 3. extract      extract.py     sections, entity pages, shared entities, typed links (rules)
 4. route        confidence.py  per section: fully covered by rules, or low-confidence?
 5. AI (opt.)    llm.py         Claude reads only the low-confidence sections (--llm)
 6. write        writer.py      one markdown file per page, with frontmatter links
 7. import       gbrain.py      gbrain import --no-embed  (pages are chunked here)
        ▼
 after all files:
 8. shared pages  state.py      people / companies / systems / glossary merged across documents
 9. clean up      pipeline.py   soft-delete pages that no document produces any more
10. link          gbrain        gbrain extract links (frontmatter → typed graph edges)
11. verify        verify.py     every word, section, chunk, page and link checked → report
```

`pipeline.py` drives the whole run, and `cli.py` is the command line.

---

## 4. docingest commands

Run commands through `./ingest.sh <command>` locally, or `docker run … docingest <command>` in a container. Both run `python -m docingest <command>`.

### `ingest <path>`: convert, extract, import and verify

```bash
./ingest.sh ingest samples/inputs
./ingest.sh ingest samples/inputs/lighthouse_brd.md --kind brd
./ingest.sh ingest inbox/ --llm --llm-effort high
```

| Option | Effect |
|---|---|
| `--llm` | After rule extraction, send only low-confidence sections to Claude (`claude-opus-5-5`). Needs `ANTHROPIC_API_KEY` |
| `--llm-all` | Send every section to Claude, not only low-confidence ones |
| `--llm-effort low\|medium\|high\|xhigh\|max` | Claude effort level (default `medium`) |
| `--kind brd` | Force the document kind for the given files instead of detecting it |
| `--no-verify` | Skip verification after ingesting |
| `--search-checks N` | Search-check at most N sections per document (default `0` = all) |

### `plan <path>`: preview routing and AI cost, without touching the brain

```bash
./ingest.sh plan samples/inputs         # low-confidence sections only
./ingest.sh plan samples/inputs -v      # every section, with its rule coverage
```

`plan` converts and extracts in memory, then prints which sections the rules fully cover and which would go to Claude, with the reasons. It also estimates the tokens and cost of `--llm` and `--llm-all`. It needs no API key and writes nothing.

### `verify [key ...]`: re-run verification

```bash
./ingest.sh verify                      # every ingested document
./ingest.sh verify brd-lh-001 sow-lh-002
```

The report is written to `work/reports/latest.md` and `latest.json`, plus timestamped copies.

### `convert <file>`: print the markdown conversion

```bash
./ingest.sh convert samples/inputs/mobile_returns_prd.pdf > /tmp/prd.md
```

Warnings (for example "PDF has no text layer, needs OCR") and attachment names are printed to stderr.

### `pack`: print the generated gbrain schema pack

```bash
./ingest.sh pack > doc-pack.yaml
```

### `purge <slug-prefix>`: soft-delete pages under a prefix

```bash
./ingest.sh purge docs/documents/old-   # dry run: shows how many pages would go
./ingest.sh purge docs/documents/old- --yes
```

gbrain keeps soft-deleted pages recoverable for 72 hours.

### Global options

| Option | Meaning |
|---|---|
| `--brain <dir>` | Brain directory (instead of `GBRAIN_HOME`) |
| `--work <dir>` | Work directory (instead of `DOCINGEST_WORK` / `$GBRAIN_HOME/work`) |

Global options go before the command: `./ingest.sh --work /tmp/w ingest docs/`.

---

## 5. Supported input formats

| Extension | Converter | Notes |
|---|---|---|
| `.md`, `.markdown`, `.txt`, `.text`, `.log` | as is | Text with `Name: message` or `date - Name: message` lines is detected as chat or transcript |
| `.docx` | mammoth → markdown | Headings and tables kept |
| `.doc` | LibreOffice → `.docx` → mammoth | Needs `soffice` (in the Docker image) |
| `.pdf` | pdfplumber | Headings detected by font size and bold; ruled tables become markdown tables. Scanned PDFs (no text layer) are reported as needing OCR |
| `.html`, `.htm` | markdownify | |
| `.csv`, `.tsv`, `.xlsx`, `.xlsm` | table reader | Split into markdown tables of 100 rows; each sheet is a section |
| `.pptx` | python-pptx | One section per slide, with speaker notes |
| `.eml`, `.msg` | email parser | From / To / Cc become people; attachments are ingested as separate documents linked with `attachment_of` |
| `.vtt`, `.srt` | subtitle parser | Speakers (`<v Name>`) become participants |

Unsupported files in a folder are listed as skipped. Hidden folders, the brain directory and the work directory are always skipped.

**Document kinds** detected: `brd`, `prd`, `sow`, `contract`, `proposal`, `specification`, `meeting_notes`, `email`, `transcript`, `chat`, `spreadsheet`, `presentation`, or `document` when nothing matches. Detection uses the file format first, then keywords in headings and the opening text.

---

## 6. Extraction: rules first, AI where needed

### Rule-based extraction (always runs, free)

The rules turn structured content into entities and links:

| Source in the document | Result |
|---|---|
| Table with an ID column (`FR-001`, `RK-02`, `US-03`, `DEL-01`, …) | One entity page per row. The ID prefix picks the page type; otherwise the section heading does, otherwise `record`. Columns that hold IDs or names become links (`Business Requirements` → `implements`, `Owner` → `owned_by`, …); the other columns become attributes |
| `Field \| Value` table (document control) | Document attributes and links (`Author` → `authored_by`, `Sponsor` → `sponsored_by`, `Organization`, `Client`, `Vendor`, …) |
| Approval table (approver + decision columns) | `person —approved→ document`, with the decision details |
| Email headers | `sent_by`, `sent_to`, `copied_to` people, with their email addresses |
| Transcript and chat speakers | `has_participant` people |
| ID mentioned in any section text | `section —mentions_item→ entity` |

### Confidence routing (`confidence.py`)

After the rules run, every section is scored. The rules are trusted only for text they consumed. A section is **low-confidence** if anything else in it matches one of these:

- 20 or more words of prose or list text
- a shorter line with an extraction cue: *decided, owner, due, must, shall, risk, budget, depends on*, a question mark, …
- a table without an ID or field column (kept as text only)
- a long table cell (12 or more words) naming a known system, person, organization or glossary term that the row has no link to
- an ID that is referenced but not defined anywhere in the document

A section counts as high-confidence only when the rules can be shown to have consumed it. When in doubt, it is routed to the AI.

### AI extraction (`--llm`, optional)

| Mode | Sections sent | What is sent |
|---|---|---|
| no flag | none | — (low-confidence sections are listed in the report) |
| `--llm` | low-confidence only | Only the text the rules did not consume. Extracted tables become one-line placeholders, and flagged cells are included verbatim |
| `--llm-all` | every section ≥ 80 characters | Full section text |

How the AI step stays accurate and cheap:

- **Constrained output.** Claude's output must match a JSON schema whose types and verbs come from `vocabulary.yaml`.
- **Caching.** The document's rule-extracted items are sent once, in a cached block that every section reuses.
- **Validation.** Each AI entity and relationship must quote the original section **verbatim**, point at items that exist, and use a verb whose target type fits. Anything else is rejected and listed in the report.

On the samples, `--llm` sends 15 of 63 sections (about 65k tokens, about $0.35). `--llm-all` sends 63 (about 297k tokens, about $1.51). For the BRD alone it is 4 of 22 sections.

---

## 7. What ends up in the brain

For every input file:

| Page | Slug | Content |
|---|---|---|
| `source_document` | `docs/documents/<key>` | Metadata, outline, extracted-item counts; links to author, owner, sponsor, organization, client, vendor, email participants, transcript speakers |
| `document_section` | `docs/sections/<key>/<n>-<heading>` | One per section, `section_of` the document. **Joined in order, the sections reproduce the converted markdown exactly**, so every word is stored, chunked and searchable |
| entity pages | `docs/<type-dir>/<key>/<id>` | One per ID-table row (or AI-extracted item), `defined_in` the document |
| shared entities | `people/<name>`, `companies/<name>`, `docs/systems/<name>`, `docs/glossary/<term>` | One page per name across **all** documents, with `appears_in` links to each document and a section per document |

**`<key>`** is the document's own ID when it has a "Document ID" field (`brd-lh-001`), otherwise the file name (`ops-team-chat`). A second file with the same ID gets `-2`. An attachment's key is `<parent-key>-<file-name>`.

**Vocabulary.** There are 36 page types and 45 relationship verbs, all defined in `docingest/vocabulary.yaml`:

- **Types:**
  - requirements (business, functional, non-functional), use cases, epics, user stories, features
  - deliverables, acceptance criteria, service levels, payment terms
  - dependencies, assumptions, constraints, risks, issues
  - decisions, action items, open questions
  - KPIs, milestones, budget items
  - personas, people, organizations, systems, glossary terms
- **Verbs:** `implements`, `achieves`, `realizes`, `performed_by`, `integrates_with`, `threatens`, `mitigates`, `measures`, `delivers`, `follows`, `funds`, `verifies`, `belongs_to`, `blocks`, `depends_on`, `owned_by`, `assigned_to`, `approved`, `sent_by`, `has_participant`, and more.

Every verb declares an inverse (`implements` / `implemented_by`). Verbs with a fixed target type are type-checked. The vocabulary is turned into a gbrain schema pack (`doc-pack`) on every run.

---

## 8. Verification

Verification runs after every `ingest` (and on `verify`). Each check is `PASS`, `WARN`, `FAIL` or `INFO`; any `FAIL` makes the exit code `1`.

**Per document:**

| Check | What it proves |
|---|---|
| conversion | Every word of an independent text extraction of the source appears in the markdown. Any lost word is listed (WARN); losing more than 2% fails |
| sections | Section pages partition the markdown exactly: no gaps, overlaps or changed characters |
| tables | Every row of every ID table produced an entity; tables kept as text are counted |
| references | Every ID mentioned in the text is defined (WARN otherwise) |
| pages | Every expected page exists in the brain with the expected type |
| content | Stored text and attributes match what was written |
| chunks | gbrain's chunks cover every byte of every page, checked with each chunk's byte range |
| links | Every expected relationship edge exists (FAIL if missing, WARN on unexpected extras) |
| search | Each section is found by keyword search using a word unique to it |
| rule confidence | Lists low-confidence sections. Without `--llm` it is INFO ("not extracted, run with --llm"). With `--llm` it warns if any low-confidence section wasn't processed by the AI |
| ai extraction | With `--llm`: accepted and rejected AI items, and why each rejection happened |

**Global:**

| Check | What it proves |
|---|---|
| shared pages / content / chunks / links | The same checks for people, companies, systems and glossary pages |
| type coverage | No page in the brain is untyped or uses a type the active pack doesn't declare |
| embeddings | Reports the embedding count (0 by design) |
| stale pages | No pages remain from documents or items that no longer exist |

---

## 9. Sample input

`samples/inputs/` holds 11 documents about one fictional project (Project Lighthouse at Meridian Home Goods). They share people and systems, so cross-document merging can be checked. Regenerate them with `../.venv/bin/python samples/make_samples.py` (needs `requirements-dev.txt`).

| File | Format | What it contains | What it exercises |
|---|---|---|---|
| `lighthouse_brd.md` | Markdown | 21-section BRD `BRD-LH-001`: document control, objectives, scope, stakeholders, personas, business/functional/non-functional requirements, use cases, systems, dependencies, assumptions, constraints, risks, KPIs, milestones, budget, glossary, approvals | ID tables, cross-references, approval table, prose sections |
| `lighthouse_brd_copy.docx` | Word | The same BRD as `.docx` | Word conversion; a second copy of the same document ID (key `brd-lh-001-2`) |
| `mobile_returns_prd.pdf` | PDF | PRD `PRD-LH-003`: epics, user stories, acceptance criteria, open questions | PDF headings by font size, PDF tables |
| `portal_build_sow.html` | HTML | SOW `SOW-LH-002`: deliverables, payment terms, service levels, acceptance criteria, approvals | HTML tables, vendor/client fields |
| `brd_approval_email.eml` | Email | Approval email from Priya Raman, with `mobile_returns_risk_register.csv` attached | Email headers → people; attachment ingested as its own document |
| `steering_committee.vtt` | WebVTT | Steering committee transcript with 4 speakers | Transcript speakers; decisions in prose (low confidence) |
| `ops_team_chat.txt` | Text | WhatsApp-style chat; mentions `US-04` from the PRD | Chat detection; a cross-document ID reference (WARN) |
| `release0_retro_notes.txt` | Text | Retro notes in plain paragraphs, no headings | Segmenting text without headings; prose only |
| `vendor_risk_register.csv` | CSV | Risk register `RISK-01`, `RISK-02` with owners | Spreadsheet → entity pages |
| `service_policy_handbook.md` | Markdown | ~15,000-character policy text in one section | Multi-chunk pages (7 chunks) and byte-range chunk coverage |
| `legacy_spec.doc` | fake `.doc` | Not a real Word file | Fails conversion on purpose without LibreOffice: shows failure reporting and exit code 1 |

---

## 10. Sample output

### Console (`./ingest.sh ingest samples/inputs/lighthouse_brd.md`)

```
[docingest] 1 document(s) to ingest into /home/ubuntu/docingest-brain
[docingest] Schema pack doc-pack installed and active
[docingest] Processing lighthouse_brd.md
[docingest]   Rule confidence: 18 section(s) fully covered by rules, 4 low-confidence
[docingest]   brd: 22 sections, 110 pages, 26 shared entities; imported 0 (unchanged 110, errors 0), 0 chunks in 1.8s
[docingest] Shared entities: 27 pages (imported 0)
[docingest] Links: 1529 frontmatter links written; 1 body-text candidate(s) skipped (no such page)
[docingest] Verifying ingestion ...
[docingest] Verification: PASS — 1 document(s), 137 pages, 137 chunks, 746 links (6.3 s)
[docingest]   PASS brd-lh-001
[docingest] Report: .../docingest/work/reports/latest.md
```

### Results for all samples (`./ingest.sh ingest samples/inputs`)

| Input | Kind | Key | Sections | Pages | Chunks | Links | Low-confidence | Status | Main entities |
|---|---|---|---|---|---|---|---|---|---|
| lighthouse_brd.md | brd | brd-lh-001 | 22 | 110 | 110 | 645 | 4/22 | PASS | 13 functional req., 8 scope items, 8 use cases, 7 NFRs, … |
| lighthouse_brd_copy.docx | brd | brd-lh-001-2 | 22 | 110 | 110 | 645 | 4/22 | PASS | same as above |
| mobile_returns_prd.pdf | prd | prd-lh-003 | 7 | 18 | 18 | 47 | 1/7 | PASS | 4 user stories, 2 epics, 2 acceptance criteria, 2 open questions |
| portal_build_sow.html | sow | sow-lh-002 | 8 | 21 | 21 | 57 | 1/8 | PASS | 4 deliverables, 4 payment terms, 2 service levels, 2 acceptance criteria |
| brd_approval_email.eml | email | brd-approval-email | 1 | 2 | 2 | 5 | 1/1 | PASS | sender and recipients (shared people) |
| ↳ mobile_returns_risk_register.csv | spreadsheet | brd-approval-email-mobile-returns-risk-register | 1 | 5 | 5 | 11 | 0/1 | PASS | 3 risks |
| steering_committee.vtt | transcript | steering-committee | 1 | 2 | 2 | 5 | 1/1 | PASS | 4 participants (shared people) |
| ops_team_chat.txt | chat | ops-team-chat | 1 | 2 | 2 | 4 | 1/1 | WARN | 3 participants; `US-04` is defined in the PRD |
| release0_retro_notes.txt | document | release0-retro-notes | 1 | 2 | 2 | 1 | 1/1 | PASS | prose only |
| service_policy_handbook.md | document | service-policy-handbook | 1 | 2 | 7 | 1 | 1/1 | PASS | prose only, 7 chunks |
| vendor_risk_register.csv | spreadsheet | vendor-risk-register | 1 | 4 | 4 | 7 | 0/1 | PASS | 2 risks |
| legacy_spec.doc | — | — | — | — | — | — | — | failed | conversion error (no LibreOffice) |

Totals: 11 documents verified, 305 pages, 310 chunks, 1,529 links, 0 embeddings. There are 27 shared pages: 10 people, 2 companies, 7 systems, 8 glossary terms. Re-running is idempotent: unchanged pages are skipped.

### Work directory (`work/`)

```
work/
  doc-pack.yaml                 generated gbrain schema pack (from vocabulary.yaml)
  state.json                    documents, their pages, and contributions to shared entities
  markdown/<key>.md             converted markdown of each document
  reference/<key>.txt           independent text extraction (for the conversion check)
  manifests/<key>.json          everything extracted: sections, tables, routing, pages, AI report
  manifests/_shared.json        the merged shared-entity pages
  pages/<key>/…/*.md            pages exactly as imported into gbrain
  pages/_shared/…               shared entity pages
  attachments/<key>/            files extracted from emails
  reports/latest.md|json        latest verification report (+ verify-<timestamp>.md|json)
  logs/gbrain-cli.log           every gbrain command run, with exit codes and stderr
  logs/gbrain-serve.log         the MCP session used for verification reads
```

### An entity page (`docs/functional-requirements/brd-lh-001/fr-003`)

```markdown
---
type: functional_requirement
title: 'FR-003: Send email and SMS notifications when order status changes'
doc_key: brd-lh-001
item_id: FR-003
section: 10. 9. Functional Requirements
priority: Medium
owned_by:        [people/daniel-okafor]
uses_term:       [docs/glossary/order-status]
defined_in:      [docs/documents/brd-lh-001]
implements:      [docs/business-requirements/brd-lh-001/br-01]
integrates_with: [docs/systems/orderhub-oms]
---

# FR-003: Send email and SMS notifications when order status changes

From Business Requirements Document: Project Lighthouse — Customer Self-Service Portal (brd-lh-001), section 10: 9. Functional Requirements.

- **Priority:** Medium
- **Business Requirements:** BR-01 (Customers can see the real-time status of every order without contacting support)
- **Owner:** SH-02 (Daniel Okafor)
- **Systems:** SYS-01 (OrderHub OMS)
```

The links are shown in compact form here. gbrain stores them as block lists and turns each into a typed graph edge.

### A section page (frontmatter of `docs/sections/brd-lh-001/17-16-risks`)

```yaml
type: "document_section"
heading: "16. Risks"
section_index: 17
char_start: 13701            # exact position in work/markdown/brd-lh-001.md
char_end: 14771
section_of:    ["docs/documents/brd-lh-001"]
mentions_item: ["docs/risks/brd-lh-001/rk-01", "people/tom-brennan", "docs/objectives/brd-lh-001/bo-02", ...]
```

### Graph traversal from a use case

```
$ gbrain graph-query docs/use-cases/brd-lh-001/uc-01 --depth 2 --direction out
[depth 0] docs/use-cases/brd-lh-001/uc-01
  --defined_in-> docs/documents/brd-lh-001 (depth 1)
    --organization-> companies/meridian-home-goods (depth 2)
    --sponsored_by-> people/priya-raman (depth 2)
  --realizes-> docs/functional-requirements/brd-lh-001/fr-001 (depth 1)
    --implements-> docs/business-requirements/brd-lh-001/br-01 (depth 2)
    --uses_term-> docs/glossary/order-status (depth 2)
```

### Verification report excerpt (`work/reports/latest.md`)

```
| Check           | Result | Detail                                                  |
|-----------------|--------|---------------------------------------------------------|
| conversion      | PASS   | 100.00% of source words present in markdown             |
| sections        | PASS   | 22 sections cover 18292 chars exactly                   |
| references      | PASS   | All 112 defined IDs resolve; no dangling references     |
| pages           | PASS   | 110/110 pages present with expected types               |
| content         | PASS   | Stored text and attributes match what was written       |
| chunks          | PASS   | 110 chunks cover every byte of 110 pages                |
| links           | PASS   | 645/645 expected edges present                          |
| search          | PASS   | 21/21 sections found by keyword search                  |
| rule confidence | INFO   | 4 of 22 sections hold content rules cannot extract ...  |

rule confidence (4):
- section 2 (1. Document Control): 67 words of prose or list text the rules do not parse; 1 table(s) without ID or field columns (5 rows) left as text
- section 3 (2. Executive Summary): 111 words of prose or list text the rules do not parse
- section 4 (3. Business Background and Problem Statement): 131 words of prose or list text the rules do not parse
- section 20 (19. Budget): short text with extraction cues (budget)
```

---

## 11. Exploring the brain with gbrain

Set up the shell first. This is already in `~/.zshrc` on this machine:

```bash
export PATH="$HOME/.bun/bin:$PATH"
export GBRAIN_HOME="$HOME/docingest-brain"
```

`gbrain <command> --help` shows every option.

Every command below was run against this brain. `gbrain call <operation> '<json>'` calls a gbrain operation directly and returns JSON; use it for things the plain commands don't show, such as chunks.

### Overview

```bash
gbrain stats                         # pages, chunks, links, embedded count, pages per type
gbrain health                        # health dashboard (score is lowered by 0% embedding coverage, which is intended)
gbrain doctor --fast                 # installation health check
```

### Schema (types and verbs)

```bash
gbrain schema active                 # active pack: doc-pack v1.0.0, number of types and verbs
gbrain schema show                   # the full active pack (types, prefixes, link verbs)
gbrain schema stats                  # pages per type and typed coverage (should be 100%)
gbrain schema explain risk           # one type: primitive, slug prefix, extractable, aliases
gbrain schema graph                  # types and the verbs that connect them
gbrain schema list                   # installed packs
gbrain schema lint doc-pack          # check the pack for problems
./ingest.sh pack | less              # the pack as generated from vocabulary.yaml
```

### Pages

```bash
gbrain list --limit 20                                  # most recently updated pages
gbrain list --type functional_requirement --limit 50    # all pages of one type
gbrain list --type person                               # shared people
gbrain list --type source_document                      # one page per ingested file
gbrain get docs/documents/brd-lh-001                    # document page: attributes, outline, item counts
gbrain get docs/functional-requirements/brd-lh-001/fr-003
gbrain get people/priya-raman                           # merged across every document
gbrain call get_page '{"slug":"docs/risks/brd-lh-001/rk-02"}'   # JSON: compiled_truth, timeline, frontmatter
gbrain history docs/risks/brd-lh-001/rk-02              # stored versions of a page
```

### Chunks

```bash
# Chunks of one page, with text and byte ranges (projected_byte_start / _end)
gbrain call get_chunks '{"slug":"docs/sections/service-policy-handbook/1-customer-service-policy-handbook"}'

# Page through a long page
gbrain call get_chunks '{"slug":"docs/sections/service-policy-handbook/1-customer-service-policy-handbook","from_index":2,"limit":2}'

# Just the chunk numbers and sizes (needs jq)
gbrain call get_chunks '{"slug":"docs/sections/brd-lh-001/10-9-functional-requirements"}' \
  | jq '.[] | {chunk_index, chunk_source, bytes: (.projected_byte_end - .projected_byte_start)}'
```

`embedded_at` is `null` on every chunk because embeddings are off.

### Links and the graph

```bash
gbrain call get_links '{"slug":"docs/functional-requirements/brd-lh-001/fr-003"}'   # outgoing edges
gbrain backlinks docs/functional-requirements/brd-lh-001/fr-003                     # incoming edges
gbrain graph-query docs/use-cases/brd-lh-001/uc-01 --depth 3 --direction out        # UC → FR → BR → objective
gbrain graph-query docs/objectives/brd-lh-001/bo-01 --direction in --depth 1        # what achieves / measures / threatens it
gbrain graph-query people/priya-raman --direction both --depth 1                    # every document and item for a person
gbrain graph-query docs/documents/brd-lh-001 --type defined_in --direction in       # every item defined in a document
gbrain graph docs/systems/orderhub-oms --depth 2                                    # neighborhood of a system
gbrain link-sources                                                                 # edge counts by provenance
gbrain orphans --count                                                              # pages with no inbound links
```

### Search (keyword only, because embeddings are off)

```bash
gbrain search "prepaid return label"                          # top matches across all pages
gbrain search "OrderHub" --types '["functional_requirement"]' # restrict to page types
gbrain search "refund" --types '["risk","decision"]' --limit 5
gbrain search "escalation" --types '["document_section"]'     # find where in the documents a word appears
gbrain search "RK-02" --match keyword                         # every page matching, paged
gbrain call search '{"query":"carrier QR","types":["user_story"],"limit":5}'   # JSON results with chunk text
```

### Timeline, export and deletion

```bash
gbrain timeline docs/documents/brd-lh-001       # dated entries, if a page has any
gbrain export --dir /tmp/brain-export            # markdown snapshot of the whole brain
gbrain export --dir /tmp/risks --type risk       # only one type
gbrain delete <slug>                             # soft delete (recoverable for 72 h); prefer ./ingest.sh purge
```

### Use the brain from an agent (MCP)

```bash
gbrain serve --surface full                      # MCP server over stdio, every operation
gbrain serve --access read-only                  # read-only tools only
claude mcp add gbrain -- ~/.bun/bin/gbrain serve --surface full   # register with Claude Code
```

### Why `gbrain stats` totals differ from the report

`gbrain stats` reports more chunks and links than the verification report (432 vs. 310 chunks on this brain). The likely cause is soft-deleted pages: the old `brd/` pages are still stored until gbrain purges them. `get_chunks` and the verification skip them; `gbrain stats` appears to count every row. The `UPGRADE_AVAILABLE` line at the top of gbrain output is gbrain's update notice and can be ignored. The pipeline is pinned to v0.60.281.0.

---

## 12. File reference

```
docingest/
  ingest.sh                 local runner
  Dockerfile                container image
  docker-entrypoint.sh      container entry point
  requirements.txt          direct Python dependencies
  requirements.lock         every Python dependency, pinned (container)
  requirements-dev.txt      + reportlab, for generating samples
  pyproject.toml            code style settings (ruff)
  .dockerignore
  README.md
  NEXT_STEPS.md             open items and plans (EC2 checks, embeddings, adaptive schema, …)
  docingest/                the Python package
    __init__.py  __main__.py  cli.py  pipeline.py
    convert.py  classify.py  extract.py  confidence.py  llm.py
    mdutil.py  model.py  vocab.py  vocabulary.yaml
    writer.py  state.py  gbrain.py  verify.py
  samples/
    make_samples.py         generates samples/inputs/
    inputs/                 the 11 sample documents
  work/                     generated at run time (not committed)
```

### Top level

| File | What it does |
|---|---|
| `setup.sh`, `run_tests.sh`, `COMMANDS.md` | One-time setup, the test runner (fake LiteLLM proxy), and the command cheat-sheet |
| `ingest.sh` | Local runner. Loads `.env.local`, puts Bun on `PATH`, defaults `GBRAIN_HOME` to `./brain` (work files go to `$GBRAIN_HOME/work`), then runs `python -m docingest` with `./.venv` (created by `setup.sh`). Passes all arguments through |
| `Dockerfile` | Builds one image with Python 3.12, Bun 1.4.3, gbrain v0.60.281.0 (cloned and installed with a frozen lockfile), LibreOffice Writer (for `.doc`) and the Python packages from `requirements.lock`. Runs as a non-root user, with `/data/brain` and `/data/work` as volumes. Default command: `ingest /input` |
| `docker-entrypoint.sh` | Creates the brain on first start (`gbrain init --pglite --no-embedding`) if `/data/brain` is empty, then runs `python -m docingest` with the container arguments |
| `requirements.txt` | Direct Python dependencies, pinned |
| `requirements.lock` | Every transitive dependency pinned for Linux and Python 3.12; used by the Docker build. Regenerate after editing `requirements.txt` (the command is at the top of the file) |
| `requirements-dev.txt` | `requirements.txt` plus `reportlab` for `samples/make_samples.py` |
| `.dockerignore` | Keeps `work/`, sample inputs and caches out of the image |
| `pyproject.toml` | Code style: PEP 8 checked with ruff, 99-character lines, docstrings on public modules, classes and functions |
| `NEXT_STEPS.md` | Open items and agreed plans: EC2 checks, Ubuntu, embeddings, adaptive schema, standard attributes, project grouping, gap analysis |

### The `docingest/` package

| File | What it does |
|---|---|
| `__init__.py` | Package version (`1.0.0`) |
| `__main__.py` | Lets the package run as `python -m docingest` |
| `cli.py` | Command line: `ingest`, `plan`, `verify`, `convert`, `pack`, `purge`, and the global `--brain` / `--work` options. Checks for an API key when `--llm` is used, prints the verification summary, and sets the exit code |
| `pipeline.py` | Runs a whole ingestion. Collects input files (recursively, skipping hidden, brain and work folders) and installs the schema pack. Then, for each file in turn: convert → classify → assign key → rule extraction → confidence routing → optional AI → save markdown, manifest and pages → `gbrain import`. Email attachments are queued as new documents. After all files: rebuilds shared entity pages, soft-deletes pages nothing produces any more, runs `gbrain extract links` and `schema sync`. Also writes the verification reports |
| `convert.py` | Converts each format to markdown, and produces an independent plain-text extraction used to prove no words were lost. Contains the converters for DOCX, DOC (via LibreOffice), PDF (headings by font size, table detection, OCR warning), HTML, CSV/TSV/XLSX, PPTX, EML/MSG (headers table, body, saved attachments) and VTT/SRT (speakers). Also detects chat and transcript text by its line patterns |
| `classify.py` | Picks the document kind: from the converter (email, transcript, chat, spreadsheet, presentation), otherwise by keyword score in headings and opening text against `document_kinds` in the vocabulary |
| `extract.py` | Rule-based extraction. Splits sections and parses tables. Registers every ID-table row, choosing its type by ID prefix, then section heading, then `record`. Builds entity pages with attributes and links from column names. Reads `Field \| Value` document-control tables, approval tables, email participants and transcript speakers. Creates section pages with `mentions_item` links, finds undefined ID references, and collects shared entities |
| `confidence.py` | Scores every section after rule extraction. Computes the residual: text the rules didn't consume, unmapped tables, long cells naming unlinked entities, undefined IDs. Marks the section `rules` (high confidence) or `ai` (low confidence) with reasons and rule coverage. Builds the reduced text sent to Claude and the token and cost estimates for `plan` |
| `llm.py` | Optional Claude extraction. Builds the system prompt and JSON schema from the vocabulary and sends low-confidence sections (or all, with `--llm-all`), with known items in a cached block. Handles refusals, `max_tokens`, API errors and retries. Validates every returned item (verbatim evidence, known endpoints, verb target type), then adds accepted items as pages, links or shared-entity facts. Records tokens, cost and rejections |
| `vocabulary.yaml` | **The single source of truth for the schema.** Defines page types (folder, gbrain primitive, document or global scope, ID prefixes, heading keywords, title columns, description), verbs (inverse, target type, column aliases, description), document-control field mappings and document-kind keywords. Edit this to extend the pipeline |
| `vocab.py` | Loads `vocabulary.yaml` and answers lookups: type for an ID prefix, type for a heading, verb for a column name. Generates the gbrain schema pack (`doc-pack`) with a frontmatter-link rule for every type × verb, in the block-style YAML gbrain requires |
| `mdutil.py` | Markdown helpers: slugs, word tokens, coverage, headings, the exact section splitter (by heading level, or by size for text without headings), pipe-table parser and table writer |
| `model.py` | Shared data classes: `Converted`, `Section`, `Table`, `Page`, `GlobalContribution`, `DocumentResult` |
| `writer.py` | Writes pages as markdown files with YAML frontmatter (scalars quoted, links as lists), one file per slug, replacing the document's previous output |
| `state.py` | `work/state.json`: which key each source file has, which pages each document produced (to find stale ones), and what each document contributed to each shared entity. Merges contributions into shared pages in a stable order, and finds shared entities no document mentions any more |
| `gbrain.py` | Talks to gbrain. `GBrain` runs CLI commands for writes (init, install and validate the pack, import, extract links, schema sync, delete, stats) and logs them to `work/logs/gbrain-cli.log`. `Reader` keeps one `gbrain serve` MCP session open for fast reads during verification (page, chunks, links, search) |
| `verify.py` | All checks in [Verification](#8-verification). Compares manifests against what gbrain actually stored, computes chunk byte-range coverage, and renders the markdown and JSON reports |

### Samples

| File | What it does |
|---|---|
| `samples/make_samples.py` | Writes the sample documents into `samples/inputs/`: the DOCX copy of the BRD, the PRD as PDF (needs `reportlab`), the SOW as HTML, the email with CSV attachment, the transcript, chat, retro notes, CSV register, long handbook and the fake `.doc` |
| `samples/inputs/` | The sample documents (see [Sample input](#9-sample-input)) |

---

## 13. Customizing

**Add a page type, verb, ID prefix or column name.** Edit `docingest/vocabulary.yaml` and re-run `ingest`; the schema pack is regenerated and re-installed. Examples:

```yaml
types:
  change_request:
    dir: docs/change-requests
    primitive: annotation
    scope: document
    id_prefixes: [CR, CHG]
    keywords: [change requests, change log]
    title_columns: [change, summary]
    description: A requested change to scope, schedule or cost

verbs:
  supersedes: {inverse: superseded_by, columns: [supersedes, replaces], description: Replaces this item}
```

- A new prefix makes rows like `CR-07` become `change_request` pages.
- A new column alias makes a column named `Replaces` produce `supersedes` links.
- Run `./ingest.sh plan <folder>` to see the effect before ingesting.

**Tune the confidence router.** The thresholds are at the top of `docingest/confidence.py`:

| Constant | Default | Meaning |
|---|---|---|
| `MIN_RESIDUAL_WORDS` | 20 | Prose words that always route a section to the AI |
| `MIN_CUE_WORDS` | 3 | Shorter text routes if it contains a cue word |
| `LONG_CELL_WORDS` | 12 | Table cells this long are checked for unlinked mentions |
| `CUE_RE` | — | The cue words |

Lower values route more sections: more accurate, more tokens.

**Add an input format.** Add a converter function in `convert.py` that returns markdown plus reference text, and register the extension in `SUPPORTED`.

**Re-running and updates.**
- Re-ingesting is safe. Unchanged pages are skipped and changed pages are updated.
- Pages a document no longer produces (for example a deleted requirement) are soft-deleted.
- Shared entities are rebuilt from every document that mentions them, and deleted when none do.
- To start over, delete `work/` and re-run. To remove everything from one document, use `./ingest.sh purge docs/…/<key>`.

---

**Code style.** The code follows PEP 8 with 99-character lines (the team limit PEP 8 allows) and PEP 257 docstrings, configured in `pyproject.toml`:

```bash
pip install ruff
ruff check .          # lint: style, unused code, likely bugs, missing docstrings
ruff format .         # format
```

## Running on a plain Linux or EC2 host (no Docker)

For a machine where gbrain is already installed and you want to run the pipeline directly:
`./setup.sh`, edit `.env.local`, `./ingest.sh doctor`, then `./ingest.sh ingest <folder> --embed`.
The full command list, including embeddings through a LiteLLM proxy, reading the reports and
fixing common errors, is in [COMMANDS.md](COMMANDS.md). Two things to know:

- The brain is created in `GBRAIN_HOME` (default `./brain`) and its work files in
  `$GBRAIN_HOME/work`. Other gbrain brains on the machine are never touched.
- The embedding model and width are fixed when a brain is created. `--embed` creates a new brain
  with `litellm:text-embedding-3-small` at 1536 dimensions; to change either, use a new folder.

## 14. Docker

The image contains everything the pipeline needs: Python 3.12 with the locked packages, Bun, gbrain v0.60.281.0 and LibreOffice.

```bash
docker build -t docingest .

# Ingest a folder; the brain and work files persist in named volumes
docker run --rm \
  -v "$PWD/inputs:/input:ro" \
  -v docingest-brain:/data/brain \
  -v docingest-work:/data/work \
  docingest ingest /input

# With AI extraction
docker run --rm -e ANTHROPIC_API_KEY \
  -v "$PWD/inputs:/input:ro" -v docingest-brain:/data/brain -v docingest-work:/data/work \
  docingest ingest /input --llm

# Other commands
docker run --rm -v "$PWD/inputs:/input:ro" docingest plan /input
docker run --rm -v docingest-brain:/data/brain -v docingest-work:/data/work docingest verify

# gbrain inside the container, against the same brain
docker run --rm -v docingest-brain:/data/brain --entrypoint gbrain docingest stats
```

| Path in container | Purpose |
|---|---|
| `/input` | Documents to ingest (mount read-only) |
| `/data/brain` | The brain (`GBRAIN_HOME`); created on first start |
| `/data/work` | Work files and reports (`DOCINGEST_WORK`) |

The container's exit code follows the same rule as locally, so it can gate a CI job or batch scheduler.

**Status:** Docker isn't installed on this machine, so the image has not been built yet. The pieces it relies on were checked separately:
- every locked package resolves and has Linux wheels for x86_64 and arm64;
- the package set runs the pipeline in a fresh Python 3.12 environment;
- gbrain v0.60.281.0 and Bun 1.4.3 are the versions used locally.

Build it once and run `ingest` on `samples/inputs` to confirm. Expect a `PASS`/`WARN` report with all 11 files, including `legacy_spec.doc`, which converts in the image because LibreOffice is installed.

---

## 15. Troubleshooting and limitations

| Symptom | Cause and fix |
|---|---|
| `gbrain: command not found` | Bun's bin folder isn't on `PATH`: `export PATH="$HOME/.bun/bin:$PATH"` (or open a new terminal; it's in `~/.zshrc`). The venv doesn't provide gbrain |
| `Legacy .doc needs LibreOffice` | Install LibreOffice (`brew install --cask libreoffice`), save the file as `.docx`, or use the Docker image |
| `conversion FAIL … needs OCR` | The PDF is a scan with no text layer. OCR it first (for example with `ocrmypdf`) |
| `--llm needs ANTHROPIC_API_KEY` | Export the key, or run without `--llm` (rules only) |
| `references WARN` | An ID is mentioned but defined in another document (for example `US-04` in the chat). Cross-document ID references are reported, not linked |
| `pip install` fails building `cryptography` on an Intel Mac | Use `pip install --prefer-binary -r requirements.txt`; recent `cryptography` releases have no Intel-macOS wheel |
| A check fails after editing pages by hand in gbrain | Verification compares the brain with what the pipeline wrote; re-run `ingest` to restore, or expect the difference |

Known limitations:

- **Prose without `--llm`.** Prose is stored, chunked and searchable, but entities in it (decisions in meeting notes, requirements written as paragraphs) are extracted only with `--llm`. The `rule confidence` check lists those sections.
- **AI path testing.** The AI path has been tested with a simulated client only. Run it once with a real key and compare `--llm` with `--llm-all` on a few documents to confirm the routing loses nothing.
- **Name matching.** Shared entities merge on exact names: "OrderHub" and "OrderHub OMS" become two pages.
- **Duplicate files.** Two files with the same document ID get separate keys (`brd-lh-001`, `brd-lh-001-2`), so their items are duplicated rather than merged.
- **PDF headings.** Headings are inferred from font size and bold text; unusual layouts may produce fewer sections. No text is lost, because the section split covers the whole text.
- **Embeddings.** Embeddings are off, so search is keyword-only. gbrain can enable them later (`gbrain embeddings enable …`) without re-ingesting.
