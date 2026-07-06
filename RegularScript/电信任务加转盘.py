#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
电信任务加转盘 v1.1.0（专属聚合推送版）

功能：中国电信账号登录、签到、连签/累签领奖、金豆转盘抽奖、任务执行、喂食，支持多账号执行，执行结束后统一聚合推送。

配置说明：
1. 账号变量：
   chinaTelecomAccount                              必填，中国电信账号变量
   - 格式：手机号#密码
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
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
   名称：电信任务加转盘
   命令：python3 电信任务加转盘.py
   定时：每天运行 1 次即可，具体时间自行调整
"""
import os, re, sys, json, time, random, string, base64, asyncio, certifi, requests
from typing import Dict, Any, Union
from datetime import datetime
from pathlib import Path
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5, DES3, AES
from Crypto.Util.Padding import pad, unpad
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

# --- 配置与常量 ---
KEYS = {
    'login_rsa': """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDBkLT15ThVgz6/NOl6s8GNPofdWzWbCkWnkaAm7O2LjkM1H7dMvzkiqdxU02jamGRHLX/ZNMCXHnPcW/sDhiFCBN18qFvy8g6VYb9QtroI09e176s+ZCtiv7hbin2cCTj99iUpnEloZm19lwHyo69u5UMiPMpq0/XKBO8lYhN/gwIDAQAB
-----END PUBLIC KEY-----""",
    'data_rsa': """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC+ugG5A8cZ3FqUKDwM57GM4io6JGcStivT8UdGt67PEOihLZTw3P7371+N47PrmsCpnTRzbTgcupKtUv8ImZalYk65dU8rjC/ridwhw9ffW2LBwvkEnDkkKKRi2liWIItDftJVBiWOh17o6gfbPoNrWORcAdcbpk2L+udld5kZNwIDAQAB
