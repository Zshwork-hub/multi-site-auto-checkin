from __future__ import annotations

import os
import types
import unittest
from unittest.mock import Mock, patch

import requests

import main
from signers.base import get_account_secrets, push_message
from signers.mihoyo import _sign_account


class DiscoveryTests(unittest.TestCase):
    def test_discovers_mihoyo(self) -> None:
        names = {entry.function.__name__ for entry in main.discover_signers()}
        self.assertIn("sign_mihoyo", names)

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
        results = _sign_account(account, "act-id", "hk4e_cn")

        self.assertEqual(len(results), 2)
        self.assertFalse(results[0][0])
        self.assertEqual(results[1], (True, "第二个角色成功"))


if __name__ == "__main__":
    unittest.main()
