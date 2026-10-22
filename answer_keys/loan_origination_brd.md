**MERIDIAN COMMUNITY BANK**

Information Technology · Lending Modernization Program

**Business Requirements Document**

Digital Loan Origination System (DLOS)

**Document Version:** 1.0 (Approved Baseline)

**Status:** Final · For Sign-off

**Author:** Business Analysis Office

**Date:** August 6, 2026

**Table of Contents**

**1. Document Control**

**1.1 Version History**

| **Version** | **Date**     | **Author**     | **Summary of Changes**                                          |
|-------------|--------------|----------------|-----------------------------------------------------------------|
| 0.1         | Jun 12, 2026 | S. Okafor (BA) | Initial draft — objectives, scope, stakeholder list.            |
| 0.5         | Jul 03, 2026 | S. Okafor (BA) | Added functional & non-functional requirements; process flow.   |
| 0.9         | Jul 24, 2026 | BA Office      | Incorporated Risk & Compliance review comments; KPIs finalized. |
| 1.0         | Aug 06, 2026 | BA Office      | Approved baseline for build phase.                              |

**1.2 Approvals**

This document requires sign-off from the following parties before development begins. Approval indicates agreement that the requirements accurately represent the business need.

| **Name / Role**                   | **Responsibility**  | **Approval Status** |
|-----------------------------------|---------------------|---------------------|
| D. Marchetti — VP, Retail Lending | Business Sponsor    | Pending signature   |
| A. Nguyen — Head of Compliance    | Regulatory sign-off | Pending signature   |
| R. Patel — IT Program Director    | Delivery owner      | Pending signature   |
| L. Fisher — Chief Risk Officer    | Risk acceptance     | Pending signature   |

**2. Executive Summary**

Meridian Community Bank currently processes consumer loan applications (personal, auto, and home-equity) through a combination of paper forms, branch visits, and disconnected back-office spreadsheets. **The average time from application to funding decision is 9 business days**, roughly three times the regional benchmark, and the manual hand-offs produce a measurable rate of data-entry error and lost applications.

This Business Requirements Document defines the business need for a Digital Loan Origination System (DLOS): a single online platform through which applicants apply, upload documents, and track status, and through which loan officers, underwriters, and compliance staff process applications on a unified workflow. The system will integrate with the bank's core banking platform, an external credit bureau, and an identity-verification service.

The target outcome is to reduce the application-to-decision cycle to **under 48 hours for 80% of standard applications**, cut manual re-keying to near zero, and give leadership real-time visibility into the lending pipeline — while maintaining full compliance with fair-lending and data-privacy obligations.

This document describes WHAT the business needs and WHY. It does not prescribe technical design; solution architecture and detailed system specifications will follow in a separate Functional Specification.

**3. Business Objectives & Background**

**3.1 Problem Statement (Current State)**

- Applications are captured on paper or PDF and manually re-keyed into three separate systems, causing delays and transcription errors.

- Applicants have no way to check status and call the branch on average 2.3 times per application, driving avoidable call-center volume.

- Underwriters pull credit reports and verify identity through manual, one-off logins rather than an integrated flow.

- Management has no consolidated view of pipeline volume, stage bottlenecks, or approval rates; reporting is assembled monthly by hand.

- Audit evidence (who decided what, and when) is scattered, making regulatory examinations slow and labor-intensive.

**3.2 Business Objectives**

The DLOS initiative is expected to deliver the following measurable objectives:

| **ID** | **Objective**                                       | **Target**                                   |
|--------|-----------------------------------------------------|----------------------------------------------|
| BO-1   | Reduce application-to-decision cycle time           | ≤ 48 hrs for 80% of standard applications    |
| BO-2   | Eliminate manual re-keying across systems           | 0 re-key steps; single point of entry        |
| BO-3   | Enable applicant self-service status tracking       | Reduce status-check calls by 60%             |
| BO-4   | Provide real-time pipeline visibility to management | Live dashboard, ≤ 5 min data latency         |
| BO-5   | Strengthen audit and compliance evidence            | 100% of decisions with immutable audit trail |

**3.3 Alignment to Strategy**

This initiative supports the bank's 2026–2028 strategic pillar of **“Digital-First Member Experience”** and directly enables the retail lending growth target of 15% year-over-year originations without proportional headcount increase.

**4. Project Scope**

**4.1 In Scope**

- Online application intake for personal, auto, and home-equity loans (web and mobile-responsive).

- Secure document upload and applicant status portal.

- Unified processing workflow for loan officers, underwriters, and compliance reviewers.

- Integrations: core banking system, credit bureau, identity/KYC verification service, e-signature.

