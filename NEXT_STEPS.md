# Next steps

## Learnings for the next step (2026-10-25)

- Grey lines in the gap audit graph are section mentions: the section names the item, by the item's ID in its text or by defining it there. They are not LLM expectations.
- Rules closed most of the link gaps: numbered objectives, ID ranges, sub-sections, "Question or risk" tables, heading-typed tables, glossary terms, system aliases, inherited objectives.
- The LLM link pass is untested against a real model. Its accepted links are logged with quotes. Turn a pattern into a rule only if it recurs across documents and holds on the held-out set.
- Still open without the LLM: NFR, KPI and risk items with no objective or system column; scope items and non-goal bullets; SOW task sub-headings; one stakeholder row.
- Next: run `./ingest.sh gap-audit ... --llm` on the loan BRD, group the accepted links by pattern, then decide which patterns become rules.

## Link gaps (2026-10-25, branch `fix/section-entity-links`)

Audit of three unseen BRD/PRD files (permit portal BRD, pharmacy refills PRD, mobile returns PRD), section by section. Closed by rules: sections now link to every item defined in them (items from tables and lists have no printed ID), numbered objectives ("1, 2"), ID ranges ("US-1 to US-4"), `Question or risk` tables read row by row, sub-sections (`subsection_of`) with container sections naming their sub-sections' items, tables typed by the heading above them (new `data_requirement` type; cost-benefit; "Current Process" gives steps), `uses_term` from glossary terms, objectives inherited through `implements`, system aliases (acronym, name without a closing "service/system"). Typed links in the permit BRD went from 15 to 49.

Still open: scope items, business rules, most non-functional requirements, budget items, assumptions, and goals that no story names have no typed link, because the document never connects them with an ID or a column. The section pass of `--llm` cannot see across sections, so `extract_with_ai.link_pass` (new, one request per document, every link needs a quote from each end) is meant for these. **Not yet run against a real model** (no proxy credentials on this machine); the fake-model tests pass. First check on EC2: `./ingest.sh ingest <file> --llm`, then compare `link_items`, `links_accepted` and `links_rejected` in the manifest's `llm_report`.

## Status (2026-10-21)

**Built on branch `feat/ec2-standalone`** (run on a plain EC2 host, no Docker, no AI assistant): `setup.sh`, a repo-relative `ingest.sh`, `doctor`, the embedding phase (`ingest --embed`, `embed`, `embeddings-setup`, five embedding checks), a fake LiteLLM proxy with tests (`run_tests.sh`) and [COMMANDS.md](COMMANDS.md). Tested locally against the fake proxy only. **The first real run is on EC2**: paste the output of `./ingest.sh doctor` and the two reports if anything fails.

What testing taught us (changes to the plan below):

- **The embedding width is fixed when the brain is created.** `gbrain init --no-embedding` makes a 1024-wide column, so `embeddings enable` at 1536 is refused. `--embed` therefore creates the brain with `--embedding-model` and `--embedding-dimensions`. A brain made without embeddings cannot be switched: gbrain's `migrate embeddings` refuses models it has no price for, which includes every LiteLLM model. Use a new brain folder.
- **`gbrain embed --max-usd` does not work for LiteLLM models** (no price entry: it stops with `cost_cap`, `no_pricing`, embedding nothing). The pipeline uses `--yes` and limits the chunk count instead (`--max-chunks`, default 20000).
- With embeddings enabled, the verification step's `gbrain serve` session may embed chunks on its own, so the `embed` step often finds nothing pending. That is fine.
- The work folder now lives beside the brain (`$GBRAIN_HOME/work`). Before, an old `state.json` paired with a new brain produced a false "shared links" failure.

Not yet known (check on EC2, see section 1): the proxy's exact model name, whether the URL needs `/v1`, and that a `.doc` file needs LibreOffice. `doctor` reports all of these.

