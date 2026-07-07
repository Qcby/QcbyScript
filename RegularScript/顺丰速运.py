#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
顺丰速运 v1.2.0（青龙多账号聚合推送版）

功能：自动执行顺丰速运日常积分任务与会员日活动，支持签到、做任务、领积分、会员日抽奖、红包合成与提取，多账号执行结束后统一聚合推送。

配置说明：
1. 账号变量：
   sfsyUrl                                           顺丰 Cookie 或登录 URL 变量
   - 单账号格式：CK值或登录URL[#代理地址] 可以通过https://sm.linzixuan.top/  扫码获取ck
   - 示例：sessionId=xxx;_login_mobile_=13800138000;_login_user_id_=xxx#http://127.0.0.1:1080
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔

2. 代理变量：
   SF_PROXY_API_URL                                  动态代理提取链接，可选
   SF_PROXY_TYPE                                     代理类型：http 或 socks5，默认 socks5

3. 任务开关：
   SFSY_PUSH                                         推送开关，1 开启，0 关闭，默认 1
   ENABLE_DAILY_TASK                                 日常积分任务，需在脚本配置区修改
   ENABLE_MEMBER_DAY                                 会员日活动，需在脚本配置区修改
   CONCURRENT_NUM                                    并发数量，需在脚本配置区修改

4. 推送变量：
   需要同目录存在 SendNotify.py，脚本结束后会统一调用 send_push_notification。
   常用推送变量如下，配置任意一种即可：
   QYWX_KEY                                         企业微信机器人 key
   PUSH_PLUS_TOKEN                                  PushPlus token
   PUSH_KEY                                         Server 酱 key
   DD_BOT_TOKEN 或 DD_BOT_SECRET                     钉钉机器人 token/secret
   FSKEY                                            飞书机器人 key

5. 青龙任务建议：
   名称：顺丰速运
   命令：python3 顺丰速运.py
   定时：11 6-18/3 * * *
"""

import hashlib
import json
import os
import random
import re
import time
import threading
from datetime import datetime
from typing import Dict, List, Optional, Any, Tuple
from urllib.parse import unquote, urlparse, parse_qs, quote as url_encode
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import requests
from requests.packages.urllib3.exceptions import InsecureRequestWarning

requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

SCRIPT_TITLE = "顺丰速运"
GLOBAL_NOTIFY_BUFFERS = []
PUSH_SWITCH = os.getenv("SFSY_PUSH", "1")

# ==================== 配置区域 ====================
# 功能开关 (True=开启, False=关闭)
ENABLE_DAILY_TASK = True         # 日常积分任务 (签到+做任务+领积分)
ENABLE_MEMBER_DAY = True         # 会员日活动 (每月26-28号自动执行)
CONCURRENT_NUM = 1               # 并发数量 (1~20)

TOKEN = 'wwesldfs29aniversaryvdld29'
inviteId = []
SYS_CODE = 'MCS-MIMP-CORE'

# 日常任务跳过列表
DAILY_SKIP_TASKS = [
    '用行业模板寄件下单', '用积分兑任意礼品', '参与积分活动',
    '每月累计寄件', '完成每月任务', '去使用AI寄件',
]

# 会员日跳过任务类型
MEMBER_DAY_SKIP_TASK_TYPES = [
    'SEND_SUCCESS', 'INVITEFRIENDS_PARTAKE_ACTIVITY', 'OPEN_SVIP',
    'OPEN_NEW_EXPRESS_CARD', 'OPEN_FAMILY_CARD', 'CHARGE_NEW_EXPRESS_CARD',
    'INTEGRAL_EXCHANGE',
]

# 代理配置
PROXY_API_URL = os.getenv("SF_PROXY_API_URL", "")
PROXY_TYPE = os.getenv("SF_PROXY_TYPE", "socks5")
PROXY_TIMEOUT = 15
MAX_PROXY_RETRIES = 5
REQUEST_RETRY_COUNT = 3
PROXY_RETRY_DELAY = 2
PROXY_CONTEXT = {'last_fetch_ts': 0}
PROXY_LOCK = threading.Lock()
print_lock = Lock()
# =================================================


class Logger:
    def __init__(self):
        self.messages: List[str] = []
        self.lock = Lock()

    def _log(self, icon: str, msg: str):
        line = f"{icon} {msg}"
        with print_lock:
            print(line)
        with self.lock:
            self.messages.append(line)

    def info(self, msg): self._log('📝', msg)
    def success(self, msg): self._log('✅', msg)
    def warning(self, msg): self._log('⚠️', msg)
    def error(self, msg): self._log('❌', msg)
    def task(self, msg): self._log('🎯', msg)
    def medal(self, msg): self._log('🏅', msg)
    def points(self, pts, prefix="当前积分"): self._log('💰', f"{prefix}: 【{pts}】")




# ==================== 聚合通知 ====================
def parse_accounts(raw: str) -> List[str]:
    return [item.strip() for item in re.split(r"[&，,\r\n]+", str(raw or "")) if item.strip()]


def mask_phone_value(phone: str) -> str:
    phone = str(phone or "")
    if len(phone) >= 11:
        return f"{phone[:3]}****{phone[7:]}"
    if len(phone) >= 7:
        return f"{phone[:3]}****{phone[-2:]}"
    return phone or "未登录"


def mask_sf_account(account_raw: str) -> str:
    decoded = unquote(str(account_raw or ""))
    for part in decoded.split(';'):
        part = part.strip()
        if part.startswith('_login_mobile_='):
            return mask_phone_value(part.split('=', 1)[1])
    if len(decoded) <= 12:
        return decoded or "未知账号"
    return f"{decoded[:6]}***{decoded[-4:]}"


def append_notify_result(result: Dict[str, Any]) -> None:
    GLOBAL_NOTIFY_BUFFERS.append(result)


def build_notify_report() -> str:
    total = len(GLOBAL_NOTIFY_BUFFERS)
    success = sum(1 for item in GLOBAL_NOTIFY_BUFFERS if item.get('success') or item.get('ok'))
    failed = total - success
    total_earned = sum(int(item.get('points_earned') or 0) for item in GLOBAL_NOTIFY_BUFFERS)

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
        ok = bool(item.get('success') or item.get('ok'))
        raw_index = item.get('index', 0)
        try:
            account_index = int(raw_index) + 1
        except Exception:
            account_index = raw_index or '-'
        account = item.get('account') or mask_phone_value(item.get('phone', ''))
        account_icon = "🧑‍💻" if ok else "🧟"
        lines.extend([
            f"{account_icon} 【账号{account_index}】{account}",
            f"{'✅' if ok else '❌'} 状态：{'执行成功' if ok else item.get('status', '执行失败')}",
        ])
        if ok:
            before = int(item.get('points_before') or 0)
            after = int(item.get('points_after') or 0)
            earned = int(item.get('points_earned') or 0)
            lines.append(f"💰 积分：始 {before} ➔ 终 {after}，获得 +{earned}")
            prizes = item.get('member_day_prizes') or []
            if prizes:
                lines.append(f"🎁 会员日：{', '.join(map(str, prizes))}")
            if item.get('proxy_display'):
                lines.append(f"🌐 代理：{item.get('proxy_display')}")
        else:
            lines.append(f"🧨 原因：{item.get('error') or item.get('message') or '登录失败或任务异常'}")
        lines.append("------------------------------")

    return "\n".join(lines)


def dispatch_notify() -> None:
    if PUSH_SWITCH == "0":
        print("ℹ️ 推送开关未开启 (SFSY_PUSH=0)")
        return
    if not GLOBAL_NOTIFY_BUFFERS:
        append_notify_result({
            'index': 0,
            'account': '未获取到账号',
            'success': False,
            'status': '配置错误',
            'error': '未生成任何账号执行结果',
            'points_before': 0,
            'points_after': 0,
            'points_earned': 0,
            'member_day_prizes': [],
        })
    content = build_notify_report()
    try:
        from SendNotify import send_push_notification
        send_push_notification(f"{SCRIPT_TITLE}任务执行结果", content)
        print("✅ 聚合通知推送完成")
    except Exception as exc:
        print(f"❌ 聚合通知推送失败：{exc}")
        print(content)


# ==================== 代理管理器 ====================
def _log_global(msg: str):
    t = datetime.now().strftime("%H:%M:%S")
    print(f"[{t}] {msg}", flush=True)


def _build_proxy_url(ip: str, port: int, username: str = "", password: str = "") -> str:
    """构建标准代理URL，认证信息自动URL编码"""
    if username and password:
        safe_user = url_encode(username, safe='')
        safe_pass = url_encode(password, safe='')
        return f"{PROXY_TYPE}://{safe_user}:{safe_pass}@{ip}:{port}"
    return f"{PROXY_TYPE}://{ip}:{port}"


def parse_proxy_response(text: str) -> Optional[Tuple[str, str]]:
    """解析代理API响应，返回(代理URL, 显示用字符串)，支持JSON和纯文本格式"""
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
    """从API获取代理，返回(代理字典, 显示用字符串)"""
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
    """解析固定代理字符串为代理字典"""
    if not fixed_proxy:
        return None
    if '://' not in fixed_proxy:
        fixed_proxy = f'{PROXY_TYPE}://{fixed_proxy}'
    return {'http': fixed_proxy, 'https': fixed_proxy}


# ==================== HTTP客户端 ====================
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
            if extra_headers:
                headers.update(extra_headers)
            try:
                resp = self.session.post(url, headers=headers, json=data or {}, timeout=PROXY_TIMEOUT)
                resp.raise_for_status()
                try:
                    result = resp.json()
                    if result is not None:
                        return result
                except (json.JSONDecodeError, ValueError):
                    pass
                retry_count += 1
                if retry_count < REQUEST_RETRY_COUNT:
                    time.sleep(2)
                    continue
                return None
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
                if retry_count < REQUEST_RETRY_COUNT:
                    time.sleep(2)
                    continue
                return None
            except Exception:
                return None
        return None

    def request_app(self, url: str, data: Optional[Dict] = None) -> Optional[Dict]:
        """APP平台请求"""
        original = self.headers.get('platform', 'MINI_PROGRAM')
        self.headers['platform'] = 'SFAPP'
        try:
            return self.request(url, data)
        finally:
            self.headers['platform'] = original

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
        except Exception:
            return False, '', ''


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
                if 'taskId' in ug_params:
                    return str(ug_params['taskId'])
            if url.startswith('com.sf-express://'):
                json_str = url.split('_ug_view_param=')[1]
                ug_params = json.loads(unquote(json_str))
                if 'taskId' in ug_params:
                    return str(ug_params['taskId'])
        except Exception:
            pass
        return ''

    def _set_task_attrs(self, task: Dict):
        self.taskId = str(task.get('taskId', ''))
        self.taskCode = str(task.get('taskCode', ''))
        self.strategyId = int(task.get('strategyId', 0))
        self.title = str(task.get('title', '未知任务'))
        self.point = int(task.get('point', 0))
        if not self.taskCode and 'buttonRedirect' in task:
            extracted = self._extract_task_id_from_url(task['buttonRedirect'])
            if extracted:
                self.taskCode = extracted

    def app_sign_in(self) -> Tuple[bool, str]:
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~integralTaskSignPlusService~getUnFetchPointAndDiscount'
        resp = self.http.request_app(url, {})
        if resp and resp.get('success'):
            obj = resp.get('obj', [])
            if obj and isinstance(obj, list) and len(obj) > 0:
                names = [item.get('packetName', '未知') for item in obj]
                self.logger.success(f'[APP签到] 获得【{", ".join(names)}】')
            else:
                self.logger.info('[APP签到] 今日已签到')
            return True, ''
        error_msg = resp.get('errorMessage', '未知错误') if resp else '请求失败'
        if '没有待领取礼包' in error_msg:
            time.sleep(1)
            resp2 = self.http.request_app(url, {})
            if resp2 and resp2.get('success'):
                obj2 = resp2.get('obj', [])
                if obj2 and isinstance(obj2, list) and len(obj2) > 0:
                    names = [item.get('packetName', '未知') for item in obj2]
                    self.logger.success(f'[APP签到] 二次领取【{", ".join(names)}】')
                else:
                    self.logger.info('[APP签到] 今日已签到，无待领取奖励')
                return True, ''
            self.logger.info('[APP签到] 今日已签到')
            return True, ''
        self.logger.error(f'[APP签到] 失败: {error_msg}')
        return False, error_msg

    def sign_in(self) -> Tuple[bool, str]:
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~integralTaskSignPlusService~automaticSignFetchPackage'
        resp = self.http.request(url, {"comeFrom": "vioin", "channelFrom": "WEIXIN"})
        if resp and resp.get('success'):
            obj = resp.get('obj', {})
            count_day = obj.get('countDay', 0)
            packets = obj.get('integralTaskSignPackageVOList', [])
            if packets:
                self.logger.success(f'签到成功，获得【{packets[0].get("packetName", "未知")}】，累计签到【{count_day + 1}】天')
            else:
                self.logger.info(f'今日已签到，累计签到【{count_day + 1}】天')
            return True, ''
        error_msg = resp.get('errorMessage', '未知错误') if resp else '请求失败'
        self.logger.error(f'签到失败: {error_msg}')
        return False, error_msg

    def _format_sign_v2_award(self, obj: Any) -> str:
        if not isinstance(obj, dict):
            return ''
        products = obj.get('award', {}).get('productDTOList', [])
        if isinstance(products, list) and products:
            names = []
            for item in products:
                if not isinstance(item, dict):
                    continue
                name = item.get('productName') or item.get('couponName')
                if name:
                    amount = item.get('amount', 1)
                    names.append(f'{name}x{amount}')
            if names:
                return '、'.join(names)
        award = obj.get('award')
        if isinstance(award, dict):
            return award.get('giftBagName') or award.get('giftBagDesc') or ''
        return obj.get('packetName') or obj.get('giftBagName') or ''

    def _sign_v2(self, platform_type: str) -> bool:
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~integralSignV2Service~sign'
        if platform_type == 'SFAPP':
            name = 'APP'
            headers = {
                'User-Agent': 'Mozilla/5.0 (Linux; Android 12; M2011K2C Build/SKQ1.211006.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/144.0.7559.132 Mobile Safari/537.36 mediaCode=SFEXPRESSAPP-Android-ML',
                'channel': 'doudiappwd',
                'platform': 'SFAPP',
                'deviceid': self.generate_device_id(),
            }
        else:
            name = '小程序'
            headers = {
                'User-Agent': 'Mozilla/5.0 (Linux; Android 12; M2011K2C Build/SKQ1.211006.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/146.0.7680.177 Mobile Safari/537.36 XWEB/1460075 MMWEBSDK/20250804 MMWEBID/4850 MicroMessenger/8.0.63.2920(0x28003FA6) WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64 miniProgram/wxd4185d00bf7e08ac',
                'channel': 'wxwddoudi',
                'platform': 'MINI_PROGRAM',
            }
        resp = self.http.request(url, {}, headers)
        if resp and resp.get('success'):
            award_text = self._format_sign_v2_award(resp.get('obj'))
            if award_text:
                self.logger.success(f'[{name}签到] 成功，获得【{award_text}】')
            else:
                self.logger.success(f'[{name}签到] 成功')
            return True
        error_msg = resp.get('errorMessage', '未知错误') if resp else '请求失败'
        if '今日已签到' in error_msg:
            self.logger.info(f'[{name}签到] 今日已签到')
            return True
        self.logger.error(f'[{name}签到] 失败: {error_msg}')
        return False

    def dual_sign_in(self) -> bool:
        app_ok = self._sign_v2('SFAPP')
        time.sleep(1)
        mini_ok = self._sign_v2('MINI_PROGRAM')
        return app_ok or mini_ok

    def get_task_list(self) -> List[Dict]:
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~integralTaskStrategyService~queryPointTaskAndSignFromES'
        all_tasks = []
        seen = set()
        for ct in ['1', '2', '3', '4', '01', '02', '03', '04']:
            data = {'channelType': ct, 'deviceId': self.generate_device_id()}
            resp = self.http.request(url, data)
            if resp and resp.get('success') and resp.get('obj'):
                if ct == '1':
                    self.total_points = resp['obj'].get('totalPoint', 0)
                for task in resp['obj'].get('taskTitleLevels', []):
                    tc = task.get('taskCode', '')
                    if not tc and 'buttonRedirect' in task:
                        tc = self._extract_task_id_from_url(task['buttonRedirect'])
                        if tc:
                            task['taskCode'] = tc
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
        data = {
            "strategyId": self.strategyId, "taskId": self.taskId,
            "taskCode": self.taskCode, "deviceId": self.generate_device_id()
        }
        resp = self.http.request(url, data)
        if resp and resp.get('success'):
            self.logger.success(f'领取奖励: {self.title}')
            return True
        return False

    def get_welfare_list(self) -> List[Dict]:
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberGoods~mallGoodsLifeService~list'
        data = {"memGrade": 3, "categoryCode": "SHTQ", "showCode": "SHTQWNTJ"}
        resp = self.http.request(url, data)
        if resp and resp.get('success'):
            result = []
            for module in resp.get('obj', []):
                for goods in module.get('goodsList', []):
                    if goods.get('exchangeStatus') == 1:
                        result.append({
                            'goodsNo': goods.get('goodsNo'),
                            'goodsName': goods.get('goodsName'),
                            'showName': goods.get('showName', ''),
                        })
            return result
        return []

    def receive_welfare(self, goods_no: str, goods_name: str) -> bool:
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberGoods~pointMallService~createOrder'
        data = {
            "from": "Point_Mall", "orderSource": "POINT_MALL_EXCHANGE",
            "goodsNo": goods_no, "quantity": 1, "taskCode": self.taskCode
        }
        resp = self.http.request(url, data)
        if resp and resp.get('success'):
            self.logger.success(f'领取特权: {goods_name}')
            return True
        return False

    def handle_welfare_task(self) -> bool:
        welfare_list = self.get_welfare_list()
        if not welfare_list:
            return False
        for w in welfare_list:
            name = f"{w['showName']} - {w['goodsName']}" if w['showName'] else w['goodsName']
            if self.receive_welfare(w['goodsNo'], name):
                return True
            time.sleep(1)
        return False

    def run(self) -> Tuple[int, int]:
        self.logger.info('正在获取日常任务列表...')
        tasks = self.get_task_list()
        if not tasks:
            self.logger.error('获取任务列表失败')
            return 0, 0
        points_before = self.total_points
        self.logger.points(points_before, "执行前积分")
        for task in tasks:
            title = task.get('title', '未知')
            status = task.get('status')
            if status == 3:
                continue
            if title in DAILY_SKIP_TASKS:
                continue
            self._set_task_attrs(task)
            if not self.taskCode:
                if 'buttonRedirect' in task:
                    extracted = self._extract_task_id_from_url(task['buttonRedirect'])
                    if extracted:
                        self.taskCode = extracted
                    else:
                        continue
                else:
                    continue
            self.logger.task(f'发现任务: {title} (状态: {status})')
            if '领任意生活特权福利' in title:
                if self.handle_welfare_task():
                    time.sleep(2)
                    if self.execute_task():
                        time.sleep(2)
                        self.receive_task_reward()
                time.sleep(3)
                continue
            if status == 1:
                if '连签7天' in title and 'process' in task:
                    cur, tot = map(int, task['process'].split('/'))
                    if cur < tot:
                        self.logger.info(f'【{title}】进度: {task["process"]}')
                        continue
                if self.execute_task():
                    self.logger.success(f'[{title}] 提交成功')
                    time.sleep(2)
                    status = 2
                else:
                    continue
            if status == 2:
                if self.receive_task_reward():
                    continue
                if self.execute_task():
                    time.sleep(2)
                    self.receive_task_reward()
            time.sleep(3)
        tasks = self.get_task_list()
        points_after = self.total_points if tasks else points_before
        self.logger.points(points_after, "执行后积分")
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
        self.packet_threshold = 1 << (self.MAX_LEVEL - 1)

    def _check_black(self, error_message: str) -> bool:
        if '没有资格参与活动' in error_message:
            self.black = True
            self.logger.info('会员日任务风控')
            return True
        return False

    def get_index(self) -> Optional[Dict]:
        available = [inv for inv in inviteId if inv != self.user_id]
        invite_user_id = random.choice(available) if available else ''
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~memberDayIndexService~index'
        resp = self.http.request(url, {'inviteUserId': invite_user_id})
        if resp and resp.get('success'):
            return resp.get('obj', {})
        error_msg = resp.get('errorMessage', '无返回') if resp else '请求失败'
        self.logger.info(f'查询会员日失败: {error_msg}')
        self._check_black(error_msg)
        return None

    def receive_invite_award(self, invite_user_id: str):
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~memberDayIndexService~receiveInviteAward'
        resp = self.http.request(url, {'inviteUserId': invite_user_id})
        if resp and resp.get('success'):
            product_name = resp.get('obj', {}).get('productName', '空气')
            self.logger.success(f'会员日邀请奖励: {product_name}')
        else:
            error_msg = resp.get('errorMessage', '无返回') if resp else '请求失败'
            self.logger.info(f'领取会员日邀请奖励失败: {error_msg}')
            self._check_black(error_msg)

    def lottery(self) -> Optional[str]:
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~memberDayLotteryService~lottery'
        resp = self.http.request(url, {})
        if resp and resp.get('success'):
            product_name = resp.get('obj', {}).get('productName', '空气')
            self.logger.success(f'会员日抽奖: {product_name}')
            return product_name
        error_msg = resp.get('errorMessage', '无返回') if resp else '请求失败'
        self.logger.info(f'会员日抽奖失败: {error_msg}')
        self._check_black(error_msg)
        return None

    def get_task_list(self) -> Optional[List[Dict]]:
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~activityTaskService~taskList'
        resp = self.http.request(url, {'activityCode': 'MEMBER_DAY', 'channelType': 'MINI_PROGRAM'})
        if resp and resp.get('success'):
            return resp.get('obj', [])
        error_msg = resp.get('errorMessage', '无返回') if resp else '请求失败'
        self.logger.info(f'查询会员日任务失败: {error_msg}')
        self._check_black(error_msg)
        return None

    def finish_task(self, task: Dict) -> bool:
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberEs~taskRecord~finishTask'
        resp = self.http.request(url, {'taskCode': task['taskCode']})
        if resp and resp.get('success'):
            self.logger.success(f'完成会员日任务[{task["taskName"]}]')
            self.fetch_mix_task_reward(task)
            return True
        error_msg = resp.get('errorMessage', '无返回') if resp else '请求失败'
        self.logger.info(f'完成会员日任务[{task["taskName"]}]失败: {error_msg}')
        self._check_black(error_msg)
        return False

    def fetch_mix_task_reward(self, task: Dict):
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~activityTaskService~fetchMixTaskReward'
        data = {'taskType': task['taskType'], 'activityCode': 'MEMBER_DAY', 'channelType': 'MINI_PROGRAM'}
        resp = self.http.request(url, data)
        if resp and resp.get('success'):
            self.logger.success(f'领取会员日任务[{task["taskName"]}]奖励')
        else:
            error_msg = resp.get('errorMessage', '无返回') if resp else '请求失败'
            self.logger.info(f'领取会员日任务[{task["taskName"]}]奖励失败: {error_msg}')
            self._check_black(error_msg)

    def do_tasks(self):
        tasks = self.get_task_list()
        if not tasks:
            return
        for task in tasks:
            if self.black:
                return
            if task['status'] == 1:
                self.fetch_mix_task_reward(task)
        for task in tasks:
            if self.black:
                return
            if task['status'] == 2:
                if task['taskType'] in MEMBER_DAY_SKIP_TASK_TYPES:
                    continue
                for _ in range(task.get('restFinishTime', 0)):
                    if self.black:
                        return
                    self.finish_task(task)

    def red_packet_status(self):
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~memberDayPacketService~redPacketStatus'
        resp = self.http.request(url, {})
        if not (resp and resp.get('success')):
            error_msg = resp.get('errorMessage', '无返回') if resp else '请求失败'
            self.logger.info(f'查询会员日合成失败: {error_msg}')
            self._check_black(error_msg)
            return
        for packet in resp.get('obj', {}).get('packetList', []):
            self.red_packet_map[packet['level']] = packet['count']
        for level in range(1, self.MAX_LEVEL):
            count = self.red_packet_map.get(level, 0)
            while count >= 2:
                self.red_packet_merge(level)
                count -= 2
        summary = [f"[{lv}级]X{ct}" for lv, ct in self.red_packet_map.items() if ct > 0]
        self.logger.info(f'会员日合成列表: {", ".join(summary)}')
        if self.red_packet_map.get(self.MAX_LEVEL):
            self.logger.success(f'会员日已拥有[{self.MAX_LEVEL}级]红包X{self.red_packet_map[self.MAX_LEVEL]}')
            self.red_packet_draw(self.MAX_LEVEL)
        else:
            remaining_needed = sum(
                1 << (int(lv) - 1) for lv, ct in self.red_packet_map.items()
                if ct > 0 and int(lv) < self.MAX_LEVEL
            )
            remaining = self.packet_threshold - remaining_needed
            self.logger.info(f'会员日距离[{self.MAX_LEVEL}级]红包还差: [1级]红包X{remaining}')

    def red_packet_merge(self, level: int):
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~memberDayPacketService~redPacketMerge'
        resp = self.http.request(url, {'level': level, 'num': 2})
        if resp and resp.get('success'):
            self.logger.success(f'会员日合成: [{level}级]红包X2 -> [{level + 1}级]红包')
            self.red_packet_map[level] -= 2
            self.red_packet_map[level + 1] = self.red_packet_map.get(level + 1, 0) + 1
        else:
            error_msg = resp.get('errorMessage', '无返回') if resp else '请求失败'
            self.logger.info(f'会员日合成[{level}级]红包失败: {error_msg}')
            self._check_black(error_msg)

    def red_packet_draw(self, level: int):
        url = 'https://mcs-mimp-web.sf-express.com/mcs-mimp/commonPost/~memberNonactivity~memberDayPacketService~redPacketDraw'
        resp = self.http.request(url, {'level': str(level)})
        if resp and resp.get('success'):
            coupon_names = [item['couponName'] for item in resp.get('obj', [])] or []
            self.logger.success(f'会员日提取[{level}级]红包: {", ".join(coupon_names) or "空气"}')
        else:
            error_msg = resp.get('errorMessage', '') if resp else '无返回'
            self.logger.info(f'会员日提取[{level}级]红包失败: {error_msg}')
            self._check_black(error_msg)

    def run(self) -> Dict[str, Any]:
        result = {'lottery_prizes': [], 'tasks_done': 0}
        index_info = self.get_index()
        if not index_info or self.black:
            return result
        available = [inv for inv in inviteId if inv != self.user_id]
        invite_user_id = random.choice(available) if available else ''
        if index_info.get('canReceiveInviteAward') and invite_user_id:
            self.receive_invite_award(invite_user_id)
        self.red_packet_status()
        lottery_num = index_info.get('lotteryNum', 0)
        self.logger.info(f'会员日可抽奖 {lottery_num} 次')
        for _ in range(lottery_num):
            if self.black:
                break
            prize = self.lottery()
            if prize:
                result['lottery_prizes'].append(prize)
        if not self.black:
            self.do_tasks()
        if not self.black:
            self.red_packet_status()
        return result


# ==================== 账号执行 ====================
def run_account_core(account_raw: str, index: int) -> Dict[str, Any]:
    logger = Logger()
    if '#' in account_raw and (':' in account_raw.split('#')[-1]):
        last_hash = account_raw.rfind('#')
        account_url = account_raw[:last_hash].strip()
        fixed_proxy = account_raw[last_hash + 1:].strip()
    else:
        account_url = account_raw
        fixed_proxy = ""
    http = SFHttpClient(fixed_proxy)
    login_success = False
    phone = ''
    user_id = ''
    for attempt in range(MAX_PROXY_RETRIES):
        if attempt > 0:
            http = SFHttpClient(fixed_proxy)
        success, user_id, phone = http.login(account_url)
        if success:
            login_success = True
            break
        time.sleep(2)
    if not login_success:
        logger.error(f'账号{index + 1} 登录失败')
        return {'success': False, 'phone': '', 'index': index,
                'points_before': 0, 'points_after': 0, 'points_earned': 0,
                'member_day_prizes': [], 'error': '登录失败'}
    masked = phone[:3] + "****" + phone[7:] if len(phone) >= 7 else phone
    logger.success(f'账号{index + 1}: 【{masked}】登录成功 | 🌐 {http.proxy_display}')
    time.sleep(random.uniform(1, 3))
    result = {
        'success': True, 'phone': phone, 'index': index,
        'points_before': 0, 'points_after': 0, 'points_earned': 0,
        'member_day_prizes': [],
        'proxy_display': http.proxy_display,
    }
    # 日常积分任务
    if ENABLE_DAILY_TASK:
        logger.info('━━━ 日常积分任务 ━━━')
        daily = DailyTaskExecutor(http, logger, user_id)
        daily.dual_sign_in()
        time.sleep(1)
        sign_ok, sign_err = daily.sign_in()
        if not sign_ok and '活动太火爆' in sign_err:
            for retry in range(3):
                logger.warning(f'签到IP问题，重试({retry + 1}/3)...')
                time.sleep(2)
                http = SFHttpClient(fixed_proxy)
                s, user_id, phone = http.login(account_url)
                if s:
                    daily.http = http
                    daily.user_id = user_id
                    sign_ok, sign_err = daily.sign_in()
                    if sign_ok or '活动太火爆' not in sign_err:
                        break
        pb, pa = daily.run()
        result['points_before'] = pb
        result['points_after'] = pa
        result['points_earned'] = pa - pb
    # 会员日活动 (每月26-28号)
    if ENABLE_MEMBER_DAY:
        current_day = datetime.now().day
        if 26 <= current_day <= 28:
            logger.info('━━━ 会员日活动 ━━━')
            md = MemberDayExecutor(http, logger, user_id)
            md_result = md.run()
            result['member_day_prizes'] = md_result.get('lottery_prizes', [])
        else:
            logger.info('⏰ 未到会员日(26-28号)，跳过')
    return result


def run_account(account_raw: str, index: int) -> Dict[str, Any]:
    try:
        result = run_account_core(account_raw, index)
        if not isinstance(result, dict):
            result = {
                'success': False,
                'phone': '',
                'index': index,
                'points_before': 0,
                'points_after': 0,
                'points_earned': 0,
                'member_day_prizes': [],
                'error': '账号执行无返回结果',
            }
    except Exception as exc:
        result = {
            'success': False,
            'phone': '',
            'index': index,
            'points_before': 0,
            'points_after': 0,
            'points_earned': 0,
            'member_day_prizes': [],
            'error': str(exc),
        }
    result.setdefault('index', index)
    result.setdefault('points_before', 0)
    result.setdefault('points_after', 0)
    result.setdefault('points_earned', 0)
    result.setdefault('member_day_prizes', [])
    result.setdefault('error', '')
    result['account'] = mask_phone_value(result.get('phone', '')) if result.get('phone') else mask_sf_account(account_raw)
    append_notify_result(result)
    return result


# ==================== 主程序 ====================
def main():
    env_name = 'sfsyUrl'
    env_value = os.getenv(env_name)
    if not env_value:
        msg = f"未找到环境变量 {env_name}"
        print(f"❌ {msg}")
        append_notify_result({
            'index': 0,
            'account': '未配置',
            'success': False,
            'status': '配置错误',
            'error': msg,
            'points_before': 0,
            'points_after': 0,
            'points_earned': 0,
            'member_day_prizes': [],
        })
        return
    account_list = parse_accounts(env_value)
    if not account_list:
        msg = f"环境变量 {env_name} 为空"
        print(f"❌ {msg}")
        append_notify_result({
            'index': 0,
            'account': '未配置',
            'success': False,
            'status': '配置错误',
            'error': msg,
            'points_before': 0,
            'points_after': 0,
            'points_earned': 0,
            'member_day_prizes': [],
        })
        return
    random.shuffle(account_list)
    task_map = {
        "日常任务": ENABLE_DAILY_TASK,
        "会员日": ENABLE_MEMBER_DAY,
    }
    enabled = [f"{k}✓" for k, v in task_map.items() if v]
    print("=" * 60)
    print("🎉 顺丰速运自动任务 v1.2.0")
    print(f"👨‍💻 原作者: 爱学习的呆子 | 二改: YaoHuo8648")
    print(f"📱 共 {len(account_list)} 个账号")
    print(f"⚙️ 并发: {CONCURRENT_NUM} | 📋 {' '.join(enabled)}")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if PROXY_API_URL:
        _log_global(f"🔌 代理已开启: {PROXY_API_URL[:40]}...")
    print("=" * 60)
    all_results = []
    if CONCURRENT_NUM <= 1:
        for idx, raw in enumerate(account_list):
            result = run_account(raw, idx)
            all_results.append(result)
            if idx < len(account_list) - 1:
                time.sleep(2)
    else:
        with ThreadPoolExecutor(max_workers=CONCURRENT_NUM) as pool:
            futures = {pool.submit(run_account, raw, idx): idx for idx, raw in enumerate(account_list)}
            for f in as_completed(futures):
                all_results.append(f.result())
    all_results.sort(key=lambda x: x['index'])
    # 汇总
    print(f"\n{'='*70}")
    print("📊 执行汇总")
    print("=" * 70)
    total_earned = 0
    for r in all_results:
        phone = r['phone'][:3] + "****" + r['phone'][7:] if r.get('phone') and len(r['phone']) >= 7 else r.get('phone', '未登录')
        earned = r.get('points_earned', 0)
        total_earned += earned
        if not r['success']:
            print(f"❌ {phone}: 登录失败")
        else:
            parts = [f"积分+{earned}"]
            md_prizes = r.get('member_day_prizes', [])
            if md_prizes:
                parts.append(f"会员日: {', '.join(md_prizes)}")
            print(f"✅ {phone}: {' | '.join(parts)}")
    print("-" * 70)
    print(f"📱 总账号: {len(all_results)} | 💰 总积分+{total_earned}")
    print("=" * 70)
    print("🎊 执行完成!")


if __name__ == '__main__':
    try:
        main()
    except Exception as err:
        append_notify_result({
            'index': len(GLOBAL_NOTIFY_BUFFERS),
            'account': '全局异常',
            'success': False,
            'status': '执行失败',
            'error': str(err),
            'points_before': 0,
            'points_after': 0,
            'points_earned': 0,
            'member_day_prizes': [],
        })
        print(f'❌ 全局异常：{err}')
    finally:
        dispatch_notify()

