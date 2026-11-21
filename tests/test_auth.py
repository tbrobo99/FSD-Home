"""Tests for the optional demo password gate.

No network, no hardware, no LLM. The gate is off unless DEMO_PASSWORD is set,
so local development and the existing suite are untouched.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import auth  # noqa: E402

PW = "demo-test-password"


class AuthToggleTest(unittest.TestCase):
    def test_off_by_default(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(auth.enabled())
            self.assertEqual(auth.password(), "")

    def test_enabled_when_env_set(self):
        with mock.patch.dict(os.environ, {auth.ENV_PASSWORD: PW}, clear=True):
            self.assertTrue(auth.enabled())

    def test_whitespace_password_is_off(self):
        with mock.patch.dict(os.environ, {auth.ENV_PASSWORD: "   "}, clear=True):
            self.assertFalse(auth.enabled())


class CredentialTest(unittest.TestCase):
    def test_correct_password(self):
        with mock.patch.dict(os.environ, {auth.ENV_PASSWORD: PW}, clear=True):
            self.assertTrue(auth.check(PW))

    def test_wrong_password(self):
        with mock.patch.dict(os.environ, {auth.ENV_PASSWORD: PW}, clear=True):
            self.assertFalse(auth.check("wrong"))

    def test_check_is_off_when_disabled(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(auth.check(PW))


class TokenTest(unittest.TestCase):
    def test_round_trip(self):
        with mock.patch.dict(os.environ, {auth.ENV_PASSWORD: PW}, clear=True):
            token = auth.issue_token()
            self.assertTrue(auth.verify_token(token))

    def test_tampered_token_rejected(self):
        with mock.patch.dict(os.environ, {auth.ENV_PASSWORD: PW}, clear=True):
            token = auth.issue_token()
            payload, _, sig = token.rpartition(".")
            self.assertFalse(auth.verify_token(payload + ".deadbeef"))

    def test_expired_token_rejected(self):
        with mock.patch.dict(os.environ, {auth.ENV_PASSWORD: PW}, clear=True):
            import json
            import base64
            import hashlib
            import hmac
            payload = base64.urlsafe_b64encode(
                json.dumps({"exp": 1}).encode()
            ).decode().rstrip("=")
            sig = hmac.new(auth._secret(), payload.encode(), hashlib.sha256).hexdigest()
            self.assertFalse(auth.verify_token(f"{payload}.{sig}"))

    def test_password_rotation_invalidates_token(self):
        with mock.patch.dict(os.environ, {auth.ENV_PASSWORD: PW}, clear=True):
            token = auth.issue_token()
        with mock.patch.dict(os.environ, {auth.ENV_PASSWORD: "new-password"}, clear=True):
            self.assertFalse(auth.verify_token(token))

    def test_cookie_has_security_flags(self):
        c = auth.cookie_header("tok", secure=True)
        self.assertIn("HttpOnly", c)
        self.assertIn("SameSite=Lax", c)
        self.assertIn("Secure", c)
        self.assertIn(f"Max-Age={auth.SESSION_TTL}", c)


if __name__ == "__main__":
    unittest.main()
