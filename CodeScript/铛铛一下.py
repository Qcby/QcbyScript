#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
铛铛一下 v1.1.0（mywc网关聚合推送版）

功能：自动执行铛铛一下小程序签到、抽奖、答题、余额查询与满足门槛提现，支持多账号执行，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL                   必填其一，自建授权服务器域名
   - 示例：http://127.0.0.1:8110
   - 脚本会自动拼接 /mywc
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=wxe378d2d7636c180e
   - 请求头：auth=账号标识

2. 账号变量：
   dd1x_wxid 或 DD1X_WXID                           推荐，铛铛一下专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：wxid_a&wxid_b&wxid_c
   - 兼容旧变量 WXID 读取，但不推荐继续使用

3. Token 兼容变量：
   dd1x                                             可选，兼容旧 token 模式
   - 格式：token#base_url=https://vues.dd1x.cn
   - 多账号一行一个

4. 推送变量：
   需要同目录存在 SendNotify.py，脚本结束后会统一调用 send_push_notification。
   常用推送变量如下，配置任意一种即可：
   QYWX_KEY                                         企业微信机器人 key
   PUSH_PLUS_TOKEN                                  PushPlus token
   PUSH_KEY                                         Server 酱 key
   DD_BOT_TOKEN 或 DD_BOT_SECRET                     钉钉机器人 token/secret
   FSKEY                                            飞书机器人 key

5. 青龙任务建议：
   名称：铛铛一下
   命令：python3 铛铛一下.py
   定时：每天运行 1 次即可，具体时间自行调整
