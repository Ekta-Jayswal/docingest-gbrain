# docingest vocabulary

Generated from `docingest/vocabulary/vocabulary.yaml` by `python -m docingest vocab`. Do not edit by hand: change the YAML and regenerate.

48 link verbs (32 kept, 6 renamed, 2 merged, 8 new) over 37 page types. A link reads as a sentence: source, verb, target.

## Link verbs

| Group | Verb | Reverse | From | To | Meaning | Evidence | Docs | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| structure | `defined_in` | `defines` | `any` | `source_document` | Item is defined in this document | Always, for every item extracted from a document | all | keep |
| structure | `section_of` | `has_section` | `document_section` | `source_document` | Section belongs to this document | Always | all | keep |
| structure | `mentions` | `mentioned_in` | `document_section` | `any` | Section text names this item | The item's ID appears in the section text | all | renamed (was `mentions_item`) |
| structure | `appears_in` | `features` | `shared_entities` | `source_document` | A shared person, organization, group, system or term appears in this document | Always, for shared entities | all | keep |
| structure | `attachment_of` | `has_attachment` | `source_document` | `source_document` | Document was attached to this email | The file was attached to an email | email | keep |
| structure | `supersedes` | `superseded_by` | `source_document` | `source_document` | A newer version of the same document | Same document ID with a higher version | all | new |
| structure | `governed_by` | `governs` | `source_document` | `source_document` | Document works under this agreement | "Governed by the MSA ..." naming a document ID | sow | new |
| structure | `references` | `referenced_by` | `any` | `any` | Generic reference to another item (used when no specific verb applies) | An ID with no more specific verb (the fallback) | all | keep |
| parties | `authored_by` | `authored` | `source_document` | `person` | Created by | Author or Prepared by field | all | keep |
| parties | `owned_by` | `owns` | `any` | `person` | Accountable owner | Owner, Accountable, Responsible or Lead column | all | keep |
| parties | `sponsored_by` | `sponsors` | `source_document`, `business_objective` | `person` | Executive sponsor | Sponsor field or column | brd, prd | keep |
| parties | `approved` | `approved_by` | `person`, `organization` | `source_document` | Person or party approved or signed off the document | Approvals or signature table | all | keep |
| parties | `assigned_to` | `assigned` | `action_item` | `person` | Task is assigned to | Assignee column | all | keep |
| parties | `requested_by` | `requested` | `requirement_types`, `change_request` | `person` | Asked for by | Requested by or Requester column | all | keep |
| parties | `decided_by` | `decided` | `decision` | `person` | Decision was made by | Decided by column | all | keep |
| parties | `member_of` | `has_member` | `person` | `organization`, `stakeholder` | Belongs to this company or group | Organization, Organisation, Company or Party column in a people or sign-off table | all | new |
| parties | `issued_by` | `issued` | `source_document` | `organization` | Organization that owns the document | Organization or Company field on the title page or in document control | all | renamed (was `organization`) |
| parties | `has_client` | `client_of` | `source_document` | `organization` | The client party | Client, Customer or Buyer field | sow | renamed (was `client`) |
| parties | `has_vendor` | `vendor_of` | `source_document` | `organization` | The supplier party | Vendor, Supplier or Contractor field | sow | renamed (was `vendor`) |
| parties | `sent_by` | `sent` | `source_document` | `person` | Email sender | Email From header | email | keep |
| parties | `sent_to` | `received` | `source_document` | `person` | Email recipient | Email To header | email | keep |
| parties | `copied_to` | `copied_on` | `source_document` | `person` | Email CC recipient | Email Cc header | email | keep |
| parties | `has_participant` | `participated_in` | `source_document` | `person` | Person spoke or posted in this transcript or chat | Speaker names in a transcript or chat | transcript, chat | keep |
| goals_scope | `supports` | `supported_by` | `requirement_types`, `epic`, `feature`, `user_story`, `scope_item`, `deliverable` | `business_objective` | Serves this objective | Objective or Goal column | all | renamed (was `achieves`) |
| goals_scope | `measures` | `measured_by` | `kpi` | `business_objective`, `requirement_types`, `deliverable` | Metric measures progress on this item | Objective or KPI for column in a KPI table | all | keep |
| goals_scope | `within_scope` | `scopes` | `requirement_types`, `feature`, `deliverable` | `scope_item` | Falls under this scope item | Scope column | all | keep |
| requirements | `implements` | `implemented_by` | `requirement_types` | `requirement_types` | Detailed requirement implements a broader one (FR to BR) | Business requirement, Parent or Traces to column | brd, prd | keep |
| requirements | `realizes` | `realized_by` | `work_items` | `requirement_types` | Use case, story or process step carries out this requirement | Requirements covered or Realizes column | brd, prd | keep |
| requirements | `part_of` | `has_part` | `user_story`, `feature` | `feature`, `epic` | Belongs to this larger item | Epic, Feature or Parent column | prd | renamed (was `belongs_to`) |
| requirements | `verifies` | `verified_by` | `acceptance_criterion` | `requirement_types`, `user_story`, `deliverable` | Test that proves the item is done | Verifies or Requirement column in an acceptance table | all | keep |
| requirements | `constrains` | `constrained_by` | `nonfunctional_requirement`, `constraint`, `business_rule`, `service_level` | `any` | Limits how this item may be met | Applies to, Affects or Limits column | all | merged (was `limits`, `applies_to`) |
| requirements | `performed_by` | `performs` | `work_items` | `persona`, `stakeholder` | Carried out by this user type | Actor, Persona or Role column | all | keep |
| requirements | `uses_system` | `used_by` | `requirement_types`, `work_items` | `system` | Needs this system | System column, or a known system's name in the row | all | new |
| requirements | `integrates_with` | `integrated_by` | `system` | `system` | System-to-system interface | Integrates with column in a systems table | brd, prd | keep |
| requirements | `uses_term` | `term_used_by` | `any` | `glossary_term` | Uses this defined term | Term column | all | keep |
| requirements | `depends_on` | `dependency_of` | `any` | `any` | Cannot finish without this item | Depends on, Prerequisite or Relies on column | all | merged (was `relies_on`) |
| requirements | `blocks` | `blocked_by` | `issue`, `dependency` | `any` | Stops this item until resolved | Blocks or Impacts column | all | keep |
| raid | `underpins` | `underpinned_by` | `assumption` | `any` | This item relies on the assumption being true | Underpins column in an assumptions table | all | keep |
| raid | `threatens` | `threatened_by` | `risk` | `any` | Risk could harm this item | Threatens or Affected items column in a risk table | all | keep |
| raid | `mitigates` | `mitigated_by` | `action_item`, `requirement_types`, `decision` | `risk` | Reduces this risk | Mitigates column | all | keep |
| raid | `resolves` | `resolved_by` | `decision`, `action_item` | `issue`, `open_question` | Closes this issue or question | Resolves or Closes column | all | keep |
| delivery | `delivers` | `delivered_by` | `deliverable`, `milestone` | `requirement_types`, `epic`, `feature`, `user_story`, `scope_item` | Hands over this item | Delivers or Includes column | prd, sow | keep |
| delivery | `follows` | `precedes` | `milestone`, `process_step` | `milestone`, `process_step` | Comes after this milestone or step | Predecessor column, or the order of a Step or schedule table | all | keep |
| delivery | `scheduled_for` | `schedules` | `deliverable` | `milestone` | Due at this milestone | Milestone or Due column in a deliverables table | prd, sow | new |
| delivery | `paid_on` | `triggers_payment` | `payment_term` | `milestone`, `deliverable` | Payment falls due when this is reached or accepted | Milestone or Deliverable column in a payment table | sow | new |
| delivery | `funds` | `funded_by` | `budget_item` | `milestone`, `deliverable` | Pays for this item | Funds column | sow | keep |
| delivery | `accepted_by` | `accepted` | `deliverable`, `milestone` | `person` | Who signs off the deliverable | Accepted by column | sow | new |
| delivery | `changes` | `changed_by` | `change_request` | `any` | Alters this agreed item | Changes or Changed items column in a change log | sow | new |

