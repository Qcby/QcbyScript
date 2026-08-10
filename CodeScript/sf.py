"""
顺丰速运自动任务 v1.1.0（mywc网关聚合推送版）

功能：自动执行顺丰速运日常积分任务、会员日活动、红包抽奖和优惠券查询，支持多账号执行，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL                 必填其一，自建授权服务器域名
   - 示例：http://127.0.0.1:8110
   - 脚本会自动拼接 /mywc
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=wxd4185d00bf7e08ac
   - 请求头：auth=账号标识

2. 账号变量：
   sf_wxid 或 SF_WXID                             推荐，顺丰速运专属账号变量
   - 兼容旧变量：sf_openid
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：wxid_a&wxid_b 或 wxid_a,wxid_b

3. 代理变量（可选，适配品赞代理）：
   sf_proxy_api_url                                品赞代理 API 地址，开启后每个账号自动获取代理
   - 代理接口返回格式支持：纯 IP:PORT，或带账号密码的 IP:PORT ACCOUNT PASSWORD（品赞格式）
   - 示例：http://your-proxy-host:port/get
   - 仅在配置了本变量时启用 API 代理，未配置则不使用代理
   - 单账号固定代理：在账号后追加 #proxy=IP:PORT 可指定该账号专用代理

4. 推送变量：
   需要同目录存在 SendNotify.py，脚本结束后会统一调用 send_push_notification。
   常用推送变量如下，配置任意一种即可：
   QYWX_KEY                                        企业微信机器人 key
   PUSH_PLUS_TOKEN                                 PushPlus token
   PUSH_KEY                                        Server 酱 key
   DD_BOT_TOKEN 或 DD_BOT_SECRET                   钉钉机器人 token/secret
   FSKEY                                           飞书机器人 key

5. 青龙任务建议：
   名称：顺丰速运自动任务
   命令：python3 sf.py
   定时：每天运行 1 次即可，具体时间自行调整
"""

import hashlib
import json
import os
import sys
import random
import re
import time
import unicodedata
from datetime import datetime

# Windows 控制台默认 GBK 无法编码 emoji/特殊字符，强制 stdout/stderr 为 UTF-8
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    if hasattr(sys.stderr, 'reconfigure'):
        sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import unquote, urlparse, parse_qs, quote as url_encode
from threading import Lock
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

try:
    from SendNotify import send_push_notification
except Exception:
    send_push_notification = None

# ==================== 配置区域 ====================
SCRIPT_TITLE = "顺丰速运自动任务"
GLOBAL_NOTIFY_BUFFERS: List[Dict[str, Any]] = []
ENABLE_DAILY_TASK = True         # 日常积分任务 (签到+做任务+领积分)
ENABLE_MEMBER_DAY = True         # 会员日活动 (每月26-28号自动执行)
ENABLE_RED_PACKET = True         # 顺丰红包大派送 (官方每天免费抽奖一次)
ENABLE_COUPON_QUERY = True       # 我的优惠券查询 (列出当前账户可用/全部优惠券)
CONCURRENT_NUM = 1               # 并发数量 (1~20)

TOKEN = 'wwesldfs29aniversaryvdld29'
inviteId = []
SYS_CODE = 'MCS-MIMP-CORE'

# 顺丰红包大派送 (活动 type=MID_JGGCJ) 固定参数
# acId 为活动 ID；secendChannel 即活动专属 channel（覆盖默认 xcxpart）
RED_PACKET_AC_ID = '1D2532575D49438FA3D63842BF53F6EB'
RED_PACKET_SECEND_CHANNEL = 'MBHD_BASIC20260409160224813'
# 抽奖发放接口 lotteryPrize 所需的 ruleCode（来自 getUserAcRuleInfo 响应 obj.ruleCode，
# 对所有账号/会话固定）。md5Sign 为该固定三元组(acId+ruleCode+secendChannel)的常量签名，
# 与账号/session/timestamp 无关，可硬编码复用。
RED_PACKET_RULE_CODE = 'SFGZ20260409160224757'
RED_PACKET_MD5_SIGN = 'f196f7a1db74f90f84bf03b8d54fc006'

# 统一变量名称与默认端口映射
SF_WX_SERVER = (os.environ.get("wx_server_url") or os.environ.get("WX_SERVER_URL") or "").strip().rstrip("/")
SF_WX_APPID = os.getenv("sf_wx_appid", "wxd4185d00bf7e08ac")       # 小程序 appid
SF_PUBLIC_ID = os.getenv("sf_public_id", "gh_f9d9fca26a50")        # 小程序原始ID
SF_OAUTH_APPID = os.getenv("sf_oauth_appid", "wx0d9aa0e894066e87") # 公众号 appid
SF_OAUTH_SCENE = os.getenv("sf_oauth_scene", "692")                # 活动场景号
SF_AUTO_COOKIE = os.getenv("sf_auto_cookie", "1") == "1"          # 自动获取Cookie开关
SF_OPENIDS = os.environ.get("sf_wxid") or os.environ.get("SF_WXID") or os.environ.get("sf_openid") or ""

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

