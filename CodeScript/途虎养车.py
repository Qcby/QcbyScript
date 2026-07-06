#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
途虎养车 v1.1.0（mywc网关聚合推送版）

功能：自动执行途虎养车小程序签到和积分查询，支持多账号执行，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL   必填其一，自建授权服务器域名
   - 示例：http://127.0.0.1:8110
   - 脚本会自动拼接 /mywc
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=wx27d20205249c56a3
   - 请求头：auth=账号标识

2. 账号变量：
   tuhu_wxid 或 TUHU_WXID                         推荐，途虎养车专属账号变量
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
   名称：途虎养车签到
   命令：python3 途虎养车.py
   定时：每天运行 1 次即可，具体时间自行调整
"""

import json
import os
import random
import re
import time
import traceback
from datetime import datetime
from typing import Any, Dict, List, Tuple
from urllib.parse import quote, urlparse

import requests
import urllib3
urllib3.disable_warnings()

APP_NAME = "途虎养车小程序"
APPID = "wx27d20205249c56a3"

# ── 痛点修复：导入并对接同目录下的 SendNotify.py 核心模块 ──
try:
    from SendNotify import send_push_notification
except Exception as exc:
    print(f"[警告] 导入 SendNotify.py 失败：{exc}，将退化为无通知模式")
    def send_push_notification(text, desp):
        pass

# ── 基础变量读取 ──
TUHU_WXID_RAW = os.environ.get("tuhu_wxid") or os.environ.get("TUHU_WXID") or ""
WX_SERVER_URL = os.environ.get("wx_server_url") or os.environ.get("WX_SERVER_URL") or ""
PROXY_API = os.getenv("PROXY_API", "")
PROXY_TYPE = os.getenv("PROXY_TYPE", "http").lower()

PROXY_RETRY_TIMES = 3
PROXY_VALIDATE_URL = "http://httpbin.org/ip"
PROXY_FETCH_INTERVAL = 3
ENABLE_DIRECT_FALLBACK = True
REQUEST_TIMEOUT = 30

BASE_URL = "https://cl-gateway.tuhu.cn"
LOGIN_URL = f"{BASE_URL}/cl-user-auth-login/login/authSilentSign"
SIGN_INFO_URL = f"{BASE_URL}/cl-common-api/api/member/getSignInInfo"
SIGN_SUBMIT_URL = f"{BASE_URL}/cl-common-api/api/dailyCheckIn/userCheckIn"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 "
    "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) "
    "UnifiedPCWindowsWechat(0xf2541938) XWEB/19823"
)

REFERER = f"https://servicewechat.com/{APPID}/1319/page-frame.html"

# 全局通知精简缓存
GLOBAL_NOTIFY_BUFFERS = []

def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def sleep(seconds: float) -> None:
    time.sleep(seconds)

def mask(value: Any) -> str:
    value = str(value or "")
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-6:]}"

def to_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0

def split_config_list(value: str) -> List[str]:
    if not value:
        return []
    normalized = value.replace("，", ",").replace(",", "&").replace("\n", "&")
    return [item.strip() for item in normalized.split("&") if item.strip()]

def direct_session() -> requests.Session:
    session = requests.Session()
    session.trust_env = False
    return session

def parse_proxy_response(text: Any) -> Dict[str, Any] | None:
    if not isinstance(text, str):
        text = json.dumps(text, ensure_ascii=False)
    text = text.strip()
    if not text: return None
    try:
        data = json.loads(text)
        proxy_obj = None
        if isinstance(data.get("data"), list) and data["data"]: proxy_obj = data["data"][0]
        elif isinstance(data.get("data"), dict): proxy_obj = data["data"]
        elif data.get("ip") and data.get("port"): proxy_obj = data
        elif isinstance(data.get("result"), dict): proxy_obj = data["result"]

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
    except Exception: pass
    if ":" in text:
        parts = text.split(":")
        if len(parts) >= 2:
            return {
                "host": parts[0], "port": int(parts[1]),
                "username": parts[2] if len(parts) > 2 else "",
                "password": parts[3] if len(parts) > 3 else "",
            }
    return None

def build_proxy_dict(proxy_info: Dict[str, Any] | None) -> Dict[str, str] | None:
    if not proxy_info: return None
    host, port = proxy_info["host"], proxy_info["port"]
    username, password = proxy_info.get("username", ""), proxy_info.get("password", "")
    auth = f"{quote(username)}:{quote(password)}@" if username and password else ""
    scheme = "socks5" if PROXY_TYPE == "socks5" else "http"
    proxy_url = f"{scheme}://{auth}{host}:{port}"
    return {"http": proxy_url, "https": proxy_url}

def validate_proxy(proxies: Dict[str, str] | None) -> Tuple[bool, str]:
    if not proxies: return False, ""
    try:
        response = requests.get(PROXY_VALIDATE_URL, proxies=proxies, timeout=15)
        if response.status_code == 200:
            ip = response.json().get("origin", "未知")
            return True, ip
    except Exception: pass
    return False, ""

def get_valid_proxy(wxid: str) -> Tuple[Dict[str, str] | None, str]:
    if not PROXY_API: return None, ""
    for index in range(1, PROXY_RETRY_TIMES + 1):
        try:
            response = direct_session().get(PROXY_API, timeout=15)
            proxy_info = parse_proxy_response(response.text)
            if not proxy_info: continue
            proxies = build_proxy_dict(proxy_info)
            ok, ip = validate_proxy(proxies)
            if ok: return proxies, ip
        except Exception: pass
        if index < PROXY_RETRY_TIMES: sleep(2)
    return None, ""

def request_with_proxy(method: str, url: str, *, proxies: Dict[str, str] | None = None, **kwargs) -> requests.Response:
    kwargs.setdefault("timeout", REQUEST_TIMEOUT)
    if proxies:
        try: return requests.request(method, url, proxies=proxies, **kwargs)
        except Exception:
            if not ENABLE_DIRECT_FALLBACK: raise
    return direct_session().request(method, url, **kwargs)

def get_code(wxid: str) -> str | None:
    if not WX_SERVER_URL:
        print("❌ [授权] 未配置环境变量 wx_server_url")
        return None
    base_url = WX_SERVER_URL.strip().rstrip("/")
    url = f"{base_url}/mywc"
    try:
        response = direct_session().get(url, params={"wxid": wxid, "appId": APPID}, headers={"auth": wxid}, timeout=25)
        data = response.json()
        if data.get("status") == "ok" and data.get("code"):
            return data["code"]
        print(f"❌ [授权] code 获取失败: {data}")
        return None
    except Exception as exc:
        print(f"❌ [授权] code 获取异常: {exc}")
        return None

def common_headers(user_session: str | None = None) -> Dict[str, str]:
    headers = {
        "Host": "cl-gateway.tuhu.cn", "Connection": "keep-alive",
        "orion_biz_gps_latitude": "22.787150540279182",
        "orion_biz_gps_province": "%E5%B9%BF%E8%A5%BF%E5%A3%AE%E6%97%8F%E8%87%AA%E6%B2%BB%E5%8C%BA",
        "xweb_xhr": "1", "distinct_id": "6a68cbca-ce9a-4b0e-8092-cc5a85cf9a85",
        "currentPage": "memberMallPackage/pages/pointCenter/pointCenter",
        "orion_biz_gps_city": "%E5%8D%97%E5%AE%81%E5%B8%82",
        "deviceId": f"{int(time.time() * 1000)}-{random.randint(1000000, 9999999)}-0f6cb850fc64da-24853921",
        "authType": "oauth", "api_level": "2", "vehicleClass": "CAR",
        "channel": "wechat-miniprogram", "Content-Type": "application/json",
        "fingerprint": f"sMPVY{int(time.time())}QPV2wLVhl8f",
        "orion_biz_gps_longitude": "108.27980328217664", "User-Agent": USER_AGENT,
        "version": "7.62.8", "Accept": "*/*", "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty", "Referer": REFERER,
    }
    if user_session: headers["Authorization"] = f"Bearer {user_session}"
    return headers

def login_by_code(code: str, proxies: Dict[str, str] | None) -> Tuple[str | None, str]:
    try:
        response = request_with_proxy("POST", LOGIN_URL, headers=common_headers(), json={"channel": "WXAPP", "code": code}, proxies=proxies)
        data = response.json()
        if data.get("code") == 10000:
            user_data = data.get("data") or {}
            return user_data.get("userSession"), user_data.get("nickName") or "微信用户"
        return None, "-"
    except Exception:
        return None, "-"

def api_post(url: str, user_session: str, proxies: Dict[str, str] | None) -> Dict[str, Any]:
    try:
        response = request_with_proxy("POST", url, headers=common_headers(user_session), json={"channel": "WXAPP"}, proxies=proxies)
        return response.json()
    except Exception as e:
        return {"code": -1, "message": str(e)}

def get_sign_info(user_session: str, proxies: Dict[str, str] | None) -> Tuple[bool | None, int]:
    data = api_post(SIGN_INFO_URL, user_session, proxies)
    if data.get("code") != 10000: return None, 0
    info = data.get("data") or {}
    return bool(info.get("signInStatus", False)), to_int(info.get("userIntegral", 0))

def submit_signin(user_session: str, proxies: Dict[str, str] | None) -> Tuple[bool, str, int]:
    data = api_post(SIGN_SUBMIT_URL, user_session, proxies)
    if data.get("code") == 10000:
        result = data.get("data") or {}
        reward = to_int(result.get("rewardIntegral", 0))
        days = to_int(result.get("continuousDays", 0))
        return True, f"✅ 成功 +{reward}积分 (连签{days}天)", reward
    message = data.get("message") or data.get("msg") or "签到失败"
    if "已签到" in message or "重复" in message: return True, "⚠️ 今日已签到过", 0
    return False, f"❌ 失败 ({message})", 0

def run_account(wxid: str) -> None:
    print(f"\n🔄 正在处理微信号: {wxid}")
    summary = {
        "wxid": wxid, "nickname": "-", "before": "0", "after": "0", "earned": "0", "msg": "未执行"
    }
    
    proxies, _ = get_valid_proxy(wxid)
    sleep(PROXY_FETCH_INTERVAL)

    code = get_code(wxid)
    if not code:
        summary["msg"] = "❌ 获取 code 失败"
        GLOBAL_NOTIFY_BUFFERS.append(summary)
        return

    user_session, nick_name = login_by_code(code, proxies)
    if not user_session:
        summary["msg"] = "❌ 登录换绑失败"
        GLOBAL_NOTIFY_BUFFERS.append(summary)
        return

    summary["nickname"] = nick_name
    try:
        status, before_integral = get_sign_info(user_session, proxies)
        summary["before"] = str(before_integral)
        summary["after"] = str(before_integral)

        if status:
            summary["msg"] = "⚠️ 今日已签到过"
        else:
            ok, sign_msg, reward = submit_signin(user_session, proxies)
            summary["msg"] = sign_msg
            summary["earned"] = str(reward)
            sleep(1)
            _, after_integral = get_sign_info(user_session, proxies)
            summary["after"] = str(after_integral)

    except Exception as exc:
        summary["msg"] = f"❌ 运行异常: {str(exc)}"
    finally:
        GLOBAL_NOTIFY_BUFFERS.append(summary)

def main() -> None:
    print("==================================================")
    print("🚗 途虎养车纯 WXID 聚合精简推送版启动...")
    print("==================================================")
    
    wxids = split_config_list(TUHU_WXID_RAW)
    if not wxids:
        print("❌ 未找到有效 tuhu_wxid 账户配置！")
        return
        
    print(f"📱 共加载 {len(wxids)} 个途虎账户")
    
    for wxid in wxids:
        try:
            run_account(wxid)
            sleep(random.randint(3, 6))
        except Exception as e:
            print(f"账户 {wxid} 发生未知错误: {e}")

    # ── 核心痛点修复：改用统一通知函数，触发多渠道分发 ──
    if GLOBAL_NOTIFY_BUFFERS:
        title = "🔔 途虎养车任务执行总结"
        desp_lines = [
            "==============================",
            f"📊 统计数据：成功 {len(GLOBAL_NOTIFY_BUFFERS)} / 总计 {len(wxids)}",
            "==============================\n"
        ]
        
        for item in GLOBAL_NOTIFY_BUFFERS:
            desp_lines.append(f"👤 【{item['wxid']}】( {item['nickname']} )")
            desp_lines.append(f"   📝 签到状态: {item['msg']}")
            desp_lines.append(f"   💰 积分变动: 始 {item['before']} ➔ 终 {item['after']} (获得 +{item['earned']})")
            desp_lines.append("-" * 30)

        final_desp = "\n".join(desp_lines)
        print("\n[精简推送报表阅览]\n" + final_desp)
        
        # 精准击中痛点：全量分发，不仅能推Pushplus，也能让微信企业机器人收到
        send_push_notification(title, final_desp)

if __name__ == "__main__":
    main()