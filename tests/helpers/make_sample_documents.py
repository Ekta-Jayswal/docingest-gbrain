# What this file does: writes the sample input documents in every supported format (Word, PDF,
# email, subtitles, chat, CSV and more). All of them describe one fictional project, so people
# shared between documents can be checked.

"""Generate sample input documents in every supported format.

Usage: python tests/helpers/make_sample_documents.py   (writes into sample_documents/)

All samples describe the same fictional project (Project Lighthouse at
Meridian Home Goods) and share people, so shared-entity merging across
documents can be checked.
"""

import csv
import io
import re
from email.message import EmailMessage
from pathlib import Path

# Folder for the sample documents: sample_documents/.
OUT = Path(__file__).resolve().parents[2] / "sample_documents"


# ---- BRD as .docx (same content as lighthouse_brd.md) -------------------------------


# Convert a markdown sample into a Word document with the same headings and paragraphs.
def md_to_docx(md_path: Path, out: Path) -> None:
    import docx

    d = docx.Document()
    lines = md_path.read_text().splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            d.add_heading(m.group(2), level=len(m.group(1)))
        elif (
            line.startswith("|")
            and i + 1 < len(lines)
            and re.match(r"^\|[-| :]+\|$", lines[i + 1])
        ):
            header = [c.strip() for c in line.strip("|").split("|")]
            rows = []
            i += 2
            while i < len(lines) and lines[i].startswith("|"):
                rows.append([c.strip() for c in lines[i].strip("|").split("|")])
                i += 1
            table = d.add_table(rows=1 + len(rows), cols=len(header))
            table.style = "Table Grid"
            for c, h in enumerate(header):
                table.cell(0, c).text = h
            for r, row in enumerate(rows, 1):
                for c, v in enumerate(row[: len(header)]):
                    table.cell(r, c).text = v
            continue
        elif line.startswith("- "):
            d.add_paragraph(line[2:], style="List Bullet")
        elif line.strip():
            d.add_paragraph(line)
        i += 1
    d.save(out)


# ---- PRD as PDF ----------------------------------------------------------------------


# Build the product requirements PDF.
def make_prd_pdf(out: Path) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

    h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=20, leading=24, spaceAfter=12)
    h2 = ParagraphStyle(
        "h2", fontName="Helvetica-Bold", fontSize=14, leading=18, spaceBefore=12, spaceAfter=6
    )
    body = ParagraphStyle("body", fontName="Helvetica", fontSize=10, leading=13, spaceAfter=6)
    cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=8, leading=10)

    # Add a grid table of the given rows to the PDF.
    def table(rows):
        t = Table([[Paragraph(c, cell) for c in r] for r in rows], repeatRows=1)
        t.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        return t

    story = [
        Paragraph("Product Requirements Document: Lighthouse Mobile Returns", h1),
        Paragraph("Document Control", h2),
        table(
            [
                ["Field", "Value"],
                ["Document ID", "PRD-LH-003"],
                ["Product", "Lighthouse Mobile Returns"],
                ["Author", "Daniel Okafor"],
                ["Organization", "Meridian Home Goods"],
                ["Version", "0.9"],
                ["Status", "In Review"],
            ]
        ),
        Paragraph("Overview", h2),
        Paragraph(
            "Lighthouse Mobile Returns extends the customer self-service portal to phones. Customers "
            "photograph a damaged item, choose a refund or exchange, and receive a QR code that any "
            "carrier drop-off point can scan. The release targets the 41% of portal sessions that "
            "already come from mobile browsers.",
            body,
        ),
        Paragraph("Epics", h2),
        table(
            [
                ["ID", "Epic", "Owner"],
                ["EP-01", "Photo-based damage claims", "Tom Brennan"],
                ["EP-02", "QR code return labels", "Daniel Okafor"],
            ]
        ),
        Paragraph("User Stories", h2),
        table(
            [
                ["ID", "User Story", "Priority", "Epic", "Owner"],
                [
                    "US-01",
                    "As a customer, I want to photograph a damaged item so that I do not need to describe the damage",
                    "High",
                    "EP-01",
                    "Tom Brennan",
                ],
                [
                    "US-02",
                    "As a customer, I want an instant refund decision for items under $50 so that I am not kept waiting",
                    "High",
                    "EP-01",
                    "Arjun Mehta",
                ],
                [
                    "US-03",
                    "As a customer, I want a QR code instead of a printed label so that I can return without a printer",
                    "High",
                    "EP-02",
                    "Daniel Okafor",
                ],
                [
                    "US-04",
                    "As a support agent, I want to see the damage photos in CareDesk so that I can resolve disputes",
                    "Medium",
                    "EP-01",
                    "Mei Lin Chen",
                ],
            ]
        ),
        Paragraph("Acceptance Criteria", h2),
        table(
            [
                ["ID", "Criterion", "Verifies"],
                ["AC-01", "Photo upload succeeds on 3G connections within 15 seconds", "US-01"],
                ["AC-02", "QR codes are accepted by all four contracted carriers", "US-03"],
            ]
        ),
        Paragraph("Open Questions", h2),
        table(
            [
                ["ID", "Question", "Owner"],
                [
                    "OQ-01",
                    "Does the instant refund limit need Finance approval per region?",
                    "Arjun Mehta",
                ],
                ["OQ-02", "Which carriers support QR drop-off in rural areas?", "Tom Brennan"],
            ]
        ),
    ]
    SimpleDocTemplate(str(out), pagesize=A4, title="Lighthouse Mobile Returns PRD").build(story)


