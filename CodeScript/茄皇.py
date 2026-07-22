"""
统一快乐星球茄皇（五期） v1.1.0（mywc网关聚合推送版）

功能：自动完成统一快乐星球茄皇（五期）任务，支持多账号执行，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL                 必填其一，自建授权服务器域名
   - 示例：http://127.0.0.1:8110
   - 脚本会自动拼接 /mywc
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=wx532ecb3bdaaf92f9
   - 请求头：auth=账号标识

2. 账号变量：
   qiehuang_wxid 或 QIEHUANG_WXID                推荐，茄皇专属账号变量
   - 兼容旧变量：wx_openid
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
   名称：统一快乐星球茄皇（五期）
   命令：python3 茄皇(1).py
   定时：每天运行 1 次即可，具体时间自行调整
"""

import base64
import json
import os
import random
import re
import time
from datetime import datetime

import requests

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    CRYPTO_BACKEND = "cryptography"
except ImportError:
    try:
        from Crypto.Cipher import AES, PKCS1_OAEP
        from Crypto.Hash import SHA256
        from Crypto.PublicKey import RSA

        CRYPTO_BACKEND = "pycryptodome"
    except ImportError:
        CRYPTO_BACKEND = None


SCRIPT_TITLE = "统一快乐星球茄皇（五期）"
GLOBAL_NOTIFY_BUFFERS = []
BASE_URL = "https://farmgames.ioutu.cn"
APP_ID = "wx532ecb3bdaaf92f9"
WEIMOB_LOGIN_URL = "https://xapi.weimob.com/fe/mapi/user/loginX"
WEIMOB_CID = "176205957"
WEIMOB_BOS_ID = "4020112618957"
WEIMOB_VID = "6013753979957"
PUBLIC_KEY = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA70sK419vy3MabW3lEGlk"
    "7Zh1u78OdnVlioVazp5Y46eBh+/TDqo/wZ9VrQ/4MmAtoP0vJ2vmwP5gqO3WPoj"
    "b07WddXfF1eU+5M+Rj3s0eSRrvZvBcGZ3qK0dOgZJScK66IDQazt/c4xqhDcsI"
    "tIyNRahUqB/IKc6E80GZJvMvFtZVSCseAXC0mAJXhi1AdUOlP+3Pv0fiUVejTJp"
    "1j7LBNWJ7Z5/8mRcclQH0vmxsdYsaV3qZiJ2d/CfNoKcwmI2IWmeZy8NP5U8Hn"
    "0AsxPEwjdHoEqG/iy/SoA46TZL+RLtWqUSHXpaKR/VFN0rbl25SE91X8FTfLqyD"
    "8LfGMCwRQIDAQAB"
)
USER_AGENT = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 26_5_2 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 "
    "MicroMessenger/8.0.75(0x18004b42) NetType/WIFI Language/zh_CN "
    "miniProgram/wx532ecb3bdaaf92f9"
)
SUPPORTED_TASK_TYPES = {"SIGN", "BROWSE", "SHARE"}
FRIEND_TASK_TYPE = "FRIEND_STEAL_ENERGY"
FRIEND_STATUS_CLAIMABLE = "0"


def split_accounts(raw_text):
    return [
        item.strip()
        for item in re.split(r"[&,\n，]+", str(raw_text or ""))
        if item.strip()
    ]


def parse_users():
    raw_text = (
        os.getenv("qiehuang_wxid")
        or os.getenv("QIEHUANG_WXID")
        or os.getenv("wx_openid")
        or ""
    )
    return split_accounts(raw_text)


def get_wx_server_url():
    server_url = (os.getenv("wx_server_url") or os.getenv("WX_SERVER_URL") or "").strip()
    return server_url.rstrip("/")


def nested_value(data, paths):
    for path in paths:
        value = data
        for key in path:
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(key)
            if value in (None, ""):
                break
        if value not in (None, ""):
            return value
    return None


