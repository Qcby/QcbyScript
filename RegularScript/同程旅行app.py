# -*- coding=UTF-8 -*-
"""
同程旅行APP福利中心 v1.1.0

功能：
  同程旅行 APP 福利中心任务执行、里程查询、聚合推送。

抓包说明：
  打开同程旅行 APP，进入“领福利”页面，点击签到。
  抓取 https://app.17u.cn/welfarecenter/index/signIndex 请求头中的 phone、apptoken、device。

青龙环境变量：
  tc_cookie
    账号格式：phone#apptoken#device
    多账号支持 &、@、英文逗号、中文逗号、换行分隔

推送：
  使用同目录 SendNotify.py 的 send_push_notification(title, content)

依赖：
  httpx

青龙定时：
  10 9 * * * python3 同程旅行app.py
"""
import os
import time
import httpx
import asyncio
import re
from datetime import datetime
from typing import Any, Dict, List


SCRIPT_TITLE = "同程旅行APP福利中心"
GLOBAL_NOTIFY_BUFFERS: List[Dict[str, Any]] = []

def fn_print(message):
    print(message)

def get_env(env_name):
    env_value = os.getenv(env_name)
    if not env_value:
        return []
    return [item.strip() for item in re.split(r"[&@,\n，]+", env_value) if item.strip()]


def mask_phone(phone: str) -> str:
    text = str(phone or "")
    if len(text) >= 7:
        return f"{text[:3]}****{text[-4:]}"
    return text or "-"


def append_notify_result(result: Dict[str, Any]) -> None:
    GLOBAL_NOTIFY_BUFFERS.append(result)


