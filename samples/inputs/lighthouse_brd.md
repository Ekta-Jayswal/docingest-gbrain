# Business Requirements Document: Project Lighthouse — Customer Self-Service Portal

## 1. Document Control

| Field | Value |
|---|---|
| Document ID | BRD-LH-001 |
| Project | Project Lighthouse — Customer Self-Service Portal |
| Organization | Meridian Home Goods |
| Version | 1.2 |
| Status | Approved |
| Author | Luis Ortega (SH-10) |
| Business Owner | Daniel Okafor (SH-02) |
| Executive Sponsor | Priya Raman (SH-01) |
| Created | 2026-09-01 |
| Last Updated | 2026-10-14 |
| Classification | Internal — Confidential |

### Revision History

| Version | Date | Author | Changes |
|---|---|---|---|
| 0.1 | 2026-09-01 | Luis Ortega | Initial draft from discovery workshops |
| 0.5 | 2026-09-18 | Luis Ortega | Added functional requirements and use cases |
| 1.0 | 2026-10-02 | Luis Ortega | Baseline issued for stakeholder review |
| 1.1 | 2026-10-09 | Luis Ortega | Added NFR-07 and RK-06 after Finance review |
| 1.2 | 2026-10-14 | Luis Ortega | Final approvals incorporated |

## 2. Executive Summary

Meridian Home Goods receives 48,000 support calls per month, most of them about order status, returns, and account changes. Project Lighthouse delivers a customer self-service portal that lets customers track orders, return or exchange items, manage their accounts, and get instant answers without contacting an agent. The portal integrates with OrderHub, the warehouse system, CareDesk CRM, and the PayBridge payment gateway. It is delivered in three releases between May and August 2027, with a total budget of $1.70M against a cap of $1.85M. Expected outcomes are a 35% reduction in support calls, CSAT rising from 72% to 85%, and return processing falling from 9 days to 4.

## 3. Business Background and Problem Statement

Meridian sells furniture and home goods online and through 140 stores. Online revenue grew 41% over the last two years, but post-purchase service did not scale with it.

Current state:

- 62% of inbound calls are "where is my order" queries; 18% concern returns; 9% concern account changes.
- Returns are started by phone or email and keyed manually into OrderHub. The average return takes 9 days from request to refund.
- Customers have no online view of order status beyond the confirmation email.
- Cost-to-serve is $4.10 per order and rising with call volume.
- Post-purchase CSAT is 72%, the lowest score in the customer journey.

Problem statement: customers cannot resolve routine post-purchase needs on their own, which drives high call volume, slow returns, rising cost, and low satisfaction.

## 4. Business Objectives

| ID | Objective | Target | Owner |
|---|---|---|---|
| BO-01 | Reduce inbound support call volume | 35% reduction within 12 months of launch | SH-03 |
| BO-02 | Raise post-purchase customer satisfaction | CSAT from 72% to 85% | SH-01 |
| BO-03 | Shorten return processing time | Average from 9 days to 4 days | SH-08 |
| BO-04 | Lower cost-to-serve per order | 20% reduction, from $4.10 to $3.28 | SH-06 |

## 5. Scope

| ID | Item | Status | Rationale |
|---|---|---|---|
| SC-01 | Online order tracking for registered and guest customers | In Scope | Largest call driver (62% of contacts) |
| SC-02 | Self-service returns, exchanges, and refunds | In Scope | Second-largest call driver; drives return cycle time |
| SC-03 | Account, profile, and privacy self-management | In Scope | Removes account-change calls; meets privacy obligations |
| SC-04 | Knowledge base and virtual assistant | In Scope | Answers common questions 24/7 |
| SC-05 | Agent console integration with self-service history | In Scope | Shortens escalated calls |
| SC-06 | In-store point-of-sale returns | Out of Scope | Covered by the separate Retail Operations program in FY27 |
| SC-07 | Native iOS and Android apps | Out of Scope | Deferred to Phase 2 after web adoption is proven |
| SC-08 | Loyalty program redesign | Out of Scope | Owned by Marketing; no dependency on this portal |