def mask_account(account):
    account = str(account or "-").strip()
    if len(account) <= 8:
        return account
    return f"{account[:4]}***{account[-4:]}"


def short_open_id(open_id):
    open_id = str(open_id or "").strip()
    if not open_id:
        return "-"
    return f"{open_id[:6]}...{open_id[-4:]}" if len(open_id) > 12 else open_id


def append_notify_result(result):
    GLOBAL_NOTIFY_BUFFERS.append(result)


def build_notify_report():
    total = len(GLOBAL_NOTIFY_BUFFERS)
    success_items = [item for item in GLOBAL_NOTIFY_BUFFERS if item.get("ok")]
    failed_items = [item for item in GLOBAL_NOTIFY_BUFFERS if not item.get("ok")]
    total_gained = sum(int(item.get("gained_tomato") or 0) for item in success_items)

    success_accounts = "、".join(item.get("account") for item in success_items) or "-"
    failed_accounts = "、".join(item.get("account") for item in failed_items) or "-"

    lines = [
        "==============================",
        f"🕒 执行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"📊 统计数据：成功 {len(success_items)} / 总计 {total}",
        f"✅ 成功账号：{len(success_items)} 个",
        f"❌ 失败账号：{len(failed_items)} 个",
        f"🍅 累计番茄：+{total_gained}",
        f"🙋 成功列表：{success_accounts}",
        f"💥 失败列表：{failed_accounts}",
        "==============================",
    ]

    for item in GLOBAL_NOTIFY_BUFFERS:
        ok = bool(item.get("ok"))
        account_icon = "🧑‍💻" if ok else "🧟"
        status_icon = "✅" if ok else "❌"
        status_text = item.get("status_text") or ("执行成功" if ok else "执行失败")

        lines.extend(
            [
                f"{account_icon} 【账号{item.get('index')}】{item.get('account')}",
                f"{status_icon} 状态：{status_text}",
                f"🔐 标识：wid={item.get('wid') or '-'}，openId={item.get('open_id') or '-'}",
            ]
        )

        if ok:
            lines.extend(
                [
                    (
                        f"⚙️ 任务：完成 {item.get('completed_tasks', 0)} 个，"
                        f"跳过 {item.get('skipped_tasks', 0)} 个，"
                        f"好友收取 {item.get('friend_count', 0)} 位"
                    ),
                    (
                        f"⚡ 能量：始 {item.get('initial_energy', 0)} ➔ "
                        f"终 {item.get('final_energy', 0)}"
                    ),
                    (
                        f"🍅 番茄：始 {item.get('initial_tomato', 0)} ➔ "
                        f"终 {item.get('final_tomato', 0)}，获得 +{item.get('gained_tomato', 0)}"
                    ),
                ]
            )
        else:
            lines.append(f"🧨 原因：{item.get('message') or '未知错误'}")

        extra_lines = item.get("detail_lines") or []
        for detail in extra_lines:
            lines.append(f"• {detail}")
        lines.append("------------------------------")

    return "\n".join(lines)


def dispatch_notify():
    if not GLOBAL_NOTIFY_BUFFERS:
        print("通知缓冲区为空，跳过推送。")
        return

    content = build_notify_report()
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


def get_wx_code(wxid):
    server_url = get_wx_server_url()
    if not server_url:
        raise RuntimeError("未配置 wx_server_url 或 WX_SERVER_URL")

    response = requests.get(
        f"{server_url}/mywc",
        params={"wxid": wxid, "appId": APP_ID},
        headers={"auth": wxid},
        timeout=20,
    )
    response.raise_for_status()
    result = response.json()
    code = nested_value(
        result,
        [
            ("data", "data", "code"),
            ("data", "data", "loginCode"),
            ("data", "data", "wxcode"),
            ("data", "code"),
            ("data", "loginCode"),
            ("data", "wxcode"),
            ("result", "data", "code"),
            ("result", "data", "wxcode"),
            ("result", "code"),
            ("code",),
            ("loginCode",),
            ("wxcode",),
        ],
    )
    if not isinstance(code, str) or len(code.strip()) < 10:
        raise RuntimeError(f"mywc 未返回有效 code：{str(result)[:180]}")
    return code.strip()


