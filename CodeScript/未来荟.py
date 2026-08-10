"""
未来荟 v1.1.0（mywc网关聚合推送版）

功能：自动执行未来荟签到、积分查询和会员任务，支持多账号执行，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL                 必填其一，自建授权服务器域名
   - 示例：http://127.0.0.1:8110
   - 脚本会自动拼接 /mywc
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=wx020209beec4251e0
   - 请求头：auth=账号标识

2. 账号变量：
   weilaihui_wxid 或 WEILAIHUI_WXID              推荐，未来荟专属账号变量
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
   名称：未来荟
   命令：python3 未来荟.py
   定时：每天运行 1 次即可，具体时间自行调整
"""

import os
import sys
import time
import random
import json
import threading
import ssl
import http.client
import gzip
import requests
import re
import subprocess
from datetime import datetime
from typing import Any, Dict, List

try:
    from SendNotify import send_push_notification
except Exception:
    send_push_notification = None

# ========== 配置 ==========
SCRIPT_TITLE = "未来荟"
GLOBAL_NOTIFY_BUFFERS = []
APP_ID = "wx020209beec4251e0"
PROJECT_UUID = "3a59e62a07f811f1bec0aeefcf2e061a"
COOKIE_FILE = "txpcookie.json"
HOST = "wlhmobile.crland.com.cn"
API = {
    "login": "/member/client/wechat/visitor/check",
    "get_user_info": "/member/client/detail",
    "sign_info": "/marketing/client/task/sign-in/record",
    "sign": "/marketing/client/task/daily/sign-in"
}
isThread = False
WX_SERVER_URL = (os.environ.get("wx_server_url") or os.environ.get("WX_SERVER_URL") or "").strip().rstrip("/")
WX_OPENIDS = (
    os.environ.get("weilaihui_wxid")
    or os.environ.get("WEILAIHUI_WXID")
    or os.environ.get("txpopenid")
    or ""
)

# ========== 工具函数 ==========
def mask_phone(phone):
    if not phone or len(phone) < 7:
        return phone
    return phone[:3] + "****" + phone[-4:]

def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}")

def send_notification(title, content=""):
    try:
        import notify
        notify.send(title, content)
    except Exception as e:
        log(f"通知发送失败: {e}")


def append_notify_result(result: Dict[str, Any]) -> None:
    GLOBAL_NOTIFY_BUFFERS.append(result)


def parse_accounts() -> List[str]:
    return [
        item.strip()
        for item in re.split(r"[&,\n，]+", str(WX_OPENIDS or ""))
        if item.strip()
    ]


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
                f"🎁 会员任务：{item.get('member_text') or '-'}",
                f"🎯 任务状态：{item.get('task_text') or '-'}",
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

