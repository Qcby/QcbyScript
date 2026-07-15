#!/usr/bin/env python3
"""
交个朋友签到 v4.0.0（mywc网关聚合推送版）

功能：自动完成交个朋友小程序签到及可领取积分任务，支持多账号，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL   必填其一，自建授权服务器地址
   - 脚本自动请求：GET {网关}/mywc?wxid=账号标识&appId=wx3b294e7a0ba29bc3
   - 请求头：auth=账号标识

2. 账号变量：
   jgpy_wxid 或 JGPY_WXID          推荐，交个朋友专属微信账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔

3. 推送配置：
   需要同目录存在 SendNotify.py，脚本结束后统一调用 send_push_notification。

依赖：requests
青龙任务建议：task CodeScript/jgpy_yyb.py
"""

import json
import os
import random
import re
import sys
import time
from datetime import datetime
from urllib.parse import quote

import requests


APP_ID = "wx3b294e7a0ba29bc3"
BASE_URL = "https://smp-api.iyouke.com"
APP_VERSION = "2.30.3"
# local package version folder packages/wx3b294e7a0ba29bc3/98
PAGE_VERSION = "98"
MAX_AUTH_RETRIES = 3
ACCOUNT_GAP = (3, 6)
ACTION_GAP = (0.4, 0.9)
WX_SERVER_URL = (os.getenv("wx_server_url") or os.getenv("WX_SERVER_URL") or "").strip().rstrip("/")
ACCOUNT_RAW = os.getenv("jgpy_wxid") or os.getenv("JGPY_WXID") or ""
NOTIFY_RESULTS = []

NOTIFY_IMPORT_ERROR = ""
try:
    from SendNotify import send_push_notification
except Exception as exc:
    send_push_notification = None
    NOTIFY_IMPORT_ERROR = str(exc)

# mini-program GET_INTEGRAL_WAY (finishable without complex UI)
SIMPLE_TASK_TYPES = {
    1,   # USER_INFO
    4,   # POINTS_SIGIN related claim
    19,  # BROWSE_TASK claim after browse (try claim if unclaimed)
}

COMMON_HEADERS = {
    "Host": "smp-api.iyouke.com",
    "appid": APP_ID,
    "version": APP_VERSION,
    "envversion": "release",
    "xy-extra-data": f"appid={APP_ID};version={APP_VERSION};envVersion=release;senceId=1089",
    "content-type": "application/json",
    "referer": f"https://servicewechat.com/{APP_ID}/{PAGE_VERSION}/page-frame.html",
    "user-agent": (
        "Mozilla/5.0 (Linux; Android 14; KB2000 Build/UKQ1.230924.001; wv) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/146.0.7680.177 "
        "Mobile Safari/537.36 XWEB/1460075 MMWEBSDK/20260202 MMWEBID/2058 "
        "MicroMessenger/8.0.70.3060(0x28004652) WeChat/arm64 Weixin NetType/WIFI "
        "Language/zh_CN ABI/arm64 MiniProgramEnv/android"
    ),
}


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def short_json(value, limit=180):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else f"{text[:limit]}..."


def action_sleep():
    time.sleep(random.uniform(*ACTION_GAP))


def api_ok(res):
    res = res or {}
    if res.get("success") is True:
        return True
    if res.get("error") in (0, "0", None) and (res.get("data") is not None or res.get("success") is not False):
        # many iyouke APIs: {error:0, data:...}
        if "error" in res:
            return int(res.get("error") or 0) == 0
    if str(res.get("code")) in ("0", "200", "10000"):
        return True
    return False


def split_accounts(raw):
    return [item.strip() for item in re.split(r"[&,，\r\n]+", str(raw or "")) if item.strip()]


def mask_account(value):
    text = str(value or "")
    if len(text) <= 8:
        return "***"
    return f"{text[:4]}***{text[-4:]}"


def read_accounts():
    accounts = split_accounts(ACCOUNT_RAW)
    if not accounts:
        print("❌ 未配置 jgpy_wxid 或 JGPY_WXID")
    return accounts


