#!/usr/bin/env python3
"""
电信0点权益极速版 v1.1.0（专属聚合推送版）

功能：中国电信金豆 0 点权益抢兑，支持登录验证、时间同步、波次抢兑、连接预热、多账号并发，执行结束后统一聚合推送。

配置说明：
1. 账号变量：
   chinaTelecomAccount                              必填，中国电信账号变量
   - 格式：手机号#密码
   - 多账号支持使用 &、英文逗号、中文逗号、@ 或换行分隔
   - 示例：18900000000#password&18911111111#password

2. 推送变量：
   需要同目录存在 SendNotify.py，脚本结束后会统一调用 send_push_notification。
   常用推送变量如下，配置任意一种即可：
   QYWX_KEY                                         企业微信机器人 key
   PUSH_PLUS_TOKEN                                  PushPlus token
   PUSH_KEY                                         Server 酱 key
   DD_BOT_TOKEN 或 DD_BOT_SECRET                     钉钉机器人 token/secret
   FSKEY                                            飞书机器人 key

3. 青龙任务建议：
   名称：电信0点权益极速版
   命令：python3 电信0点权益_极速版.py
   定时：0 点前运行，具体按活动自行调整
"""

import os
import re
import sys
import ssl
import time
import json
import base64
import random
import certifi
import aiohttp
import asyncio
import datetime
import requests
import binascii
import threading
from concurrent.futures import ThreadPoolExecutor
from http import cookiejar
from Crypto.Cipher import DES3
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5
from Crypto.Util.Padding import pad, unpad
from aiohttp import ClientSession, TCPConnector


# 🌸 标题 🌸
LOGO = f'''
  ✧･ﾟ: *✧･ﾟ:*  电信金豆0点抢兑-极速版  ✧･ﾟ: *✧･ﾟ:*
  优化：波次抢兑 | 连接预热 | 0延迟并发 | 智能重试
'''

# 配置参数
WAVE1_COUNT = 5      # 波次1：提前发射数量
WAVE2_COUNT = 15     # 波次2：0点主力数量  
WAVE3_COUNT = 30     # 波次3：捡漏总数量
PREHEAT_MS = 50      # 提前预热毫秒数
ACCOUNT_STAGGER_MS = 15  # 多账号错峰毫秒数

# 全局变量
time_offset_ms = 0
network_time_lock = threading.Lock()
SCRIPT_TITLE = "电信0点权益极速版"
GLOBAL_NOTIFY_BUFFERS = []


def mask_phone(phone: str) -> str:
    phone = str(phone or "")
    return f"{phone[:3]}****{phone[-4:]}" if len(phone) >= 7 else phone


def split_accounts_env(value: str):
    if not value:
        return []
    accounts = []
    for raw in re.split(r"[@&，,\n\r]+", value):
        raw = raw.strip()
        if not raw:
            continue
        if "#" not in raw:
            accounts.append({"raw": raw, "phone": raw, "password": "", "valid": False, "error": "账号格式错误，应为 手机号#密码"})
            continue
        phone, password = raw.split("#", 1)
        phone = phone.strip()
        password = password.strip()
        accounts.append({"raw": raw, "phone": phone, "password": password, "valid": bool(phone and password), "error": "账号或密码为空" if not (phone and password) else ""})
    return accounts


def append_notify_result(index, account, ok, status, message=""):
    GLOBAL_NOTIFY_BUFFERS.append({
        "index": index,
        "account": mask_phone(account),
        "ok": bool(ok),
        "status": status or ("success" if ok else "failed"),
        "message": str(message or "").strip(),
    })


