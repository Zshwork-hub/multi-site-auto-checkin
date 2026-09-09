"""米游社（原神国服）签到示例。

Cookie 通常会过期，请定期更新 GitHub Secret ``COOKIE_MIHOYO``。
本模块不会在日志或推送中输出 Cookie 原文。
"""

from __future__ import annotations

import hashlib
import os
import random
import string
import time
import uuid
from typing import Any

import requests

from .base import DEFAULT_TIMEOUT, LOGGER, AccountSecret, get_account_secrets, safe_message


ROLE_URL = "https://api-takumi.mihoyo.com/binding/api/getUserGameRolesByCookie"
INFO_URL = "https://api-takumi.mihoyo.com/event/bbs_sign_reward/info"
HOME_URL = "https://api-takumi.mihoyo.com/event/bbs_sign_reward/home"
SIGN_URL = "https://api-takumi.mihoyo.com/event/bbs_sign_reward/sign"
DEFAULT_ACT_ID = "e202009291139501"
DEFAULT_GAME_BIZ = "hk4e_cn"
APP_VERSION = "2.33.1"
CLIENT_TYPE = "4"
DS_SALT = "1OUn34iIy84ypu9cpXyun2VaQ2zuFeLm"


def _make_ds() -> str:
    """生成米游社国服接口常用的动态 DS 校验头。"""

    timestamp = int(time.time())
    random_text = "".join(random.choices(string.ascii_lowercase + string.digits, k=6))
    digest = hashlib.md5(
        f"salt={DS_SALT}&t={timestamp}&r={random_text}".encode("utf-8")
    ).hexdigest()
    return f"{timestamp},{random_text},{digest}"


def _headers(cookie: str) -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
        "Cookie": cookie,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://webstatic.mihoyo.com",
        "Referer": "https://webstatic.mihoyo.com/",
        "DS": _make_ds(),
        "x-rpc-app_version": APP_VERSION,
        "x-rpc-client_type": CLIENT_TYPE,
        "x-rpc-device_id": uuid.uuid4().hex,
    }


def _json(response: requests.Response) -> dict[str, Any]:
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("接口返回的不是 JSON 对象")
    return data


def _api_error(data: dict[str, Any]) -> str:
    return safe_message(data.get("message") or f"retcode={data.get('retcode')}")


def _sign_role(
    session: requests.Session,
    account: AccountSecret,
    role: dict[str, Any],
    act_id: str,
) -> tuple[bool, str]:
    region = safe_message(role.get("region", ""), limit=50)
    uid = str(role.get("game_uid", ""))
    nickname = safe_message(role.get("nickname") or "未命名角色", limit=50)
    account_name = safe_message(account.name, limit=50)
    masked_uid = f"{uid[:2]}***{uid[-2:]}" if len(uid) >= 5 else "***"
    label = f"{account_name}/{nickname}({region}, UID {masked_uid})"
    params = {"act_id": act_id, "region": region, "uid": uid}

    info = _json(session.get(INFO_URL, params=params, timeout=DEFAULT_TIMEOUT))
    if info.get("retcode") != 0:
        return False, f"{label}: 查询状态失败（{_api_error(info)}）"
    info_data = info.get("data") or {}
    if info_data.get("is_sign"):
        return True, f"{label}: 今日已签到"

    total_sign_day = int(info_data.get("total_sign_day") or 0)
    result = _json(
        session.post(
            SIGN_URL,
            params={"act_id": act_id},
            json={"act_id": act_id, "region": region, "uid": uid},
            timeout=DEFAULT_TIMEOUT,
        )
    )
    # -5003 是米游社常见的“今天已经签到”返回码，按成功处理。
    if result.get("retcode") == -5003:
        return True, f"{label}: 今日已签到"
    if result.get("retcode") != 0:
        return False, f"{label}: 签到失败（{_api_error(result)}）"

    reward = ""
    try:
        home = _json(session.get(HOME_URL, params={"act_id": act_id}, timeout=DEFAULT_TIMEOUT))
        awards = (home.get("data") or {}).get("awards") or []
        if total_sign_day < len(awards):
            item = awards[total_sign_day]
            reward = f"，获得 {item.get('name', '奖励')}×{item.get('cnt', '?')}"
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        # 奖励查询失败不改变已经成功的签到结果。
        LOGGER.warning("[%s] 签到成功，但奖励信息查询失败", label)
    return True, f"{label}: 签到成功{reward}"


def _sign_account(account: AccountSecret, act_id: str, game_biz: str) -> list[tuple[bool, str]]:
    with requests.Session() as session:
        session.headers.update(_headers(account.value))
        roles_data = _json(
            session.get(
                ROLE_URL,
                params={"game_biz": game_biz},
                timeout=DEFAULT_TIMEOUT,
            )
        )
        if roles_data.get("retcode") != 0:
            return [(False, f"{safe_message(account.name)}: 获取角色失败（{_api_error(roles_data)}）")]

        payload = roles_data.get("data") or {}
        if not isinstance(payload, dict):
            raise ValueError("角色接口 data 字段不是对象")
        roles = payload.get("list") or []
        if not isinstance(roles, list):
            raise ValueError("角色接口 list 字段不是数组")
        if not roles:
            return [(False, f"{safe_message(account.name)}: 未找到原神国服角色，请检查 Cookie")]

        results: list[tuple[bool, str]] = []
        for index, role in enumerate(roles, start=1):
            if not isinstance(role, dict):
                results.append((False, f"{safe_message(account.name)}: 第 {index} 个角色数据格式错误"))
                continue
            try:
                results.append(_sign_role(session, account, role, act_id))
            except requests.RequestException as exc:
                results.append((False, f"{safe_message(account.name)}: 第 {index} 个角色网络失败（{safe_message(exc)}）"))
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                results.append((False, f"{safe_message(account.name)}: 第 {index} 个角色解析失败（{safe_message(exc)}）"))
        return results


def sign_mihoyo() -> tuple[bool, str]:
    """
    网站名称：米游社
    签到说明：为 COOKIE_MIHOYO 对应的全部原神国服角色执行每日签到。
    """

    accounts = get_account_secrets("mihoyo", "COOKIE_MIHOYO")
    if not accounts:
        return False, "未配置 COOKIE_MIHOYO，且 JSON 多账号配置中没有米游社 Cookie"

    act_id = os.getenv("MIHOYO_ACT_ID", DEFAULT_ACT_ID).strip() or DEFAULT_ACT_ID
    game_biz = os.getenv("MIHOYO_GAME_BIZ", DEFAULT_GAME_BIZ).strip() or DEFAULT_GAME_BIZ
    results: list[tuple[bool, str]] = []
    for account in accounts:
        try:
            results.extend(_sign_account(account, act_id, game_biz))
        except requests.RequestException as exc:
            results.append((False, f"{account.name}: 网络请求失败（{safe_message(exc)}）"))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            results.append((False, f"{account.name}: 响应解析失败（{safe_message(exc)}）"))

    for ok, message in results:
        LOGGER.info("%s [米游社] %s", "✅" if ok else "❌", message)
    return all(ok for ok, _ in results), "；".join(message for _, message in results)