## 6. Stakeholders

| ID | Name | Title | Department | Project Role | RACI | Influence |
|---|---|---|---|---|---|---|
| SH-01 | Priya Raman | VP Customer Experience | Customer Experience | Executive Sponsor | Accountable | High |
| SH-02 | Daniel Okafor | Director of E-commerce | Digital | Product Owner | Responsible | High |
| SH-03 | Mei Lin Chen | Head of Support Operations | Customer Support | Business Lead, Support | Responsible | High |
| SH-04 | Rafael Souza | Enterprise Architect | IT | Solution Architect | Responsible | Medium |
| SH-05 | Hannah Weber | Information Security Lead | IT Security | Security Reviewer | Consulted | Medium |
| SH-06 | Arjun Mehta | Finance Business Partner | Finance | Budget Owner | Consulted | High |
| SH-07 | Sofia Petrova | Legal and Compliance Counsel | Legal | Compliance Reviewer | Consulted | Medium |
| SH-08 | Tom Brennan | Warehouse and Logistics Manager | Operations | Business Lead, Returns | Responsible | Medium |
| SH-09 | Grace Kim | QA Lead | IT | Test Lead | Responsible | Low |
| SH-10 | Luis Ortega | Senior Project Manager | PMO | Project Manager | Responsible | Medium |

## 7. User Personas

| ID | Persona | Description | Goals |
|---|---|---|---|
| PE-01 | Registered Customer | Has an online account and at least one past order | Track orders, return items, and update details without calling |
| PE-02 | Guest Shopper | Bought without an account | Check order status using only the order number and email |
| PE-03 | Support Agent | Handles escalated contacts in CareDesk CRM | See what the customer already tried before the call |
| PE-04 | Support Supervisor | Oversees agents and handles privacy requests | Fulfil data requests on time and with an audit trail |

## 8. Business Requirements

| ID | Requirement | Priority | Objectives | Scope | Requested By |
|---|---|---|---|---|---|
| BR-01 | Customers can see the real-time status of every order without contacting support | High | BO-01, BO-02 | SC-01 | SH-03 |
| BR-02 | Customers can start and track returns and exchanges online | High | BO-01, BO-03 | SC-02 | SH-08 |
| BR-03 | Customers can manage their account details, addresses, and communication preferences | Medium | BO-01, BO-04 | SC-03 | SH-02 |
| BR-04 | Customers get instant answers to common questions 24/7 | High | BO-01, BO-02, BO-04 | SC-04 | SH-01 |
| BR-05 | Support agents see the customer's full self-service history when a case is escalated | Medium | BO-02, BO-04 | SC-05 | SH-03 |
| BR-06 | The portal meets legal obligations for data privacy and accessibility | High | BO-02 | SC-03 | SH-07 |

## 9. Functional Requirements

| ID | Requirement | Priority | Business Requirements | Owner | Systems | Terms |
|---|---|---|---|---|---|---|
| FR-001 | Display the customer's order list with status, items, and expected delivery date | High | BR-01 | SH-02 | SYS-01, SYS-02 | GL-01 |
| FR-002 | Show live carrier tracking events for shipped orders | High | BR-01 | SH-02 | SYS-05 | GL-01 |
| FR-003 | Send email and SMS notifications when order status changes | Medium | BR-01 | SH-02 | SYS-01 | GL-01 |
| FR-004 | Let customers start a return for eligible items within the return window and generate a prepaid label | High | BR-02 | SH-08 | SYS-01, SYS-07, SYS-05 | GL-02, GL-03 |
| FR-005 | Let customers exchange an item for another size or colour, subject to stock | Medium | BR-02 | SH-08 | SYS-01, SYS-07 | GL-02 |
| FR-006 | Refund the original payment method automatically once the warehouse receives the return | High | BR-02 | SH-06 | SYS-04, SYS-07 | GL-02 |
| FR-007 | Let customers update their profile, addresses, and contact preferences | Medium | BR-03 | SH-02 | SYS-06, SYS-02 | |
| FR-008 | Support sign-in with existing storefront credentials and optional multi-factor authentication | High | BR-03, BR-06 | SH-05 | SYS-06 | |
| FR-009 | Provide a searchable knowledge base of help articles | High | BR-04 | SH-03 | SYS-03 | GL-04 |
| FR-010 | Provide a virtual assistant that answers common questions and hands off to a live agent | High | BR-04, BR-05 | SH-03 | SYS-03 | GL-04 |
| FR-011 | Create a CRM case automatically on escalation, including the full self-service transcript | Medium | BR-05 | SH-03 | SYS-03 | |
| FR-012 | Let customers download or delete their personal data | High | BR-06 | SH-07 | SYS-06, SYS-03 | GL-05 |
| FR-013 | Let guest shoppers track an order using the order number and email | Medium | BR-01 | SH-02 | SYS-01 | GL-01 |

