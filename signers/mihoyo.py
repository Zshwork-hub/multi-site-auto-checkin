"""米游社国服游戏签到。

自动识别同一米游社 Cookie 下绑定的米哈游国服游戏角色，并逐个签到。
Cookie 通常会过期，请定期更新 GitHub Secret ``COOKIE_MIHOYO``。
本模块不会在日志或推送中输出 Cookie 原文或完整 UID。
"""

from __future__ import annotations

import hashlib
import os
import random
import string
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

import requests

from .base import DEFAULT_TIMEOUT, LOGGER, AccountSecret, get_account_secrets, safe_message


ROLE_URL = "https://api-takumi.mihoyo.com/binding/api/getUserGameRolesByCookie"
APP_VERSION = "2.109.0"
CLIENT_TYPE = "5"
DS_SALT = "d9200c846b10886e8c874fc33c8f308b"


def _request_error(exc: requests.RequestException) -> str:
    """返回不含请求头、Cookie 或 URL 查询参数的安全错误说明。"""

    if isinstance(exc, requests.Timeout):
        return "请求超时"
    if isinstance(exc, requests.ConnectionError):
        return "连接失败"
    if isinstance(exc, requests.HTTPError):
        response = exc.response
        status = response.status_code if response is not None else "未知"
        return f"HTTP 错误 {status}"
    return f"请求异常（{exc.__class__.__name__}）"


def _pause() -> None:
    """在连续游戏/角色请求之间加入可配置的短随机延时。"""

    try:
        minimum = max(0.0, float(os.getenv("MIHOYO_MIN_DELAY", "1")))
        maximum = max(0.0, float(os.getenv("MIHOYO_MAX_DELAY", "3")))
    except ValueError:
        minimum, maximum = 1.0, 3.0
    if minimum > maximum:
        minimum, maximum = maximum, minimum
    time.sleep(random.uniform(minimum, maximum))


@dataclass(frozen=True)
class GameConfig:
    """一个米游社国服游戏的签到接口配置。"""

    name: str
    game_biz: str
    act_id: str
    api_base: str
    api_path: str
    signgame: str = ""

    @property
    def info_url(self) -> str:
        return f"{self.api_base}{self.api_path}/info?lang=zh-cn"

    @property
    def home_url(self) -> str:
        return f"{self.api_base}{self.api_path}/home?lang=zh-cn"

    @property
    def sign_url(self) -> str:
        return f"{self.api_base}{self.api_path}/sign"


GENSHIN = GameConfig(
    name="原神",
    game_biz="hk4e_cn",
    act_id="e202311201442471",
    api_base="https://api-takumi.mihoyo.com",
    api_path="/event/luna",
    signgame="hk4e",
)
HONKAI_STAR_RAIL = GameConfig(
    name="崩坏：星穹铁道",
    game_biz="hkrpg_cn",
    act_id="e202304121516551",
    api_base="https://api-takumi.mihoyo.com",
    api_path="/event/luna",
)
ZENLESS_ZONE_ZERO = GameConfig(
    name="绝区零",
    game_biz="nap_cn",
    act_id="e202406242138391",
    api_base="https://act-nap-api.mihoyo.com",
    api_path="/event/luna/zzz",
    signgame="zzz",
)
HONKAI_IMPACT_2 = GameConfig(
    name="崩坏学园 2",
    game_biz="bh2_cn",
    act_id="e202203291431091",
    api_base="https://api-takumi.mihoyo.com",
    api_path="/event/luna",
)
HONKAI_IMPACT_3 = GameConfig(
    name="崩坏 3",
    game_biz="bh3_cn",
    act_id="e202306201626331",
    api_base="https://api-takumi.mihoyo.com",
    api_path="/event/luna",
)
TEARS_OF_THEMIS = GameConfig(
    name="未定事件簿",
    game_biz="nxx_cn",
    act_id="e202202251749321",
    api_base="https://api-takumi.mihoyo.com",
    api_path="/event/luna",
)
SUPPORTED_GAMES = (
    GENSHIN,
    HONKAI_STAR_RAIL,
    ZENLESS_ZONE_ZERO,
    HONKAI_IMPACT_2,
    HONKAI_IMPACT_3,
    TEARS_OF_THEMIS,
)
REGION_NAMES = {
    "cn_gf01": "天空岛",
    "cn_qd01": "世界树",
}