# ---- SOW as HTML -----------------------------------------------------------------------

# The statement of work, as an HTML page.
SOW_HTML = """<!doctype html>
<html><head><title>Statement of Work: Lighthouse Portal Build</title>
<style>table{border-collapse:collapse} td,th{border:1px solid #999;padding:4px}</style></head>
<body>
<h1>Statement of Work: Lighthouse Portal Build</h1>
<h2>Document Control</h2>
<table>
<tr><th>Field</th><th>Value</th></tr>
<tr><td>Document ID</td><td>SOW-LH-002</td></tr>
<tr><td>Client</td><td>Meridian Home Goods</td></tr>
<tr><td>Vendor</td><td>Brightline Digital</td></tr>
<tr><td>Author</td><td>Luis Ortega</td></tr>
<tr><td>Effective Date</td><td>2026-11-02</td></tr>
</table>
<h2>Background</h2>
<p>Brightline Digital will build the Lighthouse customer self-service portal described in
BRD-LH-001 for Meridian Home Goods. Work runs from November 2026 to September 2027 on a fixed-price basis.</p>
<h2>Deliverables</h2>
<table>
<tr><th>ID</th><th>Deliverable</th><th>Due Date</th><th>Owner</th><th>Depends On</th></tr>
<tr><td>DEL-01</td><td>Solution design document</td><td>2027-01-15</td><td>Rafael Souza</td><td></td></tr>
<tr><td>DEL-02</td><td>Release 1: order tracking and account management</td><td>2027-05-03</td><td>Daniel Okafor</td><td>DEL-01</td></tr>
<tr><td>DEL-03</td><td>Release 2: returns, exchanges, and refunds</td><td>2027-07-12</td><td>Tom Brennan</td><td>DEL-02</td></tr>
<tr><td>DEL-04</td><td>Release 3: knowledge base, assistant, and data requests</td><td>2027-08-30</td><td>Mei Lin Chen</td><td>DEL-03</td></tr>
</table>
<h2>Payment Schedule</h2>
<table>
<tr><th>ID</th><th>Payment</th><th>Amount (USD)</th><th>Delivers</th></tr>
<tr><td>PAY-01</td><td>Mobilisation fee</td><td>82,000</td><td>DEL-01</td></tr>
<tr><td>PAY-02</td><td>Release 1 acceptance</td><td>246,000</td><td>DEL-02</td></tr>
<tr><td>PAY-03</td><td>Release 2 acceptance</td><td>246,000</td><td>DEL-03</td></tr>
<tr><td>PAY-04</td><td>Release 3 acceptance and hypercare exit</td><td>246,000</td><td>DEL-04</td></tr>
</table>
<h2>Service Levels</h2>
<table>
<tr><th>ID</th><th>Service Level</th><th>Target</th></tr>
<tr><td>SLA-01</td><td>Severity 1 defect response during hypercare</td><td>1 hour</td></tr>
<tr><td>SLA-02</td><td>Portal availability during hypercare</td><td>99.9%</td></tr>
</table>
<h2>Acceptance Criteria</h2>
<table>
<tr><th>ID</th><th>Criterion</th><th>Verifies</th></tr>
<tr><td>AC-01</td><td>All High-priority requirements pass user acceptance testing</td><td>DEL-02, DEL-03, DEL-04</td></tr>
<tr><td>AC-02</td><td>No open Severity 1 or 2 defects at go-live</td><td>DEL-04</td></tr>
</table>
<h2>Approvals</h2>
<table>
<tr><th>Approver</th><th>Organization</th><th>Decision</th><th>Date</th></tr>
<tr><td>Priya Raman</td><td>Meridian Home Goods</td><td>Approved</td><td>2026-10-20</td></tr>
<tr><td>Arjun Mehta</td><td>Meridian Home Goods</td><td>Approved</td><td>2026-10-21</td></tr>
</table>
</body></html>
"""


