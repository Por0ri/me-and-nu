import unittest

from ani_rag.wikidata_adapter import _parse_statements, _result_text


class WikidataAdapterParsingTests(unittest.TestCase):
    def test_result_text_prefers_structured_result(self):
        self.assertEqual(
            _result_text({
                "structuredContent": {"result": "Q1: Example — test item"},
                "content": [{"type": "text", "text": "fallback"}],
            }),
            "Q1: Example — test item",
        )

    def test_parses_values_qualifiers_and_reference_groups(self):
        text = (
            "Mikasa Ackerman (Q30311579): present in work (P1441): Attack on Titan (Q22126305)\n"
            "  Rank: normal\n"
            "  Qualifier:\n"
            "    - object has role (P3831): main subject (Q13442814)\n"
            "  Reference 1:\n"
            "    - stated in (P248): official guide (Q123)\n"
            "    - reference URL (P854): https://example.org/source\n"
            "\n"
            "Mikasa Ackerman (Q30311579): present in work (P1441): Attack on Titan (Q586025)\n"
            "  Rank: normal\n"
        )
        values = _parse_statements(text, "P1441", "Q30311579")
        self.assertEqual(len(values), 2)
        self.assertEqual(values[0].value, {"label": "Attack on Titan", "entity_id": "Q22126305"})
        self.assertEqual(values[0].qualifiers["rank"], "normal")
        self.assertEqual(values[0].qualifiers["P3831"]["value"], {
            "label": "main subject", "entity_id": "Q13442814",
        })
        self.assertEqual(values[0].references[0]["P854"]["value"], "https://example.org/source")

    def test_rejects_unrequested_property(self):
        with self.assertRaises(PermissionError):
            _parse_statements(
                "Mikasa (Q30311579): image (P18): File.png",
                "P1441", "Q30311579",
            )

    def test_rejects_appearance_statement_belonging_to_a_different_character(self):
        with self.assertRaises(PermissionError):
            _parse_statements(
                "Mikasa (Q30311579): present in work (P1441): Attack on Titan (Q22126305)",
                "P1441", "Q931",
            )


if __name__ == "__main__":
    unittest.main()
