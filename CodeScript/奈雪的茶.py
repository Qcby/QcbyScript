#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
奈雪的茶 v1.1.0（mywc网关聚合推送版）

功能：自动获取奈雪点单小程序 code，登录后执行每日签到并查询奈雪币，支持多账号执行，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL                   必填其一，自建授权服务器域名
   - 示例：http://127.0.0.1:8110
   - 脚本会自动拼接 /mywc
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=wxab7430e6e8b9a4ab
   - 请求头：auth=账号标识

2. 账号变量：
   naixue_wxid 或 NAIXUE_WXID                       推荐，奈雪的茶专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：wxid_a&wxid_b 或 wxid_a,wxid_b

3. 代理变量：
   PROXY_API                                        品赞代理提取链接，可选
   PROXY_TYPE                                       代理类型：http 或 socks5，默认 http

4. 推送变量：
   需要同目录存在 SendNotify.py，脚本结束后会统一调用 send_push_notification。
   常用推送变量如下，配置任意一种即可：
   QYWX_KEY                                         企业微信机器人 key
   PUSH_PLUS_TOKEN                                  PushPlus token
   PUSH_KEY                                         Server 酱 key
   DD_BOT_TOKEN 或 DD_BOT_SECRET                     钉钉机器人 token/secret
   FSKEY                                            飞书机器人 key

5. 青龙任务建议：
   名称：奈雪的茶
   命令：python3 奈雪的茶.py
   定时：每天运行 1 次即可，具体时间自行调整