## Type groups

| Group | Types |
| --- | --- |
| `requirement_types` | `business_requirement`, `functional_requirement`, `nonfunctional_requirement`, `requirement`, `business_rule` |
| `work_items` | `use_case`, `user_story`, `process_step` |
| `shared_entities` | `person`, `organization`, `stakeholder`, `system`, `glossary_term` |

## Page types

| Group | Type | Folder | Scope | ID prefixes | Description |
| --- | --- | --- | --- | --- | --- |
| structure | `source_document` | `docs/documents/` | document |  | One ingested input file (BRD, PRD, SOW, email, transcript, chat, spreadsheet, ...) |
| structure | `document_section` | `docs/sections/` | document |  | One heading-delimited section (or segment) of a source document, holding its full text |
| parties | `person` | `people/` | global | `SH`, `STK`, `PPL` | A named individual (stakeholder, author, approver, email participant, speaker) |
| parties | `organization` | `companies/` | global | `ORG`, `VEN`, `PTY` | A company or organization (client, vendor, partner) |
| solution | `system` | `docs/systems/` | global | `SYS`, `APP`, `INT` | A software system, application, platform, or external service |
| solution | `glossary_term` | `docs/glossary/` | global | `GL`, `TERM` | A defined term or acronym |
| parties | `persona` | `docs/personas/` | document | `PE`, `PER` | A user persona or actor type |
| goals | `business_objective` | `docs/objectives/` | document | `BO`, `OBJ`, `GOAL` | A business goal or objective with a measurable target |
| scope | `scope_item` | `docs/scope/` | document | `SC`, `SCOPE` | An item explicitly in or out of scope |
| requirements | `business_requirement` | `docs/business-requirements/` | document | `BR`, `BRQ` | A business-level need the solution must satisfy |
| requirements | `functional_requirement` | `docs/functional-requirements/` | document | `FR`, `FRQ` | A specific behaviour the system must provide |
| requirements | `nonfunctional_requirement` | `docs/nonfunctional-requirements/` | document | `NFR`, `NF`, `QR` | A quality attribute (performance, security, availability, ...) |
| requirements | `requirement` | `docs/requirements/` | document | `REQ`, `RQ` | A requirement that is not classified as business, functional, or non-functional |
| requirements | `use_case` | `docs/use-cases/` | document | `UC` | A use case describing an actor's interaction with the system |
| requirements | `epic` | `docs/epics/` | document | `EP`, `EPIC` | A large body of product work grouping user stories |
| requirements | `user_story` | `docs/user-stories/` | document | `US`, `STORY` | A user story ("As a ..., I want ..., so that ...") |
| requirements | `feature` | `docs/features/` | document | `FEAT`, `FT` | A product feature or capability |
| delivery | `deliverable` | `docs/deliverables/` | document | `DEL`, `DLV` | A contracted or planned output to be handed over |
| requirements | `acceptance_criterion` | `docs/acceptance-criteria/` | document | `AC` | A condition that must be met for work to be accepted |
| delivery | `service_level` | `docs/service-levels/` | document | `SLA`, `SL` | A committed service level (response time, uptime, ...) |
| delivery | `payment_term` | `docs/payment-terms/` | document | `PAY`, `PT` | A payment, fee, or invoicing term |
| raid | `dependency` | `docs/dependencies/` | document | `DEP` | Something the work depends on that it does not control |
| raid | `assumption` | `docs/assumptions/` | document | `AS`, `ASM` | Something taken to be true without proof |
| raid | `constraint` | `docs/constraints/` | document | `CON`, `CN` | A fixed limit on budget, schedule, technology, regulation, or resources |
| raid | `risk` | `docs/risks/` | document | `RK`, `RISK`, `RSK` | An uncertain event that could harm objectives |
| raid | `issue` | `docs/issues/` | document | `ISS`, `ISSUE`, `BUG` | A current problem that needs resolution |
| raid | `decision` | `docs/decisions/` | document | `DEC` | A decision that was made, and by whom |
| raid | `action_item` | `docs/action-items/` | document | `ACT`, `AI`, `TASK` | A task assigned to someone, often with a due date |
| raid | `open_question` | `docs/open-questions/` | document | `OQ`, `QN` | An unresolved question |
| goals | `kpi` | `docs/kpis/` | document | `KPI`, `MET` | A metric used to measure success, with baseline and target |
| delivery | `milestone` | `docs/milestones/` | document | `MS`, `MIL` | A dated delivery point or phase |
| delivery | `budget_item` | `docs/budget/` | document | `BUD`, `COST` | A budget line or cost item |
| parties | `stakeholder` | `docs/stakeholders/` | global | `SG` | A group or role with an interest in the work (e.g. Underwriters), not a named person |
| requirements | `business_rule` | `docs/business-rules/` | document | `BRL`, `RULE` | A policy or rule the business must follow |
| solution | `process_step` | `docs/process-steps/` | document | `STEP` | One step of a current or future business process |
| delivery | `change_request` | `docs/change-requests/` | document | `CR`, `CHG` | A requested change to agreed scope, price or schedule |
| fallback | `record` | `docs/records/` | document |  | A row from an ID-keyed table whose ID prefix is not in this vocabulary |