# 代理模块：由环境变量 sf_proxy_api_url 驱动
PROXY_API_URL = os.getenv("sf_proxy_api_url", "")
PROXY_TYPE = os.getenv("sf_proxy_type", "socks5")
PROXY_TIMEOUT = 15
MAX_PROXY_RETRIES = 5
REQUEST_COUNT = 3
print_lock = Lock()
AUTO_COOKIE_INDEX_BY_VALUE: Dict[str, int] = {}


class Logger:
    def __init__(self):
        pass

    def _log(self, icon: str, msg: str):
        line = f"{icon} {msg}" if icon else msg
        with print_lock:
            print(line)

    def info(self, msg): self._log('📝', msg)
    def debug(self, msg): self._log('🐞', msg)
    def raw(self, msg): self._log('', msg)
    def success(self, msg): self._log('✨', msg)
    def warning(self, msg): self._log('⚠️', msg)
    def error(self, msg): self._log('❌', msg)
    def task(self, msg): self._log('🎯', msg)
    def task_skip(self, msg): self._log('⏭️', msg)
    def task_complete(self, msg): self._log('✅', msg)
    def medal(self, msg): self._log('🎁', msg)
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
    return [
        item.strip()
        for item in re.split(r"[&,\n，]+", str(raw or ""))
        if item.strip()
    ]


def mask_account(value: Any) -> str:
    value = str(value or "")
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-4:]}"


class ProxyManager:
    """代理管理器（环境变量 sf_proxy_api_url）"""
    def __init__(self, api_url: str):
        self.api_url = api_url

    def get_proxy(self) -> Optional[Dict[str, str]]:
        """获取代理，返回 {'http': url, 'https': url} 或 None。

        支持两种返回格式：
        - 纯 IP:PORT（可带账号密码：IP:PORT ACCOUNT PASSWORD）
        - 已含协议的完整代理地址
        """
        try:
            if not self.api_url:
                return None
            response = requests.get(self.api_url, timeout=10)
            if response.status_code == 200:
                proxy_text = response.text.strip()
                # 品赞账号密码格式：IP:PORT ACCOUNT PASSWORD
                parts = proxy_text.split()
                if len(parts) == 3:
                    ip_port = parts[0]
                    account = parts[1]
                    password = parts[2]
                    proxy_text = f"http://{account}:{password}@{ip_port}"
                if ':' in proxy_text:
                    if proxy_text.startswith('http://') or proxy_text.startswith('https://'):
                        proxy = proxy_text
                    else:
                        proxy = f'http://{proxy_text}'
                    # 隐藏认证信息用于显示
                    display_proxy = proxy
                    if '@' in proxy:
                        seg = proxy.split('@')
                        if len(seg) == 2:
                            display_proxy = f"http://***:***@{seg[1]}"
                    _log_global(f"✅ 成功获取代理: {display_proxy}")
                    return {'http': proxy, 'https': proxy}
            _log_global(f"❌ 获取代理失败: {response.text}")
            return None
        except Exception as e:
            _log_global(f"❌ 获取代理异常: {str(e)}")
            return None


