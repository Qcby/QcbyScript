#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WPS签到任务 v3.0.2（青龙多账号聚合推送版）

功能：
1. WPS任务中心：每日签到、自动完成任务、自动抽奖。
2. 天天领福利：每日签到、自动完成任务、自动抽奖。
3. WPS超级会员小程序：签到。
4. WPS挑战计划：自动完成任务、领取奖励。
5. 支持多账号执行，所有账号执行完成后统一聚合推送。

配置说明：
1. 账号变量：
   WPS_TASK_CK                                      必填，WPS cookie 账号变量
   - 格式：备注#cookie
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：账号1#uid=xxx; act_csrf_token=xxx&账号2#uid=yyy; act_csrf_token=yyy

2. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL                   本脚本当前不依赖，仅保留青龙同类脚本统一说明
   - 如后续接入微信 code，脚本会自动拼接 /mywc
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=目标AppID
   - 请求头：auth=账号标识

3. 推送变量：
   需要同目录存在 SendNotify.py，脚本结束后会统一调用 send_push_notification。
   常用推送变量如下，配置任意一种即可：
   QYWX_KEY                                         企业微信机器人 key
   PUSH_PLUS_TOKEN                                  PushPlus token
   PUSH_KEY                                         Server 酱 key
   DD_BOT_TOKEN 或 DD_BOT_SECRET                     钉钉机器人 token/secret
   FSKEY                                            飞书机器人 key

4. 青龙任务建议：
   名称：WPS签到任务
   命令：python3 WPS签到任务v3.0.2.py
   定时：0 0 7 * * *

