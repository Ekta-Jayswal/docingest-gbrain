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

You can pass one file or a whole folder; the files are processed one after another. Extraction is rule-based first. An optional AI pass (a model behind your LiteLLM proxy) reads only the sections the rules could not fully handle.

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
12. [Project layout](#12-project-layout)
13. [Customizing](#13-customizing)
14. [Docker](#14-docker)
15. [Troubleshooting and limitations](#15-troubleshooting-and-limitations)

---

## 1. Quick start

```bash
cd docingest
./ingest.sh ingest sample_documents          # ingest every sample document, then verify
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
| `LITELLM_BASE_URL`, `LITELLM_API_KEY` | — | `--llm` / `--llm-all` and embeddings | Your LiteLLM proxy, set in `.env.local` |

### Python dependencies

There are two dependency files:

- **`requirements.txt`** lists the direct dependencies, pinned. Use it for local installs.
- **`requirements.lock`** pins every transitive dependency for Linux and Python 3.12 (62 packages, identical on x86_64 and arm64). The Docker image installs from it, so builds are reproducible.

| Package | Used for |
|---|---|
| `beautifulsoup4`, `markdownify` | HTML → markdown |
| `mammoth`, `python-docx` | `.docx` → markdown, plus an independent text extraction for the conversion check |
| `pdfplumber` | PDF text, headings (by font size) and tables |
| `openpyxl` | `.xlsx` |
| `python-pptx` | `.pptx` |
| `extract-msg` | Outlook `.msg` |
| `mcp` | fast reads from `gbrain serve` during verification |
| `PyYAML` | `vocabulary.yaml` |

`requirements-dev.txt` adds `reportlab`, which only `tests/helpers/make_sample_documents.py` needs to generate the sample PDF.

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
| `LITELLM_BASE_URL`, `LITELLM_API_KEY` | unset | The LiteLLM proxy address (with `/v1`) and key; needed for `--llm` / `--llm-all` and embeddings |
| `LLM_MODEL` | `gpt-5-mini-ai` | Chat model name the proxy offers, for the AI steps |
| `GBRAIN_BIN` | `gbrain` on `PATH`, then `~/.bun/bin/gbrain` | Override the gbrain executable |

The global options `--brain <dir>` and `--work <dir>` override the first two variables.

---

## 3. How the pipeline works

```
 input file or folder
        │  (one file at a time; email attachments are queued as their own documents)
        ▼
 1. convert      ingestion/convert_to_markdown.py   any format → markdown + an independent plain-text extraction
 2. classify     ingestion/detect_document_kind.py      brd / prd / sow / email / transcript / chat / ... 
 3. extract      ingestion/extract_with_rules.py    sections, entity pages, shared entities, typed links (rules)
 4. route        ingestion/rule_confidence.py    per section: fully covered by rules, or low-confidence?
 5. AI (opt.)    ingestion/extract_with_ai.py      the model reads only the low-confidence sections (--llm)
 6. write        ingestion/write_pages.py           one markdown file per page, with frontmatter links
 7. import       utils/gbrain_client.py                     gbrain import --no-embed  (pages are chunked here)
        ▼
 after all files:
 8. shared pages ingestion/ingest_state.py          people / companies / systems / glossary merged across documents
 9. clean up     ingestion/pipeline.py                      soft-delete pages that no document produces any more
10. link         gbrain                                     gbrain extract links (frontmatter → typed graph edges)
11. verify       evaluation/verification.py                 every word, section, chunk, page and link checked → report
```

`ingestion/pipeline.py` drives the whole run, and `commands.py` is the command line. All paths are under `docingest/`.

---

## 4. docingest commands

Run commands through `./ingest.sh <command>` locally, or `docker run … docingest <command>` in a container. Both run `python -m docingest <command>`.

### `ingest <path>`: convert, extract, import and verify

```bash
./ingest.sh ingest sample_documents
./ingest.sh ingest sample_documents/lighthouse_brd.md --kind brd
./ingest.sh ingest inbox/ --llm --llm-effort high
```

| Option | Effect |
|---|---|
| `--llm` | After rule extraction, send only low-confidence sections to the model behind the LiteLLM proxy (`LLM_MODEL`, default `gpt-5-mini-ai`). Needs `LITELLM_BASE_URL` and `LITELLM_API_KEY` |
| `--llm-all` | Send every section to the model, not only low-confidence ones |
| `--llm-effort low\|medium\|high\|xhigh\|max` | Accepted for compatibility; the LiteLLM client does not use it |
| `--kind brd` | Force the document kind for the given files instead of detecting it |
| `--no-verify` | Skip verification after ingesting |
| `--search-checks N` | Search-check at most N sections per document (default `0` = all) |
| `--schema-mode auto\|review\|off` | Unknown ID prefixes and link columns: `auto` lets the rules apply clear ones and queues the rest for reviewers (default); `review` queues all; `off` ignores them. See `./ingest.sh review` and COMMANDS.md section 11 |
| `--pending-exit-code N` | Exit code when the run succeeded but proposals wait for a reviewer (default `2`; `0` ignores them) |

### `review ...`: settle schema gaps

```bash
./ingest.sh review list | show P-0007 | approve P-0007 | map P-0008 owned_by | reject P-0009
./ingest.sh review apply | revert P-0007 | log
```

Where the vocabulary does not know an ID prefix or link column, the document is still ingested
with the generic `record` type and `references` links, and a proposal is written to
`work/review/` (read `PENDING.md`). Rules apply clear cases, the optional AI pass suggests for
the rest, and a person decides what is left, with the evidence, a preview of the change, and
safeguards (two reviewers for new types, automatic rollback if verification fails, `revert`).
Approved additions live in `work/vocabulary.learned.yaml`. Full guide: COMMANDS.md section 11.

### `plan <path>`: preview routing and AI cost, without touching the brain

```bash
./ingest.sh plan sample_documents         # low-confidence sections only
./ingest.sh plan sample_documents -v      # every section, with its rule coverage
```

`plan` converts and extracts in memory, then prints which sections the rules fully cover and which would go to the model, with the reasons. It also estimates the tokens and cost of `--llm` and `--llm-all`. It needs no API key and writes nothing.

### `verify [key ...]`: re-run verification

```bash
./ingest.sh verify                      # every ingested document
./ingest.sh verify brd-lh-001 sow-lh-002
```

The report is written to `work/reports/latest.md` and `latest.json`, plus timestamped copies.

### `convert <file>`: print the markdown conversion

```bash
./ingest.sh convert sample_documents/mobile_returns_prd.pdf > /tmp/prd.md
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
| ID mentioned in any section text | `section —mentions→ entity` |

### Confidence routing (`rule_confidence.py`)

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

- **Constrained output.** The model's output must match a JSON schema whose types and verbs come from `vocabulary.yaml`.
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

**Vocabulary.** There are 36 page types and 45 relationship verbs, all defined in `docingest/vocabulary/vocabulary.yaml`:

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

`sample_documents/` holds 11 documents about one fictional project (Project Lighthouse at Meridian Home Goods). They share people and systems, so cross-document merging can be checked. Regenerate them with `../.venv/bin/python tests/helpers/make_sample_documents.py` (needs `requirements-dev.txt`).

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

### Console (`./ingest.sh ingest sample_documents/lighthouse_brd.md`)

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

### Results for all samples (`./ingest.sh ingest sample_documents`)

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
mentions: ["docs/risks/brd-lh-001/rk-01", "people/tom-brennan", "docs/objectives/brd-lh-001/bo-02", ...]
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

## 12. Project layout

Every folder and file in the repository, and what it is for. The run-time folders (`brain/`, and `work/` inside it) and the local settings file (`.env.local`) are not in git.

```
docingest/                                     the repository
├── setup.sh  ingest.sh  run_tests.sh          set up, run and test (see COMMANDS.md)
├── Dockerfile  docker-entrypoint.sh           container image and its entry point
├── requirements.txt  requirements.lock        Python dependencies; .lock pins every version for the image
├── requirements-dev.txt                       test-only extra (reportlab, for the PDF sample)
├── pyproject.toml                             code style (ruff)
├── .gitignore  .dockerignore                  what git and the image leave out
├── README.md  COMMANDS.md  NEXT_STEPS.md      this guide, the command guide, the plans
├── docs/
│   └── VOCABULARY.md                          every link verb and page type (generated from vocabulary.yaml)
├── docingest/                                 the application (python -m docingest)
│   ├── __init__.py  __main__.py               package and version number; the entry point
│   ├── commands.py                            every command and its options (--brain, --work, ...)
│   ├── ingestion/                             the pipeline: one file per step
│   │   ├── pipeline.py                        runs the steps below for each file, then the shared steps
│   │   ├── convert_to_markdown.py             1. any file format to markdown, plus a plain-text reference
│   │   ├── normalize_headings.py              1. bold lines that stand for headings become real headings
│   │   ├── detect_document_kind.py            2. BRD, PRD, SOW, email, transcript, ...
│   │   ├── extract_with_rules.py              3. sections, entities and links, by rules
│   │   ├── rule_confidence.py                 4. which sections the rules could not fully read
│   │   ├── extract_with_ai.py                 5. optional AI pass on those sections (--llm)
│   │   ├── write_pages.py                     6. page files with frontmatter, for gbrain to import
│   │   ├── ingest_state.py                    what each document produced (re-runs, shared pages)
│   │   ├── review_proposals.py                gaps the rules could not place -> proposals; decisions, apply, undo
│   │   ├── review_ai.py                       optional AI suggestions for those proposals (--llm)
│   │   └── litellm_proxy.py                   checks the LiteLLM proxy before embedding
│   ├── evaluation/                            is the result right?
│   │   ├── verification.py                    checks after every run: nothing lost, everything stored
│   │   ├── embedding_checks.py                checks after embedding (+ semantic_search_questions.yaml)
│   │   ├── answer_key_evaluation.py           `eval`: scores every stage against answer_keys/
│   │   └── selftest.py                        `selftest`: unit tests, and the answer-key scores against the baseline
│   ├── reporting/                             what happened, and for whom
│   │   ├── recap.py                           the summary printed at the end of each run
│   │   ├── run_history.py                     append-only record of every run, document and evaluation
│   │   └── dashboard_page.py                  the static run-history page
│   ├── vocabulary/                            page types and link verbs
│   │   ├── vocabulary.yaml                    the single source of truth for the schema
│   │   └── vocabulary_loader.py               loads it; builds the gbrain schema pack and docs/VOCABULARY.md
│   └── utils/                                 shared by everything above
│       ├── data_types.py                      Converted, Section, Table, Page, DocumentResult, ...
│       ├── markdown_parsing.py                slugs, tokens, sections, tables
│       └── gbrain_client.py                   gbrain commands and fast reads
├── answer_keys/                               the expected result for each sample document
│   ├── loan_origination_brd.md + .yaml        tuned documents: the document, then its key
│   ├── expense_capture_prd.md + .yaml
│   ├── field_service_brd.docx + .yaml
│   ├── data_migration_sow.html + .yaml
│   ├── baseline.json                          saved scores that `selftest --full` compares against
│   └── holdout/                               three held-out documents, for measuring only (see its README)
├── sample_documents/                          11 demo documents of one fictional project
├── tests/                                     automated tests: python -m unittest (./run_tests.sh)
│   ├── test_*.py                              one file per area (see the table below)
│   └── helpers/                               test support: a fake proxy and the document generators
└── brain/                                     created by the first run: the brain and its work/ folder
```

Only `docingest/`, `answer_keys/`, `sample_documents/`, `tests/` and `docs/` are folders in the repository. Code is never more than two levels deep. `brain/` appears when you run the pipeline with the default `GBRAIN_HOME`.

Imports always use the full path (`from docingest.ingestion.pipeline import Pipeline`), so a name tells you where the file is.

### The `docingest/` package, file by file

| File | What it does |
|---|---|
| `__init__.py` | Marks the package and holds its version number |
| `__main__.py` | Lets the package run as `python -m docingest` |
| `commands.py` | Every command (`ingest`, `review`, `plan`, `verify`, `convert`, `pack`, `vocab`, `purge`, `embed`, `embeddings-setup`, `doctor`, `eval`, `selftest`, `dashboard`) and the global `--brain` / `--work` options. Records each run in the run history and sets the exit code |
| `ingestion/pipeline.py` | Runs a whole ingestion. Applies approved reviews, then for each file: convert → classify → assign key → rule extraction → rule confidence → optional AI → save markdown, manifest and pages → `gbrain import`. Email attachments are queued as new documents. After all files: rebuilds shared entity pages, removes pages nothing produces any more, writes graph edges, applies the rules' clear schema decisions (asks the AI for suggestions on the rest), verifies, and embeds |
| `ingestion/convert_to_markdown.py` | Converts each format to markdown (DOCX, DOC, PDF, HTML, CSV/TSV/XLSX, PPTX, EML/MSG, VTT/SRT, text) plus an independent plain-text extraction used to prove no words were lost |
| `ingestion/normalize_headings.py` | Turns bold-only lines (common in Word files) into real headings, when a document has fewer than two real ones. Runs inside the conversion step |
| `ingestion/detect_document_kind.py` | Decides the document kind (BRD, PRD, SOW, email, transcript, ...) from the converter or keyword scores |
| `ingestion/extract_with_rules.py` | Rule-based extraction: sections, tables, ID-keyed rows as entities, attributes and links from column names, document-control fields, approvals, email participants, transcript speakers, section pages with `mentions` links |
| `ingestion/rule_confidence.py` | Scores each section after rule extraction and decides which go to the AI; token and cost estimates for `plan` |
| `ingestion/extract_with_ai.py` | Optional AI extraction (`--llm`) for low-confidence sections, constrained to the vocabulary, with verbatim-evidence checks |
| `ingestion/write_pages.py` | Writes pages as markdown files with YAML frontmatter |
| `ingestion/ingest_state.py` | `work/state.json`: document keys, the pages each document produced, contributions to shared entities |
| `ingestion/review_proposals.py` | Schema gaps (an unknown ID prefix, a column with no verb) become proposals. Runs the rule checks, decides who settles each one, records reviewer decisions, applies them and undoes them (`review` command). Keeps the audit trail in `work/review/` |
| `ingestion/review_ai.py` | Runs only with `--llm`. Asks the model for a suggestion on each proposal. A suggestion is kept only if it quotes the rows and passes the same checks as a reviewer's decision; it never applies itself |
| `ingestion/litellm_proxy.py` | Checks the LiteLLM proxy: lists models and embeds one word |
| `evaluation/verification.py` | Every check in [Verification](#8-verification); writes `reports/latest.md` and `.json` |
| `evaluation/embedding_checks.py` | The five embedding checks; questions in `semantic_search_questions.yaml` |
| `evaluation/answer_key_evaluation.py` | `eval`: ingests each answer key's document into a throwaway brain and scores every stage |
| `evaluation/selftest.py` | `selftest`: runs the unit tests (quick), or with `--full` every test plus the answer-key scores compared with `answer_keys/baseline.json` |
| `reporting/recap.py` | The summary printed at the end of each run: per document, its content, checks, rule and AI results, and latest evaluation |
| `reporting/run_history.py` | Appends runs, documents and evaluations to `work/run_history/*.jsonl` |
| `reporting/dashboard_page.py` | Builds `work/reports/dashboard.html` from the run history |
| `vocabulary/vocabulary.yaml` | **The single source of truth for the schema**: page types, link verbs, column aliases, document fields and kinds. Edit this to extend the pipeline |
| `vocabulary/vocabulary_loader.py` | Loads the YAML, answers lookups (type for a prefix, verb for a column), checks consistency, generates the gbrain schema pack and `docs/VOCABULARY.md` |
| `utils/data_types.py` | Shared data classes |
| `utils/markdown_parsing.py` | Slugs, word tokens, coverage, headings, the section splitter, the pipe-table parser and writer |
| `utils/gbrain_client.py` | `GBrain` runs gbrain commands (logged to `work/logs/gbrain-cli.log`); `Reader` keeps one `gbrain serve` session open for fast reads |

### Tests, helpers, samples and answer keys

| Path | What it holds |
|---|---|
| `tests/test_markdown_parsing.py` | The word tokenizer, and turning bold lines into headings |
| `tests/test_vocabulary.py` | The vocabulary is consistent, `docs/VOCABULARY.md` is current, and verbs are chosen by type |
| `tests/test_list_extraction.py` | List items become entities; which lists the rules cover, and which go to the AI |
| `tests/test_table_extraction.py` | Tables without IDs; cover, change-history and signature tables; document titles |
| `tests/test_pdf_conversion.py` | PDF list markers and wrapped items, and that no words are lost (needs `reportlab`) |
| `tests/test_embeddings.py` | The embedding setup against the fake proxy; the full ingest needs gbrain |
| `tests/test_evaluation_and_run_history.py` | The scoring helpers, the loan BRD baseline, and the run history and dashboard (the full run needs gbrain) |
| `tests/test_selftest.py` | What counts as a regression against the baseline |
| `tests/test_recap.py` | The text of the end-of-run recap |
| `tests/test_review.py` | Gaps, proposals, decision safeguards, AI suggestions (with a fake client), applying and undoing (the full run needs gbrain) |
| `tests/helpers/fake_litellm_proxy.py` | A stand-in LiteLLM proxy, so embedding tests need no keys or network. Can also be run by hand on a port |
| `tests/helpers/fake_proxy_search_questions.yaml` | Search questions the fake proxy can answer |
| `tests/helpers/make_sample_documents.py` | Regenerates `sample_documents/` (DOCX, PDF, HTML, email, subtitles, chat, CSV, handbook and the legacy `.doc`) |
| `tests/helpers/make_field_service_brd.py` | Regenerates `answer_keys/field_service_brd.docx` |
| `tests/helpers/make_holdout_documents.py` | Regenerates the two held-out Word and PDF documents in `answer_keys/holdout/` |
| `sample_documents/` | 11 demo documents (see [Sample input](#9-sample-input)): the BRD as markdown and as DOCX, the PRD as PDF, the SOW as HTML, an approval email, the steering-committee subtitles, a team chat, retro notes, a risk register (CSV), a long policy handbook, and a legacy `.doc` (needs LibreOffice) |
| `answer_keys/` | Each `.yaml` is the result the pipeline should produce for the document beside it: its sections, tables, entities and links. `baseline.json` holds the saved scores |
| `answer_keys/holdout/` | Held-out documents and keys, used only to measure. The rules for them are in its [README](answer_keys/holdout/README.md) |
| `docs/VOCABULARY.md` | The generated reference of every link verb and page type |

---

## 13. Customizing

**Add a page type, verb, ID prefix or column name.** Edit `docingest/vocabulary/vocabulary.yaml` and re-run `ingest`; the schema pack is regenerated and re-installed. Examples:

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

**Add an input format.** Add a converter function in `docingest/ingestion/convert_to_markdown.py` that returns markdown plus reference text, and register the extension in `SUPPORTED`.

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

## Run history, dashboard and evaluation

- Every run is recorded in `$GBRAIN_HOME/work/run_history/` (`runs.jsonl`, `documents.jsonl`): project (`--project`), team, who, input path, and for each document its tables, pages, chunks, links and checks.
- `$GBRAIN_HOME/work/reports/dashboard.html` is rebuilt after each run: overall totals and one entry per document.
- `python -m docingest eval` scores the pipeline against the answer keys in `answer_keys/` (precision, recall, F1 and accuracy for every stage) and writes `reports/eval-latest.md`.
- `python -m docingest vocab` prints every verb and page type; `docs/VOCABULARY.md` is generated from it.

See [COMMANDS.md](COMMANDS.md) sections 6 and 7.

## Logs

Every line the tool prints has the same shape, so you can tell which run, document and code path it came from:

```
2026-10-22 20:13:04 IST INFO  [run=ab12cd doc=loan_origination_brd] docingest.ingestion.pipeline (pipeline.py:212) processing | file=loan_origination_brd.md
```

- **Time** is always India time (IST), whatever the machine's clock says.
- **`run`** is the run's ID. Every line from one command has the same value, and it appears in the run history and the recap.
- **`doc`** is the document key, or `-` outside a document.
- **Module, file and line** show where the message came from.
- **Colors** (on a terminal only): green is a key step that finished, yellow a warning, red an error. Other lines are plain.
- **Attributes** follow the message as `key=value` pairs, for example `secs=41.2`.

Each run writes a full log, including the debug detail that the console leaves out:

- `work/logs/runs/<run_id>.log`: that run only. The dashboard's Runs table links to it.
- `work/logs/docingest.log`: all runs, rotated at 5 MB.

Settings (in `.env.local` or the environment):

| Setting | What it does | Default |
|---|---|---|
| `DOCINGEST_LOG_LEVEL` | Console level: `DEBUG`, `INFO`, `WARNING` or `ERROR` | `INFO` |
| `DOCINGEST_COLOR` | `always`, `never` or `auto` (color only on a terminal) | `auto` |
| `NO_COLOR` | Set to any value to turn color off | unset |
| `LLM_TIMEOUT` | Seconds to wait for each model reply. Printed before each call | `300` |

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
docker run --rm -e LITELLM_BASE_URL -e LITELLM_API_KEY \
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

Build it once and run `ingest` on `sample_documents` to confirm. Expect a `PASS`/`WARN` report with all 11 files, including `legacy_spec.doc`, which converts in the image because LibreOffice is installed.

---

## 15. Troubleshooting and limitations

| Symptom | Cause and fix |
|---|---|
| `gbrain: command not found` | Bun's bin folder isn't on `PATH`: `export PATH="$HOME/.bun/bin:$PATH"` (or open a new terminal; it's in `~/.zshrc`). The venv doesn't provide gbrain |
| `Legacy .doc needs LibreOffice` | Install LibreOffice (`brew install --cask libreoffice`), save the file as `.docx`, or use the Docker image |
| `conversion FAIL … needs OCR` | The PDF is a scan with no text layer. OCR it first (for example with `ocrmypdf`) |
| `--llm` says `LITELLM_BASE_URL and LITELLM_API_KEY must be set` | Put both in `.env.local` (no leading `#`) and run through `./ingest.sh`, or run without `--llm` (rules only) |
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