- Automated decisioning rules engine for standard applications, with manual override for exceptions.

- Management reporting dashboard and immutable audit logging.

**4.2 Out of Scope**

- Commercial and small-business lending products (candidate for a future phase).

- Loan servicing and repayment collection after funding — remains in the existing servicing platform.

- Replacement of the core banking system itself; DLOS integrates with, but does not replace, it.

- Marketing / lead-generation website functionality.

**4.3 Assumptions, Constraints & Dependencies**

| **Category** | **Detail**                                                                                                  |
|--------------|-------------------------------------------------------------------------------------------------------------|
| Assumption   | The credit bureau and KYC vendors expose stable REST APIs available in a test environment by project start. |
| Assumption   | Existing product/pricing rules can be expressed as deterministic decision rules.                            |
| Constraint   | Solution must comply with fair-lending regulations, data-privacy law, and internal security standards.      |
| Constraint   | Go-live must avoid the November–December fiscal close freeze period.                                        |
| Dependency   | Core banking API enhancements owned by the Platform team must be delivered before integration testing.      |
| Dependency   | Compliance must approve the automated decisioning logic before it can be enabled in production.             |

**5. Stakeholders**

The following roles have an interest in or influence over the DLOS. Detailed RACI assignments are maintained in the project plan.

| **Stakeholder**                      | **Interest / Role**                  | **Key Needs**                                    |
|--------------------------------------|--------------------------------------|--------------------------------------------------|
| Business Sponsor (VP Retail Lending) | Owns business outcome and funding    | Faster cycle time, growth without headcount      |
| Loan Applicants (customers)          | End users of the self-service portal | Simple application, transparency, quick decision |
| Loan Officers                        | Front-line processing                | Single queue, no re-keying, clear tasks          |
| Underwriters                         | Credit assessment & decisioning      | Integrated credit/KYC data, override control     |
| Compliance & Risk                    | Regulatory adherence                 | Audit trail, fair-lending safeguards             |
| IT / Platform Team                   | Build, integrate, operate            | Clear requirements, stable integrations          |
| Executive Management                 | Strategic oversight                  | Real-time pipeline reporting                     |

**6. Future-State Business Process**

The target end-to-end flow for a standard application is summarized below. Steps marked (A) are automated by the system; steps marked (M) require a human decision.

| **Step** | **Stage**            | **Description**                                                            |
|----------|----------------------|----------------------------------------------------------------------------|
| 1        | Apply (A)            | Applicant completes the online form; system validates fields in real time. |
| 2        | Verify Identity (A)  | System calls the KYC service; result attached to the application.          |
| 3        | Upload Documents (A) | Applicant uploads required documents; system checks completeness.          |
| 4        | Credit Pull (A)      | System retrieves the credit report from the bureau via integration.        |
| 5        | Auto-Decision (A)    | Rules engine scores the application: Approve, Decline, or Refer.           |
| 6        | Manual Review (M)    | Referred cases routed to an underwriter with full context on one screen.   |
| 7        | Offer & e-Sign (A)   | Approved applicant receives terms and signs electronically.                |
| 8        | Fund & Handoff (A)   | System books the loan in core banking and hands off to servicing.          |

**7. Business Requirements**

