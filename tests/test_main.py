from __future__ import annotations

import os
import types
import unittest
from unittest.mock import Mock, patch

import requests

import main
from signers.base import get_account_secrets, push_message
from signers.mihoyo import (
    GENSHIN,
    HONKAI_STAR_RAIL,
    ZENLESS_ZONE_ZERO,
    _headers,
    _request_error,
    _sign_game_account,
    _sign_role,
    sign_mihoyo,
)


class DiscoveryTests(unittest.TestCase):
    def test_discovers_mihoyo(self) -> None:
        names = {entry.function.__name__ for entry in main.discover_signers()}
        self.assertIn("sign_mihoyo", names)
        self.assertIn("sign_aivalux", names)

    def test_bad_signer_is_isolated(self) -> None:
        def sign_broken():
            raise RuntimeError("boom")

        result = main.run_signer(main.SignerEntry("坏站点", sign_broken))
        self.assertFalse(result.success)
        self.assertEqual(result.message, "boom")

    def test_broken_module_becomes_failed_signer(self) -> None:
        broken_info = types.SimpleNamespace(name="signers.broken")
        good_info = types.SimpleNamespace(name="signers.good")

        good_module = types.ModuleType("signers.good")

        def sign_good() -> tuple[bool, str]:
            return True, "ok"

        sign_good.__module__ = "signers.good"
        good_module.sign_good = sign_good

        with patch(
            "main.pkgutil.iter_modules", return_value=[broken_info, good_info]
        ), patch(
            "main.importlib.import_module",
            side_effect=[ImportError("missing dependency"), good_module],
        ):
            entries = main.discover_signers()
        self.assertEqual(len(entries), 2)
        self.assertIn("模块 broken", {entry.site_name for entry in entries})
        failed = next(entry for entry in entries if entry.site_name == "模块 broken")
        self.assertEqual(failed.function(), (False, "模块导入失败：missing dependency"))


class AccountConfigTests(unittest.TestCase):
    @patch.dict(
        os.environ,
        {
            "COOKIE_MIHOYO": "cookie-a",
            "COOKIES_JSON": '{"mihoyo":["cookie-a","cookie-b"]}',
            "ACCOUNTS_JSON": '[{"name":"三号","mihoyo":{"cookie":"cookie-c"}}]',
        },
        clear=True,
    )
    def test_combines_and_deduplicates_account_sources(self) -> None:
        accounts = get_account_secrets("mihoyo", "COOKIE_MIHOYO")
        self.assertEqual([account.value for account in accounts], ["cookie-a", "cookie-b", "cookie-c"])

    @patch.dict(os.environ, {"COOKIES_JSON": '{"mihoyo":[123]}'}, clear=True)
    def test_rejects_non_string_cookie(self) -> None:
        with self.assertRaisesRegex(ValueError, "第 1 项"):
            get_account_secrets("mihoyo", "COOKIE_MIHOYO")


class ReportTests(unittest.TestCase):
    def test_report_counts_and_escapes_remote_text(self) -> None:
        report = main.build_report(
            [
                main.SignerResult("站点A", True, "成功"),
                main.SignerResult("站点B", False, "bad | result"),
            ]
        )
        self.assertIn("✅ 成功: 1", report)
        self.assertIn("❌ 失败: 1", report)
        self.assertIn("bad \\| result", report)


class PushTests(unittest.TestCase):
    @patch.dict(os.environ, {"PUSHPLUS_TOKEN": "test-token"}, clear=True)
    @patch("signers.base.requests.post")
    def test_non_object_json_is_controlled_failure(self, post: Mock) -> None:
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = []
        post.return_value = response
        self.assertEqual(push_message("test"), (False, "PushPlus 返回了非对象 JSON"))


class MihoyoIsolationTests(unittest.TestCase):
    @patch("signers.mihoyo._sign_role")
    @patch("signers.mihoyo._json")
    @patch("signers.mihoyo.requests.Session")
    def test_role_failure_does_not_skip_later_roles(
        self, session_factory: Mock, parse_json: Mock, sign_role: Mock
    ) -> None:
        session = Mock()
        session_factory.return_value.__enter__.return_value = session
        parse_json.return_value = {
            "retcode": 0,
            "data": {
                "list": [
                    {"game_uid": "10001", "region": "cn_gf01"},
                    {"game_uid": "10002", "region": "cn_gf01"},
                ]
            },
        }
        sign_role.side_effect = [requests.Timeout("timeout"), (True, "第二个角色成功")]

        account = types.SimpleNamespace(name="测试账号", value="fake-cookie")
        with patch("signers.mihoyo._pause"):
            found, results = _sign_game_account(account, GENSHIN)

        self.assertTrue(found)
        self.assertEqual(len(results), 2)
        self.assertFalse(results[0][0])
        self.assertEqual(results[1], (True, "第二个角色成功"))

    @patch("signers.mihoyo._json")
    def test_already_signed_is_success(self, parse_json: Mock) -> None:
        parse_json.return_value = {
            "retcode": 0,
            "data": {"is_sign": True, "total_sign_day": 10},
        }
        session = Mock()
        account = types.SimpleNamespace(name="测试账号", value="fake-cookie")
        role = {"game_uid": "123456789", "region": "cn_gf01", "nickname": "旅行者"}

        result = _sign_role(session, account, role, GENSHIN)

        self.assertTrue(result[0])
        self.assertIn("原神", result[1])
        self.assertIn("今日已签到", result[1])
        session.post.assert_not_called()

    @patch("signers.mihoyo._json")
    def test_captcha_response_is_failure(self, parse_json: Mock) -> None:
        parse_json.side_effect = [
            {"retcode": 0, "data": {"is_sign": False, "total_sign_day": 0}},
            {"retcode": 0, "data": {"success": 1, "gt": "masked"}},
        ]
        session = Mock()
        account = types.SimpleNamespace(name="测试账号", value="fake-cookie")
        role = {"game_uid": "123456789", "region": "cn_gf01", "nickname": "旅行者"}

        with patch("signers.mihoyo._pause"):
            result = _sign_role(session, account, role, GENSHIN)

        self.assertFalse(result[0])
        self.assertIn("验证码", result[1])

    @patch("signers.mihoyo._sign_game_account")
    @patch("signers.mihoyo.get_account_secrets")
    def test_all_supported_games_are_auto_detected(
        self, get_secrets: Mock, sign_game: Mock
    ) -> None:
        get_secrets.return_value = [types.SimpleNamespace(name="默认账号", value="fake-cookie")]
        sign_game.side_effect = [
            (True, [(True, "原神已签到")]),
            (True, [(True, "星铁已签到")]),
            (True, [(True, "绝区零已签到")]),
        ]

        with patch("signers.mihoyo._pause"):
            success, message = sign_mihoyo()

        self.assertTrue(success)
        self.assertEqual(
            [call.args[1] for call in sign_game.call_args_list],
            [GENSHIN, HONKAI_STAR_RAIL, ZENLESS_ZONE_ZERO],
        )
        self.assertIn("原神已签到", message)
        self.assertIn("星铁已签到", message)
        self.assertIn("绝区零已签到", message)


