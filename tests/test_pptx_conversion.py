# What this file does: tests PowerPoint conversion. The title slide names the document, slide bullets
# become list items, subtitles stay plain, tables and speaker notes are kept.
"""PowerPoint conversion: titles, bullets, tables and speaker notes."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from docingest.ingestion.convert_to_markdown import convert


# Build a small deck: a title slide, a bulleted slide with notes, and a table slide.
def make_deck(path: Path) -> None:
    """Write a three-slide deck to path for the tests."""
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[0])
    s.shapes.title.text = "Project Lighthouse: Steering Committee"
    s.placeholders[1].text = "Meridian Home Goods"
    s = prs.slides.add_slide(prs.slide_layouts[1])
    s.shapes.title.text = "Objectives"
    tf = s.placeholders[1].text_frame
    tf.text = "Cut delivery time to 3 days"
    tf.add_paragraph().text = "Reduce returns by 20%"
    s.notes_slide.notes_text_frame.text = "Agreed in September."
    s = prs.slides.add_slide(prs.slide_layouts[5])
    s.shapes.title.text = "Risks"
    table = s.shapes.add_table(2, 2, Inches(0.5), Inches(1.8), Inches(8), Inches(1)).table
    table.cell(0, 0).text = "ID"
    table.cell(0, 1).text = "Risk"
    table.cell(1, 0).text = "RK-1"
    table.cell(1, 1).text = "Carrier delays"
    prs.save(path)


# Checks for PowerPoint conversion, against a small deck built for the test.
class PptxConversionTest(unittest.TestCase):
    # Convert the deck once and keep the markdown for the tests.
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        deck = self.tmp / "steerco.pptx"
        make_deck(deck)
        self.conv, _ = convert(deck, self.tmp)
        self.md = self.conv.markdown

    # Remove the temporary folder after each test.
    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # The title slide's title becomes the document heading, not the file name.
    def test_title_slide_names_the_document(self):
        self.assertTrue(self.md.startswith("# Project Lighthouse: Steering Committee\n"))
        self.assertNotIn("steerco", self.md.splitlines()[0])

    # Slide bullets are list items, indented by level.
    def test_bullets_are_list_items(self):
        self.assertIn("- Cut delivery time to 3 days\n", self.md)
        self.assertIn("- Reduce returns by 20%\n", self.md)

    # A subtitle stays plain text, not a list item.
    def test_subtitle_is_plain(self):
        self.assertIn("Meridian Home Goods", self.md)
        self.assertNotIn("- Meridian Home Goods", self.md)

    # Tables and speaker notes are kept.
    def test_table_and_notes_kept(self):
        self.assertIn("| RK-1 | Carrier delays |", self.md)
        self.assertIn("**Speaker notes:** Agreed in September.", self.md)


if __name__ == "__main__":
    unittest.main()