def build_notify_report():
    total = len(GLOBAL_NOTIFY_BUFFERS)
    success = sum(1 for item in GLOBAL_NOTIFY_BUFFERS if item.get("ok"))
    failed = total - success
    lines = [
        "==============================",
        f"🕒 执行时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"📊 统计数据：成功 {success} / 总计 {total}",
        f"✅ 成功账号：{success} 个",
        f"❌ 失败账号：{failed} 个",
        "==============================",
    ]
    for item in GLOBAL_NOTIFY_BUFFERS:
        ok = bool(item.get("ok"))
        lines.append(f"{'🧑‍💻' if ok else '🧟'} 【账号{item.get('index')}】{item.get('account')}")
        lines.append(f"{'✅' if ok else '❌'} 状态：{'执行成功' if ok else ('配置错误' if item.get('status') == 'config_error' else '执行失败')}")
        if ok:
            lines.append(f"🎯 抢兑结果：{item.get('message') or '成功'}")
        else:
            lines.append(f"🧨 原因：{item.get('message') or '未知错误'}")
        lines.append("------------------------------")
    return "\n".join(lines)


def dispatch_notify():
    if not GLOBAL_NOTIFY_BUFFERS:
        return
    title = f"{SCRIPT_TITLE}执行结果"
    content = build_notify_report()
    print("\n[聚合推送报表阅览]\n" + content)
    try:
        from SendNotify import send_push_notification
        send_push_notification(title, content)
    except Exception as exc:
        printn(f"❌ 专属推送失败: {exc}")

class Color:
    MINT = '\033[38;5;85m'
    LIGHT_PINK = '\033[38;5;218m'
    ENDC = '\033[0m'


def get_network_time_ms():
    """获取网络时间（毫秒级精度）"""
    global time_offset_ms
    
    time_apis = [
        ("https://www.baidu.com", 0.8),
        ("https://www.189.cn", 1.0),
        ("https://www.qq.com", 0.9),
        ("https://www.bilibili.com", 0.85)
    ]
    
    best_offset = 0
    min_delay = float('inf')
    
    for api_url, weight in time_apis:
        try:
            start_time = time.time_ns()
            response = requests.head(api_url, timeout=2, allow_redirects=True)
            end_time = time.time_ns()
            
            if 'Date' in response.headers:
                from email.utils import parsedate_to_datetime
                server_time = parsedate_to_datetime(response.headers['Date'])
                server_timestamp_ms = server_time.timestamp() * 1000
                
                # 网络往返延迟的一半
                network_delay_ms = (end_time - start_time) / 2_000_000
                local_time_ms = (start_time / 1_000_000) + network_delay_ms
                offset = local_time_ms - server_timestamp_ms
                
                # 选择延迟最小的结果
                if network_delay_ms < min_delay:
                    min_delay = network_delay_ms
                    best_offset = offset
                    
        except Exception:
            continue
    
    with network_time_lock:
        time_offset_ms = best_offset
    
    print(f"✅ 网络时间同步完成 | 偏移: {best_offset:.2f}ms | 延迟: {min_delay:.2f}ms")
    return time.time() * 1000 - best_offset


def get_current_time_ms():
    """获取当前精确时间（毫秒级）"""
    return time.time() * 1000 - time_offset_ms


def get_beijing_midnight_ms():
    """获取北京时间下一个0点的毫秒时间戳（东八区）"""
    now_ms = get_current_time_ms()
    beijing_tz = datetime.timezone(datetime.timedelta(hours=8))
    now_beijing = datetime.datetime.fromtimestamp(now_ms / 1000, tz=beijing_tz)
    target = now_beijing.replace(hour=0, minute=0, second=0, microsecond=0)
    if target.timestamp() * 1000 <= now_ms:
        # 今天0点已过，取明天0点
        target = target + datetime.timedelta(days=1)
    return target.timestamp() * 1000


def run_Time(hour, minute, second, millisecond=0):
    """计算目标时间的毫秒级时间戳"""
    current_ms = get_current_time_ms()
    current_sec = current_ms / 1000
    
    current_date = datetime.datetime.fromtimestamp(current_sec)
    target_date = current_date.replace(
        hour=hour, minute=minute, second=second, 
        microsecond=millisecond * 1000
    )
    
    target_sec = target_date.timestamp()
    if target_sec < current_sec:
        target_sec += 86400
    
    return target_sec


