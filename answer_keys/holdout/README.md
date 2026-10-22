# Held-out answer keys

These three documents measure how the pipeline does on documents it was never tuned on.
`./ingest.sh eval` does not run them by default; run them on purpose:

    ./ingest.sh eval answer_keys/holdout/*.yaml

| Document | Format | Outline taken from |
|---|---|---|
| permit_portal_brd.docx | Word | Public BRD guides: executive summary, objectives, scope, stakeholders, constraints, cost-benefit (Slite), plus functional, data, non-functional, interface requirements and glossary (common BRD samples) |
| pharmacy_refills_prd.md | Markdown | A public 12-part PRD template: header, problem, goals and metrics, non-goals, personas, user stories with Given/When/Then, functional and non-functional requirements, dependencies, phasing, open questions and risks |
| accessibility_audit_sow.pdf | PDF | The areas a statement of work addresses (purpose, scope, location, period of performance, deliverables, standards, acceptance, special requirements, payment), in "The Contractor shall" government style |

Rules:

1. The keys were written from the documents alone, before the pipeline ran on them.
2. Never change extraction rules or the vocabulary to fix a held-out miss directly. Find
   the general pattern, add a training example of it under `answer_keys/`, fix it there,
   then re-measure here.
3. Never edit a key to match the pipeline's output. Change a key only to correct a
   mistake in the key itself, and record why in the experiment log.
4. Regenerate the Word and PDF files with `python tests/helpers/make_holdout_documents.py`.
