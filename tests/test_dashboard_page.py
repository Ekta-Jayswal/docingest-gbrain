# What this file does: tests how the dashboard counts distinct links for each run record, including
# older records that have no link keys and must be read from their manifest.

"""Dashboard link counting: from the record, or from the manifest for older runs."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from docingest.reporting import dashboard_page


# Checks for the distinct link keys of one run record.
class LinkKeysTest(unittest.TestCase):
    """The distinct "source|verb|target" keys of a run record."""

    # Set up a temporary work folder with an empty manifests folder.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        (self.work / "manifests").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    # Write a manifest for the document key, with the given pages.
    def write_manifest(self, key: str, pages: list[dict]) -> None:
        (self.work / "manifests" / f"{key}.json").write_text(json.dumps({"pages": pages}))

    # A record that already has link keys uses them as they are.
    def test_a_record_with_link_keys_uses_them(self):
        record = {"key": "doc1", "link_keys": ["a|owns|b"]}
        self.assertEqual(dashboard_page._link_keys(self.work, record), ["a|owns|b"])

    # An older record has no link keys, so its manifest is read. A link that two pages both hold
    # is counted once.
    def test_an_older_record_counts_unique_links_from_its_manifest(self):
        self.write_manifest(
            "doc1",
            [
                {"slug": "a", "links": {"owns": ["b", "c"]}},
                {"slug": "b", "links": {"owns": ["c"]}},
                {"slug": "a", "links": {"owns": ["b"]}},
            ],
        )
        keys = dashboard_page._link_keys(self.work, {"key": "doc1"})
        self.assertEqual(keys, ["a|owns|b", "a|owns|c", "b|owns|c"])

    # An older record with no manifest on disk has no links to count.
    def test_an_older_record_without_a_manifest_has_no_links(self):
        self.assertEqual(dashboard_page._link_keys(self.work, {"key": "missing"}), [])


if __name__ == "__main__":
    unittest.main()
