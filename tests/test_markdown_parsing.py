# What this file does: tests the markdown helpers. It checks the word tokenizer used to compare
# documents, and the step that turns bold pseudo-headings into real headings.

"""Tokenizer tests."""

from __future__ import annotations

import unittest

from docingest.utils.markdown_parsing import tokens


# Checks for the tokenizer that compares words across documents.
class TokensTest(unittest.TestCase):
    """Words stay separate and accents are folded."""

    # An en dash joins two words. They must stay two tokens, not one.
    def test_en_dash_separates_words(self):
        self.assertEqual(tokens("November–December"), ["november", "december"])

    # Curly quotes are dropped, and a hyphen splits a word into two tokens.
    def test_curly_quotes_and_hyphens(self):
        self.assertEqual(tokens("“go-live”"), ["go", "live"])

    # Accented letters are folded to plain letters ('café' becomes 'cafe'), not split apart.
    def test_accents_are_folded_not_split(self):
        self.assertEqual(tokens("café résumé"), ["cafe", "resume"])


# Checks for turning bold pseudo-headings into real headings.
class HeadingNormalizationTest(unittest.TestCase):
    """Bold numbered lines become headings; documents with real headings are untouched."""

    # Bold title and numbered lines become #, ## and ### headings, by their depth.
    def test_bold_numbers_become_headings(self):
        from docingest.ingestion.normalize_headings import normalize_headings

        md = "**Business Requirements Document**\n\n**1. Scope**\n\ntext\n\n**1.1 In Scope**\n"
        out, n = normalize_headings(md)
        self.assertEqual(n, 3)
        self.assertIn("# Business Requirements Document\n", out)
        self.assertIn("## 1. Scope\n", out)
        self.assertIn("### 1.1 In Scope\n", out)

    # A document that already has real headings is returned unchanged.
    def test_real_headings_are_left_alone(self):
        from docingest.ingestion.normalize_headings import normalize_headings

        md = "# Title\n\n## One\n\n**2. Bold**\n"
        self.assertEqual(normalize_headings(md), (md, 0))


if __name__ == "__main__":
    unittest.main()
