#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Date: 2026.09.21
Description: 小米商城 · 米金任务（YYB 登录版）
Cron: 每天 1 次，建议与「小米商城每日任务赢红包（小米lite.py）」错开时间
----------------------------------------------------------------------------------------------


登录链路：
  GET  {WX_SERVER_URL}/mywc?wxid=...&appId=...              -> 微信 code
  POST {WX_SERVER_URL}/myyhs                                  -> encryptedData/iv/signature/data
  POST account.xiaomi.com/pass/sns/wxapp/v2/code       code  -> wxSToken (+ userInfo cookie)
  POST account.xiaomi.com/pass/sns/wxapp/v3/tokenLogin wxSToken -> passToken / userId
  GET  account.xiaomi.com/pass/serviceLogin                  -> 带 _ssign 的 location
  GET  {location}                                            -> Set-Cookie: serviceToken
  POST xiaomishop.retail.mi.com/mtop/xiaomishop/account/loggedAfter  建立服务端会话

业务接口（URL 与请求体同原脚本；渠道默认 xmsc=小程序）：
  POST shop-api.retail.mi.com/mtop/navi/venue/batch?page_id=13880&pdl=mishop   拉任务列表
  POST shop-api.retail.mi.com/mtop/mf/act/infinite/do                          取 taskToken
  POST shop-api.retail.mi.com/mtop/mf/act/infinite/done                        完成任务领米金

TOKEN 说明：
  缓存 serviceToken = 响应头 Set-Cookie 里 serviceToken 的整段值（不是 JWT 的某一段）。
  抓包对应域名：account.xiaomi.com（换 token）/ xiaomishop.retail.mi.com（建立会话）。
  存放于 cache/cache_mishop_mijin_<ref>.json；文件名与 小米lite.py 的 cache_mishop_<ref>.json
  刻意区分，避免两脚本互相覆盖。

凭证来源（严格自上而下，命中即用，全不命中才走 YYB 取号）：
  ① 本脚本自有缓存 cache/cache_mishop_mijin_<ref>.json（MI_CACHE_HOURS 内有效）
  ② 只读复用 小米lite.py 的 cache/cache_mishop_<ref>.json（MI_CACHE_HOURS 内有效，可关）
     —— lite 跑过 → 直接拿它的 serviceToken，本脚本零 YYB 取号；**绝不写/改/删 lite 的文件**
  ③ MI_TOKEN 人工兜底（最高优先级的人工干预；命中即跳过登录且不写缓存）
  ④ YYB 取号登录（单次，失败不重试）→ 结果写回 ①
  运行时任一层失效 → 清 ①（不碰 ②）→ 单次 YYB 重登 → 重试当前请求（重入锁，绝不循环）

配置（沿用原脚本 MI_ 前缀）：
  1) WX_SERVER_URL / wx_server_url  自建微信网关地址（自动拼接 /mywc、/myyhs）
  2) XIAOMI_WXID / xiaomi_wxid      小米商城小程序 wxid，多账号支持换行 / &, 中英文逗号 / 分号
  3) MI_REFS      本脚本专属账号白名单（留空 = 跑 XIAOMI_WXID 全部）
  4) YYB_SERVER   兼容旧版「地址@ref」配置，仅在新变量未配置时读取
  5) MI_TOKEN     人工兜底 TOKEN（在「自有缓存 + lite 缓存」都不可用时生效，比网关取号优先）
       命中即跳过登录且不写缓存。注意：它是「兜底」而非「覆盖」——有有效缓存时不会用到它，
       所以**不要**把一份过期 token 长期留在这里（会白打一次必败请求），测完请清空。
       ① 登录信息 JSON（含 service_token，可选 openid/user_id/raw_data/signature）
          带 openid 时按 openid 精确匹配；不带 openid / 裸 token 仅在单账号场景生效
       ② 单个裸 serviceToken（仅 YYB_SERVER 只配 1 个账号时生效）
  4) 通知：BARK_PUSH + 全局 NOTIFY_ON_FAIL_ONLY + 本脚本 MI_NOTIFY_ON_FAIL_ONLY（覆盖全局）
  5) 代理（可选，品赞）：PROXY_API / PROXY_TYPE / PLUSPLUS_TOKEN
       不配置则与原脚本一样直连
  6) MI_CACHE_HOURS   缓存有效期（小时，默认 20）。若实测 serviceToken 有效期更长，
       调大此值可显著减少 YYB 取号次数（建议实测后再调）
  7) MI_LOGGED_AFTER  是否执行 loggedAfter 建立服务端会话（默认 1）；设 0 可少取一次 code
  8) MI_DEBUG         调试开关，默认关（正式版零 debug 输出）
  9) MI_TOKEN_FALLBACK_YYB  兜底 MI_TOKEN 运行时失效后是否允许「单次」回落 YYB 登录（默认 1=允许）。
       设 0 → 兜底 token 失效即直接失败、完全不碰 YYB（想零取号时用）。
 10) MI_CHANNEL      业务请求渠道预设（默认 auto）
       auto = 先 xmsc，若被判 401 自动改用 app 重试同一请求，并锁定可用渠道（一次跑出结论）
       xmsc = 小米商城小程序渠道（照抄 小米lite.py，推荐；与 YYB 登录的渠道一致）
       app  = APP 渠道（原「小米商城.py」写法，仅用于对照）
 11) MI_REUSE_LITE_CACHE  是否只读复用 小米lite.py 的缓存（默认 1=开）
       开 → lite 刚跑过就直接用它的 serviceToken，本脚本零 YYB 取号（省一半取号额度）；
            过期/缺失/归属不符则自动跳过，照常走 YYB 登录。本脚本对 lite 的文件严格只读。
       关（0/false/no）→ 完全不复用，行为回到「自有缓存 → MI_TOKEN → YYB」
 12) MI_LITE_CACHE_DIR    手动指定 小米lite.py 的 cache 目录（可选；默认自动探测两个位置：
       与本脚本同级的 cache/、以及上一级的 cache/）。仅在自动探测不到时才需要配。


运行铁律：
  - 登录失败即单次标记失败结束，绝不循环重试 YYB（连续登录大忌）
  - 运行时鉴权失效 → 清当前 openid 缓存 → 单次重登当前账号 → 重试当前请求（单次 + 重入锁）
  - 多账号串行执行 + 账号间随机延迟
  - 只有**真实业务失败**才计失败；「已达任务上限」「今日已完成」都是正常状态，
    不计失败、不推失败通知、不影响退出码（业务级失败必须报，正常状态不许误报）

青龙任务建议：
  名称：小米商城米金任务（YYB）
  命令：task 小米商城米金YYB版.py
