#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
顺丰速运自动任务 v1.4.0（mywc网关聚合推送版）

功能：自动执行顺丰速运日常积分任务、会员日活动、世界杯活动，支持多账号执行，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL   必填其一，自建授权服务器域名
   - 示例：http://127.0.0.1:8110
   - 脚本会自动拼接 /mywc
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=wxd4185d00bf7e08ac
   - 请求头：auth=账号标识

2. 账号变量：
   sf_wxid 或 SF_WXID                         推荐，顺丰速运专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：wxid_a&wxid_b&openida 或 wxid_a,wxid_b,openida
   - 兼容旧变量 sfsyUrl；如配置 sfsyUrl，则可直接使用已有 Cookie/链接凭证

3. 推送变量：
   需要同目录存在 SendNotify.py，脚本结束后会统一调用 send_push_notification。
   常用推送变量如下，配置任意一种即可：
   QYWX_KEY                                         企业微信机器人 key
   PUSH_PLUS_TOKEN                                  PushPlus token
   PUSH_KEY                                         Server 酱 key
   DD_BOT_TOKEN 或 DD_BOT_SECRET                     钉钉机器人 token/secret
   FSKEY                                            飞书机器人 key

4. 青龙任务建议：
   名称：顺丰速运自动任务
   命令：python3 顺丰.py
   定时：每天运行 1 次即可，具体时间自行调整
