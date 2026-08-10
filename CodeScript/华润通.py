"""
华润通签到 v1.1.0（mywc网关聚合推送版）

功能：自动执行华润通签到和积分任务，支持多账号执行，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL                 必填其一，自建授权服务器域名
   - 示例：http://127.0.0.1:8110
   - 脚本会自动拼接 /mywc
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=wx66c62601b987e69d
   - 请求头：auth=账号标识

2. 账号变量：
   huaruntong_wxid 或 HUA_RUNTONG_WXID           推荐，华润通专属账号变量
   - 兼容旧变量：txpopenid
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：wxid_a&wxid_b 或 wxid_a,wxid_b

3. 推送变量：
   需要同目录存在 SendNotify.py，脚本结束后会统一调用 send_push_notification。
   常用推送变量如下，配置任意一种即可：
   QYWX_KEY                                       企业微信机器人 key
   PUSH_PLUS_TOKEN                                PushPlus token
   PUSH_KEY                                       Server 酱 key
   DD_BOT_TOKEN 或 DD_BOT_SECRET                  钉钉机器人 token/secret
   FSKEY                                          飞书机器人 key

4. 青龙任务建议：
   名称：华润通签到
   命令：python3 华润通.py
   定时：每天运行 1 次即可，具体时间自行调整
"""

import hashlib
import hmac as _hmac
import json
import os
import re
import time
import uuid
import random
import string
import base64
from io import BytesIO
from collections import OrderedDict

import requests
from Crypto.Cipher import AES
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_OAEP
from Crypto.Util.Padding import pad
from datetime import datetime
from typing import Any, Dict, List

try:
    from SendNotify import send_push_notification
except Exception:
    send_push_notification = None

try:
    from PIL import Image as _Img
    PILLOW_OK = True
except ImportError:
    PILLOW_OK = False

# ================== 配置 ==================
SCRIPT_TITLE = "华润通签到"
GLOBAL_NOTIFY_BUFFERS = []
WXAPP_ID = "wx66c62601b987e69d"
WX_SERVER_URL = (os.environ.get("wx_server_url") or os.environ.get("WX_SERVER_URL") or "").strip().rstrip("/")
WX_OPENIDS = (
    os.environ.get("huaruntong_wxid")
    or os.environ.get("HUA_RUNTONG_WXID")
    or os.environ.get("txpopenid")
    or ""
)
HUA_RUNTONG_APPID = (os.environ.get("HUA_RUNTONG_APPID") or WXAPP_ID).strip()
HUA_RUNTONG_MYYHS_DATA = os.environ.get("HUA_RUNTONG_MYYHS_DATA") or ""
TOKEN_FILE = "hrtcookie.json"
HOST = "https://mid.huaruntong.cn"

# 小程序加密密钥
MINI_SECRET = "addebd90-15e9-4817-a531-1fdd0c7d5230"
MINI_PUBKEY = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCKAJ+rCtCbtr4KmkSVnDZq6c38
0R1TFO9KPJiFtC/DvG3ZVi5aeaRb6XcJCeQbmKA4LA4u8ZFNn5xzCu0/tsSwsKFu
/rM/DHtrD3GGaaq3gV27g620dnEiSZrTZ6QV+OOWIELYekl13O/GF7swqrnC2Xak
d3kfPKITQEpRsjCsKwIDAQAB
-----END PUBLIC KEY-----"""

# Web 端加密密钥（签到沿用）
AUTH_APPID  = "API_AUTH_H5"
AUTH_SECRET = "1c6120fd-5ad3-4c2d-8cb7-b87a707f416d"
CRY_SECRET  = "c274fc67-19f9-47ba-bb84-585a2e3a1f6a"
CRY_PUBKEY  = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDuAiqDmvn9Rf15o21qkDxN0rUf
ZsX6rVBrtfgY6tamN2Yn+1D3eHZJuKNlucyqeBr6nmfN2srYAX+oyCXr5vWwFclj
PuWh8aSASqyk7MfbAv5Q4VqYS7lsYUQRdw4plZG0NASDeBvHWi3lsHjGfNb7iUvg
rk312EDfBHtRgDvB0QIDAQAB
-----END PUBLIC KEY-----"""

DDDDOCR_URL = "http://1.92.97.94:7777/"

