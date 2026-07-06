#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
拼多多果园 v1.1.0（mywc网关聚合推送版）

功能：自动执行拼多多果园签到、浇水、任务领奖、偷水，支持多账号，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL   必填其一，自建授权服务器域名
   - 示例：http://127.0.0.1:8110
   - 脚本会自动拼接 /mywc
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=wx32540bd863b27570
   - 请求头：auth=账号标识

2. 账号变量：
   pdd_wxid 或 PDD_WXID                         推荐，拼多多果园专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：wxid_a&wxid_b&wxid_c

3. Cookie直连：
   pdd_cookie 或 PDD_COOKIE                      可选，完整 Cookie，提供后跳过 code 登录
   - Cookie 模式通常只建议单账号

4. 推送：
   同目录 SendNotify.py，提供 send_push_notification(title, content)

青龙任务建议：
   0 8 * * * python3 拼多多果园.py
"""
import os, sys, json, time, random, re, subprocess
from datetime import datetime
from pathlib import Path

import requests as _requests

USE_CFFI = False
try:
    from curl_cffi import requests as _curl_requests
    USE_CFFI = True
except ImportError:
    pass

# ===== 硬编码配置 =====
PDD_MINI_APP_ID = "wx32540bd863b27570"
PDD_XCX_VERSION = "v8.6.21"
PDD_APP_ID = 33

TOKEN_CACHE = "./pdd_token_cache.json"
COOKIE_CACHE = "./pdd_cookie_cache.json"

MANOR_BASE = "https://mobile.yangkeduo.com/proxy/api/api"
LOGIN_BASE = "https://api.pinduoduo.com"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/132.0.0.0 Safari/537.36 "
    "MicroMessenger/7.0.20.1781(0x6700143B) "
    "NetType/WIFI MiniProgramEnv/Windows "
    "WindowsWechat/WMPF WindowsWechat(0x63090a13) "
    "UnifiedPCWindowsWechat(0xf254193e) XWEB/19841"
)

# ===== 环境变量 =====
SCRIPT_TITLE = "拼多多果园"
GLOBAL_NOTIFY_BUFFERS = []

PDD_WXID_RAW = (
    os.environ.get("pdd_wxid")
    or os.environ.get("PDD_WXID")
    or os.environ.get("PDD_OPENID")
    or ""
).strip()
WX_SERVER_URL = (os.environ.get("wx_server_url") or os.environ.get("WX_SERVER_URL") or "").strip().rstrip("/")
COOKIE_STR = (os.environ.get("pdd_cookie") or os.environ.get("PDD_COOKIE") or "").strip()


# ===== 工具函数 =====
def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def mask(s, h=4, t=4):
    s = str(s)
    if len(s) <= h + t:
        return s[:h] + "***"
    return s[:h] + "***" + s[-t:]


def parse_accounts(raw):
    if not raw:
        return []
    return [x.strip() for x in re.split(r"[&，,\\n\\r]+", raw) if x.strip()]


def append_notify_result(index, account, ok, message, initial_water=0, final_water=0, watered=0, checkin=False, accepted=0, claimed=0):
    GLOBAL_NOTIFY_BUFFERS.append({
        "index": index,
        "account": account,
        "ok": bool(ok),
        "message": str(message or ""),
        "initial_water": int(initial_water or 0),
        "final_water": int(final_water or 0),
        "watered": int(watered or 0),
        "checkin": bool(checkin),
        "accepted": int(accepted or 0),
        "claimed": int(claimed or 0),
    })


def build_notify_report():
    total = len(GLOBAL_NOTIFY_BUFFERS)
    success = sum(1 for item in GLOBAL_NOTIFY_BUFFERS if item.get("ok"))
    failed = total - success
    total_watered = sum(int(item.get("watered") or 0) for item in GLOBAL_NOTIFY_BUFFERS)
    total_claimed = sum(int(item.get("claimed") or 0) for item in GLOBAL_NOTIFY_BUFFERS)

    lines = [
        "==============================",
        f"🕒 执行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"📊 统计数据：成功 {success} / 总计 {total}",
        f"✅ 成功账号：{success} 个",
        f"❌ 失败账号：{failed} 个",
        f"💧 浇水次数：{total_watered} 次",
        f"🎁 任务领奖：{total_claimed} 个",
        "==============================",
    ]

    for item in GLOBAL_NOTIFY_BUFFERS:
        ok = bool(item.get("ok"))
        lines.extend([
            f"{'🧑‍💻' if ok else '🧟'} 【账号{item.get('index')}】{item.get('account')}",
            f"{'✅' if ok else '❌'} 状态：{item.get('message')}",
        ])
        if ok:
            lines.extend([
                f"💧 水滴：始 {item.get('initial_water')} ➔ 终 {item.get('final_water')}",
                f"🚿 浇水：{item.get('watered')} 次",
                f"📌 签到：{'成功/已签' if item.get('checkin') else '无变化'}",
                f"🎁 任务：接受 {item.get('accepted')} 个，领取 {item.get('claimed')} 个",
            ])
        lines.append("------------------------------")
    return "\n".join(lines)


def dispatch_notify():
    if not GLOBAL_NOTIFY_BUFFERS:
        return
    try:
        from SendNotify import send_push_notification
        send_push_notification(SCRIPT_TITLE, build_notify_report())
        log("聚合推送已发送")
    except Exception as e:
        log(f"聚合推送失败: {e}")


def cookie_str_to_dict(cookie_str):
    cookies = {}
    for item in cookie_str.split(";"):
        item = item.strip()
        if "=" in item:
            k, v = item.split("=", 1)
            cookies[k.strip()] = v.strip()
    return cookies


def cookie_dict_to_str(cookies):
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def extract_uid(cookie_str):
    """从Cookie字符串中提取 pdd_user_id"""
    m = re.search(r'pdd_user_id=(\d+)', cookie_str)
    return m.group(1) if m else ""


# ===== 缓存 =====
def read_cache(path):
    try:
        p = Path(path)
        return json.loads(p.read_text("utf-8")) if p.exists() else {}
    except Exception:
        return {}


def write_cache(path, data):
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    except Exception as e:
        log(f"[缓存写入失败] {e}")


def cached_cookie(openid):
    entry = read_cache(COOKIE_CACHE).get(openid, {})
    return entry.get("cookie_str", "")


def save_cookie_cache(openid, cookie_str):
    c = read_cache(COOKIE_CACHE)
    c[openid] = {
        "cookie_str": cookie_str,
        "updatedAt": datetime.now().isoformat()
    }
    write_cache(COOKIE_CACHE, c)


# ===== HTTP 会话 =====
def make_session():
    if USE_CFFI:
        s = _curl_requests.Session(impersonate="chrome")
    else:
        s = _requests.Session()
        s.headers.update({
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
        })
    s.headers.update({"User-Agent": UA})
    return s


# ===== Code 登录流程 =====
def get_wx_code(wxid):
    """通过自建 mywc 网关获取微信小程序 code"""
    if not WX_SERVER_URL:
        log("[mywc] 未配置 wx_server_url 或 WX_SERVER_URL")
        return None
    try:
        s = _requests.Session()
        r = s.get(
            f"{WX_SERVER_URL}/mywc",
            params={"wxid": wxid, "appId": PDD_MINI_APP_ID},
            headers={"auth": wxid},
            timeout=20
        )
        if r.status_code != 200:
            log(f"[mywc] HTTP {r.status_code}: {r.text[:200]}")
            return None
        d = r.json()
        data = d.get("data")
        code = d.get("code") or (data.get("code") if isinstance(data, dict) else None) or (data if isinstance(data, str) else None)
        if not code:
            log(f"[mywc] 未返回code: {json.dumps(d, ensure_ascii=False)[:200]}")
            return None
        return code
    except Exception as e:
        log(f"[mywc] 异常: {e}")
        return None


# ===== Anti-Content 生成 (Node.js) =====
# ---- 内嵌 Anti-Content 生成器 (Node.js) ----
_ANTI_TOKEN_JS = r'''
﻿// PDD anti-token 生成器 - Node.js
// 供 Python subprocess 调用: node anti_token.js

const https = require('https');
const util = require('util');

const SDK_URL = 'https://static.pddpic.com/assets/js/risk_control_anti_dac600d707bbff03e560.js';
const USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36';

async function downloadSDK() {
    return new Promise((resolve, reject) => {
        https.get(SDK_URL, { rejectUnauthorized: false, timeout: 30000 }, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => resolve(data));
        }).on('error', reject);
    });
}

async function generateAntiToken() {
    const sdkCode = await downloadSDK();

    // Fake browser environment (minimal)
    const win = {
        webpackChunkmobile_cartoon_activity: [],
        navigator: {
            userAgent: USER_AGENT, platform: 'Win32', language: 'zh-CN',
            languages: ['zh-CN', 'zh'], cookieEnabled: true,
            hardwareConcurrency: 8, maxTouchPoints: 0,
            vendor: 'Google Inc.', appVersion: '5.0',
            appName: 'Netscape', onLine: true,
            plugins: { length: 3 }, mimeTypes: { length: 2 },
            connection: null, getBattery: null, sendBeacon: () => true,
        },
        document: {
            cookie: '', referrer: '', title: 'test', domain: 'mobile.pinduoduo.com',
            readyState: 'complete', visibilityState: 'visible', hidden: false,
            createElement: (tag) => ({ style: {}, setAttribute: () => {}, getAttribute: () => null, addEventListener: () => {}, removeEventListener: () => {}, appendChild: () => {}, removeChild: () => {}, getContext: () => null, tagName: (tag || '').toUpperCase() }),
            getElementById: () => null, querySelector: () => null, querySelectorAll: () => [],
            getElementsByTagName: () => [], addEventListener: () => {}, removeEventListener: () => {},
            createEvent: () => ({ initEvent: () => {} }),
            body: { appendChild: () => {}, removeChild: () => {}, style: {}, scrollTop: 0, scrollLeft: 0, clientWidth: 1920, clientHeight: 1080 },
            head: { appendChild: () => {}, removeChild: () => {} },
            documentElement: { scrollTop: 0, scrollLeft: 0, clientWidth: 1920, clientHeight: 1080, style: {} },
        },
        location: { href: 'https://mobile.pinduoduo.com/garden_index_lz_0.html', hostname: 'mobile.pinduoduo.com', protocol: 'https:', pathname: '/garden_index_lz_0.html', search: '', hash: '', host: 'mobile.pinduoduo.com', origin: 'https://mobile.pinduoduo.com' },
        screen: { width: 1920, height: 1080, colorDepth: 24, availWidth: 1920, availHeight: 1040 },
        performance: { now: () => Date.now() - 1000, timing: { navigationStart: Date.now() - 3000 }, getEntriesByType: () => [], mark: () => {}, measure: () => {} },
        history: { length: 3, state: null, pushState: () => {}, replaceState: () => {} },
        innerWidth: 1920, innerHeight: 1080, outerWidth: 1920, outerHeight: 1080,
        devicePixelRatio: 1, pageXOffset: 0, pageYOffset: 0, scrollX: 0, scrollY: 0,
        localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {}, clear: () => {}, length: 0, key: () => null },
        sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {}, clear: () => {}, length: 0, key: () => null },
        crypto: { getRandomValues: (arr) => { for (let i = 0; i < arr.length; i++) arr[i] = Math.floor(Math.random() * 256); return arr; }, subtle: null },
        addEventListener: () => {}, removeEventListener: () => {}, dispatchEvent: () => true,
        setTimeout, clearTimeout, setInterval, clearInterval,
        Promise, JSON, Math, Date, Array, Object, String, Number, Boolean, RegExp, Error,
        Uint8Array, Uint16Array, Int32Array, ArrayBuffer, DataView, Function,
        Map, Set, WeakMap, WeakSet, Symbol,
        Element: function Element() {}, HTMLElement: function HTMLElement() {}, Node: function Node() {},
        Event: function Event() {}, EventTarget: function EventTarget() {}, HTMLCanvasElement: function HTMLCanvasElement() {},
        TextEncoder: util.TextEncoder, TextDecoder: util.TextDecoder,
        encodeURIComponent, decodeURIComponent, encodeURI, decodeURI,
        parseInt, parseFloat, isNaN, isFinite, console,
        MutationObserver: function () { this.observe = () => {}; this.disconnect = () => {}; },
        IntersectionObserver: function () { this.observe = () => {}; this.disconnect = () => {}; },
        URL: typeof URL !== 'undefined' ? URL : function (u) { this.href = u; },
        Blob: typeof Blob !== 'undefined' ? Blob : function () {},
        FormData: function () { this.append = () => {}; },
    };
    win.self = win;
    win.window = win;
    win.top = win;
    win.parent = win;

    global.self = win;
    global.window = win;
    global.document = win.document;
    global.location = win.location;
    global.screen = win.screen;
    global.performance = win.performance;
    global.localStorage = win.localStorage;
    global.sessionStorage = win.sessionStorage;
    global.fetch = () => Promise.resolve({ ok: true, json: () => Promise.resolve({}), text: () => Promise.resolve('') });
    global.XMLHttpRequest = function () { this.open = () => {}; this.send = () => {}; };
    global.MutationObserver = win.MutationObserver;
    global.TextEncoder = win.TextEncoder;
    global.TextDecoder = win.TextDecoder;
    global.Element = win.Element;
    global.HTMLElement = win.HTMLElement;
    global.Node = win.Node;
    global.Event = win.Event;
    global.EventTarget = win.EventTarget;
    global.HTMLCanvasElement = win.HTMLCanvasElement;

    // Execute SDK
    eval(sdkCode);

    const chunks = win.webpackChunkmobile_cartoon_activity;
    if (!chunks || !chunks.length) {
        throw new Error('SDK chunks not loaded');
    }

    const [, modules] = chunks[0];
    const cache = {};
    function req(id) {
        if (cache[id]) return cache[id].exports;
        const m = { i: id, l: false, exports: {} };
        cache[id] = m;
        if (modules[id]) { modules[id].call(m.exports, m, m.exports, req); m.l = true; }
        return m.exports;
    }
    req.r = (e) => { Object.defineProperty(e, '__esModule', { value: true }); };
    req.d = (e, n, g) => { if (!Object.prototype.hasOwnProperty.call(e, n)) Object.defineProperty(e, n, { enumerable: true, get: g }); };
    req.o = (o, p) => Object.prototype.hasOwnProperty.call(o, p);
    req.n = (m) => { const g = m && m.__esModule ? () => m.default : () => m; req.d(g, 'a', g); return g; };
    req.p = '';

    const sdk = req(96636);
    const SDKClass = sdk.default;
    const instance = new SDKClass({ serverTime: Date.now(), _2827c887a48a351a: false });

    const token = await instance.messagePackSync({
        touchEventData: true, clickEventData: true, focusblurEventData: true,
        changeEventData: true, locationInfo: true, referrer: true,
        browserSize: true, browserInfo: true, token: true, fingerprint: true
    });

    console.log(token);
}

generateAntiToken().catch(e => { console.error(e.message); process.exit(1); });

'''

def generate_anti_content():
    """调用 Node.js 生成 PDD anti_content token (JS 代码内嵌，无需外部文件)"""
    import tempfile
    try:
        # 写入临时 JS 文件
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", delete=False, encoding="utf-8"
        )
        tmp.write(_ANTI_TOKEN_JS)
        tmp.close()

        log("  [AntiToken] 正在生成...")
        result = subprocess.run(
            ["node", tmp.name],
            capture_output=True, text=True, timeout=30
        )
        os.unlink(tmp.name)  # 立即删除临时文件

        if result.returncode == 0:
            token = result.stdout.strip()
            if token and len(token) > 50:
                log(f"  [AntiToken] 生成成功: {token[:40]}...")
                return token
            else:
                log(f"  [AntiToken] 输出异常: {result.stdout[:100]}")
                return None
        else:
            log(f"  [AntiToken] Node.js 错误: {result.stderr[:200]}")
            return None
    except FileNotFoundError:
        log("[AntiToken] Node.js 未安装，将尝试无 anti_content 登录")
        return None
    except subprocess.TimeoutExpired:
        log("[AntiToken] 生成超时")
        return None
    except Exception as e:
        log(f"[AntiToken] 异常: {e}")
        return None


def pdd_code_login(openid):
    """
    拼多多 code 登录流程:
    1. wx.login() 获取 code (通过 wx_server)
    2. POST /login 获取 verify_auth_token
    3. POST /api/sigerus/verify/login 获取 uid/uin/access_token
    4. 组装完整 Cookie
    """
    log(f"  --- Code登录 openId={mask(openid)} ---")

    # Step 1: 获取微信 code
    code = get_wx_code(openid)
    if not code:
        return None
    log(f"  [1/4] 获取code: {mask(code, 6, 6)}")

    s = make_session()

    # 获取 server_time
    stm_url = f"{LOGIN_BASE}/api/server/_stm"
    try:
        stm_resp = s.get(stm_url, timeout=10)
        server_time = stm_resp.json().get("server_time", int(time.time() * 1000))
    except Exception:
        server_time = int(time.time() * 1000)

    rand_str = ''.join(random.choices(
        'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=15
    ))

    # Step 2: 用 code 换取 verify_auth_token
    login_url = f"{LOGIN_BASE}/login"
    login_params = {
        "xcx": "20161201",
        "xcx_version": PDD_XCX_VERSION,
        "xcx_hash": f"{server_time}{rand_str}"
    }
    # 生成 anti_content
    anti_content = generate_anti_content()

    login_body = {
        "code": code,
        "has_auth": False,
        "app_id": PDD_APP_ID,
        "support_enhance_type": 3,
        "xcx_version": PDD_XCX_VERSION
    }
    if anti_content:
        login_body["anti_content"] = anti_content
    s.headers.update({
        "Content-Type": "application/json;charset=UTF-8",
        "Referer": f"https://servicewechat.com/{PDD_MINI_APP_ID}/1840/page-frame.html",
        "x-xcx-queries": f"mini_program_name=pdd;mp_theme_version={PDD_XCX_VERSION}",
        "rfp": "LqTtkNj4yziKrApKfKKwmWgc2NXA1yXo",
    })
    if anti_content:
        s.headers["anti-content"] = anti_content

    try:
        r = s.post(login_url, params=login_params, json=login_body, timeout=15)
        result = r.json()
    except Exception as e:
        log(f"  [2/6] /login 请求失败: {e}")
        return None

    error_code = result.get("error_code", 0)
    verify_auth_token = result.get("verify_auth_token", "")

    if not verify_auth_token:
        log(f"  [2/6] /login 未返回verify_auth_token")
        log(f"       error_code={error_code}, msg={result.get('error_msg', '')}")
        log(f"       完整响应: {json.dumps(result, ensure_ascii=False)[:500]}")
        # 尝试直接提取 uid/access_token (error_code=0 可能表示直接成功)
        direct_uid = result.get("uid") or result.get("user_id") or result.get("data", {}).get("uid")
        direct_token = result.get("access_token") or result.get("data", {}).get("access_token")
        if direct_uid and direct_token:
            uid = direct_uid
            uin = result.get("uin", "") or result.get("data", {}).get("uin", "")
            access_token = direct_token
            acid = result.get("acid", "") or result.get("data", {}).get("acid", "")
            log(f"  [2/4] 直接获取到登录数据! uid={uid}")
            # 跳转到 cookie 组装
            pdda = access_token
            response_cookies = {}
            for cookie in s.cookies:
                response_cookies[cookie.name] = cookie.value
            cookie_parts = [f"PDDAccessToken={pdda}", f"pdd_user_id={uid}",
                            f"pdd_user_uin={uin}"]
            if acid:
                cookie_parts.append(f"acid={acid}")
            api_uid = response_cookies.get("api_uid", "")
            if api_uid:
                cookie_parts.append(f"api_uid={api_uid}")
            for k, v in response_cookies.items():
                if k not in ["api_uid"]:
                    cookie_parts.append(f"{k}={v}")
            cookie_str = "; ".join(cookie_parts)
            log(f"  [5/6] Cookie组装完成")
            log(f"  [6/6] 访问果园页面收集额外Cookie...")
            cookies_for_page = cookie_str_to_dict(cookie_str)
            garden_url = f"https://mobile.yangkeduo.com/garden_index_lz_0.html?_pdd_fs=1&_pdd_tc=676666&_pdd_sbs=1&fun_id=wechat_app_home"
            try:
                page_s = make_session()
                page_s.headers.update({
                    "User-Agent": UA,
                    "Referer": "https://servicewechat.com/wx32540bd863b27570/1840/page-frame.html",
                })
                page_r = page_s.get(garden_url, cookies=cookies_for_page, timeout=15, allow_redirects=True)
                # Collect cookies from the page response
                for pc in page_s.cookies:
                    try:
                        cookies_for_page[pc.name] = pc.value
                    except AttributeError:
                        if isinstance(pc, str) and "=" in pc:
                            k, v = pc.split("=", 1)
                            cookies_for_page[k.strip()] = v.strip()
                cookie_str = cookie_dict_to_str(cookies_for_page)
                log(f"  [6/6] Cookie已更新 (共{len(cookies_for_page)}项)")
            except Exception as e:
                log(f"  [6/6] 页面访问异常(可忽略): {e}")
            return cookie_str, str(uid), uin
        return None

    log(f"  [2/6] 获取verify_auth_token: {mask(verify_auth_token)}")

    # Step 3: 第二轮 /login —— 用第一轮的 token 作为 header，获取新的 verify_auth_token
    log(f"  [3/6] 第二轮 /login ...")
    code2 = get_wx_code(openid)
    if not code2:
        log(f"  [3/6] 获取第二个code失败")
        return None
    log(f"        code2={mask(code2, 6, 6)}")

    anti_login2 = generate_anti_content()
    rand_login2 = ''.join(random.choices(
        'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=15
    ))
    login2_params = {
        "xcx": "20161201",
        "xcx_version": PDD_XCX_VERSION,
        "xcx_hash": f"{server_time}{rand_login2}"
    }
    login2_body = {
        "code": code2,
        "has_auth": False,
        "app_id": PDD_APP_ID,
        "support_enhance_type": 3,
        "xcx_version": PDD_XCX_VERSION
    }
    if anti_login2:
        login2_body["anti_content"] = anti_login2

    s2_headers = {
        "Content-Type": "application/json;charset=UTF-8",
        "Referer": f"https://servicewechat.com/{PDD_MINI_APP_ID}/1840/page-frame.html",
        "x-xcx-queries": f"mini_program_name=pdd;mp_theme_version={PDD_XCX_VERSION}",
        "rfp": "LqTtkNj4yziKrApKfKKwmWgc2NXA1yXo",
        "verifyauthtoken": verify_auth_token,
    }
    if anti_login2:
        s2_headers["anti-content"] = anti_login2

    s2 = make_session()
    s2.headers.update(s2_headers)
    try:
        r2 = s2.post(login_url, params=login2_params, json=login2_body, timeout=15)
        result2 = r2.json()
    except Exception as e:
        log(f"  [3/6] 第二轮 /login 请求失败: {e}")
        return None

    error_code2 = result2.get("error_code", 0)
    verify_auth_token2 = result2.get("verify_auth_token", "")

    if not verify_auth_token2:
        # error_code=0 可能表示直接登录成功，检查是否返回了 uid/access_token
        direct_uid2 = result2.get("uid") or result2.get("data", {}).get("uid")
        direct_token2 = result2.get("access_token") or result2.get("data", {}).get("access_token")
        if direct_uid2 and direct_token2:
            log(f"  [3/6] 第二轮直接登录成功! uid={direct_uid2}")
            uid = direct_uid2
            uin = result2.get("uin", "") or result2.get("data", {}).get("uin", "")
            access_token = direct_token2
            acid = result2.get("acid", "") or result2.get("data", {}).get("acid", "")
            # 组装Cookie后直接返回
            pdda = access_token
            response_cookies = {}
            try:
                for cookie in s2.cookies:
                    response_cookies[cookie.name] = cookie.value
            except AttributeError:
                for cookie in s2.cookies:
                    if hasattr(cookie, 'name'):
                        response_cookies[cookie.name] = cookie.value
                    elif isinstance(cookie, str) and '=' in cookie:
                        k, v = cookie.split('=', 1)
                        response_cookies[k.strip()] = v.strip()
            cookie_parts = [f"PDDAccessToken={pdda}", f"pdd_user_id={uid}",
                            f"pdd_user_uin={uin}"]
            if acid:
                cookie_parts.append(f"acid={acid}")
            api_uid = response_cookies.get("api_uid", "")
            if api_uid:
                cookie_parts.append(f"api_uid={api_uid}")
            for k, v in response_cookies.items():
                if k not in ["api_uid"]:
                    cookie_parts.append(f"{k}={v}")
            cookie_str = "; ".join(cookie_parts)
            log(f"  [5/6] Cookie组装完成")
            log(f"  [6/6] 访问果园页面收集额外Cookie...")
            cookies_for_page = cookie_str_to_dict(cookie_str)
            garden_url = f"https://mobile.yangkeduo.com/garden_index_lz_0.html?_pdd_fs=1&_pdd_tc=676666&_pdd_sbs=1&fun_id=wechat_app_home"
            try:
                page_s = make_session()
                page_s.headers.update({
                    "User-Agent": UA,
                    "Referer": "https://servicewechat.com/wx32540bd863b27570/1840/page-frame.html",
                })
                page_r = page_s.get(garden_url, cookies=cookies_for_page, timeout=15, allow_redirects=True)
                # Collect cookies from the page response
                for pc in page_s.cookies:
                    try:
                        cookies_for_page[pc.name] = pc.value
                    except AttributeError:
                        if isinstance(pc, str) and "=" in pc:
                            k, v = pc.split("=", 1)
                            cookies_for_page[k.strip()] = v.strip()
                cookie_str = cookie_dict_to_str(cookies_for_page)
                log(f"  [6/6] Cookie已更新 (共{len(cookies_for_page)}项)")
            except Exception as e:
                log(f"  [6/6] 页面访问异常(可忽略): {e}")
            return cookie_str, str(uid), uin

        log(f"  [3/6] 第二轮 /login 未返回verify_auth_token 且无直接登录数据")
        log(f"       error_code={error_code2}, msg={result2.get('error_msg', '')}")
        log(f"       完整响应: {json.dumps(result2, ensure_ascii=False)[:300]}")
        return None

    verify_auth_token = verify_auth_token2
    log(f"  [3/6] 第二轮token: {mask(verify_auth_token)}")

    # Step 4: 用第二轮 verify_auth_token 换取正式 token
    rand_str2 = ''.join(random.choices(
        'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=15
    ))
    verify_url = f"{LOGIN_BASE}/api/sigerus/verify/login"
    verify_params = {
        "xcx": "20161201",
        "xcx_version": PDD_XCX_VERSION,
        "xcx_hash": f"{server_time}{rand_str2}"
    }
    # 为 verify/login 重新生成 anti_content
    anti_content2 = generate_anti_content()
    verify_body = {
        "has_auth": False,
        "support_enhance_type": 3,
        "verify_auth_token": verify_auth_token,
        "xcx_version": PDD_XCX_VERSION
    }
    if anti_content2:
        verify_body["anti_content"] = anti_content2
    s.headers["verifyauthtoken"] = verify_auth_token
    if anti_content2:
        s.headers["anti-content"] = anti_content2

    try:
        r = s.post(verify_url, params=verify_params, json=verify_body, timeout=15)
        result = r.json()
    except Exception as e:
        log(f"  [4/6] /verify/login 请求失败: {e}")
        return None

    uid = result.get("uid", 0)
    uin = result.get("uin", "")
    access_token = result.get("access_token", "")
    acid = result.get("acid", "")

    if not uid or not access_token:
        log(f"  [4/6] 登录失败: {json.dumps(result, ensure_ascii=False)[:300]}")
        return None

    log(f"  [4/6] 登录成功! uid={uid}, uin={mask(uin)}")

    # Step 4: 组装完整 Cookie
    pdda = access_token

    # 收集响应 cookies
    response_cookies = {}
    try:
        for cookie in s.cookies:
            response_cookies[cookie.name] = cookie.value
    except AttributeError:
        for cookie in s.cookies:
            if hasattr(cookie, 'name'):
                response_cookies[cookie.name] = cookie.value
            elif isinstance(cookie, str) and '=' in cookie:
                k, v = cookie.split('=', 1)
                response_cookies[k.strip()] = v.strip()

    cookie_parts = []
    cookie_parts.append(f"PDDAccessToken={pdda}")
    cookie_parts.append(f"pdd_user_id={uid}")
    cookie_parts.append(f"pdd_user_uin={uin}")
    if acid:
        cookie_parts.append(f"acid={acid}")
    # API_UID from response
    api_uid = response_cookies.get("api_uid", "")
    if api_uid:
        cookie_parts.append(f"api_uid={api_uid}")
    # 补充其他 response cookies
    for k, v in response_cookies.items():
        if k not in ["api_uid"]:
            cookie_parts.append(f"{k}={v}")

    cookie_str = "; ".join(cookie_parts)
    log(f"  [5/6] Cookie组装完成")

    return cookie_str, str(uid), uin


# ===== 果园任务 API =====
def make_manor_headers(pdduid, cookie_str):
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://mobile.yangkeduo.com",
        "Referer": "https://mobile.yangkeduo.com/garden_index_lz_0.html",
    }
    cookies = cookie_str_to_dict(cookie_str)
    return headers, cookies


def get_water(pdduid, cookie_str):
    """查询当前水滴数量"""
    url = f"{MANOR_BASE}/manor-gateway/manor/query/user/water"
    headers, cookies = make_manor_headers(pdduid, cookie_str)
    s = make_session()
    try:
        r = s.post(
            f"{url}?pdduid={pdduid}&is_back=1",
            headers=headers, cookies=cookies, json={}, timeout=15
        )
        return r.json().get("water_amount", 0)
    except Exception:
        return 0


def water_tree(pdduid, cookie_str, tubetoken, max_times=50):
    """浇水: 每次消耗10水滴"""
    water = get_water(pdduid, cookie_str)
    log(f"  [浇水] 当前水滴: {water}")
    if water < 10:
        log(f"  [浇水] 水滴不足10颗，跳过")
        return 0

    url = f"{MANOR_BASE}/manor/water/cost"
    count = min(max_times, water // 10)
    watered = 0

    for i in range(count):
        body = {
            "atw": True,
            "location_auth": False,
            "last_stay_time": 10 + i * 4,
            "can_trigger_random_mission": False,
            "product_scene": 0,
            "minor": False,
            "ext_params": {"can_trigger201824": True},
            "mission_type": 0,
            "cost_water_amount": 10,
            "merge_cost": False,
            "fun_id": "wechat_app_home",
            "lower_end_device": False,
            "cost_water_competition_in_scene_icon": False,
            "is_small_screen": True,
            "tubetoken": tubetoken,
            "fun_pl": 2
        }
        headers, cookies = make_manor_headers(pdduid, cookie_str)
        s = make_session()
        r = s.post(
            f"{url}?pdduid={pdduid}",
            headers=headers, cookies=cookies, json=body, timeout=15
        )
        result = r.json()
        left = result.get("now_water_amount")
        if left is not None and left < water:
            water = left
            watered += 1
            log(f"  [浇水] {watered}/{count}, 剩余: {left}")
            if left < 10:
                break
            time.sleep(0.3)
        else:
            log(f"  [浇水] 水滴未扣除，停止")
            break

    final = get_water(pdduid, cookie_str)
    log(f"  [浇水] 完成! 浇水{watered}次, 剩余水滴: {final}")
    return watered


def get_home_page(pdduid, cookie_str, tubetoken):
    """获取果园首页数据, 刷新 tubetoken"""
    url = f"{MANOR_BASE}/manor-query/proxy/home/page"
    body = {
        "mission_type": 0,
        "fun_id": "wechat_app_home",
        "message_source": None,
        "page_type": "HOME_PAGE",
        "push_source_mission_type": 0,
        "fruit_config_version": "",
        "unlock_scene_version": "",
        "app_home_click_icon_type": None,
        "tubetoken": tubetoken,
        "push_act_source": None,
        "need_show_home_popup": True,
        "fun_pl": 2
    }
    headers, cookies = make_manor_headers(pdduid, cookie_str)
    s = make_session()
    r = s.post(
        f"{url}?pdduid={pdduid}",
        headers=headers, cookies=cookies, json=body, timeout=15
    )
    result = r.json()

    if result.get("error_code") == 40001:
        log(f"  [首页] 验证失败, Cookie可能已过期")
        return None, None

    new_tubetoken = result.get("tubetoken", tubetoken)
    water_amount = result.get("water_amount", 0)
    log(f"  [首页] 水滴: {water_amount}")
    return new_tubetoken, water_amount


def get_mission_list(pdduid, cookie_str, tubetoken):
    """获取任务列表"""
    log(f"  [任务] 获取任务列表...")
    url = f"{MANOR_BASE}/manor/mission/list"
    body = {
        "activity_id_list": [201015, 201036],
        "mission_types": [
            38160, 38242, 38090, 38451, 37859, 38428,
            38500, 38501, 38502, 38503, 38504, 38505,
            38600, 38601, 38700, 38701, 38800, 38900,
            37900, 37950, 38000, 38050, 38100, 38150
        ],
        "request_params": {
            "act201015EntryInfo": {},
            "act201036EntryInfo": {}
        },
        "lower_end_device": False,
        "tubetoken": tubetoken,
        "fun_pl": 2
    }
    for i in range(1, 9):
        body["request_params"]["act201015EntryInfo"][str(i)] = {"needRefresh": True}
        body["request_params"]["act201036EntryInfo"][str(i)] = {"needRefresh": True}

    headers, cookies = make_manor_headers(pdduid, cookie_str)
    s = make_session()
    r = s.post(
        f"{url}?pdduid={pdduid}",
        headers=headers, cookies=cookies, json=body, timeout=15
    )
    result = r.json()

    activity_map = result.get("activity_vo_map", {})
    tasks = []
    for act_id_str, act_data in activity_map.items():
        act_id = int(act_id_str)
        act_missions = act_data.get("mission_list", {})
        if not act_missions:
            continue
        for mission_id_str, m in act_missions.items():
            mission_id = int(mission_id_str)
            reward_info = m.get("reward_info") or []
            reward_amount = 0
            reward_type = ""
            for ri in reward_info:
                if ri.get("reward_type") == 1:
                    reward_amount = ri.get("min_reward_amount", 0)
                    reward_type = "水滴"
                    break
            if not reward_amount:
                for ri in reward_info:
                    reward_amount = ri.get("min_reward_amount", 0)
                    reward_type = f'T{ri.get("reward_type", "?")}'
                    break

            tasks.append({
                "activity_id": act_id,
                "mission_id": mission_id,
                "type": m.get("type"),
                "unified_status": m.get("unified_status"),
                "is_draw": m.get("is_draw", False),
                "is_open": m.get("is_open", False),
                "finished_count": m.get("finished_count", 0),
                "max_count": m.get("max_count", 0),
                "reward_amount": reward_amount,
                "reward_type": reward_type,
            })

    can_claim = [t for t in tasks
                 if not t["is_draw"] and t["is_open"] and t["finished_count"] >= 1]
    need_accept = [t for t in tasks
                   if not t["is_draw"] and not t["is_open"] and t["finished_count"] >= 1]

    if tasks:
        log(f"  [任务] 共{len(tasks)}个, 可领取: {len(can_claim)}, 需接受: {len(need_accept)}")
        for t in tasks:
            flag = ""
            if not t["is_draw"] and t["is_open"] and t["finished_count"] >= 1:
                flag = " [可领]"
            elif not t["is_draw"] and not t["is_open"] and t["finished_count"] >= 1:
                flag = " [需接]"
            log(f"    act={t['activity_id']} id={t['mission_id']} "
                f"draw={t['is_draw']} open={t['is_open']} "
                f"done={t['finished_count']}/{t['max_count']} "
                f"+{t['reward_amount']}{t['reward_type']}{flag}")

    return can_claim, need_accept


def accept_mission(pdduid, cookie_str, tubetoken, activity_id, mission_id):
    """接受任务"""
    url = f"{MANOR_BASE}/manor/mission/accept"
    body = {
        "mission_id": mission_id,
        "activity_id": activity_id,
        "tubetoken": tubetoken,
        "fun_pl": 2
    }
    headers, cookies = make_manor_headers(pdduid, cookie_str)
    s = make_session()
    r = s.post(
        f"{url}?pdduid={pdduid}",
        headers=headers, cookies=cookies, json=body, timeout=15
    )
    result = r.json()
    if result.get("success"):
        log(f"  [任务] 接受成功 act={activity_id} id={mission_id}")
        return True
    log(f"  [任务] 接受失败 act={activity_id} id={mission_id}: {result.get('error_msg', '')}")
    return False


def claim_mission(pdduid, cookie_str, tubetoken, activity_id, mission_id):
    """领取任务奖励"""
    url = f"{MANOR_BASE}/manor/mission/draw"
    body = {
        "mission_id": mission_id,
        "activity_id": activity_id,
        "tubetoken": tubetoken,
        "fun_pl": 2
    }
    headers, cookies = make_manor_headers(pdduid, cookie_str)
    s = make_session()
    r = s.post(
        f"{url}?pdduid={pdduid}",
        headers=headers, cookies=cookies, json=body, timeout=15
    )
    result = r.json()
    if result.get("success"):
        reward = result.get("water", result.get("reward_amount", 0))
        log(f"  [任务] 领取成功 act={activity_id} id={mission_id}: +{reward}水滴")
        return True
    log(f"  [任务] 领取失败 act={activity_id} id={mission_id}: {result.get('error_msg', '')}")
    return False


def daily_checkin(pdduid, cookie_str, tubetoken):
    """每日签到"""
    log(f"  [签到] 签到中...")
    url = f"{MANOR_BASE}/manor/common/apply/activity"
    body = {
        "type": 201811,
        "params": {"ui_id": 3, "type": 2},
        "fun_id": "wechat_app_home",
        "tubetoken": tubetoken,
        "fun_pl": 2
    }
    headers, cookies = make_manor_headers(pdduid, cookie_str)
    s = make_session()
    r = s.post(
        f"{url}?pdduid={pdduid}",
        headers=headers, cookies=cookies, json=body, timeout=15
    )
    result = r.json()
    if result.get("success"):
        log(f"  [签到] 成功!")
        return True
    log(f"  [签到] 今日已签到")
    return False



# ===== 抢水滴 =====
def get_friend_list(pdduid, cookie_str, tubetoken):
    """获取好友列表(含机器人), 用于抢水滴"""
    log(f"  [偷水] 获取好友列表...")
    url = f"{MANOR_BASE}/manor-query/friend/list/page"
    body = {
        "page_num": 1,
        "tubetoken": tubetoken,
        "fun_pl": 2
    }
    headers, cookies = make_manor_headers(pdduid, cookie_str)
    s = make_session()
    r = s.post(
        f"{url}?pdduid={pdduid}",
        headers=headers, cookies=cookies, json=body, timeout=15
    )
    result = r.json()
    friend_list = result.get("friend_list", [])
    # 只保留可以偷水的 (steal_water_status.status == 2)
    can_steal = []
    for f in friend_list:
        steal_status = f.get("steal_water_status", {})
        if steal_status.get("status") == 2:
            can_steal.append({
                "uid": f.get("uid"),
                "nickname": f.get("nickname", "未知"),
                "amount": f.get("amount", 0),
            })
    log(f"  [偷水] 可偷好友: {len(can_steal)} 人")
    for f in can_steal:
        log(f"    uid={f['uid']} {f['nickname']} 水量={f['amount']}")
    return can_steal


def get_steal_chances(pdduid, cookie_str, tubetoken):
    """获取偷水次数和机器人信息"""
    url = f"{MANOR_BASE}/manor/steal/chance/lack"
    body = {
        "tubetoken": tubetoken,
        "fun_pl": 2
    }
    headers, cookies = make_manor_headers(pdduid, cookie_str)
    s = make_session()
    r = s.post(
        f"{url}?pdduid={pdduid}",
        headers=headers, cookies=cookies, json=body, timeout=15
    )
    result = r.json()
    activity_map = result.get("activity_vo_map", {})
    steal_info = activity_map.get("201423", {})
    free_chance = steal_info.get("free_chance", 0)
    daily_free_chance = steal_info.get("daily_free_chance", 0)
    rest_chance = steal_info.get("rest_chance", 0)
    robots = steal_info.get("robots", [])
    log(f"  [偷水] 免费次数: {free_chance}, 每日总次数: {daily_free_chance}, 剩余: {rest_chance}")
    # 收集机器人 uid (也有水可偷)
    robot_uids = [(r.get("uid"), r.get("nickname", "机器人"), r.get("water", 0)) for r in robots]
    return rest_chance, robot_uids


def steal_water_from_friend(pdduid, cookie_str, tubetoken, friend_uid, dog_status):
    """对单个好友执行偷水"""
    url = f"{MANOR_BASE}/manor/steal/water"
    body = {
        "friend_uid": friend_uid,
        "steal_type": 10,
        "dog_status": dog_status,
        "tubetoken": tubetoken,
        "fun_pl": 2
    }
    headers, cookies = make_manor_headers(pdduid, cookie_str)
    s = make_session()
    r = s.post(
        f"{url}?pdduid={pdduid}",
        headers=headers, cookies=cookies, json=body, timeout=15
    )
    result = r.json()
    steal_amount = result.get("steal_amount", 0) or 0
    bitten = result.get("bitten_water", 0) or 0
    return steal_amount, bitten


def steal_from_friends(pdduid, cookie_str, tubetoken):
    """抢水滴主流程: 遍历可偷好友, 尝试不同狗位"""
    friends = get_friend_list(pdduid, cookie_str, tubetoken)
    rest_chance, robot_uids = get_steal_chances(pdduid, cookie_str, tubetoken)

    # 合并好友列表和机器人列表
    all_targets = [(f["uid"], f["nickname"], f["amount"]) for f in friends]
    all_targets.extend(robot_uids)

    if not all_targets:
        log(f"  [偷水] 没有可偷的目标")
        return

    total_stolen = 0
    steal_count = 0
    max_steals = min(rest_chance, len(all_targets)) if rest_chance > 0 else len(all_targets)

    log(f"  [偷水] 开始偷水, 最多 {max_steals} 次...")

    for target_uid, nickname, water in all_targets[:max_steals]:
        if water <= 0:
            continue

        # 随机选一个狗位, 同狗位重试(概率事件, HAR证明重试同一狗位可能从miss变成功)
        dog = random.randint(1, 3)
        stolen = 0
        for retry in range(3):  # 最多重试3次
            amount, bitten = steal_water_from_friend(pdduid, cookie_str, tubetoken, target_uid, dog)
            if amount is not None and amount > 0:
                stolen = amount
                break
            if bitten is not None and bitten > 0:
                # 被狗咬了, 同狗位重试
                time.sleep(0.15)
                continue
            # amount=None且bitten=None: miss了, 同狗位重试
            time.sleep(0.15)

        if stolen > 0:
            total_stolen += stolen
            steal_count += 1
            log(f"  [偷水] uid={target_uid} {nickname} dog={dog}: +{stolen}滴")
        else:
            log(f"  [偷水] uid={target_uid} {nickname} dog={dog}: 未偷到(重试{retry+1}次)")
        time.sleep(0.3)

    log(f"  [偷水] 完成! 共偷 {steal_count} 次, 获得 {total_stolen} 水滴")


# ===== 单账号处理 =====
def process_account(wxid, idx, total):
    """处理单个账号: 登录获取Cookie -> 执行果园任务"""
    account = mask(wxid)
    initial_water = 0
    final_water = 0
    watered = 0
    checkin_ok = False
    accepted_count = 0
    claimed_count = 0

    log(f"\n{'='*48}")
    log(f"账号 [{idx}/{total}] wxid={account}")

    try:
        cookie_str = cached_cookie(wxid)
        pdduid = ""

        if cookie_str:
            pdduid = extract_uid(cookie_str)
            if pdduid:
                cookies = cookie_str_to_dict(cookie_str)
                tubetoken = cookies.get("tubetoken", "")
                new_token, test_water = get_home_page(pdduid, cookie_str, tubetoken)
                if new_token is not None:
                    log(f"缓存Cookie有效, uid={pdduid}, 水滴={test_water}")
                else:
                    log("缓存Cookie失效, 重新登录")
                    cookie_str = ""
            else:
                cookie_str = ""

        if not cookie_str:
            result = pdd_code_login(wxid)
            if not result:
                append_notify_result(idx, account, False, "登录失败")
                log("[失败] 登录失败, 跳过此账号")
                return
            cookie_str, pdduid, pdd_uin = result
            save_cookie_cache(wxid, cookie_str)

        if not pdduid:
            pdduid = extract_uid(cookie_str)
        if not pdduid:
            append_notify_result(idx, account, False, "Cookie中无 pdd_user_id")
            log("[失败] Cookie中无 pdd_user_id")
            return

        account = mask(pdduid)
        log(f"UID: {pdduid}")

        cookies = cookie_str_to_dict(cookie_str)
        tubetoken = cookies.get("tubetoken", "")
        new_token, water = get_home_page(pdduid, cookie_str, tubetoken)
        if new_token is None:
            append_notify_result(idx, account, False, "首页加载失败/Cookie无效")
            log("[失败] 首页加载失败, Cookie 无效")
            return

        if new_token and new_token != tubetoken:
            tubetoken = new_token
            cookies["tubetoken"] = tubetoken
            cookie_str = cookie_dict_to_str(cookies)
            save_cookie_cache(wxid, cookie_str)

        initial_water = int(water or 0)
        log(f"当前水滴: {initial_water}")

        checkin_ok = daily_checkin(pdduid, cookie_str, tubetoken)
        time.sleep(1)

        watered = water_tree(pdduid, cookie_str, tubetoken, max_times=50)
        time.sleep(1)

        can_claim, need_accept = get_mission_list(pdduid, cookie_str, tubetoken)

        if need_accept:
            log(f"\n  [任务] 正在接受 {len(need_accept)} 个任务...")
            for t in need_accept:
                if accept_mission(pdduid, cookie_str, tubetoken, t["activity_id"], t["mission_id"]):
                    accepted_count += 1
                time.sleep(0.5)

        if can_claim:
            log(f"\n  [任务] 正在领取 {len(can_claim)} 个任务...")
            for t in can_claim:
                if claim_mission(pdduid, cookie_str, tubetoken, t["activity_id"], t["mission_id"]):
                    claimed_count += 1
                time.sleep(0.5)

        steal_from_friends(pdduid, cookie_str, tubetoken)

        final_water = int(get_water(pdduid, cookie_str) or 0)
        log(f"\n最终水滴: {final_water}")
        append_notify_result(
            idx, account, True, "执行成功", initial_water, final_water,
            watered, checkin_ok, accepted_count, claimed_count
        )
    except Exception as e:
        append_notify_result(idx, account, False, str(e), initial_water, final_water, watered, checkin_ok, accepted_count, claimed_count)
        log(f"[账号异常] {e}")


# ===== 直接 Cookie 模式 =====
def process_direct_cookie(cookie_str, idx=1, total=1):
    """使用已有 Cookie 直接执行任务"""
    account = f"Cookie{idx}"
    initial_water = 0
    final_water = 0
    watered = 0
    checkin_ok = False
    accepted_count = 0
    claimed_count = 0

    log("=" * 48)
    log(f"使用 Cookie 直连模式 [{idx}/{total}]")

    try:
        pdduid = extract_uid(cookie_str)
        if not pdduid:
            append_notify_result(idx, account, False, "Cookie缺少 pdd_user_id")
            log("[错误] Cookie 缺少 pdd_user_id")
            return
        account = mask(pdduid)
        log(f"UID: {pdduid}")

        cookies = cookie_str_to_dict(cookie_str)
        tubetoken = cookies.get("tubetoken", "")

        new_token, water = get_home_page(pdduid, cookie_str, tubetoken)
        if new_token is None:
            append_notify_result(idx, account, False, "Cookie无效")
            log("[失败] Cookie 无效")
            return
        if new_token:
            tubetoken = new_token

        initial_water = int(water or 0)
        log(f"当前水滴: {initial_water}")

        checkin_ok = daily_checkin(pdduid, cookie_str, tubetoken)
        time.sleep(1)

        watered = water_tree(pdduid, cookie_str, tubetoken, max_times=50)
        time.sleep(1)

        can_claim, need_accept = get_mission_list(pdduid, cookie_str, tubetoken)
        if need_accept:
            log(f"\n  [任务] 正在接受 {len(need_accept)} 个任务...")
            for t in need_accept:
                if accept_mission(pdduid, cookie_str, tubetoken, t["activity_id"], t["mission_id"]):
                    accepted_count += 1
                time.sleep(0.5)
        if can_claim:
            log(f"\n  [任务] 正在领取 {len(can_claim)} 个任务...")
            for t in can_claim:
                if claim_mission(pdduid, cookie_str, tubetoken, t["activity_id"], t["mission_id"]):
                    claimed_count += 1
                time.sleep(0.5)

        steal_from_friends(pdduid, cookie_str, tubetoken)

        final_water = int(get_water(pdduid, cookie_str) or 0)
        log(f"\n最终水滴: {final_water}")
        append_notify_result(
            idx, account, True, "执行成功", initial_water, final_water,
            watered, checkin_ok, accepted_count, claimed_count
        )
    except Exception as e:
        append_notify_result(idx, account, False, str(e), initial_water, final_water, watered, checkin_ok, accepted_count, claimed_count)
        log(f"[Cookie账号异常] {e}")


def parse_cookie_accounts(raw):
    if not raw:
        return []
    # Cookie 内部包含大量分号，不能按逗号切；这里仅支持换行或 && 分隔多账号。
    return [x.strip() for x in re.split(r"\n+|&&", raw) if x.strip()]


# ===== 入口 =====
def main():
    log("=" * 48)
    log("拼多多果园 - 自动浇水领水滴")
    log(f"HTTP: {'curl_cffi' if USE_CFFI else 'requests'}")

    try:
        if COOKIE_STR:
            cookies = parse_cookie_accounts(COOKIE_STR)
            log(f"Cookie直连模式，共 {len(cookies)} 个账号")
            for i, ck in enumerate(cookies, 1):
                process_direct_cookie(ck, i, len(cookies))
            return

        if not WX_SERVER_URL:
            log("[错误] 未配置 wx_server_url 或 WX_SERVER_URL")
            append_notify_result(1, "未配置", False, "未配置 wx_server_url 或 WX_SERVER_URL")
            return

        accounts = parse_accounts(PDD_WXID_RAW)
        if not accounts:
            log("[错误] 未配置 pdd_wxid 或 PDD_WXID")
            append_notify_result(1, "未配置", False, "未配置 pdd_wxid 或 PDD_WXID")
            return

        log(f"共 {len(accounts)} 个账号")
        for i, wxid in enumerate(accounts, 1):
            process_account(wxid, i, len(accounts))

        log("\n" + "=" * 48)
        log("全部账号处理完毕")
    finally:
        dispatch_notify()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"[异常] {e}")
        append_notify_result(1, "主程序", False, str(e))
        dispatch_notify()
        sys.exit(1)


