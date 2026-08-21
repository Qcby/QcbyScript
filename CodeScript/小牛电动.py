#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
小牛电动 v1.1.0（mywc网关聚合推送版）

功能：自动执行小牛电动小程序登录、每日分享、任务状态验证和积分流水查询，支持多账号执行，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL                   必填其一，自建授权服务器域名
   - 示例：http://127.0.0.1:8110
   - 脚本会自动拼接 /mywc
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=wx496829086cb0a118
   - 请求头：auth=账号标识

2. 账号变量：
   niu_wxid 或 NIU_WXID                             推荐，小牛电动专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：wxid_a&wxid_b 或 wxid_a,wxid_b
   - 兼容旧变量 NIU_OPENID / NIU_WX 读取

3. 推送变量：
   需要同目录存在 SendNotify.py，脚本结束后会统一调用 send_push_notification。
   常用推送变量如下，配置任意一种即可：
   QYWX_KEY                                         企业微信机器人 key
   PUSH_PLUS_TOKEN                                  PushPlus token
   PLUSPLUS_TOKEN                                   兼容旧 PushPlus token
   PUSH_KEY                                         Server 酱 key
   DD_BOT_TOKEN 或 DD_BOT_SECRET                     钉钉机器人 token/secret
   FSKEY                                            飞书机器人 key

4. 可选变量：
   PROXY_API                                        品赞代理提取 API，可选
   PROXY_TYPE                                       http / socks5，默认 http
   NIU_TOKEN                                        已有 token，可选，配置后跳过 code 登录
   NIU_AUTH_API                                     code 换 token 接口，默认小牛小程序 oauth2/token
   NIU_MAX_SHARE                                    每日分享上限，默认 2
   NIU_STATE_DIR                                    状态文件目录，默认脚本目录

5. 青龙任务建议：
   名称：小牛电动
   命令：python3 小牛电动.py
   定时：每天运行 1 次即可，具体时间自行调整

依赖：
  pip install requests
  socks5 代理需：pip install requests[socks]