**Built on branch `feat/vocabulary-v2` (2026-10-21):** vocabulary v2 (48 verbs, 37 page types, `docs/VOCABULARY.md`), the run history and dashboard (`--project`, `--team`, `--submitted-by`), and `docingest eval` with the first answer key (`answer_keys/loan_origination_brd.yaml`). Baseline on that BRD: overall 58%; entities precision 100%, recall 38%; links F1 0%; sections F1 0%; attributes 95%; retrieval 67%. After heading normalization (`feat/heading-normalization`): overall 72%. After tables without IDs (`feat/tables-without-ids`): overall 84%; tables 100%, entities F1 90%, retrieval 100%. Four answer keys now. After links from types (`feat/links-from-types`): loan BRD 95%, Word BRD 100%, PRD 95%, SOW 84%; mean 93.5%. Still to build: IDs inside cells, tables without IDs and system links, and must raise these numbers; `tests/test_eval_and_ledger.py` holds the floors.

The sections below are the plan as of 2026-10-17. Sections 1–4 are now mostly done or replaced by the status above.

## 1. Checks to run on the EC2 instance

```bash
uname -m                      # CPU: x86_64 = Intel/AMD, aarch64 = Graviton (ARM)
cat /etc/os-release           # host OS and version
docker --version              # Docker installed?
docker info | grep -i -E "architecture|operating system"
```

LiteLLM proxy (credentials exist only on EC2; use `export`, not a `.env` file, because gbrain ignores base URLs from a `.env` in the working folder):

```bash
export LITELLM_BASE_URL=https://<proxy>/v1
export LITELLM_API_KEY=<key>

# Model names the proxy offers. A 404 means try the URL without /v1.
curl -s "$LITELLM_BASE_URL/models" -H "Authorization: Bearer $LITELLM_API_KEY"

# One real embedding at 1536 dimensions. Should print 1536.
curl -s "$LITELLM_BASE_URL/embeddings" \
  -H "Authorization: Bearer $LITELLM_API_KEY" -H "Content-Type: application/json" \
  -d '{"model":"litellm:text-embed-3S-ai","input":"hello","dimensions":1536}' \
  | python3 -c "import json,sys; print(len(json.load(sys.stdin)['data'][0]['embedding']))"
```

Record the answers here:

| Question | Answer |
|---|---|
| CPU (`uname -m`) | |
| Host OS version | |
| Docker installed / version | |
| Exact embedding model name on the proxy | |
| Does the proxy need `/v1` in the base URL? | |
| Must the image be Ubuntu-based (team rule), or only the host? | |

## 2. Ubuntu: changes depending on the answer

**Ubuntu host, current Debian-based image:** no code changes. The host needs Docker. The image must be built on (or for) the instance's CPU type; all components support x86_64 and arm64. The container user has ID 1000, the same as EC2's default `ubuntu` user, so mounted folders have matching permissions.

**Image must be Ubuntu-based.** Change the Dockerfile:

| Change | Reason |
|---|---|
| `FROM ubuntu:24.04` | Ships Python 3.12; 22.04 ships 3.10 |
| Install Python packages into `/opt/venv` and put it on `PATH` | Ubuntu 24.04 refuses `pip install` into the system Python |
| `apt-get install python3 python3-venv git ca-certificates libreoffice-writer-nogui` | Not in the plain Ubuntu image; confirm the LibreOffice package name on the first build |
| Drop `useradd --uid 1000 app`; use the existing `ubuntu` user | ID 1000 already exists in Ubuntu images, so the current line fails |
| `ENV LANG=C.UTF-8` | Ubuntu images set no language; LibreOffice and others expect UTF-8 |

**Running on Ubuntu without Docker:** install `python3.12-venv`, Bun 1.4.3 and LibreOffice, then follow the README setup. `ingest.sh` expects `../.venv` and `~/.bun`.

In every case, the image has never been built. The first `docker build` on EC2 is its first test.

## 3. Embeddings: decided settings

- Provider: LiteLLM proxy, gbrain's built-in `litellm` provider
- Model: `litellm:text-embed-3S-ai`, 1536 dimensions
- If the proxy names the model `litellm:text-embed-3S-ai`, gbrain sends the width. If it uses a custom alias, declare the native width (1536 for small).

One-time setup on the brain:

```bash
gbrain embeddings enable --embedding-model litellm:text-embed-3S-ai --embedding-dimensions 1536
```

Each run:

```bash
gbrain embed --stale --max-usd 1      # without a terminal, gbrain needs a spending cap or --yes
gbrain query "customers who don't own a printer"   # hybrid search
```

Constraints:

- The model is fixed per brain; switching later re-embeds everything (`gbrain migrate embeddings`).
- `enable` and `embed` need gbrain's write lock, so no `gbrain serve` may be running. Run them after verification closes its read session.
- In Docker, pass the credentials with `-e LITELLM_BASE_URL -e LITELLM_API_KEY`.

## 4. Embedding phase to build

| Item | What |
|---|---|
| `ingest --embed` | After verification passes, run `gbrain embed --stale` with a cost cap, then the embedding checks. Skip embedding if verification failed |
| `docingest embed` | Embedding plus checks on their own, for re-runs |
| `docingest embeddings-setup --model … --dimensions …` | Wraps `gbrain embeddings enable` after checking the proxy responds |
| Clear error | `--embed` on a brain without embeddings enabled stops with the setup command |
| Docker and README | `-e` variables, an embeddings section |

Embedding verification:

1. **Coverage:** every live chunk has a vector from the expected model (`embedded_at`, `model`, `embedding_is_null` from `get_chunks`). FAIL if any is missing.
2. **Provider health:** `gbrain doctor --json` reports the width matches, the provider responds, and 0 chunks are pending.
3. **Self-retrieval:** for a sample of chunks, a query with the chunk's own text returns its page first.
4. **Semantic test:** a few queries worded differently from the documents (no shared keywords) find the right pages, where keyword search does not.
5. **Hybrid test:** `gbrain query` returns results with semantic scores.

Test plan: build and test against a fake proxy locally. The first real run is on EC2.

## 5. Other open items

- **Real Claude run:** run `--llm` once with a real `ANTHROPIC_API_KEY`, and compare `--llm` with `--llm-all` on a few documents to confirm the confidence routing loses nothing.
- **Schema evolution (adaptive schema for new section types and relationships).** Decisions so far (2026-10-17):
  - **Reviewers:** several people: the user who invoked the run, or admins/moderators who know the service.
  - **Review channels to build first:** proposal files on the volume, `schema proposals / approve / reject` commands run as one-off containers, and decision files the next run applies. Exit code 2 = ingested with decisions pending (configurable).
  - **Later:** email notification (counts and IDs only, no document text), then Teams. A review web page is undecided, because it isn't known whether reviewers can reach EC2 from a browser.

## 5b. Review system (built on `feat/review-system`, 2026-10-22)

Gap detection, proposals, rule auto-approval, optional AI suggestions, reviewer commands, safeguards (refused-at-once checks, preview, two reviewers for risky decisions, rollback, revert, audit log) and the `schema gaps` / `schema review` verification rows. Guide: COMMANDS.md section 11; code in `ingestion/review_proposals.py` and `ingestion/review_ai.py`.

Not built from section 6, on purpose for now:

- **New verbs.** A column that fits no existing verb stays generic; a reviewer can map it to an existing verb, or reject. Proposing new verbs needs an inverse name and a from/to decision.
- **Gaps for ID-less entity-like tables** and for people or system names in unmapped columns.
- **Answer-key scores as a rollback guard.** Applied decisions only add to the vocabulary, so the guard runs verification on the affected documents; run `selftest --full` after approving a new type to compare scores.
- **Email and Teams notification.** Open proposals are in `work/review/PENDING.md` and the exit code is 2.

## 6. Adaptive schema: plan (partly built, see 5b)

**Today:** unknown section types and columns lose no text, but their meaning becomes generic or wrong. Tested with a "Project Harbor" BRD:

| In the document | What happened |
|---|---|
| Unknown ID prefix (`DM-01`) | Became the catch-all `record` type |
| `REG-1` "Satisfied By" | Generic `references` link |
| "Supports: FR-001" | **Wrong verb**: `underpins` |
| "Migrated By", "Source System" columns | Plain text, no links |

None of these are flagged by the confidence check or by verification.

**Design:**

