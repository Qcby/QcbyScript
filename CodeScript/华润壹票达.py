#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
华润壹票达 v1.1.0（mywc网关聚合推送版）

功能：通过自建 mywc 网关获取壹票达小程序 code，完成登录、签到和积分查询，支持多账号执行，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL                 必填其一，自建授权服务器域名
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=wx70c418a86bc52a9f
   - 请求头：auth=账号标识
2. 账号变量：
   ypd_wxid 或 YPD_WXID                          推荐，兼容 WX_ID、ypdwxid
   - 格式：wxid#备注 或 备注#wxid
   - 多账号支持使用 &、英文逗号、中文逗号、@ 或换行分隔
3. token 回退变量：
   ypd_token 或 YPD_TOKEN                         可选，已有 accessToken 时使用
   YPD_ANGRY_DOG / ypd_angry_dog                 可选
   YPD_COOKIE / ypd_cookie                       可选
4. 推送变量：
   需要同目录存在 SendNotify.py，脚本结束后统一调用 send_push_notification。
5. 青龙任务建议：
   名称：华润壹票达
   命令：python3 华润壹票达.py
   定时：18 9,17 * * *
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import string
import sys
import time
from calendar import monthrange
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode

import requests

try:
    from SendNotify import send_push_notification
except Exception:
    send_push_notification = None


# =========================
# 可手动修改的固定配置
# =========================
DEFAULT_MERCHANT_ID = "6942616f50ef5900011a1d2e"
DEFAULT_APPID = "wx70c418a86bc52a9f"
DEFAULT_VER = "4.63.0"
DEFAULT_SRC = "weixin_mini"
DEFAULT_TERMINAL_SRC = "WEIXIN_MINI"
DEFAULT_UTC_OFFSET = "480"
API_HOST = "https://crld.caiyicloud.com"
SCRIPT_TITLE = "华润壹票达"
GLOBAL_NOTIFY_BUFFERS: List["AccountSummary"] = []
WX_SERVER_URL = (os.environ.get("wx_server_url") or os.environ.get("WX_SERVER_URL") or "").strip().rstrip("/")

PATH_UNION_LOGIN = "/cyy_gatewayapi/mcommon/pub/v1/union_login"
PATH_UNION_AUTH = "/cyy_gatewayapi/mcommon/pub/v1/union_login/authorization"
PATH_CHECK_IN = "/cyy_gatewayapi/user/buyer/v1/check_in"
PATH_CHECK_IN_CALENDAR = "/cyy_gatewayapi/user/buyer/v1/check_in_calendar"
PATH_POINT_TASK = "/cyy_gatewayapi/user/buyer/v3/current_user_point_task"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 "
    "MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI "
    "MiniProgramEnv/Windows WindowsWechat/WMPF "
    "WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf254151e) XWEB/17127"
)

TZ_CN = timezone(timedelta(hours=8))


@dataclass
class AccountConfig:
    index: int
    name: str
    access_token: str = ""
    angry_dog: str = ""
    cookie: str = ""
    wxid: str = ""
    mode: str = "wxid"  # wxid | token


@dataclass
class AccountSummary:
    index: int
    name: str
    mode: str = "wxid"
    success: bool = False
    sign_status: str = "未执行"
    points_before: Optional[Any] = None
    points_after: Optional[Any] = None
    reward: str = ""
    streak: Optional[Any] = None
    error_message: str = ""
    detail_lines: List[str] = field(default_factory=list)

    def log(self, message: str = "") -> None:
        self.detail_lines.append(message)
        print(message)

    def build_notify_lines(self) -> List[str]:
        mark = "🧑‍💻" if self.success else "🧟"
        status_mark = "✅" if self.success else "❌"
        lines = [f"{mark}【账号{self.index}】{self.name}"]
        lines.append(f"{status_mark} 状态：{self.sign_status}")
        lines.append(f"🔐 模式：{self.mode}")
        if self.reward:
            lines.append(f"🎁 奖励：{self.reward}")
        if self.streak is not None:
            lines.append(f"📅 连续：{self.streak} 天")
        if self.points_before is not None or self.points_after is not None:
            lines.append(f"💰 积分：始 {self.points_before} ➔ 终 {self.points_after}")
        if self.error_message:
            lines.append(f"🧨 原因：{self.error_message}")
        return lines


