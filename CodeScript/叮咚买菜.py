#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
叮咚买菜 v1.1.0（mywc网关聚合推送版）

功能：自动执行叮咚买菜小程序登录、每日签到、浏览任务、积分余额和积分流水查询，支持多账号执行，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL                   必填其一，自建授权服务器域名
   - 示例：http://127.0.0.1:8110
   - 脚本会自动拼接 /mywc
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=wx1e113254eda17715
   - 请求头：auth=账号标识

2. 账号变量：
   ddmc_wxid 或 DDMC_WXID                           推荐，叮咚买菜专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：wxid_a&wxid_b 或 wxid_a,wxid_b
   - 兼容旧变量 DINGDONG_WXID / DDMCAI 读取

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
   DDMC_STATION_ID                                  门店 station_id，默认 5500fe01916edfe0738b4e43
   DDMC_CITY_NUMBER                                 城市编码，默认 0101
   DDMC_CITY_NAME                                   城市名称，默认 上海市
   DDMC_APP_CLIENT_ID                               客户端 ID，默认 4
   DDMC_BROWSE_TASKS                                是否执行浏览任务，1 开启 / 0 关闭，默认 1

5. 青龙任务建议：
   名称：叮咚买菜
   命令：python3 叮咚买菜.py
   定时：每天运行 1 次即可，具体时间自行调整

依赖：
  pip install requests
  socks5 代理需：pip install requests[socks]