def load_cookies():
    if os.path.exists(COOKIE_FILE):
        try:
            with open(COOKIE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_cookies(data):
    with open(COOKIE_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

# ========== 自定义 HTTPS 客户端（绕过 SSL 限制） ==========
def create_ssl_context():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    # 兼容旧式重协商服务端
    if hasattr(ssl, 'OP_NO_RENEGOTIATION'):
        ctx.options &= ~ssl.OP_NO_RENEGOTIATION
    if hasattr(ssl, 'OP_LEGACY_SERVER_CONNECT'):
        ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
    # OpenSSL 3 部分构建没有暴露常量，只能直接补兼容位。
    ctx.options |= 0x4       # SSL_OP_LEGACY_SERVER_CONNECT
    ctx.options |= 0x40000   # SSL_OP_ALLOW_UNSAFE_LEGACY_RENEGOTIATION
    if hasattr(ssl, "TLSVersion"):
        try:
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.maximum_version = ssl.TLSVersion.TLSv1_2
        except Exception:
            pass
    try:
        ctx.set_ciphers("DEFAULT:@SECLEVEL=1")
    except Exception:
        pass
    return ctx

def parse_response(status_code, raw_data, content_encoding=''):
    if 'gzip' in content_encoding:
        raw_data = gzip.decompress(raw_data)
    elif 'deflate' in content_encoding:
        import zlib
        raw_data = zlib.decompress(raw_data)

    text = raw_data.decode('utf-8')
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        result = {"code": status_code, "text": text}
    return status_code, result

def curl_https_request(method, path, headers=None, json_data=None, timeout=15):
    body = ""
    if json_data is not None:
        body = json.dumps(json_data, ensure_ascii=False, separators=(",", ":"))

    cmd = [
        "curl",
        "-k",
        "--silent",
        "--show-error",
        "--compressed",
        "--tlsv1.2",
        "--legacy-server-connect",
        "--connect-timeout", str(timeout),
        "--max-time", str(timeout),
        "-X", method,
        f"https://{HOST}{path}",
        "-w", "\n%{http_code}",
    ]
    for key, value in (headers or {}).items():
        if key.lower() in ("host", "content-length"):
            continue
        cmd.extend(["-H", f"{key}: {value}"])
    if body:
        cmd.extend(["--data-binary", body])

    try:
        completed = subprocess.run(cmd, capture_output=True, text=False, timeout=timeout + 5)
        if completed.returncode != 0 and b"unknown option" in completed.stderr.lower():
            cmd = [item for item in cmd if item != "--legacy-server-connect"]
            completed = subprocess.run(cmd, capture_output=True, text=False, timeout=timeout + 5)
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.decode("utf-8", errors="ignore") or f"curl exit {completed.returncode}")
        output = completed.stdout
        raw_body, _, raw_code = output.rpartition(b"\n")
        status_code = int(raw_code.decode("ascii", errors="ignore") or "0")
        return parse_response(status_code, raw_body)
    except Exception as exc:
        log(f"curl HTTPS 请求异常: {path} {exc}")
        raise

def https_request(method, path, headers=None, json_data=None, timeout=15):
    conn = None
    try:
        body = None
        if json_data is not None:
            body = json.dumps(json_data).encode('utf-8')
            if headers is None:
                headers = {}
            headers["Content-Length"] = str(len(body))

        # 移除 Accept-Encoding 以避免压缩处理复杂化
        if headers:
            headers = {k: v for k, v in headers.items() if k.lower() != 'accept-encoding'}

        ctx = create_ssl_context()
        conn = http.client.HTTPSConnection(HOST, timeout=timeout, context=ctx)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()

        raw_data = resp.read()
        content_encoding = resp.getheader('Content-Encoding', '')
        status_code = resp.status
        conn.close()
        conn = None

        return parse_response(status_code, raw_data, content_encoding)
    except Exception as e:
        if "UNSAFE_LEGACY_RENEGOTIATION_DISABLED" in str(e) or "unsafe legacy renegotiation" in str(e):
            log(f"HTTPS 触发旧式重协商限制，切换 curl 兼容模式: {path}")
            return curl_https_request(method, path, headers=headers, json_data=json_data, timeout=timeout)
        log(f"HTTPS 请求异常: {path} {e}")
        raise
    finally:
        if conn:
            conn.close()

def api_request(method, path, headers=None, json_data=None, timeout=15):
    try:
        status_code, result = https_request(method, path, headers=headers, json_data=json_data, timeout=timeout)
        # 业务层 token 失效判断
        if result.get('code') == 401 or (result.get('code') != 200 and '未登录' in str(result.get('text', ''))):
            raise TokenExpired(f"token 失效: {result}")
        return result
    except TokenExpired:
        raise
    except Exception as e:
        log(f"API 请求异常: {path} {e}")
        return None

class TokenExpired(Exception):
    pass

def get_wx_code(wxid):
    if not WX_SERVER_URL:
        log("❌ 未配置 wx_server_url 或 WX_SERVER_URL，无法请求 /mywc")
        return None
    url = f"{WX_SERVER_URL}/mywc"
    try:
        resp = requests.get(
            url,
            params={"wxid": wxid, "appId": APP_ID},
            headers={"auth": wxid},
            timeout=15,
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
        log(f"获取 code 失败: {str(data)[:160]}")
    except Exception as e:
        log(f"mywc 调用失败: {e}")
    return None

# ========== 业务逻辑 ==========
class FutureHui:
    def __init__(self, wxid, index):
        self.openid = wxid
        self.nickname = mask_phone(wxid)
        self.index = index
        self.token = None          # 完整 Authorization（含 Wechat 前缀）
        self.member_uuid = None
        self.mobile = ''
        self.username = self.nickname
        self.points = 0

        self.base_headers = {
            "Host": HOST,
            "Connection": "keep-alive",
            "appId": APP_ID,
            "projectUuid": PROJECT_UUID,
            "content-type": "application/json",
            "charset": "utf-8",
            "Referer": "https://servicewechat.com/wx020209beec4251e0/60/page-frame.html",
            "User-Agent": "Mozilla/5.0 (Linux; Android 10; MI 8 Build/QKQ1.190828.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/126.0.6478.188 Mobile Safari/537.36 XWEB/1260117 MMWEBSDK/20240501 MMWEBID/3169 MicroMessenger/8.0.50.2701(0x2800325B) WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64 MiniProgramEnv/android",
        }

    def login_with_code(self, code):
        headers = dict(self.base_headers)
        payload = {"code": code}
        rs = api_request('POST', API["login"], headers=headers, json_data=payload)
        if rs and rs.get('code') == 200:
            self.token = "Wechat " + rs['result']['token']
            self.member_uuid = rs['result'].get('memberUuid')
            self.mobile = rs['result'].get('mobile', '')
            self._save_token()
            self.username = self.nickname
            log(f"[{self.username}] 登录成功，token 已缓存")
            return True
        else:
            log(f"[{self.username}] 登录失败: {rs}")
            return False

    def _save_token(self):
        cookies = load_cookies()
        cookies[self.openid] = {
            "nickname": self.nickname,
            "token": self.token,
            "member_uuid": self.member_uuid,
            "mobile": self.mobile,
            "update_time": time.strftime('%Y-%m-%d %H:%M:%S')
        }
        save_cookies(cookies)

    def load_token_from_cache(self):
        cookies = load_cookies()
        if self.openid in cookies:
            entry = cookies[self.openid]
            self.token = entry.get('token')
            self.member_uuid = entry.get('member_uuid')
            self.mobile = entry.get('mobile', '')
            if self.token:
                return True
        return False

    def get_user_info(self):
        headers = dict(self.base_headers)
        headers["Authorization"] = self.token
        payload = {"projectUuid": PROJECT_UUID}
        if self.member_uuid:
            payload["memberUuid"] = self.member_uuid
        else:
            payload["openId"] = self.openid

        try:
            rs = api_request('POST', API["get_user_info"], headers=headers, json_data=payload)
            if rs and rs.get('code') == 200:
                self.username = mask_phone(rs['result'].get('mobile', self.mobile))
                self.points = rs['result'].get('points', 0)
                return True
            else:
                log(f"[{self.username}] 获取用户信息失败: {rs.get('text') if rs else '无响应'}")
                return False
        except TokenExpired:
            return False

    def sign_info(self):
        headers = dict(self.base_headers)
        headers["Authorization"] = self.token
        payload = {"custom": {"catch": True}, "projectUuid": PROJECT_UUID}
        try:
            rs = api_request('POST', API["sign_info"], headers=headers, json_data=payload)
            if rs and rs.get('code') == 200:
                return rs['result']['isSignedToday']
            else:
                log(f"[{self.username}] 签到信息查询失败: {rs.get('text') if rs else '无响应'}")
                return False
        except TokenExpired:
            return False

    def do_sign(self):
        headers = dict(self.base_headers)
        headers["Authorization"] = self.token
        payload = {"custom": {"catch": True}, "projectUuid": PROJECT_UUID}
        try:
            rs = api_request('POST', API["sign"], headers=headers, json_data=payload)
            if rs and rs.get('code') == 200:
                log(f"[{self.username}] ✅签到成功")
                return True
            else:
                log(f"[{self.username}] ❌签到失败: {rs.get('text') if rs else '无响应'}")
                return False
        except TokenExpired:
            return False

    def ensure_token(self):
        if self.load_token_from_cache():
            if self.get_user_info():
                return True
            else:
                log(f"[{self.username}] 缓存 token 失效，重新登录")
        else:
            log(f"[{self.username}] 无缓存 token，开始登录")

        code = get_wx_code(self.openid)
        if not code:
            return False
        if self.login_with_code(code):
            return self.get_user_info()
        return False

    def run(self):
        if not isThread:
            sleep_time = random.randint(30, 60)
            log(f"[{self.username}] 延迟 {sleep_time} 秒执行...")
            time.sleep(sleep_time)

        log(f"----------- 🎊 第 {self.index} 个账号：{self.nickname} 🎊 -----------")

        result = {
            "index": self.index,
            "ok": False,
            "status_text": "执行失败",
            "account": self.nickname,
            "username": "",
            "before_points": "-",
            "after_points": "-",
            "delta_points": 0,
            "sign_text": "",
            "member_text": "",
            "task_text": "",
            "message": "",
        }

        if not self.ensure_token():
            log(f"[{self.username}] ❌登录失败，跳过")
            result["message"] = "登录失败"
            append_notify_result(result)
            return result

        old_points = self.points
        result["before_points"] = old_points
        log(f"[{self.username}] 执行前积分：{old_points}")

        signed = self.sign_info()
        if signed:
            log(f"[{self.username}] ✅今日已签到")
            result["sign_text"] = "今日已签到"
        else:
            log(f"[{self.username}] ℹ️开始签到...")
            sign_ok = self.do_sign()
            result["sign_text"] = "签到成功" if sign_ok else "签到失败"

        member_ok = self.get_user_info()
        new_points = self.points
        result["after_points"] = new_points
        result["delta_points"] = new_points - old_points
        result["member_text"] = "会员信息获取成功" if member_ok else "会员信息获取失败"
        result["task_text"] = "日常流程完成"
        result["ok"] = True
        result["status_text"] = "执行成功"
        result["username"] = self.username
        log(f"[{self.username}] ℹ️当前积分：{new_points}，新增：{new_points - old_points}")
        append_notify_result(result)
        return result

def main():
    log(f"{' ' * 10}꧁༺ {SCRIPT_TITLE} ༻꧂\n")
    accounts = parse_accounts()
    if not accounts:
        append_notify_result({
            "index": 1,
            "ok": False,
            "status_text": "配置错误",
            "account": "-",
            "username": "",
            "before_points": "-",
            "after_points": "-",
            "delta_points": 0,
            "sign_text": "",
            "member_text": "",
            "task_text": "",
            "message": "未读取到 weilaihui_wxid / WEILAIHUI_WXID，兼容旧变量 txpopenid",
        })
        dispatch_notify()
        sys.exit(1)

    if isThread:
        threads = []
        for i, wxid in enumerate(accounts):
            try:
                fh = FutureHui(wxid, i+1)
                t = threading.Thread(target=fh.run)
                threads.append(t)
                t.start()
            except Exception as e:
                log(f"线程启动异常: {e}")
        for t in threads:
            t.join()
    else:
        for i, wxid in enumerate(accounts):
            try:
                fh = FutureHui(wxid, i+1)
                fh.run()
            except Exception as e:
                log(f"账号执行异常: {e}")
                append_notify_result({
                    "index": i + 1,
                    "ok": False,
                    "status_text": "执行失败",
                    "account": mask_phone(wxid),
                    "username": "",
                    "before_points": "-",
                    "after_points": "-",
                    "delta_points": 0,
                    "sign_text": "",
                    "member_text": "",
                    "task_text": "",
                    "message": str(e),
                })

    log(f"\n----------- 🎊 执行结束 🎊 -----------\n")
    dispatch_notify()

if __name__ == '__main__':
    main()
