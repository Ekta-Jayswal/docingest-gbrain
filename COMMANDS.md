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

Prints PASS / WARN / FAIL / INFO per item: Python, packages, gbrain, bun, LibreOffice, brain folder, LiteLLM proxy (lists its models and embeds one word), Claude key. Fix every FAIL before going on. The messages say what to do.

## 3. Ingest

```bash
# Create the brain with embeddings, ingest, verify, embed, check the embeddings:
./ingest.sh ingest samples/inputs --embed

# Your second folder:
./ingest.sh ingest /path/to/your/documents --embed

# One file:
./ingest.sh ingest /path/to/file.docx --embed

# Without embeddings (keyword search only):
./ingest.sh ingest samples/inputs
```

- The first `--embed` run on a new folder creates the brain with `litellm:text-embedding-3-small` at 1536 dimensions. Other model or width: add `--embedding-model litellm:<name> --embedding-dimensions <N>` to **every** command that embeds. The width is fixed when the brain is created.
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

## 6. Ask the brain questions (gbrain commands)

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

## 7. Remove or start over

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

## 8. Run the tests

```bash
./run_tests.sh      # uses a fake LiteLLM proxy; needs no keys or network
```

To try the pipeline against the fake proxy by hand:

```bash
.venv/bin/python tests/fake_litellm.py 4010 &
LITELLM_BASE_URL=http://127.0.0.1:4010/v1 LITELLM_API_KEY=x GBRAIN_HOME=/tmp/test-brain \
  ./ingest.sh ingest samples/inputs --embed --queries tests/queries_fake.yaml
```

## 9. When something goes wrong

| Message or symptom | Fix |
|---|---|
| `No virtual environment found` | `./setup.sh` |
| `gbrain ... not found` | Set `GBRAIN_BIN` in `.env.local` to the output of `which gbrain` |
| `LITELLM_BASE_URL is not set` | Fill in `.env.local`; check with `./ingest.sh doctor` |
| `answered HTTP 404` for the proxy | Add or remove the trailing `/v1` in `LITELLM_BASE_URL` |
| `does not list model` | Use a name from the list it prints: `--embedding-model litellm:<name>` |
| `returned N dimensions, expected 1536` | Pass the model's real width with `--embedding-dimensions N`, on a new brain folder |
| `--embed: this brain was created with embeddings off` | Use a new `GBRAIN_HOME` folder (section 7) |
| `The brain ... was created with a different embedding setup` | Same: new folder |
| `Legacy .doc needs LibreOffice` | Install LibreOffice, or save the file as `.docx` |
| `chunks are pending, more than --max-chunks` | Add `--max-chunks <bigger number>` if the size is expected |
| `Could not start gbrain MCP server` | Another gbrain process holds the brain. Stop it (`gbrain serve`), run again |
| A document shows `FAIL` in the report | Open `latest.md`, find the document's section; the failing row names the check |
| `gbrain ignored GBRAIN_HOME` | Never put a `.env` or `.env.local` in a folder where you run `gbrain` by hand without exporting the variables first: gbrain then ignores `GBRAIN_HOME` and uses another brain. `./ingest.sh` already guards against this |
| Anything else | Logs: `$GBRAIN_HOME/work/logs/gbrain-cli.log` and `gbrain-serve.log` |

Your existing brain is not touched as long as `GBRAIN_HOME` points elsewhere. To be sure, compare `ls -la ~/.gbrain` (or your old brain's folder) before and after a run.
