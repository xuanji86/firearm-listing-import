"""The `login` session: which credentials win, and the token renewing itself
mid-batch (an access token lasts an hour; a batch of guns can outlast it)."""
from __future__ import annotations

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


def contextlib_all(patches):
    import contextlib
    stack = contextlib.ExitStack()
    for p in patches:
        stack.enter_context(p)
    return stack


if __name__ == "__main__":
    unittest.main()