1. **Gap detection.** Every fallback is recorded: unknown prefix, ID column without a verb, a column of person or system names without a verb, entity-like tables without IDs.
2. **Proposals.** New type, folder, prefixes and keywords; new verbs whose target type comes from what the column's values resolve to.
3. **Confidence.** A proposal is applied automatically only if all of these hold: at least 3 rows share the prefix; no clash with an existing type, verb or alias; link values resolve to one type; the column name isn't an alias of a different verb; gbrain validate and lint pass. Otherwise it goes for approval. AI proposals always go for approval.
4. **Low-confidence items are ingested immediately with defaults:** `record` type, generic `references` links, `schema_status: provisional`, `proposal: P-xxxx`. The rule: a fallback must be true but vague, never specific but wrong.
5. **Reviewer decisions:** approve as a new type (optionally renamed), map to an existing type or verb, or reject (stays generic, marked `unmapped`, never proposed again).
6. **After a decision:** re-extract only the affected documents. Moved pages are soft-deleted by default, or hard-deleted with `--purge-superseded`; only changed pages are re-chunked and re-embedded. `schema revert P-xxxx` undoes a decision. Automatic approvals are logged the same way.
7. **Review in Docker** never blocks a run:
   - proposal YAML files plus `pending.md` on the work volume;
   - `docingest schema proposals | approve | reject | revert` as one-off containers;
   - decision files applied at the start of the next run;
   - exit code 2 when decisions are pending (configurable);
   - a lock so `approve` and `ingest` don't overlap;
   - `decisions.log` as the audit trail.
8. **Learned vocabulary** goes in `vocabulary.learned.yaml` on a volume (not in the image), merged over `vocabulary.yaml`. Changes only add; the pack version increases on each change.
9. **Verification:** a `schema gaps` check: WARN on `record` or `references` fallbacks; INFO listing pending proposals.

**Files to change:**

| File | Change |
|---|---|
| `ingestion/extract_with_rules.py` | Record gaps at each fallback point |
| `utils/data_types.py` | `gaps` field on `DocumentResult` |
| new `ingestion/schema_evolution.py` | Gaps → proposals, scoring, apply or queue |
| `vocabulary/vocabulary_loader.py` | Merge the learned vocabulary, check clashes, bump the pack version |
| `ingestion/rule_confidence.py` | Treat fallbacks as low confidence |
| `ingestion/extract_with_ai.py` | Optional proposal fields, routed to review |
| `ingestion/pipeline.py` | Apply proposals, reinstall the pack, re-extract the document; queue the rest |
| `ingestion/ingest_state.py` | Map documents to pending proposals |
| `commands.py` | `--schema-mode auto\|review\|off` and the `schema` commands |
| `evaluation/verification.py` | `schema gaps` check |
| `README.md` | New section |

**Two fixes to make regardless:**

1. Generic column aliases (like "supports") map to a verb only when the target type fits.
2. The confidence check and verification report `record` and `references` fallbacks.

## 7. Standard attribute names and project grouping: plan (not built)

These come before gap analysis, because gap analysis is only as good as consistent data.

**Standard attribute names:**

- `attributes:` in `vocabulary.yaml`: standard name, the column headers it can appear under, value format. Example: `likelihood: {aliases: [probability, prob., chance], values: [low, medium, high]}`.
- Extraction maps headers to standard names and normalizes values (`Med`/`M` → `medium`); AI-extracted attributes too.
- Unknown columns keep their raw name and become schema gaps (proposal "add alias Prob. → likelihood").
- Verification reports the share of attributes stored under standard names.

**Project grouping:**

- A new shared type `project` (`projects/<name>`) and a verb `part_of_project` (document → project). Attachments take their parent's project.
- Name variants ("Lighthouse", "Project Lighthouse") are kept as aliases of one project.
- Sources for the project name, strongest first:

| # | Source | Confidence |
|---|---|---|
| 1 | `--project` flag | Certain |
| 2 | Document-control field: Project, Program, Initiative | High |
| 3 | Cover or first page ("Project: X"): PDF page 1, opening DOCX paragraphs, PPTX title slide | High |
| 4 | Header or footer repeated on every page | High |
| 5 | Title (`# Project X: …`) | Medium |
| 6 | Name of a project already in the brain, found in the text | Medium |
| 7 | Email subject, HTML `<title>`, file or folder name | Low |
| 8 | AI (`--llm`) with a verbatim quote | Low |

  High links automatically. Medium links automatically only if it matches an existing project; otherwise it goes for approval. Low always goes for approval.
