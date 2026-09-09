"""个人多网站自动签到系统主入口。"""

from __future__ import annotations

import importlib
import inspect
import os
import pkgutil
import random
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

import signers
from signers.base import LOGGER, beijing_now, markdown_escape, push_message, safe_message, setup_logging


Signer = Callable[[], tuple[bool, str]]


@dataclass(frozen=True)
class SignerEntry:
    site_name: str
    function: Signer


@dataclass(frozen=True)
class SignerResult:
    site_name: str
    success: bool
    message: str


def _site_name(function: Signer) -> str:
    """优先读取函数属性，否则从文档中的“网站名称”提取。"""

    configured = getattr(function, "site_name", "")
    if configured:
        return str(configured)
    for line in inspect.getdoc(function).splitlines() if inspect.getdoc(function) else []:
        if line.strip().startswith("网站名称："):
            return line.split("：", 1)[1].strip()
    return function.__name__.removeprefix("sign_").replace("_", " ").title()


def discover_signers() -> list[SignerEntry]:
    """自动导入 signers 目录并发现模块内定义的 sign_* 函数。"""

    discovered: list[SignerEntry] = []
    for module_info in pkgutil.iter_modules(signers.__path__, f"{signers.__name__}."):
        if module_info.name.rsplit(".", 1)[-1].startswith("_"):
            continue
        try:
            module = importlib.import_module(module_info.name)
        except Exception as exc:
            short_name = module_info.name.rsplit(".", 1)[-1]
            detail = safe_message(exc)
            LOGGER.exception("签到模块 [%s] 导入失败", short_name)

            def failed_import(message: str = detail) -> tuple[bool, str]:
                return False, f"模块导入失败：{message}"

            failed_import.__name__ = f"sign_import_error_{short_name}"
            discovered.append(SignerEntry(f"模块 {short_name}", failed_import))
            continue
        for name, function in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("sign_") and function.__module__ == module.__name__:
                discovered.append(SignerEntry(_site_name(function), function))
    return sorted(discovered, key=lambda item: (item.site_name, item.function.__name__))


def _delay_seconds() -> float:
    try:
        minimum = max(0.0, float(os.getenv("CHECKIN_MIN_DELAY", "2")))
        maximum = max(0.0, float(os.getenv("CHECKIN_MAX_DELAY", "8")))
    except ValueError:
        LOGGER.warning("随机延时配置无效，使用默认范围 2-8 秒")
        minimum, maximum = 2.0, 8.0
    if minimum > maximum:
        minimum, maximum = maximum, minimum
    return random.uniform(minimum, maximum)


def run_signer(entry: SignerEntry) -> SignerResult:
    """隔离执行单个签到器，任何异常都不会中断后续站点。"""

    LOGGER.info("开始执行 [%s]", entry.site_name)
    try:
        raw_result = entry.function()
        if not isinstance(raw_result, tuple) or len(raw_result) != 2:
            raise TypeError("签到函数必须返回 (bool, message) 二元组")
        success, message = raw_result
        if not isinstance(success, bool):
            raise TypeError("签到函数返回值第一项必须是 bool")
        result = SignerResult(entry.site_name, success, safe_message(message, limit=1200))
    except Exception as exc:  # 单站故障必须与其他站点隔离。
        LOGGER.exception("[%s] 执行异常", entry.site_name)
        result = SignerResult(entry.site_name, False, safe_message(exc))

    LOGGER.info("%s [%s] %s", "✅" if result.success else "❌", result.site_name, result.message)
    return result


def build_report(results: list[SignerResult]) -> str:
    now = beijing_now().strftime("%Y-%m-%d %H:%M:%S")
    successes = sum(result.success for result in results)
    lines = [f"## 📅 多站签到报告 - {now}", "", "---", ""]
    for result in results:
        icon = "✅" if result.success else "❌"
        lines.append(
            f"{icon} **{markdown_escape(result.site_name)}**: {markdown_escape(result.message)}"
        )
    lines.extend(
        [
            "",
            "---",
            "",
            f"总计: {len(results)} 个站点 | ✅ 成功: {successes} | ❌ 失败: {len(results) - successes}",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    log_file = setup_logging()
    LOGGER.info("签到任务启动，日志文件：%s", log_file)
    entries = discover_signers()
    if not entries:
        LOGGER.error("未发现任何 sign_* 签到函数")
        return 1

    LOGGER.info("发现 %d 个签到器：%s", len(entries), "、".join(item.site_name for item in entries))
    results: list[SignerResult] = []
    for index, entry in enumerate(entries):
        if index:
            delay = _delay_seconds()
            LOGGER.info("随机等待 %.1f 秒后执行下一个站点", delay)
            time.sleep(delay)
        results.append(run_signer(entry))

    report = build_report(results)
    LOGGER.info("\n%s", report)
    try:
        pushed, push_detail = push_message(report)
    except Exception as exc:
        LOGGER.exception("PushPlus 推送出现未处理异常")
        pushed, push_detail = False, safe_message(exc)
    LOGGER.info("%s [PushPlus] %s", "✅" if pushed else "❌", push_detail)

    # 任一站点或推送失败时返回非零，以触发 Actions 的失败日志 Artifact。
    return 0 if all(result.success for result in results) and pushed else 1


if __name__ == "__main__":
    sys.exit(main())