def login_weimob_by_code(code):
    payload = {
        "basicInfo": {
            "cid": WEIMOB_CID,
            "vid": WEIMOB_VID,
            "tcode": "weimob",
            "bosId": WEIMOB_BOS_ID,
        },
        "extendInfo": {"source": 1},
        "parentVid": 0,
        "is_pre_fetch_open": True,
        "env": "production",
        "storeId": "0",
        "appid": APP_ID,
        "pid": WEIMOB_BOS_ID,
        "code": code,
        "queryAuthConfig": True,
        "relevanceAuthRequest": None,
    }
    response = requests.post(
        WEIMOB_LOGIN_URL,
        json=payload,
        headers={
            "User-Agent": USER_AGENT,
            "Referer": f"https://servicewechat.com/{APP_ID}/288/page-frame.html",
            "Content-Type": "application/json",
            "x-biz-id": "1",
            "cloud-pid": WEIMOB_BOS_ID,
            "weimob-cid": WEIMOB_CID,
            "weimob-bosid": WEIMOB_BOS_ID,
            "x-req-from": "cms",
            "cloud-project-name": "tongyixiangmu",
            "weimob-pid": WEIMOB_BOS_ID,
        },
        timeout=20,
    )
    response.raise_for_status()
    result = response.json()
    data = result.get("data") or {}
    errcode = result.get("errcode")
    wid = data.get("wid")
    open_id = data.get("openId") or data.get("openid")
    if str(errcode) != "0" or not wid or not open_id:
        message = result.get("errmsg") or "未返回 wid/openId"
        raise RuntimeError(f"code 登录失败：{message}；{str(result)[:180]}")
    return str(wid), str(open_id)


def resolve_identity(wxid):
    last_error = None
    for attempt in range(1, 4):
        try:
            return login_weimob_by_code(get_wx_code(wxid))
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(2)
    raise RuntimeError(f"mywc/code 登录失败（已重试 3 次）：{last_error}")