- Converter gaps to fix:
  - DOCX headers and footers are dropped (mammoth ignores them); read them with python-docx.
  - PDF headers and footers are mixed into the body text; detect lines repeated at the top or bottom of most pages.
- **Sample PRD:** it says "Product: Lighthouse Mobile Returns". "Lighthouse" matches the BRD's project (source 6), so the PRD is linked to Project Lighthouse and the product name is kept as an attribute.

**Then gap analysis:**

- A blueprint file (e.g. `blueprints/tdd.yaml`) lists, per target section, the entity types, minimum counts, attributes, links and topics it needs.
- `docingest gap-analysis --blueprint tdd.yaml --project lighthouse` scores each section and lists what's present (with sources), what's missing, and questions to ask. It is also exposed to agents as a tool.

## 8. Planned branches (agreed 2026-10-21)

Each is a feature branch that the user merges into `main`.

| # | Branch | Scope | Needs EC2? |
|---|---|---|---|
| 0 | `feat/ec2-standalone` | This work (in review) | First real run |
| 1 | `fix/record-fallbacks` | The two fixes in section 9 | No |
| 2 | `feat/standard-attributes` | Section 7, first part | No |
| 3 | `feat/project-grouping` | Section 7, second part, plus the DOCX and PDF header and footer fixes | No |
| 4 | later | Section 6 (adaptive schema), then gap analysis | No |

Open questions: start with branch 1 or go straight to branch 2? Will `--project` be given for most runs, or should detection from the documents be the main path?

## 9. Two fixes to make first (explained)

**Fix 1: a generic column name becomes a verb only when the types fit.** `vocabulary.yaml` maps the header "Supports" to `underpins`, defined as "Assumption underpins this item". That is only true when the row is an assumption:

| Table | Row | Meaning | Stored today |
|---|---|---|---|
| Assumptions | `A-03` Supports `FR-001` | The assumption underpins the requirement | `underpins` (correct) |
| Requirements | `FR-010` Supports `OBJ-2` | The requirement supports an objective, so `achieves` | `underpins` (**wrong**) |
| Unknown type (`DM-01`) | `DM-01` Supports `FR-001` | Unclear | `underpins` (**wrong**) |

Change: verbs like `underpins` get a `source` type; when the row's type does not match, use the generic `references` link ("true but vague, never specific but wrong"). The decision is in `docingest/ingestion/extract_with_rules.py`, in `_entity_pages`.

**Fix 2: report the fallbacks.** An unknown ID prefix (`DM-01`) becomes type `record`, and an unknown link column becomes a generic `references` link (or none). Nothing reports this today: verification passes and the confidence check looks only at prose. Change: record each fallback, mark those sections low-confidence (so `--llm` can look at them), and add a verification row such as:

```
WARN  schema fallbacks
  6 entities typed `record` (unknown prefix DM-)   in section 7
  3 links typed `references` (column "Migrated By" has no verb)
```

This is also the input that the adaptive schema (section 6) turns into proposals.

## 10. Step E explained

E has two parts that fix the same problem: the same thing stored under different names.

- **Standard attribute names (E1).** One risk register says "Probability: High", another "Prob. | Med". An `attributes:` section in `vocabulary.yaml` gives each attribute a canonical name, its header aliases and allowed values; extraction maps headers to it and normalizes values (`Med` and `M` become `medium`).
- **Project grouping (E2).** Nothing ties a BRD, its risk register, an approval email and the PRD together as one project today. A `project` page type and a `part_of_project` link fix that (details in section 7).

Why it matters: gap analysis needs "for project X, what exists and what is missing" to be answerable; the adaptive schema needs a normalized baseline; "all high-likelihood risks on Lighthouse" only works when every risk says `likelihood: high` and belongs to the project; and changing the data model is cheapest while the brain is small (305 pages today).

## 11. How much will the adaptive schema fix automatically? (estimates, not measurements)

| Case | Path |
|---|---|
| Unknown prefix with at least 3 rows, no clash with an existing type, link values all resolve to one type | Applied automatically |
| New column whose values are IDs of one type, name not an alias of another verb | Applied automatically |
| Prefix with fewer than 3 rows | Reviewer |
| Ambiguous column ("Supports", "Impacts") | Reviewer |
| Anything proposed by the AI | Reviewer, always |
| Names or free text with no IDs | Reviewer |

