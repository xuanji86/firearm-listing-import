"""description.txt parsing: hard-wrapped prose must reach the store as one line
per paragraph, because the POS→Woo payload renders every newline as <br>."""
from __future__ import annotations

import unittest

from test_prod_gate import M  # stubbed-requests import of the script

WRAPPED = """Title: Egyptian Contract FN-49 8mm Mauser Crown Marked
This FN Model 1949 is an Egyptian contract rifle chambered in 8mm Mauser, made by
Fabrique Nationale in Belgium. The receiver carries the Egyptian crown marking. Condition
is near excellent: the stock shows virtually no dings or scratches and the rifle retains
almost all of its blued finish.

FN produced the type from 1949 to 1956, and more than 176,000 were built in total.
The rifle was offered to at least 26 nations and nine placed production orders.
Chamberings followed the customer: Belgium, the Belgian Congo, Brazil, Colombia,
Indonesia and Luxembourg took .30-06 Springfield, and Egypt took 7.92mm Mauser.

Specifications

Manufacturer: Fabrique Nationale (FN), Belgium
Country of origin: Belgium
Model: FN Model 1949 (FN-49 / SAFN), Egyptian contract
Bore: Bright
"""

FLAT = """One paragraph written on a single line, however long it gets to be.

Second paragraph, also one line.

Specifications
Title: Some Gun
Manufacturer: Tula
Condition: Very good
"""


class UnwrapParagraphs(unittest.TestCase):
    def test_wrapped_prose_becomes_one_line_per_paragraph(self):
        title, _flags, body, _bad = M.split_desc(WRAPPED)
        self.assertEqual(title, "Egyptian Contract FN-49 8mm Mauser Crown Marked")
        paras = body.split("\n\n")
        self.assertEqual(len(paras), 4)
        self.assertNotIn("\n", paras[0])
        self.assertIn("made by Fabrique Nationale in Belgium.", paras[0])
        # A prose line carrying a colon is still prose, and every line joins
        # even when the previous one ended a sentence (the wrap is per block).
        self.assertNotIn("\n", paras[1])
        self.assertIn("in total. The rifle was offered", paras[1])
        self.assertIn("customer: Belgium, the Belgian Congo, Brazil, Colombia, Indonesia", paras[1])
        self.assertEqual(paras[2], "Specifications")
        # Spec lines stay one per line.
        self.assertEqual(paras[3].split("\n"), [
            "Manufacturer: Fabrique Nationale (FN), Belgium",
            "Country of origin: Belgium",
            "Model: FN Model 1949 (FN-49 / SAFN), Egyptian contract",
            "Bore: Bright",
        ])

    def test_flat_file_is_unchanged(self):
        title, _flags, body, _bad = M.split_desc(FLAT)
        self.assertEqual(title, "Some Gun")
        self.assertEqual(body, FLAT.replace("Title: Some Gun\n", "").strip())

    def test_short_list_of_sentences_keeps_its_lines(self):
        body = "Comes with the original sling.\nBayonet included.\nBox is not included."
        self.assertEqual(M.unwrap_paragraphs(body), body)

    def test_heading_then_specs_without_blank_line(self):
        body = "Specifications\nManufacturer: Tula\nYear: 1954"
        self.assertEqual(M.unwrap_paragraphs(body), body)


if __name__ == "__main__":
    unittest.main()


CA_FLAGS = """CA Legal: Yes
Compliant Service: No
Title: Type 56 SKS 7.62x39

A clean Chinese SKS with matching numbers.
"""


class CaComplianceFlags(unittest.TestCase):
    """'CA Legal:' / 'Compliant Service:' answer the osa_ca_compliant fields on the
    Serial No. A parsed answer leaves the description (the store renders it from the
    field); anything that is not Yes/No stays in the prose and is reported, because a
    line that silently vanished would read as an answered gun."""

    def test_flags_are_parsed_and_removed_from_the_body(self):
        title, flags, body, bad = M.split_desc(CA_FLAGS)
        self.assertEqual(flags, {"osa_ca_legal": "Yes", "osa_compliant_service": "No"})
        self.assertEqual(title, "Type 56 SKS 7.62x39")
        self.assertNotIn("CA Legal", body)
        self.assertNotIn("Compliant Service", body)
        self.assertIn("matching numbers", body)
        self.assertEqual(bad, [])

    def test_case_and_spacing_are_forgiving(self):
        _t, flags, _b, _bad = M.split_desc("ca legal:yes\nCOMPLIANT SERVICE :  No\n")
        self.assertEqual(flags, {"osa_ca_legal": "Yes", "osa_compliant_service": "No"})

    def test_anywhere_in_the_file_not_just_the_top(self):
        _t, flags, _b, _bad = M.split_desc("Prose first.\n\nSpecifications\nCA Legal: No\n")
        self.assertEqual(flags, {"osa_ca_legal": "No"})

    def test_absent_lines_mean_no_write(self):
        """An unanswered file must not blank a counter-entered answer."""
        _t, flags, _b, bad = M.split_desc("Title: Something\n\nJust prose.\n")
        self.assertEqual(flags, {})
        self.assertEqual(bad, [])

    def test_a_bad_value_is_kept_as_prose_and_reported(self):
        _t, flags, body, bad = M.split_desc("CA Legal: maybe\n\nProse.\n")
        self.assertEqual(flags, {})
        self.assertEqual(bad, ["CA Legal: maybe"])
        self.assertIn("CA Legal: maybe", body)

    def test_first_answer_of_each_kind_wins(self):
        _t, flags, _b, _bad = M.split_desc("CA Legal: Yes\nCA Legal: No\n")
        self.assertEqual(flags, {"osa_ca_legal": "Yes"})

    def test_prose_mentioning_california_is_not_a_flag(self):
        _t, flags, body, bad = M.split_desc("This rifle is CA legal in most configurations.\n")
        self.assertEqual(flags, {})
        self.assertEqual(bad, [])
        self.assertIn("CA legal in most", body)
