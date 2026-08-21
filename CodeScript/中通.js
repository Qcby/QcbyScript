/*
中通快递 v1.1.0（mywc网关聚合推送版）

功能：自动执行中通快递小程序登录、积分查询和每日签到，支持多账号执行，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL                   必填其一，自建授权服务器域名
   - 示例：http://127.0.0.1:8110
   - 脚本会自动拼接 /mywc
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=wx7ddec43d9d27276a
   - 请求头：auth=账号标识

2. 账号变量：
   zto_wxid 或 ZTO_WXID                             推荐，中通快递专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：wxid_a&wxid_b&wxid_c 或 wxid_a,wxid_b,wxid_c
   - 兼容旧变量 ZTKD 读取

3. 推送变量：
   JS 脚本内置 axios 企业微信机器人聚合推送，配置 QYWX_KEY 即可。
   QYWX_KEY                                         企业微信机器人 key

4. 青龙任务建议：
   名称：中通快递
   命令：node 中通.js
   定时：20 8 * * *
*/

const axios = require("axios");

const SCRIPT_TITLE = "中通快递";
const QYWX_KEY = process.env.QYWX_KEY || "";
const GLOBAL_NOTIFY_BUFFERS = [];

const APP = {
    name: "中通快递",
    appid: "wx7ddec43d9d27276a",
    version: 670
};

const WX_SERVER_URL = (process.env.wx_server_url || process.env.WX_SERVER_URL || "").trim();
const MAIN_HOST = "https://hdgateway.zto.com/";
const MEMBER_HOST = "https://membergateway.zto.com/";

const USER_AGENT =
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) MicroMessenger/3.9.12 MiniProgramEnv/Windows WindowsWechat/WMPF";

const $ = {
    name: SCRIPT_TITLE,
    log(...args) {
        console.log(`[${this.name}]`, ...args);
    },
    wait(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    },
    done() {
        console.log(`\n[${this.name}] 任务执行完毕`);
    }
};

function getEnvAccounts() {
    const env =
        process.env.zto_wxid ||
        process.env.ZTO_WXID ||
        process.env.ZTKD ||
        "";

    return env
        .split(/[&,\n，]+/)
        .map((item) => item.trim())
        .filter(Boolean);
}

function short(value, max = 320) {
    if (value === undefined || value === null) return "";
    const text = typeof value === "string" ? value : JSON.stringify(value);
    return text.length > max ? `${text.slice(0, max)}...` : text;
}

function maskAccount(account = "") {
    const value = String(account || "");
    if (!value) return "未知账号";
    if (value.length <= 8) return `${value.slice(0, 2)}***`;
    return `${value.slice(0, 4)}***${value.slice(-4)}`;
}

function maskPhone(phone = "") {
    return String(phone || "").replace(/^(\d{3})\d{4}(\d{4})$/, "$1****$2");
}

function pad(num) {
    return String(num).padStart(2, "0");
}