# ---- email with CSV attachment ------------------------------------------------------------

# Rows of the vendor risk register, which is written as CSV.
RISK_ROWS = [
    ["ID", "Risk", "Likelihood", "Impact", "Owner", "Mitigation"],
    [
        "RISK-01",
        "Carrier QR scanning not supported in rural depots",
        "Medium",
        "High",
        "Tom Brennan",
        "Keep printed labels as a fallback",
    ],
    [
        "RISK-02",
        "Photo uploads fail on slow mobile networks",
        "High",
        "Medium",
        "Rafael Souza",
        "Compress images on the device before upload",
    ],
    [
        "RISK-03",
        "Instant refunds increase fraud",
        "Medium",
        "High",
        "Arjun Mehta",
        "Cap instant refunds at $50 and flag repeat claims",
    ],
]


# Write the approval email as a .eml file, with its headers and body.
def make_email(out: Path) -> None:
    buf = io.StringIO()
    csv.writer(buf).writerows(RISK_ROWS)
    msg = EmailMessage()
    msg["From"] = "Priya Raman <priya.raman@meridian.example>"
    msg["To"] = "Daniel Okafor <daniel.okafor@meridian.example>"
    msg["Cc"] = (
        "Mei Lin Chen <meilin.chen@meridian.example>, Arjun Mehta <arjun.mehta@meridian.example>"
    )
    msg["Subject"] = "Approval: Lighthouse BRD v1.2 and mobile returns risk register"
    msg["Date"] = "Tue, 13 Oct 2026 09:42:00 +0100"
    msg["Message-ID"] = "<lighthouse-approval-001@meridian.example>"
    msg.set_content(
        "Hi Daniel,\n\n"
        "I have approved BRD-LH-001 version 1.2. Please baseline it and share it with Brightline Digital "
        "before the statement of work is signed.\n\n"
        "Attached is the first risk register for Lighthouse Mobile Returns. Arjun owns the refund fraud "
        "risk; please make sure Finance signs off the $50 instant refund cap before design approval.\n\n"
        "Decision: the mobile returns work will follow Release 2, not run in parallel.\n\n"
        "Thanks,\nPriya"
    )
    msg.add_attachment(
        buf.getvalue().encode(),
        maintype="text",
        subtype="csv",
        filename="mobile_returns_risk_register.csv",
    )
    out.write_bytes(bytes(msg))


# ---- meeting transcript, chat, notes ---------------------------------------------------------

# The steering committee recording, as WebVTT subtitles.
VTT = """WEBVTT

00:00:05.000 --> 00:00:12.000
<v Priya Raman>Let's start. The goal today is to agree the go-live plan for Release 1.

00:00:12.500 --> 00:00:24.000
<v Rafael Souza>The OrderHub upgrade is still the critical path. The vendor now commits to the fourteenth of February.

00:00:24.500 --> 00:00:33.000
<v Daniel Okafor>If OrderHub lands in February we can still hit the third of May for Release 1.

00:00:33.500 --> 00:00:45.000
<v Hannah Weber>Security needs two weeks for the penetration test before go-live, so the test window must start by mid-April.

00:00:45.500 --> 00:00:55.000
<v Priya Raman>Agreed. Decision: Release 1 stays on the third of May. Rafael, please send weekly OrderHub status updates.

00:00:55.500 --> 00:01:04.000
<v Rafael Souza>Will do. I'll also set up the integration sandbox this month.
"""