def get_wx_code(wxid):
    if not WX_SERVER_URL:
        print("✖️ 获取Code失败: 未配置 wx_server_url 或 WX_SERVER_URL")
        return None

    last_error = "unknown"
    for attempt in range(1, MAX_AUTH_RETRIES + 1):
        try:
            response = requests.get(
                f"{WX_SERVER_URL}/mywc",
                params={"wxid": wxid, "appId": APP_ID},
                headers={"auth": wxid},
                timeout=20,
            )
            response.raise_for_status()
            data = response.json()
            payload = data.get("data") if isinstance(data, dict) else None
            code = payload.get("code") if isinstance(payload, dict) else payload
            code = code or (data.get("code") if isinstance(data, dict) else None)
            if isinstance(code, str) and code.strip():
                print("✅ 获取Code成功")
                return code.strip()
            last_error = short_json(data, 240)
        except Exception as exc:
            last_error = str(exc)
        if attempt < MAX_AUTH_RETRIES:
            print(f"✖️ 获取Code失败，重试{attempt}/{MAX_AUTH_RETRIES - 1}: {last_error}")
            time.sleep(random.uniform(1.5, 3.0))
    print(f"✖️ 获取Code失败: {last_error}")
    return None


def build_notify_report():
    total = len(NOTIFY_RESULTS)
    success = sum(1 for item in NOTIFY_RESULTS if item.get("ok"))
    lines = [
        "==============================",
        f"🕒 执行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"📊 统计数据：成功 {success} / 总计 {total}",
        f"✅ 成功账号：{success} 个",
        f"❌ 失败账号：{total - success} 个",
        "==============================",
    ]
    for item in NOTIFY_RESULTS:
        ok = bool(item.get("ok"))
        lines.extend([
            f"{'🧑‍💻' if ok else '🧟'} 【账号{item.get('index', '-')}】{item.get('account', '未知')}",
            f"{'✅' if ok else '❌'} 状态：{item.get('status', '未知')}",
        ])
        if ok:
            lines.extend([
                f"👤 昵称：{item.get('nickname', '未知用户')}",
                f"📅 签到：{item.get('sign_msg', '未知')}",
                f"💰 积分：{item.get('before_points', 0)} → {item.get('total_points', 0)}（变化 {item.get('points_delta', 0):+d}）",
                f"🎁 任务：领取 {item.get('claimed', 0)} 个，获得 {item.get('task_points', 0)} 积分",
            ])
        else:
            lines.append(f"🧨 原因：{item.get('message', '未知错误')}")
        lines.append("------------------------------")
    return "\n".join(lines)


def send_aggregate_notify():
    content = build_notify_report()
    print(f"\n{content}")
    if not send_push_notification:
        print(f"⚠️ 聚合推送跳过: SendNotify.py 导入失败: {NOTIFY_IMPORT_ERROR}")
        return
    try:
        send_push_notification("交个朋友签到", content)
        print("✅ 聚合推送完成")
    except Exception as exc:
        print(f"❌ 聚合推送失败: {exc}")