## 10. Non-Functional Requirements

| ID | Category | Requirement | Metric | Applies To | Owner |
|---|---|---|---|---|---|
| NFR-01 | Performance | Pages load quickly under normal peak load | p95 page load under 2 s at 2,000 concurrent users | FR-001, FR-002, FR-009 | SH-04 |
| NFR-02 | Availability | The portal is available around the clock | 99.9% monthly uptime, excluding planned maintenance | FR-001, FR-004, FR-010 | SH-04 |
| NFR-03 | Security | Personal data is encrypted at rest and in transit | AES-256 at rest; TLS 1.2 or higher in transit | FR-007, FR-008, FR-012 | SH-05 |
| NFR-04 | Accessibility | The portal is usable by customers with disabilities | WCAG 2.2 AA conformance | FR-001, FR-004, FR-007, FR-009 | SH-02 |
| NFR-05 | Privacy | Personal data handling complies with GDPR and CCPA | Data requests fulfilled within 30 days | FR-012 | SH-07 |
| NFR-06 | Scalability | The portal absorbs peak-season traffic | 3x Black Friday peak with no degradation | FR-001, FR-002 | SH-04 |
| NFR-07 | Auditability | Refunds and account changes are traceable | Every change logged with user, timestamp, and before and after values; retained 7 years | FR-006, FR-007 | SH-06 |

## 11. Use Cases

| ID | Use Case | Primary Actor | Trigger | Main Success Scenario | Realizes |
|---|---|---|---|---|---|
| UC-01 | Track an order | PE-01 | Customer wants to know when an order arrives | Customer signs in, opens the order, sees status and carrier events, and opts in to notifications | FR-001, FR-002, FR-003 |
| UC-02 | Track an order as a guest | PE-02 | Guest has an order number from the confirmation email | Guest enters order number and email and sees status and carrier events | FR-013, FR-002 |
| UC-03 | Return an item | PE-01 | Item is unwanted or damaged | Customer selects the item and reason, prints a prepaid label, ships it, and is refunded on receipt | FR-004, FR-006 |
| UC-04 | Exchange an item | PE-01 | Item is the wrong size or colour | Customer picks a replacement variant; the replacement ships when the return is scanned | FR-005 |
| UC-05 | Update account details | PE-01 | Customer moves house or changes contact preferences | Customer signs in with MFA and updates address and preferences | FR-007, FR-008 |
| UC-06 | Get help from the virtual assistant | PE-01 | Customer has a policy or product question | Assistant answers from the knowledge base or hands off to an agent | FR-009, FR-010 |
| UC-07 | Handle an escalated case | PE-03 | Assistant hands off a conversation | Agent opens the auto-created case, reads the transcript, and resolves the issue | FR-011 |
| UC-08 | Fulfil a data access request | PE-04 | Customer submits a data request | Supervisor verifies identity, exports or deletes the data, and closes the request within 30 days | FR-012 |

## 12. Systems and Integrations