def now_text() -> str:
    return datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M:%S")


def sleep(sec: float) -> None:
    time.sleep(sec)


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def clean_header_value(value: str) -> str:
    if value is None:
        return ""
    return str(value).replace("\\r", "").replace("\\n", "").replace("\r", "").replace("\n", "").strip()


def random_trace_id() -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "mroo" + "".join(random.choice(alphabet) for _ in range(14))


def month_range_ms(now: Optional[datetime] = None) -> Tuple[int, int]:
    """当月起止毫秒时间戳（东八区），对齐 check_in_calendar 的 beginDate/endDate。"""
    now = now or datetime.now(TZ_CN)
    start = datetime(now.year, now.month, 1, tzinfo=TZ_CN)
    if now.month == 12:
        end = datetime(now.year + 1, 1, 1, tzinfo=TZ_CN)
    else:
        end = datetime(now.year, now.month + 1, 1, tzinfo=TZ_CN)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def push_notify(title: str, content: str) -> None:
    if send_push_notification is None:
        print("通知: 未加载 SendNotify.py，跳过推送")
        return
    try:
        send_push_notification(title, content)
        print("通知: 推送完成")
    except Exception as exc:
        print(f"通知: 推送失败: {exc}")


def append_notify_result(result: AccountSummary) -> None:
    GLOBAL_NOTIFY_BUFFERS.append(result)


def build_notify_report() -> str:
    total = len(GLOBAL_NOTIFY_BUFFERS)
    success_items = [item for item in GLOBAL_NOTIFY_BUFFERS if item.success]
    failed_items = [item for item in GLOBAL_NOTIFY_BUFFERS if not item.success]
    success_accounts = "、".join(item.name for item in success_items) or "-"
    failed_accounts = "、".join(item.name for item in failed_items) or "-"
    total_delta = 0
    for item in success_items:
        try:
            if item.points_before is not None and item.points_after is not None:
                total_delta += int(item.points_after) - int(item.points_before)
        except Exception:
            pass

    lines = [
        "==============================",
        f"🕒 执行时间：{now_text()}",
        f"📊 统计数据：成功 {len(success_items)} / 总计 {total}",
        f"✅ 成功账号：{len(success_items)} 个",
        f"❌ 失败账号：{len(failed_items)} 个",
        f"💰 累计积分：+{total_delta}",
        f"🙋 成功列表：{success_accounts}",
        f"💥 失败列表：{failed_accounts}",
        "==============================",
    ]
    for item in GLOBAL_NOTIFY_BUFFERS:
        lines.extend(item.build_notify_lines())
        lines.append("------------------------------")
    return "\n".join(lines)


def dispatch_notify() -> None:
    if not GLOBAL_NOTIFY_BUFFERS:
        print("通知缓冲区为空，跳过推送")
        return
    content = build_notify_report()
    print(content)
    push_notify(SCRIPT_TITLE, content)


def split_accounts_raw(raw_value: str) -> List[str]:
    if not raw_value:
        return []
    parts = re.split(r"[&,\n，@]+", raw_value)
    return [p.strip() for p in parts if p.strip() and not p.strip().startswith("#")]


