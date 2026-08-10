#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
九号出行APP签到 v1.1.0（青龙多账号聚合推送版）

盲盒正确链路（HAR + 前端源码 2026-07-15）：
  GET  /portal/api/user-sign/v2/status
  POST /portal/api/user-sign/v2/sign              body: { deviceId }
  GET  /portal/api/user-sign/v2/blind-box/list
  POST /portal/api/user-sign/v2/blind-box/receive body: { rewardId }

list.notOpenedBoxes:
  rewardStatus === 1 && blindBoxIds → 可开
  rewardStatus === 2 + leftDaysToOpen → 未到天数

勿用 calendar.rewardInfo.receiveStatus===1 判定可领。

青龙环境变量：
  推荐多账号：
    NINEBOT_ACCOUNTS='[{"name":"账号1","deviceId":"xxx","authorization":"Bearer xxx"}]'
  兼容单变量/分隔多账号：
    NINEBOT_DEVICE_ID="device1&device2"
    NINEBOT_AUTHORIZATION="Bearer token1&Bearer token2"
    NINEBOT_NAME="账号1&账号2"

推送：
  使用同目录 SendNotify.py 的 send_push_notification(title, content)

调试变量：
  NINEBOT_DEBUG=1          全程详细（请求 URL + 成功/失败 JSON）
  NINEBOT_DUMP_ON_ERROR=0  关闭「仅失败时打印完整 JSON」（默认开启）
  NINEBOT_TIMEOUT=10
  NINEBOT_RETRY=3

依赖：requests（青龙一般自带）
运行：
  python3 ninebot_checkin.py
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

SCRIPT_TITLE = "九号出行APP签到"
GLOBAL_NOTIFY_BUFFERS: List[Dict[str, Any]] = []

DEBUG = os.getenv("NINEBOT_DEBUG", "").lower() in ("1", "true", "yes", "on")
DUMP_ON_ERROR = os.getenv("NINEBOT_DUMP_ON_ERROR", "1").lower() not in (
    "0",
    "false",
    "off",
    "no",
)

BASE = "https://cn-cbu-gateway.ninebot.com"
ENDPOINTS = {
    "sign": f"{BASE}/portal/api/user-sign/v2/sign",
    "status": f"{BASE}/portal/api/user-sign/v2/status",
    "calendar": f"{BASE}/portal/api/user-sign/v2/calendar",
    "blind_box_list": f"{BASE}/portal/api/user-sign/v2/blind-box/list",
    "blind_box_receive": f"{BASE}/portal/api/user-sign/v2/blind-box/receive",
}

UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 15_1 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "Segway v6 C 609033420"
)


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def safe_json(obj: Any, max_len: int = 8000) -> str:
    try:
        s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=2)
        if len(s) > max_len:
            return s[:max_len] + f"\n... [truncated {len(s) - max_len} chars]"
        return s
    except Exception:
        return str(obj)


