# 个人多网站自动签到系统

一个基于 Python 和 GitHub Actions 的轻量级个人签到框架。每天北京时间 09:30 自动执行各网站签到，最后通过 PushPlus 将 Markdown 汇总报告推送到微信。

本项目借鉴 [LinVireo/bailanjo-auto-check-in](https://github.com/LinVireo/bailanjo-auto-check-in) 的环境变量配置、定时工作流、多账号和失败调试思路；各网站的接口逻辑独立实现。

> 仅用于你有权使用的个人账号。网站接口可能随时调整；请遵守目标网站的服务条款和频率限制。

## 功能

- 自动发现 `signers/` 中所有以 `sign_` 开头的签到函数
- 自动识别米游社账号绑定的原神、崩坏：星穹铁道和绝区零角色
- 单个网站失败不会阻断其他网站
- Cookie、Token 等敏感信息只从环境变量或 GitHub Secrets 读取
- 支持直接 Secret、`COOKIES_JSON`、`ACCOUNTS_JSON` 三种账号配置
- 站点之间默认随机等待 2–8 秒；米游社游戏和角色之间等待 1–3 秒
- PushPlus Markdown 汇总推送
- 失败时将 UTF-8 日志上传为 GitHub Actions Artifact，保留 7 天
- 可在本地和 GitHub Actions 运行

## 项目结构

```text
.
├── .github/workflows/checkin.yml  # 每日定时和手动执行工作流
├── main.py                        # 自动发现、执行、汇总和推送
├── signers/
│   ├── __init__.py
│   ├── base.py                    # 日志、账号配置、PushPlus 等公共工具
│   ├── aivalux.py                 # Aivalux 签到与可领取红包
│   └── mihoyo.py                  # 米游社原神、星铁、绝区零国服签到
├── tests/                         # 不访问网络的基础测试
├── requirements.txt               # 锁定依赖版本
└── README.md
```

## GitHub Actions 部署（推荐）

### 1. Fork 或上传仓库

Fork 本仓库，或将这些文件提交到你自己的 GitHub 仓库。请勿将 Cookie、Token 或 `.env` 文件提交到仓库。

### 2. 获取 PushPlus Token

登录 [PushPlus](https://www.pushplus.plus/)，复制个人 Token。按照 PushPlus 页面说明完成微信消息接收配置。

当前代码按需求使用 `http://www.pushplus.plus/send`。HTTP 是明文传输，网络链路存在 Token 和报告内容被截获的风险；如果 PushPlus 官方确认你的环境支持 HTTPS，建议将 `signers/base.py` 中的地址改为 `https://www.pushplus.plus/send`。

### 3. 配置 GitHub Secrets

进入仓库：`Settings` → `Secrets and variables` → `Actions` → `New repository secret`。

| Secret 名称 | 用途 | 是否必填 |
| --- | --- | --- |
| `PUSHPLUS_TOKEN` | PushPlus Token | 是 |
| `COOKIE_MIHOYO` | 单个米游社账号 Cookie；自动识别原神、星铁和绝区零 | 米游社签到必填；使用 JSON 配置时可不填 |
| `AIVALUX_ACCESS_TOKEN` | Aivalux 登录后的 Access Token | Aivalux 推荐 |
| `AIVALUX_REFRESH_TOKEN` | Aivalux Refresh Token，用于 Access Token 过期后刷新 | Aivalux 推荐 |
| `AIVALUX_COOKIE` | Aivalux 登录 Cookie，只有接口额外校验时才需要 | 可选 |
| `COOKIES_JSON` | 按网站批量配置 Cookie | 可选 |
| `ACCOUNTS_JSON` | 带账号名称的进阶配置 | 可选 |

Cookie 可在登录目标网站后通过浏览器开发者工具的 Network 请求头获得。Cookie 通常会过期，出现登录失效或 `-100` 等错误时需要重新获取。不要在 Issue、日志、截图或聊天中公开 Cookie。

### 4. 启用和测试 Actions

1. 打开仓库的 `Actions` 页面并启用工作流。
2. 选择 `Daily multi-site check-in`。
3. 点击 `Run workflow` 手动运行一次，确认 Cookie 和推送配置正确。
4. 此后工作流由 `30 1 * * *` 触发，即每天 01:30 UTC（北京时间 09:30）。GitHub 的计划任务在繁忙时可能延迟数分钟，并不保证准点启动。

任一签到或 PushPlus 推送失败时，任务会标记为失败。打开该次运行页面，在 `Artifacts` 区域下载 `checkin-logs-*` 即可查看日志。日志不会主动打印 Cookie，但仍建议仅在私有环境中保存和分享。

## 多账号配置

三种方式可以并存，框架会按 Cookie 值自动去重。GitHub Secret 的值必须是单行合法 JSON，不要添加 Markdown 代码块。

### 方式 A：单账号

直接设置：

```text
COOKIE_MIHOYO=你的完整 Cookie
```

### 方式 B：按站点配置 Cookie 数组

设置 `COOKIES_JSON`：

```json
{"mihoyo":["账号一Cookie","账号二Cookie"]}
```

也可以使用环境变量名作为键：

```json
{"COOKIE_MIHOYO":["账号一Cookie","账号二Cookie"]}
```

### 方式 C：带名称的账号列表

设置 `ACCOUNTS_JSON`：

```json
[
  {"name":"主账号","mihoyo":{"cookie":"主账号Cookie"}},
  {"name":"副账号","mihoyo":{"cookie":"副账号Cookie"}}
]
```

也支持扁平写法：

```json
[
  {"name":"主账号","COOKIE_MIHOYO":"主账号Cookie"}
]
```

账号名称会出现在通知中，因此不要把手机号、邮箱等敏感信息用作名称。

## 本地运行

需要 Python 3.9 或更高版本。建议使用虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --requirement requirements.txt

$env:PUSHPLUS_TOKEN = "你的 PushPlus Token"
$env:COOKIE_MIHOYO = "你的米游社 Cookie"
python main.py
```

macOS/Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --requirement requirements.txt

export PUSHPLUS_TOKEN='你的 PushPlus Token'
export COOKIE_MIHOYO='你的米游社 Cookie'
python main.py
```

日志保存在 `logs/checkin.log`。程序遵循常见的退出码规则：全部签到且推送成功返回 `0`，任一失败返回 `1`。

## 米游社示例说明

`signers/mihoyo.py` 会使用同一个 Cookie 分别查询原神、崩坏：星穹铁道和绝区零国服角色，只签到实际绑定的游戏与角色，未绑定的游戏自动跳过。同一游戏存在多个角色时会逐个处理；接口返回“今日已签到”时按成功处理，不会重复签到。实现使用当前 Luna 接口、动态 DS、稳定设备标识和各游戏独立活动 ID；这些公开接口参数仍可能随米游社更新而失效。

米游社接口和风控策略可能变化。如果返回验证码、风控或接口不兼容提示，脚本不会尝试绕过验证；请先在官方客户端完成必要验证，再更新 Cookie 或接口配置。

## Aivalux 签到与红包

`signers/aivalux.py` 会先读取 `/qiandao/api/status`：若今天尚未签到，则调用 `POST /qiandao/api/check-in`；随后仅在 `red_packet.can_claim=true` 时调用 `POST /qiandao/api/red-packet/claim`。红包已领取、未开放或已抢完都不会重复请求。脚本不会执行抽奖、补签、兑换、周礼包，也不会消费积分、余额或抽奖卡。

获取凭据时，在已登录的 Aivalux 页面按 `F12`，进入 `应用/Application` → `本地存储/Local Storage` → `https://www.aivalux.com`，将 `auth_token` 和 `refresh_token` 的值分别直接保存为 GitHub Secrets：`AIVALUX_ACCESS_TOKEN`、`AIVALUX_REFRESH_TOKEN`。不要把值发到聊天、Issue 或提交到仓库。若尚未配置 Token，Aivalux 签到器会安全跳过，不影响其他站点。Access Token 过期后，脚本可在当次运行中使用 Refresh Token 自动刷新；GitHub Actions 无法安全回写新的 Secret。如果服务返回了轮换后的 Refresh Token，任务会在签到完成后标记失败并通过通知提示你重新登录、更新两个 Secrets，任何新 Token 都不会写入日志或仓库。

## 添加新网站

在 `signers/` 下新建一个 `.py` 文件，例如 `signers/example.py`。函数名必须以 `sign_` 开头、无需参数，并返回 `(bool, str)`；主程序无需修改。

```python
import os

import requests


def sign_example():
    """
    网站名称：示例网站
    签到说明：使用 Cookie 调用示例网站的签到接口。
    """

    cookie = os.getenv("COOKIE_EXAMPLE", "").strip()
    if not cookie:
        return False, "未配置 COOKIE_EXAMPLE"

    url = "https://example.com/api/checkin"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
        "Cookie": cookie,
    }
    try:
        response = requests.post(url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        if data.get("code") == 0:
            return True, data.get("message") or "签到成功"
        return False, data.get("message") or f"接口返回 code={data.get('code')}"
    except (requests.RequestException, ValueError) as exc:
        return False, str(exc)
```

然后完成两项配置：

1. 在 GitHub Secrets 新增 `COOKIE_EXAMPLE`。
2. 在 `.github/workflows/checkin.yml` 的 `jobs.checkin.env` 下增加：

```yaml
COOKIE_EXAMPLE: ${{ secrets.COOKIE_EXAMPLE }}
```

主程序会自动导入 `signers/example.py` 并执行 `sign_example()`，不需要增加 `import` 或调用代码。一个文件也可以放多个 `sign_` 函数，但为了便于维护，建议每个网站一个文件、一个入口函数。

接入真实网站前，应确认以下信息：网站名称、签到 API URL、GET/POST 方法、Cookie 或鉴权头、请求参数，以及成功/重复签到/失败的判断依据。所有请求都应设置 10–15 秒超时，返回给通知的错误消息中不得包含 Cookie、Token 或完整响应头。

## 调整随机延时

工作流在不同网站之间随机等待 2–8 秒，并在连续的米游社游戏或角色请求之间等待 1–3 秒。可编辑工作流中的：

```yaml
CHECKIN_MIN_DELAY: "2"
CHECKIN_MAX_DELAY: "8"
MIHOYO_MIN_DELAY: "1"
MIHOYO_MAX_DELAY: "3"
```

这不是规避风控的保证。请保持低频访问，并遵守网站规则。

## 运行测试

基础测试不发起网络请求：

```bash
python -m unittest discover -s tests -v
```

## 常见问题

- **任务成功但没收到微信消息**：检查 `PUSHPLUS_TOKEN`、PushPlus 账号绑定状态和 Actions 日志。
- **米游社提示 Cookie 失效**：重新登录米游社并更新 Secret；保存后手动触发工作流验证。
- **米游社提示验证码**：先在米游社 App 完成验证或手动签到，再重新运行；脚本不会绕过验证码。
- **Aivalux 显示登录失效**：重新登录网页，从 Local Storage 更新 `AIVALUX_ACCESS_TOKEN` 和 `AIVALUX_REFRESH_TOKEN`。
- **新增文件没有执行**：确认文件位于 `signers/`、扩展名为 `.py`，函数名以 `sign_` 开头且定义在该模块中。
- **为什么没有失败截图**：本项目使用 HTTP API，不启动浏览器，因此保存的是详细日志 Artifact；只有 Playwright/Selenium 页面自动化才有可用的页面截图。
- **定时任务没有精确在 09:30 开始**：GitHub Actions 的 cron 使用 UTC 且可能排队延迟。工作流中的报告时间始终按 `Asia/Shanghai` 生成。