"""

import base64
import json
import os
import random
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin, urlparse

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import requests

DEFAULT_BASE_URL = "https://vues.dd1x.cn"
APP_ID = "wxe378d2d7636c180e"
CHANNEL_ID = "154"
TRACK_URL = "https://data.dd1x.cn/api/test_api"
WITHDRAW_THRESHOLD = 0.3
SCRIPT_TITLE = "铛铛一下"
GLOBAL_NOTIFY_BUFFERS: List[Dict[str, Any]] = []

# ── 自建授权服务变量 ──
WX_SERVER_URL = os.environ.get("wx_server_url") or os.environ.get("WX_SERVER_URL") or ""

COMMON_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf2541022) XWEB/16467",
    "Content-Type": "application/json",
    "Accept": "*/*",
    "Referer": f"https://servicewechat.com/{APP_ID}/801/page-frame.html",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

class Dd1xAuto:
    def __init__(self, token: str, base_url: str, account_name: str = ""):
        self.account_name = account_name  # 此时传入的已是原始真实的 wxid
        self.token = token
        self.base_url = base_url
        self.open_id = decode_openid_from_jwt(token)
        self.headers = {**COMMON_HEADERS, "token": token}
        self.process_id = ""
        
        # 核心痛点修复：去除多余前缀，报表直接以真实 wxid 挂名
        self.summary = {
            "name": account_name,
            "ok": False,
            "status": "running",
            "message": "",
            "balance_init": "0.00",
            "balance_end": "0.00",
            "sign": "未执行",
            "lottery": [],
            "answer": "未执行",
            "withdraw": "未执行"
        }

    def log(self, message: str):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        account_prefix = f"[{self.account_name}] " if self.account_name else ""
        print(f"[{timestamp}] {account_prefix}{message}")

    def random_delay(self, min_seconds: int = 2, max_seconds: int = 8):
        delay = random.uniform(min_seconds, max_seconds)
        time.sleep(delay)

    def make_request(self, method: str, path: str, body: Any = None) -> Optional[Dict]:
        url = urljoin(self.base_url, path)
        try:
            if method.upper() == "GET":
                response = requests.get(url, headers=self.headers, timeout=30)
            else:
                response = requests.post(url, headers=self.headers, json=body or {}, timeout=30)
            try:
                return response.json()
            except Exception:
                return {"code": -1, "msg": "JSON解析失败"}
        except Exception as e:
            return {"code": -1, "msg": str(e)}

    def api_get(self, path: str) -> Dict: return self.make_request("GET", path)
    def api_post(self, path: str, body: Any = None) -> Dict: return self.make_request("POST", path, body)

    def assert_ok(self, resp: Optional[Dict]) -> None:
        if resp is None or resp.get("code") != 0:
            raise RuntimeError(str(resp.get("msg") if resp else "请求无响应"))

    def init_session(self) -> None:
        access_res = self.api_get(f"/front/accessXcx?channelId={CHANNEL_ID}&processId=")
        process_id = str((access_res or {}).get("data") or "")
        if process_id:
            self.process_id = process_id
            self.api_get(f"/front/accessXcx?channelId={CHANNEL_ID}&processId={process_id}")

    def verify_token(self) -> str:
        user_info = self.api_get("/ali/getUserInfo")
        self.assert_ok(user_info)
        return "ok"

    def get_member_info(self, is_init=True) -> Dict:
        member_info = self.api_get("/api/v2/get_member_info")
        self.assert_ok(member_info)
        money = str(member_info.get('data', {}).get('money', '0.00'))
        if is_init:
            self.summary["balance_init"] = money
        else:
            self.summary["balance_end"] = money
        return member_info

    def sign_in(self) -> bool:
        sign = self.api_get("/api/v2/sign_join")
        if sign and sign.get("code") == 0:
            reward = sign.get('data', {}).get('name', '未知奖励')
            self.summary["sign"] = f"✅ 成功 ({reward})"
            return True
        msg = str((sign or {}).get("msg") or "签到失败")
        if "签" in msg and ("过" in msg or "已经" in msg):
            self.summary["sign"] = "⚠️ 已签到过"
            return False
        self.summary["sign"] = f"❌ 失败 ({msg})"
        return False

    def lottery(self) -> None:
        lottery_info = self.api_get(f"/front/activity/get_lottery_info?id=13&channelId={CHANNEL_ID}")
        if lottery_info and lottery_info.get("code") == 0:
            times = max(int(lottery_info.get("data", {}).get("member_count") or 0), 0)
            for _ in range(times):
                result = self.api_get("/front/activity/get_lottery_result?id=13")
                if result and result.get("code") == 0:
                    prize = result.get('data', {}).get('prizeName', '未知')
                    self.summary["lottery"].append(prize)
                    record_id = result.get("data", {}).get("record_id")
                    if record_id is not None:
                        self.api_get(f"/front/activity/update_lottery_result?id={quote(str(record_id))}")
                self.random_delay(1, 2)

    def answer_questions(self) -> None:
        questions = self.api_get("/api/questions/get_questions")
        if not questions or questions.get("code") != 0:
            self.summary["answer"] = "❌ 获取题目失败"
            return
        answer_payload = build_answer_payload(questions.get("data"))
        if not answer_payload:
            self.summary["answer"] = "⚠️ 无题目"
            return
        judge = self.api_post("/api/questions/judge", answer_payload)
        if judge and judge.get("code") == 0:
            if judge.get("data") == 2:
                self.summary["answer"] = "⚠️ 已答过"
            else:
                self.summary["answer"] = "✅ 成功"
        else:
            self.summary["answer"] = "❌ 提交失败"

    def withdraw(self) -> None:
        member_info = self.api_get("/api/v2/get_member_info")
        current_money = float(member_info.get("data", {}).get("money") or 0) if member_info else 0.0
        self.summary["balance_end"] = f"{current_money:.2f}"

        if current_money < WITHDRAW_THRESHOLD:
            self.summary["withdraw"] = f"未达门槛({WITHDRAW_THRESHOLD}元)"
            return

        withdrawal_list = self.api_get("/api/h/get_withdrawal_trade_list")
        trade_list = withdrawal_list if isinstance(withdrawal_list, list) else (withdrawal_list.get("data") if isinstance((withdrawal_list or {}).get("data"), list) else [])
        available = [item for item in trade_list if not item.get("disabled") and float(item.get("money") or 0) >= WITHDRAW_THRESHOLD]
        
        if not available:
            self.summary["withdraw"] = "无满足门槛的订单"
            return

        total_money = f"{sum(float(item.get('money') or 0) for item in available):.2f}"
        withdraw_res = self.api_post("/api/h/withdrawal", {"totalMoney": total_money, "type": 1, "withdrawalDetailPojoList": available})
        if withdraw_res and withdraw_res.get("code") == 1:
            self.summary["withdraw"] = f"✅ 提现成功 ({total_money}元)"
            self.summary["balance_end"] = "0.00"
        else:
            self.summary["withdraw"] = f"❌ 失败 ({(withdraw_res or {}).get('msg') or '未知'})"

    def run_daily_tasks(self):
        self.log(f"🔄 开始执行...")
        try:
            self.init_session()
            self.verify_token()
            self.get_member_info(is_init=True)
            self.sign_in()
            self.random_delay(1, 3)
            self.lottery()
            self.random_delay(1, 3)
            self.answer_questions()
            self.random_delay(1, 3)
            self.withdraw()
            self.summary["ok"] = True
            self.summary["status"] = "success"
            self.summary["message"] = "执行成功"
            self.log(f"✅ 执行完成")
        except Exception as e:
            self.log(f"❌ 运行异常: {e}")
            self.summary["ok"] = False
            self.summary["status"] = "failed"
            self.summary["message"] = str(e)
            self.summary["sign"] = f"❌ 脚本崩溃: {e}"
        finally:
            GLOBAL_NOTIFY_BUFFERS.append(self.summary)

def get_config_var(var_name: str, default: str = "") -> str:
    value = os.getenv(var_name)
    if value is None: value = load_dotenv_values().get(var_name, default)
    return str(value or "").strip()

_DOTENV_CACHE: Optional[Dict[str, str]] = None

def load_dotenv_values() -> Dict[str, str]:
    global _DOTENV_CACHE
    if _DOTENV_CACHE is not None: return _DOTENV_CACHE
    values: Dict[str, str] = {}
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            key, value = line.split("=", 1)
            if key.strip(): values[key.strip()] = value.strip().strip('"').strip("'")
    _DOTENV_CACHE = values
    return values

def mask_secret(value: str, left: int = 6, right: int = 4) -> str:
    value = str(value or "")
    if len(value) <= left + right: return "***" if value else ""
    return f"{value[:left]}***{value[-right:]}"

def split_config_list(value: str) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in re.split(r"[&，,\n\r\s@]+", value) if item.strip()]

def split_accounts(value: str) -> List[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]

def parse_account_line(line: str) -> Optional[Dict[str, str]]:
    parts = [part.strip() for part in line.split("#") if part.strip()]
    if not parts: return None
    token = parts[0]
    base_url = DEFAULT_BASE_URL
    for part in parts[1:]:
        if part.lower().startswith("base_url="): base_url = part.split("=", 1)[1].strip()
    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc: return None
    return {"token": token, "base_url": f"{parsed.scheme}://{parsed.netloc}", "raw": line, "wxid": "抓包Token"}

def decode_openid_from_jwt(token: str) -> str:
    try:
        parts = token.split(".")
        if len(parts) < 2: return ""
        payload = parts[1].replace("-", "+").replace("_", "/")
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.b64decode(payload).decode("utf-8"))
        return data.get("openid") or data.get("openId") or ""
    except Exception: return ""

def build_answer_payload(data: Any) -> List[Dict]:
    if not isinstance(data, list): return []
    payload = []
    for item in data:
        try:
            payload.append({"answerId": int(item.get("correctAnswerId")), "questionsId": int(item.get("id"))})
        except Exception: continue
    return payload

def get_wx_code(wxid: str) -> str:
    if not WX_SERVER_URL: raise RuntimeError("未配置环境变量 wx_server_url")
    base_url = WX_SERVER_URL.strip().rstrip("/")
    response = requests.get(f"{base_url}/mywc", params={"wxid": wxid, "appId": APP_ID}, headers={"auth": wxid}, timeout=35)
    response.raise_for_status()
    result = response.json()
    data_obj = result.get("data") if isinstance(result.get("data"), dict) else {}
    code = result.get("code") or data_obj.get("code") or result.get("wx_code") or data_obj.get("wx_code")
    if code:
        return str(code)
    raise RuntimeError(f"获取 code 失败: {result}")

def exchange_dd1x_token(server_url: str, wx_code: str, channel_id: str) -> Dict[str, str]:
    response = requests.get(f"{server_url}/wechat/login", headers=COMMON_HEADERS, params={"code": wx_code, "channelId": channel_id}, timeout=30)
    response.raise_for_status()
    result = response.json()
    if result.get("code") == 0 and isinstance(result.get("data"), dict):
        return {"token": str(result["data"].get("token") or ""), "openid": str(result["data"].get("openid") or "")}
    raise RuntimeError(f"登录失败: {result.get('msg')}")

def build_account_from_wxid(wxid: str, channel_id: str) -> Dict[str, Any]:
    wx_code = get_wx_code(wxid)
    base_url = get_config_var("DD1X_BASE_URL", DEFAULT_BASE_URL) or DEFAULT_BASE_URL
    parsed = urlparse(base_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else DEFAULT_BASE_URL
    login_data = exchange_dd1x_token(base_url, wx_code, channel_id)
    return {"token": login_data["token"], "base_url": base_url, "openid": login_data["openid"], "wxid": wxid}

def append_failed_result(name: str, message: str, status: str = "failed") -> None:
    GLOBAL_NOTIFY_BUFFERS.append({
        "name": name,
        "ok": False,
        "status": status,
        "message": message,
        "balance_init": "0.00",
        "balance_end": "0.00",
        "sign": "未执行",
        "lottery": [],
        "answer": "未执行",
        "withdraw": "未执行",
    })


def parse_wxid_accounts(channel_id: str) -> List[Dict[str, Any]]:
    wxid_raw = (
        get_config_var("dd1x_wxid")
        or get_config_var("DD1X_WXID")
        or get_config_var("WXID")
    )
    wxids = split_config_list(wxid_raw)
    accounts: List[Dict[str, Any]] = []
    for wxid in wxids:
        try:
            account = build_account_from_wxid(wxid, channel_id)
            print(f"微信号 {mask_secret(wxid, 4, 3)} 换票成功")
            accounts.append(account)
        except Exception as exc:
            message = f"换票失败: {exc}"
            print(f"微信号 {mask_secret(wxid, 4, 3)} {message}")
            append_failed_result(wxid, message)
    return accounts

def build_notify_report(total_accounts: int = 0) -> str:
    total = total_accounts or len(GLOBAL_NOTIFY_BUFFERS)
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

    for index, item in enumerate(GLOBAL_NOTIFY_BUFFERS, 1):
        ok = bool(item.get("ok"))
        account_icon = "🧑‍💻" if ok else "🧟"
        status_icon = "✅" if ok else "❌"
        status_text = "执行成功" if ok else ("配置错误" if item.get("status") == "config_error" else "执行失败")
        lines.append(f"{account_icon} 【账号{index}】{item.get('name') or '未知账号'}")
        lines.append(f"{status_icon} 状态：{status_text}")
        if ok:
            lines.append(f"💰 账户余额：始 {item.get('balance_init')} 元 ➔ 终 {item.get('balance_end')} 元")
            lines.append(f"📅 签到任务：{item.get('sign')}")
            lines.append(f"📝 答题任务：{item.get('answer')}")
            lines.append(f"💳 提现动作：{item.get('withdraw')}")
            if item.get("lottery"):
                from collections import Counter
                counts = Counter(item.get("lottery") or [])
                lottery_str = ", ".join([f"{k}x{v}" for k, v in counts.items()])
                lines.append(f"🎰 抽奖成果：🎁 {lottery_str}")
            else:
                lines.append("🎰 抽奖成果：0次机会/未中奖")
        else:
            lines.append(f"🧨 原因：{item.get('message') or '未知错误'}")
        lines.append("------------------------------")
    return "\n".join(lines)


def dispatch_notify(total_accounts: int = 0) -> None:
    if not GLOBAL_NOTIFY_BUFFERS:
        return
    title = f"{SCRIPT_TITLE}任务执行总结"
    content = build_notify_report(total_accounts)
    print("\n[聚合推送报表阅览]\n" + content)
    try:
        from SendNotify import send_push_notification
        send_push_notification(title, content)
    except Exception as exc:
        print(f"[警告] 聚合推送失败：{exc}")


def main():
    print("==================================================")
    print("🚀 铛铛一下纯 WXID 推送版启动...")
    print("==================================================")
    
    channel_id = get_config_var("DD1X_CHANNEL_ID", CHANNEL_ID) or CHANNEL_ID
    
    accounts = []
    lines = split_accounts(get_config_var("dd1x", ""))
    for line in lines:
        acc = parse_account_line(line)
        if acc: accounts.append(acc)
        
    wx_accounts = parse_wxid_accounts(channel_id)
    accounts.extend(wx_accounts)

    if not accounts:
        message = "未找到有效账号配置，请配置 dd1x_wxid 或 DD1X_WXID"
        print(f"❌ {message}")
        append_failed_result("dd1x_wxid", message, "config_error")
        dispatch_notify(len(GLOBAL_NOTIFY_BUFFERS))
        return

    for i, acc in enumerate(accounts, 1):
        try:
            automation = Dd1xAuto(token=acc["token"], base_url=acc["base_url"], account_name=acc.get("wxid", f"账户{i}"))
            automation.run_daily_tasks()
            if i < len(accounts):
                time.sleep(random.randint(3, 7))
        except Exception as e:
            message = f"账户 {i} 异常中断: {e}"
            print(message)
            append_failed_result(acc.get("wxid", f"账户{i}"), message)

    exchange_failed = sum(1 for item in GLOBAL_NOTIFY_BUFFERS if "换票失败" in str(item.get("message")))
    dispatch_notify(len(accounts) + exchange_failed)

if __name__ == "__main__":
    main()
