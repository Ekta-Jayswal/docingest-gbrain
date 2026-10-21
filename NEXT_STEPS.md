# Next steps (pending access to the EC2 machine)

Status as of 2026-10-17: these are decided or open, and nothing below is built yet. When the EC2 machine is reachable, run the checks in section 1. Then decide sections 2 and 3, and build the embedding phase in section 4.

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
  -d '{"model":"text-embedding-3-small","input":"hello","dimensions":1536}' \
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
- Model: `text-embedding-3-small`, 1536 dimensions
- If the proxy names the model `text-embedding-3-small`, gbrain sends the width. If it uses a custom alias, declare the native width (1536 for small).

One-time setup on the brain:

```bash
gbrain embeddings enable --embedding-model litellm:text-embedding-3-small --embedding-dimensions 1536
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

## 6. Adaptive schema: plan (not built)

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
| `extract.py` | Record gaps at each fallback point |
| `model.py` | `gaps` field on `DocumentResult` |
| new `schema_evolution.py` | Gaps → proposals, scoring, apply or queue |
| `vocab.py` | Merge the learned vocabulary, check clashes, bump the pack version |
| `confidence.py` | Treat fallbacks as low confidence |
| `llm.py` | Optional proposal fields, routed to review |
| `pipeline.py` | Apply proposals, reinstall the pack, re-extract the document; queue the rest |
| `state.py` | Map documents to pending proposals |
| `cli.py` | `--schema-mode auto\|review\|off` and the `schema` commands |
| `verify.py` | `schema gaps` check |
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