def encrypt_payload(payload):
    if CRYPTO_BACKEND is None:
        raise RuntimeError("缺少加密依赖，请安装 cryptography：pip install cryptography")

    plaintext = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    aes_key = os.urandom(32)
    iv = os.urandom(12)
    public_key_der = base64.b64decode(PUBLIC_KEY)

    if CRYPTO_BACKEND == "cryptography":
        public_key = serialization.load_der_public_key(public_key_der)
        encrypted_data = AESGCM(aes_key).encrypt(iv, plaintext, None)
        encrypted_key = public_key.encrypt(
            aes_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
    else:
        cipher = AES.new(aes_key, AES.MODE_GCM, nonce=iv)
        ciphertext, tag = cipher.encrypt_and_digest(plaintext)
        encrypted_data = ciphertext + tag
        public_key = RSA.import_key(public_key_der)
        encrypted_key = PKCS1_OAEP.new(public_key, hashAlgo=SHA256).encrypt(aes_key)

    return {
        "data": base64.b64encode(encrypted_data).decode(),
        "key": base64.b64encode(encrypted_key).decode(),
        "iv": base64.b64encode(iv).decode(),
    }


class TomatoClient:
    def __init__(self, wid, open_id):
        self.wid = wid
        self.open_id = open_id
        self.tomato_user_id = None
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/?wid={wid}&openId={open_id}",
            }
        )

    def request(self, method, path, payload=None, encrypted=True, retry=2):
        url = f"{BASE_URL}{path}"
        for attempt in range(retry + 1):
            kwargs = {"timeout": 20}
            if payload is not None:
                kwargs["json"] = encrypt_payload(payload) if encrypted else payload
                if encrypted:
                    kwargs["headers"] = {"X-Request-Encrypted": "true"}
            response = self.session.request(method, url, **kwargs)
            if response.status_code == 429 and attempt < retry:
                retry_after = response.headers.get("Retry-After", "2")
                try:
                    wait_seconds = max(1.0, float(retry_after))
                except ValueError:
                    wait_seconds = 2.0
                time.sleep(wait_seconds + attempt)
                continue

            response.raise_for_status()
            try:
                result = response.json()
            except ValueError as exc:
                raise RuntimeError(f"接口返回非 JSON 数据：{response.text[:200]}") from exc

            msg = str(result.get("msg", ""))
            if result.get("code") == 200:
                return result
            if attempt < retry and (
                response.status_code == 429 or "频繁" in msg or "稍后" in msg
            ):
                time.sleep(2.5 + attempt * 1.5)
                continue
            raise RuntimeError(msg or f"接口返回 code={result.get('code')}")
        raise RuntimeError("请求重试后仍未成功")

    def login(self):
        result = self.request(
            "POST",
            "/api/web/open/tomato/login",
            {
                "shareTomatoUserId": None,
                "openId": self.open_id,
                "wid": self.wid,
                "queryCardStatus": True,
            },
        )
        data = result.get("data") or {}
        token = data.get("token")
        if not token:
            raise RuntimeError("登录响应中没有 token")
        self.session.headers["Authorization"] = token
        self.tomato_user_id = data.get("tomatoUserId")
        return data

    def home(self):
        return self.request("GET", "/api/web/member/tomato/home").get("data") or {}

    def tasks(self):
        return self.request("GET", "/api/web/member/tomato/tasks").get("data") or []

    def complete_task(self, task):
        task_type = task.get("taskType")
        payload = {"taskType": task_type}
        if task_type != "SHARE":
            payload["browseTarget"] = task.get("browseTarget") or ""
        elif self.tomato_user_id:
            try:
                self.request(
                    "POST",
                    "/api/web/member/tomato/miniprogram/qrcode/create",
                    {
                        "page": "packages/wm-cloud-qiehuang/home/index",
                        "scene": str(self.tomato_user_id),
                    },
                )
            except Exception:
                pass
        return self.request(
            "POST", "/api/web/member/tomato/tasks/complete", payload
        ).get("data") or {}

    def friends(self, page_size=20):
        friends = []
        page_num = 1
        while True:
            result = self.request(
                "GET",
                f"/api/web/member/tomato/friends?pageNum={page_num}&pageSize={page_size}",
            )
            rows = result.get("rows") or []
            friends.extend(rows)
            total = int(result.get("total") or 0)
            if not rows or (total and len(friends) >= total) or len(rows) < page_size:
                break
            page_num += 1
        return friends

    def friend_home(self, friend_user_id):
        return self.request(
            "GET",
            f"/api/web/member/tomato/friends/{friend_user_id}/home",
        ).get("data") or {}

    def steal_friend_energy(self, friend_user_id):
        return self.request(
            "POST",
            "/api/web/member/tomato/friends/steal",
            {"friendTomatoUserId": friend_user_id},
        ).get("data")

    def use_energy(self):
        return self.request(
            "POST", "/api/web/member/tomato/energy/use", payload={}, encrypted=False
        ).get("data") or {}


