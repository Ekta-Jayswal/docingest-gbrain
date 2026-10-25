# What this file does: tests the section graph drawn in each dashboard document entry: what it
# counts, which items it marks as shared by several sections, and when it says nothing can be drawn.

"""The dashboard's section graph, built from a document manifest."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from docingest.reporting import dashboard_page, document_graph


# A small manifest: three sections, two requirements and a risk. Sections 2 and 3 both mention FR-01.
def manifest() -> dict:
    def section(i: int, mentions: list[str]) -> dict:
        return {
            "slug": f"docs/sections/d/{i}-s",
            "type": "document_section",
            "title": f"Section {i}",
            "fields": {},
            "links": {"mentions_item": mentions} if mentions else {},
        }

    return {
        "sections": [
            {"index": i, "heading": f"Section {i}", "slug": f"docs/sections/d/{i}-s"}
            for i in range(3)
        ],
        "pages": [
            section(0, []),
            section(1, ["docs/fr/d/fr-01", "docs/fr/d/fr-02"]),
            section(2, ["docs/fr/d/fr-01"]),
            {"slug": "docs/fr/d/fr-01", "type": "functional_requirement", "title": "FR-01: Sync",
             "fields": {}, "links": {"owned_by": ["people/ravi-menon"]}},
            {"slug": "docs/fr/d/fr-02", "type": "functional_requirement", "title": "FR-02: Photo",
             "fields": {}, "links": {}},
            {"slug": "docs/risks/d/rk-01", "type": "risk", "title": "RK-01: Coverage",
             "fields": {"extracted_by": "ai"}, "links": {"references": ["docs/fr/d/fr-01"]}},
        ],
    }


# Checks for the SVG and the counts built from one manifest.
class RenderTest(unittest.TestCase):
    """What the graph draws and counts."""

    # An item that two sections mention is outlined as shared, and the counts match the manifest.
    def test_an_item_in_two_sections_is_marked_and_counted(self):
        svg, counts = document_graph.render(manifest())
        self.assertEqual(
            counts,
            {"sections": 3, "items": 3, "shared": 1, "links": 5, "left_out": 0},
        )
        self.assertEqual(svg.count("node req multi"), 1)
        self.assertIn("in 2 sections", svg)
        self.assertIn("Ravi Menon", svg)

    # An item the AI found is drawn dashed, and a typed link between items is labelled.
    def test_ai_items_are_dashed_and_item_links_are_labelled(self):
        svg, _ = document_graph.render(manifest())
        self.assertEqual(svg.count(" ai"), 1)
        self.assertIn(">references</text>", svg)

    # A manifest with no sections or no items has nothing to draw.
    def test_nothing_to_draw_returns_none(self):
        self.assertIsNone(document_graph.render({"sections": [], "pages": []}))

    # A document with more items than the limit draws the most connected and says how many it left out.
    def test_too_many_items_leaves_out_the_least_connected(self):
        m = manifest()
        for i in range(document_graph.MAX_ITEMS + 5):
            m["pages"].append({"slug": f"docs/x/d/x{i}", "type": "requirement", "title": f"X{i}",
                               "fields": {}, "links": {}})
        _, counts = document_graph.render(m)
        self.assertEqual(counts["items"], document_graph.MAX_ITEMS)
        self.assertEqual(counts["left_out"], 8)


# Checks for the block each dashboard entry shows under its sections.
class GraphBlockTest(unittest.TestCase):
    """Only the newest entry of a document shows the graph."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        (self.work / "manifests").mkdir()
        (self.work / "manifests" / "d.json").write_text(json.dumps(manifest()))

    def tearDown(self):
        self.tmp.cleanup()

    # The newest record gets the graph, an older record of the same document points to the newest.
    def test_only_the_newest_entry_draws_the_graph(self):
        old, new = {"key": "d"}, {"key": "d"}
        graph_for = dashboard_page._graph_links(self.work, [new])
        self.assertIn("<svg", graph_for(new))
        self.assertIn("newest entry", graph_for(old))

    # A document whose manifest is gone says so instead of failing.
    def test_a_missing_manifest_is_reported(self):
        rec = {"key": "gone"}
        graph_for = dashboard_page._graph_links(self.work, [rec])
        self.assertIn("No manifest", graph_for(rec))


if __name__ == "__main__":
    unittest.main()