原作者：Rex
更新时间：2026-04-12
"""
import os
import re
import sys
import json
import hmac
import time
import random
import hashlib
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Union, Tuple, List
import requests
import urllib3
from loguru import logger
from requests import Response

# 精简青龙日志输出：去掉 __main__:函数名:行号
logger.remove()
logger.add(
    sys.stdout,
    format='{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | {message}',
    colorize=False,
)

# ------------------------ 模块加载区 --------------------------
SCRIPT_TITLE = "WPS签到任务"
GLOBAL_NOTIFY_BUFFERS = []

current_script = os.path.abspath(__file__)
script_dir = os.path.dirname(current_script)
root_dir = os.path.dirname(script_dir)
for module_dir in (script_dir, root_dir):
    if module_dir and module_dir not in sys.path:
        sys.path.insert(0, module_dir)

try:
    from RnlProxy import RnlProxy
except Exception:
    RnlProxy = None
# ------------------------ 模块加载区 --------------------------


def mask_account(account: str) -> str:
    """账号脱敏，避免推送泄露完整备注。"""
    account = str(account or '').strip()
    if len(account) <= 2:
        return account or '未命名账号'
    if len(account) <= 6:
        return f"{account[0]}***{account[-1]}"
    return f"{account[:3]}***{account[-3:]}"


def append_notify_result(index: int, account: str, ok: bool, status: str, message: str = '') -> None:
    """收集单账号结果，禁止循环内逐账号推送。"""
    GLOBAL_NOTIFY_BUFFERS.append({
        'index': index,
        'account': mask_account(account),
        'ok': bool(ok),
        'status': status or ('success' if ok else 'failed'),
        'message': str(message or '').strip(),
    })


def build_notify_report() -> str:
    total = len(GLOBAL_NOTIFY_BUFFERS)
    success = sum(1 for item in GLOBAL_NOTIFY_BUFFERS if item.get('ok'))
    failed = total - success
    status_map = {
        'success': ('✅', '执行成功'),
        'failed': ('❌', '执行失败'),
        'config_error': ('⚙️', '配置错误'),
    }
    lines = [
        '==============================',
        f"🕒 执行时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f'📊 统计数据：成功 {success} / 总计 {total}',
        f'✅ 成功账号：{success} 个',
        f'❌ 失败账号：{failed} 个',
        '==============================',
    ]
    for item in GLOBAL_NOTIFY_BUFFERS:
        ok = bool(item.get('ok'))
        status_icon, status_text = status_map.get(item.get('status'), ('ℹ️', str(item.get('status') or '未知')))
        account_icon = '🧑‍💻' if ok else '🧟'
        message = item.get('message') or '无详细日志'
        lines.extend([
            f"{account_icon} 【账号{item.get('index')}】{item.get('account')}",
            f'{status_icon} 状态：{status_text}',
            '🧾 执行日志：',
            message,
            '------------------------------',
        ])
    return '\n'.join(lines)


def dispatch_notify() -> None:
    if not GLOBAL_NOTIFY_BUFFERS:
        return
    title = f'{SCRIPT_TITLE}执行结果'
    content = build_notify_report()
    try:
        from SendNotify import send_push_notification
        send_push_notification(title, content)
        logger.success('聚合推送已发送')
    except Exception as exc:
        logger.error(f'聚合推送失败：{exc}')
        print(f"\n{title}\n{content}")


class Utils:
    @staticmethod
    def r_sleep(s=1.0, e=None):
        """
        随机休眠函数（外部传秒，内部精确到毫秒）
        支持小数秒参数，内部自动转换为毫秒级随机值，保证休眠精度

        参数:
            s: 休眠时间下限（秒），支持小数，默认为1.0秒（1000毫秒）
            e: 休眠时间上限（秒），支持小数，默认为 s+1.0 秒（即原逻辑的「下限+1秒」）

        用法:
            r_sleep()          # 随机休眠1.0-2.0秒（1000-2000毫秒）
            r_sleep(3.5)       # 随机休眠3.5-4.5秒（3500-4500毫秒）
            r_sleep(2.2, 5.8)  # 随机休眠2.2-5.8秒（2200-5800毫秒）
            r_sleep(0.8, 1.5)  # 随机休眠0.8-1.5秒（800-1500毫秒）
            r_sleep(1.25, 3.75)# 随机休眠1.25-3.75秒（1250-3750毫秒）

        返回:
            float: 实际休眠的秒数（保留3位小数，对应毫秒级精度）
        """
        # 1. 类型校验与转换：确保参数为数字（支持int/float）
        try:
            s = float(s)
            e = float(e) if e is not None else None
        except (ValueError, TypeError):
            raise ValueError("参数 s/e 必须是可转换为浮点数的数字（秒）")

        # 2. 处理默认值：单参数时，上限 = 下限 + 1.0秒（保持原「+1秒」逻辑）
        if e is None:
            e = s + 1.0

        # 3. 边界修正：确保下限 ≤ 上限（自动交换，避免生成随机数失败）
        if s > e:
            s, e = e, s

        # 4. 额外防护：避免休眠时间为负数（秒数≥0）
        s = max(s, 0.0)
        e = max(e, 0.0)

        # 5. 核心转换：秒 → 毫秒（精确到1毫秒，转为整数计算）
        s_ms = int(round(s * 1000))  # 如 1.25秒 → 1250毫秒，0.8秒→800毫秒
        e_ms = int(round(e * 1000))  # 如 3.75秒 → 3750毫秒，5.8秒→5800毫秒

        # 6. 生成毫秒级随机数 → 转回秒（time.sleep接收秒为单位，保留3位小数）
        sleep_ms = random.randint(s_ms, e_ms)  # 精确到1毫秒的随机值
        sleep_sec = sleep_ms / 1000  # 如 1250毫秒 → 1.25秒

        # 7. 执行休眠
        time.sleep(sleep_sec)

        # 8. 返回实际休眠的秒数（保留3位小数，直观对应毫秒）
        return round(sleep_sec, 3)

    @staticmethod
    def dict_cookie_to_string(cookie_dict):
        """
        将字典形式的 cookie 转换为字符串
        :param cookie_dict: 包含 cookie 信息的字典
        :return: 转换后的 cookie 字符串
        """
        cookie_list = []
        for key, value in cookie_dict.items():
            cookie_list.append(f"{key}={value}")
        return "; ".join(cookie_list)

    @staticmethod
    def string_cookie_to_dict(cookie_str):
        """
        将 Cookie 字符串转换为字典
        :param cookie_str: 格式为 "key1=value1; key2=value2" 的 Cookie 字符串
        :return: 转换后的字典，格式为 {key1: value1, key2: value2}
        """
        cookie_dict = {}
        # 处理空字符串情况
        if not cookie_str:
            return cookie_dict

        # 按分号分隔 Cookie 键值对（处理可能的空格，如 "key=val; key2=val2"）
        cookie_pairs = [pair.strip() for pair in cookie_str.split(';') if pair.strip()]

        for pair in cookie_pairs:
            # 按第一个等号分割（兼容值中包含等号的情况，如 "token=abc=123"）
            key_value = pair.split('=', 1)
            if len(key_value) == 2:
                key, value = key_value
                cookie_dict[key.strip()] = value.strip()
            else:
                # 处理异常格式（如仅有 key 无 value，如 "isLogin"）
                cookie_dict[key_value[0].strip()] = ""

        return cookie_dict


class RnlRequest:
    def __init__(self, proxies=None, cookies=None, headers=None):
        """ 20251012 """
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        self.session = requests.Session()
        self.session.trust_env = False
        self.session.verify = False
        self.last_response: Optional[Response] = None  # 存储最近一次响应

        if proxies:
            self.session.proxies.update(proxies)

        # 基础请求头，默认带常见浏览器UA
        self._base_headers = headers or {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36',
        }

        self.update_cookies(cookies)

    @property
    def status_code(self) -> Optional[int]:
        """快捷获取状态码（同requests.Response.status_code）"""
        return self.last_response.status_code if self.last_response else None

    @property
    def ok(self) -> bool:
        """判断请求是否成功（状态码2xx），同requests.Response.ok"""
        return 200 <= self.status_code < 300 if self.status_code else False

    @property
    def json(self) -> Any:
        """快捷获取JSON数据（自动处理解析异常）"""
        if not self.last_response:
            return None
        try:
            return self.last_response.json()
        except (ValueError, TypeError):
            return None  # 解析失败返回None

    @property
    def text(self) -> Optional[str]:
        """快捷获取文本内容"""
        return self.last_response.text if self.last_response else None

    @property
    def content(self) -> Optional[bytes]:
        """快捷获取二进制内容"""
        return self.last_response.content if self.last_response else None

    @property
    def headers(self) -> Optional[Dict[str, str]]:
        """快捷获取响应头"""
        return dict(self.last_response.headers) if self.last_response else None


    def update_cookies(self, cookies: Union[str, dict, None]) -> None:
        """更新Cookie（支持字符串/字典）"""
        if not cookies:
            return
        if isinstance(cookies, str):
            cookies = dict(
                item.strip().split('=', 1)
                for item in cookies.split(';')
                if '=' in item.strip()
            )
        elif not isinstance(cookies, dict):
            return
        self.session.cookies.update(cookies)

    def get_cookies(self) -> Dict[str, str]:
        """获取当前会话的Cookie（字典形式）"""
        return self.session.cookies.get_dict()

    def update_headers(self, headers: Dict[str, str]) -> None:
        """更新基础请求头（会与原有头合并，新值覆盖旧值）"""
        self._base_headers.update(headers)

    def raise_for_status(self) -> None:
        """若请求失败（非2xx），主动抛出异常（同requests.Response.raise_for_status）"""
        if self.last_response:
            self.last_response.raise_for_status()

    def request(
            self,
            method: str,
            url: str,
            params: Optional[Union[Dict[str, Any], bytes]] = None,
            data: Optional[Union[Dict[str, Any], str, bytes, List[Tuple[str, Any]]]] = None,
            json: Optional[Any] = None,
            headers: Optional[Dict[str, str]] = None,
            cookies: Optional[Union[Dict[str, str]]] = None,
            files: Optional[Union[Dict[str, Any], List[Tuple[str, Any]]]] = None,
            auth: Optional[Union[Tuple[str, str]]] = None,
            timeout: Optional[Union[float, Tuple[float, float]]] = None,
            allow_redirects: bool = True,
            proxies: Optional[Dict[str, str]] = None,
            hooks: Optional[Dict[str, Any]] = None,
            stream: Optional[bool] = None,
            verify: Optional[Union[bool, str]] = None,
            cert: Optional[Union[str, Tuple[str, str]]] = None, **kwargs
    ) -> Optional[Response]:
        """发送请求，参数与原生requests保持一致"""
        self.last_response = None
        # 合并基础头和请求头（请求头优先级更高）
        request_headers = {**self._base_headers, **(headers or {})}

        try:
            resp = self.session.request(
                method=method.upper(),
                url=url,
                params=params,
                data=data,
                json=json,
                headers=request_headers,
                cookies=cookies,
                files=files,
                auth=auth,
                timeout=timeout,
                allow_redirects=allow_redirects,
                proxies=proxies,
                hooks=hooks,
                stream=stream,
                verify=verify if verify is not None else self.session.verify,
                cert=cert,
                **kwargs
            )
            self.last_response = resp
            return resp
        except requests.RequestException as e:
            if hasattr(e, 'response') and e.response:
                self.last_response = e.response
                return e.response
            return None

    def get(
            self,
            url: str,
            params: Optional[Union[Dict[str, Any], bytes]] = None,
            data: Optional[Union[Dict[str, Any], str, bytes, List[Tuple[str, Any]]]] = None,
            json: Optional[Any] = None,
            headers: Optional[Dict[str, str]] = None,
            cookies: Optional[Union[Dict[str, str]]] = None,
            files: Optional[Union[Dict[str, Any], List[Tuple[str, Any]]]] = None,
            auth: Optional[Union[Tuple[str, str]]] = None,
            timeout: Optional[Union[float, Tuple[float, float]]] = None,
            allow_redirects: bool = True,
            proxies: Optional[Dict[str, str]] = None,
            hooks: Optional[Dict[str, Any]] = None,
            stream: Optional[bool] = None,
            verify: Optional[Union[bool, str]] = None,
            cert: Optional[Union[str, Tuple[str, str]]] = None, **kwargs
    ) -> Optional[Response]:
        return self.request(
            method='GET',
            url=url,
            params=params,
            data=data,
            json=json,
            headers=headers,
            cookies=cookies,
            files=files,
            auth=auth,
            timeout=timeout,
            allow_redirects=allow_redirects,
            proxies=proxies,
            hooks=hooks,
            stream=stream,
            verify=verify,
            cert=cert,
            **kwargs
        )

    def post(
            self,
            url: str,
            data: Optional[Union[Dict[str, Any], str, bytes, List[Tuple[str, Any]]]] = None,
            json: Optional[Any] = None,
            params: Optional[Union[Dict[str, Any], bytes]] = None,
            headers: Optional[Dict[str, str]] = None,
            cookies: Optional[Union[Dict[str, str]]] = None,
            files: Optional[Union[Dict[str, Any], List[Tuple[str, Any]]]] = None,
            auth: Optional[Union[Tuple[str, str]]] = None,
            timeout: Optional[Union[float, Tuple[float, float]]] = None,
            allow_redirects: bool = True,
            proxies: Optional[Dict[str, str]] = None,
            hooks: Optional[Dict[str, Any]] = None,
            stream: Optional[bool] = None,
            verify: Optional[Union[bool, str]] = None,
            cert: Optional[Union[str, Tuple[str, str]]] = None, **kwargs
    ) -> Optional[Response]:
        return self.request(
            method='POST',
            url=url,
            params=params,
            data=data,
            json=json,
            headers=headers,
            cookies=cookies,
            files=files,
            auth=auth,
            timeout=timeout,
            allow_redirects=allow_redirects,
            proxies=proxies,
            hooks=hooks,
            stream=stream,
            verify=verify,
            cert=cert,
            **kwargs
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.session.close()



def generate_sign(t: dict, i: str, r: str) -> str:
    """
    还原JS逻辑的Python实现
    :param t: 原始字典（对应JS的t/e）
    :param i: 固定前缀字符串（对应JS的i）
    :param r: HmacSHA256的密钥（对应JS的r）
    :return: 最终的HmacSHA256签名结果（对应JS的u）
    """
    # 1. 对应JS：Object.keys(e).sort() + reduce重构对象
    # 提取键并排序，按排序后的键重构字典
    sorted_keys = sorted(t.keys())
    n = {key: t[key] for key in sorted_keys}

    # 2. 对应JS：JSON.stringify(n) → 得到p
    # 注意：JSON序列化参数要和JS默认行为一致（无空格、中文不转义）
    p = json.dumps(n, ensure_ascii=False, separators=(',', ':'))

    # 3. 计算MD5(p) → 对应JS的l().MD5(p).toString()
    md5_p = hashlib.md5(p.encode('utf-8')).hexdigest()

    # 4. 获取当前UTC时间字符串 → 对应JS的(new Date).toUTCString()
    # 匹配JS toUTCString()的格式（如：Wed, 04 Feb 2026 08:00:00 GMT）
    utc_time = datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S GMT')

    # 5. 拼接f → 对应JS的f = i + MD5(p) + UTC时间
    f = i + md5_p + utc_time

    # 6. 计算HmacSHA256(f, r) → 对应JS的l().HmacSHA256(f, r).toString()
    # 注意：密钥和字符串都要先编码为bytes
    hmac_obj = hmac.new(r.encode('utf-8'), f.encode('utf-8'), hashlib.sha256)
    u = hmac_obj.hexdigest()

    return u

class RNL:
    def __init__(self, c, proxies=None):
        if isinstance(c, str):
            new_c = Utils.string_cookie_to_dict(c)
        else:
            new_c = c
        self.act_csrf_token = new_c.get('act_csrf_token')
        self.user_id = new_c.get('uid')
        if not self.act_csrf_token or not self.user_id:
            logger.error(f'[用户{self.user_id or "未知"}] cookie参数不全（缺少act_csrf_token或uid）')
            raise ValueError('cookie参数不全（缺少act_csrf_token或uid）')
        self.user_id = int(new_c.get('uid'))
        self.userAgent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0'
        self.rr = RnlRequest(proxies=proxies, cookies=new_c, headers={'User-Agent': self.userAgent})
        self.de = {
            'PROMOTIONAL_MATERIAL': "task_center.promotional_material",
            'START_TASK': "task_center.start",
            'FINISH': "task_center.finish",
            'TOKEN_FINISH': "task_center.token_finish",
            'REWARD': "task_center.reward"
        }
        self.operation_logs = []
        self.global_config = {
            'task_center': True,  # WPS任务中心
            'fragment_collect': False,  # 办公助手 2026.03.16结束
            'lottery3': True,  # 天天领福利
            'svip_applet': True,  # 超级会员applet
            'challenge': True,  # WPS挑战计划
        }

    # WPS任务中心-获取签到key
    def get_public_key(self):
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'origin': 'https://personal-act.wps.cn',
            'priority': 'u=1, i',
            'referer': 'https://personal-act.wps.cn/',
            'sec-ch-ua': '"Chromium";v="134", "Not:A-Brand";v="24", "Microsoft Edge";v="134"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
        }
        response = self.rr.get('https://personal-bus.wps.cn/sign_in/v1/encrypt/key', headers=headers)
        json_data = response.json()
        if json_data['code'] == 1000000:
            logger.success(f'[用户{self.user_id}] 获取加密密钥成功')
            return json_data['data']
        logger.error(f'[用户{self.user_id}] 获取加密密钥失败：{json_data["msg"]}')
        return None
    # WPS任务中心-签到
    def sign_in(self, encryptData):
        data = {
            'encryptData': encryptData,
            'userId': self.user_id,
        }
        resp = self.rr.post('https://py.leishennb.icu/v1/rnl-2-gather/get-wps-publickey', json=data).json()
        params_obj = resp['data']
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'content-type': 'application/json',
            'origin': 'https://personal-act.wps.cn',
            'priority': 'u=1, i',
            'referer': 'https://personal-act.wps.cn/',
            'sec-ch-ua': '"Chromium";v="134", "Not:A-Brand";v="24", "Microsoft Edge";v="134"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-site',
            'token': params_obj['token'],
        }
        json_data = params_obj['data']
        response = self.rr.post('https://personal-bus.wps.cn/sign_in/v1/sign_in', headers=headers,
                                 json=json_data)
        json_data = response.json()
        if json_data.get('code') == 1000000:
            rewards = json_data['data']['rewards'][0]
            sign_msg = f"任务中心签到成功：{rewards['reward_name']}"
            logger.success(f'[用户{self.user_id}] {sign_msg}')
            self.operation_logs.append(sign_msg)
            return True
        if 'has sign' in json_data.get('msg'):
            sign_msg = '今天已签到'
            logger.info(f'[用户{self.user_id}] {sign_msg}')
            self.operation_logs.append(sign_msg)
            return True
        sign_msg = f'任务中心签到失败：{json_data.get("msg", "未知错误")}'
        logger.error(f'[用户{self.user_id}] {sign_msg}')
        self.operation_logs.append(sign_msg)
        return None
    # WPS任务中心-通用完成任务
    def common_component_action(self, task_id, title, component_action=None):
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'content-type': 'application/json',
            'origin': 'https://personal-act.wps.cn',
            'priority': 'u=1, i',
            'referer': 'https://personal-act.wps.cn/rubik2/portal/HD2025031821201822/YM2025040908558269?cs_from=web_vipcenter_banner_inpublic&mk_key=4b9deqIfqNO3KCZrgH17WPH1kdzMoKUEvya&position=pc_aty_ban3_kaixue_test_b',
            'sec-ch-ua': '"Chromium";v="134", "Not:A-Brand";v="24", "Microsoft Edge";v="134"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'user-agent': self.userAgent,
            'x-act-csrf-token': self.act_csrf_token,
        }
        json_data = {
            'component_uniq_number': {
                'activity_number': 'HD2025031821201822',
                'page_number': 'YM2025040908558269',
                'component_number': 'ZJ2025040709458367',
                'component_node_id': 'FN1744160180RthG',
                'filter_params': {
                    'cs_from': 'web_vipcenter_banner_inpublic',
                    'mk_key': '4b9deqIfqNO3KCZrgH17WPH1kdzMoKUEvya',
                    'position': 'pc_aty_ban3_kaixue_test_b',
                },
            },
            'component_type': 35,
            'component_action': component_action or self.de['FINISH'],
            'task_center': {
                'task_id': task_id,
            },
        }
        try:
            response = self.rr.post(
                'https://personal-act.wps.cn/activity-rubik/activity/component_action',
                headers=headers,
                json=json_data,
            ).json()
            if response['result'] == 'ok':
                task_center = response['data']['task_center']
                if task_center['success']:
                    task_msg = f'完成任务 [{title}] 成功'
                    logger.success(f'[用户{self.user_id}] {task_msg}')
                    # self.operation_logs.append(task_msg)
                    return task_center.get('token') or True
                task_msg = f'完成任务 [{title}] 失败：{task_center["reason"]}'
                logger.error(f'[用户{self.user_id}] {task_msg}')
                self.operation_logs.append(task_msg)
                return False
            task_msg = f'完成任务 [{title}] 失败：{response}'
            logger.error(f'[用户{self.user_id}] {task_msg}')
            self.operation_logs.append(task_msg)
            return False
        except Exception as e:
            task_msg = f'完成任务 [{title}] 异常：{str(e)}'
            logger.error(f'[用户{self.user_id}] {task_msg}')
            self.operation_logs.append(task_msg)
            return False

    # WPS任务中心-通用领取奖励
    def common_reward_component_action(self, task_id, title):
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'content-type': 'application/json',
            'origin': 'https://personal-act.wps.cn',
            'priority': 'u=1, i',
            'referer': 'https://personal-act.wps.cn/rubik2/portal/HD2025031821201822/YM2025040908558269?cs_from=web_vipcenter_banner_inpublic&mk_key=4b9deqIfqNO3KCZrgH17WPH1kdzMoKUEvya&position=pc_aty_ban3_kaixue_test_b',
            'sec-ch-ua': '"Chromium";v="134", "Not:A-Brand";v="24", "Microsoft Edge";v="134"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'x-act-csrf-token': self.act_csrf_token,
        }
        json_data = {
            'component_uniq_number': {
                'activity_number': 'HD2025031821201822',
                'page_number': 'YM2025040908558269',
                'component_number': 'ZJ2025040709458367',
                'component_node_id': 'FN1744160180RthG',
                'filter_params': {
                    'cs_from': 'web_vipcenter_banner_inpublic',
                    'mk_key': '4b9deqIfqNO3KCZrgH17WPH1kdzMoKUEvya',
                    'position': 'pc_aty_ban3_kaixue_test_b',
                },
            },
            'component_type': 35,
            'component_action': self.de['REWARD'],
            'task_center': {
                'task_id': task_id,
            },
        }
        try:
            response = self.rr.post(
                'https://personal-act.wps.cn/activity-rubik/activity/component_action',
                headers=headers,
                json=json_data,
            ).json()
            if response['result'] == 'ok':
                task_center = response['data']['task_center']
                if task_center['success']:
                    reward_msg = f'领取 [{title}] 奖励成功'
                    logger.success(f'[用户{self.user_id}] {reward_msg}')
                    self.operation_logs.append(reward_msg)
                    return True
                reward_msg = f'领取 [{title}] 奖励失败：{task_center["reason"]}'
                logger.error(f'[用户{self.user_id}] {reward_msg}')
                self.operation_logs.append(reward_msg)
                return False
            reward_msg = f'领取 [{title}] 奖励失败：{response}'
            logger.error(f'[用户{self.user_id}] {reward_msg}')
            self.operation_logs.append(reward_msg)
            return False
        except Exception as e:
            reward_msg = f'领取 [{title}] 奖励异常：{str(e)}'
            logger.error(f'[用户{self.user_id}] {reward_msg}')
            self.operation_logs.append(reward_msg)
            return False
    # WPS任务中心-任务详情
    def task_info(self, token):
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'priority': 'u=1, i',
            'referer': 'https://personal-act.wps.cn/rubik2/portal/HD2025091109421588/YM2025091121369865?cs_from=android_ucsty_rwzx&positon=ad_rwzx_task',
            'sec-ch-ua': '"Chromium";v="134", "Not:A-Brand";v="24", "Microsoft Edge";v="134"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'user-agent': self.userAgent,
        }
        start_time = int(time.time()*1000)
        params = {
            'batch_tag': start_time,
            'token': token,
        }
        try:
            response = self.rr.get(
                'https://personal-act.wps.cn/activity-rubik/user/task_center/task_info',
                params=params,
                headers=headers,
            ).json()
            if response.get('result') == 'ok':
                return start_time + response['data']['start_at']
            logger.error(response)
            return None
        except Exception as e:
            logger.error(str(e))
            return None
    # WPS任务中心-抽奖handle
    def lottery_process(self, task_list):
        if not task_list:
            return
        for task in task_list:
            task_id = task['task_id']
            title = task['title']
            task_status = task['task_status']
            if task_status == 2:
                logger.info(f'[用户{self.user_id}] 任务 [{title}] 已完成')
                continue
            if '浏览' in title:
                token = self.common_component_action(task_id=task_id, title=title,
                                                     component_action=self.de['START_TASK'])
                if token:
                    batch_tag = self.task_info(token=token)
                    if not batch_tag:
                        logger.error('获取浏览任务信息失败，跳过')
                        continue
                    Utils.r_sleep(10, 11)
                    is_done1 = self.task_finish(token=token, title=title, batch_tag=batch_tag)
                    if is_done1:
                        Utils.r_sleep(1)
                        self.common_reward_component_action(task_id=task_id, title=title)
                    Utils.r_sleep(1)
                continue
            skip_keywords = ['认证', '上喜马拉雅', '消费', '邀请', '微博', '苏宁易购', '开通会员']
            if any(keyword in title for keyword in skip_keywords):
                logger.info(f'[用户{self.user_id}] 跳过任务 [{title}]')
                continue
            is_done = self.common_component_action(task_id=task_id, title=title)
            if is_done:
                Utils.r_sleep(1)
                self.common_reward_component_action(task_id=task_id, title=title)
            Utils.r_sleep(1)

    # 天天领福利-任务完成 浏览任务
    def task_finish(self, token, title, batch_tag):
        headers = {
            'User-Agent': self.userAgent,
            'Accept': 'application/json, text/plain, */*',
            # 'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Content-Type': 'application/json',
            'sec-ch-ua-platform': '"Windows"',
            'sec-ch-ua': '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
            'sec-ch-ua-mobile': '?0',
            'origin': 'https://personal-act.wps.cn',
            'sec-fetch-site': 'same-origin',
            'sec-fetch-mode': 'cors',
            'sec-fetch-dest': 'empty',
            'referer': 'https://personal-act.wps.cn/rubik2/portal/HD2025031721339450/YM2025031721331326?cs_from=ad_ucsty_rwzx&position=ad_ucsty_rwzx',
            'accept-language': 'zh-CN,zh;q=0.9',
            'priority': 'u=1, i',
        }
        json_data = {
            'batch_tag': batch_tag,
            'token': token,
        }
        try:
            response = self.rr.post(
                'https://personal-act.wps.cn/activity-rubik/user/task_center/task_finish',
                headers=headers,
                json=json_data,
            ).json()
            if response.get('result') == 'ok':
                reward_msg = f'完成任务 [{title}] 成功'
                logger.success(f'[用户{self.user_id}] {reward_msg}')
                self.operation_logs.append(reward_msg)
                return True
            reward_msg = f'任务 {title} 完成失败：{response}'
            logger.error(f'[用户{self.user_id}] {reward_msg}')
            self.operation_logs.append(reward_msg)
            return False
        except Exception as e:
            reward_msg = f'任务 {title} 完成失败：{str(e)}'
            logger.error(f'[用户{self.user_id}] {reward_msg}')
            self.operation_logs.append(reward_msg)
            return False
    # 天天领福利-签到
    def fragment_collect_sign_in(self):
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9',
            'cache-control': 'no-cache',
            'content-type': 'application/json',
            'origin': 'https://personal-act.wps.cn',
            'pragma': 'no-cache',
            'priority': 'u=1, i',
            'referer': 'https://personal-act.wps.cn/rubik2/portal/HD2025031721339450/YM2025031721331326?cs_from=ad_ucsty_rwzx&position=ad_ucsty_rwzx',
            'sec-ch-ua': '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'x-act-csrf-token': self.act_csrf_token,
        }
        sign_date = datetime.now().strftime("%Y-%m-%d")
        json_data = {
            'component_uniq_number': {
                'activity_number': 'HD2025031721339450',
                'page_number': 'YM2025031721331326',
                'component_number': 'ZJ2025061815363325',
                'component_node_id': 'FN1750234948dBVL',
                'filter_params': {
                    'cs_from': 'ad_ucsty_rwzx',
                    'position': 'ad_ucsty_rwzx',
                },
            },
            'component_type': 42,
            'component_action': 'fragment_collect.sign_in',
            'fragment_collect': {
                'sign_date': sign_date,
                'series_id': '',
                'is_new_sign_series': True,
            },
        }
        try:
            response = self.rr.post(
                'https://personal-act.wps.cn/activity-rubik/activity/component_action',
                headers=headers,
                json=json_data,
            )
            json_data = response.json()
            if json_data.get('result') == 'ok':
                success = json_data['data']['fragment_collect']['success']
                rewards = json_data['data']['fragment_collect']['reason']
                if success:
                    sign_msg = f"天天领福利签到成功：{rewards}"
                    logger.success(f'[用户{self.user_id}] {sign_msg}')
                    self.operation_logs.append(sign_msg)
                    return True
                sign_msg = f"天天领福利签到失败：{rewards}"
                logger.error(f'[用户{self.user_id}] {sign_msg}')
                self.operation_logs.append(sign_msg)
                return False
            _msg = json_data.get('msg')
            if 'Duplicate entry' in _msg:
                sign_msg = f"天天领福利-今日已签到"
            else:
                sign_msg = f"天天领福利签到失败：{_msg}"
            logger.error(f'[用户{self.user_id}] {sign_msg}')
            self.operation_logs.append(sign_msg)
            return False
        except Exception as e:
            logger.error(str(e))
            sign_msg = f"天天领福利签到失败：{str(e)}"
            self.operation_logs.append(sign_msg)
            return False
    # 天天领福利-抽奖
    def lottery_v22(self):
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9',
            'cache-control': 'no-cache',
            'content-type': 'application/json',
            'origin': 'https://personal-act.wps.cn',
            'pragma': 'no-cache',
            'priority': 'u=1, i',
            'referer': 'https://personal-act.wps.cn/rubik2/portal/HD2025031721339450/YM2025031721331326?cs_from=ad_ucsty_rwzx&position=ad_ucsty_rwzx',
            'sec-ch-ua': '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'x-act-csrf-token': self.act_csrf_token,
        }
        json_data = {
            'component_uniq_number': {
                'activity_number': 'HD2025031721339450',
                'page_number': 'YM2025031721331326',
                'component_number': 'ZJ2025092916515917',
                'component_node_id': 'FN1761875116m2x8',
                'filter_params': {
                    'cs_from': 'ad_ucsty_rwzx',
                    'position': 'ad_ucsty_rwzx',
                },
            },
            'component_type': 45,
            'component_action': 'lottery_v2.exec',
            'lottery_v2': {
                'session_id': 3001,
            },
        }
        try:
            response = self.rr.post(
                'https://personal-act.wps.cn/activity-rubik/activity/component_action',
                headers=headers,
                json=json_data,
            )
            json_data = response.json()
            if json_data['result'] == 'ok':
                reward_name = json_data['data']['lottery_v2']['reward_name']
                lottery_msg = f"天天领福利抽奖成功：{reward_name}"
                logger.success(f'[用户{self.user_id}] {lottery_msg}')
                self.operation_logs.append(lottery_msg)
                return True
            lottery_msg = f'天天领福利抽奖失败：{json_data.get("msg", "未知错误")}'
            logger.error(f'[用户{self.user_id}] {lottery_msg}')
            self.operation_logs.append(lottery_msg)
            return None
        except Exception as e:
            lottery_msg = f'天天领福利抽奖异常：{str(e)}'
            logger.error(f'[用户{self.user_id}] {lottery_msg}')
            self.operation_logs.append(lottery_msg)
            return False

    # WPS任务中心-抽奖
    def lottery_v2(self):
        headers = {
            'sec-ch-ua-platform': '"Windows"',
            'Referer': 'https://personal-act.wps.cn/rubik2/portal/HD2025031821201822/YM2025040908558269?cs_from=web_vipcenter_banner_inpublic&mk_key=4b9deqIfqNO3KCZrgH17WPH1kdzMoKUEvya&position=pc_aty_ban3_kaixue_test_b',
            'sec-ch-ua': '"Chromium";v="134", "Not:A-Brand";v="24", "Microsoft Edge";v="134"',
            'sec-ch-ua-mobile': '?0',
            'User-Agent': self.userAgent,
            'Accept': 'application/json, text/plain, */*',
            'Content-Type': 'application/json',
            'X-Act-Csrf-Token': self.act_csrf_token,
        }
        json_data = {
            'component_uniq_number': {
                'activity_number': 'HD2025031821201822',
                'page_number': 'YM2025040908558269',
                'component_number': 'ZJ2025092916516585',
                'component_node_id': 'FN1762345949vdR1',
                'filter_params': {
                    'cs_from': 'web_vipcenter_banner_inpublic',
                    'mk_key': '4b9deqIfqNO3KCZrgH17WPH1kdzMoKUEvya',
                    'position': 'pc_aty_ban3_kaixue_test_b',
                },
            },
            'component_type': 45,
            'component_action': 'lottery_v2.exec',
            'lottery_v2': {
                'session_id': 2,
            },
        }
        try:
            response = self.rr.post('https://personal-act.wps.cn/activity-rubik/activity/component_action',
                                     headers=headers, json=json_data)
            json_data = response.json()
            if json_data['result'] == 'ok':
                reward_name = json_data['data']['lottery_v2']['reward_name']
                lottery_msg = f"抽奖成功：{reward_name}"
                logger.success(f'[用户{self.user_id}] {lottery_msg}')
                self.operation_logs.append(lottery_msg)
                return True
            lottery_msg = f'抽奖失败：{json_data.get("msg", "未知错误")}'
            logger.error(f'[用户{self.user_id}] {lottery_msg}')
            self.operation_logs.append(lottery_msg)
            return None
        except Exception as e:
            lottery_msg = f'抽奖异常：{str(e)}'
            logger.error(f'[用户{self.user_id}] {lottery_msg}')
            self.operation_logs.append(lottery_msg)
            return False

    # WPS任务中心-活动信息
    # https://personal-act.wps.cn/rubik2/portal/HD2025031821201822/YM2025040908558269?cs_from=web_vipcenter_banner_inpublic&mk_key=4b9deqIfqNO3KCZrgH17WPH1kdzMoKUEvya&position=pc_aty_ban3_kaixue_test_b
    def page_info(self):
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
            'priority': 'u=1, i',
            'referer': 'https://personal-act.wps.cn/rubik2/portal/HD2025031821201822/YM2025040908558269?cs_from=web_vipcenter_banner_inpublic&mk_key=4b9deqIfqNO3KCZrgH17WPH1kdzMoKUEvya&position=pc_aty_ban3_kaixue_test_b',
            'sec-ch-ua': '"Chromium";v="134", "Not:A-Brand";v="24", "Microsoft Edge";v="134"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'user-agent': self.userAgent,
        }
        try:
            # page_number=YM2025031821202008
            response = self.rr.get(
                'https://personal-act.wps.cn/activity-rubik/activity/page_info?activity_number=HD2025031821201822&page_number=YM2025040908558269&filter_params=%7B%22cs_from%22:%22web_vipcenter_banner_inpublic%22,%22mk_key%22:%224b9deqIfqNO3KCZrgH17WPH1kdzMoKUEvya%22,%22position%22:%22pc_aty_ban3_kaixue_test_b%22%7D',
                headers=headers,
            ).json()
            if response['result'] == 'ok':
                lottery_times = None
                user_integral = None
                task_list = None

                for item in response["data"]:
                    if lottery_times is None:
                        if item.get("type") == 45 and item.get("lottery_v2"):
                            for session in item["lottery_v2"].get("lottery_list", []):
                                if session.get("session_id") == 2:
                                    lottery_times = session.get("times")
                                    continue
                    if user_integral is None:
                        if item.get("task_center_user_info"):
                            user_integral = item["task_center_user_info"].get("integral")
                        elif item.get("integral_waterfall"):
                            user_integral = item["integral_waterfall"].get("user_integral")
                    if task_list is None:
                        if item.get("task_center"):
                            task_list = item["task_center"].get("task_list")
                    if lottery_times and user_integral and task_list:
                        break
                logger.info(f'[用户{self.user_id}] 积分：{user_integral}，抽奖次数：{lottery_times}')
                return {
                    "lottery_times": lottery_times,
                    "user_integral": user_integral,
                    "task_list": task_list
                }
        except Exception as e:
            logger.error(f'[用户{self.user_id}] 获取用户信息异常：{str(e)}')
            return None
    # 办公助手-活动信息
    # https://personal-act.wps.cn/rubik2/portal/HD2025031010408781/YM2025061216463517?cs_from=xinchao_activity_lottery&position=xinchao_bgzs_autoreply_2148_cj
    def page_info2(self):
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9',
            'cache-control': 'no-cache',
            'pragma': 'no-cache',
            'priority': 'u=1, i',
            'referer': 'https://personal-act.wps.cn/rubik2/portal/HD2025031010408781/YM2025061216463517?cs_from=xinchao_activity_lottery&position=xinchao_bgzs_autoreply_2148_cj',
            'sec-ch-ua': '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
        }
        try:
            response = self.rr.get(
                'https://personal-act.wps.cn/activity-rubik/activity/page_info?activity_number=HD2025031010408781&page_number=YM2025061216463517&filter_params=%7B%22cs_from%22:%22xinchao_activity_lottery%22,%22position%22:%22xinchao_bgzs_autoreply_2148_cj%22%7D',
                headers=headers,
            ).json()
            task_list = None
            lottery_times = None
            # 遍历data数组，定位包含task_center的元素
            for item in response["data"]:
                if "task_center" in item and "task_list" in item["task_center"]:
                    task_list = item["task_center"]["task_list"]
                if "lottery" in item and item["lottery"]:
                    lottery = item["lottery"]
                    if "rewards" in lottery and isinstance(lottery["rewards"], list) and len(lottery["rewards"]) > 0:
                        first_reward = lottery["rewards"][0]
                        if "times" in first_reward:
                            lottery_times = first_reward["times"]
                if lottery_times != None and task_list != None:
                    break
            return {
                'task_list': task_list,
                'lottery_times': lottery_times
            }
        except Exception as e:
            logger.error(str(e))
            return None
    # 福利中心-活动信息
    def page_info3(self):
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9',
            'cache-control': 'no-cache',
            'pragma': 'no-cache',
            'priority': 'u=1, i',
            'referer': 'https://personal-act.wps.cn/rubik2/portal/HD2025031721339450/YM2025031721331326?cs_from=ad_ucsty_rwzx&position=ad_ucsty_rwzx',
            'sec-ch-ua': '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
        }
        try:
            response = self.rr.get(
                'https://personal-act.wps.cn/activity-rubik/activity/page_info?activity_number=HD2025031721339450&page_number=YM2025031721331326&filter_params=%7B%22cs_from%22:%22ad_ucsty_rwzx%22,%22position%22:%22ad_ucsty_rwzx%22%7D',
                headers=headers,
            ).json()
            if response['result'] == 'ok':
                lottery_times = None
                for item in response["data"]:
                    if item.get("lottery_v2"):
                        for session in item["lottery_v2"].get("lottery_list", []):
                            if session.get("times"):
                                lottery_times = session.get("times")
                                break
                return {
                    "lottery_times": lottery_times,
                }
        except Exception as e:
            logger.error(f'[用户{self.user_id}] 获取用户信息异常：{str(e)}')
            return None

    # 办公助手-领取
    def components_action2(self, task_id, title):
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9',
            'cache-control': 'no-cache',
            'content-type': 'application/json',
            'origin': 'https://personal-act.wps.cn',
            'pragma': 'no-cache',
            'priority': 'u=1, i',
            'referer': 'https://personal-act.wps.cn/rubik2/portal/HD2025031010408781/YM2025061216463517?cs_from=xinchao_activity_lottery&position=xinchao_bgzs_autoreply_2148_cj',
            'sec-ch-ua': '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
        }
        json_data = {
            'component_uniq_number': {
                'activity_number': 'HD2025031010408781',
                'page_number': 'YM2025061216463517',
                'component_number': 'ZJ2024083022083755',
                'component_node_id': 'FN1740387182DaYX',
            },
            'component_type': 14,
            'component_action': self.de['REWARD'],
            'task_center': {
                'task_id': task_id,
            },
        }
        try:
            response = self.rr.post(
                'https://personal-act.wps.cn/activity-rubik/activity/component_action',
                headers=headers,
                json=json_data,
            ).json()
            if response['result'] == 'ok':
                task_center = response['data']['task_center']
                if task_center['success']:
                    reward_msg = f'领取 [{title}] 奖励成功'
                    logger.success(f'[用户{self.user_id}] {reward_msg}')
                    self.operation_logs.append(reward_msg)
                    return True
                reward_msg = f'领取 [{title}] 奖励失败：{task_center["reason"]}'
                logger.error(f'[用户{self.user_id}] {reward_msg}')
                self.operation_logs.append(reward_msg)
                return False
            reward_msg = f'领取 [{title}] 奖励失败：{response}'
            logger.error(f'[用户{self.user_id}] {reward_msg}')
            self.operation_logs.append(reward_msg)
            return False

        except Exception as e:
            reward_msg = f'领取 [{title}] 奖励异常：{str(e)}'
            logger.error(f'[用户{self.user_id}] {reward_msg}')
            self.operation_logs.append(reward_msg)
            return False
    # 办公助手-抽奖
    def lottery_exec(self):
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9',
            'cache-control': 'no-cache',
            'content-type': 'application/json',
            'origin': 'https://personal-act.wps.cn',
            'pragma': 'no-cache',
            'priority': 'u=1, i',
            'referer': 'https://personal-act.wps.cn/rubik2/portal/HD2025031010408781/YM2025061216463517?cs_from=xinchao_activity_lottery&position=xinchao_bgzs_autoreply_2148_cj',
            'sec-ch-ua': '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
        }
        json_data = {
            'component_uniq_number': {
                'activity_number': 'HD2025031010408781',
                'page_number': 'YM2025061216463517',
                'component_number': 'ZJ2024083022081230',
                'component_node_id': 'FN1741940010rC4c',
            },
            'component_type': 2,
            'component_action': 'lottery.exec',
            'lottery': {
                'pay_source': '',
                'integral_source': '',
                'position': 'bgzs_tasks_cj',
                'source': '',
                'ids': '1115,1119,1116,1117,1120,1121,1122,1118',
                'sign': '',
            },
        }
        try:
            response = self.rr.post(
                'https://personal-act.wps.cn/activity-rubik/activity/component_action',
                headers=headers,
                json=json_data,
            )
            json_data = response.json()
            if json_data['result'] == 'ok':
                reward_name = json_data['data']['lottery']['name']
                lottery_msg = f"抽奖成功：{reward_name}"
                logger.success(f'[用户{self.user_id}] {lottery_msg}')
                self.operation_logs.append(lottery_msg)
                return True
            lottery_msg = f'抽奖失败：{json_data.get("msg", "未知错误")}'
            logger.error(f'[用户{self.user_id}] {lottery_msg}')
            self.operation_logs.append(lottery_msg)
            return None
        except Exception as e:
            lottery_msg = f'抽奖异常：{str(e)}'
            logger.error(f'[用户{self.user_id}] {lottery_msg}')
            self.operation_logs.append(lottery_msg)
            return False
    # 办公助手-执行
    def done_task2_handler(self, task_list2, first_task=False):
        for t2 in task_list2:
            task_id = t2["task_id"]
            title = t2["title"]
            task_status = t2['task_status']
            if task_status == 1:
                logger.info(f'[用户{self.user_id}] 任务 [{title}] 已完成')
                continue
            if first_task:
                if '每日访问当前活动' in title:
                    self.components_action2(task_id=task_id, title=title)
                    Utils.r_sleep(2)
                    return
            else:
                if '每日访问当前活动' in title:
                    continue
                self.components_action2(task_id=task_id, title=title)
                Utils.r_sleep(2)

    # applet-wps超级会员签到
    def applet_sign(self):
        s_key = '06196ab4da15c09a3aaee610162ca56f'
        try:
            clock_info = self.rr.get('https://personal-bus.wps.cn/activity/clock_in/v1/info').json()
            s_key = clock_info.get('data',{}).get('s_key')
        except Exception as e:
            logger.error(str(e))
        ss = '7908b285f33c837d'
        json_data = {
            'client_type': 1,
        }
        # 别动，签名算法不能用，固定即可
        signature = generate_sign(json_data, s_key, ss)
        headers = {
            'Host': 'personal-bus.wps.cn',
            'Connection': 'keep-alive',
            # 'Content-Length': '17',
            'date': 'Tue, 20 Jan 2026 14:42:58 GMT',
            'charset': 'utf-8',
            'signature': '0d8ff00f5c74de36d0b2e677c82b22a1dd5ab196b0d227cc875fb051eec50156',
            'x-csrftoken': '1234567890',
            'User-Agent': 'Mozilla/5.0 (Linux; Android 14; 23117RK66C Build/UKQ1.230804.001; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/142.0.7444.173 Mobile Safari/537.36 XWEB/1420153 MMWEBSDK/20240404 MMWEBID/3531 MicroMessenger/8.0.49.2600(0x2800313D) WeChat/arm64 Weixin Android Tablet NetType/WIFI Language/zh_CN ABI/arm64 MiniProgramEnv/android',
            'content-type': 'application/json',
            # 'Accept-Encoding': 'gzip,compress,br,deflate',
            'Referer': 'https://servicewechat.com/wx2f333d84a103825d/240/page-frame.html',
        }
        try:
            response = self.rr.post(
                'https://personal-bus.wps.cn/activity/clock_in/v1/clock_in',
                headers=headers,
                json=json_data,
            ).json()
            if response['result'] == 'ok':
                lottery_msg = f"小程序签到成功"
                logger.success(f'[用户{self.user_id}] {lottery_msg}')
                self.operation_logs.append(lottery_msg)
                return True
            if 'already clocked in today' in response.get("msg", "未知错误"):
                lottery_msg = f'小程序签到失败：今日已签到'
            else:
                lottery_msg = f'小程序签到失败：{response.get("msg", "未知错误")}'
            logger.error(f'[用户{self.user_id}] {lottery_msg}')
            self.operation_logs.append(lottery_msg)
            return None
        except Exception as e:
            lottery_msg = f'小程序签到异常：{str(e)}'
            logger.error(f'[用户{self.user_id}] {lottery_msg}')
            self.operation_logs.append(lottery_msg)
            return False

    # WPS挑战计划-任务完成or领取
    def challenge_component_action(self, title, task_id, component_action=None, action_name=None):
        component_action = component_action or self.de['FINISH']
        if not action_name:
            if self.de['FINISH'] == component_action:
                action_name = '完成'
            elif self.de['REWARD'] == component_action:
                action_name = '领取'
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9',
            'content-type': 'application/json',
            'origin': 'https://personal-act.wps.cn',
            'priority': 'u=1, i',
            'referer': 'https://personal-act.wps.cn/rubik2/portal/HD2025121517384715/YM2025121517381164?cs_from=pc_ucsty_rwzx_task&position=pc_rwzx_task',
            'sec-ch-ua': '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'user-agent': self.userAgent,
            'x-act-csrf-token': self.act_csrf_token,
        }
        json_data = {
            'component_uniq_number': {
                'activity_number': 'HD2025121517384715',
                'page_number': 'YM2025121517381164',
                'component_number': 'ZJ2025031817022062',
                'component_node_id': 'FN17642133971jKe',
                'filter_params': {
                    'cs_from': 'pc_ucsty_rwzx_task',
                    'position': 'pc_rwzx_task',
                },
            },
            'component_type': 35,
            'component_action': component_action,
            'task_center': {
                'task_id': task_id,
            },
        }
        try:
            response = self.rr.post(
                'https://personal-act.wps.cn/activity-rubik/activity/component_action',
                headers=headers,
                json=json_data,
            ).json()
            if response['result'] == 'ok':
                task_center = response['data']['task_center']
                if task_center['success']:
                    task_msg = f'{action_name}任务 [{title}] 成功'
                    logger.success(f'[用户{self.user_id}] {task_msg}')
                    if action_name == '领取':
                        self.operation_logs.append(task_msg)
                    return task_center.get('token') or True
                task_msg = f'{action_name}任务 [{title}] 失败：{task_center["reason"]}'
                logger.error(f'[用户{self.user_id}] {task_msg}')
                self.operation_logs.append(task_msg)
                return False
            task_msg = f'{action_name}任务 [{title}] 失败：{response}'
            logger.error(f'[用户{self.user_id}] {task_msg}')
            self.operation_logs.append(task_msg)
            return False
        except Exception as e:
            task_msg = f'{action_name}任务 [{title}] 异常：{str(e)}'
            logger.error(f'[用户{self.user_id}] {task_msg}')
            self.operation_logs.append(task_msg)
            return False
    # WPS挑战计划-任务信息
    def challenge_page_info(self):
        headers = {
            'accept': 'application/json, text/plain, */*',
            'accept-language': 'zh-CN,zh;q=0.9',
            'priority': 'u=1, i',
            'referer': 'https://personal-act.wps.cn/rubik2/portal/HD2025121517384715/YM2025121517381164?cs_from=pc_ucsty_rwzx_task&position=pc_rwzx_task',
            'sec-ch-ua': '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'same-origin',
            'user-agent': self.userAgent,
        }
        try:
            response = self.rr.get(
                'https://personal-act.wps.cn/activity-rubik/activity/page_info?activity_number=HD2025121517384715&page_number=YM2025121517381164&filter_params=%7B%22cs_from%22:%22pc_ucsty_rwzx_task%22,%22position%22:%22pc_rwzx_task%22%7D',
                headers=headers,
            ).json()
            if response['result'] == 'ok':
                lottery_times = None
                task_list = None
                for item in response["data"]:
                    if item.get("task_center"):
                        _task_list = item["task_center"].get("task_list", [])
                        if _task_list:
                            task_list = _task_list

                    if task_list and lottery_times:
                        break
                return {
                    "task_list": task_list,
                    "lottery_times": lottery_times,
                }
        except Exception as e:
            logger.error(f'[用户{self.user_id}] 获取WPS挑战计划任务列表异常：{str(e)}')
            return None

    # PS挑战计划-任务执行
    def challenge_exec(self, task_list):
        if not task_list:
            return
        for task in task_list:
            task_id = task['task_id']
            title = task['title']
            task_status = task['task_status']
            if task_status == 2:
                logger.info(f'[用户{self.user_id}] 任务 [{title}] 已完成')
                continue
            skip_keywords = ['邀请', 'PDF转换', 'PDF合并', '语音速记', '关注', '认证', '消费', '开通会员']
            if any(keyword in title for keyword in skip_keywords):
                logger.info(f'[用户{self.user_id}] 跳过任务 [{title}]')
                continue
            is_done = self.challenge_component_action(task_id=task_id, title=title, component_action=self.de['FINISH'])
            if is_done:
                Utils.r_sleep(1)
                self.challenge_component_action(task_id=task_id, title=title, component_action=self.de['REWARD'])
            Utils.r_sleep(1)

    def main(self):
        self.operation_logs = []  # 重置操作日志

        # ================ WPS任务中心 =================
        if self.global_config.get('task_center'):
            tip = '# 开始执行 WPS任务中心 任务'
            self.operation_logs.append(tip)
            logger.info(tip)
            page_data = self.page_info()
            if not page_data:
                error_msg = f'[用户{self.user_id}] 获取用户信息失败：活动结束或cookie过期'
                logger.error(error_msg)
                self.operation_logs.append(error_msg)
                return False, '\n'.join(self.operation_logs)
            task_list = page_data["task_list"] or list()
            Utils.r_sleep(1)
            # 签到
            public_data = self.get_public_key()
            if not public_data:
                self.operation_logs.append(f'[用户{self.user_id}] 获取签到密钥失败，终止任务')
                return None, '\n'.join(self.operation_logs)
            self.sign_in(public_data)
            Utils.r_sleep(1)
            # 完成领取任务
            self.lottery_process(task_list=task_list)
            Utils.r_sleep(1.5)
            # 重试
            page_data = self.page_info() or {}
            self.lottery_process(task_list=page_data.get("task_list") or list())

            Utils.r_sleep(1.5)
            page_data = self.page_info() or {}
            lottery_times = page_data.get("lottery_times") or 0
            # 抽奖
            if lottery_times > 0:
                logger.info(f'[用户{self.user_id}] 开始执行抽奖（剩余次数：{lottery_times}）')
                for i in range(lottery_times):
                    lottery_result = self.lottery_v2()
                    if not lottery_result:
                        logger.info(f'[用户{self.user_id}] 抽奖第{i+1}次失败，终止抽奖')
                        break
                    Utils.r_sleep(1)
            logger.info('# 执行完成 WPS任务中心 任务')
        # ================ WPS任务中心 =================
        # ============== 天天领福利 =============
        if self.global_config.get('lottery3'):
            tip = '# 开始执行 天天领福利 任务'
            self.operation_logs.append(tip)
            logger.info(tip)
            Utils.r_sleep(1)
            self.fragment_collect_sign_in()
            Utils.r_sleep(1)
            page_info3 = self.page_info3() or {}
            lottery_times3 = page_info3.get('lottery_times') or 0
            if lottery_times3 > 0:
                for i3 in range(lottery_times3):
                    lottery_result3 = self.lottery_v22()
                    if not lottery_result3:
                        logger.info(f'[用户{self.user_id}] 抽奖第{i3 + 1}次失败，终止抽奖')
                        break
                    Utils.r_sleep(1)
            logger.info('# 执行完成 天天领福利 任务')
        # ============== 天天领福利 =============
        # ================ WPS办公助手 =================
        if self.global_config.get('fragment_collect'):
            tip = '# 开始执行 WPS办公助手 任务'
            self.operation_logs.append(tip)
            logger.info(tip)
            page_result = self.page_info2()
            if page_result:
                task_list2 = page_result["task_list"] or list()
                # 办公助手-执行任务
                self.done_task2_handler(task_list2, first_task=True)
                self.done_task2_handler(task_list2)
                page_result = self.page_info2()
                lottery_times = page_result["lottery_times"] or 0
                Utils.r_sleep(1)
                # 办公助手-抽奖
                if lottery_times > 0:
                    for i2 in range(lottery_times):
                        lottery_result2 = self.lottery_exec()
                        if not lottery_result2:
                            logger.info(f'[用户{self.user_id}] 抽奖第{i2+1}次失败，终止抽奖')
                            break
                        Utils.r_sleep(1)
                logger.info('# 执行完成 WPS办公助手 任务')
            else:
                logger.error("WPS办公助手数据异常，跳过")
        # ================ WPS办公助手 =================

        # ================ WPS挑战计划 start =================
        if self.global_config.get('challenge'):
            tip = '# 开始执行 WPS挑战计划 任务'
            self.operation_logs.append(tip)
            logger.info(tip)
            challenge_data = self.challenge_page_info() or {}
            challenge_task_list = challenge_data.get('task_list') or list()
            if challenge_task_list:
                self.challenge_exec(challenge_task_list)
            else:
                msg = f'[用户{self.user_id}] WPS挑战计划任务列表为空或获取失败'
                logger.warning(msg)
                self.operation_logs.append(msg)
        # ================ WPS挑战计划 end =================

        # ================ wps超级会员小程序 =================
        if self.global_config.get('svip_applet'):
            tip = '# 开始执行 wps超级会员小程序 任务'
            self.operation_logs.append(tip)
            logger.info(tip)
            self.applet_sign()
            logger.info('# 执行完成 wps超级会员小程序 任务')
        # ================ wps超级会员小程序 =================

        # 汇总操作日志作为推送消息
        final_msg = f'用户ID：{self.user_id}\n' + '\n'.join(self.operation_logs)
        return True, final_msg


def read_users_from_env():
    """从 WPS_TASK_CK 读取账号，支持 &、英文逗号、中文逗号或换行分隔。"""
    users_env = os.getenv('WPS_TASK_CK', '')
    users = []
    for raw_item in re.split(r'[&，,\n]+', users_env):
        raw_item = raw_item.strip()
        if not raw_item:
            continue
        if '#' in raw_item:
            username, cookie = raw_item.split('#', 1)
            username = username.strip() or f'账号{len(users) + 1}'
            cookie = cookie.strip()
        else:
            username = f'账号{len(users) + 1}'
            cookie = raw_item
        if cookie:
            users.append({
                'username': username,
                'cookie': cookie,
            })
    return users


def validate_wps_cookie(cookie: str) -> str:
    """校验 WPS_TASK_CK 是否为 WPS 网页 cookie，返回错误原因；为空表示通过。"""
    cookie = str(cookie or '').strip()
    if not cookie:
        return 'cookie为空，请按 备注#cookie 配置 WPS_TASK_CK'

    cookie_dict = Utils.string_cookie_to_dict(cookie)
    missing_keys = [key for key in ('uid', 'act_csrf_token') if not cookie_dict.get(key)]
    if not missing_keys:
        return ''

    if '=' not in cookie and re.fullmatch(r'[A-Za-z0-9_-]{8,}', cookie):
        return (
            '当前填入的是 wxid/openid，不是 WPS 网页 cookie；'
            '本脚本不支持 wxid 直登，请配置 WPS_TASK_CK=备注#uid=xxx; act_csrf_token=xxx; ...'
        )

    return f"cookie参数不全，缺少：{', '.join(missing_keys)}；请重新抓取 WPS 网页 cookie"


def main():
    users = read_users_from_env()

    if not users:
        error_msg = '未配置用户信息，请设置 WPS_TASK_CK 环境变量，格式：备注#cookie'
        logger.error(error_msg)
        append_notify_result(1, 'WPS_TASK_CK', False, 'config_error', error_msg)
        dispatch_notify()
        return

    print(f'共读取到 {len(users)} 个用户')
    rnlProxy = RnlProxy() if RnlProxy else None

    for i, user in enumerate(users, 1):
        username = user['username']
        cookies = user['cookie']
        print(f"\n===== 正在处理第 {i} 个用户：{username} =====")

        invalid_msg = validate_wps_cookie(cookies)
        if invalid_msg:
            logger.error(f'【{username}】{invalid_msg}')
            append_notify_result(i, username, False, 'config_error', invalid_msg)
            continue

        proxies = None
        if rnlProxy:
            proxies = rnlProxy.get_valid_proxy()
            print(f'使用代理：{proxies}')

        try:
            success, msg = RNL(cookies, proxies=proxies).main()
            append_notify_result(
                index=i,
                account=username,
                ok=bool(success),
                status='success' if success else 'failed',
                message=msg,
            )
        except Exception as exc:
            error_msg = f'处理异常：{exc}'
            logger.error(f'【{username}】{error_msg}')
            append_notify_result(i, username, False, 'failed', error_msg)
        finally:
            Utils.r_sleep(1, 3)

    dispatch_notify()


if __name__ == '__main__':
    main()