def parse_token_accounts(raw_value: str) -> List[AccountConfig]:
    """
    accessToken
    accessToken#备注
    accessToken#angryDog#备注
    accessToken#angryDog#cookie#备注
    """
    global_angry = clean_header_value(env("YPD_ANGRY_DOG") or env("ypd_angry_dog"))
    global_cookie = clean_header_value(env("YPD_COOKIE") or env("ypd_cookie"))
    accounts: List[AccountConfig] = []
    for idx, item in enumerate(split_accounts_raw(raw_value), 1):
        parts = item.split("#")
        token = clean_header_value(parts[0] if parts else "")
        angry = global_angry
        cookie = global_cookie
        name = f"账号{idx}"
        if len(parts) == 2:
            second = parts[1].strip()
            if len(second) >= 40 or second.startswith("s_"):
                angry = clean_header_value(second) or angry
            else:
                name = second or name
        elif len(parts) == 3:
            angry = clean_header_value(parts[1]) or angry
            name = parts[2].strip() or name
        elif len(parts) >= 4:
            angry = clean_header_value(parts[1]) or angry
            cookie = clean_header_value(parts[2]) or cookie
            name = "#".join(parts[3:]).strip() or name
        if not token:
            print(f"跳过空 token 配置: {item[:40]}")
            continue
        accounts.append(
            AccountConfig(
                index=idx,
                name=name,
                access_token=token,
                angry_dog=angry,
                cookie=cookie,
                mode="token",
            )
        )
    return accounts


def parse_wxid_item(item: str, idx: int) -> Tuple[str, str]:
    """兼容 wxid#备注 与 备注#wxid。"""
    item = item.strip()
    if "#" not in item:
        return item, f"账号{idx}"
    first, second = [x.strip() for x in item.split("#", 1)]
    if second.startswith("wxid_") and not first.startswith("wxid_"):
        return second, first or f"账号{idx}"
    return first, second or f"账号{idx}"


def parse_wxid_accounts(raw_value: str) -> List[AccountConfig]:
    global_angry = clean_header_value(env("YPD_ANGRY_DOG") or env("ypd_angry_dog"))
    global_cookie = clean_header_value(env("YPD_COOKIE") or env("ypd_cookie"))
    accounts: List[AccountConfig] = []
    for idx, item in enumerate(split_accounts_raw(raw_value), 1):
        wxid, remark = parse_wxid_item(item, idx)
        if not wxid:
            continue
        accounts.append(
            AccountConfig(
                index=idx,
                name=remark or f"账号{idx}",
                wxid=wxid,
                angry_dog=global_angry,
                cookie=global_cookie,
                mode="wxid",
            )
        )
    return accounts