# Cookie策略
class BlockAll(cookiejar.CookiePolicy):
    return_ok = set_ok = domain_return_ok = path_return_ok = lambda self, *args, **kwargs: False
    netscape = True
    rfc2965 = hide_cookie2 = False


# SSL上下文
context = ssl.create_default_context()
context.set_ciphers('DEFAULT@SECLEVEL=1')
context.check_hostname = False
context.verify_mode = ssl.CERT_NONE


class DESAdapter(requests.adapters.HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        kwargs['ssl_context'] = context
        return super().init_poolmanager(*args, **kwargs)


requests.packages.urllib3.disable_warnings()

# 加密密钥
key = b'1234567`90koiuyhgtfrdews'
iv = 8 * b'\0'

public_key_b64 = '''-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDBkLT15ThVgz6/NOl6s8GNPofdWzWbCkWnkaAm7O2LjkM1H7dMvzkiqdxU02jamGRHLX/ZNMCXHnPcW/sDhiFCBN18qFvy8g6VYb9QtroI09e176s+ZCtiv7hbin2cCTj99iUpnEloZm19lwHyo69u5UMiPMpq0/XKBO8lYhN/gwIDAQAB
-----END PUBLIC KEY-----'''

public_key_data = '''-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC+ugG5A8cZ3FqUKDwM57GM4io6JGcStivT8UdGt67PEOihLZTw3P7371+N47PrmsCpnTRzbTgcupKtUv8ImZalYk65dU8rjC/ridwhw9ffW2LBwvkEnDkkKKRi2liWIItDftJVBiWOh17o6gfbPoNrWORcAdcbpk2L+udld5kZNwIDAQAB
-----END PUBLIC KEY-----'''


def printn(m):
    """带网络时间的日志输出"""
    current_ms = get_current_time_ms()
    current_time = datetime.datetime.fromtimestamp(current_ms / 1000).strftime("%H:%M:%S.%f")[:-3]
    print(f'\n🌸 [{current_time}] {m}')


def get_first_three(value):
    if isinstance(value, (int, float)):
        return f"{str(value)[:3]}****{str(value)[-4:]}"
    elif isinstance(value, str):
        return f"{value[:3]}****{value[-4:]}"
    return str(value)


def encrypt(text):
    cipher = DES3.new(key, DES3.MODE_CBC, iv)
    ciphertext = cipher.encrypt(pad(text.encode(), DES3.block_size))
    return ciphertext.hex()


def decrypt(text):
    ciphertext = bytes.fromhex(text)
    cipher = DES3.new(key, DES3.MODE_CBC, iv)
    plaintext = unpad(cipher.decrypt(ciphertext), DES3.block_size)
    return plaintext.decode()


def b64(plaintext):
    public_key = RSA.import_key(public_key_b64)
    cipher = PKCS1_v1_5.new(public_key)
    ciphertext = cipher.encrypt(plaintext.encode())
    return base64.b64encode(ciphertext).decode()


def encrypt_para(plaintext):
    if not isinstance(plaintext, str):
        plaintext = json.dumps(plaintext)
    public_key = RSA.import_key(public_key_data)
    cipher = PKCS1_v1_5.new(public_key)
    key_size = public_key.size_in_bytes()
    max_chunk_size = key_size - 11
    plaintext_bytes = plaintext.encode()
    ciphertext = b''
    for i in range(0, len(plaintext_bytes), max_chunk_size):
        chunk = plaintext_bytes[i:i + max_chunk_size]
        encrypted_chunk = cipher.encrypt(chunk)
        ciphertext += encrypted_chunk
    return binascii.hexlify(ciphertext).decode()


def encode_phone(text):
    encoded_chars = []
    for char in text:
        encoded_chars.append(chr(ord(char) + 2))
    return ''.join(encoded_chars)


def userLoginNormal(phone, password):
    """登录获取ticket"""
    alphabet = 'abcdef0123456789'
    uuid = [''.join(random.sample(alphabet, 8)), ''.join(random.sample(alphabet, 4)),
            '4' + ''.join(random.sample(alphabet, 3)), ''.join(random.sample(alphabet, 4)),
            ''.join(random.sample(alphabet, 12))]
    timestamp = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    loginAuthCipherAsymmertric = 'iPhone 14 15.4.' + uuid[0] + uuid[1] + phone + timestamp + password[:6] + '0$$$0.'
    
    ss = requests.Session()
    ss.headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; 22081212C Build/TKQ1.220829.002) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.5112.97 Mobile Safari/537.36",
        "Referer": "https://wapact.189.cn:9001/JinDouMall/JinDouMall_independentDetails.html"
    }
    ss.mount('https://', DESAdapter())
    ss.cookies.set_policy(BlockAll())
    
    try:
        r = ss.post('https://appgologin.189.cn:9031/login/client/userLoginNormal', json={
            "headerInfos": {"code": "userLoginNormal", "timestamp": timestamp, "broadAccount": "", "broadToken": "",
                            "clientType": "#11.3.0#channel35#Xiaomi Redmi K30 Pro#", "shopId": "20002",
                            "source": "110003", "sourcePassword": "Sid98s", "token": "",
                            "userLoginName": encode_phone(phone)}, 
            "content": {"attach": "test",
                       "fieldData": {"loginType": "4",
                                    "accountType": "",
                                    "loginAuthCipherAsymmertric": b64(loginAuthCipherAsymmertric),
                                    "deviceUid": uuid[0] + uuid[1] + uuid[2],
                                    "phoneNum": encode_phone(phone),
                                    "isChinatelecom": "0",
                                    "systemVersion": "12",
                                    "authentication": encode_phone(password)}}}
        ).json()
        
        l = r['responseData']['data']['loginSuccessResult']
        if l:
            ticket = get_ticket(phone, l['userId'], l['token'])
            return ticket
        return False
    except Exception as e:
        print(f"💔 登录失败: {e}")
        return False