def build_notify_report() -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(GLOBAL_NOTIFY_BUFFERS)
    success_items = [item for item in GLOBAL_NOTIFY_BUFFERS if item.get("ok")]
    failed_items = [item for item in GLOBAL_NOTIFY_BUFFERS if not item.get("ok")]
    total_today_mileage = sum(int(item.get("today_mileage") or 0) for item in success_items)
    lines = [
        "==============================",
        f"🕒 执行时间：{now}",
        f"📊 统计数据：成功 {len(success_items)} / 总计 {total}",
        f"✅ 成功账号：{len(success_items)} 个",
        f"❌ 失败账号：{len(failed_items)} 个",
        f"💰 今日里程：+{total_today_mileage}",
        "==============================",
    ]
    for item in GLOBAL_NOTIFY_BUFFERS:
        icon = "🧑‍💻" if item.get("ok") else "🧟"
        status_icon = "✅" if item.get("ok") else "❌"
        lines.extend(
            [
                f"{icon} 【账号{item.get('index', '-')}】{item.get('account', '-')}",
                f"{status_icon} 状态：{item.get('status_text', '-')}",
                f"🎯 任务：{item.get('task_text', '-')}",
                f"💰 里程：{item.get('mileage', '-')}",
                f"📈 今日：+{item.get('today_mileage', 0)}",
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

class Tclx:
    def __init__(self, cookie):
        parts = str(cookie).split("#")
        if len(parts) < 3:
            raise ValueError("账号格式错误，应为 phone#apptoken#device")
        self.client = httpx.AsyncClient(base_url="https://app.17u.cn/welfarecenter",
                                        verify=False,
                                        timeout=60)
        self.phone = parts[0].strip()
        self.apptoken = parts[1].strip()
        self.device = parts[2].strip()
        self.headers = {
            'accept': 'application/json, text/plain, */*',
            'phone': self.phone,
            'channel': '1',
            'apptoken': self.apptoken,
            'sec-fetch-site': 'same-site',
            'accept-language': 'zh-CN,zh-Hans;q=0.9',
            'accept-encoding': 'gzip, deflate, br',
            'sec-fetch-mode': 'cors',
            'origin': 'https://m.17u.cn',
            'user-agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0_3 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 TcTravel/11.0.0 tctype/wk',
            'referer': 'https://m.17u.cn/',
            'device': self.device,
            'denc': 'br',
            'sv': '3',
            'secver': '4',
            'aenc': 'br',
            'Os-Type': '1',
            'sec-fetch-dest': 'empty'
        }
        self.account_result = ""
        self.token_invalid = False  # 标记token是否失效
        self.task_ok = 0
        self.task_fail = 0
        self.task_skip = 0
        self.mileage = 0
        self.today_mileage = 0
        self.error_message = ""

    def account_print(self, message):
        """只打印到控制台，不收集到通知中"""
        fn_print(f"用户【{self.phone}】 - {message}")

    @staticmethod
    async def get_today_date():
        return datetime.now().strftime('%Y-%m-%d')

    async def sign_in(self):
        """仅用于验证 token 有效性及获取里程，不执行签到操作"""
        try:
            response = await self.client.post(
                url="/index/signIndex",
                headers=self.headers,
                json={}
            )
            data = response.json()
            if data['code'] != 2200:
                self.account_print("token失效了，请更新")
                self.token_invalid = True
                return None
            else:
                mileage = data['data']['mileageBalance']['mileage']
                self.account_print(f"Token验证成功，当前里程{mileage}")
                return data['data']['todaySign']
        except Exception as e:
            self.account_print(f"签到请求异常！{e}")
            return None

    async def do_sign_in(self):
        """原本的签到函数，本次不再调用"""
        today_date = await self.get_today_date()
        try:
            response = await self.client.post(
                url="/index/sign",
                headers=self.headers,
                json={"type": 1, "day": today_date}
            )
            data = response.json()
            if data['code'] != 2200:
                self.account_print(f"签到失败！错误信息：{data.get('message', '未知错误')}")
                return False
            else:
                self.account_print("签到成功！")
                return True
        except Exception as e:
            self.account_print(f"执行签到请求异常！{e}")
            return False

    async def get_task_list(self):
        try:
            response = await self.client.post(
                url="/task/taskList?version=11.0.7",
                headers=self.headers,
                json={}
            )
            data = response.json()
            if data['code'] != 2200:
                self.account_print("获取任务列表失败了")
                return None
            else:
                tasks = []
                for task in data['data']:
                    if task['state'] == 1 and task['browserTime'] != 0:
                        tasks.append(
                            {
                                'taskCode': task['taskCode'],
                                'title': task['title'],
                                'browserTime': task['browserTime']
                            }
                        )
                return tasks
        except Exception as e:
            self.account_print(f"获取任务列表请求异常！{e}")
            return None

    async def perform_tasks(self, task_code):
        try:
            response = await self.client.post(
                url="/task/start",
                headers=self.headers,
                json={"taskCode": task_code}
            )
            data = response.json()
            if data['code'] != 2200:
                self.account_print(f"执行任务【{task_code}】失败了，跳过当前任务")
                return None
            else:
                task_id = data['data']
                return task_id
        except Exception as e:
            self.account_print(f"执行任务【{task_code}】请求异常！{e}")
            return None

    async def finsh_task(self, task_id):
        max_retry = 3
        retry_delay = 2
        for attempt in range(max_retry):
            try:
                response = await self.client.post(
                    url="/task/finish",
                    headers=self.headers,
                    json={"id": task_id}
                )
                data = response.json()
                if data['code'] == 2200:
                    self.account_print(f"完成任务【{task_id}】成功！开始领取奖励")
                    return True
                if attempt < max_retry - 1:
                    self.account_print(f"完成任务【{task_id}】失败了，尝试重新提交（第{attempt + 1}次重试。。）")
                    await asyncio.sleep(retry_delay * (attempt + 1))
                    continue
                self.account_print(f"完成任务【{task_id}】最终失败，跳过当前任务")
                return False
            except Exception as e:
                self.account_print(f"完成任务【{task_id}】请求异常！{e}")
                if attempt == max_retry - 1:
                    return False
                await asyncio.sleep(retry_delay * (attempt + 1))

    async def receive_reward(self, task_id):
        try:
            response = await self.client.post(
                url="/task/receive",
                headers=self.headers,
                json={"id": task_id}
            )
            data = response.json()
            if data['code'] != 2200:
                self.account_print("领取签到奖励失败了， 请尝试手动领取")
                self.task_fail += 1
            else:
                self.account_print("领取签到奖励成功！开始下一个任务")
                self.task_ok += 1
        except Exception as e:
            self.account_print(f"领取签到奖励请求异常！{e}")
            self.task_fail += 1

    async def get_mileage_info(self):
        try:
            response = await self.client.post(
                url="/index/signIndex",
                headers=self.headers,
                json={}
            )
            data = response.json()
            if data['code'] != 2200:
                self.account_print("获取积分信息失败了")
                return None
            else:
                mileage = data['data']['mileageBalance']['mileage']
                today_mileage = data['data']['mileageBalance']['todayMileage']
                self.account_print(f"当前剩余里程{mileage}，今日获取{today_mileage}里程")
                return {
                    'mileage': mileage,
                    'today_mileage': today_mileage
                }
        except Exception as e:
            self.account_print(f"获取积分信息请求异常！{e}")
            return None

    async def run(self):
        self.account_result = f"📱 账号：{self.phone}\n"
        today_sign = await self.sign_in()
        if today_sign is None:
            self.error_message = "token失效，请更新"
            return
            
        tasks = await self.get_task_list()
        if tasks:
            for task in tasks:
                task_code = task['taskCode']
                title = task['title']
                browser_time = task['browserTime']
                self.account_print(f"开始做任务【{title}】，需要浏览{browser_time}秒")
                task_id = await self.perform_tasks(task_code)
                if task_id:
                    await asyncio.sleep(browser_time)
                    if await self.finsh_task(task_id):
                        await self.receive_reward(task_id)
                    else:
                        self.task_fail += 1
                else:
                    self.task_fail += 1
        else:
            self.task_skip += 1
        
        mileage_info = await self.get_mileage_info()
        if mileage_info:
            self.mileage = mileage_info["mileage"]
            self.today_mileage = mileage_info["today_mileage"]
        else:
            self.error_message = self.error_message or "获取里程信息失败"

    def notify_result(self, index: int) -> Dict[str, Any]:
        ok = not self.error_message and not self.token_invalid
        return {
            "index": index,
            "account": mask_phone(self.phone),
            "ok": ok,
            "status_text": "执行成功" if ok else "执行失败",
            "task_text": f"成功{self.task_ok}/跳过{self.task_skip}/失败{self.task_fail}",
            "mileage": self.mileage,
            "today_mileage": self.today_mileage,
            "message": self.error_message or "-",
        }


async def main():
    tasks = []
    account_instances = []

    tc_cookies = get_env("tc_cookie")
    if not tc_cookies:
        append_notify_result(
            {
                "index": 1,
                "account": "配置检查",
                "ok": False,
                "status_text": "执行失败",
                "task_text": "-",
                "mileage": "-",
                "today_mileage": 0,
                "message": "未配置 tc_cookie",
            }
        )
        return

    for index, cookie in enumerate(tc_cookies, 1):
        try:
            tclx = Tclx(cookie)
        except Exception as e:
            append_notify_result(
                {
                    "index": index,
                    "account": f"账号{index}",
                    "ok": False,
                    "status_text": "执行失败",
                    "task_text": "-",
                    "mileage": "-",
                    "today_mileage": 0,
                    "message": f"初始化失败: {e}",
                }
            )
            continue
        account_instances.append(tclx)
        tasks.append(tclx.run())
    
    if tasks:
        await asyncio.gather(*tasks)

    for index, instance in enumerate(account_instances, 1):
        append_notify_result(instance.notify_result(index))


if __name__ == '__main__':
    asyncio.run(main())
    dispatch_notify()
