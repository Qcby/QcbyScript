#!/usr/bin/env python3
"""
京东更新 v1.1.0（mywc网关聚合推送版）

功能：通过微信 code 获取京东 Cookie，支持多账号执行，并按 pt_pin 自动更新青龙 JD_COOKIE，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL                 必填其一，自建授权服务器域名
   - 示例：http://127.0.0.1:8110
   - 脚本会自动拼接 /mywc
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=wx73247c7819d61796
   - 请求头：auth=账号标识

2. 账号变量：
   jd_wxid 或 JD_WXID                             推荐，京东专属账号变量
   - 兼容旧变量：WXJD
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：wxid_a&wxid_b 或 wxid_a,wxid_b

3. 青龙变量：
   QL_URL                                         青龙地址，例如 http://127.0.0.1:5700
   QL_CLIENT_ID                                   青龙应用 client_id
   QL_CLIENT_SECRET                               青龙应用 client_secret
   QL_ENV_NAME                                    可选，默认 JD_COOKIE

4. 推送变量：
   需要同目录存在 SendNotify.py，脚本结束后会统一调用 send_push_notification。
   常用推送变量如下，配置任意一种即可：
   QYWX_KEY                                       企业微信机器人 key
   PUSH_PLUS_TOKEN                                PushPlus token
   PUSH_KEY                                       Server 酱 key
   DD_BOT_TOKEN 或 DD_BOT_SECRET                  钉钉机器人 token/secret
   FSKEY                                          飞书机器人 key

5. 青龙任务建议：
   名称：京东更新
   命令：python3 京东更新.py
   定时：按业务频率自行调整
"""

from __future__ import annotations

import os
import re
import sys
import time
from datetime import datetime
from typing import Any

import requests
import urllib3

urllib3.disable_warnings()

SCRIPT_TITLE = "京东更新"
GLOBAL_NOTIFY_BUFFERS = []

WX_APP_ID = "wx73247c7819d61796"
JD_WXAPPID = "wx73247c7819d61796"
JD_APPID = "599"
JD_CLIENT_VER = "2.0.2"
JD_RETURNURL = "/pages/login/web-view/web-view"
JD_GO_TO_LOGIN = "true"

JD_HOSTSIGN = '{"noncestr":"fa945d613d5a71b35d443da51baff310","timestamp":1784565617,"signature":"9a6cf2bd4fd940138f9182e55464b1b2929cc48d"}'
JD_FORM_TS = 1784565623
JD_FORM_SIGN = "a2e880e10a4005544558e9affd18414c"

API_TIMEOUT = 15
MYWC_TIMEOUT = 40

QL_URL = (os.environ.get("QL_URL") or "").strip().rstrip("/")
QL_CLIENT_ID = (os.environ.get("QL_CLIENT_ID") or "").strip()
QL_CLIENT_SECRET = (os.environ.get("QL_CLIENT_SECRET") or "").strip()
QL_ENV_NAME = (os.environ.get("QL_ENV_NAME") or "JD_COOKIE").strip() or "JD_COOKIE"

USER_AGENT_PC = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) "
    "NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF "
    "WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf254193e) XWEB/19841"
)


class ClaimError(RuntimeError):
    pass


def split_accounts(raw_text: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"[&,\n，]+", str(raw_text or ""))
        if item.strip()
    ]


def parse_accounts() -> list[str]:
    raw_text = (
        os.environ.get("jd_wxid")
        or os.environ.get("JD_WXID")
        or os.environ.get("WXJD")
        or ""
    )
    return split_accounts(raw_text)


def get_wx_server_url() -> str:
    return (os.environ.get("wx_server_url") or os.environ.get("WX_SERVER_URL") or "").strip().rstrip("/")


def mask_account(account: str) -> str:
    account = str(account or "-").strip()
    if len(account) <= 10:
        return account
    return f"{account[:5]}***{account[-4:]}"


def append_notify_result(result: dict[str, Any]) -> None:
    GLOBAL_NOTIFY_BUFFERS.append(result)