def _make_ds() -> str:
    """生成当前米游社移动网页接口使用的动态 DS 校验头。"""

    timestamp = int(time.time())
    random_text = "".join(random.sample(string.ascii_lowercase + string.digits, 6))
    digest = hashlib.md5(
        f"salt={DS_SALT}&t={timestamp}&r={random_text}".encode("utf-8")
    ).hexdigest()
    return f"{timestamp},{random_text},{digest}"


def _headers(cookie: str, game: Optional[GameConfig] = None) -> dict[str, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 12; Unspecified Device) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
            "Chrome/103.0.5060.129 Mobile Safari/537.36 "
            f"miHoYoBBS/{APP_VERSION}"
        ),
        "Cookie": cookie,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "zh-CN,en-US;q=0.8",
        "Origin": "https://webstatic.mihoyo.com",
        "Referer": "https://act.mihoyo.com/",
        "X-Requested-With": "com.mihoyo.hyperion",
        "DS": _make_ds(),
        "x-rpc-app_version": APP_VERSION,
        "x-rpc-client_type": CLIENT_TYPE,
        "x-rpc-channel": "miyousheluodi",
        "x-rpc-device_id": str(uuid.uuid3(uuid.NAMESPACE_URL, cookie)),
    }
    if game is not None:
        headers["Origin"] = "https://act.mihoyo.com"
        if game.signgame:
            headers["x-rpc-signgame"] = game.signgame
    return headers


def _json(response: requests.Response) -> dict[str, Any]:
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError("接口返回的不是 JSON 对象")
    return data


def _api_error(data: dict[str, Any]) -> str:
    return safe_message(data.get("message") or f"retcode={data.get('retcode')}")


def _masked_label(account: AccountSecret, role: dict[str, Any], game: GameConfig) -> str:
    region_code = safe_message(role.get("region", ""), limit=50)
    region = REGION_NAMES.get(region_code, region_code)
    uid = str(role.get("game_uid", ""))
    nickname = safe_message(role.get("nickname") or "未命名角色", limit=50)
    account_name = safe_message(account.name, limit=50)
    masked_uid = f"{uid[:2]}***{uid[-2:]}" if len(uid) >= 5 else "***"
    return f"{game.name}/{account_name}/{nickname}({region}, UID {masked_uid})"


def _reward_text(
    session: requests.Session,
    account: AccountSecret,
    game: GameConfig,
    reward_index: int,
) -> str:
    """尽力查询奖励；奖励接口失败不改变已完成的签到状态。"""

    home = _json(
        session.get(
            game.home_url,
            params={"act_id": game.act_id},
            headers=_headers(account.value, game),
            timeout=DEFAULT_TIMEOUT,
        )
    )
    payload = home.get("data") or {}
    if not isinstance(payload, dict):
        raise ValueError("奖励接口 data 字段不是对象")
    awards = payload.get("awards") or []
    if not isinstance(awards, list) or not 0 <= reward_index < len(awards):
        return ""
    item = awards[reward_index]
    if not isinstance(item, dict):
        return ""
    return f"，获得 {item.get('name', '奖励')}×{item.get('cnt', '?')}"


def _sign_role(
    session: requests.Session,
    account: AccountSecret,
    role: dict[str, Any],
    game: GameConfig,
) -> tuple[bool, str]:
    region = safe_message(role.get("region", ""), limit=50)
    uid = str(role.get("game_uid", ""))
    label = _masked_label(account, role, game)
    params = {"act_id": game.act_id, "region": region, "uid": uid}

    info = _json(
        session.get(
            game.info_url,
            params=params,
            headers=_headers(account.value, game),
            timeout=DEFAULT_TIMEOUT,
        )
    )
    if info.get("retcode") != 0:
        return False, f"{label}: 查询状态失败（{_api_error(info)}）"
    info_data = info.get("data") or {}
    if not isinstance(info_data, dict):
        return False, f"{label}: 状态接口 data 字段格式错误"
    if info_data.get("first_bind"):
        return False, f"{label}: 首次绑定，请先在米游社 App 手动签到一次"
    if info_data.get("is_sign"):
        return True, f"{label}: 今日已签到"

    total_sign_day = int(info_data.get("total_sign_day") or 0)
    _pause()
    result = _json(
        session.post(
            game.sign_url,
            json={"act_id": game.act_id, "region": region, "uid": uid},
            headers=_headers(account.value, game),
            timeout=DEFAULT_TIMEOUT,
        )
    )
    if result.get("retcode") == -5003:
        return True, f"{label}: 今日已签到"
    if result.get("retcode") != 0:
        return False, f"{label}: 签到失败（{_api_error(result)}）"

    result_data = result.get("data") or {}
    if not isinstance(result_data, dict):
        return False, f"{label}: 签到接口 data 字段格式错误"
    if result_data.get("success") == 1:
        return False, f"{label}: 触发验证码，请在米游社 App 完成验证后重试"
    if result_data.get("success") != 0:
        return False, f"{label}: 签到接口返回了未知状态"

    reward = ""
    try:
        reward = _reward_text(session, account, game, total_sign_day)
    except (requests.RequestException, ValueError, TypeError, AttributeError):
        LOGGER.warning("[%s] 签到成功，但奖励信息查询失败", label)
    return True, f"{label}: 签到成功{reward}"