HEADERS_AUTH = {
    "Content-Type": "application/json;charset=utf-8",
    "Origin": "https://cloud.huaruntong.cn",
    "Referer": "https://cloud.huaruntong.cn/",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh-Hans;q=0.9",
    "Connection": "keep-alive",
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 15_4_1 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
        "MicroMessenger/8.0.63(0x18003f2f) NetType/WIFI Language/zh_CN "
        "miniProgram/wx66c62601b987e69d"
    ),
}
HEADERS_CRY = {**HEADERS_AUTH, "X-Hrt-Mid-Appid": "API_AUTH_WEB", "X-HRT-MID-NEWRISK": "newRisk"}

# ================== 工具函数 ==================
def _rand_str(n=16):
    return "".join(random.choice(string.digits + string.ascii_letters) for _ in range(n))

def crypto4mid(data: dict, secret: str, pubkey: str, debug: bool = False) -> dict:
    """参数排序后签名，AES + RSA 加密"""
    parts = []
    for k, v in sorted(data.items()):
        if isinstance(v, (dict, list)):
            parts.append(f"{k}={json.dumps(v, separators=(',',':'), ensure_ascii=False)}")
        else:
            parts.append(f"{k}={v}")
    sorted_str = "&".join(parts)
    sig = _hmac.new(secret.encode(), sorted_str.encode(), hashlib.md5).hexdigest()
    if debug:
        print(f"  [DEBUG] sign_str: {sorted_str}")
        print(f"  [DEBUG] signature: {sig}")
    data["signature"] = sig

    aes_key = _rand_str(16).encode()
    cipher_aes = AES.new(aes_key, AES.MODE_CBC, b"\x00" * 16)
    enc_data = base64.b64encode(
        cipher_aes.encrypt(pad(json.dumps(data, separators=(",",":"), ensure_ascii=False).encode(), 16))
    ).decode()

    rsa_key = RSA.import_key(pubkey)
    enc_key = base64.b64encode(PKCS1_OAEP.new(rsa_key).encrypt(aes_key)).decode()
    return {"key": enc_key, "data": enc_data}

# ================== 账号与 code ==================
def append_notify_result(result: Dict[str, Any]) -> None:
    GLOBAL_NOTIFY_BUFFERS.append(result)


def parse_accounts() -> List[str]:
    return [
        item.strip()
        for item in re.split(r"[&,\n，]+", str(WX_OPENIDS or ""))
        if item.strip()
    ]


def mask_account(account: str) -> str:
    account = str(account or "").strip()
    if len(account) <= 8:
        return account or "-"
    return f"{account[:4]}****{account[-4:]}"


def build_notify_report() -> str:
    total = len(GLOBAL_NOTIFY_BUFFERS)
    success_items = [item for item in GLOBAL_NOTIFY_BUFFERS if item.get("ok")]
    failed_items = [item for item in GLOBAL_NOTIFY_BUFFERS if not item.get("ok")]
    total_points = sum(int(item.get("delta_points") or 0) for item in success_items)
    success_accounts = "、".join(item.get("account") for item in success_items) or "-"
    failed_accounts = "、".join(item.get("account") for item in failed_items) or "-"

    lines = [
        "==============================",
        f"🕒 执行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"📊 统计数据：成功 {len(success_items)} / 总计 {total}",
        f"✅ 成功账号：{len(success_items)} 个",
        f"❌ 失败账号：{len(failed_items)} 个",
        f"💰 累计积分：+{total_points}",
        f"🙋 成功列表：{success_accounts}",
        f"💥 失败列表：{failed_accounts}",
        "==============================",
    ]

    for item in GLOBAL_NOTIFY_BUFFERS:
        ok = bool(item.get("ok"))
        lines.extend([
            f"{'🧑‍💻' if ok else '🧟'} 【账号{item.get('index')}】{item.get('account')}",
            f"{'✅' if ok else '❌'} 状态：{item.get('status_text')}",
        ])
        if ok:
            lines.extend([
                f"👤 用户：{item.get('username') or '-'}",
                f"💰 积分：始 {item.get('before_points', '-')} ➔ 终 {item.get('after_points', '-')}，获得 +{item.get('delta_points', 0)}",
                f"🗓 签到：{item.get('sign_text') or '-'}",
            ])
        else:
            lines.append(f"🧨 原因：{item.get('message') or '未知错误'}")
        lines.append("------------------------------")

    return "\n".join(lines)