# 模块级代理管理器单例
proxy_manager = ProxyManager(PROXY_API_URL)


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
            _log_global("❌ 未配置 wx_server_url，无法获取在线账号")
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
        """通过 GET /mywc 获取微信 code。"""
        if not self.wx_server:
            _log_global("❌ 未配置 wx_server_url 或 WX_SERVER_URL，无法请求 /mywc")
            return None
        target_appid = appid or SF_WX_APPID
        url = f"{self.wx_server}/mywc"

        for attempt in range(max_retries):
            try:
                r = self.session.get(
                    url,
                    params={"wxid": wxid, "appId": target_appid},
                    headers={
                        "auth": wxid,
                        "User-Agent": "Mozilla/5.0 MicroMessenger/8.0.50",
                    },
                    timeout=30,
                )
                j = r.json()

                candidates = [
                    (((j.get("data") or {}).get("data") or {}).get("code")),
                    (((j.get("data") or {}).get("data") or {}).get("loginCode")),
                    (((j.get("data") or {}).get("data") or {}).get("wxcode")),
                    ((j.get("data") or {}).get("code")),
                    ((j.get("data") or {}).get("loginCode")),
                    ((j.get("data") or {}).get("wxcode")),
                    ((((j.get("result") or {}).get("data") or {}).get("code"))),
                    ((((j.get("result") or {}).get("data") or {}).get("wxcode"))),
                    ((j.get("result") or {}).get("code")),
                    j.get("code"),
                    j.get("loginCode"),
                    j.get("wxcode"),
                    j.get("wx_code"),
                ]
                code = next(
                    (
                        str(candidate).strip()
                        for candidate in candidates
                        if isinstance(candidate, str) and len(candidate.strip()) >= 10
                    ),
                    "",
                )

                if not code:
                    if attempt < max_retries - 1:
                        wait = (attempt + 1) * 3
                        _log_global(f"⚠️ {wxid[:12]}***: code为空，{wait}s后重试({attempt+1}/{max_retries})")
                        time.sleep(wait)
                        continue
                    _log_global(f"❌ {wxid[:12]}***: 获取code失败 appid={target_appid} resp={str(j)[:160]}")
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
        """通过 /mywc 拿到 code 后，走 UCMP 换取顺丰 Cookie。"""
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
            masked_mobile = login_mobile[:3] + "****" + login_mobile[7:] if len(login_mobile) >= 7 else login_mobile
            _log_global(f"✅ {wxid[:10]}*** 自动获取凭证换绑成功 ➔ 手机: {masked_mobile}")
            return cookie_str
        except Exception as e:
            _log_global(f"❌ {wxid[:10]}*** 换取 Cookie 异常: {str(e)[:80]}")
            return None

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
        proxy = proxy_manager.get_proxy()
        if proxy:
            self.session.proxies = proxy
            self.proxy_display = "API代理"

    def _generate_sign(self) -> Dict[str, str]:
        timestamp = str(int(round(time.time() * 1000)))
        data = f'token={TOKEN}&timestamp={timestamp}&sysCode={SYS_CODE}'
        signature = hashlib.md5(data.encode()).hexdigest()
        return {'sysCode': SYS_CODE, 'timestamp': timestamp, 'signature': signature}

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
                        proxy = proxy_manager.get_proxy()
                        if proxy:
                            self.session.proxies = proxy
                            self.proxy_display = "API代理"
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
        self.welfare_completed = 0
        self.welfare_done = []
        self.device_id = self.generate_device_id()

    @staticmethod
    def generate_device_id() -> str:
        # 完整的 36 位 UUID 格式，避免截断导致服务端校验/任务关联异常
        pattern = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
        return "".join(random.choice("abcdef0123456789") if c == "x" else c for c in pattern)

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
        """小程序签到（automaticSignFetchPackage）。"""
        url = "https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~integralTaskSignPlusService~automaticSignFetchPackage"
        resp = self.http.request(url, {"comeFrom": "vioin", "channelFrom": "WEIXIN"})
        if resp and resp.get("success"):
            obj = resp.get("obj") or {}
            packets = obj.get("integralTaskSignPackageVOList") or []
            count_day = obj.get("countDay", obj.get("countDays", "-"))
            if packets:
                self.logger.success(
                    f"[小程序签到] 签到成功，获得【{packets[0].get('packetName')}】，本周累计签到【{int(count_day) + 1}】天"
                )
            else:
                # hasFinishSign=1 表示今日已签
                if obj.get("hasFinishSign") == 1:
                    self.logger.info(f"[小程序签到] 今日已签到，本周累计签到【{count_day}】天")
                else:
                    self.logger.success(f"[小程序签到] 签到完成，本周累计签到【{int(count_day) + 1}】天")
            return True, ""
        err = (resp or {}).get("errorMessage") or "失败"
        self.logger.warning(f"[小程序签到] 签到失败: {err}")
        return False, err

    def new_sign_in(self) -> Tuple[bool, str]:
        """签到日历（integralSignV2Service）。"""
        url = "https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~integralSignV2Service~sign"
        data: Dict = {}
        original_platform = self.http.headers.get("platform", "MINI_PROGRAM")
        self.http.headers["platform"] = "MINI_PROGRAM"
        try:
            resp = self.http.request(url, data)
            if resp and resp.get("success"):
                obj = resp.get("obj") or {}
                signed = obj.get("signed", False)
                day_count = obj.get("dayCount", 0)
                award = obj.get("award") or {}
                if signed and award:
                    gift_bag_name = award.get("giftBagName", "未知奖励")
                    self.logger.success(
                        f"[签到日历] 签到成功，连续第{day_count}天，获得【{gift_bag_name}】"
                    )
                elif signed:
                    self.logger.info(f"[签到日历] 今日已签到，连续第{day_count}天")
                else:
                    self.logger.info(f"[签到日历] 签到完成")
                return True, ""
            err = (resp or {}).get("errorMessage") or "失败"
            self.logger.warning(f"[签到日历] 签到失败: {err}")
            return False, err
        finally:
            self.http.headers["platform"] = original_platform

    def get_task_list(self) -> List[Dict]:
        """拉取多 channel 任务并去重，兼容 taskCode 为空的浏览任务。"""
        url = "https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~integralTaskStrategyService~queryPointTaskAndSignFromES"
        all_tasks: List[Dict] = []
        seen = set()

        for ct in ["1", "2", "3", "4", "01", "02", "03", "04"]:
            resp = self.http.request(url, {
                "channelType": ct,
                "deviceId": self.device_id,
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

    def query_points(self) -> int:
        """签到前查询当前积分，作为统计基线，避免漏统签到积分。"""
        url = "https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~integralTaskStrategyService~queryPointTaskAndSignFromES"
        try:
            resp = self.http.request(url, {"channelType": "1", "deviceId": self.device_id})
            if resp and resp.get("success") and resp.get("obj"):
                self.total_points = int((resp.get("obj") or {}).get("totalPoint", self.total_points) or self.total_points or 0)
        except Exception:
            pass
        return self.total_points

    def execute_task(self) -> bool:
        if not self.taskCode:
            return False
        url = "https://mcs-mimp-web.sf-express.com/mcs-mimp/commonRoutePost/memberEs/taskRecord/finishTask"
        resp = self.http.request(url, {"taskCode": self.taskCode})
        if not resp:
            self.logger.warning(f"任务提交无响应: {self.title}")
            return False
        if resp.get("success"):
            self.logger.task_complete(f"[{self.title}] 提交成功")
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
            "deviceId": self.device_id,
        }
        resp = self.http.request(url, data)
        if resp and resp.get("success"):
            self.logger.success(f"成功领取任务奖励: {self.title}")
            self.logger.medal(f"[{self.title}] 奖励领取成功 (+{self.point})")
            self.rewarded_count += 1
            return True
        err = (resp or {}).get("errorMessage") or "领取失败"
        self.logger.warning(f"奖励领取失败: {self.title} ➔ {err}")
        return False

    def _update_points(self):
        """刷新积分统计（不打印，避免冗余输出）"""
        tasks = self.get_task_list()
        if tasks:
            self.query_points()

    # ===== 生活特权完成任务（领任意生活特权福利）=====
    def get_welfare_list(self) -> List[Dict]:
        """获取可领取的生活特权列表 (exchangeStatus=1 表示可领取)"""
        url = "https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberGoods~mallGoodsLifeService~list"
        data = {
            "memGrade": 3,
            "categoryCode": "SHTQ",
            "showCode": "SHTQWNTJ",
        }
        response = self.http.request(url, data)
        if response and response.get("success"):
            obj_list = response.get("obj", [])
            welfare_list = []
            for module in obj_list:
                for goods in module.get("goodsList", []):
                    if goods.get("exchangeStatus") == 1:
                        welfare_list.append({
                            "goodsId": goods.get("goodsId"),
                            "goodsNo": goods.get("goodsNo"),
                            "goodsName": goods.get("goodsName"),
                            "showName": goods.get("showName", ""),
                            "id": goods.get("id"),
                        })
            return welfare_list
        return []

    def handle_welfare_task(self) -> bool:
        """领取生活特权"""
        welfare_list = self.get_welfare_list()
        if not welfare_list:
            self.logger.warning("没有可领取的生活特权")
            return False
        self.logger.info(f"找到 {len(welfare_list)} 个可领取的生活特权")
        for welfare in welfare_list:
            goods_no = welfare.get("goodsNo")
            goods_name = welfare.get("goodsName")
            show_name = welfare.get("showName")
            if not goods_no:
                continue
            display_name = f"{show_name} - {goods_name}" if show_name else goods_name
            url = "https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberGoods~pointMallService~createOrder"
            data = {
                "from": "Point_Mall",
                "orderSource": "POINT_MALL_EXCHANGE",
                "goodsNo": goods_no,
                "quantity": 1,
                "taskCode": self.taskCode,
            }
            response = self.http.request(url, data)
            if response and response.get("success"):
                order_no = (response.get("obj") or {}).get("orderNo", "")
                self.logger.success(f"成功领取生活特权: {display_name} (订单号: {order_no})")
                self.welfare_done.append(welfare)
                return True
            else:
                err = (response or {}).get("errorMessage") or "未知错误"
                self.logger.error(f"领取生活特权失败: {display_name} - {err}")
            time.sleep(1)
        return False

    def run(self) -> Tuple[int, int]:
        points_before = self.total_points
        self.logger.info("开始获取日常积分任务列表")
        tasks = self.get_task_list()
        if not tasks:
            self.logger.warning("日常任务列表为空")
            self.query_points()  # 刷新积分（含已签到的积分）
            points_after = self.total_points
            self.logger.points(points_after, "执行后积分")
            self.logger.info(
                f"日常任务统计: 提交成功 {self.completed_count}，"
                f"领奖成功 {self.rewarded_count}，积分变化 {(points_after - points_before):+d}"
            )
            return points_before, points_after

        self.logger.task(f"共发现 {len(tasks)} 个日常任务")

        for task in tasks:
            title = str(task.get("title") or "未知任务")
            status = task.get("status")
            try:
                status = int(status)
            except Exception:
                pass

            # 3 = 已完成
            if status == 3:
                self.logger.success(f"{title} - 已完成")
                continue

            if title in DAILY_SKIP_TASKS:
                self.logger.task_skip(f"[{title}] 已跳过")
                continue

            self._set_task_attrs(task)
            if not self.taskCode:
                self.logger.warning(f"无法提取 taskCode，跳过: {title}")
                continue

            self.logger.task(f"发现任务: {title} (状态: {status})")

            if '领任意生活特权福利' in title:
                if self.handle_welfare_task():
                    self.welfare_completed += 1
                    time.sleep(2)
                    if self.execute_task():
                        time.sleep(2)
                        if self.receive_task_reward():
                            self._update_points()
                    else:
                        self.logger.warning(f'任务执行失败: {title}')
                else:
                    self.logger.warning(f'{title} - 无法完成,跳过')
                time.sleep(3)
                continue

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

        # 刷新积分（含签到 + 任务所得）
        self.query_points()
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
            name = (resp.get('obj') or {}).get('productName', '未抽中')
            self.logger.success(f'会员日抽奖成功 ➔ 获得: {name}')
            return name
        return None

    def run(self) -> Dict[str, Any]:
        result = {'lottery_prizes': []}
        index_info = self.get_index()
        if not index_info: return result
        try:
            lottery_num = int(index_info.get('lotteryNum', 0) or 0)
        except (TypeError, ValueError):
            lottery_num = 0
        for _ in range(lottery_num):
            prize = self.lottery()
            if prize: result['lottery_prizes'].append(prize)
        return result


# ==================== 顺丰红包大派送 ====================
class RedPacketExecutor:
    """顺丰红包大派送 (mid-platform, activityType=MID_JGGCJ)，官方每天免费抽奖一次。
    端点：
      - getUserAcRuleInfo : 查询活动规则 / 剩余抽奖次数 surplusLotteryNum / ruleCode
      - lotteryPrize      : 抽奖发放接口，点击"抽奖"按钮调用，服务端随机发券
    lotteryPrize 必须带 body: {acId, ruleCode, secendChannel, md5Sign}，且 Referer 为
    nineBlockDraw 抽奖页（含 acId 与 token）。md5Sign 为固定常量（见 RED_PACKET_MD5_SIGN）。
    必须先查 getUserAcRuleInfo 的 surplusLotteryNum，为 0 时直接跳过，避免"暂无抽奖次数"。
    """

    BASE = 'https://mcs-mimp-web.sf-express.com/mcs-mimp'

    def __init__(self, http: SFHttpClient, logger: Logger):
        self.http = http
        self.logger = logger
        # 该活动专属 channel（覆盖默认 xcxpart）
        self.act_headers = {
            'channel': RED_PACKET_SECEND_CHANNEL,
            'sysCode': SYS_CODE,
            'platform': 'MINI_PROGRAM',
        }

    def _rule_info(self) -> Optional[Dict]:
        """查询活动规则，返回 obj.surplusLotteryNum（剩余免费抽奖次数）等。"""
        url = f'{self.BASE}/commonNoLoginPost/~actMiddlePlat~midActivity~getUserAcRuleInfo'
        resp = self.http.request(
            url,
            {'acId': RED_PACKET_AC_ID, 'empNum': '', 'shareUserId': '',
             'shareRuleCode': '', 'shareTaskId': ''},
            extra_headers=self.act_headers,
        )
        return resp

    def _draw(self) -> Optional[Dict]:
        """执行一次免费抽奖，调用抽奖发放接口 lotteryPrize。

        lotteryPrize 是"点击抽奖→服务端随机发券"的发放接口。
        必须带 body: {acId, ruleCode, secendChannel, md5Sign}，Referer 为 nineBlockDraw 抽奖页。
        md5Sign 为固定常量（acId+ruleCode+secendChannel 的 MD5，与账号/session/timestamp 无关）。
        """
        url = f'{self.BASE}/commonPost/~actMiddlePlat~midActivity~lotteryPrize'
        body = {
            'acId': RED_PACKET_AC_ID,
            'ruleCode': RED_PACKET_RULE_CODE,
            'secendChannel': RED_PACKET_SECEND_CHANNEL,
            'md5Sign': RED_PACKET_MD5_SIGN,
        }
        # 当前会话 token（sessionId），拼进 nineBlockDraw 抽奖页 Referer
        sid = ''
        uid = ''
        phone = ''
        try:
            ck = self.http.session.cookies.get_dict()
            sid = ck.get('sessionId', '')
            uid = ck.get('_login_user_id_', '')
            phone = ck.get('_login_mobile_', '')
        except Exception:
            pass
        mask = f"{phone[:3]}****{phone[-4:]}" if len(phone) >= 7 else phone
        referer = (
            'https://mcs-mimp-web.sf-express.com/origin/g/mid-platform/main-active/'
            'nineBlockDraw'
            f'?redirectUri=/origin/g/mid-platform/main-active/activityCenterEntry'
            f'&mobile={mask}&userId={uid}&scene=676&memberType=0&token={sid}'
            f'&acId={RED_PACKET_AC_ID}&from={RED_PACKET_SECEND_CHANNEL}'
            '&activityType=MID_JGGCJ&source=CX&isFinishActivity=true'
        )
        draw_headers = dict(self.act_headers)
        draw_headers['referer'] = referer
        draw_headers['origin'] = 'https://mcs-mimp-web.sf-express.com'
        resp = self.http.request(url, body, extra_headers=draw_headers)
        return resp

    def run(self) -> List[str]:
        prizes: List[str] = []

        # 1. 查询活动规则（含剩余次数 surplusLotteryNum 与本人中奖 midAcAwardRecords）
        rule = None
        surplus = None
        try:
            rule = self._rule_info()
            if rule and rule.get('success'):
                obj = rule.get('obj') or {}
                rule_name = obj.get('acName') or obj.get('name') or '顺丰红包大派送'
                self.logger.info(f'[顺丰红包大派送] 活动: {rule_name}')
                if 'surplusLotteryNum' in obj:
                    try:
                        surplus = int(obj.get('surplusLotteryNum'))
                    except (TypeError, ValueError):
                        surplus = None
                freq = obj.get('periodFrequency')
                pname = obj.get('periodName')
                if surplus is not None:
                    self.logger.info(f'[顺丰红包大派送] 剩余免费抽奖次数: {surplus}'
                                     + (f'（{pname or "每天"}限{freq}次）' if freq else ''))
            else:
                self.logger.info('[顺丰红包大派送] 查询活动规则未返回成功，仍尝试抽奖')
        except Exception:
            pass

        # 2. 次数校验：剩余次数为 0 时直接跳过抽奖
        if surplus == 0:
            self.logger.info('[顺丰红包大派送] 今日免费抽奖次数已用完（暂无抽奖次数），跳过抽奖。')
        else:
            # 3. 免费抽奖一次
            try:
                resp = self._draw()
            except Exception as e:
                self.logger.error(f'[顺丰红包大派送] 抽奖请求异常: {str(e)[:80]}')
                resp = None

            if resp and resp.get('success'):
                # 4. 解析中奖：lotteryPrize 真正发放时，中奖落在 obj.userWinPrizeList[]，
                #    success=true 且 notSuccess=false 即本客户端本次真实抽中。
                obj = resp.get('obj') or {}
                draw_records = obj.get('userWinPrizeList') if isinstance(obj, dict) else None
                if not isinstance(draw_records, list):
                    draw_records = obj.get('midAcAwardRecords') if isinstance(obj, dict) else None
                if not isinstance(draw_records, list):
                    draw_records = []

                if draw_records:
                    for rec in draw_records:
                        if not isinstance(rec, dict):
                            continue
                        name = (rec.get('prizeName') or rec.get('packetName')
                                or rec.get('couponName') or rec.get('commodityName')
                                or rec.get('productName') or '未解析奖品')
                        amount = rec.get('prizeAmount') or rec.get('couponAmount') or ''
                        eff = rec.get('effectTm') or ''
                        inv = rec.get('invalidTm') or ''
                        extra = ''
                        if amount:
                            extra += f' 面额={amount}元'
                        if eff and inv:
                            extra += f' 有效期={eff}~{inv}'
                        self.logger.success(f'[顺丰红包大派送] 免费抽奖成功 ➔ 获得: {name}{extra}')
                        prizes.append(name)
                else:
                    # success 但无中奖列表：本次未中（活动可能返回"谢谢参与"）
                    self.logger.info('[顺丰红包大派送] 抽奖受理成功，但本次未抽中奖品'
                                     '（服务端返回空奖池或未命中）。')
                    if surplus is not None:
                        self.logger.info(f'[顺丰红包大派送] 剩余免费抽奖次数: {surplus}')
            else:
                code = resp.get('errorCode') if resp else None
                msg = (resp or {}).get('errorMsg') or (resp or {}).get('msg') or '未知错误'
                if any(k in str(msg) for k in ('已', '次数', '今日', '重复', 'limit', 'Limit', '暂无')):
                    self.logger.info(f'[顺丰红包大派送] 今日免费抽奖已完成或次数不足: {msg}')
                else:
                    self.logger.error(f'[顺丰红包大派送] 抽奖失败: code={code} msg={msg}')

        return prizes


# ==================== 优惠券查询执行器 ====================

class CouponQueryExecutor:
    """查询当前账户【已持有】的优惠券列表（"我的优惠券"页 = couponCollection）。

    接口：
        POST https://mcs-mimp-web.sf-express.com/mcs-mimp/coupon/available/list
    body: {"type":"1","pageSize":10,"pageNum":1,"couponType":"","labelCode":"0","channel":"SFAPP"}
    """
    DEFAULT_URL = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/coupon/available/list'
    REQ_BODY = {
        "type": "1",
        "pageSize": 50,
        "pageNum": 1,
        "couponType": "",
        "labelCode": "0",
        "channel": "SFAPP",
    }
    ACT_HEADERS = {
        'channel': 'HOME_COUPON',
        'syscode': 'MCS-MIMP-CORE',
        'platform': 'SFAPP',
        'content-type': 'application/json',
        'referer': (
            'https://mcs-mimp-web.sf-express.com/home'
            '?redirectUri=/couponCollection&from=HOME_COUPON'
        ),
    }

    def __init__(self, http: SFHttpClient, logger: Logger):
        self.http = http
        self.logger = logger
        self.force_url = os.environ.get('COUPON_QUERY_URL', '').strip() or None

    @staticmethod
    def _fmt_coupon(c: Dict) -> str:
        name = c.get('couponName') or c.get('couponName') or '未命名券'
        amt = c.get('pledgeAmt')
        amt_s = f"¥{amt}" if amt is not None else ''
        eff = c.get('effectTm', '')
        inv = c.get('invalidTm', '')
        status = c.get('status', '')
        status_s = '有效' if status == 'EFFE' else (status or '未知')
        return f"{name}({amt_s})[{status_s} {eff}~{inv}]"

    def run(self) -> List[str]:
        url = self.force_url or self.DEFAULT_URL
        try:
            resp = self.http.request(url, data=self.REQ_BODY, extra_headers=self.ACT_HEADERS)
        except Exception as e:
            self.logger.error(f"优惠券查询请求异常: {str(e)[:80]}")
            return []

        if not resp:
            self.logger.error("优惠券查询无响应（接口可能已下线）")
            return []

        if not resp.get('success'):
            self.logger.error(f"优惠券查询失败: {resp.get('msg') or resp.get('message') or resp}")
            return []

        obj = resp.get('obj')
        if not isinstance(obj, list):
            self.logger.info("优惠券列表为空")
            return []

        coupons = [self._fmt_coupon(c) for c in obj if isinstance(c, dict)]
        self.logger.success(f"查询到 {len(coupons)} 张优惠券")
        for c in coupons:
            self.logger.raw(f"  🎟️ {c}")
        return coupons


# ==================== 核心处理器 ====================
def run_account(account_raw: str, index: int) -> Dict[str, Any]:
    logger = Logger()
    parts = account_raw.split('#', 1)
    account_url = parts[0].strip()
    fixed_proxy = parts[1].strip() if len(parts) > 1 else ""
    if fixed_proxy.startswith("proxy="):
        fixed_proxy = fixed_proxy[len("proxy="):]
    
    http = SFHttpClient(fixed_proxy)
    success, user_id, phone = http.login(account_url)
    if not success:
        return {'success': False, 'phone': '未登录账号'}
        
    masked = phone[:3] + "****" + phone[7:] if len(phone) >= 7 else phone
    logger.success(f"账号 [{index + 1}] ➔ 【{masked}】激活认证成功")
    
    result = {'success': True, 'phone': masked, 'index': index, 'points_earned': 0,
              'member_day_prizes': [], 'red_packet_prizes': [], 'coupons': [],
              'welfare_done': []}
    
    if ENABLE_DAILY_TASK:
        logger.task("开始执行日常积分任务（签到 + 做任务 + 领积分）")
        daily = DailyTaskExecutor(http, logger, user_id)
        # 签到前查询当前积分
        daily.query_points()
        daily.logger.points(daily.total_points, "当前积分")
        # 小程序签到
        daily.sign_in()
        time.sleep(1)
        # 签到日历
        daily.new_sign_in()
        time.sleep(1)
        pb, pa = daily.run()
        result['points_earned'] = pa - pb
        result['welfare_done'] = daily.welfare_done
        logger.info(f"日常任务积分变化: {pb} -> {pa} ({(pa - pb):+d})")
        
    if ENABLE_MEMBER_DAY and 26 <= datetime.now().day <= 28:
        md = MemberDayExecutor(http, logger, user_id)
        result['member_day_prizes'] = md.run().get('lottery_prizes', [])

    if ENABLE_RED_PACKET:
        logger.task("开始执行顺丰红包大派送（每天免费抽奖一次）")
        rp = RedPacketExecutor(http, logger)
        result['red_packet_prizes'] = rp.run()

    if ENABLE_COUPON_QUERY:
        logger.task("开始查询我的优惠券")
        cq = CouponQueryExecutor(http, logger)
        result['coupons'] = cq.run()

    return result


def _auto_fetch_cookies() -> List[str]:
    mgr = AutoCookieManager()
    wxids = parse_env_accounts(SF_OPENIDS) if SF_OPENIDS else [a["wxid"] for a in mgr._get_online_accounts()]
    if not wxids:
        return []

    _log_global(f"🔎 顺丰 sf_wxid 解析到 {len(wxids)} 个账号")
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
            _log_global(f"👤 账号[{index}] {mask_account(wxid)} 自动换 Cookie 成功")
            continue

        _log_global(f"❌ 账号[{index}] {mask_account(wxid)} 自动换 Cookie 失败")
        _log_global("   请检查该微信是否在线、是否已授权顺丰、是否绑定手机号")
        if index < len(wxids):
            time.sleep(2)

    _log_global(f"📦 顺丰 Cookie 换取成功 {len(cookies)} / 解析账号 {len(wxids)}")
    return cookies


def dispatch_summary(logger: Logger, results: List[Dict[str, Any]]) -> None:
    """打印并推送所有账号执行结果的汇总报表。"""
    GLOBAL_NOTIFY_BUFFERS.clear()
    GLOBAL_NOTIFY_BUFFERS.extend(results)

    total = len(results)
    success = sum(1 for r in results if r.get("success"))
    failed = total - success
    total_earned = sum(int(r.get("points_earned") or 0) for r in results if r.get("success"))

    lines = [
        "==============================",
        f"🕒 执行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"📊 统计数据：成功 {success} / 总计 {total}",
        f"✅ 成功账号：{success} 个",
        f"❌ 失败账号：{failed} 个",
        f"💰 累计积分：+{total_earned}",
        "==============================",
    ]

    for idx, r in enumerate(results, 1):
        ok = bool(r.get("success"))
        account_icon = "👤"
        account = r.get("phone") or "未知账号"
        lines.extend([
            f"{account_icon} 【账号{idx}】{account}",
            f"{'✅' if ok else '❌'} 状态：{'执行成功' if ok else '执行失败'}",
        ])

        if ok:
            lines.append(f"💰 积分：+{int(r.get('points_earned') or 0)}")
            prizes = r.get("member_day_prizes") or []
            if prizes:
                lines.append(f"🎁 会员日：{', '.join(str(p) for p in prizes)}")
            rp_prizes = r.get("red_packet_prizes") or []
            if rp_prizes:
                lines.append(f"🧧 红包大派送：{', '.join(str(p) for p in rp_prizes)}")
            coupons = r.get("coupons") or []
            if coupons:
                lines.append(f"🎟️ 优惠券统计({len(coupons)}张)：")
                # 列对齐：拆分「名称(¥金额)」与「[有效 ...]」，按最大显示宽度补齐
                parsed = []
                max_w = 0
                for c in coupons:
                    m = re.match(r'(.*?)(\s*\[.*)$', c)
                    prefix, suffix = (m.group(1), m.group(2)) if m else (c, '')
                    parsed.append((prefix, suffix))
                    w = sum(2 if ord(ch) > 0xFFFF or unicodedata.east_asian_width(ch) in ('F', 'W') else 1 for ch in prefix)
                    max_w = max(max_w, w)
                for prefix, suffix in parsed:
                    pw = sum(2 if ord(ch) > 0xFFFF or unicodedata.east_asian_width(ch) in ('F', 'W') else 1 for ch in prefix)
                    lines.append(f"  🎟️ {prefix}{' ' * (max_w - pw)}{suffix}")
        else:
            lines.append(f"⚠️ 原因：{r.get('error') or '登录失效'}")

        lines.append("------------------------------")

    content = "\n".join(lines)
    print("\n[执行报表]\n" + content)

    if send_push_notification is None:
        print("通知发送跳过：未加载 SendNotify.py")
        return
    try:
        send_push_notification(SCRIPT_TITLE, content)
    except Exception as exc:
        print(f"通知发送失败：{exc}")


def main():
    account_list = _auto_fetch_cookies() if SF_AUTO_COOKIE else []
    
    if not account_list:
        results = [{
            "success": False,
            "phone": "-",
            "error": "未捕获到在线顺丰账号凭证，请检查 wx_server_url/WX_SERVER_URL 和 sf_wxid/SF_WXID",
        }]
        dispatch_summary(Logger(), results)
        return 1

    print("==================================================")
    print(f"🎉 顺丰速运任务启动... 共加载 {len(account_list)} 个账户")
    print("==================================================")

    results: List[Dict[str, Any]] = []
    for idx, raw in enumerate(account_list):
        result = run_account(raw, idx)
        if not result.get('success'):
            result.setdefault('error', result.get('phone') or '登录失效')
        results.append(result)
        time.sleep(2)

    dispatch_summary(Logger(), results)
    total_failed = sum(1 for r in results if not r.get("success"))
    return 0 if total_failed == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