def _sign_game_account(
    account: AccountSecret,
    game: GameConfig,
) -> tuple[bool, list[tuple[bool, str]]]:
    """查询一个账号在某游戏下的全部角色；未绑定该游戏时返回 found=False。"""

    with requests.Session() as session:
        roles_data = _json(
            session.get(
                ROLE_URL,
                params={"game_biz": game.game_biz},
                headers=_headers(account.value),
                timeout=DEFAULT_TIMEOUT,
            )
        )
        if roles_data.get("retcode") != 0:
            raise ValueError(f"{game.name}获取角色失败（{_api_error(roles_data)}）")

        payload = roles_data.get("data") or {}
        if not isinstance(payload, dict):
            raise ValueError(f"{game.name}角色接口 data 字段不是对象")
        roles = payload.get("list") or []
        if not isinstance(roles, list):
            raise ValueError(f"{game.name}角色接口 list 字段不是数组")
        if not roles:
            return False, []

        results: list[tuple[bool, str]] = []
        for index, role in enumerate(roles, start=1):
            if index > 1:
                _pause()
            if not isinstance(role, dict):
                results.append((False, f"{game.name}/{safe_message(account.name)}: 第 {index} 个角色数据格式错误"))
                continue
            try:
                results.append(_sign_role(session, account, role, game))
            except requests.RequestException as exc:
                results.append(
                    (
                        False,
                        f"{game.name}/{safe_message(account.name)}: "
                        f"第 {index} 个角色网络失败（{_request_error(exc)}）",
                    )
                )
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                results.append(
                    (
                        False,
                        f"{game.name}/{safe_message(account.name)}: "
                        f"第 {index} 个角色解析失败（{safe_message(exc)}）",
                    )
                )
        return True, results


def sign_mihoyo() -> tuple[bool, str]:
    """
    网站名称：米游社
    签到说明：自动识别并签到米游社 Cookie 下已绑定的国服游戏角色。
    """

    accounts = get_account_secrets("mihoyo", "COOKIE_MIHOYO")
    if not accounts:
        return False, "未配置 COOKIE_MIHOYO，且 JSON 多账号配置中没有米游社 Cookie"

    results: list[tuple[bool, str]] = []
    for account in accounts:
        found_for_account = False
        had_query_error = False
        for game_index, game in enumerate(SUPPORTED_GAMES):
            if game_index > 0:
                _pause()
            try:
                found, game_results = _sign_game_account(account, game)
                if not found:
                    LOGGER.info("⏭️ [%s] %s 未绑定，跳过", game.name, safe_message(account.name))
                    continue
                found_for_account = True
                results.extend(game_results)
            except requests.RequestException as exc:
                had_query_error = True
                results.append(
                    (False, f"{game.name}/{safe_message(account.name)}: 网络请求失败（{_request_error(exc)}）")
                )
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                had_query_error = True
                results.append(
                    (False, f"{game.name}/{safe_message(account.name)}: 响应解析失败（{safe_message(exc)}）")
                )

        if not found_for_account and not had_query_error:
            results.append(
                (
                    False,
                    f"{safe_message(account.name)}: 未找到可签到的米哈游游戏角色",
                )
            )

    for ok, message in results:
        LOGGER.info("%s [米游社] %s", "✅" if ok else "❌", message)
    return all(ok for ok, _ in results), "；".join(message for _, message in results)
