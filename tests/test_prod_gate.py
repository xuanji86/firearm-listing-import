"""The production gate (AC-88) — the one thing standing between an agent
running a folder of guns and real firearms on the live GunBroker marketplace.

Why this file exists: the gate was verified by hand and it worked, but nothing
would have caught the next person editing cmd_push. A guard on a compliance
surface with no regression test is a guard with a shelf life.

Deliberately stdlib-only — `python3 -m unittest discover tests` with no
install, matching this repo's portable-single-script character. `requests` is
stubbed out at import: the module imports it at top level, and none of these
tests may make an HTTP request. If one ever tries, the stub raises rather than
silently reaching the network.

Run:  python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import pathlib
import sys
import tempfile
import types
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "firearm_listings.py"


def _load_module():
    """Import the script with `requests` stubbed and FIREARM_ENV pointed at a
    throwaway .env, since it reads config at import time."""
    stub = types.ModuleType("requests")

    def _no_network(*a, **k):  # pragma: no cover - only runs if a test regresses
        raise AssertionError("a test tried to make a real HTTP request")

    for verb in ("get", "post", "put", "delete", "request"):
        setattr(stub, verb, _no_network)
    sys.modules.setdefault("requests", stub)

    tmp = tempfile.mkdtemp()
    env = pathlib.Path(tmp) / "env"
    env.write_text("FRAPPE_BASE_URL=http://dev.localhost:8000\n"
                   "FRAPPE_API_KEY=dummy\nFRAPPE_API_SECRET=dummy\n")
    os.environ["FIREARM_ENV"] = str(env)

    spec = importlib.util.spec_from_file_location("firearm_listings", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load_module()


class IsLocalBase(unittest.TestCase):
    """Locality is decided on the parsed hostname. Substring matching on the URL
    is the trap this function exists to avoid."""

    LOCAL = [
        "http://localhost:8000",
        "http://dev.localhost:8000",
        "https://dev.localhost",
        "http://127.0.0.1:8000",
        "http://[::1]:8000",
        "http://DEV.LOCALHOST:8000",          # case is not a bypass
        "http://dev.localhost.:8000",         # fully-qualified spelling, same host
        "http://anything.localhost:8000",
    ]

    NOT_LOCAL = [
        "https://pos.oldsteelarsenal.com",
        "https://dev.localhost.evil.example.com",   # the substring trap
        "https://localhost.evil.example.com",
        "http://localhost@evil.example.com/",       # userinfo, real host is evil
        "http://evil.example.com/dev.localhost",    # path, not host
        "http://evil.example.com/?h=localhost",     # query, not host
        "dev.localhost:8000",                       # no scheme -> no hostname
        "",
        "not a url at all",
    ]

    def test_local_hosts(self):
        for url in self.LOCAL:
            with self.subTest(url=url):
                self.assertTrue(M._is_local_base(url), f"{url} should be local")

    def test_everything_else_is_production(self):
        for url in self.NOT_LOCAL:
            with self.subTest(url=url):
                self.assertFalse(M._is_local_base(url), f"{url} must NOT read as local")

    def test_unparseable_fails_closed(self):
        """Not "we couldn't tell, so proceed" — an unreadable BASE is treated as
        production, because that is the direction where being wrong is cheap."""
        for url in ("", "://///", "not a url at all", "dev.localhost:8000"):
            with self.subTest(url=url):
                self.assertFalse(M._is_local_base(url))


class ProdGate(unittest.TestCase):
    """_prod_gate_blocks returns True when the push must not happen."""

    def _blocks(self, base, channel, allow=None):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            old_base, old_allow = M.BASE, os.environ.get(M.ALLOW_PROD_ENV)
            try:
                M.BASE = base
                os.environ.pop(M.ALLOW_PROD_ENV, None)
                if allow is not None:
                    os.environ[M.ALLOW_PROD_ENV] = allow
                return M._prod_gate_blocks(channel), out.getvalue()
            finally:
                M.BASE = old_base
                os.environ.pop(M.ALLOW_PROD_ENV, None)
                if old_allow is not None:
                    os.environ[M.ALLOW_PROD_ENV] = old_allow

    PROD = "https://pos.example-not-real.test"
    LOCAL = "http://dev.localhost:8000"

    def test_gunbroker_against_production_is_refused(self):
        blocked, out = self._blocks(self.PROD, "gunbroker")
        self.assertTrue(blocked)
        self.assertIn("REFUSED", out)

    def test_the_refusal_says_what_to_do_instead(self):
        """A refusal nobody can act on gets worked around."""
        _, out = self._blocks(self.PROD, "gunbroker")
        self.assertIn("dev.localhost", out)
        self.assertIn(M.ALLOW_PROD_ENV, out)
        self.assertIn("sandbox_mode", out)

    def test_explicit_opt_in_allows_it_and_says_so_loudly(self):
        blocked, out = self._blocks(self.PROD, "gunbroker", allow="1")
        self.assertFalse(blocked)
        self.assertIn(M.ALLOW_PROD_ENV, out)

    def test_anything_other_than_exactly_1_fails_closed(self):
        """A typo in the opt-in must not read as consent."""
        for value in ("true", "TRUE", "yes", "0", "", " 1", "1 ", "01", "on"):
            with self.subTest(value=value):
                blocked, _ = self._blocks(self.PROD, "gunbroker", allow=value)
                self.assertTrue(blocked, f"{value!r} was accepted as an opt-in")

    def test_local_needs_no_opt_in(self):
        blocked, _ = self._blocks(self.LOCAL, "gunbroker")
        self.assertFalse(blocked)

    def test_woo_is_not_gated(self):
        """Woo has always been allowed against production, and a Woo mistake can
        be unpublished. Narrowing that is not this gate's job."""
        blocked, _ = self._blocks(self.PROD, "woo")
        self.assertFalse(blocked)

    def test_an_unreadable_base_is_treated_as_production(self):
        """Fail-closed at the GATE, not just in _is_local_base. If BASE is
        malformed we cannot prove it is local, and "we could not tell" must
        never resolve to "go ahead" for this particular action."""
        for base in ("", "dev.localhost:8000", "://///", "not a url at all"):
            with self.subTest(base=base):
                blocked, out = self._blocks(base, "gunbroker")
                self.assertTrue(blocked, f"{base!r} was allowed through")
                self.assertIn("REFUSED", out)

    def test_the_substring_lookalike_is_refused(self):
        blocked, _ = self._blocks("https://dev.localhost.evil.example.com", "gunbroker")
        self.assertTrue(blocked)


class ChannelTable(unittest.TestCase):
    def test_both_channels_are_present_and_woo_is_the_default(self):
        self.assertEqual(M.DEFAULT_CHANNEL, "woo")
        self.assertEqual(set(M.PUSH_CHANNELS), {"woo", "gunbroker"})

    def test_each_channel_carries_method_id_field_and_timeout(self):
        expected = {
            "woo": ("ffl_woo_sync.woocommerce.client_api.push_serial_now",
                    "woo_product_id", 240),
            "gunbroker": ("ffl_integrations.gunbroker.client_api.push_serial_now",
                          "gb_item_id", 180),
        }
        self.assertEqual(dict(M.PUSH_CHANNELS), expected)

    def test_the_channels_do_not_share_an_id_field(self):
        """push skips already-listed guns on this field. If the two channels
        shared one, listing on Woo would silently suppress the GunBroker push."""
        fields = [v[1] for v in M.PUSH_CHANNELS.values()]
        self.assertEqual(len(fields), len(set(fields)))


if __name__ == "__main__":
    unittest.main()
