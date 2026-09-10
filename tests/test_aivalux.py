from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

import requests

from signers.aivalux import (
    CHECKIN_URL,
    LOGIN_URL,
    RED_PACKET_URL,
    STATUS_URL,
    AivaluxError,
    AuthTokens,
    _login,
    _refresh_access_token,
    _request,
    _safe_request_error,
    sign_aivalux,
)


def response(status: int, payload: dict) -> Mock:
    result = Mock()
    result.status_code = status
    result.json.return_value = payload
    return result


class AivaluxTests(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_missing_tokens_is_safe_skip(self) -> None:
        self.assertEqual(sign_aivalux(), (True, "未配置 Aivalux 凭据，已跳过"))

    @patch.dict(os.environ, {"AIVALUX_ACCESS_TOKEN": "access-token-value"}, clear=True)
    @patch("signers.aivalux.requests.Session")
    def test_already_signed_skips_checkin_and_claims_available_red_packet(
        self, session_factory: Mock
    ) -> None:
        session = session_factory.return_value.__enter__.return_value
        session.request.side_effect = [
            response(200, {"ok": True, "data": {
                "today": {"checked_in": True, "reward_choice": {"kind": "points", "quantity": 17}},
                "red_packet": {"claimed": False, "can_claim": True, "phase": "open"},
            }}),
            response(200, {"ok": True, "data": {
                "already_claimed": False,
                "reward_choice": {"kind": "points", "quantity": 2},
            }}),
        ]
        success, message = sign_aivalux()
        self.assertTrue(success)
        self.assertIn("今日已签到，17 积分", message)
        self.assertIn("红包领取成功，2 积分", message)
        self.assertEqual(session.request.call_args_list[0].args[:2], ("GET", STATUS_URL))
        self.assertEqual(session.request.call_args_list[1].args[:2], ("POST", RED_PACKET_URL))
        self.assertEqual(session.request.call_args_list[1].kwargs["json"], {})
        self.assertNotIn(CHECKIN_URL, [call.args[1] for call in session.request.call_args_list])

    @patch.dict(os.environ, {"AIVALUX_EMAIL": "only-email"}, clear=True)
    def test_partial_login_credentials_fail_safely(self) -> None:
        success, message = sign_aivalux()
        self.assertFalse(success)
        self.assertIn("必须同时配置", message)

    def test_login_uses_exact_payload_and_stores_tokens(self) -> None:
        session = Mock()
        session.post.return_value = response(200, {
            "code": 0,
            "message": "success",
            "data": {
                "access_token": "new-access-token",
                "refresh_token": "new-refresh-token",
                "expires_in": 86400,
            },
        })
        tokens = AuthTokens("", "", "user@example.com", "password-secret")

        _login(session, tokens)

        self.assertEqual(session.post.call_args.args[0], LOGIN_URL)
        self.assertEqual(session.post.call_args.kwargs["json"], {
            "email": "user@example.com",
            "password": "password-secret",
        })
        self.assertEqual(tokens.access_token, "new-access-token")
        self.assertEqual(tokens.refresh_token, "new-refresh-token")
        self.assertTrue(tokens.logged_in)

    def test_request_logs_in_when_tokens_are_missing(self) -> None:
        session = Mock()
        session.post.return_value = response(200, {
            "code": 0,
            "data": {
                "access_token": "login-access-token",
                "refresh_token": "login-refresh-token",
            },
        })
        session.request.return_value = response(200, {"ok": True, "data": {}})
        tokens = AuthTokens("", "", "user@example.com", "password-secret")

        payload = _request(session, tokens, "GET", STATUS_URL)

        self.assertTrue(payload["ok"])
        self.assertEqual(
            session.request.call_args.kwargs["headers"]["Authorization"],
            "Bearer login-access-token",
        )

    @patch.dict(os.environ, {"AIVALUX_ACCESS_TOKEN": "access-token-value"}, clear=True)
    @patch("signers.aivalux.requests.Session")
    def test_unsigned_user_checks_in_then_reloads_status(self, session_factory: Mock) -> None:
        session = session_factory.return_value.__enter__.return_value
        session.request.side_effect = [
            response(200, {"ok": True, "data": {"today": {"checked_in": False}}}),
            response(200, {"ok": True, "data": {
                "already_checked_in": False,
                "reward_choice": {"kind": "points", "quantity": 10},
            }}),
            response(200, {"ok": True, "data": {
                "today": {"checked_in": True},
                "red_packet": {"claimed": False, "can_claim": False, "phase": "closed"},
            }}),
        ]
        success, message = sign_aivalux()
        self.assertTrue(success)
        self.assertIn("签到成功，10 积分", message)
        self.assertIn("红包不可领取（closed）", message)
        self.assertEqual(
            [call.args[:2] for call in session.request.call_args_list],
            [("GET", STATUS_URL), ("POST", CHECKIN_URL), ("GET", STATUS_URL)],
        )
        self.assertEqual(session.request.call_args_list[1].kwargs["json"], {})

    @patch("signers.aivalux._refresh_access_token")
    def test_401_refreshes_once_without_leaking_tokens(self, refresh: Mock) -> None:
        session = Mock()
        tokens = AuthTokens("expired-access", "refresh-secret")
        session.request.side_effect = [
            response(401, {"ok": False, "error": {"message": "unauthorized"}}),
            response(200, {"ok": True, "data": {}}),
        ]
        def set_new_token(_session: Mock, auth: AuthTokens) -> None:
            auth.access_token = "new-access"
            auth.refreshed = True
        refresh.side_effect = set_new_token
        payload = _request(session, tokens, "GET", STATUS_URL)
        self.assertTrue(payload["ok"])
        self.assertEqual(refresh.call_count, 1)
        self.assertEqual(
            session.request.call_args_list[1].kwargs["headers"]["Authorization"],
            "Bearer new-access",
        )

    def test_request_exception_does_not_echo_secret(self) -> None:
        detail = _safe_request_error(
            requests.exceptions.InvalidHeader("invalid access-token-secret")
        )
        self.assertNotIn("access-token-secret", detail)


    def test_refresh_token_rotation_is_detected(self) -> None:
        session = Mock()
        session.post.return_value = response(200, {
            "code": 0,
            "data": {
                "access_token": "new-access-token",
                "refresh_token": "rotated-refresh-token",
            },
        })
        tokens = AuthTokens("expired-access-token", "old-refresh-token")

        _refresh_access_token(session, tokens)

        self.assertTrue(tokens.refreshed)
        self.assertTrue(tokens.refresh_token_rotated)
        self.assertEqual(tokens.access_token, "new-access-token")
        self.assertEqual(tokens.refresh_token, "rotated-refresh-token")
        self.assertEqual(session.post.call_args_list[0].kwargs["json"], {
            "refresh_token": "old-refresh-token"
        })

        session.post.return_value = response(200, {
            "code": 0,
            "data": {
                "access_token": "newer-access-token",
                "refresh_token": "rotated-refresh-token",
            },
        })
        _refresh_access_token(session, tokens)

        self.assertTrue(tokens.refresh_token_rotated)
        self.assertEqual(session.post.call_args_list[1].kwargs["json"], {
            "refresh_token": "rotated-refresh-token"
        })

    def test_remote_error_message_redacts_known_credentials(self) -> None:
        session = Mock()
        session.headers = {"Cookie": "cookie-secret"}
        session.request.return_value = response(400, {
            "ok": False,
            "error": {
                "code": "BAD_REQUEST",
                "message": "bad access-secret and cookie-secret",
            },
        })
        tokens = AuthTokens("access-secret", "")

        with self.assertRaises(AivaluxError) as caught:
            _request(session, tokens, "GET", STATUS_URL)

        detail = str(caught.exception)
        self.assertNotIn("access-secret", detail)
        self.assertNotIn("cookie-secret", detail)


    def _rotated_status(self, _session: Mock, tokens: AuthTokens, *args, **kwargs) -> dict:
        tokens.refreshed = True
        tokens.refresh_token_rotated = True
        return {
            "ok": True,
            "data": {
                "today": {"checked_in": True},
                "red_packet": {"claimed": False, "can_claim": False, "phase": "closed"},
            },
        }

    @patch.dict(os.environ, {
        "AIVALUX_ACCESS_TOKEN": "access-token",
        "AIVALUX_REFRESH_TOKEN": "old-refresh",
        "AIVALUX_EMAIL": "user@example.com",
        "AIVALUX_PASSWORD": "password-secret",
    }, clear=True)
    @patch("signers.aivalux._request")
    @patch("signers.aivalux.requests.Session")
    def test_rotation_with_login_fallback_remains_successful(
        self, _session_factory: Mock, request: Mock
    ) -> None:
        request.side_effect = self._rotated_status
        success, message = sign_aivalux()
        self.assertTrue(success)
        self.assertNotIn("请重新登录", message)

    @patch.dict(os.environ, {
        "AIVALUX_ACCESS_TOKEN": "access-token",
        "AIVALUX_REFRESH_TOKEN": "old-refresh",
    }, clear=True)
    @patch("signers.aivalux._request")
    @patch("signers.aivalux.requests.Session")
    def test_rotation_without_login_fallback_requires_secret_update(
        self, _session_factory: Mock, request: Mock
    ) -> None:
        request.side_effect = self._rotated_status
        success, message = sign_aivalux()
        self.assertFalse(success)
        self.assertIn("请重新登录并更新 GitHub Secrets", message)


if __name__ == "__main__":
    unittest.main()