def get_ticket(phone, userId, token):
    """获取ticket"""
    ss = requests.Session()
    ss.mount('https://', DESAdapter())
    
    try:
        r = ss.post('https://appgologin.189.cn:9031/map/clientXML',
                    data='<Request><HeaderInfos><Code>getSingle</Code><Timestamp>' + datetime.datetime.now().strftime(
                        "%Y%m%d%H%M%S") + '</Timestamp><BroadAccount></BroadAccount><BroadToken></BroadToken><ClientType>#9.6.1#channel50#iPhone 14 Pro Max#</ClientType><ShopId>20002</ShopId><Source>110003</Source><SourcePassword>Sid98s</SourcePassword><Token>' + token + '</Token><UserLoginName>' + phone + '</UserLoginName></HeaderInfos><Content><Attach>test</Attach><FieldData><TargetId>' + encrypt(
                        userId) + '</TargetId><Url>4a6862274835b451</Url></FieldData></Content></Request>',
                    headers={'user-agent': 'CtClient;10.4.1;Android;13;22081212C;NTQzNzgx!#!MTgwNTg1'},
                    verify=certifi.where())
        tk = re.findall('<Ticket>(.*?)</Ticket>', r.text)
        if len(tk) == 0:
            return False
        return decrypt(tk[0])
    except Exception as e:
        print(f"💔 获取ticket失败: {e}")
        return False


def getSign_sync(ticket):
    """同步获取sign（用于预热）"""
    ss = requests.Session()
    ss.mount('https://', DESAdapter())
    ss.verify = False
    
    try:
        response = ss.get(
            'https://wappark.189.cn/jt-sign/ssoHomLogin?ticket=' + ticket,
            headers={'User-Agent': "Mozilla/5.0 (Linux; Android 13; 22081212C Build/TKQ1.220829.002) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.5112.97 Mobile Safari/537.36"},
            timeout=5
        ).json()
        
        if response.get('resoultCode') == '0':
            return response.get('sign'), response.get('accId')
    except Exception as e:
        print(f"💔 获取sign失败: {e}")
    return None, None


