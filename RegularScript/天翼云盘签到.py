#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
天翼云盘签到 v1.1.0（专属聚合推送版）

功能：天翼云盘账号密码登录并执行每日签到，支持多账号执行，执行结束后统一聚合推送。

配置说明：
1. 账号变量：
   tyCloudAccount                                  必填，天翼云盘账号变量
   - 格式：手机号#密码
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：tyCloudAccount=18900000000#pwd1&18911111111#pwd2

2. 推送变量：
   需要同目录存在 SendNotify.py，脚本结束后会统一调用 send_push_notification。
   常用推送变量如下，配置任意一种即可：
   QYWX_KEY                                         企业微信机器人 key
   PUSH_PLUS_TOKEN                                  PushPlus token
   PUSH_KEY                                         Server 酱 key
   DD_BOT_TOKEN 或 DD_BOT_SECRET                     钉钉机器人 token/secret
   FSKEY                                            飞书机器人 key

3. 青龙任务建议：
   名称：天翼云盘签到
   命令：python3 天翼云盘签到.py
   定时：每天运行 1 次即可，具体时间自行调整
"""

import time
import os
import base64
import rsa
import requests
import re
from urllib.parse import urlparse, parse_qs
from datetime import datetime


BI_RM = list("0123456789abcdefghijklmnopqrstuvwxyz")
B64MAP = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
SCRIPT_TITLE = "天翼云盘签到"
GLOBAL_NOTIFY_BUFFERS = []



def split_account_items(value: str):
    if not value:
        return []
    return [item.strip() for item in re.split(r"[&，,\n\r]+", value) if item.strip()]


def get_accounts():
    raw = os.getenv("tyCloudAccount", "")
    accounts = []
    for index, item in enumerate(split_account_items(raw), 1):
        if "#" not in item:
            append_notify_result(index, item or "tyCloudAccount", False, "config_error", "账号格式错误，应为 手机号#密码")
            continue
        username, password = item.split("#", 1)
        username = username.strip()
        password = password.strip()
        if not username or not password:
            append_notify_result(index, username or "tyCloudAccount", False, "config_error", "账号或密码为空，应为 手机号#密码")
            continue
        accounts.append({"username": username, "password": password})
    return accounts

def append_notify_result(index, account, ok, status, message="", bonus=""):
    GLOBAL_NOTIFY_BUFFERS.append({
        "index": index,
        "account": mask_phone(account),
        "ok": bool(ok),
        "status": status or ("success" if ok else "failed"),
        "message": str(message or "").strip(),
        "bonus": str(bonus or "").strip(),
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
            lines.append(f"☁️ 签到结果：{item.get('message')}")
            if item.get("bonus"):
                lines.append(f"🎁 空间奖励：{item.get('bonus')}")
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

def mask_phone(phone):
    """仅显示手机号后四位"""
    return phone[-4:] if len(phone) >= 4 else phone

def int2char(a):
    return BI_RM[a]

def b64tohex(a):
    d = ""
    e = 0
    c = 0
    for i in range(len(a)):
        if list(a)[i] != "=":
            v = B64MAP.index(list(a)[i])
            if 0 == e:
                e = 1
                d += int2char(v >> 2)
                c = 3 & v
            elif 1 == e:
                e = 2
                d += int2char(c << 2 | v >> 4)
                c = 15 & v
            elif 2 == e:
                e = 3
                d += int2char(c)
                d += int2char(v >> 2)
                c = 3 & v
            else:
                e = 0
                d += int2char(c << 2 | v >> 4)
                d += int2char(15 & v)
    if e == 1:
        d += int2char(c << 2)
    return d

def rsa_encode(j_rsakey, string):
    rsa_key = f"-----BEGIN PUBLIC KEY-----\n{j_rsakey}\n-----END PUBLIC KEY-----"
    pubkey = rsa.PublicKey.load_pkcs1_openssl_pem(rsa_key.encode())
    return b64tohex((base64.b64encode(rsa.encrypt(f'{string}'.encode(), pubkey))).decode())

def login(username, password):
    print("🔄 正在执行登录流程...")
    s = requests.Session()
    try:
        urlToken = "https://m.cloud.189.cn/udb/udb_login.jsp?pageId=1&pageKey=default&clientType=wap&redirectURL=https://m.cloud.189.cn/zhuanti/2021/shakeLottery/index.html"
        r = s.get(urlToken)
        match = re.search(r"href\s*=\s*'([^']*autoLogin[^']*)'", r.text)
        if not match:
            print("❌ 错误：未找到动态登录页")
            return None

        auto_login_url = match.group(1)
        r = s.get(auto_login_url, allow_redirects=True)
        redirect_url = r.url  

        parsed = urlparse(r.url)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        r = s.post("https://open.e.189.cn/api/logbox/oauth2/wap/appConf.do", params=params, timeout=10)
        conf = r.json()
        if conf.get('result', '-1') != '0':
            print(f"❌ 错误：获取登录配置失败 - {conf.get('msg', '未知错误')}")
            return None

        data = conf['data']
        lt = data['lt']
        returnUrl = data['returnUrl']
        paramId = data['paramId']
        accountType = data.get('accountType', '02')
        s.headers.update({"lt": lt})

        login_html_url = re.sub(r'/index\.html', '/login.html', redirect_url)
        r = s.get(login_html_url, timeout=10)
        match = re.search(r'id="j_rsaKey"\s+value="([^"]+)"', r.text)
        if not match:
            print("❌ 错误：获取RSA密钥失败")
            return None
        j_rsakey = match.group(1)

        username_enc = rsa_encode(j_rsakey, username)
        password_enc = rsa_encode(j_rsakey, password)

        data = {
            "appKey": "cloud",
            "accountType": accountType,
            "userName": f"{{RSA}}{username_enc}",
            "password": f"{{RSA}}{password_enc}",
            "validateCode": "",
            "captchaToken": "",
            "returnUrl": returnUrl,
            "mailSuffix": "@189.cn",
            "paramId": paramId
        }

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:74.0) Gecko/20100101 Firefox/76.0',
            'Referer': 'https://open.e.189.cn/',
        }

        r = s.post(
            "https://open.e.189.cn/api/logbox/oauth2/loginSubmit.do",
            data=data,
            headers=headers,
            timeout=10
        )

        result = r.json()
        if str(result.get('result', -1)) != '0':
            print(f"❌ 登录错误：{result.get('msg', '未知错误')}")
            return None

        if 'toUrl' not in result:
            print("❌ 错误：登录响应缺少 toUrl")
            return None
        s.get(result['toUrl'])

        print("✅ 登录成功")
        return s

    except Exception as e:
        print(f"⚠️ 登录异常：{str(e)}")
        return None

def main():
    print("\n=============== 天翼云盘签到开始 ===============")
    accounts = get_accounts()

    if not accounts:
        if not GLOBAL_NOTIFY_BUFFERS:
            append_notify_result(1, "tyCloudAccount", False, "config_error", "未找到账号信息，请配置 tyCloudAccount，格式：手机号#密码")
        dispatch_notify()
        return

    for index, acc in enumerate(accounts, 1):
        username = acc["username"]
        password = acc["password"]
        masked_phone = mask_phone(username)
        print(f"\n🔔 处理账号：{masked_phone}")

        session = login(username, password)
        if not session:
            append_notify_result(index, username, False, "failed", "登录失败")
            continue

        try:
            rand = str(round(time.time() * 1000))
            sign_url = f'https://api.cloud.189.cn/mkt/userSign.action?rand={rand}&clientType=TELEANDROID&version=8.6.3&model=SM-G930K'
            headers = {
                'User-Agent': 'Mozilla/5.0 (Linux; Android 5.1.1; SM-G930K Build/NRD90M; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/74.0.3729.136 Mobile Safari/537.36 Ecloud/8.6.3 Android/22 clientId/355325117317828 clientModel/SM-G930K imsi/460071114317824 clientChannelId/qq proVersion/1.0.6',
                "Referer": "https://m.cloud.189.cn/zhuanti/2016/sign/index.jsp?albumBackupOpened=1",
                "Host": "m.cloud.189.cn",
            }
            resp = session.get(sign_url, headers=headers, timeout=15).json()
            bonus = f"+{resp.get('netdiskBonus', '?')}M"
            if str(resp.get('isSign')).lower() == "false" or resp.get('isSign') is False:
                message = "签到成功"
            else:
                message = "今日已签到"
            append_notify_result(index, username, True, "success", message, bonus)
            print(f"  ✅ | {message} {bonus}")
        except Exception as e:
            append_notify_result(index, username, False, "failed", f"签到异常：{e}")
            print(f"  ❌ | 签到异常：{e}")

    dispatch_notify()
    print("\n✅ 所有账号处理完成！")

if __name__ == "__main__":
    main()
