"""The `login` session: which credentials win, and the token renewing itself
mid-batch (an access token lasts an hour; a batch of guns can outlast it)."""
from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import tempfile
import unittest
from unittest import mock

from test_prod_gate import M  # stubbed-requests import of the script


class _Resp:
    def __init__(self, ok, body=None, status=200):
        self.ok, self._body, self.status_code = ok, body or {}, status

    def json(self):
        return self._body


class Precedence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.auth = os.path.join(self.tmp, "auth.json")

    def test_explicit_key_file_wins_over_a_login(self):
        open(self.auth, "w").write("{}")
        with mock.patch.object(M, "AUTH_FILE", self.auth), \
                mock.patch.dict(os.environ, {"FIREARM_ENV": "/k/.env"}):
            self.assertEqual(M._find_env(), "/k/.env")

    def test_a_login_wins_over_a_repo_key_file(self):
        open(self.auth, "w").write("{}")
        with mock.patch.object(M, "AUTH_FILE", self.auth), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FIREARM_ENV", None)
            self.assertIsNone(M._find_env())


class Renewal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.auth_file = os.path.join(self.tmp, "cfg", "auth.json")
        self.auth = {"base": "https://pos.example.com", "client_id": "c1",
                     "token_endpoint": "https://pos.example.com/token",
                     "access_token": "old", "refresh_token": "r1", "expires_at": 0}
        self.h = {"Authorization": "Bearer old"}

    def _patched(self, post):
        return [mock.patch.object(M, "AUTH_FILE", self.auth_file),
                mock.patch.object(M, "AUTH", self.auth), mock.patch.object(M, "H", self.h),
                mock.patch.object(M.requests, "post", post)]

    def test_an_expiring_token_is_renewed_and_kept_private(self):
        post = mock.Mock(return_value=_Resp(True, {"access_token": "new", "expires_in": 3600}))
        with contextlib_all(self._patched(post)):
            self.assertEqual(M._h()["Authorization"], "Bearer new")
            M._h()  # fresh now: no second refresh
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs["data"]["grant_type"], "refresh_token")
        self.assertEqual(self.auth["refresh_token"], "r1", "kept when the POS sends none")
        self.assertEqual(stat.S_IMODE(os.stat(self.auth_file).st_mode), 0o600)

    def test_a_refused_renewal_stops_with_the_login_command(self):
        post = mock.Mock(return_value=_Resp(False, status=401))
        with contextlib_all(self._patched(post)), self.assertRaises(SystemExit) as e:
            M._h()
        self.assertIn("login https://pos.example.com", str(e.exception))

    def test_a_renewal_does_not_undo_a_login_to_another_store(self):
        os.makedirs(os.path.dirname(self.auth_file))
        with open(self.auth_file, "w") as fh:
            json.dump({"base": "https://pos.other.com", "client_id": "c2"}, fh)
        post = mock.Mock(return_value=_Resp(True, {"access_token": "new", "expires_in": 3600}))
        with contextlib_all(self._patched(post)):
            self.assertEqual(M._h()["Authorization"], "Bearer new")  # this batch carries on
        with open(self.auth_file) as fh:
            self.assertEqual(json.load(fh)["client_id"], "c2")

    def test_a_key_file_session_never_refreshes(self):
        post = mock.Mock()
        with mock.patch.object(M, "AUTH", None), mock.patch.object(M.requests, "post", post):
            M._h()
        post.assert_not_called()


class LoginFlow(unittest.TestCase):
    """The loopback callback: an idle browser preconnect and a favicon request
    must not use up the one answer the login waits for (codex r2)."""

    def test_the_real_callback_is_awaited_past_noise(self):
        import argparse, socket, threading, urllib.error, urllib.request
        from urllib.parse import parse_qs, urlparse
        meta = {"registration_endpoint": "R", "authorization_endpoint": "https://pos.example.com/auth",
                "token_endpoint": "T"}

        def get(url, **k):
            if url.endswith("oauth-authorization-server"):
                return _Resp(True, meta)
            return _Resp(True, {"message": "tom@example.com"})

        def post(url, **k):
            if url == "R":
                return _Resp(True, {"client_id": "c9"}, 201)
            self.assertEqual(k["data"]["code"], "the-code")
            return _Resp(True, {"access_token": "a", "refresh_token": "r", "expires_in": 3600})

        def browser(url):
            q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
            port = urlparse(q["redirect_uri"]).port

            def act():
                idle = socket.create_connection(("127.0.0.1", port))  # preconnect, never speaks
                for path in ("/favicon.ico", "/callback?state=wrong&code=x"):
                    with self.assertRaises(urllib.error.HTTPError):
                        urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5)
                urllib.request.urlopen(f"{q['redirect_uri']}?state={q['state']}&code=the-code",
                                       timeout=5).read()
                idle.close()
            threading.Thread(target=act, daemon=True).start()
            return True

        tmp = tempfile.mkdtemp()
        auth_file = os.path.join(tmp, "auth.json")
        with mock.patch.object(M.requests, "get", get), mock.patch.object(M.requests, "post", post), \
                mock.patch.object(M, "AUTH_FILE", auth_file), \
                mock.patch("webbrowser.open", browser), \
                contextlib.redirect_stdout(io.StringIO()):
            M.cmd_login(argparse.Namespace(url="https://pos.example.com/"))
        with open(auth_file) as fh:
            saved = json.load(fh)
        self.assertEqual((saved["base"], saved["client_id"], saved["access_token"]),
                         ("https://pos.example.com", "c9", "a"))


def contextlib_all(patches):
    import contextlib
    stack = contextlib.ExitStack()
    for p in patches:
        stack.enter_context(p)
    return stack


if __name__ == "__main__":
    unittest.main()
