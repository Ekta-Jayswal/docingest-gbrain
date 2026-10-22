# What this file does: tests PDF list handling. Markers become markdown list markers, list items
# are never headings, and no words are lost. The tests that build a real PDF need reportlab.

"""PDF lists: markers become markdown list markers and list items are never headings."""

from __future__ import annotations

import importlib.util
import re
import tempfile
import unittest
from pathlib import Path

from docingest.ingestion.convert_to_markdown import _list_marker, convert
from docingest.utils.markdown_parsing import token_coverage

# True when reportlab is installed, so the tests that build a PDF can run.
HAS_REPORTLAB = importlib.util.find_spec("reportlab") is not None
# A list item long enough to wrap onto a second line in the PDF.
LONG_ITEM = (
    "A deliberately long item that wraps onto a second line so that the converter has to join "
    "the rest of the item back to its first line instead of starting a new paragraph."
)


# A word as pdfplumber would report it: its text, font size, left position and font name.
def word(text: str, size: float, x0: float = 0.0, font: str = "Helvetica") -> dict:
    """A pdfplumber-like word."""
    return {"text": text, "size": size, "x0": x0, "fontname": font}


# Checks for what counts as a list marker at the start of a PDF line.
class ListMarkerTest(unittest.TestCase):
    """What counts as a list marker at the start of a PDF line."""

    # Bullet glyphs (a font code, a dot, a circle) are list markers, whatever their size.
    def test_glyph_bullets(self):
        for glyph in ("(cid:127)", "•", "●"):
            self.assertEqual(_list_marker([word(glyph, 12), word("Item", 10)]), "-")

    # A letter or number drawn larger than the text after it is a marker ('a.', '2.').
    def test_a_letter_in_a_different_size_is_a_marker(self):
        self.assertEqual(_list_marker([word("a", 12), word("Item", 10)]), "a.")
        self.assertEqual(_list_marker([word("2)", 12), word("Item", 10)]), "2.")

    # A number at the same size as the text ('1. Introduction') is text, not a marker.
    def test_same_size_is_text(self):
        self.assertIsNone(_list_marker([word("1.", 10), word("Introduction", 10)]))

    # A bullet with no text after it is not a marker.
    def test_a_marker_alone_is_not_a_marker(self):
        self.assertIsNone(_list_marker([word("•", 12)]))


# Converts a real PDF made by reportlab: bullets, lettered items, a wrapped item and one real
# heading.
@unittest.skipUnless(HAS_REPORTLAB, "reportlab is not installed (requirements-dev.txt)")
class PdfListsTest(unittest.TestCase):
    """A real PDF with bullets, lettered items, a wrapped item and one real heading."""

    # Build the PDF once for the class, convert it, and keep the markdown and reference text for
    # the tests.
    @classmethod
    def setUpClass(cls):
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import ListFlowable, Paragraph, SimpleDocTemplate

        styles = getSampleStyleSheet()
        body = styles["BodyText"]

        # A list of paragraphs, each marked with a bullet or a letter.
        def items(texts, kind):
            return ListFlowable(
                [Paragraph(t, body) for t in texts],
                bulletType=kind,
                start="a" if kind == "a" else None,
                leftIndent=14,
            )

        cls.tmp = Path(tempfile.mkdtemp()).resolve()
        pdf = cls.tmp / "lists.pdf"
        SimpleDocTemplate(str(pdf), pagesize=A4).build(
            [
                Paragraph("Scope of Work", styles["Heading2"]),
                Paragraph("The Contractor shall:", body),
                items(["Test every page.", LONG_ITEM, "Record each failure."], "a"),
                Paragraph("Standards", styles["Heading2"]),
                items(["Section 508 of the Act", "WCAG 2.2 level AA"], "bullet"),
            ]
        )
        conv, reference = convert(pdf, cls.tmp)
        cls.md, cls.reference = conv.markdown, reference

    # Font codes such as (cid:...) do not leak into the markdown.
    def test_no_font_codes_in_the_text(self):
        self.assertNotIn("(cid:", self.md)

    # Bullets become '-' items, and lettered items become 'a.' and 'c.' items.
    def test_bullets_and_lettered_items_become_list_items(self):
        self.assertRegex(self.md, r"(?m)^- Section 508 of the Act$")
        self.assertRegex(self.md, r"(?m)^a\. Test every page\.$")
        self.assertRegex(self.md, r"(?m)^c\. Record each failure\.$")

    # A long item that wraps onto a second line is joined into one list line.
    def test_a_wrapped_item_is_one_line(self):
        self.assertRegex(self.md, rf"(?m)^b\. {re.escape(LONG_ITEM)}$")

    # Only the two real headings are headings. List items are not.
    def test_only_real_headings_are_headings(self):
        headings = re.findall(r"(?m)^#+ (.+)$", self.md)
        self.assertEqual(headings, ["Scope of Work", "Standards"])

    # Every word of the PDF's text is present in the markdown.
    def test_no_words_are_lost(self):
        coverage, missing = token_coverage(self.reference, self.md)
        self.assertEqual((coverage, missing), (1.0, []))


if __name__ == "__main__":
    unittest.main()