"""

import hashlib
import json
import os
import random
import re
import time
import threading
import importlib.util
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import unquote, urlparse, parse_qs, quote as url_encode
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

PUSH_SWITCH = os.getenv("SFSY_PUSH", "1")
SCRIPT_TITLE = "🔔 顺丰速运任务执行总结"

try:
    from SendNotify import send_push_notification
except Exception as exc:
    print(f"[警告] 导入 SendNotify.py 失败：{exc}，将退化为控制台打印")
    def send_push_notification(text, desp):
        pass

# ==================== 配置区域 ====================
ENABLE_DAILY_TASK = True         # 日常积分任务 (签到+做任务+领积分)
ENABLE_MEMBER_DAY = True         # 会员日活动 (每月26-28号自动执行)
ENABLE_DRAGON_BOAT = False       # 端午临时活动
ENABLE_WORLD_CUP = True          # 世界杯活动
CONCURRENT_NUM = 1               # 并发数量 (1~20)

TOKEN = 'wwesldfs29aniversaryvdld29'
inviteId = []
SYS_CODE = 'MCS-MIMP-CORE'

# 统一变量名称与默认端口映射
SF_WX_SERVER = (os.environ.get("wx_server_url") or os.environ.get("WX_SERVER_URL") or "").strip().rstrip("/")
SF_WX_APPID = os.getenv("SF_WX_APPID", "wxd4185d00bf7e08ac")       # 小程序 appid
SF_PUBLIC_ID = os.getenv("SF_PUBLIC_ID", "gh_f9d9fca26a50")         # 小程序原始ID
SF_OAUTH_APPID = os.getenv("SF_OAUTH_APPID", "wx0d9aa0e894066e87") # 公众号 appid
SF_OAUTH_SCENE = os.getenv("SF_OAUTH_SCENE", "692")                # 活动场景号
SF_AUTO_COOKIE = os.getenv("SF_AUTO_COOKIE", "1") == "1"            # 自动获取Cookie开关
SF_WXIDS = os.environ.get("sf_wxid") or os.environ.get("SF_WXID") or "" # 顺丰专属微信号

WORLD_CUP_ACTIVITY_CODE = 'WORLD_CUP'
WORLD_CUP_CHANNEL = '26sjbapp'
WORLD_CUP_PLATFORM = 'SFAPP'
WORLD_CUP_CITY_CODE = '021'
WORLD_CUP_TOKEN = 'wwesldfs29aniversaryvdld29'
WORLD_CUP_SYS_CODE = 'MCS-MIMP-CORE'

WORLD_CUP_AUTO_FINISH_TASK_CODES = {
    '0CD7AFA68009402DBE5BF9D3C10D0115',
    '895011E183CE4E61BBACE8A63B98596A',
}

DAILY_SKIP_TASKS = [
    '用行业模板寄件下单', '用积分兑任意礼品', '参与积分活动',
    '每月累计寄件', '完成每月任务', '去使用AI寄件',
    '去新增一个收件偏好', '设置你的顺丰ID', '去使用AI小丰寄件',
]

EXECUTE_FIRST_KEYWORDS = [
    '浏览', '查看', '点击', '去微博', '打开', '去看看', '看小丰',
]

MEMBER_DAY_SKIP_TASK_TYPES = [
    'SEND_SUCCESS', 'INVITEFRIENDS_PARTAKE_ACTIVITY', 'OPEN_SVIP',
    'OPEN_NEW_EXPRESS_CARD', 'OPEN_FAMILY_CARD', 'CHARGE_NEW_EXPRESS_CARD',
    'INTEGRAL_EXCHANGE',
]

PROXY_API_URL = os.getenv("SF_PROXY_API_URL", "")
PROXY_TYPE = os.getenv("SF_PROXY_TYPE", "socks5")
PROXY_TIMEOUT = 15
MAX_PROXY_RETRIES = 5
REQUEST_COUNT = 3
PROXY_RETRY_DELAY = 2
PROXY_CONTEXT = {'last_fetch_ts': 0}
PROXY_LOCK = threading.Lock()
print_lock = Lock()
DRAGON_BOAT_MODULE = None
DRAGON_BOAT_LOCK = threading.Lock()
GLOBAL_NOTIFY_BUFFERS: List[Dict[str, Any]] = []
AUTO_COOKIE_INDEX_BY_VALUE: Dict[str, int] = {}


class Logger:
    def __init__(self):
        self.messages: List[str] = []
        self.lock = Lock()

    def _log(self, icon: str, msg: str):
        line = f"{icon} {msg}"
        with print_lock:
            print(line)

    def info(self, msg): self._log('📝', msg)
    def success(self, msg): self._log('✅', msg)
    def warning(self, msg): self._log('⚠️', msg)
    def error(self, msg): self._log('❌', msg)
    def task(self, msg): self._log('🎯', msg)
    def medal(self, msg): self._log('🏅', msg)
    def points(self, pts, prefix="当前积分"): self._log('💰', f"{prefix}: 【{pts}】")


def _log_global(msg: str):
    t = datetime.now().strftime("%H:%M:%S")
    print(f"[{t}] {msg}", flush=True)


def parse_env_accounts(raw: str) -> List[str]:
    normalized = (raw or "").replace("，", ",").replace(",", "&").replace("\n", "&")
    return [item.strip() for item in normalized.split("&") if item.strip()]


def mask_account(value: Any) -> str:
    value = str(value or "")
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-4:]}"


def _build_proxy_url(ip: str, port: int, username: str = "", password: str = "") -> str:
    if username and password:
        safe_user = url_encode(username, safe='')
        safe_pass = url_encode(password, safe='')
        return f"{PROXY_TYPE}://{safe_user}:{safe_pass}@{ip}:{port}"
    return f"{PROXY_TYPE}://{ip}:{port}"


def parse_proxy_response(text: str) -> Optional[Tuple[str, str]]:
    text = text.strip()
    try:
        data = json.loads(text)
        def extract(d: dict) -> Optional[Tuple[str, str]]:
            if 'ip' not in d or 'port' not in d:
                return None
            ip, port = str(d['ip']), int(d['port'])
            user = str(d.get('account', d.get('user', '')) or '')
            pwd = str(d.get('password', d.get('pass', '')) or '')
            url = _build_proxy_url(ip, port, user, pwd)
            display = f"{ip}:{port}" + (" (认证)" if user else "")
            return url, display
        if isinstance(data, dict):
            if 'ip' in data and 'port' in data:
                return extract(data)
            if 'data' in data:
                pd = data['data']
                if isinstance(pd, dict) and 'list' in pd:
                    pl = pd['list']
                    if isinstance(pl, list) and pl:
                        return extract(pl[0])
                if isinstance(pd, list) and pd:
                    return extract(pd[0])
                if isinstance(pd, dict) and 'ip' in pd:
                    return extract(pd)
            if 'result' in data:
                r = data['result']
                if isinstance(r, dict) and 'ip' in r:
                    return extract(r)
    except (json.JSONDecodeError, ValueError):
        pass
    if ':' in text:
        segments = text.split()
        addr_parts = segments[0].split(':')
        if len(addr_parts) == 2 and addr_parts[1].isdigit():
            ip, port = addr_parts[0], int(addr_parts[1])
            user = segments[1] if len(segments) > 1 else ""
            pwd = segments[2] if len(segments) > 2 else ""
            url = _build_proxy_url(ip, port, user, pwd)
            display = f"{ip}:{port}" + (" (认证)" if user else "")
            return url, display
    return None


def get_api_proxy() -> Optional[Tuple[Dict[str, str], str]]:
    if not PROXY_API_URL:
        return None
    with PROXY_LOCK:
        elapsed = time.time() - PROXY_CONTEXT['last_fetch_ts']
        if elapsed < 3:
            time.sleep(3 - elapsed)
        for i in range(MAX_PROXY_RETRIES):
            try:
                resp = requests.get(PROXY_API_URL, timeout=10)
                if resp.status_code == 200:
                    result = parse_proxy_response(resp.text)
                    if result:
                        proxy_url, display = result
                        PROXY_CONTEXT['last_fetch_ts'] = time.time()
                        _log_global(f"✅ 代理获取成功: {display}")
                        return {'http': proxy_url, 'https': proxy_url}, display
                _log_global(f"⚠️ 第{i+1}次代理格式无效")
            except Exception as e:
                _log_global(f"⚠️ 第{i+1}次获取代理异常: {str(e)[:80]}")
            if i < MAX_PROXY_RETRIES - 1:
                time.sleep(PROXY_RETRY_DELAY)
        PROXY_CONTEXT['last_fetch_ts'] = time.time()
        _log_global(f"❌ 代理获取失败：已重试{MAX_PROXY_RETRIES}次")
        return None


def parse_fixed_proxy(fixed_proxy: str) -> Optional[Dict[str, str]]:
    if not fixed_proxy:
        return None
    if '://' not in fixed_proxy:
        fixed_proxy = f'{PROXY_TYPE}://{fixed_proxy}'
    return {'http': fixed_proxy, 'https': fixed_proxy}


# ==================== AutoCookieManager ====================
UCMP_BASE = "https://ucmp.sf-express.com"

class AutoCookieManager:
    def __init__(self, wx_server: str = None):
        self.wx_server = (wx_server or SF_WX_SERVER).strip().rstrip("/")
        self.session = requests.Session()
        self.session.verify = False
    
    def _get_online_accounts(self) -> List[Dict]:
        if not self.wx_server:
            _log_global("❌ 未配置 wx_server_url 或 WX_SERVER_URL，无法获取在线账号")
            return []
        try:
            r = self.session.get(f"{self.wx_server}/api/v1/wx/user/status", timeout=10)
            j = r.json()
            data = j.get("Data") or j.get("data") or {}
            accounts = []
            for k, v in data.items():
                if isinstance(v, dict) and v.get("wxid") and v.get("survival") == 1:
                    accounts.append(v)
            return accounts
        except Exception as e:
            _log_global(f"❌ 获取在线账号失败: {e}")
            return []
    
    def _get_wx_code(self, wxid: str, appid: str = None, max_retries: int = 3) -> Optional[str]:
        if not self.wx_server:
            _log_global("❌ 未配置 wx_server_url 或 WX_SERVER_URL，无法请求 /mywc")
            return None
        target_appid = appid or SF_WX_APPID
        url = f"{self.wx_server}/mywc"
        
        for attempt in range(max_retries):
            try:
                params = {"wxid": wxid, "appId": target_appid}
                headers = {"auth": wxid, "User-Agent": "Mozilla/5.0 MicroMessenger/8.0.50"}
                
                r = self.session.get(url, params=params, headers=headers, timeout=30)
                j = r.json()
                
                if j.get("status") == "ok" and j.get("code"):
                    return str(j["code"])
                
                data = j.get("Data") or j.get("data") or {}
                code = data.get("code") or j.get("code") or ""
                if not code:
                    if attempt < max_retries - 1:
                        wait = (attempt + 1) * 3
                        _log_global(f"⚠️ {wxid[:12]}***: code为空，{wait}s后重试({attempt+1}/{max_retries})")
                        time.sleep(wait)
                        continue
                    _log_global(f"❌ {wxid[:12]}***: 获取code失败 appid={target_appid} resp={str(j)[:120]}")
                    return None
                return str(code)
            except Exception as e:
                if attempt < max_retries - 1:
                    wait = (attempt + 1) * 3
                    _log_global(f"⚠️ {wxid[:12]}***: code异常 {str(e)[:60]}，{wait}s后重试({attempt+1}/{max_retries})")
                    time.sleep(wait)
                    continue
                _log_global(f"❌ {wxid[:12]}***: 获取code异常 appid={target_appid} err={str(e)[:80]}")
                return None
        return None

    def _ucmp_app_on_login(self, code: str) -> Optional[Dict]:
        try:
            url = f"{UCMP_BASE}/wxaccess/weixin/appOnLogin"
            r = self.session.get(url, params={"code": code, "publicId": SF_PUBLIC_ID}, timeout=25)
            j = r.json()
            if j.get("sessionId") and j.get("openid"):
                return j
            return None
        except Exception:
            return None

    def _get_oauth_redirect_info(self, ucmp_sid: str) -> Tuple[Optional[str], Optional[str]]:
        try:
            s = requests.Session()
            s.verify = False
            s.headers.update({
                "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 MicroMessenger/8.0.50",
                "Accept": "text/html,*/*",
                "Cookie": f"suuid={ucmp_sid}",
            })
            r = s.get(f"{UCMP_BASE}/wxaccess/weixin/activity/sfmemfe?p1={SF_OAUTH_SCENE}", allow_redirects=False, timeout=25)
            oauth_url = r.headers.get("Location", "")
            if not oauth_url: return None, None
            parsed = urlparse(oauth_url)
            qs = parse_qs(parsed.query)
            redirect_uri = unquote(qs.get("redirect_uri", [""])[0])
            state = qs.get("state", [""])[0]
            return redirect_uri, state
        except Exception: return None, None
    
    def get_cookie_for_wxid(self, wxid: str) -> Optional[str]:
        code = self._get_wx_code(wxid, SF_WX_APPID)
        if not code: return None
        ucmp = self._ucmp_app_on_login(code)
        if not ucmp: return None
        ucmp_sid = ucmp.get("sessionId", "")
        
        redirect_uri, state = self._get_oauth_redirect_info(ucmp_sid)
        if not redirect_uri or not state: return None
        
        oauth_code = self._get_wx_code(wxid, SF_OAUTH_APPID)
        if not oauth_code: return None
        
        try:
            callback_url = f"{redirect_uri}&code={oauth_code}&state={url_encode(state)}"
            s2 = requests.Session()
            s2.verify = False
            s2.headers.update({
                "User-Agent": "Mozilla/5.0 MicroMessenger/8.0.50",
                "Accept": "text/html,*/*",
                "Referer": "https://open.weixin.qq.com/",
            })
            s2.get(callback_url, allow_redirects=True, timeout=25)
            cookies = {}
            for c in s2.cookies:
                if "mcs-mimp" in c.domain or "sf-express" in c.domain:
                    cookies[c.name] = c.value
            
            session_id = cookies.get("sessionId", "")
            login_mobile = cookies.get("_login_mobile_", "")
            login_user_id = cookies.get("_login_user_id_", "")
            
            if session_id and (not login_mobile or not login_user_id):
                try:
                    s2.headers.update({"Cookie": f"sessionId={session_id}"})
                    s2.get("https://mcs-mimp-web.sf-express.com/mcs-mimp/app/index.html", allow_redirects=True, timeout=15)
                    for c in s2.cookies:
                        if "mcs-mimp" in c.domain or "sf-express" in c.domain:
                            cookies[c.name] = c.value
                    login_mobile = cookies.get("_login_mobile_", "")
                    login_user_id = cookies.get("_login_user_id_", "")
                except: pass
            
            if not session_id or not login_mobile or not login_user_id:
                return None
            
            parts = [f"sessionId={session_id}", f"_login_mobile_={login_mobile}", f"_login_user_id_={login_user_id}"]
            for k in ["HWWAFSESTIME", "HWWAFSESID", "JSESSIONID"]:
                if k in cookies: parts.append(f"{k}={cookies[k]}")
            
            cookie_str = ";".join(parts)
            _log_global(f"✅ {wxid[:10]}*** 自动获取凭证换绑成功 ➔ 手机: {login_mobile}")
            return cookie_str
        except Exception: return None
    
    def get_cookies_for_wxids(self, wxids: List[str] = None) -> Dict[str, str]:
        if not wxids:
            accounts = self._get_online_accounts()
            wxids = [a["wxid"] for a in accounts]
        
        results = {}
        for i, wxid in enumerate(wxids):
            try:
                cookie = self.get_cookie_for_wxid(wxid)
                if cookie: results[wxid] = cookie
            except Exception: pass
            if i < len(wxids) - 1: time.sleep(2)
        return results


# ==================== HTTP 客户端 ====================
class SFHttpClient:
    def __init__(self, fixed_proxy: str = ""):
        self.session = requests.Session()
        self.session.verify = False
        self.proxy_display = '无代理'
        self._setup_proxy(fixed_proxy)
        self.headers = {
            'Host': 'mcs-mimp-web.sf-express.com',
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf254173b) XWEB/19027',
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json',
            'channel': 'xcxpart',
            'platform': 'MINI_PROGRAM',
            'accept-language': 'zh-CN,zh;q=0.9',
        }

    def _setup_proxy(self, fixed_proxy: str):
        if fixed_proxy:
            proxy_dict = parse_fixed_proxy(fixed_proxy)
            if proxy_dict:
                self.session.proxies = proxy_dict
                display = fixed_proxy
                if '@' in fixed_proxy:
                    parts = fixed_proxy.split('@')
                    display = f"***@{parts[-1]}"
                self.proxy_display = display
                return
        result = get_api_proxy()
        if result:
            self.session.proxies = result[0]
            self.proxy_display = result[1]

    def _generate_sign(self) -> Dict[str, str]:
        timestamp = str(int(round(time.time() * 1000)))
        data = f'token={TOKEN}&timestamp={timestamp}&sysCode={SYS_CODE}'
        signature = hashlib.md5(data.encode()).hexdigest()
        return {'syscode': SYS_CODE, 'timestamp': timestamp, 'signature': signature}

    def request(self, url: str, data: Optional[Dict] = None, extra_headers: Optional[Dict[str, str]] = None) -> Optional[Dict]:
        proxy_retry_count = 0
        retry_count = 0
        while proxy_retry_count < MAX_PROXY_RETRIES:
            sign_data = self._generate_sign()
            headers = {**self.headers, **sign_data}
            if extra_headers: headers.update(extra_headers)
            try:
                resp = self.session.post(url, headers=headers, json=data or {}, timeout=PROXY_TIMEOUT)
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.RequestException as e:
                retry_count += 1
                error_str = str(e)
                if 'ProxyError' in error_str or 'SSLError' in error_str or 'ConnectionError' in error_str:
                    proxy_retry_count += 1
                    if proxy_retry_count < MAX_PROXY_RETRIES:
                        result = get_api_proxy()
                        if result:
                            self.session.proxies = result[0]
                            self.proxy_display = result[1]
                        retry_count = 0
                    time.sleep(2)
                    continue
                if retry_count < REQUEST_COUNT:
                    time.sleep(2)
                    continue
                return None
            except Exception: return None
        return None

    def request_app(self, url: str, data: Optional[Dict] = None) -> Optional[Dict]:
        original = self.headers.get('platform', 'MINI_PROGRAM')
        self.headers['platform'] = 'SFAPP'
        try: return self.request(url, data)
        finally: self.headers['platform'] = original

    def login(self, url: str) -> Tuple[bool, str, str]:
        try:
            decoded = unquote(url)
            if decoded.startswith('sessionId=') or '_login_mobile_=' in decoded:
                cookie_dict = {}
                for item in decoded.split(';'):
                    item = item.strip()
                    if '=' in item:
                        k, v = item.split('=', 1)
                        cookie_dict[k] = v
                for k, v in cookie_dict.items():
                    self.session.cookies.set(k, v, domain='mcs-mimp-web.sf-express.com')
                user_id = cookie_dict.get('_login_user_id_', '')
                phone = cookie_dict.get('_login_mobile_', '')
                return (True, user_id, phone) if phone else (False, '', '')
            else:
                self.session.get(decoded, headers=self.headers, timeout=PROXY_TIMEOUT)
                cookies = self.session.cookies.get_dict()
                user_id = cookies.get('_login_user_id_', '')
                phone = cookies.get('_login_mobile_', '')
                return (True, user_id, phone) if phone else (False, '', '')
        except Exception: return False, '', ''


# ==================== 日常积分任务执行器 ====================
class DailyTaskExecutor:
    def __init__(self, http: SFHttpClient, logger: Logger, user_id: str):
        self.http = http
        self.logger = logger
        self.user_id = user_id
        self.total_points = 0
        self.taskId = ""
        self.taskCode = ""
        self.strategyId = 0
        self.title = ""
        self.point = 0

    @staticmethod
    def generate_device_id() -> str:
        result = ''
        for char in 'xxxxxxxx-xxxx-xxxx':
            result += random.choice('abcdef0123456789') if char == 'x' else char
        return result

    def _extract_task_id_from_url(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            if '_ug_view_param' in params:
                ug_params = json.loads(unquote(params['_ug_view_param'][0]))
                if 'taskId' in ug_params: return str(ug_params['taskId'])
        except Exception: pass
        return ''

    def _set_task_attrs(self, task: Dict):
        self.taskId = str(task.get('taskId', ''))
        self.taskCode = str(task.get('taskCode', ''))
        self.strategyId = int(task.get('strategyId', 0))
        self.title = str(task.get('title', '未知任务'))
        self.point = int(task.get('point', 0))

    def app_sign_in(self) -> Tuple[bool, str]:
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~integralTaskSignPlusService~getUnFetchPointAndDiscount'
        resp = self.http.request_app(url, {})
        if resp and resp.get('success'): return True, ''
        return False, '未完成'

    def sign_in(self) -> Tuple[bool, str]:
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~integralTaskSignPlusService~automaticSignFetchPackage'
        resp = self.http.request(url, {"comeFrom": "vioin", "channelFrom": "WEIXIN"})
        if resp and resp.get('success'):
            obj = resp.get('obj', {})
            packets = obj.get('integralTaskSignPackageVOList', [])
            if packets: self.logger.success(f'小程序签到成功: 【{packets[0].get("packetName")}】')
            return True, ''
        return False, '失败'

    def _sign_v2(self, platform_type: str) -> bool:
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~integralSignV2Service~sign'
        headers = {'platform': platform_type, 'deviceid': self.generate_device_id()}
        resp = self.http.request(url, {}, headers)
        if resp and resp.get('success'): return True
        return False

    def dual_sign_in(self) -> bool:
        self._sign_v2('SFAPP')
        time.sleep(1)
        self._sign_v2('MINI_PROGRAM')
        return True

    # ── 痛点修复：修正字典结构解析层级，解决 KeyError 死结 ──
    def get_task_list(self) -> List[Dict]:
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~integralTaskStrategyService~queryPointTaskAndSignFromES'
        all_tasks = []
        seen = set()
        for ct in ['1', '2']:
            resp = self.http.request(url, {'channelType': ct, 'deviceId': self.generate_device_id()})
            if resp and resp.get('success') and resp.get('obj'):
                obj = resp['obj']
                self.total_points = obj.get('totalPoint', 0)
                
                # 阶梯式安全抓取嵌套内部的核心任务链
                task_items = obj.get('ESobj') or obj.get('taskTitleLevels') or []
                for task in task_items:
                    tc = task.get('taskCode', '')
                    if tc and tc not in seen:
                        seen.add(tc)
                        all_tasks.append(task)
        return all_tasks

    def execute_task(self) -> bool:
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonRoutePost/memberEs/taskRecord/finishTask'
        resp = self.http.request(url, {'taskCode': self.taskCode})
        return bool(resp and resp.get('success'))

    def receive_task_reward(self) -> bool:
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~integralTaskStrategyService~fetchIntegral'
        data = {"strategyId": self.strategyId, "taskId": self.taskId, "taskCode": self.taskCode, "deviceId": self.generate_device_id()}
        resp = self.http.request(url, data)
        if resp and resp.get('success'):
            self.logger.success(f'日常任务奖励领取成功 ➔ {self.title}')
            return True
        return False

    def run(self) -> Tuple[int, int]:
        tasks = self.get_task_list()
        if not tasks: return 0, 0
        points_before = self.total_points
        for task in tasks:
            title = task.get('title', '未知')
            status = task.get('status')
            if status == 3 or title in DAILY_SKIP_TASKS: continue
            self._set_task_attrs(task)
            if not self.taskCode: continue
            
            if status == 1:
                if self.execute_task():
                    time.sleep(2)
                    status = 2
            if status == 2:
                need_execute = any(kw in title for kw in EXECUTE_FIRST_KEYWORDS)
                if need_execute:
                    self.execute_task()
                    time.sleep(2)
                self.receive_task_reward()
            time.sleep(3)
        self.get_task_list()
        return points_before, self.total_points


# ==================== 会员日活动执行器 ====================
class MemberDayExecutor:
    MAX_LEVEL = 8
    def __init__(self, http: SFHttpClient, logger: Logger, user_id: str):
        self.http = http
        self.logger = logger
        self.user_id = user_id
        self.black = False
        self.red_packet_map: Dict[int, int] = {}

    def get_index(self) -> Optional[Dict]:
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~memberDayIndexService~index'
        resp = self.http.request(url, {'inviteUserId': ''})
        return resp.get('obj', {}) if resp and resp.get('success') else None

    def lottery(self) -> Optional[str]:
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~memberDayLotteryService~lottery'
        resp = self.http.request(url, {})
        if resp and resp.get('success'):
            name = resp.get('obj', {}).get('productName', '未抽中')
            self.logger.success(f'会员日抽奖成功 ➔ 获得: {name}')
            return name
        return None

    def run(self) -> Dict[str, Any]:
        result = {'lottery_prizes': []}
        index_info = self.get_index()
        if not index_info: return result
        lottery_num = index_info.get('lotteryNum', 0)
        for _ in range(lottery_num):
            prize = self.lottery()
            if prize: result['lottery_prizes'].append(prize)
        return result


# ==================== 世界杯活动执行器 ====================
class WorldCupExecutor:
    BASE_URL = 'https://mcs-mimp-web.sf-express.com/mcs-mimp'
    def __init__(self, http: SFHttpClient, logger: Logger, user_id: str):
        self.http = http
        self.logger = logger
        self.user_id = user_id

    def _post(self, path: str, data: Optional[Dict] = None) -> Optional[Dict]:
        return self.http.request(f'{self.BASE_URL}{path}', data=data or {})

    def run(self) -> Dict[str, Any]:
        result = {'tasks_completed': 0, 'stages_passed': [], 'daily_gift': False, 'final_balance': 0}
        idx = self._post('/commonPost/~memberNonactivity~worldCupIndexService~index')
        if not idx or not idx.get('success'): return result
        
        gift = self._post('/commonPost/~memberNonactivity~worldCupDailyService~getDailyGiftStatus', {"cityCode": WORLD_CUP_CITY_CODE})
        if gift and gift.get('obj', {}).get('canReceive') and not gift.get('obj', {}).get('received'):
            self._post('/commonPost/~memberNonactivity~worldCupDailyService~receiveDailyGift', {"cityCode": WORLD_CUP_CITY_CODE})
            result['daily_gift'] = True
            self.logger.success("世界杯每日特权礼物领取成功")
            
        for code in WORLD_CUP_AUTO_FINISH_TASK_CODES:
            res = self._post('/commonPost/~memberEs~taskRecord~finishTask', {"taskCode": code})
            if res and res.get('success'): result['tasks_completed'] += 1
        
        self._post('/commonPost/~memberNonactivity~worldCupTaskService~fetchTaskReward', {"channelType": WORLD_CUP_PLATFORM, "activityCode": WORLD_CUP_ACTIVITY_CODE})
        
        game = self._post('/commonPost/~memberNonactivity~worldCupGameService~index')
        if game and game.get('success'):
            lv_list = game.get('obj', {}).get('levelList', [])
            cur_lv = game.get('obj', {}).get('curLevel', 1)
            for cfg in lv_list:
                no = cfg.get('level')
                if no == cur_lv:
                    p_res = self._post('/commonPost/~memberNonactivity~worldCupGameService~passReport', {"level": no, "shotNum": cfg.get('target', 0)})
                    if p_res and p_res.get('success'):
                        result['stages_passed'].append(no)
                        self.logger.success(f"世界杯射门游戏突破第 {no} 关")
                        
        bet = self._post('/commonPost/~memberNonactivity~worldCupMatchService~betStatus')
        if bet: result['final_balance'] = bet.get('obj', {}).get('currentAccount', {}).get('balance', 0)
        return result


# ==================== 核心处理器 ====================
def run_account(account_raw: str, index: int) -> Dict[str, Any]:
    logger = Logger()
    fixed_proxy = account_raw.split('#')[-1].strip() if '#' in account_raw else ""
    account_url = account_raw.split('#')[0].strip() if '#' in account_raw else account_raw
    
    http = SFHttpClient(fixed_proxy)
    success, user_id, phone = http.login(account_url)
    if not success:
        return {'success': False, 'phone': '未登录账号'}
        
    masked = phone[:3] + "****" + phone[7:] if len(phone) >= 7 else phone
    logger.success(f"账号 [{index + 1}] ➔ 【{masked}】激活认证成功")
    
    result = {'success': True, 'phone': masked, 'index': index, 'points_earned': 0, 'member_day_prizes': [], 'world_cup': None}
    
    if ENABLE_DAILY_TASK:
        daily = DailyTaskExecutor(http, logger, user_id)
        daily.dual_sign_in()
        daily.sign_in()
        pb, pa = daily.run()
        result['points_earned'] = pa - pb
        
    if ENABLE_MEMBER_DAY and 26 <= datetime.now().day <= 28:
        md = MemberDayExecutor(http, logger, user_id)
        result['member_day_prizes'] = md.run().get('lottery_prizes', [])
        
    if ENABLE_WORLD_CUP and datetime.now() <= datetime(2026, 7, 20, 23, 59, 59):
        wc = WorldCupExecutor(http, logger, user_id)
        result['world_cup'] = wc.run()
        
    return result


def _auto_fetch_cookies() -> List[str]:
    mgr = AutoCookieManager()
    wxids = parse_env_accounts(SF_WXIDS) if SF_WXIDS else [a["wxid"] for a in mgr._get_online_accounts()]
    if not wxids:
        return []

    _log_global(f"🔎 顺丰 wxid 解析到 {len(wxids)} 个账号")
    cookies: List[str] = []
    AUTO_COOKIE_INDEX_BY_VALUE.clear()

    for index, wxid in enumerate(wxids, 1):
        try:
            cookie = mgr.get_cookie_for_wxid(wxid)
        except Exception as exc:
            cookie = None
            _log_global(f"❌ 账号[{index}] {mask_account(wxid)} 自动换 Cookie 异常：{str(exc)[:80]}")

        if cookie and "_login_mobile_" in cookie:
            cookies.append(cookie)
            AUTO_COOKIE_INDEX_BY_VALUE[cookie] = index
            _log_global(f"✅ 账号[{index}] {mask_account(wxid)} 自动换 Cookie 成功")
            continue

        _log_global(f"❌ 账号[{index}] {mask_account(wxid)} 自动换 Cookie 失败，已计入失败通知")
        GLOBAL_NOTIFY_BUFFERS.append({
            "index": index,
            "account": mask_account(wxid),
            "ok": False,
            "points": 0,
            "member_day_prizes": [],
            "world_cup": {},
            "message": "自动换取顺丰 Cookie 失败，请检查该微信是否在线、是否已授权顺丰、是否绑定手机号",
        })
        if index < len(wxids):
            time.sleep(2)

    _log_global(f"📦 顺丰 Cookie 换取成功 {len(cookies)} / 解析账号 {len(wxids)}")
    return cookies


def append_notify_result(index: int, result: Dict[str, Any]) -> None:
    GLOBAL_NOTIFY_BUFFERS.append({
        "index": index,
        "account": result.get("phone") or "未知账号",
        "ok": bool(result.get("success")),
        "points": int(result.get("points_earned") or 0),
        "member_day_prizes": result.get("member_day_prizes") or [],
        "world_cup": result.get("world_cup") or {},
        "message": result.get("error") or "登录失效",
    })


def build_notify_report() -> str:
    total = len(GLOBAL_NOTIFY_BUFFERS)
    success = sum(1 for item in GLOBAL_NOTIFY_BUFFERS if item.get("ok"))
    failed = total - success
    total_earned = sum(int(item.get("points") or 0) for item in GLOBAL_NOTIFY_BUFFERS if item.get("ok"))

    lines = [
        "==============================",
        f"🕒 执行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"📊 统计数据：成功 {success} / 总计 {total}",
        f"✅ 成功账号：{success} 个",
        f"❌ 失败账号：{failed} 个",
        f"💰 累计积分：+{total_earned}",
        "==============================",
    ]

    for item in GLOBAL_NOTIFY_BUFFERS:
        ok = bool(item.get("ok"))
        account_icon = "🧑‍💻" if ok else "🧟"
        lines.extend([
            f"{account_icon} 【账号{item.get('index')}】{item.get('account')}",
            f"{'✅' if ok else '❌'} 状态：{'执行成功' if ok else '执行失败'}",
        ])

        if ok:
            lines.append(f"💰 积分：+{item.get('points')}")
            prizes = item.get("member_day_prizes") or []
            if prizes:
                lines.append(f"🎁 会员日：{', '.join(str(p) for p in prizes)}")
            wc = item.get("world_cup") or {}
            wc_parts = []
            if wc.get("daily_gift"):
                wc_parts.append("礼物已领✅")
            if wc.get("tasks_completed"):
                wc_parts.append(f"任务+{wc.get('tasks_completed')}")
            if wc.get("stages_passed"):
                wc_parts.append(f"通关+{len(wc.get('stages_passed') or [])}关")
            if wc.get("final_balance"):
                wc_parts.append(f"金币:{wc.get('final_balance')}")
            if wc_parts:
                lines.append(f"⚽ 世界杯：{' '.join(wc_parts)}")
        else:
            lines.append(f"🧨 原因：{item.get('message')}")

        lines.append("------------------------------")

    return "\n".join(lines)


def dispatch_notify() -> None:
    if PUSH_SWITCH != "1" or not GLOBAL_NOTIFY_BUFFERS:
        return
    final_desp = build_notify_report()
    print("\n[聚合推送报表阅览]\n" + final_desp)
    try:
        send_push_notification(SCRIPT_TITLE, final_desp)
    except Exception as exc:
        print(f"[通知] 推送失败：{exc}")


def main():
    env_value = os.getenv('sfsyUrl') or ""
    account_list = _auto_fetch_cookies() if (not env_value or SF_AUTO_COOKIE) else parse_env_accounts(env_value)
    
    if not account_list:
        print("❌ 未捕获到在线顺丰账号凭证，请检查 wx_server_url / SF_WXID / sf_wxid / sfsyUrl")
        GLOBAL_NOTIFY_BUFFERS.append({
            "index": 0,
            "account": "未配置",
            "ok": False,
            "points": 0,
            "member_day_prizes": [],
            "world_cup": {},
            "message": "未捕获到在线顺丰账号凭证",
        })
        dispatch_notify()
        return 1

    print("==================================================")
    print(f"🎉 顺丰速运 mywc 网关聚合推送版启动... 共加载 {len(account_list)} 个账户")
    print("==================================================")
    
    ok_count = 0
    for idx, raw in enumerate(account_list):
        result = run_account(raw, idx)
        append_notify_result(AUTO_COOKIE_INDEX_BY_VALUE.get(raw, idx + 1), result)
        if result.get('success'):
            ok_count += 1
        time.sleep(2)

    dispatch_notify()
    total_failed = sum(1 for item in GLOBAL_NOTIFY_BUFFERS if not item.get("ok"))
    return 0 if total_failed == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())