def process_user(wxid, index):
    wid = ""
    open_id = ""
    detail_lines = []

    try:
        wid, open_id = resolve_identity(wxid)
        print(f"账号{index} code 登录成功：wid={wid}，openId={short_open_id(open_id)}")
        client = TomatoClient(wid, open_id)

        login_data = client.login()
        nickname = login_data.get("nickName") or "未设置昵称"
        home = client.home()
        initial_energy = int(home.get("energyBalance") or 0)
        initial_tomato = int(home.get("tomatoBalance") or 0)

        detail_lines.append(f"昵称：{nickname}")
        detail_lines.append(
            f"初始状态：能量 {initial_energy}，番茄 {initial_tomato}，阶段 {home.get('stageName', '未知阶段')}"
        )

        completed = 0
        skipped = 0
        friend_task = None

        for task in client.tasks():
            name = task.get("taskName") or task.get("taskCode") or "未知任务"
            task_type = task.get("taskType")
            if task_type == FRIEND_TASK_TYPE:
                friend_task = task
                if str(task.get("completed")) == "1":
                    detail_lines.append(f"任务已完成：{name}")
                continue
            if str(task.get("completed")) == "1":
                detail_lines.append(f"任务已完成：{name}")
                continue
            if task_type not in SUPPORTED_TASK_TYPES:
                skipped += 1
                detail_lines.append(f"跳过任务：{name}（需手动处理）")
                continue
            try:
                result = client.complete_task(task)
                reward = result.get("rewardText") or task.get("rewardText") or "已领取"
                detail_lines.append(f"任务完成：{name}，{reward}")
                completed += 1
            except Exception as exc:
                detail_lines.append(f"任务失败：{name}，{exc}")
            time.sleep(random.uniform(2.5, 3.5))

        friend_count = 0
        friend_energy = 0
        failed_friend_count = 0
        try:
            claimable_friends = [
                friend
                for friend in client.friends()
                if str(friend.get("friendStatus")) == FRIEND_STATUS_CLAIMABLE
                and friend.get("friendTomatoUserId")
            ]
            for friend in claimable_friends:
                friend_user_id = friend.get("friendTomatoUserId")
                if not friend_user_id:
                    continue
                try:
                    friend_home = client.friend_home(friend_user_id)
                    amount = int(friend_home.get("stealAmount") or 0)
                    if str(friend_home.get("canSteal")) != "1" or amount <= 0:
                        continue
                    client.steal_friend_energy(friend_user_id)
                    friend_count += 1
                    friend_energy += amount
                except Exception:
                    failed_friend_count += 1
                time.sleep(random.uniform(1.5, 2.5))

            if friend_count:
                msg = f"好友能量：成功收取 {friend_count} 位，共 {friend_energy} 能量"
                if failed_friend_count:
                    msg = f"{msg}，失败 {failed_friend_count} 位"
                detail_lines.append(msg)
                if friend_task and str(friend_task.get("completed")) != "1":
                    completed += 1
            elif failed_friend_count:
                detail_lines.append(f"好友能量：收取失败 {failed_friend_count} 位")
            else:
                detail_lines.append("好友能量：暂无可收取能量")
        except Exception as exc:
            detail_lines.append(f"好友能量失败：{exc}")

        home = client.home()
        energy_before_use = int(home.get("energyBalance") or 0)
        before_use_tomato = int(home.get("tomatoBalance") or 0)
        final_home = home
        gained_tomato = 0

        if energy_before_use > 0:
            try:
                grown = client.use_energy()
                final_home = grown or home
                final_tomato_value = int(final_home.get("tomatoBalance") or before_use_tomato)
                gained_tomato = int(final_home.get("gainedTomatoAmount") or 0)
                if gained_tomato <= 0:
                    gained_tomato = max(0, final_tomato_value - before_use_tomato)
                detail_lines.append(
                    (
                        f"使用能量：消耗 {final_home.get('usedEnergyAmount', energy_before_use)}，"
                        f"阶段 {final_home.get('stageName', '未知阶段')}，获得番茄 {gained_tomato}"
                    )
                )
            except Exception as exc:
                detail_lines.append(f"使用能量失败：{exc}")
        else:
            detail_lines.append("使用能量：当前没有可用能量")

        final_energy = int(final_home.get("energyBalance") or 0)
        final_tomato = int(final_home.get("tomatoBalance") or before_use_tomato)
        if gained_tomato <= 0:
            gained_tomato = max(0, final_tomato - initial_tomato)

        detail_lines.append(
            f"最终状态：能量 {final_energy}，番茄 {final_tomato}，阶段 {final_home.get('stageName', '未知阶段')}"
        )

        return {
            "index": index,
            "ok": True,
            "status_text": "执行成功",
            "account": mask_account(wxid),
            "wid": wid,
            "open_id": short_open_id(open_id),
            "completed_tasks": completed,
            "skipped_tasks": skipped,
            "friend_count": friend_count,
            "friend_energy": friend_energy,
            "initial_energy": initial_energy,
            "final_energy": final_energy,
            "initial_tomato": initial_tomato,
            "final_tomato": final_tomato,
            "gained_tomato": gained_tomato,
            "message": "",
            "detail_lines": detail_lines,
        }
    except Exception as exc:
        return {
            "index": index,
            "ok": False,
            "status_text": "执行失败",
            "account": mask_account(wxid),
            "wid": wid,
            "open_id": short_open_id(open_id),
            "completed_tasks": 0,
            "skipped_tasks": 0,
            "friend_count": 0,
            "friend_energy": 0,
            "initial_energy": 0,
            "final_energy": 0,
            "initial_tomato": 0,
            "final_tomato": 0,
            "gained_tomato": 0,
            "message": str(exc),
            "detail_lines": detail_lines,
        }


