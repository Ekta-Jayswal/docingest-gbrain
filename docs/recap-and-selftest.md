# Run recap and self-test

Two features help you see what a run did and whether a change broke anything:

- **The run recap**: printed at the end of every `ingest`, one block per document.
- **The self-test**: `./ingest.sh selftest`, which runs the tests and, with `--full`, scores the answer keys.

Commands are shown with `./ingest.sh`; see [COMMANDS.md](../COMMANDS.md) for every option.

---

## 1. The run recap

When `ingest` finishes, it prints a summary of what it did. Each line has a timestamp
(`HH:MM:SS`), so you can see when each step happened.

Example, for one document:

```
14:41:32 Summary of run 20261022T091041Z-00f3a5
14:41:32 loan_origination_brd.md (brd)
14:41:32   content: 25 sections, 13 tables, 89 pages, 63 entities
14:41:32   chunks: 89    links: 153 (defined_in 63, mentions 41, section_of 25, supports 15, ...)
14:41:32   rules: 11 of 25 sections hold content rules cannot extract (stored, chunked and searchable, but entities in it are not extracted); run with --llm to extract them
14:41:32   tests: PASS (10 pass, 0 warn, 0 fail)
14:41:32   eval (this run): overall 97%, classification 100%, sections F1 96%, tables 100%, entities F1 99%, attributes 91%, links F1 91%, storage 100%, retrieval 100%
14:41:32   time: 5.96 s
14:41:32 Totals: 1 document(s) ingested, 0 failed
14:41:32   tests: PASS (111 pages, 111 chunks, 175 links verified)
```

### What each line means

| Line | Meaning |
|---|---|
| `content` | Sections, tables, pages, and entities the document became. Entities are the pages that are not the document or one of its sections. |
| `chunks` | Searchable text chunks stored for the document's pages. |
| `links` | Links stored between pages, with the most common relationship types (`mentions`, `owned_by`, ...). |
| `rules` | Sections the rules fully covered, and sections whose content the rules cannot extract. Those sections are still stored and searchable; only their entities are missing. Use `--llm` to extract them. |
| `AI` | Shown only with `--llm`: items the AI accepted and rejected, and the estimated cost. |
| `tests` | The verification checks for this document. `PASS`, `WARN` or `FAIL` with the counts. Each `WARN` or `FAIL` check is listed under it with its detail. |
| `eval` | Scores against an answer key (see section 2). |
| `time` | Seconds the document took. |
| `Totals` | Documents ingested and failed, the run-wide verification result, and any failures, deleted pages, embedding status, and schema proposals. |

If a line says `tests: not run (--no-verify)`, the run skipped verification, so chunk counts
are not shown either. If it says `chunks: not counted (verification skipped)`, the same applies.

### Evaluation in the recap

After ingestion, every document that has a matching answer key in `answer_keys/` (or
`answer_keys/holdout/`) is scored with the same code as `./ingest.sh eval`. The matching is by
file name. The scores appear on that document's `eval` line, labelled `this run`.

- **No matching key**: the recap says `eval: no answer key matches these documents`.
- **`--no-eval`**: skips the scoring. The recap then shows the newest evaluation on record for
  the file, labelled with its date, or nothing if there is none.

Scoring re-ingests the answer key's document into a throwaway brain, so it adds time to the run.
Scores need an answer key: a document without one gets no precision, recall or F1. The
held-out keys are described in `answer_keys/holdout/README.md`.

### Where the recap is saved

The recap is printed only. Everything it shows is also saved in the work folder, so it can be
checked later:

- `$DOCINGEST_WORK/run_history/` has one record per run and per document.
- `$DOCINGEST_WORK/reports/latest.md` is the verification report.
- `$DOCINGEST_WORK/reports/eval-latest.md` is the evaluation report (when eval ran).
- `$DOCINGEST_WORK/reports/dashboard.html` shows the same history in a browser.

---

## 2. Evaluation scores

Scores are calculated against an answer key, which is a hand-written list of what an ideal
ingestion of one document produces. For each stage, the scores are:

- **Precision**: of the items the pipeline produced, the share that were right.
- **Recall**: of the items the answer key expects, the share the pipeline found.
- **F1**: the harmonic mean of precision and recall.

Stages: classification, sections, tables, entities, attributes, links, storage (verification
checks), and retrieval (search hit rate). The **overall** score is the mean of the stage scores
that have one.

Answer keys live in `answer_keys/`, and held-out keys (not used for tuning) in
`answer_keys/holdout/`. To score a new document you must write its key; the pipeline does not
produce one. The `--llm` option extracts more entities during ingestion, but it does not write
a key, and an AI-written key would only measure agreement with itself.

Run the scoring on purpose with:

```bash
./ingest.sh eval                              # the tuned answer keys
./ingest.sh eval --holdout                    # the held-out keys
./ingest.sh eval --all                        # both
```

---

## 3. Self-test: did a change break anything?

```bash
./ingest.sh selftest                          # quick: about a second
./ingest.sh selftest --full                   # about 4 minutes: every test, then every answer key
./ingest.sh selftest --full --update-baseline # accept the current scores as the new baseline
cat $GBRAIN_HOME/work/reports/selftest-latest.md
```

### Quick run

Runs the rule unit tests (markdown parsing, vocabulary, list, table, PDF conversion, and
self-test). They take seconds and ingest nothing.

### Full run

Runs every test in `tests/`, some of which ingest the sample documents into a throwaway brain.
Then it scores every answer key, tuned and held out, and compares the scores with
`answer_keys/baseline.json`. It fails when:

- a stage score or the overall score falls by more than **one point**, or
- **precision** falls at all. A drop in precision means a wrong entity or link appeared, so
  there is no tolerance for it.
- an answer key in the baseline was not run, or there is no baseline yet.

Scores that rose by more than a point are listed as **improvements**. Raise the bar with
`--update-baseline` only after you have looked at them.

### Reading the report

The report is printed and saved to `reports/selftest-latest.md` in the work folder. It starts
with the unit test count, then (full run) a table of each answer key's overall score against
its baseline, marked `ok` or `FAIL`, and ends with `PASS` or `FAIL` and the reasons.

### Exit code

The exit code is `0` only when everything passes, so the command can go in a script.

### Rules to follow

- Update the baseline **only on purpose**, after a deliberate improvement or a reviewed change to
  an answer key. Never update it to make a failure go away.
- `--update-baseline` is refused while any unit test fails.
- Fixing a bug: add a test that fails before the fix and passes after it, then run `selftest`,
  and before handing over, `selftest --full`.

---

## Where the code is

| File | What it does |
|---|---|
| `docingest/reporting/recap.py` | Builds the recap text from the manifests, the verification report, and the evaluation history |
| `docingest/commands.py` | Runs the evaluation after ingestion (`--no-eval` skips it) and prints the recap with timestamps |
| `docingest/evaluation/selftest.py` | The quick and full self-test, the baseline comparison, and the report |
| `docingest/evaluation/answer_key_evaluation.py` | Scores one answer key (used by both `eval` and the recap) |
| `tests/test_recap.py` | The recap text: document blocks, totals, evaluation lines, and fallbacks |
| `tests/test_selftest.py` | What counts as a regression against the baseline |
