"""Tokenizer tests."""

from __future__ import annotations

import unittest

from docingest.mdutil import tokens


class TokensTest(unittest.TestCase):
    """Words stay separate and accents are folded."""

    def test_en_dash_separates_words(self):
        self.assertEqual(tokens("November–December"), ["november", "december"])

    def test_curly_quotes_and_hyphens(self):
        self.assertEqual(tokens("“go-live”"), ["go", "live"])

    def test_accents_are_folded_not_split(self):
        self.assertEqual(tokens("café résumé"), ["cafe", "resume"])


if __name__ == "__main__":
    unittest.main()