def dispatch_notify() -> None:
    if not GLOBAL_NOTIFY_BUFFERS:
        print("通知缓冲区为空，跳过推送。")
        return
    content = build_notify_report()
    print(content)
    if send_push_notification is None:
        print("通知发送跳过：未加载 SendNotify.py")
        return
    try:
        send_push_notification(SCRIPT_TITLE, content)
    except Exception as exc:
        print(f"通知发送失败：{exc}")


def get_wx_code(wxid: str):
    if not WX_SERVER_URL:
        print("❌ 未配置 wx_server_url 或 WX_SERVER_URL，无法请求 /mywc")
        return None
    try:
        resp = requests.get(
            f"{WX_SERVER_URL}/mywc",
            params={"wxid": wxid, "appId": WXAPP_ID},
            headers={"auth": wxid},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        candidates = [
            (((data.get("data") or {}).get("data") or {}).get("code")),
            (((data.get("data") or {}).get("data") or {}).get("loginCode")),
            (((data.get("data") or {}).get("data") or {}).get("wxcode")),
            ((data.get("data") or {}).get("code")),
            ((data.get("data") or {}).get("loginCode")),
            ((data.get("data") or {}).get("wxcode")),
            ((((data.get("result") or {}).get("data") or {}).get("code"))),
            ((((data.get("result") or {}).get("data") or {}).get("wxcode"))),
            ((data.get("result") or {}).get("code")),
            data.get("code"),
            data.get("loginCode"),
            data.get("wxcode"),
        ]
        for candidate in candidates:
            if isinstance(candidate, str) and len(candidate.strip()) >= 10:
                return candidate.strip()
        print(f"获取 code 失败: {str(data)[:160]}")
    except Exception as e:
        print(f"mywc 调用异常: {e}")
    return None


def build_gateway_url(path: str) -> str:
    if not WX_SERVER_URL:
        return ""
    return f"{WX_SERVER_URL}/{path.lstrip('/')}"


def try_json_loads(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "[{":
        return value
    try:
        return json.loads(text)
    except ValueError:
        return value


def find_nested_value(data: Any, keys: set[str]) -> Any:
    data = try_json_loads(data)
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key) in keys and value not in (None, ""):
                return value
        for value in data.values():
            found = find_nested_value(value, keys)
            if found not in (None, ""):
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_nested_value(item, keys)
            if found not in (None, ""):
                return found
    return None


def normalize_user_info_payload(data: Dict[str, Any]) -> OrderedDict | None:
    raw_data = find_nested_value(data, {"rawData"})
    if not raw_data:
        raw_data = find_nested_value(data, {"data"})
        raw_data = try_json_loads(raw_data)
        if isinstance(raw_data, dict):
            raw_data = raw_data.get("data") or json.dumps(raw_data, ensure_ascii=False, separators=(",", ":"))
    enc_data = find_nested_value(data, {"encryptedData"})
    iv = find_nested_value(data, {"iv"})
    signature = find_nested_value(data, {"signature"})

    if enc_data and iv and signature and raw_data:
        info = OrderedDict()
        info["rawData"] = raw_data if isinstance(raw_data, str) else json.dumps(raw_data, ensure_ascii=False, separators=(",", ":"))
        info["signature"] = signature
        info["encryptedData"] = enc_data
        info["iv"] = iv
        return info
    return None


def extract_token(data: Dict[str, Any]) -> str | None:
    token = find_nested_value(data, {"token"})
    if isinstance(token, str) and token.strip():
        return token.strip()
    return None


def load_myyhs_data() -> dict:
    if HUA_RUNTONG_MYYHS_DATA:
        try:
            return json.loads(HUA_RUNTONG_MYYHS_DATA)
        except ValueError as exc:
            print(f"HUA_RUNTONG_MYYHS_DATA 不是合法 JSON，使用默认 getUserInfo payload: {exc}")
    return {"api_name": "getUserInfo", "data": {}, "env": 1}


def fetch_user_info(ref: str):
    if not WX_SERVER_URL:
        print("❌ 未配置 wx_server_url 或 WX_SERVER_URL，无法请求 /myyhs")
        return None
    try:
        resp = requests.post(
            build_gateway_url("/myyhs"),
            json={
                "wxid": ref,
                "appId": HUA_RUNTONG_APPID,
                "data": load_myyhs_data(),
            },
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        info = normalize_user_info_payload(data)
        if info:
            return info
        print(f"获取用户信息失败: {data}")
    except Exception as e:
        print(f"请求 /myyhs 异常: {e}")
    return None


def fetch_phone_number(ref: str):
    if not WX_SERVER_URL:
        print("❌ 未配置 wx_server_url 或 WX_SERVER_URL，无法请求 /mysjh")
        return None
    try:
        resp = requests.get(
            build_gateway_url("/mysjh"),
            params={"wxid": ref, "appId": HUA_RUNTONG_APPID},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        phone_code = find_nested_value(data, {"code", "phoneCode"})
        if isinstance(phone_code, str) and phone_code.strip():
            return phone_code.strip()
        print(f"获取手机号失败: {data}")
    except Exception as e:
        print(f"请求 /mysjh 异常: {e}")
    return None

def load_token_cache():
    if os.path.exists(TOKEN_FILE):
        try:
            with open(TOKEN_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except:
            pass
    return {}

def save_token_cache(cache):
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

# ================== 华润通接口 ==================
def mini_request(data: dict, api_path: str, debug: bool = False) -> dict:
    params = OrderedDict()
    params["appId"] = "API_AUTH_MINI"
    params["apiPath"] = requests.utils.quote(api_path, safe="")
    params["timestamp"] = int(time.time() * 1000)
    params.update(data)

    payload = json.dumps(crypto4mid(params, MINI_SECRET, MINI_PUBKEY, debug=debug))
    headers = {
        "content-type": "application/json",
        "x-Hrt-Mid-Appid": "API_AUTH_MINI",
        "X-HRT-MID-NEWRISK": "NEWRISK",
        "User-Agent": HEADERS_AUTH["User-Agent"],
        "Referer": "https://servicewechat.com/wx66c62601b987e69d/160/page-frame.html",
    }
    resp = requests.post(HOST + api_path, data=payload, headers=headers, timeout=20)
    return resp.json()

def auto_login(nickname: str, openid: str) -> str | None:
    """优先接住直接登录 token；未返回 token 时再走手机号登录。"""
    print("  正在获取 code ...")
    wx_code = get_wx_code(openid)
    if not wx_code:
        return None
    print(f"  获取到 code: {wx_code[:10]}...")

    user_info = fetch_user_info(openid)
    if not user_info:
        print("  未获取到用户信息，无法继续")
        return None

    # 获取 randomCode（不再尝试直接登录）
    print("  获取 randomCode ...")
    data = OrderedDict([
        ("code", wx_code),
        ("typeId", "10005"),
        ("merchantCode", "1651200000001"),
        ("channelId", "WECHAT"),
        ("shopId", "A606"),
        ("businessChannel", "WeChatAPP_Member"),
        ("deviceChannel", "WECHAT"),
        ("userInfoEncryptedData", user_info),
    ])
    res = mini_request(data, "/api/user/member/login/upgraded/miniProgramAutoLoginCheck")
    direct_token = extract_token(res)
    if res.get("code") == "S0A00000" and direct_token:
        print(f"  AutoLoginCheck 直接返回 token: {direct_token[:8]}...")
        return direct_token
    random_code = res.get("data", {}).get("randomCode")
    if not random_code:
        print(f"  获取 randomCode 失败: {res}")
        return None

    # 手机号登录
    print("  尝试手机号登录...")
    phone_code = fetch_phone_number(openid)
    if not phone_code:
        return None
    data2 = OrderedDict([
        ("randomCode", random_code),
        ("typeId", "10005"),
        ("code", phone_code),
        ("channelId", "WECHAT"),
        ("shopId", "117"),
        ("merchantCode", "1999000000001"),
        ("businessChannel", "WeChatAPP_Member"),
        ("deviceChannel", "WECHAT"),
    ])
    res2 = mini_request(data2, "/api/user/member/login/upgraded/miniProgramLogin")
    login_token = extract_token(res2)
    if res2.get("code") == "S0A00000" and login_token:
        return login_token
    print(f"  手机号登录失败: {res2}")
    return None

# ================== 签到相关 ==================
def make_auth() -> dict:
    ts = int(time.time() * 1000)
    nonce = str(uuid.uuid4())
    raw = "".join(sorted([AUTH_APPID, AUTH_SECRET, str(ts), nonce]))
    sig = hashlib.md5(raw.encode()).hexdigest()
    return {"appid": AUTH_APPID, "nonce": nonce, "timestamp": ts, "signature": sig}

def auth_request(token: str, api_path: str, data: dict = None) -> dict:
    payload = {"auth": make_auth(), "token": token}
    if data:
        payload.update(data)
    resp = requests.post(HOST + api_path, json=payload, headers=HEADERS_AUTH, timeout=15)
    return resp.json()

def cry_request(token: str, api_path: str, data: dict = None) -> dict:
    params = {
        "apiPath": requests.utils.quote(api_path, safe=""),
        "timestamp": int(time.time() * 1000),
        "appId": "API_AUTH_WEB",
        "token": token,
    }
    if data:
        params.update(data)
    payload = json.dumps(crypto4mid(params, CRY_SECRET, CRY_PUBKEY))
    resp = requests.post(HOST + api_path, data=payload, headers=HEADERS_CRY, timeout=15)
    return resp.json()

# ----- 滑块验证 -----
TCAPTCHA_HOST = "https://turing.captcha.qcloud.com"
TCAPTCHA_AID  = 2098355475

def _calc_pow(pow_cfg: dict) -> tuple:
    prefix = pow_cfg.get("prefix", "")
    target_md5 = pow_cfg.get("md5", "")
    if not prefix or not target_md5:
        return "0" * 16 + "#0", 0
    t0 = time.time()
    for nonce in range(1, 500000):
        h = hashlib.md5(f"{prefix}{nonce}".encode()).hexdigest()
        if h == target_md5:
            elapsed = int((time.time() - t0) * 1000)
            return f"{prefix}{nonce}", elapsed
    elapsed = int((time.time() - t0) * 1000)
    return f"{prefix}1", elapsed

def solve_slider_captcha(risk_data: dict) -> dict | None:
    if not PILLOW_OK:
        print("  需要安装 Pillow: pip install Pillow")
        return None

    app_id = risk_data.get("appId", TCAPTCHA_AID)
    ua_b64 = base64.b64encode(HEADERS_CRY["User-Agent"].encode()).decode()
    cb = f"_aq_{random.randint(100000,999999)}"

    pre_url = (
        f"{TCAPTCHA_HOST}/cap_union_prehandle"
        f"?aid={app_id}&protocol=https&accver=1&showtype=popup"
        f"&ua={ua_b64}&noheader=0&fb=0&aged=0&enableAged=0"
        f"&enableDarkMode=0&grayscale=1&clientype=1&cap_cd=&uid="
        f"&lang=zh-cn&entry_url=https%3A%2F%2Fcloud.huaruntong.cn%2Fweb%2Fonline%2F%23%2FsignIn"
        f"&elder_captcha=0&js=%2FtgJCap.627c7f42.js&login_appid=&wb=2&subsid=1"
        f"&callback={cb}&sess="
    )
    pre_resp = requests.get(pre_url, headers={
        "User-Agent": HEADERS_CRY["User-Agent"],
        "Referer": "https://cloud.huaruntong.cn/",
        "Accept-Language": "zh-CN,zh-Hans;q=0.9",
    }, timeout=15)
    m = re.search(r'\((.*)\)', pre_resp.text, re.DOTALL)
    if not m:
        print(f"  prehandle 解析失败: {pre_resp.text[:200]}")
        return None
    pre_data = json.loads(m.group(1))
    sess = pre_data.get("sess", "")
    if not sess:
        return None

    comm_cfg = pre_data.get("data", {}).get("comm_captcha_cfg", {})
    pow_cfg = comm_cfg.get("pow_cfg", {})
    show_info = pre_data.get("data", {}).get("dyn_show_info", {})
    bg_cfg = show_info.get("bg_elem_cfg", {})
    fg_list = show_info.get("fg_elem_list", [])
    sp_url_path = show_info.get("sprite_url", "")
    bg_url_path = bg_cfg.get("img_url", "")
    bg_w, bg_h = bg_cfg.get("size_2d", [672, 480])

    target_elem = next((e for e in fg_list if e.get("id") == 1), None)
    print(f"  [滑块] prehandle 成功，图片尺寸 {bg_w}x{bg_h}")

    img_headers = {"Referer": "https://cloud.huaruntong.cn/", "User-Agent": HEADERS_CRY["User-Agent"]}
    bg_bytes = requests.get(TCAPTCHA_HOST + bg_url_path, headers=img_headers, timeout=15).content
    sp_bytes = requests.get(TCAPTCHA_HOST + sp_url_path, headers=img_headers, timeout=15).content

    bg_b64 = base64.b64encode(bg_bytes).decode()
    slider_b64 = base64.b64encode(sp_bytes).decode()
    if target_elem:
        try:
            sx, sy = target_elem["sprite_pos"]
            sw, sh = target_elem["size_2d"]
            sp_img = _Img.open(BytesIO(sp_bytes))
            buf = BytesIO()
            sp_img.crop((sx, sy, sx + sw, sy + sh)).save(buf, format="PNG")
            slider_b64 = base64.b64encode(buf.getvalue()).decode()
        except Exception as e:
            print(f"  [滑块] 裁图失败: {e}")

    try:
        match_resp = requests.post(
            f"{DDDDOCR_URL}/capcode",
            json={"slidingImage": slider_b64, "backImage": bg_b64, "simpleTarget": True},
            timeout=15,
        ).json()
        target_x = match_resp.get("result")
        print(f"  [滑块] 识别缺口 x={target_x}")
    except Exception as e:
        print(f"  [滑块] ddddocr 失败: {e}")
        return None

    if target_x is None:
        return None

    pow_answer, pow_calc_time = _calc_pow(pow_cfg)

    target_y = target_elem["init_pos"][1] if target_elem else 193
    ans = json.dumps([{"elem_id": 1, "type": "DynAnswerType_POS",
                       "data": f"{int(target_x)},{target_y}"}], separators=(",", ":"))

    verify_payload = {
        "sess": sess,
        "ans": ans,
        "pow_answer": pow_answer,
        "pow_calc_time": str(pow_calc_time),
    }
    verify_resp = requests.post(
        f"{TCAPTCHA_HOST}/cap_union_new_verify",
        data=verify_payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://cloud.huaruntong.cn",
            "Referer": "https://cloud.huaruntong.cn/",
            "User-Agent": HEADERS_CRY["User-Agent"],
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": "zh-CN,zh-Hans;q=0.9",
        },
        timeout=15,
    )
    verify_data = verify_resp.json()
    print(f"  [滑块] verify 结果: {verify_data}")

    ticket = verify_data.get("ticket", "")
    randstr = verify_data.get("randstr", "")
    if not ticket:
        print(f"  [滑块] 未获取到 ticket，errorCode={verify_data.get('errorCode')}")
        return None

    print(f"  [滑块] 成功 ticket={ticket[:20]}... randstr={randstr}")
    return {"ticket": ticket, "randstr": randstr}

def solve_click_captcha(token: str, risk_data: dict, max_refresh: int = 5) -> tuple:
    def detect_chars(cap_b64: str) -> dict:
        det = requests.post(f"{DDDDOCR_URL}/detection",
                            json={"image": cap_b64}, timeout=10).json()
        boxes = det.get("result", [])
        char_map = {}
        if PILLOW_OK and boxes:
            img = _Img.open(BytesIO(base64.b64decode(cap_b64)))
            for box in boxes:
                x1, y1, x2, y2 = int(box[0]), int(box[1]), int(box[2]), int(box[3])
                crop = img.crop((x1, y1, x2, y2))
                buf = BytesIO()
                crop.save(buf, format="PNG")
                text = requests.post(f"{DDDDOCR_URL}/classification",
                                     json={"image": base64.b64encode(buf.getvalue()).decode()},
                                     timeout=10).json().get("result", "").strip()
                if text:
                    char_map[text] = box
        else:
            for item in requests.post(f"{DDDDOCR_URL}/select",
                                      json={"image": cap_b64}, timeout=10).json():
                for text, bbox in item.items():
                    if text:
                        char_map[text] = bbox
        return char_map

    def get_targets(prm_b64: str) -> list:
        raw = requests.post(f"{DDDDOCR_URL}/classification",
                            json={"image": prm_b64}, timeout=10).json().get("result", "").strip()
        print(f"  prompt 原始识别: {raw!r}")
        m = re.search(r'[\u201c\u201d\u300c"]\([\u4e00-\u9fff]+)[\u201c\u201d\u300d"]', raw)
        if m:
            return list(m.group(1))
        after = re.split(r'点击', raw)
        return [c for c in (after[-1] if len(after) > 1 else raw) if '\u4e00' <= c <= '\u9fff']

    try:
        rd = risk_data
        for attempt in range(max_refresh + 1):
            cap_b64 = rd.get("captcha", "")
            prm_b64 = rd.get("prompt", "")
            if "," in cap_b64:
                cap_b64 = cap_b64.split(",", 1)[1]
            if "," in prm_b64:
                prm_b64 = prm_b64.split(",", 1)[1]

            img_w = rd.get("width", 400)
            img_h = rd.get("height", 200)
            need_num = rd.get("number", 1)

            targets = get_targets(prm_b64)
            print(f"  目标汉字: {targets}  (第{attempt+1}次)")

            char_map = detect_chars(cap_b64)
            print(f"  背景识别: {list(char_map.keys())}")

            matched = [ch for ch in targets if ch in char_map]
            if len(matched) >= need_num:
                clicks = []
                for ch in targets:
                    if ch in char_map:
                        bbox = char_map[ch]
                        clicks.append({
                            "x": round((bbox[0] + bbox[2]) / 2 / img_w, 2),
                            "y": round((bbox[1] + bbox[3]) / 2 / img_h, 2),
                        })
                    if len(clicks) >= need_num:
                        break
                print(f"  点击坐标: {clicks}")
                return clicks, rd

            print(f"  命中 {len(matched)}/{need_num}，刷新验证码...")
            if attempt < max_refresh:
                refresh = auth_request(token, "/api/common/captcha", {
                    "captchaType": rd.get("captchaType", "click"),
                    "type": rd.get("type", ""),
                })
                new_data = refresh.get("data", {})
                if new_data.get("captcha"):
                    rd = {**rd, **new_data}
                else:
                    break

        cap_b64 = rd.get("captcha", "")
        if "," in cap_b64:
            cap_b64 = cap_b64.split(",", 1)[1]
        img_w = rd.get("width", 400)
        img_h = rd.get("height", 200)
        need_num = rd.get("number", 1)
        char_map = detect_chars(cap_b64)
        targets = get_targets(rd.get("prompt", "").split(",", 1)[-1])
        clicks = []
        for ch in targets:
            if ch in char_map:
                bbox = char_map[ch]
                clicks.append({
                    "x": round((bbox[0] + bbox[2]) / 2 / img_w, 2),
                    "y": round((bbox[1] + bbox[3]) / 2 / img_h, 2),
                })
            if len(clicks) >= need_num:
                break
        if len(clicks) < need_num:
            for bbox in char_map.values():
                coord = {
                    "x": round((bbox[0] + bbox[2]) / 2 / img_w, 2),
                    "y": round((bbox[1] + bbox[3]) / 2 / img_h, 2),
                }
                if coord not in clicks:
                    clicks.append(coord)
                if len(clicks) >= need_num:
                    break
        return (clicks[:need_num] if clicks else None), rd
    except Exception as e:
        print(f"  ddddocr 失败: {e}")
        return None, risk_data

def _signin_params(extra: dict = None) -> dict:
    p = {
        "answerResult": 1,
        "channelId": "H5",
        "merchantCode": "1641000001532",
        "storeCode": "qiandaosonjifen",
        "sysId": "T0000001",
        "transactionUuid": str(uuid.uuid4()),
        "inviteCode": "",
    }
    if extra:
        p.update(extra)
    return p

def do_signin(token: str, retry: int = 2) -> bool:
    result = cry_request(token, "/api/points/saveQuestionSignin", _signin_params())
    code = result.get("code")

    if code == "S0A00000":
        print(f"  签到成功，获得 {result.get('data', {}).get('point', 0)} 积分")
        return True
    if code == "E1B00101":
        print("  今日已签到")
        return True
    if code in ("E1B00002", "E1M00009"):
        print(f"  ⚠️  Token 已失效（code={code}）")
        return False
    if code == "ETR01002":
        print(f"  触发短信验证码，无法自动处理")
        return False

    # 滑块验证
    if code == "ETR01004" and retry > 0:
        print(f"  触发腾讯防水墙滑块验证 (剩余重试 {retry})")
        risk_code = result.get("riskCode", "")
        risk_data = result.get("riskData", {})
        slider_result = solve_slider_captcha(risk_data)
        if not slider_result:
            print("  滑块验证失败，重试...")
            return do_signin(token, retry - 1)
        verify = cry_request(token, "/api/points/saveQuestionSignin", _signin_params({
            "riskCode": risk_code,
            "riskData": {
                "ticket": slider_result["ticket"],
                "randstr": slider_result["randstr"],
            },
        }))
        v_code = verify.get("code")
        if v_code == "S0A00000":
            print(f"  验证通过，签到成功，获得 {verify.get('data',{}).get('point',0)} 积分")
            return True
        if v_code == "E1B00101":
            print("  验证通过，今日已签到")
            return True
        return do_signin(token, retry - 1)

    # 点选验证
    if code == "ETR01001" and retry > 0:
        print(f"  触发点选验证码 (剩余重试 {retry})")
        risk_code = result.get("riskCode", "")
        risk_data = result.get("riskData", {})
        clicks, risk_data = solve_click_captcha(token, risk_data)
        if not clicks:
            print("  验证码识别失败")
            return False
        verify = cry_request(token, "/api/points/saveQuestionSignin", _signin_params({
            "riskCode": risk_code,
            "riskData": {"captcha": clicks},
        }))
        v_code = verify.get("code")
        if v_code == "S0A00000":
            print(f"  验证通过，签到成功，获得 {verify.get('data',{}).get('point',0)} 积分")
            return True
        if v_code == "E1B00101":
            print("  验证通过，今日已签到")
            return True
        return do_signin(token, retry - 1)

    print(f"  签到失败: code={code} msg={result.get('msg','')}")
    return False

# ================== 主流程 ==================
def run_account(index: int, nickname: str, openid: str | None, cache: dict):
    result = {
        "index": index,
        "account": nickname,
        "ok": False,
        "status_text": "执行失败",
        "message": "",
        "username": "",
        "before_points": "-",
        "after_points": "-",
        "delta_points": 0,
        "sign_text": "",
    }
    print(f"\n{'='*40}")
    print(f"账号: {nickname}")

    token_info = cache.get(nickname, {})
    token = token_info.get("token")

    # 检查缓存 token 是否有效
    if token:
        try:
            week = auth_request(token, "/api/points/queryWeekSignin")
            if week.get("code") in ("E1B00002", "E1M00009", None):
                print("  缓存 token 已失效")
                token = None
            else:
                data = week.get("data", {})
                signin_days = data.get("signinDays", 0)
                total_points = int(data.get("totalPoints", 0) or 0)
                today_item = next((x for x in data.get("list", []) if x.get("today")), {})
                already_signed = today_item.get("signinPoints", 0) < 0
                print(f"  累计签到 {signin_days} 天，总积分 {total_points}，今日: {'已签到' if already_signed else '未签到'}")
                result["before_points"] = total_points
                result["after_points"] = total_points
                if already_signed:
                    print("  今日已签到，跳过")
                    result.update({
                        "ok": True,
                        "status_text": "今日已签到",
                        "sign_text": "今日已签到",
                    })
                    append_notify_result(result)
                    return result
                if do_signin(token):
                    result.update({
                        "ok": True,
                        "status_text": "签到成功",
                        "sign_text": "签到成功",
                    })
                    append_notify_result(result)
                    return result
                token = None
        except Exception as e:
            print(f"  验证 token 异常: {e}")
            token = None

    # 无有效 token 且有 openid，尝试自动登录
    if openid:
        new_token = auto_login(nickname, openid)
        if new_token:
            cache[nickname] = {"token": new_token}
            save_token_cache(cache)
            print(f"  自动登录成功，token: {new_token[:8]}...")
            # 登录与签到之间随机延时 3-5 秒
            delay = random.uniform(3, 5)
            print(f"  等待 {delay:.1f} 秒后签到...")
            time.sleep(delay)
            if do_signin(new_token):
                result.update({
                    "ok": True,
                    "status_text": "自动登录并签到成功",
                    "sign_text": "自动登录并签到成功",
                })
                append_notify_result(result)
                return result
            result["message"] = "自动登录后签到失败"
            append_notify_result(result)
            return result
        print("  自动登录失败")
        result["message"] = "自动登录失败"
        append_notify_result(result)
        return result

    print("  缺少 openid 且缓存 token 无效，无法自动登录，跳过")
    result["message"] = "缺少 openid 且缓存 token 无效"
    append_notify_result(result)
    return result

def main():
    accounts = parse_accounts()
    cache = load_token_cache()
    if accounts:
        print(f"从环境变量获取到 {len(accounts)} 个账号")
        for idx, openid in enumerate(accounts, 1):
            run_account(idx, mask_account(openid), openid, cache)
    else:
        print("未配置 huaruntong_wxid / HUA_RUNTONG_WXID / txpopenid，尝试使用本地 token 缓存签到")
        if not cache:
            print("token 缓存为空，退出")
            append_notify_result({
                "index": 0,
                "account": "-",
                "ok": False,
                "status_text": "配置错误",
                "message": "未配置账号变量且 token 缓存为空",
                "delta_points": 0,
            })
            dispatch_notify()
            return
        for nickname in cache:
            run_account(0, nickname, None, cache)
    dispatch_notify()

if __name__ == "__main__":
    main()