| ID | System | Purpose | Type | Owner |
|---|---|---|---|---|
| SYS-01 | OrderHub OMS | System of record for orders, statuses, and returns | Internal | SH-04 |
| SYS-02 | Shopline Storefront | E-commerce platform that hosts the portal pages | Internal | SH-02 |
| SYS-03 | CareDesk CRM | Support case management and knowledge base | Vendor SaaS | SH-03 |
| SYS-04 | PayBridge Gateway | Payment processing and refunds | External | SH-06 |
| SYS-05 | ShipTrack Carrier API | Carrier tracking aggregation and return labels | External | SH-08 |
| SYS-06 | Meridian Identity Service | Customer authentication and single sign-on | Internal | SH-05 |
| SYS-07 | Warehouse Management System | Receiving, stock, and return inspection | Internal | SH-08 |

## 13. Dependencies

| ID | Dependency | Relies On | Blocks | Status | Owner |
|---|---|---|---|---|---|
| DEP-01 | ShipTrack contract extension to include the return-label API | SYS-05 | FR-004 | Pending | SH-08 |
| DEP-02 | OrderHub v5.2 upgrade that publishes real-time status events | SYS-01 | FR-001, FR-003 | In Progress | SH-04 |
| DEP-03 | PayBridge certification for the partial-refund API | SYS-04 | FR-006 | Not Started | SH-06 |
| DEP-04 | CareDesk API licence tier upgrade | SYS-03 | FR-010, FR-011 | Approved | SH-03 |

## 14. Assumptions

| ID | Assumption | Underpins | Owner |
|---|---|---|---|
| AS-01 | 70% of support contacts are order, return, or account queries that self-service can resolve | BR-01, BR-02, BR-03 | SH-03 |
| AS-02 | A valid email address is on file for 95% of orders | FR-003, FR-013 | SH-02 |
| AS-03 | Existing storefront credentials can be reused through single sign-on without a forced password reset | FR-008 | SH-04 |
| AS-04 | Carrier tracking data refreshes at least every 30 minutes | FR-002 | SH-08 |

## 15. Constraints

| ID | Type | Constraint | Limits | Owner |
|---|---|---|---|---|
| CON-01 | Budget | Capital spend is capped at $1.85M for FY27 | BR-04, BR-05 | SH-06 |
| CON-02 | Schedule | Go-live must precede the 1 October 2027 peak-season code freeze | MS-05, MS-06 | SH-10 |
| CON-03 | Technology | The portal must run on the approved AWS cloud and use the existing Identity Service | FR-008, NFR-03 | SH-04 |
| CON-04 | Regulatory | Refunds must follow PCI-DSS; card data is never stored in the portal | FR-006 | SH-05 |
| CON-05 | Resource | Support subject-matter experts are available for at most 20% of their time | FR-009, FR-010 | SH-03 |

## 16. Risks

| ID | Risk | Likelihood | Impact | Mitigation | Owner | Threatens |
|---|---|---|---|---|---|---|
| RK-01 | Carrier data latency shows customers inaccurate tracking | Medium | High | Show a "last updated" time and fall back to polling | SH-08 | BO-02, FR-002 |
| RK-02 | The OrderHub v5.2 upgrade slips and delays Release 1 | High | High | Early integration sandbox and weekly vendor checkpoint | SH-04 | MS-03 |
| RK-03 | Customers keep calling instead of using the portal | Medium | High | IVR deflection message and launch email campaign | SH-01 | BO-01 |
| RK-04 | The virtual assistant gives incorrect policy answers | Medium | Medium | Curated answers only; hand off to an agent after two failed intents | SH-03 | FR-010, BO-02 |
| RK-05 | A breach exposes customer personal data | Low | Critical | Penetration test before go-live and a web application firewall | SH-05 | NFR-03, BO-02 |
| RK-06 | Return abuse leads to refund fraud | Medium | Medium | Refund only after warehouse scan; limit return frequency per account | SH-06 | BO-04, FR-006 |

## 17. Success Metrics (KPIs)

