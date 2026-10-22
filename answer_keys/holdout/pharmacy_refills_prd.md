# Automatic Prescription Refills

Product requirements document · Larkspur Pharmacy mobile app

- **Owner (product):** Nadia Okafor
- **Engineering lead:** Tomasz Wierzbicki
- **Design lead:** Hana Sato
- **Status:** In review
- **Last updated:** 2026-10-02

**Change history**

| Date | Author | What changed |
|---|---|---|
| 2026-09-10 | Nadia Okafor | First draft |
| 2026-09-24 | Nadia Okafor | Added the controlled-substance rule after pharmacist review |
| 2026-10-02 | Hana Sato | Linked the final designs |

## 1. Problem statement

About 38% of our app users take at least one long-term medicine. Today they must ask for every refill by hand, through the app or by phone. One request in five arrives after the patient has already run out, and store pharmacists spend part of every morning returning refill calls. Patients who run out often skip doses for several days.

## 2. Goals & success metrics

| Goal | Metric | Baseline | Target | Measured by | Date |
|---|---|---|---|---|---|
| Fewer patients run out of medicine | Share of refills requested after the previous supply ran out | 21% | 8% | Dispensing data, monthly | 2027-06-30 |
| Patients adopt automatic refills | Share of eligible prescriptions with automatic refills turned on | 0% | 35% | Product analytics | 2027-06-30 |
| Less phone work for pharmacists | Refill calls per store per week | 64 | 30 | Store call logs | 2027-09-30 |

## 3. Non-goals

- This phase does not include controlled substances, which still need a new prescription for each supply.
- This phase does not include home delivery; refills are collected in store.
- This phase does not include refills for family members managed from one account.

## 4. Users & personas

### Long-term patient

Takes two or more daily medicines for a chronic condition and collects them from the same store every month. Opens the app about once a week, mostly to check whether a refill is ready.

### Store pharmacist

Checks and releases every refill. Is legally responsible for not supplying medicine without a valid prescription, and wants fewer phone calls during the morning rush.

### Pharmacy technician

Prepares refills before the store opens and works from a printed list today.

## 5. User stories & acceptance criteria

### US-1: Turn on automatic refills

As a long-term patient, I want to turn on automatic refills for a prescription, so that I do not have to remember to ask.

Acceptance criteria:

- Given an eligible prescription with repeats left, when I turn on automatic refills, then the app shows the date of the next refill.
- Given a controlled substance, when I open the prescription, then the automatic refill switch is not shown.

### US-2: Know when a refill is ready

As a long-term patient, I want a notification when my refill is ready to collect, so that I make only one trip.

Acceptance criteria:

- Given automatic refills are on, when the pharmacist marks the refill ready, then I receive a push notification within five minutes.

### US-3: Skip one refill

As a long-term patient, I want to skip the next refill, so that I do not pay for medicine I still have.

Acceptance criteria:

- Given a refill is due more than 48 hours from now, when I tap Skip, then the refill moves to the following cycle.
- Given a refill is due less than 48 hours from now, when I tap Skip, then the app asks me to call the store.

### US-4: See the day's automatic refills

As a pharmacy technician, I want a list of today's automatic refills, so that I can prepare them before the store opens.

Acceptance criteria:

- Given it is 06:00 store time, when I open the refill queue, then every automatic refill due today is listed with the patient name and medicine.

### US-5: Stop refills when a prescription runs out

As a store pharmacist, I want refills to stop when no repeats remain, so that no patient is supplied without a valid prescription.

Acceptance criteria:

- Given the last repeat has been dispensed, when the next refill date arrives, then no refill is created and the patient is asked to contact their prescriber.

## 6. Functional requirements

1. **FR-1** Patients can turn automatic refills on or off for each eligible prescription. (US-1)
2. **FR-2** The app hides the automatic refill option for controlled substances. (US-1)
3. **FR-3** The refill is created in PharmaLink seven days before the current supply runs out. (US-1)
4. **FR-4** The app sends a push notification through Notify when the refill is marked ready. (US-2)
5. **FR-5** Patients can skip the next refill up to 48 hours before it is due. (US-3)
6. **FR-6** Technicians see a daily queue of automatic refills at 06:00 store time. (US-4)
7. **FR-7** No refill is created when the prescription has no repeats left. (US-5)

## 7. Non-functional requirements

- **Performance:** The refill list loads in under 1 second at p95 on a 4G connection.
- **Security & privacy:** Medicine names never appear in the text of a push notification.
- **Accessibility:** All new screens meet WCAG 2.2 AA and work with VoiceOver and TalkBack.
- **Availability:** Refill creation keeps working when the app backend is down, because it runs inside PharmaLink.

## 8. Dependencies & integrations

| System | What we need | Owner | Lead time |
|---|---|---|---|
| PharmaLink | Refill creation API and the "ready" status event | Pharmacy Systems team | 6 weeks |
| Notify | Push notification templates without medicine names | Messaging Platform team | 2 weeks |
| Identity service | A consent flag for automatic refills on the patient profile | Platform team | 3 weeks |

## 9. Milestones & phasing

| Phase | Scope | Exit criteria | Target date |
|---|---|---|---|
| Pilot | 12 stores in the Eastgate region; US-1 to US-4 | Fewer than 2% of automatic refills cancelled by pharmacists | 2027-01-29 |
| Regional launch | All 140 stores in the region; all stories | Crash-free sessions above 99.5% for two weeks | 2027-03-26 |
| National launch | All stores | Goal metrics on track at the 30-day review | 2027-05-28 |

## 10. Open questions & risks

| Question or risk | Owner | Needed by | Impact if unanswered |
|---|---|---|---|
| Do state pharmacy boards allow a refill to start without a fresh patient request each time? | Nadia Okafor | 2026-11-15 | Launch blocked in the affected states |
| Pharmacists may cancel automatic refills they think are not needed, which would erode patient trust. | Rafael Mendes | 2026-12-01 | Adoption target missed |
| Should a skipped refill count towards the insurer's refill window? | Nadia Okafor | 2026-11-30 | Claims rejected for some patients |

## 11. Appendix & links

- Designs: "Auto-refill v3" design file
- Pharmacist interview notes, August 2026
- Legal memo on state refill rules (LGL-2026-117)