def safe_json(response: requests.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise ClaimError(f"接口返回非 JSON：{response.text[:200]}") from exc
    return data if isinstance(data, dict) else {}


def extract_pt_pin(cookie_value: str) -> str:
    match = re.search(r"pt_pin=([^;]+)", str(cookie_value or ""))
    return match.group(1).strip() if match else ""


def ql_config_ready() -> bool:
    return all([QL_URL, QL_CLIENT_ID, QL_CLIENT_SECRET])


def ql_login() -> str:
    url = f"{QL_URL}/open/auth/token"
    params = {"client_id": QL_CLIENT_ID, "client_secret": QL_CLIENT_SECRET}
    response = requests.get(url, params=params, timeout=10, verify=False)
    response.raise_for_status()
    data = safe_json(response)
    token = ((data.get("data") or {}).get("token") or "").strip()
    if data.get("code") != 200 or not token:
        raise ClaimError(f"青龙登录失败：{data.get('message') or data}")
    return token


def ql_get_envs(token: str) -> list[dict[str, Any]]:
    response = requests.get(
        f"{QL_URL}/open/envs",
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
        verify=False,
    )
    response.raise_for_status()
    data = safe_json(response)
    if data.get("code") != 200:
        raise ClaimError(f"获取青龙环境变量失败：{data.get('message') or data}")
    envs = data.get("data") or []
    return envs if isinstance(envs, list) else []


def ql_update_env(token: str, env_id: Any, env_value: str, remarks: str) -> bool:
    payload = {"id": env_id, "name": QL_ENV_NAME, "value": env_value, "remarks": remarks}
    response = requests.put(
        f"{QL_URL}/open/envs",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=10,
        verify=False,
    )
    response.raise_for_status()
    data = safe_json(response)
    return data.get("code") == 200


def ql_create_env(token: str, env_value: str, remarks: str) -> bool:
    payload = [{"name": QL_ENV_NAME, "value": env_value, "remarks": remarks}]
    response = requests.post(
        f"{QL_URL}/open/envs",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=10,
        verify=False,
    )
    response.raise_for_status()
    data = safe_json(response)
    return data.get("code") == 200


def update_ql_envs(account_results: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "enabled": ql_config_ready(),
        "ok": False,
        "updated": 0,
        "created": 0,
        "skipped": 0,
        "failed": 0,
        "message": "",
    }

    success_items = [
        item
        for item in account_results
        if item.get("ok") and item.get("pt_pin") and item.get("pt_key")
    ]
    if not success_items:
        summary["message"] = "没有成功获取的 Cookie，跳过青龙更新"
        return summary

    if not ql_config_ready():
        summary["message"] = "未配置 QL_URL / QL_CLIENT_ID / QL_CLIENT_SECRET，跳过青龙更新"
        for item in success_items:
            item["ql_action"] = "未更新"
            item["ql_message"] = "未配置青龙"
        return summary

    try:
        token = ql_login()
        envs = ql_get_envs(token)
        existing = {}
        for env in envs:
            if env.get("name") != QL_ENV_NAME:
                continue
            value = str(env.get("value") or "")
            pt_pin = extract_pt_pin(value)
            if not pt_pin:
                continue
            existing[pt_pin] = {
                "id": env.get("id") or env.get("_id"),
                "value": value,
                "remarks": str(env.get("remarks") or ""),
            }

        for item in success_items:
            pt_pin = str(item.get("pt_pin") or "")
            pt_key = str(item.get("pt_key") or "")
            cookie = f"pt_key={pt_key};pt_pin={pt_pin};"
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            remarks = f"京东Cookie - 更新于 {timestamp}"

            try:
                if pt_pin in existing:
                    old_env = existing[pt_pin]
                    old_value = str(old_env.get("value") or "")
                    old_remarks = str(old_env.get("remarks") or "") or remarks
                    env_id = old_env.get("id")
                    if old_value == cookie:
                        item["ql_action"] = "跳过"
                        item["ql_message"] = "Cookie 无变化"
                        summary["skipped"] += 1
                        continue
                    if not ql_update_env(token, env_id, cookie, old_remarks):
                        raise ClaimError("青龙更新接口返回失败")
                    item["ql_action"] = "更新"
                    item["ql_message"] = "JD_COOKIE 更新成功"
                    summary["updated"] += 1
                else:
                    if not ql_create_env(token, cookie, remarks):
                        raise ClaimError("青龙创建接口返回失败")
                    item["ql_action"] = "新建"
                    item["ql_message"] = "JD_COOKIE 新建成功"
                    summary["created"] += 1
            except Exception as exc:
                item["ql_action"] = "失败"
                item["ql_message"] = str(exc)
                summary["failed"] += 1

        summary["ok"] = summary["failed"] == 0
        summary["message"] = (
            f"更新 {summary['updated']} 个，新建 {summary['created']} 个，"
            f"跳过 {summary['skipped']} 个，失败 {summary['failed']} 个"
        )
        return summary
    except Exception as exc:
        summary["message"] = f"青龙更新失败：{exc}"
        for item in success_items:
            item["ql_action"] = "失败"
            item["ql_message"] = str(exc)
        summary["failed"] = len(success_items)
        return summary


def get_wx_code(wxid: str) -> str:
    server_url = get_wx_server_url()
    if not server_url:
        raise ClaimError("未配置 wx_server_url 或 WX_SERVER_URL")

    response = requests.get(
        f"{server_url}/mywc",
        params={"wxid": wxid, "appId": WX_APP_ID},
        headers={"auth": wxid},
        timeout=MYWC_TIMEOUT,
        verify=False,
    )
    response.raise_for_status()
    data = safe_json(response)

    candidates = [
        ((data.get("data") or {}).get("data") or {}).get("code"),
        ((data.get("data") or {}).get("data") or {}).get("loginCode"),
        ((data.get("data") or {}).get("data") or {}).get("wxcode"),
        (data.get("data") or {}).get("code"),
        (data.get("data") or {}).get("loginCode"),
        (data.get("data") or {}).get("wxcode"),
        ((data.get("result") or {}).get("data") or {}).get("code"),
        ((data.get("result") or {}).get("data") or {}).get("wxcode"),
        (data.get("result") or {}).get("code"),
        data.get("code"),
        data.get("loginCode"),
        data.get("wxcode"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and len(candidate.strip()) >= 10:
            return candidate.strip()
    raise ClaimError(f"mywc 未返回有效 code：{str(data)[:180]}")


def jd_login(code: str) -> dict[str, Any]:
    headers = {
        "Host": "wxapplogin.m.jd.com",
        "Connection": "keep-alive",
        "X-WECHAT-HOSTSIGN": JD_HOSTSIGN,
        "User-Agent": USER_AGENT_PC,
        "cookie": "guid=; pt_pin=; pt_key=; pt_token=",
        "xweb_xhr": "1",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "*/*",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
        "Referer": f"https://servicewechat.com/{JD_WXAPPID}/888/page-frame.html",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    form_data = {
        "code": code,
        "goToLogin": JD_GO_TO_LOGIN,
        "returnurl": JD_RETURNURL,
        "wxappid": JD_WXAPPID,
        "appid": JD_APPID,
        "client_ver": JD_CLIENT_VER,
        "ts": JD_FORM_TS,
        "sign": JD_FORM_SIGN,
    }
    response = requests.post(
        "https://wxapplogin.m.jd.com/cgi-bin/jxpp/silentauthlogin",
        headers=headers,
        data=form_data,
        timeout=API_TIMEOUT,
        verify=False,
    )
    response.raise_for_status()
    data = safe_json(response)
    err_code = data.get("err_code")
    if err_code != 0:
        raise ClaimError(f"京东登录失败：{data.get('err_msg') or '未知错误'} (code: {err_code})")
    return data


def run_account(wxid: str, index: int) -> dict[str, Any]:
    try:
        code = get_wx_code(wxid)
        result = jd_login(code)
        pt_key = str(result.get("pt_key") or "")
        pt_pin = str(result.get("pt_pin") or "")
        if not pt_key or not pt_pin:
            raise ClaimError(f"登录成功但未返回 pt_key 或 pt_pin：{str(result)[:180]}")

        return {
            "index": index,
            "ok": True,
            "status_text": "获取成功",
            "account": mask_account(wxid),
            "pt_pin": pt_pin,
            "pt_key": pt_key,
            "ql_action": "待处理",
            "ql_message": "等待青龙更新",
            "message": "",
            "detail_lines": [
                f"Code：长度 {len(code)}",
                f"Cookie：pt_key={pt_key[:16]}...;pt_pin={pt_pin};",
            ],
        }
    except Exception as exc:
        return {
            "index": index,
            "ok": False,
            "status_text": "获取失败",
            "account": mask_account(wxid),
            "pt_pin": "",
            "pt_key": "",
            "ql_action": "未更新",
            "ql_message": "未获取到 Cookie",
            "message": str(exc),
            "detail_lines": [],
        }


def build_notify_report(ql_summary: dict[str, Any]) -> str:
    total = len(GLOBAL_NOTIFY_BUFFERS)
    success_items = [item for item in GLOBAL_NOTIFY_BUFFERS if item.get("ok")]
    failed_items = [item for item in GLOBAL_NOTIFY_BUFFERS if not item.get("ok")]
    success_accounts = "、".join(item.get("account") for item in success_items) or "-"
    failed_accounts = "、".join(item.get("account") for item in failed_items) or "-"

    lines = [
        "==============================",
        f"🕒 执行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"📊 统计数据：成功 {len(success_items)} / 总计 {total}",
        f"✅ 成功账号：{len(success_items)} 个",
        f"❌ 失败账号：{len(failed_items)} 个",
        f"🍪 成功获取 Cookie：{len(success_items)} 个",
        f"📮 成功账号：{success_accounts}",
        f"🚫 失败账号：{failed_accounts}",
        "==============================",
        (
            f"🧩 青龙结果：更新 {ql_summary.get('updated', 0)} / "
            f"新建 {ql_summary.get('created', 0)} / "
            f"跳过 {ql_summary.get('skipped', 0)} / "
            f"失败 {ql_summary.get('failed', 0)}"
        ),
        f"📝 青龙说明：{ql_summary.get('message') or '-'}",
        "==============================",
    ]

    for item in GLOBAL_NOTIFY_BUFFERS:
        ok = bool(item.get("ok"))
        account_icon = "🧑‍💻" if ok else "🧟"
        status_icon = "✅" if ok else "❌"
        lines.extend(
            [
                f"{account_icon} 【账号{item.get('index')}】{item.get('account')}",
                f"{status_icon} 状态：{item.get('status_text')}",
            ]
        )
        if ok:
            lines.extend(
                [
                    f"🍪 Cookie：pt_pin={item.get('pt_pin')}",
                    f"📦 青龙：{item.get('ql_action')}，{item.get('ql_message')}",
                ]
            )
        else:
            lines.append(f"🧨 原因：{item.get('message') or '未知错误'}")
        for detail in item.get("detail_lines") or []:
            lines.append(f"• {detail}")
        lines.append("------------------------------")

    return "\n".join(lines)


def dispatch_notify(ql_summary: dict[str, Any]) -> None:
    if not GLOBAL_NOTIFY_BUFFERS:
        print("通知缓冲区为空，跳过推送。")
        return

    content = build_notify_report(ql_summary)
    print(content)
    try:
        from SendNotify import send_push_notification
    except Exception as exc:
        print(f"加载 SendNotify.py 失败：{exc}")
        return

    try:
        send_push_notification(SCRIPT_TITLE, content)
    except Exception as exc:
        print(f"通知发送失败：{exc}")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    accounts = parse_accounts()
    if not accounts:
        append_notify_result(
            {
                "index": 1,
                "ok": False,
                "status_text": "配置错误",
                "account": "-",
                "pt_pin": "",
                "pt_key": "",
                "ql_action": "未更新",
                "ql_message": "未配置账号",
                "message": "未读取到 jd_wxid / JD_WXID，兼容旧变量 WXJD",
                "detail_lines": [],
            }
        )
        dispatch_notify({"updated": 0, "created": 0, "skipped": 0, "failed": 0, "message": "未执行"})
        return 1

    if not get_wx_server_url():
        append_notify_result(
            {
                "index": 1,
                "ok": False,
                "status_text": "配置错误",
                "account": "-",
                "pt_pin": "",
                "pt_key": "",
                "ql_action": "未更新",
                "ql_message": "未配置 mywc 网关",
                "message": "未配置 wx_server_url 或 WX_SERVER_URL",
                "detail_lines": [],
            }
        )
        dispatch_notify({"updated": 0, "created": 0, "skipped": 0, "failed": 0, "message": "未执行"})
        return 1

    for index, wxid in enumerate(accounts, start=1):
        print(f"\n===== 开始处理账号 {index}：{mask_account(wxid)} =====")
        result = run_account(wxid, index)
        append_notify_result(result)
        if result.get("ok"):
            print(f"账号{index}获取成功：pt_pin={result.get('pt_pin')}")
        else:
            print(f"账号{index}获取失败：{result.get('message')}")
        if index < len(accounts):
            time.sleep(1.5)

    ql_summary = update_ql_envs(GLOBAL_NOTIFY_BUFFERS)
    dispatch_notify(ql_summary)

    success_count = sum(1 for item in GLOBAL_NOTIFY_BUFFERS if item.get("ok"))
    return 0 if success_count == len(GLOBAL_NOTIFY_BUFFERS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