-----END PUBLIC KEY-----""",
    'des3': b'1234567`90koiuyhgtfrdews',
    'aes_def': b'34d7cb0bcdf07523',
    'aes_login': 'telecom_wap_2018'
}
CACHE_FILE = Path(__file__).parent / 'Cache.json'
global_logs, cache = [], {}
SCRIPT_TITLE = "电信任务加转盘"
GLOBAL_NOTIFY_BUFFERS = []

# --- 工具函数 ---
log = lambda m: (global_logs.append(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {m}"), print(global_logs[-1]))
mask = lambda p: f"{p[:3]}****{p[-4:]}" if len(p) >= 7 else p
ts = lambda: datetime.now().strftime('%Y%m%d%H%M%S')
rd_str = lambda l: ''.join(random.choices(string.ascii_letters + string.digits, k=l))
encode = lambda s: ''.join(chr(ord(c) + 2) for c in s)


def split_accounts_env(value: str):
    if not value:
        return []
    accounts = []
    for raw in re.split(r"[&，,\n\r]+", value):
        raw = raw.strip()
        if not raw or "#" not in raw:
            continue
        phone, password = raw.split("#", 1)
        phone = phone.strip()
        password = password.strip()
        if phone and password:
            accounts.append((phone, password))
    return accounts


def append_notify_result(index, account, ok, status, message="", detail=""):
    GLOBAL_NOTIFY_BUFFERS.append({
        "index": index,
        "account": mask(account),
        "ok": bool(ok),
        "status": status or ("success" if ok else "failed"),
        "message": str(message or "").strip(),
        "detail": str(detail or "").strip(),
    })


def build_notify_report():
    total = len(GLOBAL_NOTIFY_BUFFERS)
    success = sum(1 for item in GLOBAL_NOTIFY_BUFFERS if item.get("ok"))
    failed = total - success
    lines = [
        "==============================",
        f"🕒 执行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
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
            detail_lines = [line for line in item.get("detail", "").splitlines() if line.strip()]
            key_lines = []
            for line in detail_lines:
                if any(key in line for key in ["[签到]", "[连签]", "[累签]", "[领奖]", "[抽奖]", "[抽奖结果]", "[任务]", "[喂食]"]):
                    key_lines.append(line)
            lines.append("🧾 执行摘要：")
            lines.extend(key_lines[-12:] if key_lines else ["登录成功，任务已执行"])
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
        print(f"[推送失败] {exc}")

# --- SSL与会话 ---
class CustomSSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context(ciphers='DEFAULT@SECLEVEL=1:!aNULL:!eNULL:!MD5')
        ctx.check_hostname = False
        kwargs['ssl_context'] = ctx
        return super().init_poolmanager(*args, **kwargs)

session = requests.Session()
session.verify = certifi.where()
session.headers.update({'User-Agent': 'Mozilla/5.0 (Linux; U; Android 12; zh-cn) AppleWebKit/533.1 (KHTML, like Gecko) Version/5.0 Mobile Safari/533.1'})
session.mount('https://', CustomSSLAdapter())

# --- 加密逻辑 ---
def encrypt_des3(data, mode='enc'):
    cipher = DES3.new(KEYS['des3'], DES3.MODE_CBC, 8 * b'\0')
    if mode == 'enc':
        return cipher.encrypt(pad(data.encode(), 8)).hex()
    return unpad(cipher.decrypt(bytes.fromhex(data)), 8).decode()

def encrypt_aes(data, key=KEYS['aes_def'], b64=False):
    data = json.dumps(data, separators=(',', ':')) if isinstance(data, (dict, list)) else data
    cipher = AES.new(key if isinstance(key, bytes) else key.encode(), AES.MODE_ECB)
    enc = cipher.encrypt(pad(data.encode(), 16))
    return base64.b64encode(enc).decode() if b64 else enc.hex()

def encrypt_rsa(data, key_type='data', out='hex'):
    cipher = PKCS1_v1_5.new(RSA.import_key(KEYS[f'{key_type}_rsa']))
    data = json.dumps(data, separators=(',', ':')) if isinstance(data, (dict, list)) else data
    if out == 'hex': # 分段加密
        return ''.join(cipher.encrypt(data[i:i+32].encode()).hex() for i in range(0, len(data), 32))
    return base64.b64encode(cipher.encrypt(data.encode())).decode()

# --- 请求装饰器/助手 ---
def api_req(url, method='POST', log_msg=None, raw=False, **kwargs) -> Union[Dict[str, Any], str]:
    try:
        r = session.request(method, url, timeout=15, **kwargs)
        if raw: return r.text

        # Try parse JSON
        res = r.json()
        if not isinstance(res, dict): return {} # Enforce dict return

        if log_msg:
            msg = res.get('msg') or res.get('resoultMsg') or ('成功' if isinstance(res, dict) else '已响应')
            log(f"{log_msg}: {msg}")
        return res
    except Exception as e:
        if log_msg: log(f"{log_msg}失败: {e}")
        return '' if raw else {}

# --- 业务逻辑 ---
def login(phone, password):
    m_phone = mask(phone)
    if phone not in cache:
        log(f"[登录] {m_phone} 正在请求新Token...")
        body = {
            "headerInfos": {"code": "userLoginNormal", "timestamp": ts(), "broadAccount": "", "broadToken": "", "clientType": "#10.5.0#channel50#iPhone 14 Pro Max#", "shopId": "20002", "source": "110003", "sourcePassword": "Sid98s", "token": "", "userLoginName": encode(phone)},
            "content": {"attach": "test", "fieldData": {"loginType": "4", "accountType": "", "loginAuthCipherAsymmertric": encrypt_rsa(f"iPhone 14 15.4.{rd_str(12)}{phone}{ts()}{password}0$$$0.", 'login', 'b64'), "deviceUid": rd_str(16), "phoneNum": encode(phone), "isChinatelecom": "0", "systemVersion": "15.4.0", "authentication": encode(password)}}
        }
        res = api_req('https://appgologin.189.cn:9031/login/client/userLoginNormal', json=body, log_msg=f"[请求登录] {m_phone}")
        if isinstance(res, dict):
            res_data = res.get('responseData', {}).get('data', {}).get('loginSuccessResult')
            if not res_data:
                log(f"[登录失败] {m_phone} 无响应数据")
                return None
            cache[phone] = res_data
            save_cache()

    # 获取Ticket/UID
    xml = f'<Request><HeaderInfos><Code>getSingle</Code><Timestamp>{ts()}</Timestamp><BroadAccount></BroadAccount><BroadToken></BroadToken><ClientType>#9.6.1#channel50#iPhone 14 Pro Max#</ClientType><ShopId>20002</ShopId><Source>110003</Source><SourcePassword>Sid98s</SourcePassword><Token>{cache[phone]["token"]}</Token><UserLoginName>{phone}</UserLoginName></HeaderInfos><Content><Attach>test</Attach><FieldData><TargetId>{encrypt_des3(cache[phone]["userId"])}</TargetId><Url>4a6862274835b451</Url></FieldData></Content></Request>'
    res_xml = api_req('https://appgologin.189.cn:9031/map/clientXML', data=xml, headers={'Content-Type': 'application/xml'}, raw=True, log_msg=f"[获取Ticket] {m_phone}")
    if isinstance(res_xml, str) and ('过期' in res_xml or '校验错误' in res_xml):
        log(f"[Token失效] {m_phone} 重新登录")
        del cache[phone]; save_cache(); return login(phone, password)

    try:
        if not isinstance(res_xml, str) or '<Ticket>' not in res_xml:
            log(f"[Ticket异常] {m_phone} 响应: {str(res_xml)[:100]}")
            return None
        uid = encrypt_des3(res_xml.split('<Ticket>')[1].split('</Ticket>')[0], 'dec')
        user = {**cache[phone], 'uid': uid, 'phoneNbr': phone}
        # 统一登录获取Bearer
        auth_body = encrypt_aes({"ticket": uid, "backUrl": "https%3A%2F%2Fwapact.189.cn%3A9001", "platformCode": "P201010301", "loginType": 2}, KEYS['aes_login'], True)
        auth_res = api_req('https://wapact.189.cn:9001/unified/user/login', data=auth_body, headers={'Content-Type': 'application/json'})
        if isinstance(auth_res, dict) and auth_res.get('code') == 0:
            user['Authorization'] = f"Bearer {auth_res['biz']['token']}"
        return user
    except Exception as e:
        log(f"[登录流程异常] {m_phone}: {e}")
        return None
def sign_tasks(user):
    m = mask(user['phoneNbr'])
    sso = api_req(f"https://wappark.189.cn/jt-sign/ssoHomLogin?ticket={user['uid']}", method='GET')
    if not isinstance(sso, dict) or not sso or 'sign' not in sso: return

    # 签到
    api_req('https://wappark.189.cn/jt-sign/webSign/sign', json={"encode": encrypt_aes({"phone": user['phoneNbr'], "date": int(time.time()*1000)})}, headers={'sign': sso['sign']}, log_msg=f"[签到] {m}")

    # 进度与奖励
    def check_and_award(path, key, days_list, label):
        res = api_req(f'https://wappark.189.cn/jt-sign/{path}', json={"para": encrypt_rsa({"phone": user['phoneNbr']})}, headers={'sign': sso['sign']})
        if not isinstance(res, dict): return
        days = str(res.get('data', {}).get(key) if 'data' in res else res.get(key, 0))
        log(f"[{label}] {m}: {days}天")
        if days in days_list:
            api_req('https://wappark.189.cn/jt-sign/webSign/exchangePrize', json={"para": encrypt_rsa({"phone": user['phoneNbr'], "type": days})}, headers={'sign': sso['sign']}, log_msg=f"[领奖] {m} {days}天")

    check_and_award('api/home/userStatusInfo', 'signDay', ['7'], '连签')
    check_and_award('webSign/continueSignDays', 'continueSignDays', ['15', '28'], '累签')

    # 金豆转盘
    if 'Authorization' in user:
        tab = api_req(f"https://wapact.189.cn:9001/gateway/golden/api/queryTurnTable?userType=1&_={int(time.time()*1000)}", method='GET', headers={'Authorization': user['Authorization']})
        if isinstance(tab, dict) and tab.get('code') == 0:
            act_id = tab['biz']['wzTurntable']['code']
            chk = api_req(f"https://wapact.189.cn:9001/gateway/standQuery/detail/check?activityId={act_id}", method='GET', headers={'Authorization': user['Authorization']})
            rem = chk.get('biz', {}).get('resultInfo', {}).get('userMaximum', 0) - chk.get('biz', {}).get('resultInfo', {}).get('userCount', 0) if isinstance(chk, dict) else 0
            log(f"[抽奖] {m} 可抽{rem}次")
            for _ in range(rem):
                api_req('https://wapact.189.cn:9001/gateway/golden/api/lottery', json={"activityId": act_id}, headers={'Authorization': user['Authorization']}, log_msg=f"[抽奖结果] {m}")
                time.sleep(2)

    # 任务列表
    tasks = api_req('https://wappark.189.cn/jt-sign/webSign/homepage', json={"para": encrypt_rsa({"phone": user['phoneNbr'], "shopId": "20001", "type": "hg_qd_zrwzjd"})}, headers={'sign': sso['sign']})
    if isinstance(tasks, dict):
        for t in tasks.get('data', {}).get('biz', {}).get('adItems', []):
            if t.get('taskState') in ['0', '1'] and t.get('contentOne') == '18':
                api_req('https://wappark.189.cn/jt-sign/webSign/polymerize', json={"para": encrypt_rsa({"phone": user['phoneNbr'], "jobId": t['taskId']})}, headers={'sign': sso['sign']}, log_msg=f"[任务] {m} {t['title']}")
                time.sleep(2)

    # 喂食
    for i in range(10):
        res = api_req('https://wappark.189.cn/jt-sign/paradise/food', json={"para": encrypt_rsa({"phone": user['phoneNbr']})}, headers={'sign': sso['sign']})
        msg = res.get('resoultMsg', '') if isinstance(res, dict) else ''
        if "最大" in msg or not msg: break
        log(f"[喂食] {m} 第{i+1}次: {msg}")
        time.sleep(1)

# --- 缓存管理 ---
def load_cache():
    global cache
    if CACHE_FILE.exists(): cache = json.loads(CACHE_FILE.read_text(encoding='utf-8'))
def save_cache():
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')

# --- 主入口 ---
def main():
    load_cache()
    accs = split_accounts_env(os.environ.get('chinaTelecomAccount', ''))
    if not accs:
        message = "未找到账号，请设置 chinaTelecomAccount，格式：手机号#密码"
        log(message)
        append_notify_result(1, 'chinaTelecomAccount', False, 'config_error', message)
        dispatch_notify()
        return

    for i, (p, pwd) in enumerate(accs, 1):
        start_log_index = len(global_logs)
        log(f"\n{'='*10} 账号[{i}] {mask(p)} {'='*10}")
        try:
            u = login(p, pwd)
            if not u:
                raise RuntimeError("登录失败，未获取到有效用户信息")
            sign_tasks(u)
            detail = "\n".join(global_logs[start_log_index:])
            append_notify_result(i, p, True, 'success', '执行成功', detail)
        except Exception as exc:
            detail = "\n".join(global_logs[start_log_index:])
            append_notify_result(i, p, False, 'failed', str(exc), detail)
            log(f"账号[{i}] 执行异常：{exc}")
        time.sleep(2)

    dispatch_notify()


if __name__ == '__main__':
    main()