def getLevelRightsList_sync(session, accId):
    """同步获取权益列表"""
    try:
        value = {
            "type": "hg_qd_djqydh",
            "accId": accId,
            "shopId": "20001"
        }
        paraV = encrypt_para(value)
        
        response = session.post(
            'https://wappark.189.cn/jt-sign/paradise/queryLevelRightInfo',
            json={"para": paraV},
            timeout=5
        )
        
        data = response.json()
        if data.get('code') == 401:
            print(f"💔 获取权益失败: sign过期")
            return None
        
        current_level = int(data['currentLevel'])
        key_name = 'V' + str(current_level)
        ids = [item['activityId'] for item in data.get(key_name, []) if '话费' in item.get('title', '')]
        return ids
        
    except Exception as e:
        print(f"💔 获取权益列表失败: {e}")
        return None


async def conversionRights_battle(phone, aid, session, accId, headers, retry_count=0):
    """
    抢兑战斗版 - 极速模式
    根据重试次数动态调整延迟策略
    """
    value = {
        "id": aid,
        "accId": accId,
        "showType": "9003",
        "showEffect": "8",
        "czValue": "0"
    }
    paraV = encrypt_para(value)
    payload = {"para": paraV}
    url = 'https://wappark.189.cn/jt-sign/paradise/receiverRights'
    
    # 动态延迟策略：前3次几乎0延迟，之后递增
    if retry_count < 3:
        delay = 0.001  # 1ms
    elif retry_count < 6:
        delay = 0.005  # 5ms
    elif retry_count < 10:
        delay = 0.01   # 10ms
    else:
        delay = 0.02 + random.uniform(0, 0.02)  # 20-40ms随机
    
    if delay > 0.001:
        await asyncio.sleep(delay)
    
    try:
        response = await asyncio.to_thread(
            session.post,
            url,
            json=payload,
            headers=headers,
            timeout=(1, 3)
        )
        
        result_text = response.text
        
        # 快速结果判断
        if '兑换成功' in result_text:
            printn(f"{Color.MINT}🎉 {get_first_three(phone)} 兑换成功！第{retry_count+1}次尝试{Color.ENDC}")
            return True, "success"
        elif '已兑换' in result_text:
            printn(f"✅ {get_first_three(phone)} 已兑换过")
            return True, "already"
        elif '库存不足' in result_text or '已抢完' in result_text or '售罄' in result_text:
            printn(f"💔 {get_first_three(phone)} 库存已空")
            return False, "empty"
        elif '活动未开始' in result_text:
            return False, "not_started"
        elif '系统繁忙' in result_text or '频繁' in result_text:
            return False, "busy"
        else:
            # 解析具体错误
            try:
                result = response.json()
                msg = result.get('msg', result.get('message', '未知'))
                return False, f"retry:{msg[:20]}"
            except:
                return False, "retry"
                
    except requests.exceptions.Timeout:
        return False, "timeout"
    except Exception as e:
        return False, f"error:{str(e)[:30]}"