"""

import json
import os
import random
import re
import string
import time
import traceback
from datetime import datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import quote, unquote

import requests

try:
    from SendNotify import send_push_notification
except Exception:
    send_push_notification = None

SCRIPT_TITLE = "叮咚买菜"
APP_NAME = "叮咚买菜小程序"
APPID = "wx1e113254eda17715"
GLOBAL_NOTIFY_BUFFERS: List[Dict[str, Any]] = []

WX_SERVER_URL = (
    os.getenv("wx_server_url")
    or os.getenv("WX_SERVER_URL")
    or ""
).strip().rstrip("/")
DDMC_WXID_RAW = (
    os.getenv("ddmc_wxid")
    or os.getenv("DDMC_WXID")
    or os.getenv("DINGDONG_WXID")
    or os.getenv("DDMCAI")
    or ""
)

STATION_ID = os.getenv("DDMC_STATION_ID", "5500fe01916edfe0738b4e43")
CITY_NUMBER = os.getenv("DDMC_CITY_NUMBER", "0101")
CITY_NAME = os.getenv("DDMC_CITY_NAME", "上海市")
APP_CLIENT_ID = os.getenv("DDMC_APP_CLIENT_ID", "4")
BROWSE_TASKS_ENABLED = os.getenv("DDMC_BROWSE_TASKS", "1") not in ("0", "false", "False", "no")

PLUSPLUS_TOKEN = os.getenv("PLUSPLUS_TOKEN", "")
PROXY_API = os.getenv("PROXY_API", "")
PROXY_TYPE = os.getenv("PROXY_TYPE", "http").lower()

PROXY_RETRY_TIMES = 3
PROXY_VALIDATE_URL = "http://httpbin.org/ip"
PROXY_FETCH_INTERVAL = 3
ENABLE_DIRECT_FALLBACK = True
REQUEST_TIMEOUT = 30

LOGIN_API_VERSION = "13.9.1"
LOGIN_APP_VERSION = "13.9.1"
ACTIVITY_API_VERSION = "9.7.3"
ACTIVITY_APP_VERSION = "2.104.0"
MISSION_API_VERSION = "11.30.1"
WECHAT_API_VERSION = "13.8.2"

BASE_URL = "https://maicai.api.ddxq.mobi"
SUNQUAN_URL = "https://sunquan.api.ddxq.mobi"
GW_URL = "https://gw.api.ddxq.mobi"

LOGIN_URL = f"{BASE_URL}/user/wxAppletAuth"
SIGN_IN_URL = f"{SUNQUAN_URL}/api/v2/user/signin/"
POINT_HOME_URL = f"{BASE_URL}/point/home"
POINT_FLOW_URL = f"{BASE_URL}/point/flow"
WELFARE_CONSULT_URL = f"{GW_URL}/promocore-service/client/welfare/center/v1/consult"
CREATE_MISSION_URL = f"{GW_URL}/promomission-service/mission/search/new/createUserMission"
MISSION_NOTICE_URL = f"{GW_URL}/promomission-service/mission/notice/v1/notice"
SEARCH_UNCOMPLETE_URL = f"{GW_URL}/promomission-service/mission/search/v1/searchUnCompleteMissionByUserId"

BROWSE_MISSION_WAIT = 11

BROWSE_TASK_TITLES = [
    ("今日疯抢", "flash_sale"),
    ("品质之爱", "cms"),
    ("糟卤", "cms"),
]

FALLBACK_PAGE_IDS = {
    "flash_sale": "PAGE_NEW_FlASHSALE_V3",
    "品质之爱": "d5d71e97cba74087",
    "糟卤": "e9b21acfce074d6b",
}

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36 "
    "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) "
    "UnifiedPCWindowsWechat(0xf2541c37) XWEB/25364 "
    f"miniProgram/{APPID}"
)


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


def to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def safe_data(resp: Dict[str, Any]) -> Dict[str, Any]:
    """Safely extract 'data' from an API response, handling null/missing."""
    return resp.get("data") or {}


def log_title(total: int) -> None:
    print()
    print("╔" + "═" * 50 + "╗")
    print("║ 🥬 叮咚买菜 mywc版                         ║")
    print(f"║ 🕒 启动时间: {now_text():<32}║")
    print(f"║ 🔢 账号数量: {total:<34}║")
    print("╚" + "═" * 50 + "╝")


def log_account_header(index: int, total: int, wxid: str) -> None:
    print()
    print("┌" + "─" * 50 + "┐")
    print(f"│ 🧩 账号 {index} / {total:<37}│")
    print(f"│ 🆔 标识 {mask(wxid):<40}│")
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

        code = str(
            data.get("code")
            or (data.get("data") or {}).get("code")
            or ((data.get("data") or {}).get("result") or {}).get("code")
            or (data.get("result") or {}).get("code")
            or ""
        ).strip()

        if data.get("err") not in (None, 0, "0") or not code:
            print(f"❌ [授权] code 获取失败: {json_preview(data)}")
            return None

        print("✅ [授权] code 获取成功")
        return code
    except Exception as exc:
        print(f"❌ [授权] code 获取异常: {exc}")
        return None


def random_device_id() -> str:
    alphabet = string.ascii_letters + string.digits + "_-"
    return "".join(random.choices(alphabet, k=28))


def common_headers(
    *,
    uid: str = "",
    device_id: str = "",
    api_version: str = ACTIVITY_API_VERSION,
    build_version: str = "",
    channel: str = "",
    token: str = "",
    login: bool = False,
) -> Dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "ddmc-longitude": "0",
        "ddmc-latitude": "0",
        "ddmc-station-id": STATION_ID,
        "ddmc-city-number": CITY_NUMBER,
        "ddmc-api-version": api_version,
        "ddmc-app-client-id": APP_CLIENT_ID,
        "ddmc-device-id": device_id,
        "ddmc-build-version": build_version,
        "ddmc-channel": channel,
        "ddmc-ip": "",
    }

    if login:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        headers["Referer"] = f"https://servicewechat.com/{APPID}/848/page-frame.html"
        headers["ddmc-time"] = str(int(time.time()))
    else:
        headers["Content-Type"] = "application/json"
        headers["Origin"] = "https://activity.m.ddxq.mobi"
        headers["Referer"] = "https://activity.m.ddxq.mobi/"

    if uid:
        headers["ddmc-uid"] = uid

    if token:
        headers["Cookie"] = f"DDXQSESSID={token}"

    return headers


def build_common_params(uid: str, device_id: str, device_token: str = "") -> Dict[str, Any]:
    return {
        "api_version": ACTIVITY_API_VERSION,
        "app_client_id": APP_CLIENT_ID,
        "app_version": ACTIVITY_APP_VERSION,
        "app_client_name": "activity",
        "station_id": STATION_ID,
        "native_version": "",
        "city_name": CITY_NAME,
        "city_number": CITY_NUMBER,
        "uid": uid,
        "latitude": 0,
        "longitude": 0,
        "device_token": device_token,
        "device_id": device_id,
        "os_version": "10",
    }


def login_by_code(
    server: str,
    code: str,
    device_id: str,
    proxies: Dict[str, str] | None,
) -> Tuple[Dict[str, Any] | None, Dict[str, Any] | None]:
    try:
        print("🔐 [登录] 使用 code 换 uid + session_id")
        payload = {
            "uid": "",
            "longitude": 0,
            "latitude": 0,
            "station_id": STATION_ID,
            "city_number": CITY_NUMBER,
            "api_version": LOGIN_API_VERSION,
            "app_version": LOGIN_APP_VERSION,
            "channel": "applet",
            "app_client_id": APP_CLIENT_ID,
            "s_id": "",
            "openid": "",
            "device_id": device_id,
            "h5_source": "",
            "daily_fresh": 1,
            "time": int(time.time()),
            "device_token": "",
            "code": code,
            "model": "microsoft",
            "showData": "true",
            "showMsg": "false",
            "app_client_name": "wechat",
        }

        response = request_with_proxy(
            "POST",
            LOGIN_URL,
            headers=common_headers(
                device_id=device_id,
                api_version=LOGIN_API_VERSION,
                build_version=LOGIN_APP_VERSION,
                channel="applet",
                login=True,
            ),
            data=payload,
            proxies=proxies,
            server=server,
        )

        try:
            data = response.json()
        except Exception:
            data = {"raw": response.text[:800]}

        login_info = safe_data(data)
        uid = login_info.get("uid")
        session_id = login_info.get("session_id")
        openid = login_info.get("openid")

        if uid and session_id:
            info = login_info.get("user_info") or {}
            print(f"✅ [登录] 登录成功: {mask(uid)} / {info.get('name', '') or mask(openid)}")
            return {
                "uid": str(uid),
                "session_id": str(session_id),
                "openid": str(openid or ""),
                "name": str(info.get("name", "")),
                "raw": data,
            }, data

        print(f"❌ [登录] 未识别登录信息: {json_preview(data)}")
        return None, data
    except Exception as exc:
        print(f"❌ [登录] 请求异常: {exc}")
        return None, None


def api_get(
    server: str,
    url: str,
    session_id: str,
    uid: str,
    device_id: str,
    proxies: Dict[str, str] | None,
    params: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    response = request_with_proxy(
        "GET",
        url,
        headers=common_headers(uid=uid, device_id=device_id, token=session_id),
        params=params,
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


def api_post(
    server: str,
    url: str,
    session_id: str,
    uid: str,
    device_id: str,
    proxies: Dict[str, str] | None,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    response = request_with_proxy(
        "POST",
        url,
        headers=common_headers(uid=uid, device_id=device_id, token=session_id),
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


def gw_post(
    server: str,
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    proxies: Dict[str, str] | None,
) -> Dict[str, Any]:
    response = request_with_proxy(
        "POST",
        url,
        headers=headers,
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


def mission_activity_headers(session_id: str, uid: str, device_id: str) -> Dict[str, str]:
    headers = common_headers(uid=uid, device_id=device_id, token=session_id)
    headers["ddmc-os-version"] = "10"
    headers["ddmc-device-token"] = ""
    return headers


def mission_notice_headers(kind: str, session_id: str, uid: str, device_id: str) -> Dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Cookie": f"DDXQSESSID={session_id}",
        "ddmc-station-id": STATION_ID,
        "ddmc-city-number": CITY_NUMBER,
        "ddmc-device-id": device_id,
    }
    if kind == "flash_sale":
        headers.update({
            "Origin": "https://wx.m.ddxq.mobi",
            "Referer": "https://wx.m.ddxq.mobi/",
            "ddmc-longitude": "0",
            "ddmc-latitude": "0",
            "ddmc-build-version": WECHAT_API_VERSION,
            "ddmc-channel": "undefined",
            "ddmc-os-version": "0",
            "ddmc-app-client-id": "3",
            "ddmc-api-version": WECHAT_API_VERSION,
            "ddmc-uid": uid,
            "ddmc-ip": "",
        })
    else:
        headers.update({
            "Origin": "https://cms.api.ddxq.mobi",
            "Referer": "https://cms.api.ddxq.mobi/",
        })
    return headers


def normalize_mission_list(data: Any) -> List[Dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]

    if not isinstance(data, dict):
        return []

    for key in ("pointMissionModule", "missionList", "list", "rows", "records"):
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]

    return []


def extract_uuid_from_link(link: Any) -> str:
    text = unquote(str(link or ""))
    match = re.search(r"uuid=([0-9a-fA-F]+)", text)
    if match:
        return match.group(1)
    return ""


def fallback_page_id(title: str) -> str:
    for keyword, page_id in FALLBACK_PAGE_IDS.items():
        if keyword != "flash_sale" and keyword in title:
            return page_id
    return ""


def classify_mission(mission: Dict[str, Any]) -> Tuple[str, str] | None:
    title = str(mission.get("missionTitle") or mission.get("title") or "")
    for keyword, kind in BROWSE_TASK_TITLES:
        if keyword in title:
            return title, kind
    return None


def fetch_welfare_missions(
    server: str,
    session_id: str,
    uid: str,
    device_id: str,
    proxies: Dict[str, str] | None,
) -> List[Dict[str, Any]]:
    payload = build_common_params(uid, device_id)
    payload["app_platform_id"] = APP_CLIENT_ID

    resp = gw_post(
        server,
        WELFARE_CONSULT_URL,
        mission_activity_headers(session_id, uid, device_id),
        payload,
        proxies,
    )
    if resp.get("code") != 0:
        print(f"⚠️ [任务] 获取福利中心任务列表失败: {resp.get('msg') or resp.get('message') or json_preview(resp, 300)}")
        return []

    return normalize_mission_list(safe_data(resp))


def search_uncomplete_missions(
    server: str,
    session_id: str,
    uid: str,
    device_id: str,
    proxies: Dict[str, str] | None,
    page_id: str,
) -> Dict[str, Any]:
    payload = {
        "latitude": 0,
        "longitude": 0,
        "env": "PE",
        "station_id": STATION_ID,
        "city_number": CITY_NUMBER,
        "api_version": MISSION_API_VERSION,
        "app_client_id": 4,
        "native_version": "0",
        "h5_source": "",
        "page_type": "",
        "pageUuid": page_id,
    }
    return gw_post(
        server,
        SEARCH_UNCOMPLETE_URL,
        mission_notice_headers("cms", session_id, uid, device_id),
        payload,
        proxies,
    )


def run_browse_mission(
    server: str,
    session_id: str,
    uid: str,
    device_id: str,
    proxies: Dict[str, str] | None,
    mission: Dict[str, Any],
) -> Tuple[bool, str]:
    title, kind = classify_mission(mission) or ("未知任务", "cms")
    mission_id = mission.get("missionId")
    if mission_id is None:
        return False, f"{title}: 未识别 missionId"

    print(f"📋 [任务] 开始执行: {title}（missionId={mission_id}）")

    create_payload = build_common_params(uid, device_id)
    create_payload["missionId"] = mission_id

    create_resp = gw_post(
        server,
        CREATE_MISSION_URL,
        mission_activity_headers(session_id, uid, device_id),
        create_payload,
        proxies,
    )
    create_msg = str(create_resp.get("data") or create_resp.get("msg") or create_resp.get("message") or "")
    if create_resp.get("code") == 0:
        print(f"✅ [任务] {title} 领取成功: {create_msg or '任务领取成功'}")
    else:
        print(f"⚠️ [任务] {title} 领取返回: code={create_resp.get('code')} {create_msg or json_preview(create_resp, 200)}")

    wait_time = BROWSE_MISSION_WAIT + random.randint(0, 2)
    print(f"⏳ [任务] {title} 浏览计时 {wait_time}s")
    sleep(wait_time)

    serial_no = f"{int(time.time() * 1000)}.{random.randint(1000, 9999)}"

    if kind == "flash_sale":
        page_id = "PAGE_NEW_FlASHSALE_V3"
        notice_payload = {
            "uid": uid,
            "longitude": 0,
            "latitude": 0,
            "station_id": STATION_ID,
            "city_number": CITY_NUMBER,
            "api_version": WECHAT_API_VERSION,
            "app_version": WECHAT_API_VERSION,
            "app_client_id": 3,
            "h5_source": "",
            "s_id": session_id,
            "openid": "",
            "device_id": "",
            "daily_fresh": 1,
            "time": int(time.time()),
            "native_version": "",
            "device_token": "",
            "pageId": page_id,
            "seconds": 10,
            "missionType": "promo_scan",
            "cityCode": CITY_NUMBER,
            "serialNo": serial_no,
            "missionId": str(mission_id),
            "app_client_name": "wechat",
        }
    else:
        page_id = extract_uuid_from_link(mission.get("link")) or fallback_page_id(title)
        if not page_id:
            print(f"⚠️ [任务] {title}: 链接中未找到 uuid")
            return False, f"{title}: 链接中未找到 uuid"

        search_resp = search_uncomplete_missions(server, session_id, uid, device_id, proxies, page_id)
        if search_resp.get("code") != 0:
            print(f"⚠️ [任务] {title} 查询未完成列表失败: {search_resp.get('msg') or search_resp.get('message') or json_preview(search_resp, 200)}")
        notice_payload = {
            "latitude": 0,
            "longitude": 0,
            "env": "PE",
            "station_id": STATION_ID,
            "city_number": CITY_NUMBER,
            "api_version": MISSION_API_VERSION,
            "app_client_id": 4,
            "native_version": "0",
            "h5_source": "",
            "page_type": "",
            "pageUuid": page_id,
            "pageId": page_id,
            "seconds": 10,
            "missionType": "scan",
            "missionId": mission_id,
            "cityCode": CITY_NUMBER,
            "serialNo": serial_no,
        }

    notice_resp = gw_post(
        server,
        MISSION_NOTICE_URL,
        mission_notice_headers(kind, session_id, uid, device_id),
        notice_payload,
        proxies,
    )
    if notice_resp.get("code") == 0:
        print(f"✅ [任务] {title} 浏览上报成功")
        return True, f"{title} 完成"
    else:
        msg = notice_resp.get("msg") or notice_resp.get("message") or json_preview(notice_resp, 200)
        print(f"❌ [任务] {title} 浏览上报失败: {msg}")
        return False, f"{title} 失败: {msg}"


def run_account(index: int, total: int, wxid: str) -> Dict[str, Any]:
    result = {
        "wxid": wxid,
        "server": mask(wxid),
        "success": False,
        "proxyStatus": "未使用代理",
        "proxyIp": "-",
        "uid": "-",
        "name": "-",
        "signMsg": "-",
        "seriesMsg": "-",
        "browseMsg": "-",
        "pointMsg": "-",
        "flowMsg": "-",
        "error": "",
    }

    log_account_header(index, total, wxid)

    proxies, proxy_ip = get_valid_proxy(mask(wxid))
    result["proxyStatus"] = "使用专属代理" if proxies else "使用直连"
    result["proxyIp"] = proxy_ip or "-"

    sleep(PROXY_FETCH_INTERVAL)

    delay = random.randint(2, 6)
    print(f"⏳ [延迟] 启动延迟 {delay}s")
    sleep(delay)

    code = get_code(wxid)
    if not code:
        result["error"] = "获取 code 失败"
        return result

    device_id = random_device_id()

    login_info, raw_login = login_by_code(wxid, code, device_id, proxies)
    if not login_info:
        result["error"] = f"登录失败: {json_preview(raw_login)}"
        return result

    uid = login_info["uid"]
    session_id = login_info["session_id"]
    result["uid"] = mask(uid)
    result["name"] = login_info.get("name") or mask(login_info.get("openid"))

    try:
        common_params = build_common_params(uid, device_id)

        sign_resp = api_post(wxid, SIGN_IN_URL, session_id, uid, device_id, proxies, common_params)
        if sign_resp.get("code") == 0:
            sign_data = safe_data(sign_resp)
            sign_point = sign_data.get("point", 0)
            if sign_point is None:
                sign_point = 0
            series = sign_data.get("new_sign_series") or sign_data.get("sign_series") or 0
            sign_role = sign_data.get("sign_role", "")
            if sign_data.get("is_today_sign") is True:
                result["signMsg"] = "今日已签到"
            else:
                result["signMsg"] = f"签到成功，+{sign_point} 积分"
            result["seriesMsg"] = f"连签 {series} 天" + (f"（{sign_role}）" if sign_role else "")
            print(f"✅ [签到] {result['signMsg']}")
            print(f"🎰 [连签] {result['seriesMsg']}")
        else:
            result["signMsg"] = sign_resp.get("msg") or sign_resp.get("message") or "签到失败"
            print(f"⚠️ [签到] {result['signMsg']}")

        browse_parts: List[str] = []
        if BROWSE_TASKS_ENABLED:
            missions = fetch_welfare_missions(wxid, session_id, uid, device_id, proxies)
            target_missions: List[Dict[str, Any]] = []
            for mission in missions:
                if classify_mission(mission):
                    target_missions.append(mission)

            if not target_missions:
                browse_parts.append("未找到浏览任务")
                print(f"⚠️ [任务] {browse_parts[-1]}")
            else:
                for mission in target_missions:
                    title, _ = classify_mission(mission) or ("未知任务", "cms")
                    if str(mission.get("status")) == "1":
                        msg = f"{title}: 今日已完成"
                        print(f"✅ [任务] {msg}")
                        browse_parts.append(msg)
                        continue
                    ok, msg = run_browse_mission(wxid, session_id, uid, device_id, proxies, mission)
                    browse_parts.append(msg)
                    if len(target_missions) > 1:
                        sleep(random.randint(1, 3))

            result["browseMsg"] = "；".join(browse_parts) if browse_parts else "-"

            verify_missions = fetch_welfare_missions(wxid, session_id, uid, device_id, proxies)
            verify_map = {item.get("missionId"): item for item in verify_missions if isinstance(item, dict)}
            verify_parts = []
            for mission in target_missions:
                title, _ = classify_mission(mission) or ("未知任务", "cms")
                current = verify_map.get(mission.get("missionId")) or {}
                status = str(current.get("status"))
                verify_parts.append(f"{title}: {'已完成' if status == '1' else '未完成'}")
            result["browseMsg"] = "；".join(verify_parts)
            print(f"📋 [任务] 复核结果: {result['browseMsg']}")
        else:
            print("⚠️ [任务] DDMC_BROWSE_TASKS 已关闭，跳过浏览任务")

        home_resp = api_get(
            wxid,
            POINT_HOME_URL,
            session_id,
            uid,
            device_id,
            proxies,
            params=common_params,
        )
        if home_resp.get("code") != 0:
            print(f"⚠️ [积分] 获取积分余额失败: {home_resp.get('msg') or home_resp.get('message') or json_preview(home_resp, 300)}")
        else:
            home_data = safe_data(home_resp)
            point_num = home_data.get("point_num", 0)
            point_money = home_data.get("point_money", "0")
            expire_display = home_data.get("expire_point_display", "")
            user_sign = home_data.get("user_sign") or {}
            result["pointMsg"] = f"{point_num} 积分（{point_money} 元）"
            if not result["seriesMsg"] or result["seriesMsg"] == "-":
                result["seriesMsg"] = f"连签 {user_sign.get('new_sign_series', 0)} 天"
            print(f"💰 [积分] {result['pointMsg']}")
            if expire_display:
                print(f"⏳ [积分] {expire_display}")

        flow_params = dict(common_params)
        flow_params.update({"type": 0, "count": 50, "page": 1})
        flow_resp = api_get(
            wxid,
            POINT_FLOW_URL,
            session_id,
            uid,
            device_id,
            proxies,
            params=flow_params,
        )
        if flow_resp.get("code") != 0:
            print(f"⚠️ [明细] 获取积分流水失败: {flow_resp.get('msg') or flow_resp.get('message') or json_preview(flow_resp, 300)}")
        else:
            flow_data = safe_data(flow_resp)
            point_list = flow_data.get("point_list") or []
            today = datetime.now().strftime("%Y-%m-%d")
            today_items = [
                item
                for item in point_list
                if isinstance(item, dict) and str(item.get("create_time", "")).startswith(today)
            ]
            sample = today_items[:3] if today_items else point_list[:3]
            if sample:
                parts = [
                    f"{item.get('description', '')} {to_float(item.get('point')):+.0f}"
                    for item in sample
                ]
                result["flowMsg"] = "；".join(parts)
                print(f"📈 [明细] {result['flowMsg']}")
            else:
                result["flowMsg"] = "暂无积分流水"
                print(f"📈 [明细] {result['flowMsg']}")

        result["success"] = True
        return result

    except Exception as exc:
        result["error"] = traceback.format_exc().strip()
        print(f"❌ [账号] 执行失败: {exc}")
        return result


def append_notify_result(result: Dict[str, Any]) -> None:
    GLOBAL_NOTIFY_BUFFERS.append(result)


def empty_result(wxid: str, error: str) -> Dict[str, Any]:
    return {
        "wxid": wxid,
        "server": mask(wxid),
        "success": False,
        "proxyStatus": "-",
        "proxyIp": "-",
        "uid": "-",
        "name": "-",
        "signMsg": "-",
        "seriesMsg": "-",
        "browseMsg": "-",
        "pointMsg": "-",
        "flowMsg": "-",
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
        "💰 积分余额：详见账号明细",
        "==============================",
    ]

    for idx, res in enumerate(results, 1):
        lines.append(f"🧑‍💻 【账号{idx}】{mask(res.get('wxid') or res.get('server') or '')}")
        if res["success"]:
            lines.extend([
                "✅ 状态：执行成功",
                f"👤 昵称：{res['name']}",
                f"🌐 代理：{res['proxyStatus']}，出口IP：{res['proxyIp']}",
                f"🔐 UID：{res['uid']}",
                f"📝 签到：{res['signMsg']}",
                f"🎰 连签：{res['seriesMsg']}",
                f"📋 任务：{res['browseMsg']}",
                f"💰 积分：{res['pointMsg']}",
                f"📈 明细：{res['flowMsg']}",
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
    wxids = parse_accounts(DDMC_WXID_RAW)
    log_title(len(wxids))

    if not wxids:
        append_notify_result(empty_result("未配置", "请配置 ddmc_wxid 或 DDMC_WXID"))
        dispatch_notify(SCRIPT_TITLE, build_notify(GLOBAL_NOTIFY_BUFFERS))
        return

    if not WX_SERVER_URL:
        for wxid in wxids:
            append_notify_result(empty_result(wxid, "请配置 wx_server_url 或 WX_SERVER_URL"))
        dispatch_notify(SCRIPT_TITLE, build_notify(GLOBAL_NOTIFY_BUFFERS))
        return

    for index, wxid in enumerate(wxids, 1):
        try:
            result = run_account(index, len(wxids), wxid)
            append_notify_result(result)
        except Exception as exc:
            print(f"❌ [主程序] {mask(wxid)} 执行异常: {exc}")
            append_notify_result(empty_result(wxid, traceback.format_exc().strip()))

        if index < len(wxids):
            print("⏳ [间隔] 等待 2s 后处理下一个账号")
            sleep(2)

    success_count = sum(1 for item in GLOBAL_NOTIFY_BUFFERS if item["success"])
    fail_count = len(GLOBAL_NOTIFY_BUFFERS) - success_count

    print()
    print("╔" + "═" * 50 + "╗")
    print("║ 🏁 叮咚买菜任务执行完成                    ║")
    print(f"║ ✅ 成功: {success_count:<39}║")
    print(f"║ ❌ 失败: {fail_count:<39}║")
    print(f"║ 🕒 结束时间: {now_text():<32}║")
    print("╚" + "═" * 50 + "╝")

    dispatch_notify(SCRIPT_TITLE, build_notify(GLOBAL_NOTIFY_BUFFERS))


if __name__ == "__main__":
    main()
