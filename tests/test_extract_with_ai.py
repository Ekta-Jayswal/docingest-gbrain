# What this file does: tests the checks an AI answer must pass before anything is written. The
# checks run without a model, so no keys or network are needed.

"""AI extraction: an item is kept only when its quote appears in the section, and a rejection
records the quote and the reason."""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from docingest.ingestion.extract_with_ai import LLMExtractor
from docingest.utils.data_types import Page
from docingest.utils.llm_client import LLMError
from docingest.vocabulary.vocabulary_loader import Vocabulary


# Checks for the evidence rule on items the AI returns.
class EvidenceTest(unittest.TestCase):
    """An item whose quote is not in the section is rejected, and the reason keeps the quote."""

    # Run the checks on one answer against one section, and return the report.
    def apply(self, text: str, data: dict) -> dict:
        # The constructor needs a model client, and these checks use none of it.
        extractor = LLMExtractor.__new__(LLMExtractor)
        report = {"accepted": 0, "rejected": 0, "rejections": []}
        extractor._apply(
            res=None,
            s=SimpleNamespace(index=0, heading="Parties"),
            text=text,
            data=data,
            known={},
            doc_slug="doc",
            section_page=None,
            pages_by_slug={},
            report=report,
        )
        return report

    # An entity whose quote is not in the section is rejected, and the reason shows the quote.
    def test_an_entity_quote_not_in_the_section_is_rejected_with_its_quote(self):
        data = {
            "entities": [
                {
                    "key": "e1",
                    "type": "customer",
                    "name": "Acme",
                    "attributes": [],
                    "evidence": "Acme pays monthly",
                }
            ],
            "relationships": [],
        }
        report = self.apply("The sky is blue.", data)
        self.assertEqual((report["accepted"], report["rejected"]), (0, 1))
        self.assertEqual(
            report["rejections"],
            [
                "section 1: entity 'Acme' (customer): evidence not found verbatim: "
                '"Acme pays monthly"'
            ],
        )

    # A relationship whose quote is not in the section is rejected, with its quote in the reason.
    def test_a_relationship_quote_not_in_the_section_is_rejected_with_its_quote(self):
        data = {
            "entities": [],
            "relationships": [
                {"from": "e1", "verb": "owns", "to": "e2", "evidence": "owns the system"}
            ],
        }
        report = self.apply("The sky is blue.", data)
        self.assertEqual((report["accepted"], report["rejected"]), (0, 1))
        self.assertEqual(
            report["rejections"],
            ['section 1: e1 --owns--> e2: evidence not found verbatim: "owns the system"'],
        )


# The second pass: links between items in different sections.
class LinkPassTest(unittest.TestCase):
    """A link the AI proposes is kept only when it names real items, fits the verb, quotes both
    ends and is new."""

    # Three items: an objective, a requirement serving it, and a scope item.
    def setUp(self):
        self.vocab = Vocabulary()
        gb = self.vocab.gbrain_type
        self.pages = [
            Page(slug="docs/objectives/d/bo-1", type=gb("business_objective"), title="BO-1: Cut permit time",
                 fields={"item_id": "BO-1", "section": "2. Goals"}, body="# BO-1: Cut permit time\n\nFrom D, section 2.\n\nCut permit time to 20 days.\n"),
            Page(slug="docs/functional-requirements/d/fr-1", type=gb("functional_requirement"), title="FR-1: Apply online",
                 fields={"item_id": "FR-1", "section": "6. Requirements"}, body="# FR-1: Apply online\n\nApplicants apply online.\n"),
            Page(slug="docs/scope/d/sc-1", type=gb("scope_item"), title="SC-1: Online forms",
                 fields={"item_id": "SC-1", "section": "3. Scope"}, body="# SC-1: Online forms\n\nOnline application forms.\n"),
        ]
        self.res = SimpleNamespace(pages=self.pages, title="D", kind="brd")

    # Run the pass with a fake model that returns this answer, and return the report.
    def run_pass(self, links: list[dict]) -> dict:
        extractor = LLMExtractor.__new__(LLMExtractor)
        extractor.v = self.vocab
        extractor.client = SimpleNamespace(
            complete=lambda *a: (json.dumps({"links": links}), {"input": 10, "output": 5})
        )
        report = {"rejections": [], "errors": [], "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0}
        extractor.link_pass(self.res, report)
        return report

    # One proposed link, with defaults that are valid.
    def link(self, **kw) -> dict:
        base = {"from": "FR-1", "verb": "achieves", "to": "BO-1", "from_quote": "Applicants apply online",
                "to_quote": "Cut permit time", "reason": "Online filing shortens the process."}
        return base | kw

    # A valid link is written on the source page and listed as inferred.
    def test_valid_link_is_added(self):
        report = self.run_pass([self.link()])
        self.assertEqual((report["links_accepted"], report["links_rejected"]), (1, 0))
        self.assertEqual(self.pages[1].links["achieves"], ["docs/objectives/d/bo-1"])
        self.assertIn("Inferred by the AI", self.pages[1].body)

    # Each way a link can be wrong is rejected with a reason.
    def test_bad_links_are_rejected(self):
        report = self.run_pass([
            self.link(**{"from": "FR-9"}),                       # unknown item
            self.link(from_quote="Applicants pay by cheque"),    # quote not in the item
            self.link(to_quote="Cut permit cost"),               # quote not in the item
            self.link(verb="verifies"),                          # verbs do not join these types
            self.link(**{"from": "SC-1", "from_quote": "Online application forms"}),
        ])
        self.assertEqual(report["links_accepted"], 1)  # only the last one is valid
        self.assertEqual(report["links_rejected"], 4)
        self.assertTrue(any("unknown" in r for r in report["rejections"]))
        self.assertEqual(sum("quote not found" in r for r in report["rejections"]), 2)

    # A relationship that already exists, in either direction, is not added twice.
    def test_existing_link_is_not_repeated(self):
        self.pages[1].add_link("references", self.pages[0].slug)
        report = self.run_pass([self.link()])
        self.assertEqual((report["links_accepted"], report["links_rejected"]), (0, 1))
        self.assertIn("already linked", report["rejections"][0])

    # A failed request is reported and changes nothing.
    def test_model_failure_is_reported(self):
        extractor = LLMExtractor.__new__(LLMExtractor)
        extractor.v = self.vocab

        def boom(*a):
            raise LLMError("proxy down")

        extractor.client = SimpleNamespace(complete=boom)
        report = {"rejections": [], "errors": [], "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0}
        extractor.link_pass(self.res, report)
        self.assertEqual(report["errors"], ["link pass: proxy down"])
        self.assertEqual(self.pages[1].links, {})


if __name__ == "__main__":
    unittest.main()