class Task:
    def __init__(self, account, index):
        self.index = index
        self.account = str(account).strip()
        self.token = ""
        self.http = requests.Session()
        self.http.headers.update(COMMON_HEADERS)

    def close(self):
        try:
            self.http.close()
        except Exception:
            pass

    def headers_auth(self):
        # mini-program uses bearer + token without space
        return {**COMMON_HEADERS, "authorization": f"bearer{self.token}"}

    def req(self, method, path, params=None, data=None, timeout=12):
        url = path if path.startswith("http") else f"{BASE_URL}{path}"
        kwargs = {"method": method, "url": url, "headers": self.headers_auth(), "timeout": timeout}
        if params is not None:
            kwargs["params"] = params
        if data is not None:
            kwargs["json"] = data
        resp = self.http.request(**kwargs)
        try:
            return resp.json()
        except Exception:
            return {"success": False, "message": resp.text, "error": -1}

    def login(self):
        code = get_wx_code(self.account)
        if not code:
            return False
        last_error = "unknown"
        for attempt in range(1, MAX_AUTH_RETRIES + 1):
            try:
                res = self.http.post(
                    f"{BASE_URL}/dtapi/appLogin",
                    json={"appType": 1, "principal": code},
                    headers=COMMON_HEADERS,
                    timeout=12,
                ).json()
                token = res.get("access_token")
                if token:
                    self.token = token
                    print(f"账号[{self.index}] ✅ Token获取成功")
                    return True
                last_error = short_json(res, 240)
            except Exception as exc:
                last_error = str(exc)
            if attempt < MAX_AUTH_RETRIES:
                print(f"账号[{self.index}] ✖️ Token获取失败，重试{attempt}/2: {last_error}")
                time.sleep(random.uniform(1.5, 3.0))
        print(f"账号[{self.index}] ✖️ Token获取失败: {last_error}")
        return False

    def get_profile(self):
        nickname = "未知用户"
        try:
            res = self.req("get", "/dtapi/userProfile/get")
            rows = res.get("data") or []
            if isinstance(rows, list):
                for item in rows:
                    if item.get("tag") == "nickname":
                        nickname = item.get("value") or nickname
                        break
        except Exception:
            pass
        return nickname

    def get_points_balance(self):
        try:
            res = self.req("get", "/dtapi/points/user/centerInfo")
            data = res.get("data") or {}
            return data.get("pointsBalance", 0)
        except Exception:
            return 0

    def get_sign_info(self):
        try:
            res = self.req("get", "/dtapi/pointsSign/user/pointsInfo/query")
            data = res.get("data") or {}
            sign_num = data.get("totalSignCount") or data.get("continuousSignDays") or data.get("seriesDays") or 0
            is_signed = bool(data.get("signTodayResult", False))
            return sign_num, is_signed, data
        except Exception:
            return 0, False, {}

    def do_sign(self):
        # reverse: GET /pointsSign/user/sign?date=YYYY/MM/DD  (setPointsSignUserSign)
        today = datetime.now().strftime("%Y/%m/%d")
        try:
            res = self.req("get", f"/dtapi/pointsSign/user/sign?date={quote(today, safe='')}")
            # success shapes: success=true | error=0
            if res.get("success") is True or int(res.get("error") or -1) == 0:
                data = res.get("data") or {}
                reward = 0
                if isinstance(data, dict):
                    reward = data.get("signReward") or data.get("pointsNums") or data.get("reward") or 0
                    if isinstance(data.get("prize"), dict):
                        reward = reward or data["prize"].get("result") or 0
                return True, int(reward or 0), "签到成功"
            # already signed
            msg = res.get("message") or res.get("errorMsg") or short_json(res, 120)
            if any(k in str(msg) for k in ("已签", "重复", "2005")) or int(res.get("error") or 0) == 2005:
                return False, 0, "今日已签"
            return False, 0, f"签到失败: {msg}"
        except Exception as exc:
            return False, 0, f"签到异常: {exc}"

    def do_tasks(self):
        """Claim simple integral tasks: list + finishTask for unclaimed/simple types."""
        claimed = 0
        points_got = 0
        try:
            res = self.req("get", "/dtapi/points/task/list")
            if not (res.get("success") is True or int(res.get("error") or -1) == 0):
                msg = res.get("message") or res.get("errorMsg") or short_json(res, 100)
                print(f"账号[{self.index}] 任务列表: {msg}")
                return claimed, points_got
            data = res.get("data")
            tasks = []
            if isinstance(data, list):
                tasks = data
            elif isinstance(data, dict):
                for key in ("list", "records", "tasks", "taskList", "items"):
                    if isinstance(data.get(key), list):
                        tasks = data.get(key)
                        break
                if not tasks:
                    # sometimes nested groups
                    for v in data.values():
                        if isinstance(v, list):
                            tasks.extend(v)
            if not tasks:
                print(f"账号[{self.index}] 任务: 无待做/无可领")
                return claimed, points_got

            for task in tasks:
                if not isinstance(task, dict):
                    continue
                status = task.get("status")
                try:
                    status = int(status) if status is not None else -1
                except Exception:
                    status = -1
                # 0 not finished, 99 unclaimed, 1 finished
                task_type = task.get("taskType") or task.get("type")
                task_id = task.get("id") or task.get("taskId")
                name = task.get("taskName") or task.get("title") or task.get("name") or task_type
                try:
                    task_type_i = int(task_type) if task_type is not None else -1
                except Exception:
                    task_type_i = -1

                # claim unclaimed, or try simple unfinished browse/userinfo
                if status == 1:
                    continue
                if status not in (0, 99) and status != -1:
                    continue
                if status == 0 and task_type_i not in SIMPLE_TASK_TYPES and task_type_i != -1:
                    continue

                payload = {}
                if task_type is not None:
                    payload["taskType"] = task_type
                if task_id is not None and status == 99:
                    # unclaimed often only needs taskType; keep both if present
                    payload["taskId"] = task_id
                if not payload:
                    continue

                fr = self.req("post", "/dtapi/points/task/finishTask", data=payload)
                if fr.get("success") is True or int(fr.get("error") or -1) == 0:
                    claimed += 1
                    prize = ((fr.get("data") or {}).get("prize") or {}) if isinstance(fr.get("data"), dict) else {}
                    got = prize.get("result") or (fr.get("data") or {}).get("points") or 0
                    try:
                        points_got += int(got or 0)
                    except Exception:
                        pass
                    print(f"账号[{self.index}] 任务领取: {name} +{got}")
                else:
                    msg = fr.get("message") or fr.get("errorMsg") or short_json(fr, 80)
                    # quiet skip common not-ready
                    if not any(k in str(msg) for k in ("未完成", "不能", "无需", "已领", "不存在")):
                        print(f"账号[{self.index}] 任务跳过: {name} {msg}")
                action_sleep()
        except Exception as exc:
            print(f"账号[{self.index}] 任务异常: {exc}")
        return claimed, points_got

    def run(self):
        print(f"\n账号[{self.index}] 开始执行交个朋友")
        account_name = mask_account(self.account)
        try:
            if not self.login():
                return {
                    "index": self.index,
                    "account": account_name,
                    "ok": False,
                    "status": "登录失败",
                    "message": "获取 Code 或 Token 失败",
                }

            nickname = self.get_profile()
            before_points = int(self.get_points_balance() or 0)
            sign_num, is_signed, _ = self.get_sign_info()
            get_points = 0
            sign_ok = bool(is_signed)

            if is_signed:
                sign_msg = "今日已签"
            else:
                time.sleep(random.uniform(1.0, 2.5))
                sign_ok, get_points, sign_msg = self.do_sign()
                if sign_ok:
                    sign_num = int(sign_num or 0) + 1

            action_sleep()
            claimed, task_points = self.do_tasks()
            after_points = int(self.get_points_balance() or 0)

            print(f"账号[{self.index}] 昵称: {nickname}")
            print(f"账号[{self.index}] 签到: {sign_msg} | 累计{sign_num}天 | 签到+{get_points}")
            if claimed:
                print(f"账号[{self.index}] 任务: 领取{claimed}个 +{task_points}")
            print(f"账号[{self.index}] 积分: {before_points} -> {after_points}")

            return {
                "index": self.index,
                "account": account_name,
                "ok": bool(sign_ok),
                "status": "执行成功" if sign_ok else "签到失败",
                "message": sign_msg,
                "nickname": nickname,
                "sign_msg": sign_msg,
                "before_points": before_points,
                "total_points": after_points,
                "points_delta": after_points - before_points,
                "claimed": claimed,
                "task_points": task_points,
            }
        except Exception as exc:
            print(f"账号[{self.index}] ✖️ 执行异常: {exc}")
            return {
                "index": self.index,
                "account": account_name,
                "ok": False,
                "status": "执行失败",
                "message": str(exc),
            }
        finally:
            self.close()


def run():
    accounts = read_accounts()
    if not accounts:
        NOTIFY_RESULTS.append({
            "index": 0,
            "account": "未配置",
            "ok": False,
            "status": "配置错误",
            "message": "请配置 jgpy_wxid 或 JGPY_WXID",
        })
        send_aggregate_notify()
        return

    print(f"交个朋友账号数量：{len(accounts)}")
    for idx, account in enumerate(accounts, 1):
        print(f"==== 交个朋友账号 {idx}/{len(accounts)} ====")
        NOTIFY_RESULTS.append(Task(account, idx).run())
        print()
        if idx < len(accounts):
            time.sleep(random.uniform(*ACCOUNT_GAP))
    send_aggregate_notify()


if __name__ == "__main__":
    run()