def extract_wx_code(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("code", "wx_code", "js_code"):
        val = data.get(key)
        if val:
            return str(val)
    for nest_key in ("Data", "data", "result"):
        nested = data.get(nest_key)
        if isinstance(nested, dict):
            for key in ("code", "wx_code", "js_code"):
                val = nested.get(key)
                if val:
                    return str(val)
        elif isinstance(nested, str) and nested and not nested.startswith("{"):
            return nested
    return ""


def extract_phone_code(data: Any) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("code", "phoneCode", "phone_code", "authCode", "auth_code", "cloud_id"):
        value = data.get(key)
        if value:
            return str(value).strip()
    for key in ("data", "Data", "result"):
        nested = data.get(key)
        if isinstance(nested, dict):
            found = extract_phone_code(nested)
            if found:
                return found
        elif isinstance(nested, str):
            try:
                parsed = json.loads(nested)
            except Exception:
                parsed = None
            if isinstance(parsed, dict):
                found = extract_phone_code(parsed)
                if found:
                    return found
    return ""


def wechat_success(data: Dict[str, Any]) -> bool:
    if data.get("Success") is False or data.get("success") is False:
        return False
    return True


class YiPiaoDaClient:
    def __init__(self, account: AccountConfig):
        self.account = account
        self.merchant_id = clean_header_value(
            env("YPD_MERCHANT_ID") or env("ypd_merchant_id") or DEFAULT_MERCHANT_ID
        )
        self.app_id = clean_header_value(env("YPD_APPID") or env("ypd_appid") or DEFAULT_APPID)
        self.ver = clean_header_value(env("YPD_VER") or env("ypd_ver") or DEFAULT_VER)
        self.src = DEFAULT_SRC
        self.terminal_src = DEFAULT_TERMINAL_SRC
        self.utc_offset = DEFAULT_UTC_OFFSET
        self.access_token = clean_header_value(account.access_token)
        self.angry_dog = clean_header_value(account.angry_dog)
        self.cookie = clean_header_value(account.cookie)
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.headers.update(
            {
                "User-Agent": UA,
                "Accept": "*/*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "xweb_xhr": "1",
                "content-type": "application/json",
                "Referer": f"https://servicewechat.com/{self.app_id}/22/page-frame.html",
                "sec-fetch-site": "cross-site",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
            }
        )
        self.timeout = float(env("YPD_TIMEOUT") or "30")
        self.wechat_code_url = f"{WX_SERVER_URL}/mywc" if WX_SERVER_URL else ""
        self.wechat_appid = clean_header_value(
            env("WECHAT_MINI_APPID") or env("wechat_mini_appid") or self.app_id
        )

    def _common_query(self, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        q: Dict[str, Any] = {
            "currency": "CNY",
            "lang": "zh",
            "terminalSrc": self.terminal_src,
            "utcOffset": self.utc_offset,
            "ver": self.ver,
        }
        if extra:
            q.update(extra)
        return q

    def _auth_headers(self, with_token: bool = True) -> Dict[str, str]:
        headers = {
            "src": self.src,
            "terminal-src": self.terminal_src,
            "merchant-id": self.merchant_id,
            "ver": self.ver,
            "utc-offset": self.utc_offset,
            "front-trace-id": random_trace_id(),
        }
        if with_token and self.access_token:
            headers["access-token"] = self.access_token
        if self.angry_dog:
            headers["angry-dog"] = self.angry_dog
        if self.cookie:
            headers["cookie"] = self.cookie
        return headers

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Optional[Dict[str, Any]] = None,
        body: Any = None,
        with_token: bool = True,
    ) -> Dict[str, Any]:
        url = API_HOST + path
        if query:
            url = f"{url}?{urlencode(query)}"
        headers = self._auth_headers(with_token=with_token)
        resp = self.session.request(
            method.upper(),
            url,
            headers=headers,
            json=body if body is not None else None,
            timeout=self.timeout,
            proxies={"http": None, "https": None},
        )
        text = resp.text or ""
        try:
            data = resp.json()
        except Exception:
            data = {"_raw": text[:500], "_http_status": resp.status_code}
        if not isinstance(data, dict):
            data = {"_raw": data, "_http_status": resp.status_code}
        data.setdefault("_http_status", resp.status_code)
        return data

    def ok(self, data: Dict[str, Any]) -> bool:
        return int(data.get("statusCode") or 0) == 200 and int(data.get("_http_status") or 0) in (0, 200)

    def comments(self, data: Dict[str, Any]) -> str:
        return str(data.get("comments") or data.get("errorCode") or data.get("_raw") or "")

    # ---------- 微信协议服务 ----------
    def fetch_wx_code(self, summary: AccountSummary) -> str:
        if not WX_SERVER_URL:
            raise RuntimeError("未配置 wx_server_url 或 WX_SERVER_URL，无法请求 /mywc")
        summary.log("获取 code: 通过 /mywc 网关获取")
        try:
            resp = requests.get(
                f"{WX_SERVER_URL}/mywc",
                params={"wxid": self.account.wxid, "appId": self.wechat_appid},
                headers={"auth": self.account.wxid},
                timeout=self.timeout,
                proxies={"http": None, "https": None},
            )
            result = resp.json()
        except Exception as exc:
            raise RuntimeError(f"mywc 取 code 失败: {exc}") from exc
        code = extract_wx_code(result)
        if not code:
            raise RuntimeError(f"mywc 未返回有效 code: {json.dumps(result, ensure_ascii=False)[:300]}")
        summary.log(f"code 获取成功: {str(code)[:12]}... (len={len(str(code))})")
        return str(code)

    def fetch_phone_code_from_gateway(self, summary: AccountSummary) -> Dict[str, Any]:
        if not WX_SERVER_URL:
            raise RuntimeError("未配置 wx_server_url 或 WX_SERVER_URL，无法请求 /mysjh")
        summary.log("手机号协议: 通过 /mysjh 获取授权 code")
        try:
            resp = requests.get(
                f"{WX_SERVER_URL}/mysjh",
                params={"wxid": self.account.wxid, "appId": self.wechat_appid},
                timeout=self.timeout,
                proxies={"http": None, "https": None},
            )
            result = resp.json()
        except Exception as exc:
            raise RuntimeError(f"mysjh 获取手机号失败: {exc}") from exc
        code = extract_phone_code(result)
        if not code:
            raise RuntimeError(f"mysjh 未返回手机号授权 code: {json.dumps(result, ensure_ascii=False)[:300]}")
        summary.log(f"✅ phone code ok (len={len(str(code))})")
        return {"code": str(code)}

    def fetch_phone_encrypted(self, summary: AccountSummary) -> Dict[str, Any]:
        """通过 /mysjh 获取手机号授权 code。"""
        return self.fetch_phone_code_from_gateway(summary)

    def _apply_login_payload(self, payload: Dict[str, Any], summary: AccountSummary, stage: str) -> bool:
        token = clean_header_value(str(payload.get("accessToken") or ""))
        if token:
            self.access_token = token
            summary.log(f"{stage} 拿到 accessToken (len={len(token)})")
            return True
        return False

    def union_login(self, wx_code: str, summary: AccountSummary) -> Dict[str, Any]:
        volc_web_id = str(random.randint(10**18, 10**19 - 1))
        body = {
            "src": self.src,
            "merchantId": self.merchant_id,
            "ver": self.ver,
            "appId": self.app_id,
            "unionType": "WEIXIN_MINI",
            "wxParam": {"code": wx_code},
            "deviceInfo": {"volcWebId": volc_web_id},
        }
        summary.log("POST union_login ...")
        data = self.request(
            "POST",
            PATH_UNION_LOGIN,
            query=self._common_query(),
            body=body,
            with_token=False,
        )
        if not self.ok(data):
            raise RuntimeError(
                f"union_login 失败: {self.comments(data)} | {json.dumps(data, ensure_ascii=False)[:300]}"
            )
        payload = data.get("data") or {}
        if not isinstance(payload, dict):
            payload = {}
        if self._apply_login_payload(payload, summary, "union_login"):
            return payload

        auth_token = str(payload.get("authToken") or "")
        open_id = str(payload.get("openId") or "")
        summary.log(
            f"union_login 未返回 accessToken，尝试手机号 authorization。"
            f" authToken={bool(auth_token)} openId={open_id[:10] + '...' if open_id else ''}"
        )
        if not auth_token or not open_id:
            raise RuntimeError(
                "union_login 未返回 accessToken/authToken/openId，无法继续自动登录。"
                " 可改用 ypd_token 模式。"
            )
        return self.union_authorize(auth_token, open_id, volc_web_id, summary)

    def union_authorize(
        self,
        auth_token: str,
        open_id: str,
        volc_web_id: str,
        summary: AccountSummary,
    ) -> Dict[str, Any]:
        phone = self.fetch_phone_encrypted(summary)
        encrypt_phone = (
            phone.get("encryptedData")
            or phone.get("encrypted_data")
            or phone.get("encryptPhoneNumber")
            or ""
        )
        init_vector = phone.get("iv") or phone.get("initVector") or phone.get("init_vector") or ""
        # ALLMobile[].code 实测 64 位 hex，与抓包 authCode 形态一致
        auth_code = (
            phone.get("authCode")
            or phone.get("auth_code")
            or phone.get("code")
            or phone.get("phone_code")
            or phone.get("cloud_id")
            or ""
        )
        if auth_code and len(str(auth_code)) not in (32, 64) and encrypt_phone:
            # 非预期长度时仍提交原值，同时记录
            summary.log(f"authCode 长度异常 len={len(str(auth_code))}")
        if not auth_code and encrypt_phone:
            summary.log("无 authCode/code 字段，sha256(encryptedData) 兜底")
            auth_code = hashlib.sha256(str(encrypt_phone).encode("utf-8")).hexdigest()
        if not encrypt_phone and not init_vector:
            # 应用宝(YYB) 仅返回手机号授权 code，无加密包，走 code 授权路径
            if auth_code:
                summary.log("无加密包(encryptedData/iv)，使用手机号授权 code 完成 authorization")
                encrypt_phone = ""
                init_vector = ""
            else:
                raise RuntimeError(
                    "手机号 encryptedData/iv 与 authCode 均为空，无法 authorization。"
                    " 请确认协议服务支持 get/all/mobile，或改用 ypd_token。"
                )
        mobile_hint = str(phone.get("show_mobile") or phone.get("mobile") or "")
        summary.log(f"手机号包 ok mobile={mobile_hint or 'N/A'} enc_len={len(str(encrypt_phone))}")

        body = {
            "src": self.src,
            "merchantId": self.merchant_id,
            "ver": self.ver,
            "appId": self.app_id,
            "unionType": "WEIXIN_MINI",
            "authToken": auth_token,
            "openId": open_id,
            "wxParam": {
                "openId": open_id,
                "encryptPhoneNumber": encrypt_phone,
                "initVector": init_vector,
                "authCode": str(auth_code),
            },
            "invitePageId": "",
            "deviceInfo": {"volcWebId": volc_web_id},
        }
        summary.log("POST union_login/authorization ...")
        data = self.request(
            "POST",
            PATH_UNION_AUTH,
            query=self._common_query(),
            body=body,
            with_token=False,
        )
        if not self.ok(data):
            raise RuntimeError(
                f"authorization 失败: {self.comments(data)} | {json.dumps(data, ensure_ascii=False)[:300]}"
            )
        payload = data.get("data") or {}
        if not isinstance(payload, dict):
            payload = {}
        if not self._apply_login_payload(payload, summary, "authorization"):
            raise RuntimeError(
                "authorization 未返回 accessToken。"
                f" keys={list(payload.keys())} | {json.dumps(payload, ensure_ascii=False)[:200]}"
            )
        return payload

    def login_by_wxid(self, summary: AccountSummary) -> None:
        if not self.account.wxid:
            raise RuntimeError("缺少 wxid")
        if not self.wechat_code_url:
            raise RuntimeError("缺少 wx_server_url 或 WX_SERVER_URL")
        summary.log(f"wxid 登录: {self.account.wxid}")
        summary.log(f"mywc => {self.wechat_code_url}")
        summary.log(f"appId => {self.wechat_appid}")
        code = self.fetch_wx_code(summary)
        self.union_login(code, summary)
        if not self.access_token:
            raise RuntimeError("wxid 登录后仍无 access-token")

    # ---------- 签到 ----------
    def get_calendar(self) -> Dict[str, Any]:
        begin_ms, end_ms = month_range_ms()
        query = self._common_query(
            {
                "src": self.src,
                "merchantId": self.merchant_id,
                "appId": self.app_id,
                "pageSource": "TASK_CENTER",
                "beginDate": str(begin_ms),
                "endDate": str(end_ms),
            }
        )
        return self.request("GET", PATH_CHECK_IN_CALENDAR, query=query, with_token=True)

    def do_check_in(self) -> Dict[str, Any]:
        body = {
            "src": self.src,
            "merchantId": self.merchant_id,
            "ver": self.ver,
            "appId": self.app_id,
        }
        query = self._common_query()
        return self.request("POST", PATH_CHECK_IN, query=query, body=body, with_token=True)

    def run(self) -> AccountSummary:
        summary = AccountSummary(
            index=self.account.index,
            name=self.account.name,
            mode=self.account.mode,
        )
        summary.log(f"\n====== 【账号{self.account.index}】{self.account.name} ({self.account.mode}) ======")
        try:
            if self.account.mode == "wxid":
                self.login_by_wxid(summary)
            if not self.access_token:
                raise RuntimeError("缺少 access-token，请配置 ypd_wxid(+wx_server_url) 或 ypd_token")

            summary.log("查询签到日历 ...")
            cal = self.get_calendar()
            if not self.ok(cal):
                raise RuntimeError(
                    f"查询日历失败: {self.comments(cal)} | {json.dumps(cal, ensure_ascii=False)[:300]}"
                )
            cal_data = cal.get("data") or {}
            today_checked = bool(cal_data.get("todayCheckedIn"))
            summary.points_before = cal_data.get("points")
            summary.streak = cal_data.get("streakCheckInDays")
            summary.log(
                f"日历: todayCheckedIn={today_checked} points={summary.points_before} "
                f"streak={summary.streak}"
            )

            if today_checked:
                summary.success = True
                summary.sign_status = "今日已签到"
                summary.points_after = summary.points_before
                summary.log("今日已签到，跳过 POST check_in")
            else:
                summary.log("执行签到 POST check_in ...")
                sign = self.do_check_in()
                if not self.ok(sign):
                    raise RuntimeError(
                        f"签到失败: {self.comments(sign)} | {json.dumps(sign, ensure_ascii=False)[:300]}"
                    )
                sign_data = sign.get("data") or {}
                rewards = sign_data.get("rewardAggPackage") or sign_data.get("rewardPackage") or []
                reward_text = []
                for r in rewards:
                    if isinstance(r, dict):
                        reward_text.append(f"{r.get('rewardType', '?')}={r.get('reward', '?')}")
                    else:
                        reward_text.append(str(r))
                summary.reward = ", ".join(reward_text) if reward_text else "成功"
                summary.streak = sign_data.get("streakCheckInDays", summary.streak)
                summary.sign_status = "签到成功"
                summary.success = True
                summary.log(f"签到成功 reward={summary.reward} streak={summary.streak}")
                sleep(0.5)
                cal2 = self.get_calendar()
                if self.ok(cal2):
                    d2 = cal2.get("data") or {}
                    summary.points_after = d2.get("points", summary.points_before)
                    summary.streak = d2.get("streakCheckInDays", summary.streak)
                    if d2.get("todayCheckedIn"):
                        summary.log(f"回读确认已签到 points={summary.points_after}")
                    else:
                        summary.log("回读日历 todayCheckedIn 仍为 false（可能延迟）")
                else:
                    summary.points_after = summary.points_before
                    summary.log(f"回读日历失败: {self.comments(cal2)}")

        except Exception as exc:
            summary.success = False
            summary.sign_status = "失败"
            summary.error_message = str(exc)
            summary.log(f"失败: {exc}")
        return summary


def load_accounts() -> List[AccountConfig]:
    """优先 ypd_wxid；无 wxid 时回退 ypd_token。"""
    wxid_raw = env("ypd_wxid") or env("YPD_WXID") or env("WX_ID") or env("ypdwxid")
    token_raw = env("ypd_token") or env("YPD_TOKEN") or env("YPDTOKEN")
    accounts: List[AccountConfig] = []
    if wxid_raw:
        accounts.extend(parse_wxid_accounts(wxid_raw))
    if token_raw:
        token_accounts = parse_token_accounts(token_raw)
        base = len(accounts)
        for i, acc in enumerate(token_accounts, 1):
            acc.index = base + i
            accounts.append(acc)
    return accounts


def main() -> None:
    print(f"壹票达签到开始 {now_text()}")
    print(f"API_HOST => {API_HOST}")
    print(f"WX_SERVER_URL => {WX_SERVER_URL or '未配置'}")
    accounts = load_accounts()
    if not accounts:
        summary = AccountSummary(
            index=1,
            name="-",
            mode="config",
            success=False,
            sign_status="配置错误",
            error_message=(
                "未配置账号。请设置 ypd_wxid 或 YPD_WXID；"
                "token 回退可设置 ypd_token 或 YPD_TOKEN。"
            ),
        )
        print(summary.error_message)
        append_notify_result(summary)
        dispatch_notify()
        return

    modes = ", ".join(sorted({a.mode for a in accounts}))
    print(f"账号数: {len(accounts)} 模式: {modes}")
    results: List[AccountSummary] = []
    for acc in accounts:
        client = YiPiaoDaClient(acc)
        result = client.run()
        results.append(result)
        append_notify_result(result)
        sleep(random.uniform(0.8, 1.6))

    ok_n = sum(1 for r in results if r.success)
    fail_n = len(results) - ok_n
    print(f"\n====== 汇总 成功 {ok_n}/{len(results)} 失败 {fail_n} ======")
    dispatch_notify()


if __name__ == "__main__":
    main()