class MihoyoContractTests(unittest.TestCase):
    def test_current_game_api_contracts(self) -> None:
        self.assertEqual(
            (
                GENSHIN.game_biz,
                GENSHIN.act_id,
                GENSHIN.info_url,
                GENSHIN.sign_url,
                GENSHIN.signgame,
            ),
            (
                "hk4e_cn",
                "e202311201442471",
                "https://api-takumi.mihoyo.com/event/luna/info?lang=zh-cn",
                "https://api-takumi.mihoyo.com/event/luna/sign",
                "hk4e",
            ),
        )
        self.assertEqual(
            (
                HONKAI_STAR_RAIL.game_biz,
                HONKAI_STAR_RAIL.act_id,
                HONKAI_STAR_RAIL.info_url,
            ),
            (
                "hkrpg_cn",
                "e202304121516551",
                "https://api-takumi.mihoyo.com/event/luna/info?lang=zh-cn",
            ),
        )
        self.assertEqual(
            (
                ZENLESS_ZONE_ZERO.game_biz,
                ZENLESS_ZONE_ZERO.act_id,
                ZENLESS_ZONE_ZERO.info_url,
                ZENLESS_ZONE_ZERO.sign_url,
                ZENLESS_ZONE_ZERO.signgame,
            ),
            (
                "nap_cn",
                "e202406242138391",
                "https://act-nap-api.mihoyo.com/event/luna/zzz/info?lang=zh-cn",
                "https://act-nap-api.mihoyo.com/event/luna/zzz/sign",
                "zzz",
            ),
        )

    @patch("signers.mihoyo._make_ds", side_effect=["ds-one", "ds-two"])
    def test_headers_use_fresh_ds_and_stable_device(self, make_ds: Mock) -> None:
        first = _headers("fake-cookie", GENSHIN)
        second = _headers("fake-cookie", GENSHIN)

        self.assertEqual(first["DS"], "ds-one")
        self.assertEqual(second["DS"], "ds-two")
        self.assertEqual(first["x-rpc-device_id"], second["x-rpc-device_id"])
        self.assertEqual(first["x-rpc-signgame"], "hk4e")
        self.assertEqual(make_ds.call_count, 2)

    def test_request_error_never_echoes_cookie(self) -> None:
        detail = _request_error(requests.exceptions.InvalidHeader("invalid secret-cookie-value"))
        self.assertNotIn("secret-cookie-value", detail)
        self.assertIn("InvalidHeader", detail)

    @patch("signers.mihoyo._json")
    @patch("signers.mihoyo.requests.Session")
    def test_unbound_game_is_skipped(self, session_factory: Mock, parse_json: Mock) -> None:
        session_factory.return_value.__enter__.return_value = Mock()
        parse_json.return_value = {"retcode": 0, "data": {"list": []}}

        found, results = _sign_game_account(
            types.SimpleNamespace(name="默认账号", value="fake-cookie"),
            HONKAI_STAR_RAIL,
        )

        self.assertFalse(found)
        self.assertEqual(results, [])

    @patch("signers.mihoyo._sign_game_account")
    @patch("signers.mihoyo.get_account_secrets")
    def test_partial_query_failure_is_aggregated(
        self, get_secrets: Mock, sign_game: Mock
    ) -> None:
        get_secrets.return_value = [
            types.SimpleNamespace(name="默认账号", value="fake-cookie")
        ]
        sign_game.side_effect = [
            ValueError("角色查询失败"),
            (False, []),
            (True, [(True, "绝区零今日已签到")]),
        ]

        with patch("signers.mihoyo._pause"):
            success, message = sign_mihoyo()

        self.assertFalse(success)
        self.assertIn("原神", message)
        self.assertIn("绝区零今日已签到", message)


if __name__ == "__main__":
    unittest.main()