# An operations team chat export, in the WhatsApp line format.
CHAT = """12/10/2026, 09:14 - Tom Brennan: Warehouse can scan QR returns from day one, tested with two carriers
12/10/2026, 09:16 - Mei Lin Chen: Great. Support needs the damage photos visible in CareDesk though
12/10/2026, 09:18 - Daniel Okafor: That's US-04, it's in the PRD as Medium priority
12/10/2026, 09:21 - Tom Brennan: Rural depots are the problem, two of four carriers can't scan QR yet
12/10/2026, 09:23 - Daniel Okafor: Then we keep printed labels as fallback, I'll update the PRD
12/10/2026, 09:30 - Mei Lin Chen: Can we get the agent training scheduled for April?
"""

# Release retrospective notes, as plain text.
NOTES = """Lighthouse retro notes, Release 0 prototype

The prototype ran for three weeks with 120 invited customers. Order tracking was the most used feature by a
wide margin: 86 of the 120 customers checked an order at least once, and 31 of them used carrier tracking
more than three times. Returns were tried by 22 customers; four abandoned the flow at the label step because
they had no printer at home, which supports the QR code idea in the mobile returns PRD.

The virtual assistant answered 64 percent of questions without a hand-off. The most common failure was
questions about delivery slots, which the knowledge base does not cover yet. Mei Lin will add twelve
articles on delivery slots before the Release 3 content freeze.

Performance was acceptable on desktop but slow on older Android phones; page loads averaged 3.4 seconds
against the two-second target. Rafael suspects the order list query and will profile it next sprint.
"""


# Write a long service policy handbook, so the handling of long documents is tested.
def make_long_policy(out: Path) -> None:
    """One long section, so a single page splits into many chunks."""
    topics = [
        "returns",
        "exchanges",
        "refunds",
        "order tracking",
        "account changes",
        "data requests",
        "delivery slots",
        "damaged items",
        "missing parcels",
        "gift orders",
        "bulk orders",
        "warranty",
    ]
    paras = []
    for n, topic in enumerate(topics * 3, 1):
        paras.append(
            f"Rule {n} covers {topic}. Agents confirm the customer's identity, check the order history in "
            f"OrderHub, and record every decision about {topic} in CareDesk with the reason code. If the "
            f"customer disagrees, the agent offers escalation to a supervisor within one working day, and the "
            f"supervisor replies in writing. Exceptions to rule {n} need approval from the Head of Support "
            f"Operations and are reviewed monthly for patterns."
        )
    out.write_text("# Customer Service Policy Handbook\n\n" + "\n\n".join(paras) + "\n")


# Write every sample document into sample_documents/.
def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    make_long_policy(OUT / "service_policy_handbook.md")
    md_to_docx(OUT / "lighthouse_brd.md", OUT / "lighthouse_brd_copy.docx")
    make_prd_pdf(OUT / "mobile_returns_prd.pdf")
    (OUT / "portal_build_sow.html").write_text(SOW_HTML)
    make_email(OUT / "brd_approval_email.eml")
    (OUT / "steering_committee.vtt").write_text(VTT)
    (OUT / "ops_team_chat.txt").write_text(CHAT)
    (OUT / "release0_retro_notes.txt").write_text(NOTES)
    with open(OUT / "vendor_risk_register.csv", "w", newline="") as f:
        csv.writer(f).writerows(
            [
                ["ID", "Risk", "Owner", "Status"],
                ["RISK-01", "Brightline Digital key developer attrition", "Luis Ortega", "Open"],
                ["RISK-02", "Late design sign-off delays Release 1", "Rafael Souza", "Monitoring"],
            ]
        )
    # A legacy Word file; real .doc conversion needs LibreOffice.
    (OUT / "legacy_spec.doc").write_bytes(b"\xd0\xcf\x11\xe0 not a real Word document")
    for p in sorted(OUT.iterdir()):
        print(f"{p.stat().st_size:>8}  {p.name}")


if __name__ == "__main__":
    main()
