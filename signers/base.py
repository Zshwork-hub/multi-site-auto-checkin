"""签到器共用的日志、配置和 PushPlus 推送工具。"""

from __future__ import annotations

import html
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests


LOGGER = logging.getLogger("checkin")
# 北京自 1991 年后不实行夏令时；固定 UTC+8 可避免 Windows 缺少 IANA tzdata。
BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
PUSHPLUS_URL = "http://www.pushplus.plus/send"
DEFAULT_TIMEOUT = 15


@dataclass(frozen=True)
class AccountSecret:
    """一个账号的显示名称和密钥值；value 永远不应写入日志。"""

    name: str
    value: str


def setup_logging(log_dir: str = "logs") -> Path:
    """同时输出到控制台和文件，并返回日志文件路径。"""

    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    log_file = directory / "checkin.log"

    LOGGER.setLevel(logging.INFO)
    LOGGER.handlers.clear()
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    LOGGER.addHandler(console)
    LOGGER.addHandler(file_handler)
    return log_file


def beijing_now() -> datetime:
    """返回北京时间，避免依赖 GitHub Runner 的系统时区。"""

    return datetime.now(BEIJING_TZ)


def _parse_json_env(name: str) -> Any:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"环境变量 {name} 不是合法 JSON: {exc.msg}") from exc


def get_account_secrets(site: str, env_name: str) -> list[AccountSecret]:
    """读取单账号或 JSON 多账号密钥。

    支持以下来源，结果会自动去重：
    1. ``COOKIE_MIHOYO`` 这类直接环境变量；
    2. ``COOKIES_JSON={\"mihoyo\": [\"cookie1\", \"cookie2\"]}``；
    3. ``ACCOUNTS_JSON=[{\"name\": \"主账号\", \"mihoyo\": {\"cookie\": \"...\"}}]``。
    """

    accounts: list[AccountSecret] = []
    direct = os.getenv(env_name, "").strip()
    if direct:
        accounts.append(AccountSecret("默认账号", direct))

    cookies_json = _parse_json_env("COOKIES_JSON")
    if cookies_json is not None:
        if not isinstance(cookies_json, dict):
            raise ValueError("COOKIES_JSON 必须是 JSON 对象")
        values = cookies_json.get(site, cookies_json.get(env_name, []))
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, list):
            raise ValueError(f"COOKIES_JSON 中 {site} 的值必须是字符串或数组")
        for index, value in enumerate(values, start=1):
            if not isinstance(value, str):
                raise ValueError(f"COOKIES_JSON 中 {site} 的第 {index} 项必须是字符串")
            if value.strip():
                accounts.append(AccountSecret(f"JSON账号{index}", value.strip()))

    accounts_json = _parse_json_env("ACCOUNTS_JSON")
    if accounts_json is not None:
        if not isinstance(accounts_json, list):
            raise ValueError("ACCOUNTS_JSON 必须是 JSON 数组")
        for index, item in enumerate(accounts_json, start=1):
            if not isinstance(item, dict):
                raise ValueError(f"ACCOUNTS_JSON 第 {index} 项必须是对象")
            label = str(item.get("name") or f"账号{index}")
            value = item.get(env_name)
            nested = item.get(site)
            if not value and isinstance(nested, dict):
                value = nested.get("cookie")
            if isinstance(value, str) and value.strip():
                accounts.append(AccountSecret(label, value.strip()))

    # 使用密钥值去重，但不将密钥本身暴露到日志或异常文本。
    unique: list[AccountSecret] = []
    seen: set[str] = set()
    for account in accounts:
        if account.value not in seen:
            seen.add(account.value)
            unique.append(account)
    return unique


def safe_message(value: Any, limit: int = 300) -> str:
    """清理远端文本和常见敏感查询参数，避免污染日志与汇总消息。"""

    text = str(value or "未知原因")
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = "".join(character if character.isprintable() else " " for character in text)
    text = re.sub(r"(?i)(uid=)[^&\s]+", r"\1***", text).strip()
    return text[:limit]


def push_message(content: str, title: str = "多站签到报告") -> tuple[bool, str]:
    """通过 PushPlus 推送 Markdown 消息。"""

    token = os.getenv("PUSHPLUS_TOKEN", "").strip()
    if not token:
        return False, "未配置 PUSHPLUS_TOKEN"

    payload = {
        "token": token,
        "title": title,
        "content": content,
        "template": "markdown",
    }
    try:
        response = requests.post(PUSHPLUS_URL, json=payload, timeout=DEFAULT_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            return False, "PushPlus 返回了非对象 JSON"
        if data.get("code") == 200:
            return True, safe_message(data.get("msg") or "推送成功")
        return False, safe_message(data.get("msg") or f"PushPlus 返回 code={data.get('code')}")
    except (requests.RequestException, ValueError) as exc:
        return False, safe_message(exc)


def markdown_escape(text: str) -> str:
    """转义来自远端的少量 Markdown/HTML 特殊字符。"""

    return html.escape(text, quote=False).replace("|", "\\|")