Rough guess: 85–95% right from the start on documents like the samples (which were written to fit the vocabulary, so this overstates real life); 60–75% fixed automatically on messy or new document types; prose (email, chat, notes) adapts least and depends on the `--llm` pass. A reviewer would see a handful of decisions per new document type, not per document, and approvals apply to every later document.

**To get real numbers:** collect 15–20 real documents of the kinds the team will ingest; after Fix 2, run them and read the fallback report per document; hand-label a sample to measure how many typed entities and links are right. This also shows whether thresholds such as "3 rows" are sensible.

## 12. Follow-ups from the 2026-10-23 session (brain path, rejected AI items, counts)

Small items to do first (to be picked up tomorrow):

1. **Line length:** `_link_keys` in `docingest/reporting/dashboard_page.py` is 107 characters; the limit is 99.
2. **Tests:** add unit tests for `_brain_home` and `_env_local_value` (`docingest/commands.py`), for the evidence quote in rejection reasons (`extract_with_ai.py`), and for unique links from manifests for older runs (`dashboard_page.py`).
3. **Full test suite:** run it on the branch after rebasing onto `main`, before merging PR #5 (only 15 related tests were run after the rebase).
4. **`COMMANDS.md` line ~130:** `set -a; . ./.env.local; set +a` exports every setting into the shell, including `GBRAIN_HOME`. Reword it so it does not export `GBRAIN_HOME`.
5. **`COMMANDS.md` line ~17 and `setup.sh` line ~30:** the example `GBRAIN_HOME=/home/ubuntu/docingest-brain` caused confusion. Say that leaving it unset uses `./brain`.
6. **Q&A document** (`docs/local-only/technical/docingest-session-qa-2026-10-23.html`, not in git): update section 10 statuses. Items 4, 6 and 8 are done or partly done.

Deferred, and agreed to be done later:

7. **`ingest.sh` checks before each run:** warn when the brain's `config.json` `database_path` points outside `GBRAIN_HOME/.gbrain`, and print the brain path and its source on every run.
8. **Dashboard:** show the accepted and rejected AI items per document, with each quote and reason.
9. **Manifest:** keep rejected items with their quote in a list the reviewer can read, not only as text in the reasons.
10. **Verify check:** fail when a document has pages or chunks beyond what its manifest lists.
11. **AI pages and links:** mark AI-sourced pages and links, so they can be told apart from rule output. Decide whether accepted AI items should go to the review queue.
12. **Graph view:** a view built from an export of nodes and edges (not started).
13. **Counts:** reconcile the dashboard totals with `gbrain stats` (show brain totals and shared-page totals beside the per-document sums).

Environment checks, not code:

14. Remove any `export GBRAIN_HOME=…` line from the shell profile (`~/.zshrc`, `~/.bashrc`, `~/.profile`) on every machine. A shell value overrides `.env.local`.
15. Keep the repo's `.env` free of docingest settings. gbrain reads it from the folder it runs in and ignores `GBRAIN_HOME` there. Move those settings to `.env.local`.
16. EC2: the installed gbrain is not the tested version (0.60.281.0). Doctor shows a WARN. This stays as it is for now.
17. Check the brain with `./ingest.sh doctor` and `grep database_path` on each machine after pulling.

## 13. Parked: canonical names for shared entities (2026-10-25)

Parked for now. Revisit when the multi-document results need it.

- **Problem.** Shared pages are named by slugifying the name (`entity_slug`), so "Ravi Menon" and "R. Menon" become two pages. Only organizations have a matching rule (`_same_organization`, whole-word containment).
- **Measure first.** Count brain pages that would collapse to one normalized name. If there are almost none, skip the rest.
- **Scope if done.** Normalize only case, punctuation and legal suffixes (Ltd, Inc, LLC). Apply it to people and organizations only. No first-name-only matching, no fuzzy matching, no automatic merges.
- **Cost.** About 30 to 50 lines, plus tests for names that must not merge. Changing slugs means re-ingesting affected documents; otherwise old pages become orphans and links to them break.
- **Not planned.** Alias lists in `vocabulary.yaml`, unless the measurement shows variants that normalization misses.
- **Why not now.** Risk of over-merging people with the same name, and slug changes in an existing brain.