def main():
    users = parse_users()

    if CRYPTO_BACKEND is None:
        append_notify_result(
            {
                "index": 1,
                "ok": False,
                "status_text": "配置错误",
                "account": "-",
                "wid": "-",
                "open_id": "-",
                "completed_tasks": 0,
                "skipped_tasks": 0,
                "friend_count": 0,
                "friend_energy": 0,
                "initial_energy": 0,
                "final_energy": 0,
                "initial_tomato": 0,
                "final_tomato": 0,
                "gained_tomato": 0,
                "message": "缺少加密依赖，请安装 cryptography：pip install cryptography",
                "detail_lines": [],
            }
        )
        dispatch_notify()
        return

    if not get_wx_server_url():
        append_notify_result(
            {
                "index": 1,
                "ok": False,
                "status_text": "配置错误",
                "account": "-",
                "wid": "-",
                "open_id": "-",
                "completed_tasks": 0,
                "skipped_tasks": 0,
                "friend_count": 0,
                "friend_energy": 0,
                "initial_energy": 0,
                "final_energy": 0,
                "initial_tomato": 0,
                "final_tomato": 0,
                "gained_tomato": 0,
                "message": "未配置 wx_server_url 或 WX_SERVER_URL",
                "detail_lines": [],
            }
        )
        dispatch_notify()
        return

    if not users:
        append_notify_result(
            {
                "index": 1,
                "ok": False,
                "status_text": "配置错误",
                "account": "-",
                "wid": "-",
                "open_id": "-",
                "completed_tasks": 0,
                "skipped_tasks": 0,
                "friend_count": 0,
                "friend_energy": 0,
                "initial_energy": 0,
                "final_energy": 0,
                "initial_tomato": 0,
                "final_tomato": 0,
                "gained_tomato": 0,
                "message": "未读取到 qiehuang_wxid / QIEHUANG_WXID，兼容旧变量 wx_openid",
                "detail_lines": [],
            }
        )
        dispatch_notify()
        return

    for index, wxid in enumerate(users, 1):
        print(f"\n===== 开始处理账号 {index}：{mask_account(wxid)} =====")
        result = process_user(wxid, index)
        append_notify_result(result)
        if result.get("ok"):
            print(
                f"账号{index}执行成功：番茄 +{result.get('gained_tomato', 0)}，"
                f"完成任务 {result.get('completed_tasks', 0)} 个"
            )
        else:
            print(f"账号{index}执行失败：{result.get('message')}")
        if index < len(users):
            time.sleep(random.uniform(3, 5))

    dispatch_notify()


if __name__ == "__main__":
    main()