"""

import base64
import hashlib
import hmac
import json
import os
import random
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

SCRIPT_TITLE = "奈雪的茶"
GLOBAL_NOTIFY_BUFFERS = []

APPID = "wxab7430e6e8b9a4ab"
WX_SERVER_URL = (os.getenv("wx_server_url") or os.getenv("WX_SERVER_URL") or "").rstrip("/")
ACCOUNT_RAW = os.getenv("naixue_wxid") or os.getenv("NAIXUE_WXID") or ""

PROXY_API = os.getenv("PROXY_API", "")
PROXY_TYPE = os.getenv("PROXY_TYPE", "http").lower()
PROXY_RETRY_TIMES = 3
PROXY_VALIDATE_URL = "http://httpbin.org/ip"
ENABLE_PER_ACCOUNT_PROXY = True
PROXY_FETCH_INTERVAL = 3
ENABLE_DIRECT_FALLBACK = True

OPEN_ID = "QL6ZOftGzbziPlZwfiXM"
SIGN_SECRET = "sArMTldQ9tqU19XIRDMWz7BO5WaeBnrezA"
LOGIN_URL = "https://tm-api.pin-dao.cn/passport/authenticate/wxapp/verify/grc"

UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf2541923) XWEB/19823",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781 NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF XWEB/19725",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781 NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF XWEB/19613",
]


def sleep(seconds: float) -> None:
    time.sleep(seconds)


def rand_sleep(min_s: int = 2, max_s: int = 5) -> None:
    sleep(random.randint(min_s, max_s))


def get_ua() -> str:
    return random.choice(UA_LIST)


def random_int_string(length: int) -> str:
    return "".join(random.choice("123456789") for _ in range(length))


def hmac_sha1_base64(secret: str, message: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha1).digest()
    return base64.b64encode(digest).decode("ascii")


def parse_accounts(raw: str) -> list[str]:
    items = []
    for part in str(raw or "").replace("&", "\n").replace(",", "\n").replace("，", "\n").splitlines():
        item = part.strip()
        if item:
            items.append(item)
    return items


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def safe_json(data, limit: int = 500) -> str:
    try:
        return json.dumps(data, ensure_ascii=False)[:limit]
    except Exception:
        return str(data)[:limit]


def build_request_data(extra_params: dict | None = None) -> dict:
    nonce = random_int_string(6)
    timestamp = int(time.time())
    url_path = f"nonce={nonce}&openId={OPEN_ID}&timestamp={timestamp}"
    signature = hmac_sha1_base64(SIGN_SECRET, url_path)

    common = {
        "platform": "wxapp",
        "version": "6.0.42",
        "imei": "",
        "osn": "microsoft",
        "sv": "Windows 10 x64",
        "lat": "",
        "lng": "",
        "lang": "zh_CN",
        "currency": "CNY",
        "timeZone": "",
        "nonce": int(nonce),
        "openId": OPEN_ID,
        "timestamp": timestamp,
        "signature": signature,
    }

    params = {
        "businessType": 1,
        "brand": 26000252,
        "tenantId": 1,
        "channel": 2,
        "stallType": None,
        "storeId": "",
        "storeType": "",
        "cityId": "",
    }

    if extra_params:
        params.update(extra_params)

    return {"common": common, "params": params}


def china_date_parts() -> tuple[int, int, int]:
    now = datetime.now(timezone(timedelta(hours=8)))
    return now.year, now.month, now.day


def mask_account(account: str) -> str:
    account = str(account or "")
    if len(account) <= 4:
        return f"{account[:1]}***" if account else "未知"
    if len(account) <= 10:
        return f"{account[:2]}***{account[-2:]}"
    return f"{account[:4]}***{account[-4:]}"


def mask_phone(phone: str) -> str:
    phone = str(phone or "")
    if len(phone) >= 11:
        return f"{phone[:3]}****{phone[7:]}"
    return phone or "未知"


def extract_wx_code(data) -> str:
    if isinstance(data, str) and data.strip():
        return data.strip()

    if not isinstance(data, dict):
        raise RuntimeError(f"mywc未返回有效JSON：{safe_json(data)}")

    candidates = [
        data.get("code"),
        data.get("wx_code"),
        data.get("wxCode"),
        data.get("data"),
        data.get("result"),
    ]

    for item in candidates:
        if isinstance(item, str) and item.strip():
            return item.strip()
        if isinstance(item, dict):
            for key in ("code", "wx_code", "wxCode"):
                value = item.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

    nested = data.get("data")
    if isinstance(nested, dict) and isinstance(nested.get("data"), dict):
        value = nested["data"].get("code") or nested["data"].get("wx_code")
        if isinstance(value, str) and value.strip():
            return value.strip()

    raise RuntimeError(f"mywc未返回有效code：{safe_json(data)}")


def get_code(wxid: str) -> str:
    if not WX_SERVER_URL:
        raise RuntimeError("未配置 wx_server_url 或 WX_SERVER_URL")

    url = f"{WX_SERVER_URL}/mywc"
    print(f"[{mask_account(wxid)}] 请求 mywc 网关：{url}")

    res = requests.get(
        url,
        params={"wxid": wxid, "appId": APPID},
        headers={"auth": wxid},
        timeout=20,
        proxies={"http": None, "https": None},
    )

    try:
        data = res.json()
    except Exception:
        data = res.text

    code = extract_wx_code(data)
    print(f"[{mask_account(wxid)}] 获取 code 成功")
    return code


# ===================== 品赞代理 =====================

def parse_proxy_response(text) -> dict | None:
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


def build_proxy_dict(proxy_info: dict | None) -> dict | None:
    if not proxy_info:
        return None

    host = proxy_info["host"]
    port = proxy_info["port"]
    username = proxy_info.get("username", "")
    password = proxy_info.get("password", "")

    auth = ""
    if username and password:
        auth = f"{quote(username)}:{quote(password)}@"

    scheme = "socks5" if PROXY_TYPE == "socks5" else "http"
    proxy_url = f"{scheme}://{auth}{host}:{port}"
    print(f"生成代理：{proxy_url}")
    return {"http": proxy_url, "https": proxy_url}


def validate_proxy(proxies: dict | None) -> bool:
    if not proxies:
        return False

    try:
        res = requests.get(PROXY_VALIDATE_URL, proxies=proxies, timeout=15)
        if res.status_code == 200:
            try:
                ip = res.json().get("origin", "未知")
            except Exception:
                ip = "未知"
            print(f"代理验证通过，出口IP：{ip}")
            return True
    except Exception as exc:
        print(f"代理验证失败：{exc}")

    return False


def get_valid_proxy(account_name: str) -> dict | None:
    if not PROXY_API:
        print(f"[{account_name}] 未配置 PROXY_API，使用直连")
        return None

    print(f"[{account_name}] 正在获取品赞代理...")

    for index in range(1, PROXY_RETRY_TIMES + 1):
        try:
            res = requests.get(PROXY_API, timeout=15)
            proxy_info = parse_proxy_response(res.text)

            if not proxy_info:
                print(f"[{account_name}] 第 {index} 次代理解析失败")
                continue

            print(f"[{account_name}] 提取到代理：{proxy_info['host']}:{proxy_info['port']}")
            proxies = build_proxy_dict(proxy_info)

            if validate_proxy(proxies):
                return proxies

            print(f"[{account_name}] 第 {index} 次代理不可用")
        except Exception as exc:
            print(f"[{account_name}] 第 {index} 次获取代理异常：{exc}")

        if index < PROXY_RETRY_TIMES:
            sleep(2)

    print(f"[{account_name}] 获取代理失败，使用直连")
    return None


# ===================== 通知 =====================

def append_notify_result(result: dict) -> None:
    GLOBAL_NOTIFY_BUFFERS.append(result)


def build_notify_report() -> str:
    total = len(GLOBAL_NOTIFY_BUFFERS)
    success = sum(1 for item in GLOBAL_NOTIFY_BUFFERS if item.get("ok"))
    failed = total - success

    lines = [
        "==============================",
        f"🕒 执行时间：{now_text()}",
        f"📊 统计数据：成功 {success} / 总计 {total}",
        f"✅ 成功账号：{success} 个",
        f"❌ 失败账号：{failed} 个",
        "==============================",
    ]

    for item in GLOBAL_NOTIFY_BUFFERS:
        ok = bool(item.get("ok"))
        account_icon = "🧑‍💻" if ok else "🧟"
        lines.extend([
            f"{account_icon} 【账号{item.get('index')}】{item.get('account')}",
            f"{'✅' if ok else '❌'} 状态：{item.get('status')}",
        ])

        if ok:
            lines.extend([
                f"📱 手机：{item.get('phone', '未知')}",
                f"🎯 签到：{item.get('sign_msg', '-')}",
                f"💰 奈雪币：{item.get('coin', '-')}",
                f"🌐 代理：{item.get('proxy_status', '-')}",
            ])
        else:
            lines.append(f"🧨 原因：{item.get('message') or '未知错误'}")
            if item.get("proxy_status"):
                lines.append(f"🌐 代理：{item.get('proxy_status')}")

        lines.append("------------------------------")

    return "\n".join(lines)


def dispatch_notify() -> None:
    if not GLOBAL_NOTIFY_BUFFERS:
        append_notify_result({
            "index": 1,
            "account": "未获取到账号",
            "ok": False,
            "status": "配置错误",
            "message": "未生成任何账号执行结果",
        })

    try:
        from SendNotify import send_push_notification
        send_push_notification(f"{SCRIPT_TITLE}任务执行结果", build_notify_report())
    except Exception as exc:
        print(f"通知推送失败：{exc}")
        print(build_notify_report())


# ===================== 请求封装 =====================

def request_with_proxy(method: str, url: str, *, proxies: dict | None = None, account_name: str = "", **kwargs):
    kwargs.setdefault("timeout", 30)

    if proxies:
        try:
            return requests.request(method, url, proxies=proxies, **kwargs)
        except Exception as exc:
            print(f"[{account_name}] 代理请求失败：{exc}")
            if not ENABLE_DIRECT_FALLBACK:
                raise
            print(f"[{account_name}] 切换直连重试")

    return requests.request(method, url, **kwargs)


def extract_token(data) -> str | None:
    if not isinstance(data, dict):
        return None

    candidates = [
        data.get("token"),
        data.get("accessToken"),
        data.get("access_token"),
        data.get("authToken"),
        data.get("memberToken"),
    ]

    inner = data.get("data")
    if isinstance(inner, dict):
        candidates.extend([
            inner.get("token"),
            inner.get("accessToken"),
            inner.get("access_token"),
            inner.get("authToken"),
            inner.get("memberToken"),
            inner.get("access_token_value"),
        ])

        token_info = inner.get("tokenInfo")
        if isinstance(token_info, dict):
            candidates.extend([
                token_info.get("token"),
                token_info.get("accessToken"),
                token_info.get("access_token"),
            ])

        user_token = inner.get("userToken")
        if isinstance(user_token, dict):
            candidates.extend([
                user_token.get("token"),
                user_token.get("accessToken"),
                user_token.get("access_token"),
            ])

    for item in candidates:
        if item and item != "null":
            return str(item)

    return None


def login_by_code(code: str, ua: str, proxies: dict | None, account_name: str) -> tuple[str | None, dict | None]:
    headers = {
        "Host": "tm-api.pin-dao.cn",
        "Connection": "keep-alive",
        "Authorization": "Bearer null",
        "User-Agent": ua,
        "xweb_xhr": "1",
        "storeId": "",
        "Content-Type": "application/json",
        "iv": random_int_string(16),
        "Accept": "*/*",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": f"https://servicewechat.com/{APPID}/819/page-frame.html",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }

    body = build_request_data({
        "appId": APPID,
        "dAId": "",
        "type": 3,
        "wxappCode": code,
        "regChannelCode": "|1027",
    })

    try:
        res = request_with_proxy(
            "POST",
            LOGIN_URL,
            headers=headers,
            data=json.dumps(body, separators=(",", ":"), ensure_ascii=False),
            proxies=proxies,
            account_name=account_name,
        )

        try:
            data = res.json()
        except Exception:
            data = {"raw": res.text[:500]}

        token = extract_token(data)
        if token:
            print(f"[{account_name}] 登录成功，已获取 token")
            return token, data

        print(f"[{account_name}] 登录成功但未识别 token 字段：{safe_json(data, 800)}")
        return None, data
    except Exception as exc:
        print(f"[{account_name}] 登录异常：{exc}")
        return None, None


def call_api(url: str, token: str, ua: str, proxies: dict | None, account_name: str, body: dict | None = None) -> dict:
    headers = {
        "User-Agent": ua,
        "Authorization": f"Bearer {token}",
        "Referer": "https://tm-web.pin-dao.cn/",
        "Origin": "https://tm-web.pin-dao.cn",
        "Content-Type": "application/json",
    }

    payload = build_request_data(body or {})

    try:
        res = request_with_proxy(
            "POST",
            url,
            headers=headers,
            data=json.dumps(payload, separators=(",", ":"), ensure_ascii=False),
            proxies=proxies,
            account_name=account_name,
        )
        return res.json()
    except Exception as exc:
        return {"code": -1, "message": str(exc)}


# ===================== 业务逻辑 =====================

def run_account(wxid: str, index: int, global_proxy: dict | None = None) -> None:
    account_name = mask_account(wxid)
    result = {
        "index": index,
        "account": account_name,
        "ok": False,
        "status": "执行失败",
        "phone": "未知",
        "proxy_status": "未使用代理",
        "sign_msg": "-",
        "coin": "-",
        "message": "",
    }

    print(f"\n===== {SCRIPT_TITLE} - 账号{index} {account_name} =====")
    ua = get_ua()

    proxies = global_proxy
    if ENABLE_PER_ACCOUNT_PROXY:
        proxies = get_valid_proxy(account_name)
        result["proxy_status"] = "使用专属代理" if proxies else "使用直连"
        sleep(PROXY_FETCH_INTERVAL)

    try:
        delay = random.randint(2, 6)
        print(f"[{account_name}] 启动延迟 {delay}s")
        sleep(delay)

        code = get_code(wxid)
        token, _ = login_by_code(code, ua, proxies, account_name)
        if not token:
            result["message"] = "登录失败或未识别 token 字段"
            return

        rand_sleep(2, 5)
        userinfo = call_api(
            "https://tm-web.pin-dao.cn/user/base-userinfo",
            token,
            ua,
            proxies,
            account_name,
            {},
        )

        if userinfo.get("code") != 0:
            result["message"] = f"查询用户信息失败：{userinfo.get('message') or '未知错误'}"
            return

        phone = userinfo.get("data", {}).get("phone", "")
        result["phone"] = mask_phone(phone)
        print(f"[{account_name}] 登录账号：{result['phone']}")

        year, month, day = china_date_parts()
        sign_date = f"{year}-{month:02d}-01"
        today = f"{year}-{month:02d}-{day:02d}"

        sign_records = call_api(
            "https://tm-web.pin-dao.cn/user/sign/records",
            token,
            ua,
            proxies,
            account_name,
            {"signDate": sign_date, "startDate": today},
        )

        sign_ok = False
        if sign_records.get("code") != 0:
            result["sign_msg"] = f"查询签到失败：{sign_records.get('message') or '未知错误'}"
            print(f"[{account_name}] {result['sign_msg']}")
        else:
            status = bool(sign_records.get("data", {}).get("status"))
            count = sign_records.get("data", {}).get("signCount", "-")
            print(f"[{account_name}] 今天{'已' if status else '未'}签到，已签到 {count} 天")

            if status:
                sign_ok = True
                result["sign_msg"] = f"今日已签到，累计 {count} 天"
            else:
                sign_save = call_api(
                    "https://tm-web.pin-dao.cn/user/sign/save",
                    token,
                    ua,
                    proxies,
                    account_name,
                    {"signDate": today},
                )

                if sign_save.get("code") == 0 and sign_save.get("data", {}).get("flag"):
                    sign_ok = True
                    result["sign_msg"] = "签到成功"
                    print(f"[{account_name}] 签到成功")
                else:
                    result["sign_msg"] = f"签到失败：{sign_save.get('message') or '未知错误'}"
                    print(f"[{account_name}] {result['sign_msg']}")

        rand_sleep(2, 5)
        account = call_api(
            "https://tm-web.pin-dao.cn/user/account/user-account",
            token,
            ua,
            proxies,
            account_name,
            {},
        )

        if account.get("code") == 0:
            result["coin"] = account.get("data", {}).get("coin", "-")
            print(f"[{account_name}] 当前奈雪币：{result['coin']}")
        else:
            print(f"[{account_name}] 查询奈雪币失败：{account.get('message') or '未知错误'}")

        if sign_ok:
            result["ok"] = True
            result["status"] = "执行成功"
            result["message"] = result["sign_msg"]
        else:
            result["message"] = result["sign_msg"] or "签到失败"
    except Exception as exc:
        result["message"] = str(exc)
        print(f"[{account_name}] 执行异常：{exc}")
    finally:
        append_notify_result(result)


def main() -> None:
    print(f"===== {SCRIPT_TITLE} mywc网关聚合推送版 =====\n")

    accounts = parse_accounts(ACCOUNT_RAW)
    if not accounts:
        append_notify_result({
            "index": 1,
            "account": "未配置",
            "ok": False,
            "status": "配置错误",
            "message": "请配置 naixue_wxid 或 NAIXUE_WXID，多个账号用 &、英文逗号、中文逗号或换行分隔",
        })
        return

    if not WX_SERVER_URL:
        for index, account in enumerate(accounts, 1):
            append_notify_result({
                "index": index,
                "account": mask_account(account),
                "ok": False,
                "status": "配置错误",
                "message": "请配置 wx_server_url 或 WX_SERVER_URL",
            })
        return

    global_proxy = None
    if not ENABLE_PER_ACCOUNT_PROXY:
        global_proxy = get_valid_proxy("全局共用")

    for index, wxid in enumerate(accounts, 1):
        run_account(wxid, index, global_proxy)
        if index < len(accounts):
            sleep(2)


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        append_notify_result({
            "index": len(GLOBAL_NOTIFY_BUFFERS) + 1,
            "account": "全局异常",
            "ok": False,
            "status": "执行失败",
            "message": str(err),
        })
        print(f"全局异常：{err}")
    finally:
        dispatch_notify()
        print("\n===== 所有账号执行完成 =====")