"""

import os
import re
import sys
import json
import time
import random
import uuid
import base64
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import quote, urlparse
from threading import Lock

try:
    from Crypto.Cipher import AES
except ImportError:
    AES = None

import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

SCRIPT_TITLE = "小米商城米金任务（YYB）"
GLOBAL_NOTIFY_BUFFERS: List[Dict[str, Any]] = []

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# ==================== 环境变量 / 开关 ====================
VERSION = "1.2.0"          # 版本号唯一来源：日志横幅与文件头说明都引用这里，避免两处漂移
WX_SERVER_URL = (os.getenv("WX_SERVER_URL") or os.getenv("wx_server_url") or "").strip().rstrip("/")
XIAOMI_WXID = os.getenv("XIAOMI_WXID") or os.getenv("xiaomi_wxid") or ""
YYB_SERVER = os.getenv("YYB_SERVER", "")  # 旧版兼容：地址@ref
MI_TOKEN = os.getenv("MI_TOKEN", "")
# 本脚本专属账号白名单（openid，支持换行/逗号/空格分隔）；留空 = 跑 YYB_SERVER 全部账号。
# 注意：这是脚本级开关，只影响本脚本，绝不改动全局共用的 YYB_SERVER。
MI_REFS = os.getenv("MI_REFS", "")
BARK_PUSH = os.getenv("BARK_PUSH", "")
NOTIFY_ON_FAIL_ONLY = os.getenv("NOTIFY_ON_FAIL_ONLY", "true").strip().lower() in ("1", "true", "yes")
MI_NOTIFY_ON_FAIL_ONLY = os.getenv("MI_NOTIFY_ON_FAIL_ONLY", "").strip()   # 留空跟随全局
PROXY_API = os.getenv("PROXY_API", "")
PROXY_TYPE = os.getenv("PROXY_TYPE", "http").lower()
PLUSPLUS_TOKEN = os.getenv("PLUSPLUS_TOKEN", "")
MI_DEBUG = os.getenv("MI_DEBUG", "").strip().lower() in ("1", "true", "yes")
MI_LOGGED_AFTER = os.getenv("MI_LOGGED_AFTER", "1").strip().lower() not in ("0", "false", "no")
# 兜底 MI_TOKEN 运行时失效后，是否允许「单次」回落 YYB 登录（1=允许，默认）。
# 设 0 则兜底 token 失效即直接失败、完全不碰 YYB（想零取号时用）。
MI_TOKEN_FALLBACK_YYB = os.getenv("MI_TOKEN_FALLBACK_YYB", "1").strip().lower() not in ("0", "false", "no")
try:
    MI_CACHE_HOURS = float(os.getenv("MI_CACHE_HOURS", "20") or 20)
except ValueError:
    MI_CACHE_HOURS = 20.0
# 是否只读复用「小米lite.py」的 cache_mishop_<ref>.json（1=开，默认）。
# lite 刚跑过 → 直接用它的 serviceToken，本脚本零 YYB 取号；过期/缺失则照常走 YYB 登录。
# 本脚本对 lite 的缓存严格只读：不写、不改、不删。
MI_REUSE_LITE_CACHE = os.getenv("MI_REUSE_LITE_CACHE", "1").strip().lower() not in ("0", "false", "no")

# ==================== 业务常量（原脚本原样，勿改） ====================
SIGN = "ff8960139490adb9071ed47a34f179ff"
ACT_ID = "6706c0695404a23dfb5b2cab"
XM_API_BASE = "https://shop-api.retail.mi.com"
TASK_LIST_URL = f"{XM_API_BASE}/mtop/navi/venue/batch?page_id=13880&pdl=mishop"
TASK_DO_URL = f"{XM_API_BASE}/mtop/mf/act/infinite/do"
TASK_DONE_URL = f"{XM_API_BASE}/mtop/mf/act/infinite/done"
# 注意：sign 与 parameter 强绑定，字符串内不能有多余空格
TASK_PARAMETER = '{{"actId":"{}","taskTypeList":[101,200,110,201,202]}}'.format(ACT_ID)

TASK_LIST_RESOLVER = "infinite-task"
SKIP_TASK_TYPES = {201}          # 201 需支付，脚本无法完成
BROWSE_TASK_WAIT = 3             # 浏览类任务(200)等待秒数（与原脚本一致）

# ==================== 登录常量（照抄 小米lite.py） ====================
XM_WX_APPID = "wx17ea87763491620f"
XM_PAGE_VER = "1912"
XM_ACCOUNT_BASE = "https://account.xiaomi.com"
XM_SHOP_BASE = "https://xiaomishop.retail.mi.com"
XM_SID = "mieshop_weixin"
XM_CLIENT_ID = "180100041089"
XM_CHANNEL_ID = "1044.1000"
XM_CLIENT_VERSION = "5.21.23"
XM_SIGN_KEY = b"JF)#Poga3_agq638"
XM_SIGN_IV = b"\x00" * 16
PROXY_TIMEOUT = 25

XM_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/144.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) "
    "UnifiedPCWindowsWechat(0xf2541d0c) XWEB/25510"
)
XM_X_USER_AGENT = "channel/mishop platform/mishop.wxlite"
XM_REFERER = f"https://servicewechat.com/{XM_WX_APPID}/{XM_PAGE_VER}/page-frame.html"

# 服务端返回"已完成/重复"类提示
# 「今日已完成」类（正常状态，不算失败）。
# 务必收窄：早先写成 r"已|重复|完成|done" 会把「活动已结束/已过期/已达上限」这类**失败**文案
# 全部误判成"已完成"而静默跳过（2026-09-20 离线测试抓到）。
DONE_PATTERN = re.compile(r"已完成|已经完成|已完成过|重复领取|重复参与|重复|task[_ ]?done|already[_ ]?done", re.I)
# 服务端返回鉴权失效类信号
AUTH_PATTERN = re.compile(
    # 注意：小米 mtop 实际返回的是「用户未登陆」(陆)，与「未登录」(录) 是两种写法，两个都必须认，
    # 否则失效信号被漏判 → 既不重登也不计失败。
    r"授权过期|Token过期|token过期|Token失效|未登录|未登陆|登录失效|登陆失效|登录过期|登陆过期"
    r"|用户信息失效|请重新登录|请重新登陆|登录异常|登陆异常|账号异常"
    r"|expired|unauthorized|invalid[_ ]?token|not[_ ]?login",
    re.I,
)
# mtop 网关的「未登录」业务码（HTTP 仍是 200，只能从 body 的 code 判断）
AUTH_CODES = {"401"}

# 服务端返回「已达任务上限」类提示 —— **这是正常状态，不是错误**。
# 含义：该号今日的米金任务已经做完了（10/10 全返回它），再跑也拿不到米金。
CAP_PATTERN = re.compile(
    r"达到任务上限|已达任务上限|任务已达上限|达到上限|已达上限"
    r"|超出上限|已领完|已抢完|已抢光|额度已满|次数已用完",
    re.I,
)
# mtop 的「已达任务上限」业务码
CAP_CODES = {"200001"}
# 连续这么多个任务都返回「已达任务上限」→ 判定本号今日已完成，提前结束剩余任务（省无谓请求）
CAPPED_EARLY_STOP_STREAK = 3

# ==================== 缓存路径 ====================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(SCRIPT_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

print_lock = Lock()


# ==================== 日志 ====================
def _log_global(msg: str) -> None:
    with print_lock:
        try:
            print(msg, flush=True)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "utf-8"
            print(msg.encode(enc, errors="ignore").decode(enc, errors="ignore"), flush=True)


def log_info(msg: str) -> None:
    _log_global(f"📝 {msg}")


def log_ok(msg: str) -> None:
    _log_global(f"✅ {msg}")


def log_warn(msg: str) -> None:
    _log_global(f"⚠️ {msg}")


def log_err(msg: str) -> None:
    _log_global(f"❌ {msg}")


def log_debug(msg: str) -> None:
    if MI_DEBUG:
        _log_global(f"🐞 {msg}")


# ==================== 工具 ====================
def mask_account(value: Any) -> str:
    value = str(value or "")
    if len(value) <= 12:
        return value
    return f"{value[:6]}...{value[-4:]}"


def _try_json_loads(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text or text[0] not in "[{":
        return value
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return value


def find_nested_value(data: Any, keys: set) -> Any:
    data = _try_json_loads(data)
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key) in keys and value not in (None, "", 0):
                return value
        for value in data.values():
            found = find_nested_value(value, keys)
            if found not in (None, "", 0):
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_nested_value(item, keys)
            if found not in (None, "", 0):
                return found
    return None


def find_nested_object(data: Any, keys: set) -> Optional[Dict[str, Any]]:
    data = _try_json_loads(data)
    if isinstance(data, dict):
        if data.get("encryptedData") not in (None, ""):
            return data
        for value in data.values():
            found = find_nested_object(value, keys)
            if found:
                return found
        if any(data.get(key) not in (None, "") for key in keys):
            return data
    elif isinstance(data, list):
        for item in data:
            found = find_nested_object(item, keys)
            if found:
                return found
    return None


# ==================== 异常 ====================
class AuthExpiredError(Exception):
    """登录态失效：触发单次重登恢复。"""


class TaskCappedError(Exception):
    """已达任务上限 = 该号今日已完成（正常状态）。

    不算失败、不计入失败推送、不触发渠道切换、也不触发重新登录。
    """


class BusinessError(Exception):
    """业务级失败（不触发重登）。"""


class TransientNetworkError(Exception):
    """网络/5xx 抖动（不触发重登）。"""


# ==================== 代理（品赞，照抄 小米lite.py） ====================
class ProxyManager:
    def __init__(self, api_url: str):
        self.api_url = api_url

    def get_proxy(self) -> Optional[Dict[str, str]]:
        try:
            if not self.api_url:
                return None
            response = requests.get(self.api_url, timeout=10)
            if response.status_code == 200:
                proxy_text = response.text.strip()
                parts = proxy_text.split()
                if len(parts) == 3:
                    ip_port, account, password = parts[0], parts[1], parts[2]
                    proxy_text = f"http://{account}:{password}@{ip_port}"
                if ":" in proxy_text:
                    proxy = proxy_text if proxy_text.startswith(("http://", "https://")) else f"http://{proxy_text}"
                    display_proxy = proxy
                    if "@" in proxy:
                        seg = proxy.split("@")
                        if len(seg) == 2:
                            display_proxy = f"http://***:***@{seg[1]}"
                    _log_global(f"🌐 成功获取代理: {display_proxy}")
                    return {"http": proxy, "https": proxy}
            _log_global(f"❌ 获取代理失败: {response.text[:80]}")
            return None
        except Exception as e:
            _log_global(f"❌ 获取代理异常: {str(e)[:80]}")
            return None


proxy_manager = ProxyManager(PROXY_API)


def parse_fixed_proxy(fixed_proxy: str) -> Optional[Dict[str, str]]:
    if not fixed_proxy:
        return None
    if "://" not in fixed_proxy:
        fixed_proxy = f"{PROXY_TYPE}://{fixed_proxy}"
    return {"http": fixed_proxy, "https": fixed_proxy}


# ==================== AutoCookieManager（照抄 小米lite.py） ====================
def gen_sign_token(user_id: str, url: str) -> str:
    """生成 sign_token（AES-128-CBC，密钥=XM_SIGN_KEY，IV=全零）；明文 userId_ts_random_url"""
    if AES is None:
        log_warn("未安装 pycryptodome，sign_token 置空（仅影响 loggedAfter）")
        return ""
    plaintext = f"{user_id}_{int(time.time() * 1000)}_{random.randint(0, 999)}_{url}"
    data = plaintext.encode("utf-8")
    pad_len = 16 - len(data) % 16
    data += bytes([pad_len]) * pad_len
    cipher = AES.new(XM_SIGN_KEY, AES.MODE_CBC, iv=XM_SIGN_IV)
    return quote(base64.b64encode(cipher.encrypt(data)).decode("utf-8"), safe="")


class AutoCookieManager:
    """通过应用宝网关 /wxapp/getCode 拿微信 code，再走小米账号中心 4 步换取 serviceToken。"""

    def __init__(self, wx_server: Optional[str] = None, fixed_proxy: str = ""):
        self.wx_server = (wx_server or "").strip().rstrip("/")
        if not self.wx_server:
            raise ValueError("AutoCookieManager 需要 wx_server（来自 YYB_SERVER 的地址）")
        self.session = requests.Session()
        self.session.verify = False
        self._fixed_proxy = parse_fixed_proxy(fixed_proxy) if fixed_proxy else None
        self.device_id = self._new_device_id()

    # ---------- 基础 ----------
    @staticmethod
    def _new_device_id() -> str:
        """小程序 deviceId 格式：wp_<uuid4>_<4位随机>"""
        return "wp_" + str(uuid.uuid4()) + "%04d" % random.randint(0, 9999)

    def _headers(self, form: bool = True) -> Dict[str, str]:
        h = {
            "User-Agent": XM_UA,
            "xweb_xhr": "1",
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
            "Referer": XM_REFERER,
        }
        if form:
            h["Content-Type"] = "application/x-www-form-urlencoded"
        return h

    def _set_cookie(self, name: str, value: str, domain: str = "account.xiaomi.com") -> None:
        """设置 session cookie（让 requests 自动合并，避免 headers Cookie 覆盖 jar）"""
        self.session.cookies.set(name, value, domain=domain, path="/")

    def _request(self, method: str, url: str, **kwargs) -> Optional[Any]:
        kwargs.setdefault("timeout", PROXY_TIMEOUT)
        kwargs.setdefault("proxies", self._fixed_proxy or None)
        try:
            return self.session.request(method, url, **kwargs)
        except Exception as e:
            _log_global(f"❌ 请求异常 {url.split('?')[0]}: {str(e)[:80]}")
            return None

    # ---------- 网关取 code（单次，不重试） ----------
    def _get_wx_code(self, wxid: str, max_retries: int = 1) -> Optional[str]:
        """通过自建 /mywc 网关获取微信 code，默认单次调用。"""
        if not self.wx_server:
            _log_global("❌ 未配置 WX_SERVER_URL，无法请求 /mywc")
            return None
        url = f"{self.wx_server}/mywc"
        for attempt in range(max_retries):
            try:
                r = self.session.get(
                    url,
                    params={"wxid": wxid, "appId": XM_WX_APPID},
                    headers={"auth": wxid,
                             "User-Agent": "Mozilla/5.0 MicroMessenger/8.0.50"},
                    timeout=30,
                    proxies=self._fixed_proxy or None,
                )
                j = r.json()
                code = find_nested_value(j, {"code", "loginCode", "wxcode"})
                if code and str(code) not in {"0", "200"}:
                    log_debug(f"获取 code 成功: {str(code)[:10]}***")
                    return str(code).strip()
                if attempt < max_retries - 1:
                    time.sleep((attempt + 1) * 3)
                    continue
                _log_global(f"❌ {mask_account(wxid)}: 获取 code 失败 resp={str(j)[:160]}")
                return None
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep((attempt + 1) * 3)
                    continue
                _log_global(f"❌ {mask_account(wxid)}: 获取 code 异常 {str(e)[:80]}")
                return None
        return None

    # ---------- 网关取微信加密信封 ----------
    def _get_wx_user_info(self, wxid: str) -> Optional[Dict]:
        """通过 /myyhs 透传 getUserInfo 云函数，兼容嵌套 JSON 返回。"""
        if not self.wx_server:
            return None
        url = f"{self.wx_server}/myyhs"
        payload = {"wxid": wxid, "appId": XM_WX_APPID,
                   "data": {"api_name": "getUserInfo", "data": {}, "env": 1}}
        try:
            r = self.session.post(
                url,
                json=payload,
                headers={"Content-Type": "application/json",
                         "User-Agent": "Mozilla/5.0 MicroMessenger/8.0.50"},
                timeout=30,
                proxies=self._fixed_proxy or None,
            )
            j = r.json()
            result = find_nested_object(j, {"encryptedData", "iv", "signature", "rawData", "data"})
            if result and result.get("encryptedData"):
                log_debug("获取微信加密信封成功")
                return result
            log_warn(f"获取微信加密信封失败: {str(j)[:160]}")
            return None
        except Exception as e:
            log_warn(f"获取微信加密信封异常: {str(e)[:80]}")
            return None

    # ---------- code 换取 wxSToken ----------
    def _wxapp_code(self, code: str, wx_user_info: Optional[Dict] = None) -> Optional[Dict]:
        """POST /pass/sns/wxapp/v2/code  响应含 wxSToken + 小米用户信息"""
        url = f"{XM_ACCOUNT_BASE}/pass/sns/wxapp/v2/code"
        data = {"code": code, "appid": XM_WX_APPID, "sid": XM_SID,
                "userInfo": "true", "_locale": "zh_CN"}
        self._set_cookie("deviceId", self.device_id)
        r = self._request("POST", url, headers=self._headers(), data=data)
        if r is None:
            return None
        try:
            j = r.json()
        except Exception:
            _log_global(f"❌ v2/code 响应解析失败: {r.text[:120]}")
            return None
        if j.get("code") != 0:
            _log_global(f"❌ v2/code 失败: {j.get('desc') or j.get('description') or str(j)[:120]}")
            return None
        d = j.get("data") or {}
        if not d.get("wxSToken"):
            _log_global(f"❌ v2/code 未返回 wxSToken: {str(j)[:160]}")
            return None
        wx_stoken = d["wxSToken"]
        self._set_cookie("wxSToken", wx_stoken)
        if wx_user_info:
            try:
                cookie_ui = {
                    "cloudID": wx_user_info.get("cloud_id", ""),
                    "encryptedData": wx_user_info.get("encryptedData", ""),
                    "iv": wx_user_info.get("iv", ""),
                    "signature": wx_user_info.get("signature", ""),
                    "userInfo": json.loads(wx_user_info.get("data") or "{}"),
                    "rawData": wx_user_info.get("data", ""),
                    "errMsg": "getUserInfo:ok",
                }
                self._set_cookie("userInfo", quote(json.dumps(cookie_ui, ensure_ascii=False), safe=""))
            except Exception as e:
                log_debug(f"构造 userInfo cookie 异常: {str(e)[:80]}")
        return d

    # ---------- wxSToken 换取 passToken ----------
    def _token_login(self, wx_stoken: str) -> Optional[Dict]:
        """POST /pass/sns/wxapp/v3/tokenLogin  需带 wxSToken/userInfo cookie"""
        url = f"{XM_ACCOUNT_BASE}/pass/sns/wxapp/v3/tokenLogin"
        data = {"sid": XM_SID, "appid": XM_WX_APPID, "callback": "", "authType": "1",
                "wxSToken": wx_stoken, "_locale": "zh_CN"}
        r = self._request("POST", url, headers=self._headers(), data=data, allow_redirects=False)
        if r is None:
            return None
        try:
            j = r.json()
        except Exception:
            loc = r.headers.get("Location", "")
            _log_global(f"❌ tokenLogin 响应非JSON(HTTP {r.status_code} "
                        f"{r.headers.get('Content-Type')}) location={loc[:80]} body={r.text[:80]}")
            return None
        if j.get("code") != 0 or not j.get("passToken"):
            _log_global(f"❌ tokenLogin 失败: {j.get('desc') or str(j)[:160]}")
            return None
        return j

    # ---------- passToken 换取 serviceLogin location ----------
    def _service_login(self, pass_token: str, user_id: Any) -> Optional[str]:
        """GET /pass/serviceLogin  响应体前缀 &&&START&&&，取 location"""
        url = f"{XM_ACCOUNT_BASE}/pass/serviceLogin?sid={XM_SID}&_json=true&_locale=zh_CN"
        self._set_cookie("passToken", pass_token)
        self._set_cookie("userId", str(user_id))
        r = self._request("GET", url, headers=self._headers(form=False))
        if r is None:
            return None
        try:
            payload = r.text.split("&&&START&&&")[-1].strip()
            j = json.loads(payload)
        except Exception:
            _log_global(f"❌ serviceLogin 响应解析失败: {r.text[:160]}")
            return None
        if j.get("code") != 0:
            _log_global(f"❌ serviceLogin 失败: {j.get('desc') or str(j)[:160]}")
            return None
        location = j.get("location")
        if not location:
            _log_global(f"❌ serviceLogin 未返回 location: {str(j)[:160]}")
            return None
        return location

    # ---------- location 换取 serviceToken ----------
    def _get_service_token(self, location: str) -> Optional[str]:
        """GET {location}  -> Set-Cookie: serviceToken"""
        r = self._request("GET", location, headers=self._headers(form=False))
        if r is None:
            return None
        token = self._pick_cookie("serviceToken")
        if not token:
            _log_global(f"❌ wx/sts 未下发 serviceToken（HTTP {r.status_code}）")
            return None
        return token

    # ---------- loggedAfter 建立服务端会话 ----------
    def _logged_after(self, service_token: str, user_id: str,
                      wx_code: str, wx_user_info: Optional[Dict]) -> bool:
        """POST /mtop/xiaomishop/account/loggedAfter，建立 app.m.mi.com 的服务端会话。"""
        url = f"{XM_SHOP_BASE}/mtop/xiaomishop/account/loggedAfter"

        st_enc = quote(service_token, safe="")
        raw_data = (wx_user_info or {}).get("data", "")
        signature = (wx_user_info or {}).get("signature", "")
        raw_data_b64 = base64.b64encode(raw_data.encode("utf-8")).decode("utf-8") if raw_data else ""

        sign_token = gen_sign_token(user_id, "mtop/xiaomishop/account/loggedAfter")

        cookie = (
            f"client_id={XM_CLIENT_ID};channel_id={XM_CHANNEL_ID};"
            f"serviceToken={st_enc};nickName=;avatar=;lat=;lng=;"
            f"share_user=;wx_business_user=;share_channel=;"
            f"platform=windows;model=microsoft;brand=microsoft;"
            f"version=4.1.13.12;system=Windows 11 x64;SDKVersion=3.17.2;"
            f"mishop_wx_version={XM_CLIENT_VERSION};masid=;gdt_vid=;weixinadinfo=;"
            f"user_token={service_token};sign_token={sign_token};"
            f"rawData={raw_data_b64};signature={signature};"
            f"rebate_invite_code=;mstuid=;client_version={XM_CLIENT_VERSION};"
        )

        wx_user_info_b64 = ""
        if raw_data:
            try:
                obj = json.loads(raw_data)
                obj["userId"] = int(user_id) if user_id else 0
                wx_user_info_b64 = base64.b64encode(
                    json.dumps(obj, ensure_ascii=False).encode()
                ).decode()
            except Exception:
                pass

        body = json.dumps([{}, {
            "wxCode": wx_code,
            "wxEncryptedData": (wx_user_info or {}).get("encryptedData", ""),
            "wxIv": (wx_user_info or {}).get("iv", ""),
            "wxUserInfo": wx_user_info_b64,
        }], ensure_ascii=False)

        headers = {
            "User-Agent": XM_UA,
            "x-user-agent": XM_X_USER_AGENT,
            "cookie": cookie,
            "Content-Type": "application/json;charset=UTF-8",
            "Accept": "*/*",
            "Referer": XM_REFERER,
        }
        try:
            r = self.session.post(url, data=body, headers=headers,
                                  timeout=PROXY_TIMEOUT, proxies=self._fixed_proxy or None)
            j = r.json()
            if j.get("code") == 0:
                return True
            log_warn(f"loggedAfter 返回: {j.get('message') or str(j)[:100]}")
        except Exception as e:
            log_warn(f"loggedAfter 异常: {str(e)[:80]}")
        return False

    def _pick_cookie(self, name: str) -> str:
        """从 session cookiejar 中取指定 cookie（同名取 domain 最长者）"""
        best = None
        for c in self.session.cookies:
            if c.name == name:
                if best is None or len(c.domain or "") > len(best.domain or ""):
                    best = c
        return best.value if best else ""

    # ---------- 主入口 ----------
    def get_ck_for_wxid(self, wxid: str) -> Optional[Dict]:
        code = self._get_wx_code(wxid, max_retries=1)
        if not code:
            return None

        wx_user_info = self._get_wx_user_info(wxid)

        d1 = self._wxapp_code(code, wx_user_info)
        if not d1:
            return None
        wx_stoken = d1["wxSToken"]
        info = (d1.get("userInfo") or {})

        d2 = self._token_login(wx_stoken)
        if not d2:
            return None

        location = self._service_login(d2["passToken"], d2.get("userId"))
        if not location:
            return None

        service_token = self._get_service_token(location)
        if not service_token:
            return None

        log_debug(f"登录成功 serviceToken:{service_token[:16]}... userId:{d2.get('userId')}")

        if MI_LOGGED_AFTER:
            new_code = self._get_wx_code(wxid, max_retries=1)
            if new_code:
                self._logged_after(service_token, str(d2.get("userId") or ""), new_code, wx_user_info)
            else:
                log_warn("loggedAfter 跳过（未取到第二个 code）")

        return {
            "service_token": service_token,
            "user_id": str(d2.get("userId") or ""),
            "nick_name": info.get("nickname") or "",
            "phone": info.get("phone") or "",
            "openid": wxid,
            "raw_data": (wx_user_info or {}).get("data", ""),
            "signature": (wx_user_info or {}).get("signature", ""),
        }


# ==================== 业务客户端 ====================
# 渠道预设（/mf/act/infinite/do 会校验「请求渠道」与「token 签发渠道」是否匹配）：
#   xmsc = 小米商城小程序渠道： 小米lite.py 的 XmscHttpClient
#          （x-user-agent=channel/mishop platform/mishop.wxlite + 微信 UA/Referer + 完整 cookie 集：
#            client_id/channel_id/URL 编码的 serviceToken/user_token/sign_token/rawData/signature）
#          经证据链：lite 用**同一个 do 接口、同一套 YYB token** 每天正常跑通 → 这是已验证可用的写法。
#   app  = APP 渠道：原「小米商城.py」写法（okhttp UA + platform/mishop.android + 只发 serviceToken）
CHANNELS = ("xmsc", "app")
CHANNEL_MODE = (os.getenv("MI_CHANNEL", "auto") or "auto").strip().lower()   # auto / xmsc / app


class MishopClient:
    """商城 mtop 请求客户端。

    实测：YYB 登录走的是微信小程序渠道（sid=mieshop_weixin），
    只发 APP 渠道头去打 /mf/act/infinite/do 会被判 {"code":401,"msg":"用户未登陆"}；
    换用小程序渠道（xmsc 预设）才与该 token 的签发渠道一致。
    """

    APP_UA = "okhttp/3.12.3"
    APP_X_USER_AGENT = "channel/mishop platform/mishop.android"

    def __init__(self, service_token: str, user_id: Any = "", raw_data: str = "",
                 signature: str = "", fixed_proxy: str = "", mode: str = ""):
        self.service_token = service_token
        self.user_id = str(user_id or "")
        self.raw_data = raw_data or ""
        self.signature = signature or ""
        self._raw_data_b64 = ""
        if self.raw_data:
            self._raw_data_b64 = base64.b64encode(self.raw_data.encode("utf-8")).decode("utf-8")

        self.mode = (mode or CHANNEL_MODE).lower()
        if self.mode not in ("auto",) + CHANNELS:
            self.mode = "auto"
        self.channel = "xmsc" if self.mode == "auto" else self.mode

        self.session = requests.Session()
        self.session.verify = False
        self.proxy_display = "无代理"
        self._setup_proxy(fixed_proxy)

    def _setup_proxy(self, fixed_proxy: str) -> None:
        if fixed_proxy:
            proxy_dict = parse_fixed_proxy(fixed_proxy)
            if proxy_dict:
                self.session.proxies = proxy_dict
                self.proxy_display = f"***@{fixed_proxy.split('@')[-1]}" if "@" in fixed_proxy else fixed_proxy
                return
        proxy = proxy_manager.get_proxy()
        if proxy:
            self.session.proxies = proxy
            self.proxy_display = "API代理"

    # ---------- 小程序渠道 cookie（照抄 小米lite.py 的 XmscHttpClient._build_cookie） ----------
    def _build_xmsc_cookie(self, url: str) -> str:
        path = urlparse(url).path
        for prefix in ("/v2/", "/v3/"):
            if path.startswith(prefix):
                path = path[len(prefix):]
                break
        path = path.lstrip("/")

        sign_token = gen_sign_token(self.user_id, path)
        st_encoded = quote(self.service_token, safe="")
        return (
            f"client_id={XM_CLIENT_ID};channel_id={XM_CHANNEL_ID};"
            f"serviceToken={st_encoded};"
            f"nickName=;avatar=;lat=;lng=;"
            f"share_user=;wx_business_user=;share_channel=;"
            f"platform=windows;model=microsoft;brand=microsoft;"
            f"version=4.1.13.12;system=Windows 11 x64;SDKVersion=3.17.2;"
            f"mishop_wx_version={XM_CLIENT_VERSION};masid=;gdt_vid=;weixinadinfo=;"
            f"user_token={self.service_token};"
            f"sign_token={sign_token};"
            f"rawData={self._raw_data_b64};signature={self.signature};"
            f"rebate_invite_code=;mstuid=;"
            f"client_version={XM_CLIENT_VERSION};"
        )

    def _headers(self, url: str) -> Dict[str, str]:
        if self.channel == "xmsc":
            return {
                "User-Agent": XM_UA,
                "x-user-agent": XM_X_USER_AGENT,
                "cookie": self._build_xmsc_cookie(url),
                "Content-Type": "application/json",
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Referer": XM_REFERER,
            }
        # app：原「小米商城.py」写法（保持原样，仅用于 A/B 对照）
        return {
            "x-user-agent": self.APP_X_USER_AGENT,
            "Content-Type": "application/json",
            "User-Agent": self.APP_UA,
            "Cookie": f"serviceToken={self.service_token}; ",
        }

    def _request_once(self, url: str, body: str) -> Tuple[Dict[str, Any], str]:
        """返回 (data, 失效信号)。失效信号非空表示这轮被判失效。"""
        resp = self.session.post(url, headers=self._headers(url), data=body, timeout=15)

        if resp.status_code in (401, 403):
            return {}, f"HTTP {resp.status_code}"
        if resp.status_code >= 500:
            raise TransientNetworkError(f"HTTP {resp.status_code}")

        text = resp.text or ""
        try:
            data = resp.json()
        except Exception:
            if AUTH_PATTERN.search(text[:300]):
                return {}, f"响应非 JSON 且含失效信号：{text[:120]}"
            raise TransientNetworkError(f"响应非 JSON：{text[:120]}")

        log_debug(f"POST {url.split('?')[0]} [{self.channel}] -> {json.dumps(data, ensure_ascii=False)[:300]}")

        msg = ""
        code = None
        if isinstance(data, dict):
            msg = str(data.get("msg") or data.get("message") or data.get("desc") or "")
            code = data.get("code")
        # 失效信号：文案（含"未登陆"变体）或 mtop code=401（HTTP 为 200，只能看 body）
        if (msg and AUTH_PATTERN.search(msg)) or (code is not None and str(code) in AUTH_CODES):
            return {}, f"code={code} {msg}".strip()
        # 「已达任务上限」= 该号今日已完成（正常状态）：直接抛，绝不当失效去切渠道/重登
        if (msg and CAP_PATTERN.search(msg)) or (code is not None and str(code) in CAP_CODES):
            raise TaskCappedError(f"code={code} {msg}".strip())
        return (data if isinstance(data, dict) else {}), ""

    def post(self, url: str, payload: Any) -> Dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False)
        net_retry = 0
        switched = False
        while True:
            try:
                data, sig = self._request_once(url, body)
            except TransientNetworkError:
                if net_retry < 1:
                    net_retry += 1
                    time.sleep(1.5)
                    continue
                raise

            if not sig:
                if switched:
                    # A/B 出结论后锁定，避免后续请求来回切渠道
                    self.mode = self.channel
                    log_warn(f"渠道 A/B 结论：{self.channel} 渠道可用，本次运行锁定该渠道")
                return data

            if self.mode == "auto" and not switched:
                switched = True
                old = self.channel
                self.channel = "app" if old == "xmsc" else "xmsc"
                log_warn(f"渠道 {old} 被判失效（{sig}）→ 自动改用 {self.channel} 渠道重试同一请求")
                continue

            raise AuthExpiredError(sig)


# ==================== 业务逻辑（语义与原脚本一致） ====================
def fetch_tasks(client: MishopClient, tag: str) -> Optional[List[Dict[str, Any]]]:
    payload = {
        "query_list": [
            {
                "resolver": TASK_LIST_RESOLVER,
                "sign": SIGN,
                "parameter": TASK_PARAMETER,
                "variable": {},
            }
        ]
    }
    data = client.post(TASK_LIST_URL, payload)
    if data.get("message") != "ok":
        raise BusinessError(f"获取任务列表失败：{data.get('message') or '未知错误'}")
    comps = ((data.get("data") or {}).get("result_list") or [{}])[0].get("components") or []
    tasks: List[Dict[str, Any]] = []
    for comp in comps:
        if not comp.get("canDo", True):
            continue
        tasks.append({
            "taskId": comp.get("taskId"),
            "taskName": comp.get("taskName") or "未知任务",
            "taskType": int(comp.get("taskType") or 0),
        })
    _log_global(f"🎯 {tag} 共获取到 {len(tasks)} 个可执行任务")
    return tasks


def request_task_token(client: MishopClient, task_id: Any, task_name: str, tag: str) -> Tuple[Optional[str], str]:
    """返回 (taskToken, 原因)。

    原因约定："" = 成功；"__done__" = 今日已完成；"__capped__" = 已达任务上限
    （后两者都是正常状态，不计失败）；其它为真实失败原因。
    """
    try:
        data = client.post(TASK_DO_URL, [{}, {"taskId": task_id, "actId": ACT_ID}])
    except TaskCappedError as e:
        _log_global(f"⏭️ {tag} [{task_name}] 已达任务上限（本号今日已完成，非错误）（{e}）")
        return None, "__capped__"
    token = (data.get("data") or {}).get("taskToken")
    if token:
        return token, ""
    msg = str(data.get("msg") or data.get("message") or "")
    code = data.get("code")
    if DONE_PATTERN.search(msg):
        _log_global(f"⏭️ {tag} [{task_name}] 今日已完成，跳过")
        return None, "__done__"
    reason = f"code={code} {msg}".strip() if code is not None else (msg or "未知错误")
    log_warn(f"{tag} [{task_name}] 获取 token 失败：{reason}")
    return None, reason


def complete_task(client: MishopClient, token: str, task_type: int, task_name: str, tag: str) -> Tuple[float, bool, str]:
    """返回 (米金, 是否成功, 原因)；原因为 "__capped__" = 已达任务上限（正常状态，不计失败）"""
    try:
        data = client.post(TASK_DONE_URL, [{}, {"taskToken": token, "actId": ACT_ID, "taskType": task_type}])
    except TaskCappedError as e:
        _log_global(f"⏭️ {tag} [{task_name}] 已达任务上限（奖励已领完，非错误）（{e}）")
        return 0.0, False, "__capped__"
    if not data.get("success", False):
        msg = str(data.get("msg") or data.get("message") or "未知错误")
        code = data.get("code")
        reason = f"code={code} {msg}".strip() if code is not None else msg
        log_warn(f"{tag} [{task_name}] 执行失败：{reason}")
        return 0.0, False, reason

    awards = (data.get("data") or {}).get("awardList") or []
    mijin = 0.0
    for award in awards:
        if not isinstance(award, dict):
            continue
        # 小程序端奖项名可能落在 customAwardName（照抄 小米lite.py 的取值顺序）
        name = str(award.get("customAwardName") or award.get("awardName") or "")
        if "米金" in name:
            try:
                mijin += float(award.get("awardValue") or 0)
            except (TypeError, ValueError):
                pass
    if mijin > 0:
        _log_global(f"✅ {tag} [{task_name}] 执行成功，获得米金：{mijin}")
    else:
        _log_global(f"✅ {tag} [{task_name}] 执行成功，无米金奖励")
    return mijin, True, ""


def _capped_streak_hit(streak: int, summary: Dict[str, Any], tag: str) -> bool:
    """连续多个任务都提示「已达任务上限」→ 判定本号今日已完成，提前结束剩余任务。"""
    if streak < CAPPED_EARLY_STOP_STREAK or summary["ok"] or summary["failed"]:
        return False
    _log_global(f"⏭️ {tag} 连续 {streak} 个任务均提示「已达任务上限」，"
                f"判定本号今日已完成，提前结束剩余任务（避免无谓请求）")
    return True


def run_business(client: MishopClient, tag: str) -> Dict[str, Any]:
    """返回本次执行明细：mijin / 任务级成功失败计数 / 首个失败原因 / 是否鉴权类失败。

    注意：
      - 鉴权类失败（AuthExpiredError）不在这里吞掉，直接向上抛，交给上层做单次重登恢复。
      - 「已达任务上限」(capped) 是正常状态（该号今日已完成），单独计数，**绝不进 failed**。
    """
    summary: Dict[str, Any] = {
        "mijin": 0.0, "attempted": 0, "ok": 0, "failed": 0,
        "skipped": 0, "capped": 0, "first_error": "", "auth_failed": 0,
    }

    tasks = fetch_tasks(client, tag)
    if not tasks:
        _log_global(f"📝 {tag} 无可用任务，获得米金：0")
        return summary

    capped_streak = 0
    for task in tasks:
        tid, tname, ttype = task["taskId"], task["taskName"], task["taskType"]
        if ttype in SKIP_TASK_TYPES:
            _log_global(f"⏭️ {tag} [{tname}] 跳过（类型{ttype}，需支付）")
            summary["skipped"] += 1
            continue

        _log_global(f"📝 {tag} 开始执行任务：[{tname}] (任务ID: {tid}，类型: {ttype})")
        summary["attempted"] += 1

        token, why = request_task_token(client, tid, tname, tag)
        if not token:
            if why == "__done__":
                summary["skipped"] += 1
                capped_streak = 0
            elif why == "__capped__":
                summary["capped"] += 1
                capped_streak += 1
            else:
                summary["failed"] += 1
                summary["auth_failed"] += 1 if AUTH_PATTERN.search(why) else 0
                summary["first_error"] = summary["first_error"] or why
                capped_streak = 0
            if _capped_streak_hit(capped_streak, summary, tag):
                break
            continue

        if ttype == 200:
            _log_global(f"📝 {tag} [{tname}] 为浏览类任务，等待 {BROWSE_TASK_WAIT} 秒...")
            time.sleep(BROWSE_TASK_WAIT)

        mijin, ok, why = complete_task(client, token, ttype, tname, tag)
        if ok:
            summary["mijin"] += mijin
            summary["ok"] += 1
            capped_streak = 0
        elif why == "__capped__":
            summary["capped"] += 1
            capped_streak += 1
        else:
            summary["failed"] += 1
            summary["auth_failed"] += 1 if AUTH_PATTERN.search(why) else 0
            summary["first_error"] = summary["first_error"] or why
            capped_streak = 0
        time.sleep(1)  # 避免请求过快（原脚本一致）

        if _capped_streak_hit(capped_streak, summary, tag):
            break

    summary["mijin"] = round(summary["mijin"], 2)
    _log_global(f"📝 {tag} 本次任务执行完成，总计获得米金：{summary['mijin']}")
    if summary["failed"]:
        _log_global(f"📝 {tag} 任务明细：成功 {summary['ok']} / 失败 {summary['failed']} "
                    f"/ 跳过 {summary['skipped']}（首个失败原因：{summary['first_error']}）")
    elif summary["capped"]:
        _log_global(f"📝 {tag} 任务明细：成功 {summary['ok']} / 已达任务上限 {summary['capped']} "
                    f"（本号今日已完成，非错误）")
    return summary


# ==================== 缓存 / 兜底 TOKEN ====================
def cache_path(ref: str) -> str:
    safe = re.sub(r"[^\w.-]", "_", ref)
    # 与 小米lite.py 的 cache_mishop_<ref>.json 区分，避免互相覆盖
    return os.path.join(CACHE_DIR, f"cache_mishop_mijin_{safe}.json")


def load_cookie(ref: str) -> Optional[Dict]:
    p = cache_path(ref)
    if not os.path.exists(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        ts = d.get("__ts", 0)
        if time.time() - ts > MI_CACHE_HOURS * 3600:
            log_debug(f"[缓存] {mask_account(ref)} 缓存已过期")
            return None
        return d
    except Exception:
        return None


def save_cookie(ref: str, info: Dict) -> None:
    p = cache_path(ref)
    try:
        info = dict(info)
        info["__ts"] = int(time.time())
        with open(p, "w", encoding="utf-8") as f:
            json.dump(info, f, ensure_ascii=False)
        log_debug(f"[缓存] {mask_account(ref)} 登录态已缓存（{MI_CACHE_HOURS:g}h）")
    except Exception as e:
        log_warn(f"[缓存] 写入失败: {e}")


def clear_cookie(ref: str) -> None:
    p = cache_path(ref)
    try:
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        pass


# ---------- 复用 小米lite.py 的缓存（严格只读：不写、不改、不删对方文件） ----------
def _lite_cache_dirs() -> List[str]:
    """小米lite.py 的 cache 目录可能落在的位置（按优先级；都找不到则本层自动跳过）。"""
    cands: List[str] = []
    env_dir = os.getenv("MI_LITE_CACHE_DIR", "").strip()
    if env_dir:
        cands.append(env_dir)
    cands.append(CACHE_DIR)                                          # 与 lite 同目录（青龙 code/ 下最常见）
    cands.append(os.path.join(os.path.dirname(SCRIPT_DIR), "cache"))  # 本脚本在子目录时的上一级
    out: List[str] = []
    for d in cands:
        if d and d not in out:
            out.append(d)
    return out


def lite_cache_path(ref: str) -> Optional[str]:
    """返回已存在的 lite 缓存文件路径（文件名与 lite 的 cache_path 完全一致）。"""
    safe = re.sub(r"[^\w.-]", "_", ref)
    name = f"cache_mishop_{safe}.json"
    for d in _lite_cache_dirs():
        p = os.path.join(d, name)
        if os.path.exists(p):
            return p
    return None


def load_lite_cookie(ref: str) -> Optional[Dict]:
    """只读复用 小米lite.py 的 cache_mishop_<ref>.json。

    用途：lite 与本脚本同后端、同 YYB 登录链路，lite 刚跑过就有一份新鲜 serviceToken，
    直接拿来用即可**零 YYB 取号**。缓存过期/缺失/归属不符 → 返回 None，上层照常走 YYB 登录。
    注意：本函数绝不修改 lite 的文件；返回的凭证也不会回写本脚本缓存。
    """
    if not MI_REUSE_LITE_CACHE:
        return None
    p = lite_cache_path(ref)
    if not p:
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception as e:
        log_debug(f"[lite缓存] 读取失败：{e}")
        return None
    if not isinstance(d, dict) or not d.get("service_token"):
        log_debug("[lite缓存] 缺少 service_token，已忽略")
        return None
    owner = d.get("openid") or d.get("ref")
    if owner and owner != ref:
        log_warn(f"[lite缓存] {mask_account(ref)} 归属不匹配（文件里是 {mask_account(owner)}），已忽略")
        return None
    try:
        age_h = (time.time() - float(d.get("__ts") or 0)) / 3600.0
    except (TypeError, ValueError):
        age_h = 1e9
    if age_h > MI_CACHE_HOURS:
        log_debug(f"[lite缓存] {mask_account(ref)} 已过期（{age_h:.1f}h）")
        return None
    d["openid"] = owner or ref
    d["_age_h"] = round(age_h, 2)     # 仅供日志展示；本层凭证从不回写磁盘
    return d


def apply_direct_token(ref: str, account_total: int) -> Optional[Dict]:
    """MI_TOKEN 兜底（最高优先级，命中即跳过登录且不写缓存）。"""
    raw = (MI_TOKEN or "").strip()
    if not raw:
        return None
    if raw.startswith("{"):
        try:
            d = json.loads(raw)
        except Exception:
            log_warn("MI_TOKEN JSON 解析失败")
            return None
        if not isinstance(d, dict) or not d.get("service_token"):
            log_warn("MI_TOKEN JSON 缺少 service_token 字段")
            return None
        owner = d.get("openid") or d.get("ref")
        if owner:
            # 带 openid：按归属精确匹配，多账号下也只作用于对应账号
            if owner != ref:
                return None
            d.setdefault("openid", ref)
            return d
        # 不带 openid：多账号时无法确定归属，拒绝（否则会让所有账号用同一个人的凭证）
        if account_total != 1:
            log_warn("MI_TOKEN JSON 未带 openid 但配置了多个账号，已忽略（请补上 openid）")
            return None
        d["openid"] = ref
        return d
    # 裸 token：仅单账号场景可用（多账号无法确定归属，避免错配）
    if account_total != 1:
        log_warn("MI_TOKEN 为裸 token 但配置了多个账号，已忽略（请改用 JSON 带 openid）")
        return None
    return {"service_token": raw, "user_id": "", "openid": ref}


# ==================== 单账号任务（三层登录 + 运行时失效恢复） ====================
class AccountTask:
    def __init__(self, server: str, ref: str, idx: int, total: int):
        self.server = server
        self.ref = ref
        self.idx = idx
        self.total = total
        self._proxy: str = ""
        self._recovered: bool = False
        self._cred_source: str = ""       # cache / lite / token / yyb
        self.result: Dict[str, Any] = {
            "success": False,
            "masked_phone": mask_account(ref),
            "index": idx,
            "mijin": 0.0,
            "tasks_attempted": 0,
            "tasks_ok": 0,
            "tasks_failed": 0,
            "tasks_capped": 0,      # 「已达任务上限」计数（正常状态，非失败）
            "error": "",
        }

    def log(self, msg: str, icon: str = "🔧") -> None:
        _log_global(f"{icon} [账号{self.idx}/{self.total}] {msg}")

    # ---------- 四层登录来源（由上到下）：自有缓存 → lite 缓存复用 → MI_TOKEN → YYB 登录 ----------
    def _load_credential(self) -> Optional[Dict]:
        info = load_cookie(self.ref)
        if info:
            self.log("使用缓存登录态", "💾")
            self._cred_source = "cache"
            return info
        info = load_lite_cookie(self.ref)
        if info:
            self.log(f"复用 小米lite.py 缓存登录态（{info.get('_age_h', 0)} 小时前登录，零 YYB 取号）", "🔗")
            self._cred_source = "lite"
            return info
        info = apply_direct_token(self.ref, self.total)
        if info:
            self.log("使用兜底 MI_TOKEN 登录态（不写缓存）", "🔑")
            self._cred_source = "token"
            return info
        return None

    def _login_by_yyb(self) -> Optional[Dict]:
        """单次 YYB 取号登录（失败不重试，连续登录大忌）。"""
        try:
            self.log(f"[YYB] 请求 code: {self.server} (ref={mask_account(self.ref)})", "🔐")
            mgr = AutoCookieManager(wx_server=self.server, fixed_proxy=self._proxy)
            info = mgr.get_ck_for_wxid(self.ref)
            if info and info.get("service_token"):
                self.log("YYB 登录成功", "✨")
                return info
            self.log("YYB 登录失败（未取得 serviceToken）", "❌")
        except Exception as e:
            self.log(f"YYB 登录异常: {str(e)[:100]}", "❌")
        return None

    # ---------- 业务执行 ----------
    def _do_business(self, info: Dict) -> Dict[str, Any]:
        client = MishopClient(
            info.get("service_token", ""),
            user_id=info.get("user_id", ""),
            raw_data=info.get("raw_data", ""),
            signature=info.get("signature", ""),
            fixed_proxy=self._proxy,
        )
        self.log(f"代理: {client.proxy_display} | 渠道: {client.channel}"
                 f"（MI_CHANNEL={client.mode}）", "🌐")
        return run_business(client, f"[账号{self.idx}/{self.total}]")

    def _apply_summary(self, summary: Dict[str, Any]) -> None:
        """把任务级明细落到结果里；只有**真实任务失败**才判账号失败。

        「已达任务上限」= 该号今日已完成（正常状态），不计 failed、不判失败、不推失败通知
        （2026-09-20 实测：code=200001「达到任务上限」，曾被误报成「10/10 个任务全部失败」）。
        """
        self.result["mijin"] = summary.get("mijin", 0.0)
        self.result["tasks_attempted"] = summary.get("attempted", 0)
        self.result["tasks_ok"] = summary.get("ok", 0)
        self.result["tasks_failed"] = summary.get("failed", 0)
        self.result["tasks_capped"] = summary.get("capped", 0)
        attempted, failed = summary.get("attempted", 0), summary.get("failed", 0)
        if attempted and failed >= attempted:
            self.result["success"] = False
            self.result["error"] = (f"{failed}/{attempted} 个任务全部失败："
                                    f"{summary.get('first_error') or '未知原因'}")
            self.log(self.result["error"], "❌")
        else:
            self.result["success"] = True
            if self.result["tasks_capped"] and not self.result["tasks_ok"]:
                self.log(f"本号今日已达任务上限（{self.result['tasks_capped']} 个任务无需执行），非错误", "ℹ️")

    # ---------- 主流程 ----------
    def run(self) -> Dict[str, Any]:
        info = self._load_credential()
        if not info:
            info = self._login_by_yyb()
            if info:
                self._cred_source = "yyb"
                save_cookie(self.ref, info)

        if not info:
            self.result["error"] = "登录失败（未取得 serviceToken）"
            self.log("所有登录方式均失败", "❌")
            return self.result

        try:
            self._apply_summary(self._do_business(info))
            return self.result
        except AuthExpiredError as e:
            if self._recovered:
                self.result["error"] = f"登录态失效且重登后仍失败（{e}）"
                self.log("重登后仍失效", "❌")
                return self.result
            if self._cred_source == "token" and not MI_TOKEN_FALLBACK_YYB:
                self.result["error"] = f"兜底 MI_TOKEN 已失效，MI_TOKEN_FALLBACK_YYB=0 故不回落 YYB（{e}）"
                self.log(self.result["error"], "❌")
                return self.result
            # 单次重登恢复（重入锁防递归，不违反连续登录大忌）
            self._recovered = True
            if self._cred_source == "token":
                self.log("兜底 MI_TOKEN 已失效 → 单次回落 YYB 登录（MI_TOKEN_FALLBACK_YYB=0 可关闭）", "🔁")
            elif self._cred_source == "lite":
                self.log("复用的 lite 缓存已失效 → 单次回落 YYB 登录（lite 文件不动，仅写本脚本缓存）", "🔁")
            self.log(f"登录态失效，单次重登恢复（{e}）", "🔁")
            clear_cookie(self.ref)
            info2 = self._login_by_yyb()
            if not info2:
                self.result["error"] = "登录态失效且重登失败"
                self.log("重登失败", "❌")
                return self.result
            save_cookie(self.ref, info2)
            self._cred_source = "yyb"
            try:
                self._apply_summary(self._do_business(info2))
                if self.result["success"]:
                    self.log("重登后恢复成功", "✨")
                return self.result
            except Exception as e2:
                self.result["error"] = f"重登后仍失败（{str(e2)[:80]}）"
                return self.result
        except BusinessError as e:
            self.result["error"] = str(e)
            self.log(f"业务失败：{e}", "❌")
            return self.result
        except TransientNetworkError as e:
            self.result["error"] = str(e)
            self.log(f"网络失败：{e}", "❌")
            return self.result
        except Exception as e:
            self.result["error"] = f"异常（{str(e)[:80]}）"
            self.log(f"执行异常：{str(e)[:120]}", "❌")
            return self.result


# ==================== 解析 / 通知 ====================
def parse_yyb_accounts(raw: str) -> List[Tuple[str, str]]:
    """兼容解析旧版地址@ref配置，支持换行、逗号和分号。"""
    out: List[Tuple[str, str]] = []
    for line in re.split(r"[\r\n,，;；]+", raw or ""):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "@" not in line:
            log_warn(f"配置行无效（缺少 @）: {line[:40]}")
            continue
        server, ref = line.split("@", 1)
        server = server.strip().rstrip("/")
        ref = ref.strip()
        # 先校验再补协议头：否则 "@ref" 这类行会被补成 "http://" 混进账号列表
        if not server or not ref:
            log_warn(f"配置行无效（地址或 ref 为空）: {line[:40]}")
            continue
        # 归一化协议头（裸 IP:端口会让 requests 抛 No connection adapters）
        if not re.match(r"^https?://", server, re.IGNORECASE):
            server = "http://" + server
        if not re.match(r"^https?://[^/\s]+", server, re.IGNORECASE):
            log_warn(f"配置行地址无效: {line[:40]}")
            continue
        out.append((server, ref))
    return out


def mask_ref(ref: str) -> str:
    """openid 脱敏，仅用于日志"""
    ref = (ref or "").strip()
    if not ref:
        return "-"
    if len(ref) <= 10:
        return ref
    return f"{ref[:6]}***{ref[-4:]}"


def parse_ref_whitelist(raw: str) -> List[str]:
    """MI_REFS 解析：支持换行 / 逗号(中英) / 分号 / 空格 分隔"""
    return [x for x in re.split(r"[\s,，;；]+", (raw or "").strip()) if x]


def parse_wxids(raw: str) -> List[str]:
    return [x.strip() for x in re.split(r"[\r\n&,，,;；]+", raw or "") if x.strip()]


def apply_ref_filter(accounts: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """按 MI_REFS 白名单过滤账号。

    留空 → 原样返回全部（行为与改造前一致）。
    命中为空 → 返回空列表，由调用方报错退出；**绝不回退成跑全量**（避免误刷其他账号的取号额度）。
    """
    wanted = parse_ref_whitelist(MI_REFS)
    if not wanted:
        return accounts

    index = {ref: (server, ref) for server, ref in accounts}
    kept: List[Tuple[str, str]] = []
    missing: List[str] = []
    for item in wanted:
        hit = index.get(item)
        if hit is None and len(item) >= 8:
            cands = [v for k, v in index.items() if k.endswith(item)]
            if len(cands) == 1:
                hit = cands[0]
                log_warn(f"MI_REFS「{mask_ref(item)}」按唯一尾匹配到 {mask_ref(hit[1])}")
        if hit is None:
            missing.append(item)
            continue
        if hit not in kept:
            kept.append(hit)

    for item in missing:
        log_warn(f"MI_REFS 中的 {mask_ref(item)} 不在 YYB_SERVER 列表里，已忽略")
    skipped = [ref for ref in index if (index[ref] not in kept)]
    if skipped:
        log_warn(f"MI_REFS 白名单生效：跳过 {len(skipped)} 个账号 "
                 f"({', '.join(mask_ref(r) for r in skipped[:5])}{' 等' if len(skipped) > 5 else ''})，"
                 f"本次仅跑 {len(kept)} 个（YYB_SERVER 全局配置未改动）")
    return kept


def _notify_only_fail() -> bool:
    if MI_NOTIFY_ON_FAIL_ONLY != "":
        return MI_NOTIFY_ON_FAIL_ONLY.strip().lower() in ("1", "true", "yes")
    return NOTIFY_ON_FAIL_ONLY


def push_bark(title: str, content: str) -> bool:
    """BARK_PUSH 归一化：api.day.app → /push；自建域名缺 /push 自动补；带端口不补。"""
    if not BARK_PUSH:
        _log_global("未配置 BARK_PUSH，跳过推送")
        return False
    endpoint = BARK_PUSH.strip()
    if re.match(r"^https?://api\.day\.app/?$", endpoint):
        endpoint = "https://api.day.app/push"
    elif not endpoint.endswith("/push") and not re.search(r":\d+/", endpoint):
        endpoint = endpoint.rstrip("/") + "/push"
    ok = False
    chunks = [content[i:i + 4000] for i in range(0, len(content), 4000)] or [""]
    for i, chunk in enumerate(chunks):
        t = f"{title} ({i + 1})" if len(chunks) > 1 else title
        try:
            response = requests.post(endpoint, json={"title": t, "body": chunk, "group": title, "isArchive": 1},
                                     timeout=10, verify=False)
            if 200 <= response.status_code < 300:
                ok = True
            else:
                log_warn(f"推送失败（HTTP {response.status_code}）：{response.text[:80]}")
        except Exception as e:
            log_warn(f"推送异常：{str(e)[:80]}")
    if ok:
        _log_global("📮 消息推送成功")
    return ok


def notify(results: List[Dict[str, Any]]) -> None:
    fails = [r for r in results if not r.get("success")]
    # 失败一律推送（业务级失败不可因其他账号成功而被静默吞掉）
    if not fails and _notify_only_fail():
        return
    capped_cnt = sum(1 for r in results if r.get("tasks_capped") and not r.get("tasks_ok"))
    lines = [f"账号总数：{len(results)}", f"成功：{len(results) - len(fails)}", f"失败：{len(fails)}"]
    if capped_cnt:
        lines.append(f"（其中 {capped_cnt} 个今日已达任务上限，属正常状态）")
    lines.append("")
    for idx, r in enumerate(results, 1):
        acct = r.get("masked_phone") or "未知账号"
        detail = ""
        if r.get("tasks_capped") and not r.get("tasks_ok"):
            detail = "（今日已达任务上限，无需重跑）"
        elif r.get("tasks_attempted"):
            detail = f"（任务 {r.get('tasks_ok', 0)}/{r.get('tasks_attempted')} 成功）"
        if r.get("success"):
            lines.append(f"【账号{idx}】{acct} ✅ 米金 {r.get('mijin', 0)}{detail}")
        else:
            lines.append(f"【账号{idx}】{acct} ❌ {r.get('error') or '未知失败'}{detail}")
    title = f"{SCRIPT_TITLE}失败" if fails else f"{SCRIPT_TITLE}完成"
    push_bark(title, "\n".join(lines))


def append_notify_result(result: Dict[str, Any]) -> None:
    GLOBAL_NOTIFY_BUFFERS.append(result)


def dispatch_notify() -> None:
    if GLOBAL_NOTIFY_BUFFERS:
        try:
            from SendNotify import send_push_notification
        except ImportError:
            notify(GLOBAL_NOTIFY_BUFFERS)
            return
        fails = [item for item in GLOBAL_NOTIFY_BUFFERS if not item.get("success")]
        if not fails and _notify_only_fail():
            return
        content_lines = [
            "==============================",
            f"🕒 执行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"📊 统计数据：成功 {len(GLOBAL_NOTIFY_BUFFERS) - len(fails)} / 总计 {len(GLOBAL_NOTIFY_BUFFERS)}",
            f"✅ 成功账号：{len(GLOBAL_NOTIFY_BUFFERS) - len(fails)} 个",
            f"❌ 失败账号：{len(fails)} 个",
            "==============================",
        ]
        for index, item in enumerate(GLOBAL_NOTIFY_BUFFERS, 1):
            status = "执行成功" if item.get("success") else "执行失败"
            content_lines.append(f"🧑‍💻 【账号{index}】{item.get('masked_phone') or '-'}")
            content_lines.append(f"{'✅' if item.get('success') else '❌'} 状态：{status}")
            if item.get("success"):
                content_lines.append(f"💰 米金：{item.get('mijin', 0)}")
            else:
                content_lines.append(f"🧨 原因：{item.get('error') or '未知失败'}")
            content_lines.append("------------------------------")
        try:
            send_push_notification(SCRIPT_TITLE, "\n".join(content_lines))
        except Exception as exc:
            log_warn(f"SendNotify 推送异常：{str(exc)[:80]}")


# ==================== 主程序 ====================
def main() -> int:
    started = time.time()
    _log_global("==============================")
    _log_global(f"🚀 小米商城 · 米金任务（YYB 登录版）v{VERSION}")
    _log_global("==============================")

    global proxy_manager
    proxy_manager = ProxyManager(PROXY_API)

    accounts = ([(WX_SERVER_URL, wxid) for wxid in parse_wxids(XIAOMI_WXID)]
                if WX_SERVER_URL else [])
    if not accounts:
        accounts = parse_yyb_accounts(YYB_SERVER)
    if not accounts:
        if not (MI_TOKEN or "").strip():
            log_err("未配置 WX_SERVER_URL/XIAOMI_WXID，且无 MI_TOKEN 兜底")
            append_notify_result({"success": False, "masked_phone": "-", "error": "未配置微信网关和账号，且无 MI_TOKEN"})
            dispatch_notify()
            return 1
        log_warn("未配置微信网关和账号，仅使用兜底 MI_TOKEN 执行")
        manual_ref = "manual"
        if (MI_TOKEN or "").strip().startswith("{"):
            try:
                token_data = json.loads(MI_TOKEN)
                manual_ref = str(token_data.get("openid") or token_data.get("ref") or manual_ref)
            except (TypeError, ValueError):
                pass
        accounts = [("", manual_ref)]
    else:
        all_cnt = len(accounts)
        accounts = apply_ref_filter(accounts)
        if not accounts:
            wl = parse_ref_whitelist(MI_REFS)
            shown = ", ".join(mask_ref(x) for x in wl[:3]) + (" 等" if len(wl) > 3 else "")
            log_err(f"MI_REFS 白名单（{shown}）未命中 YYB_SERVER 里任何账号，已中止"
                    f"（不会回退成跑全部 {all_cnt} 个账号）")
            append_notify_result({"success": False, "masked_phone": "-", "error": "MI_REFS 白名单未命中任何账号"})
            dispatch_notify()
            return 1

    _log_global(f"📱 共配置 {len(accounts)} 个账号")
    if parse_ref_whitelist(MI_REFS):
        _log_global("📝 MI_REFS 白名单已生效（仅本脚本；YYB_SERVER 全局配置未改动）")
    if not MI_LOGGED_AFTER:
        _log_global("📝 MI_LOGGED_AFTER=0：跳过 loggedAfter（登录少取一次 code）")
    _log_global(f"📝 缓存有效期：{MI_CACHE_HOURS:g} 小时")
    if MI_REUSE_LITE_CACHE:
        _hit = any(
            p.startswith("cache_mishop_") and p.endswith(".json")
            for d in _lite_cache_dirs()
            for p in (os.listdir(d) if os.path.isdir(d) else [])
        )
        if _hit:
            _log_global("📝 复用 小米lite.py 缓存：已开启（仅只读，命中即零 YYB 取号）")
        else:
            _log_global("📝 复用 小米lite.py 缓存：已开启但未发现 lite 缓存文件（本次将走 YYB 登录）")

    results: List[Dict[str, Any]] = []
    for idx, (server, ref) in enumerate(accounts, 1):
        task = AccountTask(server, ref, idx, len(accounts))
        _pd = proxy_manager.get_proxy()          # 每账号取品赞代理 IP（隔离风控）
        if _pd:
            task._proxy = list(_pd.values())[0]
        r = task.run()
        results.append(r)
        append_notify_result(r)
        if idx < len(accounts):
            d = random.uniform(10, 18)           # 账号间随机延迟，避免并发打服务
            _log_global(f"⏳ 等待 {d:.1f} 秒后执行下一个账号...")
            time.sleep(d)

    total_mijin = round(sum(float(r.get("mijin") or 0) for r in results), 2)
    ok_cnt = sum(1 for r in results if r.get("success"))
    fail_cnt = len(results) - ok_cnt
    cost = round(time.time() - started, 2)

    if _notify_only_fail() and fail_cnt == 0:
        _log_global(f"📝 MI_NOTIFY_ON_FAIL_ONLY 生效，成功静默"
                    f"（全局 NOTIFY_ON_FAIL_ONLY={'开' if NOTIFY_ON_FAIL_ONLY else '关'}）")

    _log_global("=" * 62)
    _log_global("【最终统计】")
    _log_global(f"账号总数：{len(results)}   成功：{ok_cnt}   失败：{fail_cnt}")
    capped_cnt = sum(1 for r in results if r.get("tasks_capped") and not r.get("tasks_ok"))
    if capped_cnt:
        _log_global(f"（其中 {capped_cnt} 个账号今日已达任务上限，属正常状态，未计入失败）")
    for r in results:
        if r.get("success"):
            if r.get("tasks_capped") and not r.get("tasks_ok"):
                detail = "  今日已达任务上限（无需执行）"
            elif r.get("tasks_attempted"):
                detail = f"  任务 {r.get('tasks_ok', 0)}/{r.get('tasks_attempted')} 成功"
            else:
                detail = ""
            _log_global(f"  [{r.get('masked_phone')}] 米金 {r.get('mijin', 0)}{detail}")
        else:
            detail = (f"  任务 {r.get('tasks_failed', 0)}/{r.get('tasks_attempted', 0)} 失败"
                      if r.get("tasks_attempted") else "")
            _log_global(f"  [{r.get('masked_phone')}] 失败：{r.get('error')}{detail}")
    _log_global(f"合计米金：{total_mijin}")
    _log_global(f"耗时：{cost} 秒   完成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    _log_global("=" * 62)

    dispatch_notify()
    return 0 if fail_cnt == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
