# Commands

Everything you need to set up, run, check and reset the pipeline, without any AI help.
Run all commands from this folder. `./ingest.sh` is the one entry point.

## 1. First-time setup

```bash
git clone -b <branch> <repository-url> docingest     # ask whoever shared this for the URL and branch
cd docingest
./setup.sh              # creates .venv, installs packages, creates .env.local, runs the check
```

Then edit `.env.local` (it is not committed). Remove the `#` from the lines you need:

```bash
GBRAIN_HOME=/home/ubuntu/docingest-brain      # a NEW folder for this experiment's brain
GBRAIN_BIN=/home/ubuntu/.bun/bin/gbrain       # only if `which gbrain` finds nothing
LITELLM_BASE_URL=https://your-proxy/v1
LITELLM_API_KEY=your-key
```

- `GBRAIN_HOME` must **not** be your existing brain's folder. If you leave it unset, the brain is created in `./brain` in this folder. All the pipeline's work files (state, reports) live in `$GBRAIN_HOME/work`, so a new brain folder means a clean start.
- Python 3.12 or newer is needed. On Ubuntu 24.04: `sudo apt-get install python3 python3-venv`.
- Word `.doc` files (not `.docx`) need LibreOffice: `sudo apt-get install libreoffice-writer-nogui`. Without it those files fail and everything else still runs.

## 2. Check the machine

```bash
./ingest.sh doctor
```

Prints PASS / WARN / FAIL / INFO per item: Python, packages, gbrain, bun, LibreOffice, brain folder, LiteLLM proxy (lists its models and embeds one word), LLM proxy for `--llm`. Fix every FAIL before going on. The messages say what to do.

## 3. Ingest

```bash
# Create the brain with embeddings, ingest, verify, embed, check the embeddings:
./ingest.sh ingest sample_documents --embed

# Your second folder:
./ingest.sh ingest /path/to/your/documents --embed

# One file:
./ingest.sh ingest /path/to/file.docx --embed

# Without embeddings (keyword search only):
./ingest.sh ingest sample_documents

# Say which project, team and person the run is for (recorded in the run history):
./ingest.sh ingest /path/to/documents --project lighthouse --team "Retail IT" --submitted-by ekta
```

`--project` can also come from `DOCINGEST_PROJECT`, `--team` from `DOCINGEST_TEAM` and `--submitted-by` from `DOCINGEST_USER` (in a container, set them with `-e`). Without a project the run is recorded as `unassigned`.

- The first `--embed` run on a new folder creates the brain with `litellm:text-embedding-3-small` at 1536 dimensions. Other model or width: set `EMBEDDING_MODEL=<name the proxy lists>` and `EMBEDDING_DIMENSIONS=<N>` in `.env.local`, or add `--embedding-model litellm:<name> --embedding-dimensions <N>` to **every** command that embeds. The width is fixed when the brain is created.
- Re-running is safe. Unchanged files are skipped; changed files are replaced.
- Exit code `0` = all files converted and all checks passed; `1` = a file failed or a check failed.
- Embedding only starts if verification did not fail. At most `--max-chunks` (default 20000) pending chunks are embedded in one run; a bigger backlog stops with a message.

## 4. Embeddings on their own

```bash
./ingest.sh embeddings-setup     # checks the proxy, creates the brain with embeddings if it is new
./ingest.sh embed                # embed whatever is pending, then run the five embedding checks
./ingest.sh embed --queries my-queries.yaml   # with your own semantic-search checks
```

`my-queries.yaml` format (the query must be worded unlike the document; `expect` is part of the page slug that must come back in the top 5):

```yaml
queries:
  - query: how do we hand money back to buyers
    expect: brd-lh-001/fr-006
```

## 5. Read the results

```bash
cat $GBRAIN_HOME/work/reports/latest.md              # ingestion verification, per document
cat $GBRAIN_HOME/work/reports/embeddings-latest.md   # embedding checks
```

