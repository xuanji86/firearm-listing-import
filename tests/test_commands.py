"""Command-level smoke tests for resolve/attach against a temp batch folder.

Issue #2: ece8af2 removed read_desc() but left its two call sites, and nothing
here ran cmd_resolve/cmd_attach, so 20 green tests shipped two dead commands.
POS lookups are patched out; `requests` is the no-network stub from test_prod_gate."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from unittest import mock

from test_prod_gate import M  # stubbed-requests import of the script

DESC = """Title: Egyptian Contract FN-49
This rifle is an Egyptian contract FN-49 chambered in
8mm Mauser. Condition is excellent.

Specifications
Bore: Bright
"""


def _batch(with_desc=True):
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "SN123"))
    if with_desc:
        with open(os.path.join(root, "SN123", "description.txt"), "w", encoding="utf-8") as fh:
            fh.write(DESC)
    return root


def _args(root, **kw):
    return argparse.Namespace(root=root, map=None, only=None, force=False, **kw)


def _run(fn, args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        fn(args)
    return out.getvalue()


class ResolveCommand(unittest.TestCase):
    def test_resolve_reads_title_from_description(self):
        root = _batch()
        with mock.patch.object(M, "resolve_serial", return_value=("SN123", "exact")), \
             mock.patch.object(M, "get_serial", return_value={"status": "Active", "sell_price": 100,
                                                              "item_code": "FN49"}):
            out = _run(M.cmd_resolve, _args(root))
        self.assertIn("Egyptian Contract FN-49", out)
        self.assertNotIn("NO-TITLE", out)
        self.assertNotIn("NO-DESC", out)

    def test_resolve_flags_missing_description(self):
        root = _batch(with_desc=False)
        with mock.patch.object(M, "resolve_serial", return_value=("SN123", "exact")), \
             mock.patch.object(M, "get_serial", return_value={"status": "Active", "sell_price": 100,
                                                              "item_code": "FN49"}):
            out = _run(M.cmd_resolve, _args(root))
        self.assertIn("NO-DESC", out)


class AttachCommand(unittest.TestCase):
    def test_attach_without_photos_puts_unwrapped_description_and_title(self):
        root = _batch()
        sent = {}

        def fake_put(url, headers=None, data=None, timeout=None):
            sent["url"], sent["body"] = url, json.loads(data)
            return mock.Mock(raise_for_status=lambda: None)

        with mock.patch.object(M, "resolve_serial", return_value=("SN123", "exact")), \
             mock.patch.object(M, "get_serial", return_value={"image_gallery": []}), \
             mock.patch.object(M.requests, "put", fake_put):
            out = _run(M.cmd_attach, _args(root))
        self.assertNotIn("ERROR", out)
        self.assertIn("SN123", sent["url"])
        self.assertEqual(sent["body"]["item_name"], "Egyptian Contract FN-49")
        body = sent["body"]["description"]
        self.assertNotIn("Title:", body)
        # hard-wrapped prose arrives as one line per paragraph (ece8af2's intent)
        self.assertIn("chambered in 8mm Mauser. Condition is excellent.", body)


if __name__ == "__main__":
    unittest.main()
