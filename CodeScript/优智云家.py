#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
优智云家 v1.1.0（mywc网关聚合推送版）

功能：自动获取优智云家小程序登录 code，完成每日签到，支持多账号执行，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL   必填其一，自建授权服务器域名
   - 示例：http://127.0.0.1:8110
   - 脚本会自动拼接 /mywc
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=wxa61f98248d20178b
   - 请求头：auth=账号标识

2. 账号变量：
   yzyj_wxid 或 YZYJ_WXID                         推荐，优智云家专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：wxid_a&wxid_b&openida 或 wxid_a,wxid_b,openida

3. 推送变量：
   需要同目录存在 SendNotify.py，脚本结束后会统一调用 send_push_notification。
   常用推送变量如下，配置任意一种即可：
   QYWX_KEY                                         企业微信机器人 key
   PUSH_PLUS_TOKEN                                  PushPlus token
   PUSH_KEY                                         Server 酱 key
   DD_BOT_TOKEN 或 DD_BOT_SECRET                     钉钉机器人 token/secret
   FSKEY                                            飞书机器人 key

4. 青龙任务建议：
   名称：优智云家签到
   命令：python3 优智云家.py
   定时：每天运行 1 次即可，具体时间自行调整
"""
from __future__ import annotations

import json
import os
import random
import re
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import quote

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ──────────────────────────────────────────────
# 基础配置
# ──────────────────────────────────────────────

APP_NAME = "优智云家"
APPID = "wxa61f98248d20178b"
SCRIPT_TITLE = "🏠 优智云家签到执行总结"

# 账号变量：优先专属变量，兼容旧变量 yzyj
ACCOUNT_RAW = (
    os.getenv("yzyj_wxid")
    or os.getenv("YZYJ_WXID")
    or os.getenv("yzyj", "")
)

# mywc 自建网关：只读取用户环境变量，不硬编码本地 IP
WX_SERVER_URL = (os.getenv("wx_server_url") or os.getenv("WX_SERVER_URL") or "").strip().rstrip("/")
WX_CODE_TIMEOUT = 40

# 可选代理配置：配置 PROXY_API 后，业务请求优先走代理，失败直连兜底
PROXY_API = os.getenv("PROXY_API", "")
PROXY_TYPE = os.getenv("PROXY_TYPE", "http").lower()
PROXY_RETRY_TIMES = 3
PROXY_VALIDATE_URL = "http://httpbin.org/ip"
ENABLE_DIRECT_FALLBACK = True
REQUEST_TIMEOUT = 30

BASE_URL = "https://xapi.weimob.com"
LOGIN_URL = f"{BASE_URL}/fe/mapi/user/loginX"
SIGN_STATUS_URL = f"{BASE_URL}/api3/onecrm/mactivity/sign/misc/sign/activity/c/signMainInfo"
SIGN_SUBMIT_URL = f"{BASE_URL}/api3/onecrm/mactivity/sign/misc/sign/activity/core/c/sign"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 "
    "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) "
    "UnifiedPCWindowsWechat(0xf2541938) XWEB/19823"
)

GLOBAL_NOTIFY_BUFFERS: List[Dict[str, Any]] = []


class TaskError(RuntimeError):
    pass


# ──────────────────────────────────────────────
# 通用工具
# ──────────────────────────────────────────────

def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def mask(value: Any) -> str:
    value = str(value or "")
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-6:]}"


def parse_accounts(raw: str) -> List[str]:
    normalized = raw.replace("，", ",").replace(",", "&").replace("\n", "&")
    return [item.strip() for item in normalized.split("&") if item.strip()]


def get_accounts() -> List[str]:
    accounts = parse_accounts(ACCOUNT_RAW)
    if not accounts:
        raise TaskError("未配置有效账号变量，请配置 yzyj_wxid 或 YZYJ_WXID")
    return accounts


def log_title(total: int) -> None:
    print()
    print("╔" + "═" * 50 + "╗")
    print("║  🏠 优智云家 mywc 网关版  ║")
    print(f"║  🕒 启动时间: {now_text():<32}║")
    print(f"║  🔢 账号数量: {total:<34}║")
    print("╚" + "═" * 50 + "╝")


def log_account_header(index: int, total: int, wxid: str) -> None:
    print()
    print("┌" + "─" * 50 + "┐")
    print(f"│  🧩 账号 {index} / {total:<37}│")
    print(f"│  🆔 wxid {mask(wxid):<39}│")
    print("└" + "─" * 50 + "┘")


def direct_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    session.verify = False
    return session


def safe_json_response(resp: requests.Response, action: str) -> Dict[str, Any]:
    try:
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        raise TaskError(f"{action} 请求异常：{exc}，响应：{getattr(resp, 'text', '')}") from exc
    if not isinstance(data, dict):
        raise TaskError(f"{action} 返回格式异常：{data!r}")
    return data


# ──────────────────────────────────────────────
# 代理处理
# ──────────────────────────────────────────────

def parse_proxy_response(text: Any) -> Dict[str, Any] | None:
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    text = text.strip()
    if not text:
        return None

    try:
        data = json.loads(text)
        proxy_obj = None
        if isinstance(data.get("data"), list) and data["data"]:
            proxy_obj = data["data"][0]
        elif isinstance(data.get("data"), dict):
            proxy_obj = data["data"]
        elif data.get("ip") and data.get("port"):
            proxy_obj = data
        elif isinstance(data.get("result"), dict):
            proxy_obj = data["result"]

        if proxy_obj:
            host = proxy_obj.get("ip") or proxy_obj.get("host")
            port = proxy_obj.get("port")
            if host and port:
                return {
                    "host": str(host),
                    "port": int(port),
                    "username": proxy_obj.get("user") or proxy_obj.get("username") or "",
                    "password": proxy_obj.get("pass") or proxy_obj.get("password") or "",
                }
    except Exception:
        pass

    if ":" in text:
        parts = text.split(":")
        if len(parts) >= 2:
            return {
                "host": parts[0],
                "port": int(parts[1]),
                "username": parts[2] if len(parts) > 2 else "",
                "password": parts[3] if len(parts) > 3 else "",
            }
    return None


def build_proxy_dict(proxy_info: Dict[str, Any] | None) -> Dict[str, str] | None:
    if not proxy_info:
        return None
    host = proxy_info["host"]
    port = proxy_info["port"]
    username = proxy_info.get("username", "")
    password = proxy_info.get("password", "")
    auth = f"{quote(username)}:{quote(password)}@" if username and password else ""
    scheme = "socks5" if PROXY_TYPE == "socks5" else "http"
    proxy_url = f"{scheme}://{auth}{host}:{port}"
    print(f"  🛠️ [代理] 生成 {scheme.upper()} 代理 {host}:{port}")
    return {"http": proxy_url, "https": proxy_url}


def validate_proxy(proxies: Dict[str, str] | None) -> Tuple[bool, str]:
    if not proxies:
        return False, ""
    try:
        resp = requests.get(PROXY_VALIDATE_URL, proxies=proxies, timeout=15, verify=False)
        if resp.status_code == 200:
            data = resp.json()
            ip = data.get("origin", "未知") if isinstance(data, dict) else "未知"
            print(f"  ✅ [代理] 验证通过，出口 IP: {ip}")
            return True, ip
    except Exception as exc:
        print(f"  ⚠️ [代理] 验证失败: {exc}")
    return False, ""


def get_valid_proxy() -> Tuple[Dict[str, str] | None, str]:
    if not PROXY_API:
        print("  ⚠️ [代理] 未配置 PROXY_API，使用直连")
        return None, ""

    print("  🌐 [代理] 正在获取代理...")
    for idx in range(1, PROXY_RETRY_TIMES + 1):
        try:
            resp = direct_session().get(PROXY_API, timeout=15)
            proxy_info = parse_proxy_response(resp.text)
            proxies = build_proxy_dict(proxy_info)
            ok, ip = validate_proxy(proxies)
            if ok:
                return proxies, ip
        except Exception as exc:
            print(f"  ⚠️ [代理] 第{idx}次获取失败：{exc}")
        if idx < PROXY_RETRY_TIMES:
            time.sleep(2)

    print("  ⚠️ [代理] 获取失败，使用直连")
    return None, ""


def request_with_proxy(method: str, url: str, *, proxies: Dict[str, str] | None = None, **kwargs: Any) -> requests.Response:
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    kwargs.setdefault("verify", False)
    if proxies:
        try:
            return requests.request(method, url, proxies=proxies, **kwargs)
        except Exception as exc:
            print(f"  ⚠️ [代理] 节点请求异常，转直连：{exc}")
            if not ENABLE_DIRECT_FALLBACK:
                raise
    return direct_session().request(method, url, **kwargs)


# ──────────────────────────────────────────────
# mywc 授权 + 业务接口
# ──────────────────────────────────────────────

def get_code(wxid: str) -> str:
    if not WX_SERVER_URL:
        raise TaskError("未配置 wx_server_url 或 WX_SERVER_URL")

    url = f"{WX_SERVER_URL}/mywc"
    params = {"wxid": wxid, "appId": APPID}
    print(f"  🔐 [授权] 从 mywc 取 code，wxid={mask(wxid)}")

    try:
        resp = requests.get(
            url,
            params=params,
            headers={"auth": wxid},
            timeout=WX_CODE_TIMEOUT,
            verify=False,
        )
        data = safe_json_response(resp, "mywc授权")
    except Exception as exc:
        raise TaskError(f"mywc 授权异常：{exc}") from exc

    code = data.get("code") or (data.get("data") or {}).get("code")
    if data.get("status") not in (None, "ok", 0, "0", True):
        raise TaskError(f"mywc 返回失败：{data}")
    if not code:
        raise TaskError(f"mywc 未返回 code：{data}")

    print("  ✅ [授权] 成功拿到 code")
    return str(code)


def common_headers(token: str | None = None, extra_headers: Dict[str, str] | None = None) -> Dict[str, str]:
    headers = {
        "Host": "xapi.weimob.com",
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Referer": f"https://servicewechat.com/{APPID}/109/page-frame.html",
        "Accept-Encoding": "gzip, deflate, br",
    }
    if token:
        headers["X-WX-Token"] = token
    if extra_headers:
        headers.update(extra_headers)
    return headers


def extract_token(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    nested = data.get("data") if isinstance(data.get("data"), dict) else {}
    for key in ["token", "accessToken", "access_token", "jwt"]:
        val = data.get(key) or nested.get(key)
        if val and val != "null":
            return str(val)
    return None


def login_by_code(code: str, proxies: Dict[str, str] | None) -> Tuple[str, Dict[str, Any]]:
    payload = {
        "appid": APPID,
        "basicInfo": {
            "bosId": "4022115200359",
            "cid": "821033359",
            "tcode": "weimob",
            "vid": "6016741943359",
        },
        "env": "production",
        "extendInfo": {"source": 1},
        "is_pre_fetch_open": True,
        "parentVid": 0,
        "pid": "",
        "storeId": "",
        "code": code,
        "queryAuthConfig": True,
    }
    resp = request_with_proxy("POST", LOGIN_URL, headers=common_headers(), json=payload, proxies=proxies)
    data = safe_json_response(resp, "登录")
    if str(data.get("errcode")) != "0":
        raise TaskError(f"登录失败：{data.get('errmsg') or data}")

    token = extract_token(data)
    if not token:
        raise TaskError(f"登录返回缺少 Token：{data}")

    print(f"  ✅ [登录] Token 获取成功: {mask(token)}")
    return token, data


def check_sign_status(token: str, proxies: Dict[str, str] | None) -> Tuple[bool, Dict[str, Any]]:
    extra_headers = {
        "x-wmsdk-vid": "6016741943359",
        "x-biz-id": "146",
        "cloud-project-name": "fansquan",
        "x-component-is": "onecrm/signgift",
        "cloud-bosid": "4022115200359",
        "weimob-bosId": "4022115200359",
    }
    payload = {
        "appid": APPID,
        "basicInfo": {
            "vid": 6016741943359,
            "vidType": 2,
            "bosId": 4022115200359,
            "productId": 146,
            "productInstanceId": 15532102359,
            "productVersionId": "10003",
            "merchantId": 2000230069359,
            "tcode": "weimob",
            "cid": 821033359,
        },
        "extendInfo": {"wxTemplateId": 7930},
    }
    resp = request_with_proxy("POST", SIGN_STATUS_URL, headers=common_headers(token, extra_headers), json=payload, proxies=proxies)
    data = safe_json_response(resp, "签到状态")
    if str(data.get("errcode")) != "0":
        # 兼容旧版逻辑：该接口偶发返回 999999/data=None，但登录 token 有效。
        # 不能在这里中断，否则会比旧脚本少执行后续签到接口。
        print(f"  ⚠️ [签到] 状态查询失败，按未签到继续尝试：{data}")
        return False, {"status_error": data}
    sign_data = data.get("data") if isinstance(data.get("data"), dict) else {}
    return bool(sign_data.get("isSign", False)), sign_data


def submit_signin(token: str, proxies: Dict[str, str] | None) -> Tuple[bool, str, int]:
    extra_headers = {
        "x-wmsdk-vid": "6016741943359",
        "x-biz-id": "146",
        "cloud-project-name": "fansquan",
        "x-component-is": "onecrm/signgift",
        "cloud-bosid": "4022115200359",
        "weimob-bosId": "4022115200359",
        "parentrpcid": "a6e117c9d2dad0ad",
    }
    payload = {
        "appid": APPID,
        "basicInfo": {
            "vid": 6016741943359,
            "vidType": 2,
            "bosId": 4022115200359,
            "productId": 146,
            "productInstanceId": 15532102359,
            "productVersionId": "10003",
            "merchantId": 2000230069359,
            "tcode": "weimob",
            "cid": 821033359,
        },
        "extendInfo": {
            "wxTemplateId": 8105,
            "analysis": [],
            "bosTemplateId": 1000002154,
            "childTemplateIds": [
                {"customId": 90004, "version": "crm@0.1.81"},
                {"customId": 90002, "version": "ec@80.0"},
                {"customId": 90006, "version": "hudong@0.0.251"},
                {"customId": 90008, "version": "cms@0.0.524"},
                {"customId": 90070, "version": "1.0.12"},
            ],
            "quickdeliver": {"enable": True},
            "youshu": {"enable": False},
            "source": 1,
            "channelsource": 5,
            "refer": "onecrm-signgift",
            "mpScene": 1005,
        },
        "queryParameter": None,
        "i18n": {"language": "zh", "timezone": "8"},
        "pid": "",
        "storeId": "",
        "customInfo": {"source": 0, "wid": 11983225884},
    }
    resp = request_with_proxy("POST", SIGN_SUBMIT_URL, headers=common_headers(token, extra_headers), json=payload, proxies=proxies)
    data = safe_json_response(resp, "签到")
    if str(data.get("errcode")) != "0":
        errmsg = str(data.get("errmsg") or data.get("msg") or "签到失败")
        if any(keyword in errmsg for keyword in ("重复签到", "已签到", "今日已签")):
            print(f"  ✅ [签到] {errmsg}，按今日已签处理")
            return True, "今日已签", 0
        return False, errmsg, 0

    result_data = data.get("data") if isinstance(data.get("data"), dict) else {}
    fixed_reward = result_data.get("fixedReward") if isinstance(result_data.get("fixedReward"), dict) else {}
    point_name = str(result_data.get("pointName") or "积分")
    points = int(fixed_reward.get("points") or 0)
    growth = int(fixed_reward.get("growth") or 0)

    print("  ┌─ 🎁 签到奖励明细")
    print(f"  ├─ 状态: {data.get('errmsg', '成功')}")
    print(f"  ├─ {point_name}: +{points}")
    print(f"  └─ 成长值: +{growth}")
    return True, f"签到成功，获得{points}{point_name}", points


# ──────────────────────────────────────────────
# 通知聚合
# ──────────────────────────────────────────────

def append_notify_result(index: int, wxid: str, result: Dict[str, Any]) -> None:
    GLOBAL_NOTIFY_BUFFERS.append({
        "index": index,
        "account": mask(wxid),
        "ok": bool(result.get("success")),
        "status": result.get("status") or ("failed" if not result.get("success") else "success"),
        "sign_msg": result.get("signMsg", "-"),
        "points": result.get("earnedIntegral", "0"),
        "proxy_status": result.get("proxyStatus", "直连"),
        "proxy_ip": result.get("proxyIp", "-"),
        "message": result.get("error", ""),
    })


def build_notify_report() -> str:
    total = len(GLOBAL_NOTIFY_BUFFERS)
    success = sum(1 for item in GLOBAL_NOTIFY_BUFFERS if item.get("ok"))
    failed = total - success
    total_points = sum(int(item.get("points") or 0) for item in GLOBAL_NOTIFY_BUFFERS if item.get("ok"))

    status_map = {
        "signed": ("✅", "今日已签"),
        "signin_success": ("🎉", "签到成功"),
        "failed": ("❌", "执行失败"),
        "config_error": ("⚙️", "配置错误"),
        "success": ("✅", "执行成功"),
    }

    lines = [
        "==============================",
        f"🕒 执行时间：{now_text()}",
        f"📊 统计数据：成功 {success} / 总计 {total}",
        f"✅ 成功账号：{success} 个",
        f"❌ 失败账号：{failed} 个",
        f"💰 累计积分：+{total_points}",
        "==============================",
    ]

    for item in GLOBAL_NOTIFY_BUFFERS:
        ok = bool(item.get("ok"))
        status = str(item.get("status") or "unknown")
        status_icon, status_text = status_map.get(status, ("ℹ️", status))
        account_icon = "🧑‍💻" if ok else "🧟"

        lines.extend([
            f"{account_icon} 【账号{item.get('index')}】{item.get('account')}",
            f"{status_icon} 状态：{status_text}",
        ])

        if ok:
            lines.extend([
                f"🎁 结果：{item.get('sign_msg')}",
                f"💰 积分：+{item.get('points')}",
                f"🌐 网络：{item.get('proxy_status')} {item.get('proxy_ip')}",
            ])
        else:
            lines.append(f"🧨 原因：{item.get('message')}")

        lines.append("------------------------------")

    return "\n".join(lines)


def dispatch_notify() -> None:
    if not GLOBAL_NOTIFY_BUFFERS:
        return
    try:
        from SendNotify import send_push_notification
    except Exception as exc:
        print(f"[通知] SendNotify.py 导入失败，已跳过推送：{exc}")
        print(build_notify_report())
        return

    try:
        send_push_notification(SCRIPT_TITLE, build_notify_report())
    except Exception as exc:
        print(f"[通知] 推送失败：{exc}")


# ──────────────────────────────────────────────
# 调度层
# ──────────────────────────────────────────────

def run_account(index: int, total: int, wxid: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "wxid": mask(wxid),
        "success": False,
        "status": "failed",
        "proxyStatus": "直连",
        "proxyIp": "-",
        "signMsg": "-",
        "earnedIntegral": "0",
        "error": "",
    }
    log_account_header(index, total, wxid)

    try:
        proxies, proxy_ip = get_valid_proxy()
        if proxies:
            result["proxyStatus"], result["proxyIp"] = "代理", proxy_ip

        code = get_code(wxid)
        token, _ = login_by_code(code, proxies)
        is_signed, _ = check_sign_status(token, proxies)

        if is_signed:
            result.update({"success": True, "status": "signed", "signMsg": "今日已签", "earnedIntegral": "0"})
            print("  ✅ [签到] 检测：今日已签到")
            return result

        print("  📝 [签到] 检测：未签到，下发签到请求...")
        sign_ok, msg, earned = submit_signin(token, proxies)
        result["signMsg"] = msg
        result["earnedIntegral"] = str(earned)
        if not sign_ok:
            result["error"] = msg
            return result

        if any(keyword in msg for keyword in ("今日已签", "重复签到", "已签到")):
            result.update({"success": True, "status": "signed"})
        else:
            result.update({"success": True, "status": "signin_success"})
        return result
    except Exception as exc:
        result["error"] = str(exc)
        print(f"  ❌ [异常] {exc}")
        return result


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    try:
        accounts = get_accounts()
    except Exception as exc:
        print(f"❌ 配置错误：{exc}")
        GLOBAL_NOTIFY_BUFFERS.append({
            "index": 0,
            "account": "未配置",
            "ok": False,
            "status": "config_error",
            "message": str(exc),
        })
        dispatch_notify()
        return 1

    log_title(len(accounts))
    ok_count = 0
    for idx, wxid in enumerate(accounts, 1):
        result = run_account(idx, len(accounts), wxid)
        append_notify_result(idx, wxid, result)
        if result.get("success"):
            ok_count += 1
        if idx < len(accounts):
            time.sleep(random.randint(2, 5))

    failed = len(accounts) - ok_count
    print("\n╔" + "═" * 50 + "╗")
    print("║  🏁 优智云家任务执行完成  ║")
    print(f"║  ✅ 成功: {ok_count:<39}║")
    print(f"║  ❌ 失败: {failed:<39}║")
    print(f"║  🕒 结束时间: {now_text():<32}║")
    print("╚" + "═" * 50 + "╝")

    dispatch_notify()
    return 0 if ok_count == len(accounts) else 1


if __name__ == "__main__":
    raise SystemExit(main())