(Use `./brain/work/...` if you left `GBRAIN_HOME` unset.) Status words: **PASS** fine, **WARN** look at it (often prose that rules can't parse, which is stored and searchable but not turned into entities), **FAIL** something was lost or missing, **INFO** a note.

Other commands:

```bash
./ingest.sh verify                    # re-verify everything ingested so far
./ingest.sh plan /path/to/folder      # per-section rule coverage, no changes to the brain
./ingest.sh convert /path/file.pdf    # print one file's markdown conversion
```

## 6. Run history and dashboard

Every `ingest` run appends to three files under `$GBRAIN_HOME/work/run_history/`, which are never rewritten:

| File | One line per | Holds |
|---|---|---|
| `runs.jsonl` | run | run ID, time, project, team, who, input path, documents, failed files, pages, chunks, links, check result, seconds |
| `documents.jsonl` | document per run | input file and size, SHA-256, kind, file type, sections, every table and what it became, pages by type, chunks, links by verb, each check, seconds, or the error if it failed |
| `evals.jsonl` | evaluated answer key | the scores from `eval` |

The page `$GBRAIN_HOME/work/reports/dashboard.html` is rebuilt after every run: totals, per project, by kind, file type, result and team, activity per day, evaluation scores, the runs, and one entry per document. Open it in a browser straight from the folder.

```bash
./ingest.sh dashboard                      # rebuild it by hand
./ingest.sh dashboard --out /tmp/page.html # write it elsewhere

# Quick looks without the page:
tail -n 1 $GBRAIN_HOME/work/run_history/runs.jsonl
grep '"project": "lighthouse"' $GBRAIN_HOME/work/run_history/documents.jsonl | wc -l
```

## 7. Measure quality (evaluation)

```bash
./ingest.sh eval              # the tuned answer keys in answer_keys/
./ingest.sh eval --holdout    # the held-out keys in answer_keys/holdout/ (never tuned on)
./ingest.sh eval --all        # both sets
./ingest.sh eval answer_keys/loan_origination_brd.yaml   # one key
./ingest.sh eval --keep                                 # keep the throwaway brain to inspect it
cat $GBRAIN_HOME/work/reports/eval-latest.md
```

Each answer key names a document in `answer_keys/` and lists what an ideal ingestion produces: sections, tables, entities with attributes, links, and search questions. `eval` ingests the document into a new, temporary brain (your brain is not touched), scores every stage (precision, recall, F1, accuracy), writes `eval-latest.md` and `.json`, and adds the scores to the dashboard. To add a key, copy `answer_keys/loan_origination_brd.yaml`, put the document beside it, and edit.

## 8. Ask the brain questions (gbrain commands)

Run these in the same shell, or prefix each with `GBRAIN_HOME=...`. Load your settings first: `set -a; . ./.env.local; set +a`.

```bash
gbrain stats                                   # pages, chunks, embedded, links
gbrain search "refund fraud" --limit 5         # keyword search
gbrain query "how do customers get their money back" --limit 5   # hybrid: keyword + meaning
gbrain list --limit 20                         # pages: slug, type, date, title
gbrain get docs/risks/brd-lh-001/rk-06         # one page with all its fields
gbrain graph-query docs/risks/brd-lh-001/rk-06 # follow its links
gbrain doctor                                  # gbrain's own health check
```

## 9. Remove or start over

```bash
# Soft-delete every page whose slug starts with a prefix (recoverable for 72 h).
# Without --yes it only says how many pages would go:
./ingest.sh purge docs/risks/brd-lh-001/
./ingest.sh purge docs/risks/brd-lh-001/ --yes

# Start completely over (also the way to change the embedding model or width):
#   1. point GBRAIN_HOME at a new, empty folder (edit .env.local)
#   2. ./ingest.sh ingest <folder> --embed
# The old brain folder can then be deleted by hand. Nothing else is touched.
```

## 10. Check that nothing broke (after a bug fix or any change)

```bash
./ingest.sh selftest                    # quick, about a second: the rule tests
./ingest.sh selftest --full             # about 4 minutes: every test, then every answer key
                                        # (tuned and held out) scored against the baseline
./ingest.sh selftest --full --update-baseline   # accept the current scores as the new baseline
cat $GBRAIN_HOME/work/reports/selftest-latest.md
```

The exit code is 0 only when everything passes, so it can sit in a script. `--full` fails when
any stage score falls by more than a point, or when precision falls at all (a wrong entity or
link appeared), compared with `answer_keys/baseline.json`. Scores that rose are listed as
improvements: raise the bar with `--update-baseline` once you have looked at them. Update the
baseline only on purpose, never to make a failure go away.

When you fix a bug, add a test that fails before the fix and passes after: a few lines of
markdown in `tests/test_list_extraction.py` or `tests/test_table_extraction.py` (copy a test
there), then run `selftest` and, before handing over, `selftest --full`. The tests need no
keys or network (the embedding tests use a fake LiteLLM proxy). The older `./run_tests.sh`
still runs every test with verbose output.

To try the pipeline against the fake proxy by hand:

```bash
.venv/bin/python tests/helpers/fake_litellm_proxy.py 4010 &
LITELLM_BASE_URL=http://127.0.0.1:4010/v1 LITELLM_API_KEY=x GBRAIN_HOME=/tmp/test-brain \
  ./ingest.sh ingest sample_documents --embed --queries tests/helpers/fake_proxy_search_questions.yaml
```

## 11. Review schema gaps (when the vocabulary does not know something)

When a document uses an ID prefix the vocabulary lacks (`DM-01`), or a link column that fits no
verb, docingest still ingests it, safely: the rows become the generic `record` type and the links
the generic `references`. Those pages carry `schema_status: provisional` and `proposal: P-0007`.
Nothing is lost and nothing is claimed that the document did not say. Each gap becomes a
**proposal**, one per prefix or column across all documents.

Who decides, in order:

1. **Rules** apply a proposal on their own when every check passes (at least 3 rows share the
   prefix and the name is free; or the column header spells out the one verb that fits). The
   documents are read again in the same run, and it is logged.
2. **AI** (only with `--llm`) writes a suggestion into the proposal. It never applies itself.
3. **A person** approves, maps to an existing type or verb, or rejects.

```bash
./ingest.sh review list                      # open proposals (also: cat $GBRAIN_HOME/work/review/PENDING.md)
./ingest.sh review show P-0007               # evidence, checks, alternatives, preview of the change
./ingest.sh review approve P-0007 --by ana --note "migration tasks"     # accept the suggestion
./ingest.sh review approve P-0007 --as migration_item --by ana --note "..."   # new type, other name
./ingest.sh review map P-0008 owned_by --by ana         # use an existing type (prefix) or verb (column)
./ingest.sh review reject P-0009 --by ana --note "..."  # stays generic, never proposed again
./ingest.sh review approve --all-suggested --by ana     # every suggestion that maps to an existing type or verb
./ingest.sh review apply                     # apply what reviewers recorded (the next ingest does it too)
./ingest.sh review revert P-0007 --by ana    # undo an applied decision
./ingest.sh review log                       # the audit trail
```

Reviewers can also edit `work/review/pending/P-0007.yaml`: set `decision:` (`approve`,
`map:<name>`, `rename:<name>` or `reject`), put a name in `reviewer:` and save. It is the same
record as the commands.

**Before you decide, read `review show`.** It lists every affected row, what each cell points
to, which checks passed or failed, the three closest existing types or verbs with their
definitions, the AI's reason and word-for-word quote, and a preview made by reading the
documents again with your decision applied ("2 pages change `record` -> `data_migration`", with
example lines). **When unsure, reject**: the data stays generic but correct, and you can
approve later.

What stops a wrong decision:

| Safeguard | Effect |
|---|---|
| Refused at once | A verb whose from/to types do not fit the rows, a type that is not a per-document type, a name that clashes with a type, verb, keyword, prefix or folder, or anything that makes `vocabulary.yaml` inconsistent |
| Preview and confirm | `approve`, `map` and `reject` print the preview and ask (`--yes` for scripts) |
| Two reviewers | A new type, a change to more than 20 pages, or a decision that differs from every suggestion needs two different people (`--by`). Optional `review/reviewers.yaml` with `new_type_approvers: [names]` limits who may approve new types |
| A reason | A new type, a rejection, or going against the suggestion needs `--note` |
| One proposal per prefix or column | The same prefix cannot be decided two ways in two documents |
| Rollback | After a decision is applied the affected documents are verified; if verification fails, the decision is undone and the proposal reopens |
| Undo | `review revert` removes it from `vocabulary.learned.yaml` and reads the documents again |
| Audit | `work/review/decisions.log` records who decided what, and when; the verification report lists decisions of the last 7 days |

Settings: `ingest --schema-mode auto|review|off` (`auto`: rules apply the clear ones;
`review`: every gap waits for a person; `off`: ignore gaps) and `--pending-exit-code N`. An
`ingest` that succeeded but left proposals open exits with code **2** (use `0` to turn that off).
Files: `work/review/{PENDING.md, pending/, decided/, decisions.log}` and
`work/vocabulary.learned.yaml` (additions only, merged over `vocabulary.yaml`).

## 12. When something goes wrong

| Message or symptom | Fix |
|---|---|
| `No virtual environment found` | `./setup.sh` |
| `gbrain ... not found` | Set `GBRAIN_BIN` in `.env.local` to the output of `which gbrain` |
| `LITELLM_BASE_URL is not set` | Fill in `.env.local`; check with `./ingest.sh doctor` |
| `answered HTTP 404` for the proxy | Add or remove the trailing `/v1` in `LITELLM_BASE_URL` |
| `does not list model` | Use a name from the list it prints: `--embedding-model litellm:<name>` |
| `returned N dimensions, expected 1536` | Pass the model's real width with `--embedding-dimensions N`, on a new brain folder |
| `--embed: this brain was created with embeddings off` | Use a new `GBRAIN_HOME` folder (section 9) |
| `The brain ... was created with a different embedding setup` | Same: new folder |
| `Legacy .doc needs LibreOffice` | Install LibreOffice, or save the file as `.docx` |
| `chunks are pending, more than --max-chunks` | Add `--max-chunks <bigger number>` if the size is expected |
| `Could not start gbrain MCP server` | Another gbrain process holds the brain. Stop it (`gbrain serve`), run again |
| A document shows `FAIL` in the report | Open `latest.md`, find the document's section; the failing row names the check |
| `gbrain ignored GBRAIN_HOME` | Never put a `.env` or `.env.local` in a folder where you run `gbrain` by hand without exporting the variables first: gbrain then ignores `GBRAIN_HOME` and uses another brain. `./ingest.sh` already guards against this |
| Anything else | Logs: `$GBRAIN_HOME/work/logs/gbrain-cli.log` and `gbrain-serve.log` |

Your existing brain is not touched as long as `GBRAIN_HOME` points elsewhere. To be sure, compare `ls -la ~/.gbrain` (or your old brain's folder) before and after a run.