async def exchange_battle_waves(phone, session, aid, accId, headers, account_index=0):
    """
    波次抢兑策略
    - 波次1：提前发射（抢占先机）
    - 波次2：0点主力（多并发冲击）
    - 波次3：持续捡漏
    """
    success = False
    result_msg = ""
    
    # 计算目标时间（北京时间0点）
    network_now = get_current_time_ms()
    today_midnight = get_beijing_midnight_ms()
    
    # 多账号错峰：每个账号错开15ms
    stagger_delay = account_index * ACCOUNT_STAGGER_MS
    
    # ===== 波次1：提前50ms预热发射 =====
    wave1_time = today_midnight - PREHEAT_MS + stagger_delay
    wait_ms = wave1_time - network_now
    
    if wait_ms > 0:
        await asyncio.sleep(wait_ms / 1000)
    
    printn(f"{get_first_three(phone)} 🚀 波次1：提前{PREHEAT_MS}ms发射 {WAVE1_COUNT}请求")
    
    wave1_tasks = [conversionRights_battle(phone, aid, session, accId, headers, i) for i in range(WAVE1_COUNT)]
    results = await asyncio.gather(*wave1_tasks)
    
    for success_flag, msg in results:
        if success_flag:
            success = True
            result_msg = msg
            break
    
    if success:
        return success, result_msg
    
    # ===== 波次2：0点整主力冲击 =====
    network_now = get_current_time_ms()
    wave2_time = today_midnight + stagger_delay
    wait_ms = wave2_time - network_now
    
    if wait_ms > 0:
        await asyncio.sleep(wait_ms / 1000)
    
    printn(f"{get_first_three(phone)} ⚡ 波次2：0点主力发射 {WAVE2_COUNT}请求")
    
    wave2_tasks = [conversionRights_battle(phone, aid, session, accId, headers, WAVE1_COUNT + i) for i in range(WAVE2_COUNT)]
    results = await asyncio.gather(*wave2_tasks)
    
    for success_flag, msg in results:
        if success_flag:
            success = True
            result_msg = msg
            break
    
    if success:
        return success, result_msg
    
    # ===== 波次3：捡漏模式（持续5秒） =====
    printn(f"{get_first_three(phone)} 🔍 波次3：进入捡漏模式...")
    
    start_time = time.time()
    end_time = start_time + 5  # 捡漏5秒
    retry_idx = WAVE1_COUNT + WAVE2_COUNT
    
    while time.time() < end_time and not success:
        # 每轮2-3个并发
        batch_size = random.randint(2, 3)
        tasks = [conversionRights_battle(phone, aid, session, accId, headers, retry_idx + i) for i in range(batch_size)]
        results = await asyncio.gather(*tasks)
        
        for success_flag, msg in results:
            if success_flag:
                success = True
                result_msg = msg
                break
        
        retry_idx += batch_size
        
        # 捡漏间隔：100-200ms
        if not success:
            await asyncio.sleep(random.uniform(0.1, 0.2))
    
    if not success:
        printn(f"{get_first_three(phone)} 😔 捡漏结束，未能成功")
    
    return success, result_msg


async def process_account(phone, password, account_index=0, is_test=False):
    """处理单个账号"""
    printn(f'{get_first_three(phone)} 开始登录...')
    
    # 同步登录（异步内调用同步函数）
    ticket = await asyncio.to_thread(userLoginNormal, phone, password)
    
    if not ticket:
        printn(f'{get_first_three(phone)} 💔 登录失败')
        return False, "登录失败"
    
    printn(f'{get_first_three(phone)} ✅ 登录成功！ticket获取成功')
    
    if is_test:
        printn(f'{get_first_three(phone)} 🧪 测试模式：登录验证通过')
        return True, "测试模式登录验证通过"
    
    # 获取sign和accId
    sign, accId = await asyncio.to_thread(getSign_sync, ticket)
    
    if not sign or not accId:
        printn(f'{get_first_three(phone)} 💔 获取sign失败')
        return False, "获取sign失败"
    
    printn(f'{get_first_three(phone)} ✅ 获取sign和accId成功')
    
    # 获取权益列表
    session = requests.Session()
    session.mount('https://', DESAdapter())
    session.verify = False
    session.headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; 22081212C Build/TKQ1.220829.002) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/104.0.5112.97 Mobile Safari/537.36",
        "sign": sign
    }
    
    rights_ids = await asyncio.to_thread(getLevelRightsList_sync, session, accId)
    
    if not rights_ids:
        printn(f'{get_first_three(phone)} 💔 未找到话费权益')
        return False, "未找到话费权益"
    
    aid = rights_ids[0]
    printn(f'{get_first_three(phone)} 🎯 找到话费权益ID: {aid}')
    
    # 预访问一次（连接预热）
    try:
        await asyncio.to_thread(
            session.get,
            'https://wappark.189.cn/jt-sign/paradise/queryLevelRightInfo',
            timeout=3
        )
        printn(f'{get_first_three(phone)} 🔥 连接预热完成')
    except:
        pass
    
    # 等待到抢兑时间（北京时间0点）
    network_now = get_current_time_ms()
    today_midnight = get_beijing_midnight_ms()
    wait_sec = (today_midnight - PREHEAT_MS - 200) - network_now  # 提前250ms准备
    
    if wait_sec > 0:
        printn(f'{get_first_three(phone)} ⏳ 等待抢兑开始... {wait_sec/1000:.2f}秒')
        await asyncio.sleep(wait_sec / 1000)
    
    # 执行波次抢兑
    success, msg = await exchange_battle_waves(phone, session, aid, accId, session.headers, account_index)
    
    return success, msg