"""

import base64
import hashlib
import json
import os
import random
import sys
import time
import traceback
from datetime import datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import quote

import requests

try:
    from SendNotify import send_push_notification
except Exception:
    send_push_notification = None

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


SCRIPT_TITLE = "小牛电动"
APP_NAME = "小牛电动小程序"
APPID = "wx496829086cb0a118"
NIU_APP_ID = "niu_89cyuop8"
GLOBAL_NOTIFY_BUFFERS: List[Dict[str, Any]] = []

WX_SERVER_URL = (
    os.getenv("wx_server_url")
    or os.getenv("WX_SERVER_URL")
    or ""
).strip().rstrip("/")
NIU_WXID_RAW = (
    os.getenv("niu_wxid")
    or os.getenv("NIU_WXID")
    or os.getenv("NIU_OPENID")
    or os.getenv("NIU_WX")
    or ""
)

PLUSPLUS_TOKEN = os.getenv("PLUSPLUS_TOKEN", "")
PROXY_API = os.getenv("PROXY_API", "")
PROXY_TYPE = os.getenv("PROXY_TYPE", "http").lower()

PROXY_RETRY_TIMES = 3
PROXY_VALIDATE_URL = "http://httpbin.org/ip"
PROXY_FETCH_INTERVAL = 3
ENABLE_DIRECT_FALLBACK = True
REQUEST_TIMEOUT = 30

NIU_TOKEN = os.getenv("NIU_TOKEN", "").strip()
NIU_MAX_SHARE = int(os.getenv("NIU_MAX_SHARE", "2"))
DEFAULT_STATE_DIR = os.path.dirname(os.path.abspath(__file__))
NIU_STATE_DIR = os.getenv("NIU_STATE_DIR", DEFAULT_STATE_DIR)

AUTH_API = os.getenv(
    "NIU_AUTH_API",
    "https://account-miniapp.niucache.com/v3/api/oauth2/token",
)
CHECK_AUTH_API = os.getenv(
    "NIU_CHECK_AUTH_API",
    "https://account-miniapp.niucache.com/v3/api/auth/wx-mini/check_auth",
)
APP_API = os.getenv("NIU_APP_API", "https://app-api.niu.com")
STORE_API = os.getenv("NIU_STORE_API", "https://store.niu.com")

STORE_LOGIN_URL = f"{STORE_API}/api/auth/login"
RECOMMEND_URL = f"{APP_API}/community/api/posts/recommend/list"
SHARE_URL = f"{APP_API}/community/api/posts/shares"
DETAIL_URL = f"{APP_API}/community/api/posts/detail"
COMMENTS_URL = f"{APP_API}/community/api/posts/comments/list"
TASK_URL = f"{STORE_API}/api/integral/task"
INTEGRAL_LIST_URL = f"{STORE_API}/api/integral/list"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 "
    "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "MiniProgramEnv/Windows WindowsWechat/WMPF "
    "WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf2541b37) XWEB/20089"
)

# 服务器按北京时间(UTC+8)结算每日任务
SHANGHAI_OFFSET = 8 * 3600


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def sleep(seconds: float) -> None:
    time.sleep(seconds)


def mask(value: Any) -> str:
    value = str(value or "")
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-6:]}"


def parse_accounts(raw: str) -> List[str]:
    return [
        item.strip()
        for item in raw.replace("，", ",").replace(",", "&").replace("&", "\n").splitlines()
        if item.strip()
    ]


def json_preview(data: Any, limit: int = 800) -> str:
    try:
        return json.dumps(data, ensure_ascii=False)[:limit]
    except Exception:
        return str(data)[:limit]


def log_title(total: int) -> None:
    print()
    print("╔" + "═" * 50 + "╗")
    print("║ 🛵 小牛电动 mywc版                         ║")
    print(f"║ 🕒 启动时间: {now_text():<32}║")
    print(f"║ 🔢 账号数量: {total:<34}║")
    print("╚" + "═" * 50 + "╝")


def log_account_header(index: int, total: int, account: str) -> None:
    print()
    print("┌" + "─" * 50 + "┐")
    print(f"│ 🧩 账号 {index} / {total:<37}│")
    print(f"│ 🆔 标识 {mask(account):<40}│")
    print("└" + "─" * 50 + "┘")


def direct_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session


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

    auth = ""
    if username and password:
        auth = f"{quote(username)}:{quote(password)}@"

    scheme = "socks5" if PROXY_TYPE == "socks5" else "http"
    proxy_url = f"{scheme}://{auth}{host}:{port}"

    print(f"🛠️ [代理] 生成 {scheme.upper()} 代理 {host}:{port}")

    return {
        "http": proxy_url,
        "https": proxy_url,
    }


def validate_proxy(proxies: Dict[str, str] | None) -> Tuple[bool, str]:
    if not proxies:
        return False, ""

    try:
        response = requests.get(PROXY_VALIDATE_URL, proxies=proxies, timeout=15)
        if response.status_code == 200:
            try:
                ip = response.json().get("origin", "未知")
            except Exception:
                ip = "未知"
            print(f"✅ [代理] 验证通过，出口 IP: {ip}")
            return True, ip
    except Exception as exc:
        print(f"⚠️ [代理] 验证失败: {exc}")

    return False, ""


def get_valid_proxy(account_name: str) -> Tuple[Dict[str, str] | None, str]:
    if not PROXY_API:
        print(f"⚠️ [代理] {account_name} 未配置 PROXY_API，使用直连")
        return None, ""

    print(f"🌐 [代理] {account_name} 正在获取品赞代理...")

    for index in range(1, PROXY_RETRY_TIMES + 1):
        try:
            response = direct_session().get(PROXY_API, timeout=15)
            proxy_info = parse_proxy_response(response.text)

            if not proxy_info:
                print(f"⚠️ [代理] 第 {index} 次代理解析失败")
                continue

            print(f"✅ [代理] 提取到 {proxy_info['host']}:{proxy_info['port']}")
            proxies = build_proxy_dict(proxy_info)

            ok, ip = validate_proxy(proxies)
            if ok:
                return proxies, ip

            print(f"⚠️ [代理] 第 {index} 次代理不可用")
        except Exception as exc:
            print(f"⚠️ [代理] 第 {index} 次获取代理异常: {exc}")

        if index < PROXY_RETRY_TIMES:
            sleep(2)

    print("⚠️ [代理] 获取失败，使用直连")
    return None, ""


def request_with_proxy(
    method: str,
    url: str,
    *,
    proxies: Dict[str, str] | None = None,
    server: str = "",
    **kwargs,
) -> requests.Response:
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)

    if proxies:
        try:
            return requests.request(method, url, proxies=proxies, **kwargs)
        except Exception as exc:
            print(f"⚠️ [代理] {server} 代理请求失败: {exc}")
            if not ENABLE_DIRECT_FALLBACK:
                raise
            print("🔁 [兜底] 切换直连重试")

    session = direct_session()
    return session.request(method, url, **kwargs)


def send_pushplus(title: str, content: str) -> None:
    token = PLUSPLUS_TOKEN or os.getenv("PUSH_PLUS_TOKEN", "")
    if not token:
        print("⚠️ [PushPlus] 未配置 PLUSPLUS_TOKEN/PUSH_PLUS_TOKEN，跳过 PushPlus 兼容推送")
        return

    try:
        requests.post(
            "https://www.pushplus.plus/send",
            json={
                "token": token,
                "title": title,
                "content": content,
                "template": "txt",
            },
            timeout=10,
        )
        print("✅ [PushPlus] 兼容推送成功")
    except Exception as exc:
        print(f"❌ [PushPlus] 兼容推送失败: {exc}")


def dispatch_notify(title: str, content: str) -> None:
    if send_push_notification:
        try:
            send_push_notification(title, content)
            print("✅ [通知] SendNotify 聚合推送完成")
            return
        except Exception as exc:
            print(f"❌ [通知] SendNotify 推送异常: {exc}")

    send_pushplus(title, content)


def gateway_url(path: str) -> str:
    if not WX_SERVER_URL:
        raise RuntimeError("请配置 wx_server_url 或 WX_SERVER_URL")
    return f"{WX_SERVER_URL.rstrip('/')}/{path.lstrip('/')}"


def get_code(wxid: str) -> str | None:
    url = gateway_url("/mywc")
    print(f"🔐 [授权] 请求 mywc code 网关: {url}")

    try:
        response = direct_session().get(
            url,
            params={"wxid": wxid, "appId": APPID},
            headers={"auth": wxid},
            timeout=20,
        )
        data = response.json()

        code_value = (
            data.get("code")
            or (data.get("data") or {}).get("code")
            or ((data.get("data") or {}).get("result") or {}).get("code")
            or (data.get("result") or {}).get("code")
        )
        if data.get("err") not in (None, 0, "0") or not code_value or str(code_value) == "null":
            print(f"❌ [授权] code 获取失败: {json_preview(data)}")
            return None

        print("✅ [授权] code 获取成功")
        return str(code_value)
    except Exception as exc:
        print(f"❌ [授权] code 获取异常: {exc}")
        return None


def common_headers(token: str | None = None, token_header: str = "token") -> Dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json",
        "Accept": "*/*",
        "xweb_xhr": "1",
        "Referer": f"https://servicewechat.com/{APPID}/77/page-frame.html",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    if token:
        headers[token_header] = token
    return headers


def extract_token(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None

    candidates = [
        data.get("token"),
        data.get("accessToken"),
        data.get("access_token"),
        data.get("jwt"),
    ]

    inner = data.get("data")
    if isinstance(inner, dict):
        candidates.extend([
            inner.get("token"),
            inner.get("accessToken"),
            inner.get("access_token"),
            inner.get("jwt"),
        ])

        token_obj = inner.get("token")
        if isinstance(token_obj, dict):
            candidates.extend([
                token_obj.get("access_token"),
                token_obj.get("accessToken"),
                token_obj.get("jwt"),
                token_obj.get("token"),
            ])

        user = inner.get("user")
        if isinstance(user, dict):
            candidates.extend([
                user.get("token"),
                user.get("accessToken"),
                user.get("access_token"),
                user.get("jwt"),
            ])

    for item in candidates:
        if isinstance(item, str) and item and item != "null":
            return item
        if isinstance(item, dict):
            for key in ("access_token", "accessToken", "jwt", "token"):
                value = item.get(key)
                if isinstance(value, str) and value and value != "null":
                    return value

    return None


def login_headers() -> Dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "*/*",
        "xweb_xhr": "1",
        "Referer": f"https://servicewechat.com/{APPID}/77/page-frame.html",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    return headers


def check_auth(server: str, code: str, proxies: Dict[str, str] | None) -> str | None:
    """check_auth 用 wx code 换取 open_id(小程序真实授权流程)"""
    print("🔐 [授权] check_auth 用 code 换取 open_id")
    try:
        response = request_with_proxy(
            "POST",
            CHECK_AUTH_API,
            headers=common_headers(),
            json={
                "mp_code": code,
                "app_id": NIU_APP_ID,
                "mini_app_id": APPID,
            },
            proxies=proxies,
            server=server,
        )

        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:800]}

        status = data.get("status") if isinstance(data, dict) else None
        desc = data.get("desc") if isinstance(data, dict) else ""
        inner = data.get("data") if isinstance(data, dict) else None
        open_id = inner.get("open_id") if isinstance(inner, dict) else None

        if status == 0 and open_id:
            print(f"✅ [授权] open_id 获取成功: {mask(open_id)}")
            return open_id

        if status == 20021:
            print("⚠️ [授权] 授权频繁被限流，请 10 分钟后重试")
        elif status in (40029, 40163, 40242):
            print("⚠️ [授权] code 不合法或已过期，请确认本地 code 服务有有效 code")
        else:
            print(f"⚠️ [授权] check_auth 失败: {desc or '未知错误'} (status={status})")

        print(f"❌ [授权] 未识别 open_id 字段: {json_preview(data)}")
        return None
    except Exception as exc:
        print(f"❌ [授权] check_auth 请求异常: {exc}")
        return None


def login_by_code(server: str, code: str, proxies: Dict[str, str] | None) -> Tuple[str | None, Dict[str, Any] | None]:
    try:
        open_id = check_auth(server, code, proxies)
        if not open_id:
            return None, {"desc": "check_auth 未返回 open_id"}

        print("🔐 [登录] 使用 open_id 换 token (grant_type=new_mini_code)")
        response = request_with_proxy(
            "POST",
            AUTH_API,
            headers=login_headers(),
            data={
                "scope": "base",
                "open_id": open_id,
                "mini_app_id": APPID,
                "grant_type": "new_mini_code",
                "mini_code": "",
                "mp_code": "",
                "app_id": NIU_APP_ID,
                "account": "",
                "country_code": "",
                "captcha": "",
            },
            proxies=proxies,
            server=server,
        )

        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:800]}

        token = extract_token(data)
        if token:
            print(f"✅ [登录] token 获取成功: {mask(token)}")
            return token, data

        status = data.get("status") if isinstance(data, dict) else None
        desc = data.get("desc") if isinstance(data, dict) else ""

        if status == 20021:
            print("⚠️ [登录] 登录频繁被限流，请 10 分钟后重试")
        elif status == 20052:
            print("⚠️ [登录] 请重新进入微信小程序后重试(20052)")
        elif status in (40029, 40163, 40242):
            print("⚠️ [登录] code 不合法或已过期，请确认本地 code 服务有有效 code")
        else:
            print(f"⚠️ [登录] 登录失败: {desc or '未知错误'} (status={status})")

        print(f"❌ [登录] 未识别 token 字段: {json_preview(data)}")
        return None, data
    except Exception as exc:
        print(f"❌ [登录] 请求异常: {exc}")
        return None, None


def api_get(server: str, url: str, token: str, proxies: Dict[str, str] | None,
            token_header: str = "token") -> Dict[str, Any]:
    response = request_with_proxy(
        "GET",
        url,
        headers=common_headers(token, token_header),
        proxies=proxies,
        server=server,
    )
    try:
        return response.json()
    except Exception:
        return {
            "code": -1,
            "msg": f"JSON解析失败: {response.text[:300]}",
        }


def api_post(server: str, url: str, token: str, proxies: Dict[str, str] | None,
             payload: Dict[str, Any], token_header: str = "token") -> Dict[str, Any]:
    response = request_with_proxy(
        "POST",
        url,
        headers=common_headers(token, token_header),
        json=payload,
        proxies=proxies,
        server=server,
    )
    try:
        return response.json()
    except Exception:
        return {
            "code": -1,
            "msg": f"JSON解析失败: {response.text[:300]}",
        }


def jwt_exp(token: str) -> int | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data.get("exp")
    except Exception:
        return None


def jwt_sub(token: str) -> str | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("sub")
    except Exception:
        return None


def show_token_status(token: str) -> None:
    exp = jwt_exp(token)
    if exp:
        left = exp - int(time.time())
        if left <= 0:
            print(f"  [Token] 已过期")
        else:
            print(f"  [Token] 有效期至 {time.strftime('%Y-%m-%d %H:%M', time.localtime(exp))} "
                  f"(剩余 {left // 86400} 天)")
    else:
        print(f"  [Token] 非 JWT, 未做过期检查")


def store_login(server: str, token: str, proxies: Dict[str, str] | None) -> str:
    """用主 token 换取 store 积分 token, 失败时直接复用主 token"""
    r = api_post(server, STORE_LOGIN_URL, token, proxies, {"access_token": token})
    if r.get("status") in (200, 0):
        store_token = (r.get("data") or {}).get("token")
        if store_token:
            print(f"✅ [登录] store token 获取成功: {mask(store_token)}")
            return store_token
    print(f"⚠️ [登录] store 换 token 失败, x-token 直接使用主 token: {json_preview(r, 200)}")
    return token


def today_cn() -> str:
    # 固定按北京时间(UTC+8)取日期, 避免本机时区已是 UTC+8 时重复加偏移
    return time.strftime("%Y-%m-%d",
                         time.gmtime(time.time() + SHANGHAI_OFFSET))


def state_path_for(server: str, token: str) -> str:
    sub = jwt_sub(token)
    ident = sub[:12] if sub else hashlib.md5(server.encode()).hexdigest()[:12]
    return os.path.join(NIU_STATE_DIR, f"niu_shared_{ident}.json")


def load_state(server: str, token: str) -> Dict[str, Any]:
    path = state_path_for(server, token)
    state = {"shared": {}}  # {post_id: "YYYY-MM-DD"}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data.get("shared"), dict):
                state["shared"] = data["shared"]
        except Exception:
            pass
    state["_path"] = path
    return state


def save_state(state: Dict[str, Any]) -> None:
    path = state.get("_path")
    out = {"shared": state["shared"]}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def get_task_status(server: str, store_token: str, proxies: Dict[str, str] | None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    r = api_get(server, f"{TASK_URL}?type=2&version=v2", store_token, proxies,
                token_header="x-token")
    status = {}
    for t in (r.get("data") or []):
        if "每日分享" in t.get("name", ""):
            status["share"] = t.get("status")
    return status, r


def get_today_share_count(server: str, store_token: str, proxies: Dict[str, str] | None) -> int:
    """查询 store 当日「每日分享」积分到账条数(按北京时间)"""
    today = today_cn()
    count = 0
    for page in range(1, 5):
        r = api_get(server, f"{INTEGRAL_LIST_URL}?page={page}&limit=50",
                    store_token, proxies, token_header="x-token")
        items = r.get("data") or []
        if not items:
            break
        for it in items:
            add_time = str(it.get("add_time") or "")
            if add_time < today:
                return count
            if add_time.startswith(today) and "每日分享" in str(it.get("mark") or ""):
                count += 1
    return count


def fetch_recommend(server: str, token: str, proxies: Dict[str, str] | None,
                    pages: int = 10) -> List[Tuple[int, str]]:
    posts = []
    for page in range(1, pages + 1):
        r = api_get(server, f"{RECOMMEND_URL}?page={page}&page_size=20&version=0",
                    token, proxies)
        items = (r.get("data") or {}).get("items") or []
        if not items:
            break
        for it in items:
            pid = it.get("id")
            if pid:
                title = (it.get("title") or it.get("content") or "")[:40]
                posts.append((pid, title))
    return posts


def share_post(server: str, token: str, proxies: Dict[str, str] | None,
               post_id: int) -> Dict[str, Any]:
    return api_post(server, SHARE_URL, token, proxies, {"id": int(post_id)})


def simulate_browse(server: str, token: str, proxies: Dict[str, str] | None,
                    post_id: int) -> None:
    time.sleep(1)
    try:
        api_get(server, f"{DETAIL_URL}?id={int(post_id)}&version=2", token, proxies)
        time.sleep(1)
        api_get(server, f"{COMMENTS_URL}?hide_hot=1&page=1&page_size=20"
                        f"&parent_id={int(post_id)}&parent_type=1&version=2",
                token, proxies)
    except Exception:
        pass


def pick_new_posts(server: str, token: str, proxies: Dict[str, str] | None,
                   state: Dict[str, Any], limit: int) -> List[int]:
    exclude = set(state["shared"].keys())
    new_posts = []
    for pid, title in fetch_recommend(server, token, proxies):
        if str(pid) not in exclude:
            new_posts.append(pid)
            print(f"  候选帖子: {pid} {title}")
            if len(new_posts) >= limit:
                break
    return new_posts


def record_share(state: Dict[str, Any], post_id: int, today: str) -> None:
    state["shared"][str(post_id)] = today


def wait_share_points(server: str, store_token: str,
                        proxies: Dict[str, str] | None, before: int,
                        max_wait: int = 25) -> int:
    """轮询等待「每日分享」积分到账, 最多 max_wait 秒, 返回最新到账条数"""
    waited = 0
    while waited < max_wait:
        time.sleep(5)
        waited += 5
        count = get_today_share_count(server, store_token, proxies)
        if count > before:
            return count
    return before


def do_daily_share(server: str, token: str, store_token: str,
                   proxies: Dict[str, str] | None, state: Dict[str, Any],
                   post_id: int | None = None, force: bool = False) -> str:
    print("📝 [分享] 每日分享(文章/片刻)")
    today = today_cn()

    done = 0 if force else get_today_share_count(server, store_token, proxies)
    if done >= NIU_MAX_SHARE:
        msg = f"今日「每日分享」积分已到账 {done}/{NIU_MAX_SHARE} 条, 已达标, 跳过"
        print(f"✅ [分享] {msg}")
        return msg

    if post_id is not None:
        targets = [post_id]
    else:
        targets = pick_new_posts(server, token, proxies, state,
                                 NIU_MAX_SHARE - done)

    if not targets:
        msg = "推荐列表中没有未分享过的新帖子, 请检查 token 或稍后重试"
        print(f"⚠️ [分享] {msg}")
        return msg

    shared: List[int] = []
    for pid in targets:
        r = share_post(server, token, proxies, pid)
        ok = (r.get("_error") is None
              and (r.get("code") in (None, 0, 200)
                   or "成功" in str(r.get("msg", "")) + str(r.get("desc", ""))
                   or r.get("data") is not None))
        if not ok:
            print(f"❌ [分享] posts/shares {pid}: {json_preview(r, 300)} "
                  f"(接口失败, 不计入记录, 下轮可重试)")
            time.sleep(1)
            continue

        print(f"📤 [分享] posts/shares {pid}: 提交成功, 等待积分到账确认...")
        simulate_browse(server, token, proxies, pid)
        time.sleep(1)
        new_done = wait_share_points(server, store_token, proxies, done)
        if new_done > done:
            print(f"✅ [分享] [OK] {pid} 积分已到账 (今日 {new_done}/{NIU_MAX_SHARE})")
            done = new_done
        else:
            print(f"⚠️ [分享] [X] {pid} 积分未到账, 该帖此前可能已分享过, 标记后换下一篇")
        # 无论是否到账都记录, 避免下次重复尝试该帖
        record_share(state, pid, today)
        save_state(state)
        shared.append(pid)
        time.sleep(1)
        if done >= NIU_MAX_SHARE:
            break

    return f"分享 {len(shared)} 篇, 今日积分到账 {done}/{NIU_MAX_SHARE} 条"


def verify(server: str, store_token: str, proxies: Dict[str, str] | None) -> str:
    print("📋 [验证] 任务状态与积分流水")
    time.sleep(2)
    _, r = get_task_status(server, store_token, proxies)
    lines: List[str] = []
    for t in (r.get("data") or []):
        if "每日分享" in t.get("name", ""):
            st = {0: "未完成", 1: "进行中", 2: "已完成"}.get(t.get("status"),
                                                           t.get("status"))
            line = f"[{st}] {t.get('name')} ({t.get('point')}分)"
            lines.append(line)
            print(f"  {line}")

    r2 = api_get(server, f"{INTEGRAL_LIST_URL}?page=1&limit=5", store_token,
                 proxies, token_header="x-token")
    print("  最近积分流水:")
    for item in (r2.get("data") or [])[:5]:
        line = (f"{item.get('add_time')} {item.get('mark')} "
                f"+{item.get('number')} 余额{item.get('balance')}")
        lines.append(line)
        print(f"    {line}")

    return "；".join(lines) if lines else "未获取到任务/流水数据"


def run_account(index: int, total: int, account: str) -> Dict[str, Any]:
    result = {
        "wxid": account,
        "server": mask(account),
        "success": False,
        "proxyStatus": "未使用代理",
        "proxyIp": "-",
        "token": "-",
        "codeMsg": "-",
        "loginMsg": "-",
        "shareMsg": "-",
        "pointsMsg": "-",
        "error": "",
    }

    log_account_header(index, total, account)

    proxies, proxy_ip = get_valid_proxy(mask(account))
    result["proxyStatus"] = "使用专属代理" if proxies else "使用直连"
    result["proxyIp"] = proxy_ip or "-"

    sleep(PROXY_FETCH_INTERVAL)

    delay = random.randint(2, 6)
    print(f"⏳ [延迟] 启动延迟 {delay}s")
    sleep(delay)

    if NIU_TOKEN:
        token = NIU_TOKEN
        result["codeMsg"] = "使用环境变量 NIU_TOKEN"
        result["loginMsg"] = f"token 读取成功: {mask(token)}"
        print(f"🔐 [登录] 读取环境变量 NIU_TOKEN: {mask(token)}")
    else:
        code = get_code(account)
        result["codeMsg"] = "code 获取成功" if code else "code 获取失败"
        if not code:
            result["error"] = "获取 code 失败"
            return result

        token, raw_login = login_by_code(account, code, proxies)
        if not token:
            result["error"] = f"登录失败: {json_preview(raw_login)}"
            return result
        result["loginMsg"] = f"code 换 token 成功: {mask(token)}"

    result["token"] = mask(token)
    show_token_status(token)

    try:
        store_token = store_login(account, token, proxies)

        state = load_state(account, token)
        print(f"📁 [状态] 已记录历史分享 {len(state['shared'])} 篇: {state['_path']}")

        result["shareMsg"] = do_daily_share(account, token, store_token,
                                            proxies, state)
        result["pointsMsg"] = verify(account, store_token, proxies)

        result["success"] = True
        return result
    except Exception as exc:
        result["error"] = traceback.format_exc().strip()
        print(f"❌ [账号] 执行失败: {exc}")
        return result


def append_notify_result(result: Dict[str, Any]) -> None:
    GLOBAL_NOTIFY_BUFFERS.append(result)


def empty_result(account: str, error: str) -> Dict[str, Any]:
    return {
        "wxid": account,
        "server": mask(account),
        "success": False,
        "proxyStatus": "-",
        "proxyIp": "-",
        "token": "-",
        "codeMsg": "-",
        "loginMsg": "-",
        "shareMsg": "-",
        "pointsMsg": "-",
        "error": error,
    }


def build_notify(results: List[Dict[str, Any]]) -> str:
    success_count = sum(1 for item in results if item["success"])
    fail_count = len(results) - success_count

    lines = [
        "==============================",
        f"🕒 执行时间：{now_text()}",
        f"📊 统计数据：成功 {success_count} / 总计 {len(results)}",
        f"✅ 成功账号：{success_count} 个",
        f"❌ 失败账号：{fail_count} 个",
        "📋 积分任务：详见账号明细",
        "==============================",
    ]

    for idx, res in enumerate(results, 1):
        lines.append(f"🧑‍💻 【账号{idx}】{mask(res.get('wxid') or res.get('server') or '')}")
        if res["success"]:
            lines.extend([
                "✅ 状态：执行成功",
                f"🌐 代理：{res['proxyStatus']}，出口IP：{res['proxyIp']}",
                f"🔑 Code：{res['codeMsg']}",
                f"📝 登录：{res['loginMsg']}",
                f"📤 分享：{res['shareMsg']}",
                f"📋 积分：{res['pointsMsg']}",
            ])
        else:
            lines.extend([
                "❌ 状态：执行失败",
                f"🌐 代理：{res['proxyStatus']}，出口IP：{res['proxyIp']}",
                f"🧨 原因：{res['error'] or '未知错误'}",
            ])
        lines.append("------------------------------")

    return "\n".join(lines)


def main() -> None:
    accounts = parse_accounts(NIU_WXID_RAW)
    if NIU_TOKEN and not accounts:
        accounts = ["NIU_TOKEN"]

    log_title(len(accounts))

    if not accounts:
        append_notify_result(empty_result("未配置", "请配置 niu_wxid 或 NIU_WXID；也可配置 NIU_TOKEN"))
        dispatch_notify(SCRIPT_TITLE, build_notify(GLOBAL_NOTIFY_BUFFERS))
        return

    if not NIU_TOKEN and not WX_SERVER_URL:
        for account in accounts:
            append_notify_result(empty_result(account, "请配置 wx_server_url 或 WX_SERVER_URL"))
        dispatch_notify(SCRIPT_TITLE, build_notify(GLOBAL_NOTIFY_BUFFERS))
        return

    for index, account in enumerate(accounts, 1):
        try:
            result = run_account(index, len(accounts), account)
            append_notify_result(result)
        except Exception as exc:
            print(f"❌ [主程序] {mask(account)} 执行异常: {exc}")
            append_notify_result(empty_result(account, traceback.format_exc().strip()))

        if index < len(accounts):
            print("⏳ [间隔] 等待 2s 后处理下一个账号")
            sleep(2)

    success_count = sum(1 for item in GLOBAL_NOTIFY_BUFFERS if item["success"])
    fail_count = len(GLOBAL_NOTIFY_BUFFERS) - success_count

    print()
    print("╔" + "═" * 50 + "╗")
    print("║ 🏁 小牛电动任务执行完成                        ║")
    print(f"║ ✅ 成功: {success_count:<39}║")
    print(f"║ ❌ 失败: {fail_count:<39}║")
    print(f"║ 🕒 结束时间: {now_text():<32}║")
    print("╚" + "═" * 50 + "╝")

    dispatch_notify(SCRIPT_TITLE, build_notify(GLOBAL_NOTIFY_BUFFERS))


if __name__ == "__main__":
    main()