function formatDate(date) {
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function formatDateTime(date = new Date()) {
    return `${formatDate(date)} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function appendNotifyResult(result) {
    GLOBAL_NOTIFY_BUFFERS.push({
        success: false,
        account: "",
        beforePoints: null,
        afterPoints: null,
        gainPoints: 0,
        signStatus: "",
        reason: "",
        ...result
    });
}

async function sendNativeNotify(title, content) {
    if (!QYWX_KEY) {
        $.log("未配置 QYWX_KEY，跳过企业微信推送");
        return;
    }

    const url = `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=${QYWX_KEY}`;
    const res = await axios.post(
        url,
        {
            msgtype: "text",
            text: {
                content: `${title}\n\n${content}`
            }
        },
        {
            timeout: 15000,
            validateStatus: () => true
        }
    );

    if (res.status !== 200 || res.data?.errcode !== 0) {
        $.log(`企业微信推送失败：${short(res.data || res.status)}`);
    }
}

function buildNotifyReport() {
    const total = GLOBAL_NOTIFY_BUFFERS.length;
    const successList = GLOBAL_NOTIFY_BUFFERS.filter((item) => item.success);
    const failedList = GLOBAL_NOTIFY_BUFFERS.filter((item) => !item.success);
    const totalGain = successList.reduce((sum, item) => sum + Number(item.gainPoints || 0), 0);

    const lines = [
        "==============================",
        `🕒 执行时间：${formatDateTime()}`,
        `📊 统计数据：成功 ${successList.length} / 总计 ${total}`,
        `✅ 成功账号：${successList.length} 个`,
        `❌ 失败账号：${failedList.length} 个`,
        `💰 累计积分：+${totalGain}`,
        "=============================="
    ];

    GLOBAL_NOTIFY_BUFFERS.forEach((item, index) => {
        lines.push(`🧑‍💻 【账号${index + 1}】${maskAccount(item.account)}`);
        if (item.success) {
            lines.push("✅ 状态：执行成功");
            lines.push(`🎯 签到：${item.signStatus || "已执行"}`);
            lines.push(
                `💰 积分：始 ${item.beforePoints ?? "未知"} ➔ 终 ${item.afterPoints ?? "未知"}，获得 +${item.gainPoints ?? 0}`
            );
        } else {
            lines.push("❌ 状态：执行失败");
            lines.push(`🧨 原因：${item.reason || "未知错误"}`);
        }
        lines.push("------------------------------");
    });

    return lines.join("\n");
}

async function dispatchNotify() {
    if (!GLOBAL_NOTIFY_BUFFERS.length) {
        appendNotifyResult({
            account: "未读取到账号",
            reason: "脚本未产生任何执行结果"
        });
    }

    try {
        await sendNativeNotify(SCRIPT_TITLE, buildNotifyReport());
    } catch (error) {
        $.log(`聚合推送异常：${error.message || error}`);
    }
}

async function request(options) {
    const res = await axios.request({
        timeout: 20000,
        validateStatus: () => true,
        ...options,
        headers: {
            "User-Agent": USER_AGENT,
            Accept: "application/json, text/plain, */*",
            ...(options.headers || {})
        }
    });

    return {
        status: res.status,
        data: res.data || {},
        headers: res.headers || {}
    };
}

function gatewayUrl(path) {
    if (!WX_SERVER_URL) {
        throw new Error("请配置 wx_server_url 或 WX_SERVER_URL");
    }
    return `${WX_SERVER_URL.replace(/\/+$/, "")}/${path.replace(/^\/+/, "")}`;
}

async function getWxCode(wxid) {
    const res = await request({
        method: "GET",
        url: gatewayUrl("/mywc"),
        params: {
            wxid,
            appId: APP.appid
        },
        headers: {
            auth: wxid
        }
    });

    const code =
        res.data?.code ||
        res.data?.data?.code ||
        res.data?.data?.result?.code ||
        res.data?.result?.code;

    if (res.status !== 200 || !code) {
        throw new Error(`获取微信code失败：${short(res.data || res.status)}`);
    }

    return {
        code,
        openid:
            res.data?.openid ||
            res.data?.openId ||
            res.data?.data?.openid ||
            res.data?.data?.openId ||
            res.data?.data?.result?.openid ||
            ""
    };
}

class ZtoExpress {
    constructor(wxid, index) {
        this.index = index;
        this.wxid = wxid;
        this.openId = "";
        this.token = "";
        this.user = {};
        this.today = {};
        this.beforePoints = null;
        this.afterPoints = null;
        this.signStatus = "未签到";
    }

    headers(extra = {}) {
        return {
            "content-type": "application/json",
            "x-token": this.token || "",
            "x-version": "V8.153.1",
            "x-clientCode": "wechatMiniZtoHelper",
            "x-oid": this.openId || "",
            "x-ys-dt": "",
            "x-sv-v": "0.22.0",
            Referer: `https://servicewechat.com/${APP.appid}/${APP.version}/page-frame.html`,
            ...extra
        };
    }

    isTokenInvalid(resData) {
        if (!resData) return false;

        const msg = String(resData.message || resData.errMessage || resData.msg || "");
        const statusCode = String(resData.statusCode || resData.code || "");

        return /未登录|token失效|token过期|请先登录|invalid token|Unauthorized/i.test(msg) || statusCode === "S209";
    }

    async api(host, apiPath, data = {}, allowFail = false, isRetry = false) {
        const res = await request({
            method: "POST",
            url: `${host}${apiPath}`,
            headers: this.headers(),
            data
        });

        if (this.isTokenInvalid(res.data) && !isRetry) {
            $.log(`账号[${this.index}] token失效，重新登录`);
            try {
                await this.login();
                return this.api(host, apiPath, data, allowFail, true);
            } catch (error) {
                $.log(`重新登录失败：${error.message || error}`);
            }
        }

        if (res.status !== 200 && !allowFail) {
            throw new Error(`HTTP ${res.status}：${short(res.data)}`);
        }

        return res.data;
    }

    async login() {
        $.log(`账号[${this.index}] 使用 wxid：${maskAccount(this.wxid)}`);

        const wx = await getWxCode(this.wxid);
        const res = await this.api(MAIN_HOST, "auth_wechatMini_authByCode", { code: wx.code }, true, true);
        const data = res.result || res.data || {};

        if (!data.token) {
            throw new Error(`登录失败：${short(res)}`);
        }

        this.token = data.token;
        this.openId = data.openId || wx.openid || this.openId;
        this.user = data;

        $.log(
            `登录成功 userId=${data.userId || "未知"} 手机=${maskPhone(data.mobile || "") || "未知"} openId=${this.openId || "未知"}`
        );
    }

    async queryPoints() {
        const res = await this.api(MEMBER_HOST, "member/getMemberPoints", {}, true);

        if (!res.success && !res.data) {
            $.log(`积分查询失败：${short(res)}`);
            return null;
        }

        const data = res.data || {};
        const totalPoint = data.totalPoint ?? null;

        $.log(`积分查询：当前积分=${totalPoint ?? "未知"}，即将过期=${data.overDuePoint ?? 0}`);
        return Number.isFinite(Number(totalPoint)) ? Number(totalPoint) : null;
    }

    signRange() {
        const today = new Date();
        const start = new Date(today);
        const end = new Date(today);

        start.setDate(today.getDate() - 3);
        end.setDate(today.getDate() + 3);

        return {
            startDate: `${formatDate(start)} 00:00:00`,
            endDate: `${formatDate(end)} 23:59:59`,
            todayDate: formatDate(today)
        };
    }

    async querySignInfo(prefix = "签到信息") {
        const range = this.signRange();
        const res = await this.api(
            MEMBER_HOST,
            "member/activity/queryRecentSign",
            {
                startDate: range.startDate,
                endDate: range.endDate
            },
            true
        );

        const data = res.result || res.data || {};
        const list = Array.isArray(data.dailyList) ? data.dailyList : [];

        this.today = list.find((item) => item.isToday || item.date === range.todayDate) || {};

        $.log(
            `${prefix}：今日${this.today.isSigned ? "已签到" : "未签到"}，可得积分=${this.today.pointsEarned ?? "未知"}，连续=${data.continuousDays ?? 0}天，总积分=${data.totalPoints ?? "未知"}`
        );

        return data;
    }

    async sign() {
        if (this.today?.isSigned) {
            this.signStatus = "今日已签到";
            $.log("签到：今日已签到");
            return;
        }

        const date = this.today?.date || formatDate(new Date());
        const res = await this.api(
            MEMBER_HOST,
            "member/activity/signIn",
            {
                signType: "TODAY_SIGN",
                signDate: `${date} 00:00:00`,
                supplementaryScene: null
            },
            true
        );

        if (res.status || res.success || res.result) {
            const data = res.result || res.data || {};
            this.signStatus = `签到成功，获得 ${data.pointsEarned ?? "未知"} 积分`;
            $.log(`签到：成功，获得=${data.pointsEarned ?? "未知"}积分，奖励=${data.awardType || "未知"}`);
            return;
        }

        const msg = res.message || res.errMessage || short(res);
        if (/已签|重复|already/i.test(String(msg))) {
            this.signStatus = "今日已签到";
            $.log(`签到：今日已签到，${msg}`);
            return;
        }

        throw new Error(`签到失败：${short(res)}`);
    }

    async run() {
        $.log(`\n========== ${APP.name} 账号[${this.index}] ==========\n`);

        await this.login();
        this.beforePoints = await this.queryPoints();
        await this.querySignInfo("签到前");
        await this.sign();
        await this.querySignInfo("签到后");
        this.afterPoints = await this.queryPoints();

        return {
            success: true,
            account: this.wxid,
            beforePoints: this.beforePoints,
            afterPoints: this.afterPoints,
            gainPoints:
                this.beforePoints !== null && this.afterPoints !== null
                    ? Math.max(0, this.afterPoints - this.beforePoints)
                    : 0,
            signStatus: this.signStatus
        };
    }
}

(async () => {
    try {
        const wxids = getEnvAccounts();

        if (!wxids.length) {
            appendNotifyResult({
                account: "未配置",
                reason: "请配置 zto_wxid 或 ZTO_WXID"
            });
            return;
        }

        if (!WX_SERVER_URL) {
            wxids.forEach((wxid) => {
                appendNotifyResult({
                    account: wxid,
                    reason: "请配置 wx_server_url 或 WX_SERVER_URL"
                });
            });
            return;
        }

        $.log(`读取到 ${wxids.length} 个账号`);

        for (let index = 0; index < wxids.length; index++) {
            const wxid = wxids[index];
            const runner = new ZtoExpress(wxid, index + 1);

            try {
                appendNotifyResult(await runner.run());
            } catch (error) {
                const reason = error.message || String(error);
                $.log(`账号[${index + 1}]执行失败：${reason}`);
                appendNotifyResult({
                    account: wxid,
                    reason
                });
            }

            await $.wait(800);
        }
    } catch (error) {
        $.log(`脚本异常：${error.stack || error.message || error}`);
        appendNotifyResult({
            account: "脚本异常",
            reason: error.message || String(error)
        });
    } finally {
        await dispatchNotify();
        $.done();
    }
})();