Requirements are prioritized using MoSCoW (Must / Should / Could / Won't-this-release). Each requirement is traceable to a business objective (BO) from Section 3.

**7.1 Applicant Experience**

| **Req ID** | **Requirement**                                                                                                       | **Priority** |
|------------|-----------------------------------------------------------------------------------------------------------------------|--------------|
| FR-01      | The system must allow an applicant to complete and submit a loan application online without visiting a branch. (BO-1) | Must         |
| FR-02      | The system must let applicants securely upload and re-upload supporting documents. (BO-2)                             | Must         |
| FR-03      | The system must provide a real-time status view of each application. (BO-3)                                           | Must         |
| FR-04      | The system must notify applicants of status changes by email and SMS. (BO-3)                                          | Should       |
| FR-05      | The system could allow an applicant to save a draft and resume later. (BO-1)                                          | Could        |

**7.2 Processing & Decisioning**

| **Req ID** | **Requirement**                                                                                                         | **Priority** |
|------------|-------------------------------------------------------------------------------------------------------------------------|--------------|
| FR-06      | The system must present each application to staff on a single unified screen with all data and documents. (BO-2)        | Must         |
| FR-07      | The system must integrate with the credit bureau to retrieve reports automatically. (BO-1)                              | Must         |
| FR-08      | The system must apply configurable decision rules to auto-classify applications as Approve, Decline, or Refer. (BO-1)   | Must         |
| FR-09      | The system must allow an authorized underwriter to override an automated decision with a recorded justification. (BO-5) | Must         |
| FR-10      | The system should route referred applications to the appropriate work queue based on loan type and amount. (BO-1)       | Should       |

**7.3 Compliance, Reporting & Administration**

| **Req ID** | **Requirement**                                                                                                       | **Priority** |
|------------|-----------------------------------------------------------------------------------------------------------------------|--------------|
| FR-11      | The system must record an immutable audit trail of every action and decision with user, timestamp, and reason. (BO-5) | Must         |
| FR-12      | The system must present a management dashboard showing pipeline volume, stage aging, and approval rates. (BO-4)       | Must         |
| FR-13      | The system must enforce role-based access so users see only functions appropriate to their role. (BO-5)               | Must         |
| FR-14      | The system should allow a compliance administrator to update decision rules without a code release. (BO-5)            | Should       |
| FR-15      | The system could export regulatory reports in a standard examiner-ready format. (BO-5)                                | Could        |

**8. Non-Functional Requirements**

| **Category**   | **Ref** | **Requirement**                                                                                  |
|----------------|---------|--------------------------------------------------------------------------------------------------|
| Performance    | NFR-01  | Application submission screens load in ≤ 2 seconds under normal load.                            |
| Availability   | NFR-02  | The applicant portal is available 99.7% of the time (24×7 excluding planned windows).            |
| Scalability    | NFR-03  | The system supports at least 3× current peak daily application volume without redesign.          |
| Security       | NFR-04  | Data is encrypted in transit and at rest; access requires multi-factor authentication for staff. |
| Privacy        | NFR-05  | Personal data handling complies with applicable data-privacy regulations and retention rules.    |
| Usability      | NFR-06  | The applicant journey is completable on a mobile browser and meets WCAG 2.1 AA accessibility.    |
| Auditability   | NFR-07  | Audit logs are tamper-evident and retained for the regulatory minimum retention period.          |
| Recoverability | NFR-08  | Recovery point objective ≤ 15 min; recovery time objective ≤ 4 hrs.                              |

**9. Success Metrics & Acceptance Criteria**

The initiative will be considered successful when the following measures are met within three months of go-live:

| **Metric (KPI)**                        | **Baseline (Today)** | **Target**             |
|-----------------------------------------|----------------------|------------------------|
| Application-to-decision cycle time      | 9 business days      | ≤ 48 hrs (80% of apps) |
| Manual re-keying steps per application  | 3                    | 0                      |
| Status-check calls per application      | 2.3                  | ≤ 0.9                  |
| Applications completed on mobile        | 0%                   | ≥ 40%                  |
| Decisions with complete audit trail     | ~60% (manual)        | 100%                   |
| Straight-through (auto-decisioned) rate | 0%                   | ≥ 55%                  |

**10. Key Business Risks**

| **ID** | **Risk**                                              | **Rating** | **Mitigation**                                               |
|--------|-------------------------------------------------------|------------|--------------------------------------------------------------|
| RK-1   | Vendor API instability delays integration.            | High       | Early vendor test access; contractual SLAs.                  |
| RK-2   | Automated decision rules introduce fair-lending bias. | High       | Compliance review + periodic bias testing before enablement. |
| RK-3   | Staff resistance to new workflow reduces adoption.    | Medium     | Early involvement, training, phased rollout.                 |
| RK-4   | Data migration errors from legacy spreadsheets.       | Medium     | Reconciliation checks; parallel-run validation.              |
| RK-5   | Scope creep from out-of-scope loan types.             | Medium     | Change-control board; documented scope baseline.             |

**11. Glossary**

| **Term**                    | **Definition**                                                                                 |
|-----------------------------|------------------------------------------------------------------------------------------------|
| BRD                         | Business Requirements Document — describes the business need (what/why), not technical design. |
| DLOS                        | Digital Loan Origination System — the platform defined by this document.                       |
| KYC                         | Know Your Customer — regulatory identity-verification process.                                 |
| Straight-through processing | An application decided automatically with no manual intervention.                              |
| MoSCoW                      | Prioritization method: Must, Should, Could, Won't-have (this release).                         |
| RPO / RTO                   | Recovery Point Objective / Recovery Time Objective — data-loss and downtime tolerances.        |

**12. Sign-off**

By signing below, each approver confirms that the requirements in this document accurately represent the business need and authorizes the project to proceed to the design and build phase.

**D. Marchetti —** Business Sponsor Date: \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_

**A. Nguyen —** Head of Compliance Date: \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_

**R. Patel —** IT Program Director Date: \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_

**L. Fisher —** Chief Risk Officer Date: \_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_\_