async def main(is_test=False):
    """主函数"""
    print(LOGO)

    print("🌐 正在同步网络时间...")
    get_network_time_ms()

    account_items = split_accounts_env(os.environ.get('chinaTelecomAccount', ''))
    if not account_items:
        message = "未设置 chinaTelecomAccount，格式：手机号#密码"
        print(f"💔 错误: {message}")
        append_notify_result(1, 'chinaTelecomAccount', False, 'config_error', message)
        dispatch_notify()
        return []

    print(f"📱 发现 {len(account_items)} 个账号")

    if is_test:
        print("🧪 测试模式：仅验证登录")
    else:
        print("⚔️ 抢兑模式：波次策略已启用")
        print(f"   波次1: 提前{PREHEAT_MS}ms发射 {WAVE1_COUNT}请求")
        print(f"   波次2: 0点整发射 {WAVE2_COUNT}请求")
        print(f"   波次3: 持续5秒捡漏")
        print(f"   多账号错峰: {ACCOUNT_STAGGER_MS}ms/账号")

    tasks = []
    task_meta = []
    for idx, account in enumerate(account_items):
        if not account.get('valid'):
            print(f"💔 跳过无效格式: {account.get('raw')}")
            append_notify_result(idx + 1, account.get('phone') or account.get('raw'), False, 'config_error', account.get('error'))
            continue
        phone, password = account['phone'], account['password']
        task_meta.append((idx + 1, phone))
        tasks.append(process_account(phone, password, idx, is_test))

    results = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []

    for meta, result in zip(task_meta, results):
        index, phone = meta
        if isinstance(result, Exception):
            append_notify_result(index, phone, False, 'failed', str(result))
            continue
        if isinstance(result, tuple):
            ok, msg = result[0], result[1] if len(result) > 1 else ''
        else:
            ok, msg = bool(result), '成功' if result else '失败'
        append_notify_result(index, phone, bool(ok), 'success' if ok else 'failed', msg)

    success_count = sum(1 for item in GLOBAL_NOTIFY_BUFFERS if item.get('ok'))
    fail_count = len(GLOBAL_NOTIFY_BUFFERS) - success_count
    print(f"\n{'='*50}")
    print(f"📊 任务统计: 成功 {success_count} | 失败 {fail_count}")
    print(f"{'='*50}")

    dispatch_notify()
    return results


if __name__ == "__main__":
    # 测试模式：python3 script.py test
    is_test = len(sys.argv) > 1 and sys.argv[1] == 'test'
    
    try:
        asyncio.run(main(is_test))
    except KeyboardInterrupt:
        print("\n⚠️ 用户中断")
    except Exception as e:
        print(f"💔 运行错误: {e}")
        append_notify_result(1, SCRIPT_TITLE, False, 'failed', str(e))
        dispatch_notify()
