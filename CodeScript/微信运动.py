#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
青龙脚本：微信运动步数提交（不支持yyb协议,其他的可以）

环境变量：
    wx_server_url      接口服务地址，例如：http://127.0.0.1:8110
    sports_wxid  微信 wxid，多个账号用 & 区分
    sports_num   步数范围，格式：最小值-最大值，默认 12345-23456
"""

import os
import random
import sys
import time
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

try:
    from SendNotify import send_push_notification
except Exception as exc:
    print(f"[警告] 导入 SendNotify.py 失败：{exc}，将跳过通知推送。")
    def send_push_notification(text, desp):
        pass

DEFAULT_NUM_RANGE = "22345-36456"
TIMEOUT = 20
MAX_STEP_LIMIT = 98000
# 缓存文件名
CACHE_FILE = Path(__file__).resolve().parent / "vxsports_step_cache.json"

GLOBAL_NOTIFY_BUFFERS = []


def get_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def parse_num_range(raw: str) -> tuple[int, int]:
    raw = (raw or DEFAULT_NUM_RANGE).strip()
    if "-" not in raw:
        try:
            value = int(raw)
            return value, value
        except ValueError:
            raw = DEFAULT_NUM_RANGE

    left, right = raw.split("-", 1)
    try:
        min_num = int(left.strip())
        max_num = int(right.strip())
    except ValueError:
        min_num, max_num = map(int, DEFAULT_NUM_RANGE.split("-", 1))

    if min_num > max_num:
        min_num, max_num = max_num, min_num
    return min_num, max_num


# ── 核心痛点：读写增量 JSON 锁缓存（单日内步数只增不减，0点重置） ──
def load_cache() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(cache_data: dict):
    try:
        CACHE_FILE.write_text(json.dumps(cache_data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        print(f⚠️ 写入本地缓存失败: {e}")


def get_safe_step(wxid: str, min_num: int, max_num: int) -> int:
    cache = load_cache()
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    # 初始化或重置今日数据
    if wxid not in cache or cache[wxid].get("date") != today_str:
        cache[wxid] = {"date": today_str, "last_step": 0, "success": False}
        save_cache(cache)

    last_step = cache[wxid].get("last_step", 0)
    
    # 随机本次步数
    target_step = random.randint(min_num, max_num)
    
    # 核心拦截：如果随机出的步数小于或等于上一次步数，强行在上一次基础上递增突破（防止微信步数覆盖失败）
    if target_step <= last_step:
        target_step = last_step + random.randint(1000, 3000)
        
    # 硬性封顶限制
    if target_step > MAX_STEP_LIMIT:
        target_step = MAX_STEP_LIMIT
        
    return target_step


def update_account_cache(wxid: str, step: int, success: bool):
    cache = load_cache()
    today_str = datetime.now().strftime("%Y-%m-%d")
    cache[wxid] = {
        "date": today_str,
        "last_step": step,
        "success": success
    }
    save_cache(cache)


def build_url(host: str, wxid: str, num: int) -> str:
    host = host.rstrip("/")
    query = urlencode({"wxid": wxid, "num": str(num)})
    return f"{host}/mybs?{query}"


def request_step(host: str, wxid: str, num: int) -> bool:
    url = build_url(host, wxid, num)
    print(f"🚀 提交账号：{wxid}，安全增量步数：{num}")

    req = Request(
        url,
        headers={
            "User-Agent": "QingLong-Sports-Step/1.0",
            "Accept": "*/*",
            "auth": wxid,
        },
        method="GET",
    )

    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            status = resp.getcode()
            body = resp.read().decode("utf-8", errors="replace").strip()
            print(f"✅ HTTP {status}")
            return 200 <= status < 300
    except Exception as e:
        print(f"❌ 请求接口发生错误：{e}")
    return False


def main() -> int:
    wx_server_url = get_env("wx_server_url")
    sports_wxid = get_env("sports_wxid")
    sports_num = get_env("sports_num", DEFAULT_NUM_RANGE)

    if not wx_server_url or not sports_wxid:
        print("❌ 请先检查配置环境变量 wx_server_url 或 sports_wxid")
        return 1

    wxids = [item.strip() for item in sports_wxid.split("&") if item.strip()]
    if not wxids:
        print("❌ sports_wxid 未解析到有效账号")
        return 1

    min_num, max_num = parse_num_range(sports_num)

    print("============== 微信运动步数提交 ==============")
    print(f"接口地址：{wx_server_url.rstrip('/')}/mybs")
    print(f"账号数量：{len(wxids)}")
    print("============================================")

    success = 0
    fail = 0

    for index, wxid in enumerate(wxids, start=1):
        print(f"\n---------- 账号 {index}/{len(wxids)} ----------")
        
        # 联动安全增量缓存控制
        num = get_safe_step(wxid, min_num, max_num)
        
        ok = request_step(wx_server_url, wxid, num)
        
        # 回写本地 JSON 状态
        update_account_cache(wxid, num, ok)
        
        # 核心痛点：清洗数据结构，剥离原始 JSON 杂质，极简报表
        summary = {
            "wxid": wxid,
            "num": num,
            "status": "✅ 成功" if ok else "❌ 失败"
        }
        GLOBAL_NOTIFY_BUFFERS.append(summary)

        if ok:
            success += 1
        else:
            fail += 1

        if index < len(wxids):
            time.sleep(random.randint(1, 3))

    print("\n================ 执行完成 ================")
    print(f"成功：{success} | 失败：{fail}")
    print("========================================")

    if GLOBAL_NOTIFY_BUFFERS:
        title = "🔔 微信运动步数提交总结"
        desp_lines = [
            "==============================",
            f"📊 统计数据：成功 {success} / 总计 {len(wxids)}",
            "==============================\n"
        ]
        
        for item in GLOBAL_NOTIFY_BUFFERS:
            # 极简卡片输出
            desp_lines.append(f"👤 【{item['wxid']}】")
            desp_lines.append(f"   🏃 同步步数: {item['num']} 步")
            desp_lines.append(f"   📝 执行结果: {item['status']}")
            desp_lines.append("------------------------------")
            
        final_desp = "\n".join(desp_lines)
        print("\n[精简推送报表阅览]\n" + final_desp)
        send_push_notification(title, final_desp)

    return 0 if fail == 0 else 2


if __name__ == "__main__":
    sys.exit(main())