| ID | KPI | Baseline | Target | Frequency | Measures | Owner |
|---|---|---|---|---|---|---|
| KPI-01 | Monthly inbound support calls | 48,000 | 31,200 | Monthly | BO-01 | SH-03 |
| KPI-02 | Self-service containment rate | 0% | 60% | Weekly | BO-01, BO-04 | SH-03 |
| KPI-03 | Post-purchase CSAT | 72% | 85% | Quarterly | BO-02 | SH-01 |
| KPI-04 | Average return cycle time | 9 days | 4 days | Monthly | BO-03 | SH-08 |
| KPI-05 | Cost-to-serve per order | $4.10 | $3.28 | Quarterly | BO-04 | SH-06 |
| KPI-06 | Share of orders tracked online | 0% | 55% | Monthly | BO-01 | SH-02 |

## 18. Timeline and Milestones

| ID | Milestone | Target Date | Delivers | After | Owner |
|---|---|---|---|---|---|
| MS-01 | Project kickoff and BRD sign-off | 2026-11-02 | | | SH-10 |
| MS-02 | Solution design approved | 2027-01-15 | | MS-01 | SH-04 |
| MS-03 | Release 1: order tracking and account management | 2027-05-03 | FR-001, FR-002, FR-003, FR-007, FR-008, FR-013 | MS-02 | SH-02 |
| MS-04 | Release 2: returns, exchanges, and refunds | 2027-07-12 | FR-004, FR-005, FR-006 | MS-03 | SH-08 |
| MS-05 | Release 3: knowledge base, assistant, CRM integration, and data requests | 2027-08-30 | FR-009, FR-010, FR-011, FR-012 | MS-04 | SH-03 |
| MS-06 | Hypercare complete and handover to operations | 2027-09-27 | | MS-05 | SH-10 |

## 19. Budget

| ID | Category | Amount (USD) | Funds | Owner | Notes |
|---|---|---|---|---|---|
| BUD-01 | Platform build | 820,000 | MS-03, MS-04, MS-05 | SH-10 | Vendor development, fixed price |
| BUD-02 | Integration and middleware | 310,000 | MS-03, MS-04 | SH-04 | OrderHub, WMS, PayBridge, ShipTrack connectors |
| BUD-03 | Software licences | 180,000 | MS-03, MS-05 | SH-04 | CareDesk tier upgrade and ShipTrack, first year |
| BUD-04 | Security and accessibility testing | 95,000 | MS-05 | SH-05 | Penetration test and WCAG audit |
| BUD-05 | Change management and training | 120,000 | MS-06 | SH-03 | Agent training and customer launch campaign |
| BUD-06 | Contingency | 175,000 | | SH-06 | About 10% of the build and integration cost |

Total budget: $1,700,000, within the $1.85M cap (CON-01).

## 20. Glossary

| ID | Term | Definition |
|---|---|---|
| GL-01 | Order Status | The lifecycle state of an order: Placed, Picked, Shipped, Out for Delivery, Delivered, or Cancelled |
| GL-02 | RMA | Return Merchandise Authorization; the record that approves a return and tracks it to refund |
| GL-03 | Return Window | The 30 days after delivery during which an item can be returned |
| GL-04 | Containment Rate | The share of self-service sessions resolved without contacting an agent |
| GL-05 | DSAR | Data Subject Access Request; a customer's legal request to access or delete their personal data |
| GL-06 | CSAT | Customer Satisfaction score; the share of surveyed customers rating their experience 4 or 5 out of 5 |
| GL-07 | Cost-to-Serve | Total support and fulfilment service cost divided by the number of orders |
| GL-08 | Hypercare | The period of heightened support immediately after go-live |

## 21. Approvals

| Stakeholder | Role | Decision | Date | Comments |
|---|---|---|---|---|
| SH-01 | Executive Sponsor | Approved | 2026-10-12 | |
| SH-02 | Product Owner | Approved | 2026-10-12 | |
| SH-04 | Solution Architect | Approved with comments | 2026-10-13 | Confirm the OrderHub v5.2 delivery date before design approval |
| SH-06 | Budget Owner | Approved | 2026-10-14 | |
| SH-07 | Compliance Reviewer | Approved | 2026-10-14 | |
