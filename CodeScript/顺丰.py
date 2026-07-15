#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
顺丰速运自动任务 v1.5.0（mywc网关聚合推送版）

功能：自动换取顺丰业务 Cookie，执行日常签到、积分任务及会员日活动，支持多账号并在结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL   微信账号模式必填其一，自建授权服务器地址
   - 脚本自动请求：GET {网关}/mywc?wxid=账号标识&appId=wxd4185d00bf7e08ac
   - 请求头：auth=账号标识

2. 账号变量：
   sf_wxid 或 SF_WXID              推荐，顺丰速运专属微信账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   sfsyUrl                          可选，兼容已有顺丰 Cookie 或链接凭证

3. 推送配置：
   需要同目录存在 SendNotify.py，脚本结束后统一调用 send_push_notification。

4. 可选代理：
   SF_PROXY_API_URL                 代理提取接口
   SF_PROXY_TYPE                    代理类型，默认 socks5

依赖：requests
青龙任务建议：task sf.py
"""

import hashlib
import json
import os
import sys
import random
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import unquote, urlparse, parse_qs, quote as url_encode
from threading import Lock
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

PUSH_SWITCH = os.getenv("SFSY_PUSH", "1")
SCRIPT_TITLE = "🔔 顺丰速运任务执行总结"

SEND_NOTIFY_AVAILABLE = True
SEND_NOTIFY_IMPORT_ERROR = ""
try:
    from SendNotify import send_push_notification
except Exception as exc:
    SEND_NOTIFY_AVAILABLE = False
    SEND_NOTIFY_IMPORT_ERROR = str(exc)
    send_push_notification = None
    print(f"[警告] 导入 SendNotify.py 失败：{exc}")


# ==================== 配置区域 ====================
ENABLE_DAILY_TASK = True         # 日常积分任务 (签到+做任务+领积分)
ENABLE_MEMBER_DAY = True         # 会员日活动 (每月26-28号自动执行)
CONCURRENT_NUM = 1               # 并发数量 (1~20)

TOKEN = 'wwesldfs29aniversaryvdld29'
inviteId = []
SYS_CODE = 'MCS-MIMP-CORE'

SF_WX_SERVER = (
    os.getenv("wx_server_url")
    or os.getenv("WX_SERVER_URL")
    or os.getenv("wechat_server")
    or os.getenv("WECHAT_SERVER")
    or ""
).strip().rstrip("/")
SF_WX_APPID = os.getenv("SF_WX_APPID", "wxd4185d00bf7e08ac")
SF_PUBLIC_ID = os.getenv("SF_PUBLIC_ID", "gh_f9d9fca26a50")
SF_OAUTH_APPID = os.getenv("SF_OAUTH_APPID", "wx0d9aa0e894066e87")
SF_OAUTH_SCENE = os.getenv("SF_OAUTH_SCENE", "692")
SF_AUTO_COOKIE = os.getenv("SF_AUTO_COOKIE", "1") == "1"
SF_WXIDS = (
    os.getenv("sf_wxid")
    or os.getenv("SF_WXID")
    or os.getenv("sfwx_openid")
    or os.getenv("SFWX_OPENID")
    or ""
)

DAILY_SKIP_TASKS = [
    '用行业模板寄件下单', '用积分兑任意礼品', '参与积分活动',
    '每月累计寄件', '完成每月任务', '去使用AI寄件',
    '去新增一个收件偏好', '设置你的顺丰ID', '去使用AI小丰寄件',
    '寄一单国际件',  # 需真实寄件，无法自动完成
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
PROXY_LOCK = Lock()
print_lock = Lock()
GLOBAL_NOTIFY_BUFFERS: List[Dict[str, Any]] = []
AUTO_COOKIE_INDEX_BY_VALUE: Dict[str, int] = {}


class Logger:
    def __init__(self):
        pass

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
    line = f"[{t}] {msg}"
    try:
        print(line, flush=True)
    except UnicodeEncodeError:
        # Windows 控制台默认 GBK 时，降级去掉无法编码字符，避免影响主流程
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(line.encode(encoding, errors="ignore").decode(encoding, errors="ignore"), flush=True)


def parse_env_accounts(raw: str) -> List[str]:
    return [item.strip() for item in re.split(r"[&,，\r\n]+", str(raw or "")) if item.strip()]


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
    
    def _get_wx_code(self, wxid: str, appid: str = None, max_retries: int = 3) -> Optional[str]:
        """通过标准 mywc 网关获取微信小程序 Code。"""
        if not self.wx_server:
            _log_global("❌ 未配置 wx_server_url 或 WX_SERVER_URL，无法获取微信 Code")
            return None

        target_appid = appid or SF_WX_APPID
        url = f"{self.wx_server}/mywc"
        last_error = "未知错误"

        for attempt in range(1, max_retries + 1):
            try:
                response = self.session.get(
                    url,
                    params={"wxid": wxid, "appId": target_appid},
                    headers={"auth": wxid},
                    timeout=30,
                )
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                code_value = data.get("code") if isinstance(data, dict) else data
                code_value = code_value or (payload.get("code") if isinstance(payload, dict) else None)

                if isinstance(code_value, str) and code_value.strip():
                    _log_global(f"✅ {mask_account(wxid)} 获取微信 Code 成功")
                    return code_value.strip()
                last_error = str(payload)[:160]
            except Exception as exc:
                last_error = str(exc)[:160]

            if attempt < max_retries:
                wait = attempt * 3
                _log_global(
                    f"⚠️ {mask_account(wxid)} 获取 Code 失败，{wait}s 后重试 "
                    f"({attempt}/{max_retries - 1})：{last_error}"
                )
                time.sleep(wait)

        _log_global(f"❌ {mask_account(wxid)} 获取微信 Code 失败：{last_error}")
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
        """通过 /mywc 获取 Code 后，走 UCMP 换取顺丰 Cookie。

        说明：
        - 旧版 OAuth 回调链路容易只拿到 sessionId，但 _login_mobile_ / _login_user_id_ 为空
        - 这里对齐 sfsy/sfkd 的 sfnewactivity 换绑流程，保证业务 Cookie 完整
        """
        code = self._get_wx_code(wxid, SF_WX_APPID)
        if not code:
            return None

        ucmp = self._ucmp_app_on_login(code)
        if not ucmp:
            _log_global(f"❌ {wxid[:10]}*** appOnLogin 失败")
            return None

        suuid = ucmp.get("sessionId", "")
        if not suuid:
            _log_global(f"❌ {wxid[:10]}*** appOnLogin 未返回 sessionId")
            return None

        try:
            s = requests.Session()
            s.verify = False
            ua = (
                "Mozilla/5.0 (iPhone; CPU iPhone OS 16_2 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
                "MicroMessenger/8.0.69(0x1800452d) NetType/WIFI Language/zh_CN"
            )

            # 尝试查询绑定信息（失败不阻断，后续仍可从 Cookie 取手机号）
            try:
                bind_headers = {
                    "user-agent": ua,
                    "content-type": "application/json",
                    "accept": "application/json, text/plain, */*",
                    "cookie": f"suuid={suuid}",
                    "referer": f"https://servicewechat.com/{SF_WX_APPID}/663/page-frame.html",
                }
                s.post(
                    "https://ucmp.sf-express.com/wxopen/weixin/wxMemIsBind",
                    json={},
                    headers=bind_headers,
                    timeout=15,
                )
            except Exception:
                pass

            biz_code = json.dumps({
                "path": "/up-member/newPoints",
                "linkCode": "SFAC20230803190840424",
                "supportShare": "YES",
                "subCategoryCode": "1",
                "from": "mypoint",
                "categoryCode": "1",
            }, ensure_ascii=False)
            sfnew_url = (
                "https://ucmp.sf-express.com/wechat-act/weixin/activity/sfnewactivity?"
                f"bizCode={url_encode(biz_code)}&regSource=mypoint&citycode=025"
                f"&cityname={url_encode('广州')}&wxapp-version=V17.49&suuid={suuid}"
            )
            sfnew_headers = {
                "user-agent": ua,
                "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
            s.get(sfnew_url, headers=sfnew_headers, timeout=25, allow_redirects=True)

            cookies = {}
            for c in s.cookies:
                if "mcs-mimp" in c.domain or "sf-express" in c.domain:
                    cookies[c.name] = c.value

            session_id = cookies.get("sessionId") or s.cookies.get("sessionId", "")
            login_mobile = cookies.get("_login_mobile_") or s.cookies.get("_login_mobile_", "")
            login_user_id = cookies.get("_login_user_id_") or s.cookies.get("_login_user_id_", "")

            # 兜底：部分环境下需要再访问会员页补齐 cookie
            if session_id and (not login_mobile or not login_user_id):
                try:
                    s.headers.update({
                        "User-Agent": ua,
                        "Cookie": f"sessionId={session_id}",
                    })
                    s.get(
                        "https://mcs-mimp-web.sf-express.com/mcs-mimp/app/index.html",
                        allow_redirects=True,
                        timeout=15,
                    )
                    for c in s.cookies:
                        if "mcs-mimp" in c.domain or "sf-express" in c.domain:
                            cookies[c.name] = c.value
                    login_mobile = cookies.get("_login_mobile_", "")
                    login_user_id = cookies.get("_login_user_id_", "")
                    session_id = cookies.get("sessionId", session_id)
                except Exception:
                    pass

            if not session_id or not login_mobile or not login_user_id:
                _log_global(
                    f"❌ {wxid[:10]}*** Cookie 不完整 session={bool(session_id)} "
                    f"mobile={bool(login_mobile)} uid={bool(login_user_id)}"
                )
                return None

            parts = [
                f"sessionId={session_id}",
                f"_login_mobile_={login_mobile}",
                f"_login_user_id_={login_user_id}",
            ]
            for k in ["HWWAFSESTIME", "HWWAFSESID", "JSESSIONID"]:
                if k in cookies and cookies[k]:
                    parts.append(f"{k}={cookies[k]}")

            cookie_str = ";".join(parts)
            _log_global(f"✅ {wxid[:10]}*** 自动获取凭证换绑成功 ➔ 手机: {login_mobile}")
            return cookie_str
        except Exception as e:
            _log_global(f"❌ {wxid[:10]}*** 换取 Cookie 异常: {str(e)[:80]}")
            return None

    def get_cookies_for_wxids(self, wxids: List[str] = None) -> Dict[str, str]:
        if not wxids:
            return {}

        results = {}
        for i, wxid in enumerate(wxids):
            try:
                cookie = self.get_cookie_for_wxid(wxid)
                if cookie:
                    results[wxid] = cookie
            except Exception:
                pass
            if i < len(wxids) - 1:
                time.sleep(2)
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
        self.completed_count = 0
        self.rewarded_count = 0

    @staticmethod
    def generate_device_id() -> str:
        result = ""
        for char in "xxxxxxxx-xxxx-xxxx":
            result += random.choice("abcdef0123456789") if char == "x" else char
        return result

    def _extract_task_id_from_url(self, url: str) -> str:
        """从 buttonRedirect 的 _ug_view_param 中提取 taskId/taskCode。"""
        if not url:
            return ""
        try:
            parsed = urlparse(str(url))
            params = parse_qs(parsed.query)
            if "_ug_view_param" in params:
                ug_params = json.loads(unquote(params["_ug_view_param"][0]))
                for key in ("taskId", "taskCode", "task_id"):
                    if ug_params.get(key):
                        return str(ug_params[key])
            # 兜底：正则抓 taskId
            m = re.search(r'"taskId"\s*:\s*"([^"]+)"', str(url))
            if m:
                return m.group(1)
        except Exception:
            pass
        return ""

    def _resolve_task_code(self, task: Dict) -> str:
        code = str(task.get("taskCode") or "").strip()
        if code:
            return code
        # 部分浏览任务 taskCode 为空，真实 code 在跳转参数里
        for key in ("buttonRedirect", "taskJumpAddress", "redirectUrl"):
            extracted = self._extract_task_id_from_url(task.get(key, ""))
            if extracted:
                return extracted
        return ""

    def _set_task_attrs(self, task: Dict):
        self.taskId = str(task.get("taskId", "") or "")
        self.taskCode = self._resolve_task_code(task)
        try:
            self.strategyId = int(task.get("strategyId", 0) or 0)
        except Exception:
            self.strategyId = 0
        self.title = str(task.get("title", "未知任务") or "未知任务")
        try:
            self.point = int(task.get("point", 0) or task.get("awardIntegral", 0) or 0)
        except Exception:
            self.point = 0

    def sign_in(self) -> Tuple[bool, str]:
        url = "https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~integralTaskSignPlusService~automaticSignFetchPackage"
        resp = self.http.request(url, {"comeFrom": "vioin", "channelFrom": "WEIXIN"})
        if resp and resp.get("success"):
            obj = resp.get("obj") or {}
            packets = obj.get("integralTaskSignPackageVOList") or []
            count_day = obj.get("countDay", obj.get("countDays", "-"))
            if packets:
                self.logger.success(
                    f"小程序签到成功: 【{packets[0].get('packetName')}】，本周累计【{count_day}】天"
                )
            else:
                # hasFinishSign=1 表示今日已签
                if obj.get("hasFinishSign") == 1:
                    self.logger.info(f"小程序今日已签到，本周累计【{count_day}】天")
                else:
                    self.logger.success(f"小程序签到完成，本周累计【{count_day}】天")
            return True, ""
        err = (resp or {}).get("errorMessage") or "失败"
        self.logger.warning(f"小程序签到失败: {err}")
        return False, err

    def get_task_list(self) -> List[Dict]:
        """拉取多 channel 任务并去重，兼容 taskCode 为空的浏览任务。"""
        url = "https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~integralTaskStrategyService~queryPointTaskAndSignFromES"
        all_tasks: List[Dict] = []
        seen = set()

        for ct in ["1", "2", "3", "4", "01", "02", "03", "04"]:
            resp = self.http.request(url, {
                "channelType": ct,
                "deviceId": self.generate_device_id(),
            })
            if not (resp and resp.get("success") and resp.get("obj")):
                continue

            obj = resp["obj"] or {}
            # 优先记录 channel 1 的积分
            if ct in ("1", "01") or not self.total_points:
                self.total_points = int(obj.get("totalPoint", self.total_points) or self.total_points or 0)

            task_items = obj.get("taskTitleLevels") or obj.get("ESobj") or []
            if not isinstance(task_items, list):
                continue

            for task in task_items:
                if not isinstance(task, dict):
                    continue
                task = dict(task)
                tc = self._resolve_task_code(task)
                if tc:
                    task["taskCode"] = tc
                # 去重键：优先 taskCode，其次 taskId+title
                key = tc or f"{task.get('taskId','')}|{task.get('title','')}"
                if not key or key in seen:
                    continue
                seen.add(key)
                all_tasks.append(task)

        return all_tasks

    def execute_task(self) -> bool:
        if not self.taskCode:
            return False
        url = "https://mcs-mimp-web.sf-express.com/mcs-mimp/commonRoutePost/memberEs/taskRecord/finishTask"
        resp = self.http.request(url, {"taskCode": self.taskCode})
        if not resp:
            self.logger.warning(f"任务提交无响应: {self.title}")
            return False
        if resp.get("success"):
            # 有些任务 success=true 但 obj=false，表示服务端接受但未真正完成
            if resp.get("obj") is False:
                self.logger.warning(f"任务提交返回未完成: {self.title}")
                return False
            self.logger.success(f"任务提交成功: {self.title}")
            self.completed_count += 1
            return True
        err = resp.get("errorMessage") or "未知错误"
        self.logger.warning(f"任务提交失败: {self.title} ➔ {err}")
        return False

    def receive_task_reward(self) -> bool:
        if not self.taskCode:
            return False
        url = "https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~integralTaskStrategyService~fetchIntegral"
        data = {
            "strategyId": self.strategyId,
            "taskId": self.taskId,
            "taskCode": self.taskCode,
            "deviceId": self.generate_device_id(),
        }
        resp = self.http.request(url, data)
        if resp and resp.get("success"):
            self.logger.success(f"日常任务奖励领取成功 ➔ {self.title} (+{self.point})")
            self.rewarded_count += 1
            return True
        err = (resp or {}).get("errorMessage") or "领取失败"
        self.logger.warning(f"奖励领取失败: {self.title} ➔ {err}")
        return False

    def run(self) -> Tuple[int, int]:
        self.logger.task("开始获取日常积分任务列表")
        tasks = self.get_task_list()
        if not tasks:
            self.logger.warning("日常任务列表为空")
            return 0, 0

        points_before = self.total_points
        self.logger.points(points_before, "执行前积分")
        self.logger.info(f"共发现 {len(tasks)} 个日常任务")

        for task in tasks:
            title = str(task.get("title") or "未知任务")
            status = task.get("status")
            try:
                status = int(status)
            except Exception:
                pass

            # 3 = 已完成
            if status == 3:
                self.logger.info(f"已完成: {title}")
                continue

            if title in DAILY_SKIP_TASKS:
                self.logger.info(f"跳过不可自动完成任务: {title}")
                continue

            self._set_task_attrs(task)
            if not self.taskCode:
                self.logger.warning(f"无法提取 taskCode，跳过: {title}")
                continue

            self.logger.task(f"处理任务: {title} (status={status}, +{self.point})")

            # status 1 = 待完成，先提交
            if status == 1:
                # 连续签到类进度未满则跳过
                process = str(task.get("process") or "")
                if "连签" in title and "/" in process:
                    try:
                        current, total = map(int, process.split("/", 1))
                        if current < total:
                            self.logger.info(f"{title} 进度 {process}，暂不可领")
                            continue
                    except Exception:
                        pass

                if self.execute_task():
                    time.sleep(2)
                    status = 2
                else:
                    time.sleep(1)
                    continue

            # status 2 = 可尝试领奖；失败则先完成再领
            if status == 2:
                # 浏览类关键词优先完成再领
                need_execute_first = any(kw in title for kw in EXECUTE_FIRST_KEYWORDS)
                if need_execute_first:
                    self.execute_task()
                    time.sleep(2)
                    if self.receive_task_reward():
                        time.sleep(1)
                        continue

                # 先尝试直接领奖
                if self.receive_task_reward():
                    time.sleep(1)
                    continue

                # 直接领失败，再执行一次后重试
                if self.execute_task():
                    time.sleep(2)
                    self.receive_task_reward()
                time.sleep(1)
                continue

            time.sleep(1)

        # 刷新积分
        self.get_task_list()
        points_after = self.total_points
        self.logger.points(points_after, "执行后积分")
        earned = points_after - points_before
        if self.completed_count == 0 and self.rewarded_count == 0:
            self.logger.info(
                "说明: 当前可自动完成的浏览/点击类任务已全部完成；"
                "剩余未完成任务多为真实寄件/设置类，需人工操作"
            )
        self.logger.info(
            f"日常任务统计: 提交成功 {self.completed_count}，领奖成功 {self.rewarded_count}，积分变化 {earned:+d}"
        )
        return points_before, points_after


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


# ==================== 核心处理器 ====================
def run_account(account_raw: str, index: int) -> Dict[str, Any]:
    logger = Logger()
    fixed_proxy = account_raw.split('#')[-1].strip() if '#' in account_raw else ""
    account_url = account_raw.split('#')[0].strip() if '#' in account_raw else account_raw
    
    http = SFHttpClient(fixed_proxy)
    success, user_id, phone = http.login(account_url)
    if not success:
        return {
            'success': False,
            'phone': '未登录账号',
            'error': '登录失败或顺丰凭证已失效',
            'points_before': 0,
            'points_after': 0,
            'points_earned': 0,
            'member_day_prizes': [],
        }
        
    masked = phone[:3] + "****" + phone[7:] if len(phone) >= 7 else phone
    logger.success(f"账号 [{index + 1}] ➔ 【{masked}】激活认证成功")
    
    result = {'success': True, 'phone': masked, 'index': index, 'points_before': 0, 'points_after': 0, 'points_earned': 0, 'member_day_prizes': []}
    
    if ENABLE_DAILY_TASK:
        logger.task("开始执行日常积分任务（签到 + 做任务 + 领积分）")
        daily = DailyTaskExecutor(http, logger, user_id)
        # 小程序签到
        daily.sign_in()
        time.sleep(1)
        pb, pa = daily.run()
        result['points_before'] = pb
        result['points_after'] = pa
        result['points_earned'] = pa - pb
        logger.info(f"日常任务积分变化: {pb} -> {pa} ({(pa - pb):+d})")
        
    if ENABLE_MEMBER_DAY and 26 <= datetime.now().day <= 28:
        md = MemberDayExecutor(http, logger, user_id)
        result['member_day_prizes'] = md.run().get('lottery_prizes', [])
        
    return result


def _auto_fetch_cookies() -> List[str]:
    mgr = AutoCookieManager()
    wxids = parse_env_accounts(SF_WXIDS)
    if not wxids:
        _log_global("❌ 未配置 sf_wxid 或 SF_WXID")
        return []

    _log_global(f"🔎 顺丰专属账号变量解析到 {len(wxids)} 个账号")
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
        "points_before": int(result.get("points_before") or 0),
        "points_after": int(result.get("points_after") or 0),
        "points": int(result.get("points_earned") or 0),
        "member_day_prizes": result.get("member_day_prizes") or [],
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
            lines.append(
                f"💰 积分：{item.get('points_before', 0)} → {item.get('points_after', 0)} "
                f"（变化 {int(item.get('points') or 0):+d}）"
            )
            prizes = item.get("member_day_prizes") or []
            if prizes:
                lines.append(f"🎁 会员日：{', '.join(str(p) for p in prizes)}")
        else:
            lines.append(f"🧨 原因：{item.get('message')}")

        lines.append("------------------------------")

    return "\n".join(lines)


def dispatch_notify() -> None:
    if not GLOBAL_NOTIFY_BUFFERS:
        return

    final_desp = build_notify_report()
    print("\n" + final_desp)
    if PUSH_SWITCH != "1":
        print("[通知] SFSY_PUSH 已关闭，仅输出聚合报表")
        return
    if not SEND_NOTIFY_AVAILABLE:
        print(f"[通知] 未执行推送：SendNotify.py 导入失败：{SEND_NOTIFY_IMPORT_ERROR}")
        return

    try:
        send_push_notification(SCRIPT_TITLE, final_desp)
        print("[通知] 聚合推送完成")
    except Exception as exc:
        print(f"[通知] 聚合推送失败：{exc}")


def main():
    GLOBAL_NOTIFY_BUFFERS.clear()
    AUTO_COOKIE_INDEX_BY_VALUE.clear()

    legacy_value = os.getenv("sfsyUrl") or ""
    if SF_AUTO_COOKIE and SF_WXIDS.strip():
        account_list = _auto_fetch_cookies()
    elif legacy_value.strip():
        account_list = parse_env_accounts(legacy_value)
    else:
        account_list = _auto_fetch_cookies() if SF_AUTO_COOKIE else []

    if not account_list:
        if not GLOBAL_NOTIFY_BUFFERS:
            GLOBAL_NOTIFY_BUFFERS.append({
                "index": 0,
                "account": "未配置",
                "ok": False,
                "points_before": 0,
                "points_after": 0,
                "points": 0,
                "member_day_prizes": [],
                "message": "请配置 sf_wxid 或 SF_WXID；也可使用旧变量 sfsyUrl",
            })
        dispatch_notify()
        return 1

    print("==================================================")
    print(f"🎉 顺丰速运任务启动... 共加载 {len(account_list)} 个账户")
    print("==================================================")

    for idx, raw in enumerate(account_list):
        try:
            result = run_account(raw, idx)
        except Exception as exc:
            result = {
                "success": False,
                "phone": f"账号{idx + 1}",
                "error": f"账号执行异常：{str(exc)[:160]}",
                "points_before": 0,
                "points_after": 0,
                "points_earned": 0,
                "member_day_prizes": [],
            }
        append_notify_result(AUTO_COOKIE_INDEX_BY_VALUE.get(raw, idx + 1), result)
        if idx < len(account_list) - 1:
            time.sleep(2)

    dispatch_notify()
    total_failed = sum(1 for item in GLOBAL_NOTIFY_BUFFERS if not item.get("ok"))
    return 0 if total_failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())