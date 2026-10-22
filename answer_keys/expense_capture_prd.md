# Product Requirements Document: SnapClaim Mobile Expense Capture

| Field | Value |
|---|---|
| Document ID | PRD-EXP-014 |
| Product | SnapClaim (mobile) |
| Version | 2.1 |
| Owner | Priya Nandakumar |
| Status | In review |
| Date | 2026-09-30 |

## 1. Overview

Halvorsen Logistics employees file about 14,000 expense claims a month. Most start as paper receipts that are photographed days later, typed into the web portal, and often returned by finance for missing details. SnapClaim moves capture to the moment of purchase: the employee photographs the receipt, the app reads it, and the claim is ready to submit in under a minute.

This document covers the first two releases of the mobile app. Card reconciliation and per-diem rules are handled by the existing finance systems.

## 2. Goals and Success Metrics

| ID | Goal | Metric | Target |
|---|---|---|---|
| G-1 | Faster claim submission | Median days from purchase to submitted claim | Under 2 days |
| G-2 | Fewer returned claims | Claims returned by finance for missing information | Below 5% |
| G-3 | Mobile-first approvals | Claims approved from a phone | At least 60% |

## 3. Personas

| ID | Persona | Description |
|---|---|---|
| PE-1 | Field Employee | Travels most weeks and files 6 to 10 receipts a week from a phone |
| PE-2 | Approving Manager | Approves the team's claims, often between meetings |
| PE-3 | Finance Auditor | Checks claims against the travel policy before payment |

## 4. Scope

### 4.1 In scope

- Receipt capture with automatic reading of amount, date and merchant
- Claim drafting and submission from the phone
- Manager approval and rejection from the phone
- Offline capture with upload when the connection returns

### 4.2 Out of scope

- Corporate card reconciliation
- Per-diem and mileage calculations
- Changes to the web portal

## 5. Epics

| ID | Epic | Goal |
|---|---|---|
| EP-1 | Receipt capture | G-1 |
| EP-2 | Claim submission | G-1, G-2 |
| EP-3 | Mobile approvals | G-3 |

## 6. User Stories

| ID | Epic | Persona | User story | Priority |
|---|---|---|---|---|
| US-01 | EP-1 | PE-1 | As a field employee, I want to photograph a receipt so that I do not have to keep the paper. | Must |
| US-02 | EP-1 | PE-1 | As a field employee, I want the amount, date and merchant filled in for me so that I type less. | Must |
| US-03 | EP-1 | PE-1 | As a field employee, I want to capture receipts without a connection so that I can file them on the road. | Should |
| US-04 | EP-2 | PE-1 | As a field employee, I want the app to flag missing fields before I submit so that my claim is not returned. | Must |
| US-05 | EP-3 | PE-2 | As an approving manager, I want to approve or reject a claim from a notification so that my team is paid on time. | Must |
| US-06 | EP-3 | PE-3 | As a finance auditor, I want to see the original receipt image beside each line so that I can check it against policy. | Should |

## 7. Acceptance Criteria

| ID | Story | Criterion |
|---|---|---|
| AC-01 | US-01 | A receipt photo is stored with the claim and can be opened at full resolution. |
| AC-02 | US-02 | Amount, date and merchant are read correctly on at least 95% of printed receipts in the test set. |
| AC-03 | US-03 | Receipts captured offline upload automatically within 5 minutes of the connection returning. |
| AC-04 | US-04 | Submission is blocked until every required field for the expense category is filled. |
| AC-05 | US-05 | A manager can approve or reject in two taps from the push notification. |
| AC-06 | US-06 | The receipt image opens beside its line item in under 2 seconds. |

## 8. Non-Functional Requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-01 | Performance | Receipt reading completes in under 3 seconds on a mid-range phone. |
| NFR-02 | Security | Receipt images are encrypted on the device and in transit. |
| NFR-03 | Availability | The submission service is available 99.5% of business hours. |
| NFR-04 | Accessibility | Every screen meets WCAG 2.1 AA. |

## 9. Integrations

| System | Purpose |
|---|---|
| LedgerOne ERP | Receives approved claims for payment |
| IdentityHub | Single sign-on for employees and managers |
| ReceiptVision OCR | Reads amount, date and merchant from receipt images |

## 10. Release Plan

| ID | Milestone | Date | Includes |
|---|---|---|---|
| MS-1 | Beta for 200 employees | 2026-12-01 | EP-1 |
| MS-2 | General availability | 2027-02-15 | EP-1, EP-2 |
| MS-3 | Mobile approvals | 2027-04-01 | EP-3 |

## 11. Dependencies and Risks

| ID | Description | Owner |
|---|---|---|
| DEP-1 | LedgerOne ERP must expose its claims API (v3) before general availability. | Tomas Ruiz |
| RK-1 | Receipt reading accuracy may fall below 95% on faded thermal-paper receipts. | Priya Nandakumar |

## 12. Open Questions

| ID | Question | Owner |
|---|---|---|
| OQ-1 | Should rejected claims return to draft or start a new claim? | Priya Nandakumar |
| OQ-2 | Do managers need delegation while on leave in the first release? | Tomas Ruiz |
