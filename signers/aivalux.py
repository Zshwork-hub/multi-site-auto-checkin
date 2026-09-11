"""Aivalux 每日签到与可领取红包。

仅执行免费签到，以及状态接口明确标记 can_claim=true 的红包领取。
不会执行抽奖、补签、兑换、周礼包或任何积分/余额消费操作。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional

import requests

from .base import DEFAULT_TIMEOUT, LOGGER, safe_message


API_BASE = "https://www.aivalux.com/qiandao/api"
STATUS_URL = f"{API_BASE}/status"
CHECKIN_URL = f"{API_BASE}/check-in"
RED_PACKET_URL = f"{API_BASE}/red-packet/claim"
REFRESH_URL = f"{API_BASE}/refresh"
LOGIN_URL = "https://www.aivalux.com/api/v1/auth/login"
SITE_ORIGIN = "https://www.aivalux.com"
CHECKIN_PAGE_URL = f"{SITE_ORIGIN}/qiandao/"
LOGIN_REFERER = f"{SITE_ORIGIN}/login"


class AivaluxError(RuntimeError):
    """Aivalux 返回的可安全展示错误。"""

    def __init__(self, message: str, code: str = "REQUEST_FAILED", status: int = 0):
        super().__init__(safe_message(message))
        self.code = code
        self.status = status


@dataclass
class AuthTokens:
    access_token: str
    refresh_token: str
    email: str = ""
    password: str = ""
    refreshed: bool = False
    refresh_token_rotated: bool = False
    logged_in: bool = False


def _safe_request_error(exc: requests.RequestException) -> str:
    """避免异常文本回显 Authorization、Cookie 或完整请求 URL。"""

    if isinstance(exc, requests.Timeout):
        return "请求超时"
    if isinstance(exc, requests.ConnectionError):
        return "连接失败"
    if isinstance(exc, requests.HTTPError):
        status = exc.response.status_code if exc.response is not None else "未知"
        return f"HTTP 错误 {status}"
    return f"请求异常（{exc.__class__.__name__}）"


def _safe_api_message(
    value: Any,
    session: requests.Session,
    tokens: AuthTokens,
) -> str:
    """从服务端错误文本中移除当前已知凭据。"""

    text = str(value or "请求失败，请稍后再试。")
    secrets = [
        tokens.access_token,
        tokens.refresh_token,
        tokens.email,
        tokens.password,
        str(session.headers.get("Cookie") or ""),
    ]
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return safe_message(text)


def _decode_json(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise AivaluxError("接口返回的不是合法 JSON", status=response.status_code) from exc
    if not isinstance(payload, dict):
        raise AivaluxError("接口返回的不是 JSON 对象", status=response.status_code)
    return payload


def _refresh_access_token(session: requests.Session, tokens: AuthTokens) -> None:
    if not tokens.refresh_token:
        raise AivaluxError("登录状态已失效，请更新 AIVALUX_ACCESS_TOKEN", status=401)
    try:
        response = session.post(
            REFRESH_URL,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json={"refresh_token": tokens.refresh_token},
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise AivaluxError(f"刷新登录状态失败：{_safe_request_error(exc)}") from exc
    payload = _decode_json(response)
    data = payload.get("data")
    if response.status_code >= 400 or payload.get("code") != 0 or not isinstance(data, dict):
        raise AivaluxError("登录状态刷新失败，请重新获取 Token", status=response.status_code)
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise AivaluxError("刷新接口未返回 Access Token")
    tokens.access_token = access_token.strip()
    new_refresh_token = data.get("refresh_token")
    if isinstance(new_refresh_token, str) and new_refresh_token.strip():
        normalized_refresh_token = new_refresh_token.strip()
        tokens.refresh_token_rotated = (
            tokens.refresh_token_rotated
            or normalized_refresh_token != tokens.refresh_token
        )
        tokens.refresh_token = normalized_refresh_token
    tokens.refreshed = True


def _login(session: requests.Session, tokens: AuthTokens) -> None:
    """使用 GitHub Secrets 中的账号密码获取新的 Token。"""

    if not tokens.email or not tokens.password:
        raise AivaluxError("缺少可用登录凭据，请更新 Aivalux Secrets", status=401)
    try:
        response = session.post(
            LOGIN_URL,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            json={"email": tokens.email, "password": tokens.password},
            timeout=DEFAULT_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise AivaluxError(f"账号密码登录失败：{_safe_request_error(exc)}") from exc
    payload = _decode_json(response)
    data = payload.get("data")
    if response.status_code >= 400 or payload.get("code") != 0 or not isinstance(data, dict):
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        raw_message = error.get("message") or payload.get("message") or "账号密码登录失败"
        raise AivaluxError(
            _safe_api_message(raw_message, session, tokens),
            code=str(error.get("code") or "LOGIN_FAILED"),
            status=response.status_code,
        )
    access_token = data.get("access_token")
    if not isinstance(access_token, str) or not access_token.strip():
        raise AivaluxError("登录接口未返回 Access Token")
    tokens.access_token = access_token.strip()
    refresh_token = data.get("refresh_token")
    tokens.refresh_token = refresh_token.strip() if isinstance(refresh_token, str) else ""
    tokens.logged_in = True
    tokens.refresh_token_rotated = False


def _authenticate(session: requests.Session, tokens: AuthTokens) -> None:
    """优先刷新 Token；无法刷新时才使用账号密码登录。"""

    if tokens.refresh_token:
        try:
            _refresh_access_token(session, tokens)
            return
        except AivaluxError:
            if not tokens.email or not tokens.password:
                raise
    _login(session, tokens)


def _request(
    session: requests.Session,
    tokens: AuthTokens,
    method: str,
    url: str,
    *,
    json_body: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """发送鉴权请求；遇到一次 401 时刷新或重新登录后重试。"""

    for attempt in range(2):
        if not tokens.access_token:
            _authenticate(session, tokens)
        headers = {"Accept": "application/json", "Authorization": f"Bearer {tokens.access_token}"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        try:
            response = session.request(
                method, url, headers=headers, json=json_body, timeout=DEFAULT_TIMEOUT
            )
        except requests.RequestException as exc:
            raise AivaluxError(_safe_request_error(exc)) from exc
        if response.status_code == 401 and attempt == 0:
            tokens.access_token = ""
            _authenticate(session, tokens)
            continue
        payload = _decode_json(response)
        if response.status_code >= 400 or payload.get("ok") is not True:
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            raw_message = error.get("message") or f"接口请求失败（HTTP {response.status_code}）"
            message = _safe_api_message(raw_message, session, tokens)
            code = str(error.get("code") or "REQUEST_FAILED")
            raise AivaluxError(message, code=code, status=response.status_code)
        return payload
    raise AivaluxError("重新认证后登录状态仍然无效", status=401)


def _reward_text(data: dict[str, Any]) -> str:
    choice = data.get("reward_choice") if isinstance(data.get("reward_choice"), dict) else {}
    kind = choice.get("kind")
    quantity = choice.get("quantity")
    if kind == "points" and quantity is not None:
        return f"{quantity} 积分"
    if kind == "card" and quantity is not None:
        return f"{quantity} 张抽奖卡"
    reward = data.get("reward")
    if reward not in (None, "", "0", "0.00", 0):
        return f"${reward} 余额"
    return "奖励已到账"


def _red_packet_result(
    session: requests.Session,
    tokens: AuthTokens,
    status_data: dict[str, Any],
) -> tuple[bool, str]:
    red_packet = status_data.get("red_packet")
    if not isinstance(red_packet, dict):
        return True, "红包状态不可用，已跳过"
    if red_packet.get("claimed"):
        return True, "今日红包已领取"
    if red_packet.get("can_claim") is not True:
        phase = safe_message(red_packet.get("phase") or "当前不可领取", limit=50)
        return True, f"红包不可领取（{phase}），已跳过"
    try:
        payload = _request(session, tokens, "POST", RED_PACKET_URL, json_body={})
    except AivaluxError as exc:
        if exc.code == "RED_PACKET_DEPLETED":
            return True, "红包已被抢完"
        return False, f"红包领取失败：{exc}"
    result = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    prefix = "今日红包已领取" if result.get("already_claimed") else "红包领取成功"
    return True, f"{prefix}，{_reward_text(result)}"


def sign_aivalux() -> tuple[bool, str]:
    """
    网站名称：Aivalux
    签到说明：每日签到，并在状态明确允许时领取免费红包。
    """

    access_token = os.getenv("AIVALUX_ACCESS_TOKEN", "").strip()
    refresh_token = os.getenv("AIVALUX_REFRESH_TOKEN", "").strip()
    email = (os.getenv("AIVALUX_EMAIL", "").strip()
             or os.getenv("AIVALUX_USERNAME", "").strip())
    password = os.getenv("AIVALUX_PASSWORD", "").strip()
    cookie = os.getenv("AIVALUX_COOKIE", "").strip()
    if bool(email) != bool(password):
        return False, "AIVALUX_EMAIL 和 AIVALUX_PASSWORD 必须同时配置"
    if not access_token and not refresh_token and not email:
        return True, "未配置 Aivalux 凭据，已跳过"
    tokens = AuthTokens(access_token, refresh_token, email, password)
    with requests.Session() as session:
        session.headers.update(
            {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
             "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
             "Origin": SITE_ORIGIN,
             "Referer": CHECKIN_PAGE_URL,
            }
        )
        if cookie:
            session.headers["Cookie"] = cookie
        try:
            status_payload = _request(session, tokens, "GET", STATUS_URL)
            status_data = status_payload.get("data")
            if not isinstance(status_data, dict):
                raise AivaluxError("状态接口 data 字段格式错误")
            today = status_data.get("today") if isinstance(status_data.get("today"), dict) else {}
            if today.get("checked_in"):
                checkin_message = f"今日已签到，{_reward_text(today)}"
            else:
                checkin_payload = _request(session, tokens, "POST", CHECKIN_URL, json_body={})
                result = checkin_payload.get("data")
                if not isinstance(result, dict):
                    raise AivaluxError("签到接口 data 字段格式错误")
                prefix = "今日已签到" if result.get("already_checked_in") else "签到成功"
                checkin_message = f"{prefix}，{_reward_text(result)}"
                status_payload = _request(session, tokens, "GET", STATUS_URL)
                status_data = status_payload.get("data")
                if not isinstance(status_data, dict):
                    raise AivaluxError("签到后状态接口 data 字段格式错误")
            red_packet_ok, red_packet_message = _red_packet_result(session, tokens, status_data)
            auth_message = "；已使用账号密码重新登录" if tokens.logged_in else (
                "；登录状态已自动刷新" if tokens.refreshed else ""
            )
            message = f"{checkin_message}；{red_packet_message}{auth_message}"
            if tokens.refresh_token_rotated and not (tokens.email and tokens.password):
                message += "；Refresh Token 已轮换，请重新登录并更新 GitHub Secrets"
                LOGGER.warning("❌ [Aivalux] %s", message)
                return False, message
            LOGGER.info("%s [Aivalux] %s", "✅" if red_packet_ok else "❌", message)
            return red_packet_ok, message
        except AivaluxError as exc:
            LOGGER.warning("❌ [Aivalux] %s", exc)
            return False, str(exc)