class NineBot:
    def __init__(self, device_id: str, authorization: str, name: str = "九号出行"):
        if not device_id or not authorization:
            raise ValueError("缺少必要的参数: deviceId 或 authorization")

        self.msg: List[Dict[str, str]] = []
        self.name = name
        self.device_id = device_id
        self.timeout = env_int("NINEBOT_TIMEOUT", 10)
        self.retry = env_int("NINEBOT_RETRY", 3)
        self.retry_delay = 2

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json, text/plain, */*",
                "Authorization": authorization,
                "Accept-Encoding": "gzip, deflate, br",
                "Accept-Language": "zh-CN,zh-Hans;q=0.9",
                "Content-Type": "application/json",
                "Host": "cn-cbu-gateway.ninebot.com",
                "Origin": "https://h5-bj.ninebot.com",
                "from_platform_1": "1",
                "language": "zh",
                "User-Agent": UA,
                "Referer": "https://h5-bj.ninebot.com/",
                "device_id": device_id,
            }
        )

    def log(self, *args: Any) -> None:
        print(f"[{self.name}]", *args, flush=True)

    def debug(self, *args: Any) -> None:
        if DEBUG:
            print(f"[{self.name}]", *args, flush=True)

    def dump_on_error(self, label: str, payload: Any) -> None:
        if DEBUG or DUMP_ON_ERROR:
            self.log(f"❌ {label}\n{safe_json(payload)}")

    @property
    def logs(self) -> str:
        return "\n".join(f"{m['name']}: {m['value']}" for m in self.msg)

    def make_request(
        self,
        method: str,
        url: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> Any:
        short_url = url.split("?", 1)[0]
        last_err: Optional[Exception] = None

        for attempt in range(1, self.retry + 1):
            try:
                self.debug(f"{method.upper()} {url}")
                if DEBUG and data is not None:
                    self.debug("request body:", safe_json(data, 2000))

                resp = self.session.request(
                    method=method.upper(),
                    url=url,
                    json=data if data is not None else None,
                    timeout=self.timeout,
                )
                # 尽量解析 JSON；非 2xx 也尝试带 body dump
                try:
                    body = resp.json()
                except Exception:
                    body = {"_raw": resp.text[:2000], "_status": resp.status_code}

                if DEBUG:
                    self.debug(f"response {short_url}:\n{safe_json(body)}")

                if resp.status_code >= 400:
                    raise requests.HTTPError(
                        f"HTTP {resp.status_code}",
                        response=resp,
                    )
                return body
            except Exception as e:
                last_err = e
                if attempt >= self.retry:
                    payload: Dict[str, Any] = {
                        "method": method.upper(),
                        "url": short_url,
                        "attempts": attempt,
                        "message": str(e),
                        "requestBody": data,
                    }
                    resp = getattr(e, "response", None)
                    if resp is not None:
                        payload["status"] = resp.status_code
                        try:
                            payload["response"] = resp.json()
                        except Exception:
                            payload["response"] = resp.text[:2000]
                    self.dump_on_error(f"HTTP 失败 {method.upper()} {short_url}", payload)
                    raise
                self.log(f"请求失败，重试 {attempt}/{self.retry}: {e}")
                time.sleep(self.retry_delay)

        if last_err:
            raise last_err
        raise RuntimeError("request failed")

    def sign(self) -> Dict[str, Any]:
        try:
            response = self.make_request(
                "post",
                ENDPOINTS["sign"],
                {"deviceId": self.device_id},
            )
            if response.get("code") == 0:
                self.log("签到成功")
                return {"ok": True, "already": False, "msg": response.get("msg") or "ok"}

            error_msg = response.get("msg") or response.get("key") or "未知错误"
            if response.get("code") == 540004 or re.search(
                r"already signed|已签到|cannot sign in again",
                str(error_msg),
                re.I,
            ):
                self.log("今日已签到（幂等）")
                return {"ok": True, "already": True, "msg": error_msg}

            self.dump_on_error("签到业务失败 response", response)
            self.msg.append({"name": "签到结果", "value": f"签到失败: {error_msg}"})
            self.log("签到失败:", error_msg)
            return {"ok": False, "already": False, "msg": error_msg}
        except Exception as e:
            self.handle_error("签到", e)
            return {"ok": False, "already": False, "msg": str(e)}

    def valid(self) -> Tuple[Any, str]:
        try:
            ts = int(time.time() * 1000)
            response = self.make_request("get", f"{ENDPOINTS['status']}?t={ts}")
            if response.get("code") == 0:
                return response.get("data") or {}, ""
            self.dump_on_error("status 业务失败 response", response)
            error_msg = response.get("msg") or "验证失败"
            self.log("验证失败:", error_msg)
            return False, error_msg
        except Exception as e:
            error_msg = f"登录验证异常: {e}"
            self.log(error_msg)
            return False, error_msg

    def get_blind_box_list(self) -> Optional[Dict[str, Any]]:
        try:
            ts = int(time.time() * 1000)
            response = self.make_request("get", f"{ENDPOINTS['blind_box_list']}?t={ts}")
            if response.get("code") == 0:
                return response.get("data") or {}
            self.dump_on_error("blind-box/list 业务失败 response", response)
            self.log("获取盲盒列表失败:", response.get("msg"))
            self.msg.append(
                {
                    "name": "盲盒列表",
                    "value": f"获取失败: {response.get('msg') or '未知'}",
                }
            )
            return None
        except Exception as e:
            self.handle_error("获取盲盒列表", e)
            return None

    @staticmethod
    def format_reward(reward: Optional[Dict[str, Any]] = None) -> str:
        reward = reward or {}
        rtype = reward.get("rewardType", reward.get("type"))
        try:
            rtype_n = int(rtype) if rtype is not None else None
        except (TypeError, ValueError):
            rtype_n = None
        value = reward.get("rewardValue", reward.get("value", ""))

        if rtype_n == 1:
            return f"经验值 +{value}"
        if rtype_n == 2:
            return f"N币 +{value}"
        if rtype_n == 3:
            return f"勋章 {reward.get('medalName') or value or ''}".strip()
        if rtype_n == 4:
            return f"补签卡 +{value}"
        if rtype_n == 5:
            return "商城抵扣券"
        if value != "" and value is not None:
            return f"类型{rtype_n or '?'} {value}"
        return json.dumps(reward, ensure_ascii=False)

    def receive_blind_box(self, reward_id: str, award_days: Any = None) -> bool:
        try:
            self.debug(
                f"开盲盒 rewardId={reward_id}"
                + (f" ({award_days}天)" if award_days is not None else "")
            )
            response = self.make_request(
                "post",
                ENDPOINTS["blind_box_receive"],
                {"rewardId": reward_id},
            )
            if response.get("code") == 0:
                reward = response.get("data") or {}
                desc = self.format_reward(reward)
                days_part = f"{award_days}天盲盒 · " if award_days is not None else ""
                value = f"🎁 {days_part}{desc}"
                self.log(f"盲盒领取成功: {value}")
                self.msg.append({"name": "盲盒奖励", "value": value})
                return True

            self.dump_on_error(
                "blind-box/receive 失败",
                {"rewardId": reward_id, "awardDays": award_days, "response": response},
            )
            self.log(f"盲盒领取失败: {response.get('msg') or '未知'}")
            self.msg.append(
                {
                    "name": "盲盒结果",
                    "value": f"领取失败: {response.get('msg') or '未知'}",
                }
            )
            return False
        except Exception as e:
            self.handle_error("领取盲盒", e)
            return False

    def process_blind_box(self, _status_hint: Any = None) -> None:
        list_data = self.get_blind_box_list()
        if not list_data:
            self.msg.append({"name": "盲盒状态", "value": "列表不可用，跳过领取"})
            return

        not_opened = list_data.get("notOpenedBoxes") or []
        if not isinstance(not_opened, list):
            not_opened = []

        claimable: List[Dict[str, Any]] = []
        pending: List[str] = []

        for box in not_opened:
            if not isinstance(box, dict):
                continue
            ids = [x for x in (box.get("blindBoxIds") or []) if x]
            try:
                status = int(box.get("rewardStatus"))
            except (TypeError, ValueError):
                status = -1

            if status == 1 and ids:
                for rid in ids:
                    claimable.append({"rewardId": rid, "awardDays": box.get("awardDays")})
            elif status == 2 and box.get("leftDaysToOpen") is not None:
                pending.append(f"{box.get('awardDays')}天(还差{box.get('leftDaysToOpen')}天)")

        seen = set()
        unique = []
        for item in claimable:
            rid = item["rewardId"]
            if rid in seen:
                continue
            seen.add(rid)
            unique.append(item)

        if not unique:
            tip = "暂无待开盲盒"
            if pending:
                tip += f"；未到: {'、'.join(pending[:3])}"
            self.log(tip)
            self.msg.append({"name": "盲盒状态", "value": tip})
            return

        self.log(f"可开盲盒 {len(unique)} 个")
        self.msg.append({"name": "盲盒待开", "value": f"{len(unique)} 个"})

        ok_count = 0
        for item in unique:
            if self.receive_blind_box(item["rewardId"], item.get("awardDays")):
                ok_count += 1
            time.sleep(1)
        self.msg.append({"name": "盲盒领取", "value": f"成功 {ok_count}/{len(unique)}"})

    def handle_error(self, action: str, error: Exception) -> None:
        self.log(f"{action}错误:", error)
        resp = getattr(error, "response", None)
        if resp is not None:
            try:
                self.dump_on_error(f"{action} response.data", resp.json())
            except Exception:
                self.dump_on_error(f"{action} response.text", resp.text[:2000])
        self.msg.append({"name": f"{action}结果", "value": f"{action}失败"})
        self.msg.append({"name": "错误详情", "value": str(error)})

    def run(self) -> None:
        try:
            self.log("开始")
            valid_data, err_info = self.valid()

            if valid_data:
                completed = int(valid_data.get("currentSignStatus") or 0) == 1
                self.msg.append(
                    {
                        "name": "连续签到",
                        "value": f"{valid_data.get('consecutiveDays') or 0}天",
                    }
                )
                self.msg.append(
                    {
                        "name": "今日签到",
                        "value": "已签到" if completed else "未签到",
                    }
                )

                if not completed:
                    sign_result = self.sign()
                    if sign_result.get("ok"):
                        for item in self.msg:
                            if item["name"] == "今日签到":
                                item["value"] = "已签到"
                        self.msg.append(
                            {
                                "name": "签到结果",
                                "value": "今日已签到"
                                if sign_result.get("already")
                                else "签到成功",
                            }
                        )
                        after_status, _ = self.valid()
                        if after_status:
                            valid_data = after_status
                            for item in self.msg:
                                if item["name"] == "连续签到":
                                    item["value"] = (
                                        f"{after_status.get('consecutiveDays') or 0}天"
                                    )
                else:
                    self.log(
                        f"今日已签到 · 连续 {valid_data.get('consecutiveDays') or 0} 天"
                    )

                self.process_blind_box(valid_data)
            else:
                self.msg.append({"name": "验证结果", "value": err_info})
                self.log(err_info)
        except Exception as e:
            self.msg.append({"name": "执行结果", "value": f"执行异常: {e}"})
            self.log("执行异常:", e)


def split_env_values(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in re.split(r"[&,\n，]+", value) if item.strip()]


def get_msg_value(messages: List[Dict[str, str]], names: Tuple[str, ...], default: str = "-") -> str:
    for item in messages:
        if item.get("name") in names and item.get("value"):
            return item["value"]
    return default


def is_account_success(messages: List[Dict[str, str]]) -> bool:
    text = "\n".join(f"{item.get('name', '')}: {item.get('value', '')}" for item in messages)
    fail_words = ("失败", "异常", "错误", "验证失败", "初始化失败")
    if any(word in text for word in fail_words):
        return False
    return any(word in text for word in ("签到成功", "已签到", "盲盒奖励", "暂无待开盲盒", "成功 "))


def build_account_result(index: int, account: Dict[str, str], messages: List[Dict[str, str]]) -> Dict[str, Any]:
    ok = is_account_success(messages)
    reason = get_msg_value(messages, ("错误详情", "执行结果", "验证结果", "签到结果"), "")
    return {
        "index": index,
        "account": account.get("name") or f"账号{index}",
        "ok": ok,
        "status_text": "执行成功" if ok else "执行失败",
        "sign_text": get_msg_value(messages, ("签到结果", "今日签到")),
        "streak": get_msg_value(messages, ("连续签到",)),
        "blind_box": get_msg_value(messages, ("盲盒奖励", "盲盒领取", "盲盒状态", "盲盒待开")),
        "message": reason or "\n".join(f"{m.get('name')}: {m.get('value')}" for m in messages) or "-",
    }


def append_notify_result(result: Dict[str, Any]) -> None:
    GLOBAL_NOTIFY_BUFFERS.append(result)


def build_notify_report() -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(GLOBAL_NOTIFY_BUFFERS)
    success_items = [item for item in GLOBAL_NOTIFY_BUFFERS if item.get("ok")]
    failed_items = [item for item in GLOBAL_NOTIFY_BUFFERS if not item.get("ok")]
    lines = [
        "==============================",
        f"🕒 执行时间：{now}",
        f"📊 统计数据：成功 {len(success_items)} / 总计 {total}",
        f"✅ 成功账号：{len(success_items)} 个",
        f"❌ 失败账号：{len(failed_items)} 个",
        "==============================",
    ]

    for item in GLOBAL_NOTIFY_BUFFERS:
        icon = "🧑‍💻" if item.get("ok") else "🧟"
        status_icon = "✅" if item.get("ok") else "❌"
        lines.extend(
            [
                f"{icon} 【账号{item.get('index', '-')}】{item.get('account', '-')}",
                f"{status_icon} 状态：{item.get('status_text', '-')}",
                f"📅 签到：{item.get('sign_text', '-')}",
                f"🔥 连续：{item.get('streak', '-')}",
                f"🎁 盲盒：{item.get('blind_box', '-')}",
            ]
        )
        if not item.get("ok"):
            lines.append(f"🧨 原因：{item.get('message', '-')}")
        lines.append("------------------------------")
    return "\n".join(lines)


def dispatch_notify() -> None:
    content = build_notify_report()
    print("\n" + content, flush=True)
    try:
        from SendNotify import send_push_notification
    except Exception as e:
        print(f"推送未发送：导入 SendNotify.py 失败: {e}", flush=True)
        return

    try:
        send_push_notification(SCRIPT_TITLE, content)
        print("聚合推送已发送", flush=True)
    except Exception as e:
        print(f"聚合推送失败: {e}", flush=True)


def load_accounts() -> List[Dict[str, str]]:
    raw = os.getenv("NINEBOT_ACCOUNTS")
    if raw:
        try:
            arr = json.loads(raw)
            if not isinstance(arr, list):
                raise ValueError("必须是 JSON 数组")
            out: List[Dict[str, str]] = []
            for i, acc in enumerate(arr):
                if not isinstance(acc, dict):
                    continue
                out.append(
                    {
                        "name": str(acc.get("name") or f"账号{i + 1}").strip(),
                        "deviceId": str(acc.get("deviceId") or acc.get("device_id") or "").strip(),
                        "authorization": str(acc.get("authorization") or "").strip(),
                    }
                )
            return [item for item in out if item["deviceId"] and item["authorization"]]
        except Exception as e:
            print(f"NINEBOT_ACCOUNTS 格式错误: {e}", flush=True)
            return []

    device_ids = split_env_values(os.getenv("NINEBOT_DEVICE_ID"))
    authorizations = split_env_values(os.getenv("NINEBOT_AUTHORIZATION"))
    names = split_env_values(os.getenv("NINEBOT_NAME"))
    if device_ids and authorizations:
        total = min(len(device_ids), len(authorizations))
        return [
            {
                "name": names[i] if i < len(names) else f"账号{i + 1}",
                "deviceId": device_ids[i],
                "authorization": authorizations[i],
            }
            for i in range(total)
        ]
    print("未配置任何账号信息", flush=True)
    return []


def main() -> None:
    accounts = load_accounts()
    if not accounts:
        append_notify_result(
            {
                "index": 1,
                "account": "配置检查",
                "ok": False,
                "status_text": "执行失败",
                "sign_text": "-",
                "streak": "-",
                "blind_box": "-",
                "message": "未配置 NINEBOT_ACCOUNTS 或 NINEBOT_DEVICE_ID/NINEBOT_AUTHORIZATION",
            }
        )
        dispatch_notify()
        return

    for index, account in enumerate(accounts, 1):
        print(f"\n== {account['name']} ==", flush=True)
        try:
            bot = NineBot(
                account["deviceId"],
                account["authorization"],
                account["name"],
            )
            bot.run()
            print(bot.logs, flush=True)
            append_notify_result(build_account_result(index, account, bot.msg))
        except Exception as e:
            print(f"初始化失败: {e}", flush=True)
            append_notify_result(
                {
                    "index": index,
                    "account": account.get("name") or f"账号{index}",
                    "ok": False,
                    "status_text": "执行失败",
                    "sign_text": "-",
                    "streak": "-",
                    "blind_box": "-",
                    "message": f"初始化失败: {e}",
                }
            )

    dispatch_notify()


if __name__ == "__main__":
    main()
