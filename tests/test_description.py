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
        title, body = M.split_title(WRAPPED)
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
        title, body = M.split_title(FLAT)
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
