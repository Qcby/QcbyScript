/*
腾讯地图 v1.1.0（mywc网关聚合推送版）

功能：腾讯地图小程序签到领现金、现金余额查询、资产查询，支持多账号执行，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL                   必填其一，自建授权服务器域名
   - 示例：http://127.0.0.1:8110
   - 脚本会自动拼接 /mywc
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=wx7643d5f831302ab0
   - 请求头：auth=账号标识

2. 账号变量：
   txdt_wxid 或 TXDT_WXID                           推荐，腾讯地图专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：wxid_a&wxid_b&wxid_c
   - 兼容旧变量 txdt、TXDT、tencentmap、wx_openid 读取
   - 兼容 JSON：{"wxid":"xxx","remark":"备注"} 或 {"openid":"xxx"}

3. 推送变量：
   QYWX_KEY                                         企业微信机器人 key

4. 青龙任务建议：
   名称：腾讯地图
   命令：node 腾讯地图.js
   定时：18 8 * * *
*/

const axios = require("axios");
const crypto = require("crypto");

const SCRIPT_TITLE = "腾讯地图";
const QYWX_KEY = process.env.QYWX_KEY || "";
const GLOBAL_NOTIFY_BUFFERS = [];
const CK_NAME = "txdt_wxid / TXDT_WXID";
const APP = { name: "腾讯地图", appid: "wx7643d5f831302ab0", version: Number(process.env.TXDT_APP_VERSION || process.env.txdt_app_version || 545) };
const WX_SERVER_URL = (process.env.wx_server_url || process.env.WX_SERVER_URL || "").replace(/\/+$/, "");
const MINI_LOGIN_BASE = "https://miniapp.map.qq.com";
const MAP_BASE = "https://mmapgwh.map.qq.com";
const LOGIN_ACCESS_KEY = "1";
const LOGIN_SECRET_KEY = "4300eec60bedec22a73408a0d76b03ec";
const TMAP_SECRET = "3a9875e795c3ecff15f617085e72d4cc";
const CHECKIN_TOKEN = "e643d512f085d621bf6c9e80310d0498";
const ACTIVITY_ID = 1721983577;
const USER_AGENT =
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) MicroMessenger/3.9.12 MiniProgramEnv/Windows WindowsWechat/WMPF";

const $ = {
    log(...args) {
        console.log(...args);
    },
    wait(ms) {
        return new Promise((resolve) => setTimeout(resolve, ms));
    },
    async done() {
        console.log("执行结束");
    },
};

function getNowTime() {
    const pad = (n) => String(n).padStart(2, "0");
    const d = new Date();
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function maskAccount(value = "") {
    const text = String(value || "").trim();
    if (text.length <= 2) return text || "未命名账号";
    if (text.length <= 8) return `${text[0]}***${text[text.length - 1]}`;
    return `${text.slice(0, 4)}***${text.slice(-4)}`;
}

function appendNotifyResult(item) {
    GLOBAL_NOTIFY_BUFFERS.push({
        index: item.index,
        account: maskAccount(item.account),
        ok: Boolean(item.ok),
        status: item.status || (item.ok ? "success" : "failed"),
        message: item.message || "",
        balanceBefore: item.balanceBefore,
        balanceAfter: item.balanceAfter,
        coinsBefore: item.coinsBefore,
        coinsAfter: item.coinsAfter,
        checkinStatus: item.checkinStatus || "",
        prizes: item.prizes || "",
    });
}

function buildNotifyReport() {
    const total = GLOBAL_NOTIFY_BUFFERS.length;
    const success = GLOBAL_NOTIFY_BUFFERS.filter((item) => item.ok).length;
    const failed = total - success;
    const lines = [
        "==============================",
        `🕒 执行时间：${getNowTime()}`,
        `📊 统计数据：成功 ${success} / 总计 ${total}`,
        `✅ 成功账号：${success} 个`,
        `❌ 失败账号：${failed} 个`,
        "==============================",
    ];
    for (const item of GLOBAL_NOTIFY_BUFFERS) {
        const ok = Boolean(item.ok);
        lines.push(`${ok ? "🧑‍💻" : "🧟"} 【账号${item.index}】${item.account}`);
        lines.push(`${ok ? "✅" : "❌"} 状态：${ok ? "执行成功" : (item.status === "config_error" ? "配置错误" : "执行失败")}`);
        if (ok) {
            lines.push(`💰 现金：始 ${formatCoin(item.balanceBefore)} ➔ 终 ${formatCoin(item.balanceAfter)}`);
            lines.push(`🪙 金币：始 ${formatCoin(item.coinsBefore)} ➔ 终 ${formatCoin(item.coinsAfter)}`);
            lines.push(`📅 签到：${item.checkinStatus || "未知"}`);
            if (item.prizes) lines.push(`🎁 奖励：${item.prizes}`);
        } else {
            lines.push(`🧨 原因：${item.message || "未知错误"}`);
        }
        lines.push("------------------------------");
    }
    return lines.join("\n");
}

async function sendNativeNotify(title, content) {
    if (!QYWX_KEY) {
        console.log(`未配置 QYWX_KEY，跳过企业微信推送\n${title}\n${content}`);
        return;
    }
    await axios.post(
        `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=${QYWX_KEY}`,
        { msgtype: "text", text: { content: `${title}\n${content}` } },
        { timeout: 15000 }
    );
}

async function dispatchNotify() {
    if (!GLOBAL_NOTIFY_BUFFERS.length) return;
    const title = `${SCRIPT_TITLE}执行结果`;
    const content = buildNotifyReport();
    try {
        await sendNativeNotify(title, content);
        console.log("聚合推送已发送");
    } catch (e) {
        console.log(`聚合推送失败：${e.response?.data?.errmsg || e.message || e}`);
        console.log(`\n${title}\n${content}`);
    }
}

function getAccountEnv() {
    return process.env.txdt_wxid || process.env.TXDT_WXID || process.env.txdt || process.env.TXDT || process.env.tencentmap || process.env.wx_openid || "";
}

function splitAccounts(value = "") {
    return String(value)
        .split(/\n|&/)
        .map((item) => item.trim())
        .filter(Boolean);
}

function short(value, max = 320) {
    if (value === undefined || value === null) return "";
    const text = typeof value === "string" ? value : JSON.stringify(value);
    return text.length > max ? `${text.slice(0, max)}...` : text;
}

function md5(value) {
    return crypto.createHash("md5").update(String(value)).digest("hex");
}

function sha256(value) {
    return crypto.createHash("sha256").update(String(value)).digest("hex");
}

function uuid() {
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (char) => {
        const n = (Math.random() * 16) | 0;
        return (char === "x" ? n : (n & 3) | 8).toString(16);
    });
}

function sortedQuery(data) {
    const normalized = {};
    Object.keys(data)
        .sort()
        .forEach((key) => {
            if (data[key] !== undefined && data[key] !== null) normalized[key] = data[key];
        });
    return Object.keys(normalized)
        .map((key) => `${key}=${normalized[key]}`)
        .join("&");
}

function formatCoin(value) {
    const num = Number(value || 0);
    return `${num}(${(num / 100).toFixed(2)})`;
}

function parseAccount(raw) {
    const text = String(raw || "").trim();
    if (!text) return {};
    if (text.startsWith("{")) {
        const data = JSON.parse(text);
        const wxid = data.wxid || data.wx_id || data.openid || data.openId || "";
        return { raw: text, openid: wxid, remark: data.remark || data.name || "" };
    }
    const [wxid, remark] = text.split("#").map((item) => item.trim());
    return { raw: text, openid: wxid, remark };
}

async function request(options) {
    const res = await axios.request({
        timeout: 20000,
        validateStatus: () => true,
        ...options,
        headers: {
            "User-Agent": USER_AGENT,
            Accept: "application/json, text/plain, */*",
            Referer: `https://servicewechat.com/${APP.appid}/${APP.version}/page-frame.html`,
            ...(options.headers || {}),
        },
    });
    return { status: res.status, headers: res.headers || {}, data: res.data };
}

async function getWxCode(wxid) {
    if (!WX_SERVER_URL) throw new Error("未配置 wx_server_url 或 WX_SERVER_URL");
    if (!wxid) throw new Error("账号标识为空");
    const { status, data } = await request({
        method: "GET",
        url: `${WX_SERVER_URL}/mywc`,
        headers: { auth: wxid },
        params: { wxid, appId: APP.appid },
    });
    const code = data?.data?.code || data?.code || data?.wx_code || data?.data?.wx_code || (typeof data === "string" ? data : "");
    if (status !== 200 || !code) throw new Error(`获取code失败 HTTP ${status}: ${short(data)}`);
    return code;
}

function loginSign({ appId, sessionId = "-1", openId, userId, postBody }) {
    const reqId = md5(`${Math.random()} ${Date.now()}`);
    const reqTime = Date.now().toString().slice(0, 10);
    const signParams = {
        appId,
        reqId,
        reqTime,
        userId,
        openID: openId,
        sessionID: sessionId,
        accessKey: LOGIN_ACCESS_KEY,
        businessStr: JSON.stringify(postBody),
    };
    const signText = `${sortedQuery(signParams)}&secretKey=${LOGIN_SECRET_KEY}`;
    const headers = {
        "mapservice-sign-version": "v2",
        "mapservice-sign": sha256(signText),
        "mapservice-reqid": reqId,
        "mapservice-reqtime": reqTime,
        "mapservice-appid": appId,
        "mapservice-accesskey": LOGIN_ACCESS_KEY,
        "mapservice-sessionid": sessionId,
    };
    if (sessionId && sessionId !== "-1") {
        headers["mapservice-openid"] = openId;
        headers["mapservice-userid"] = userId;
    }
    return headers;
}

function mapH5Sign(apiPath, user) {
    const reqId = uuid();
    const reqTime = Date.now();
    const normalizedPath = apiPath.split("?")[0];
    const signBase = `mapinst=0&mapnonce=0&reqid=${reqId}&reqtime=${reqTime}`;
    const defaultSign = md5(`${signBase}${normalizedPath}0${TMAP_SECRET}`);
    const headers = {
        "tmap-reqid": reqId,
        "tmap-reqtime": reqTime,
        "tmap-userid": Number(user.user_id) || Number(user.userId) || 0,
        "tmap-login-ssid": user.session_id || user.sessionId || 0,
        "tmap-imei": 0,
        "tmap-qimei": 0,
        "tmap-qimei36": 0,
        "tmap-nonce": 0,
        "tmap-install-id": 0,
        "tmap-sign": 0,
        "tmap-default-sign": defaultSign,
        "tmap-app-version": 0,
        "tmap-channel": 0,
        "tmap-engine": "web",
        "tmap-mini-login-ssid": user.map_session_id || user.mapSessionId || "",
        "tmap-app-id": user.appId || APP.appid,
    };
    if (user.openid || user.openId) headers["tmap-openid"] = user.openid || user.openId;
    return headers;
}

function checkinHeader(user) {
    const requestId = uuid();
    const timestamp = Math.floor(Date.now() / 1000);
    const signText = `request_id=${requestId}&from_source=${APP.appid}&timestamp=${timestamp}&token=${CHECKIN_TOKEN}`;
    return {
        user_id: user.openid || user.openId,
        from_source: APP.appid,
        request_id: requestId,
        timestamp,
        sign: sha256(signText).toUpperCase(),
    };
}

class TencentMap {
    constructor(rawAccount, index) {
        this.index = index;
        this.account = parseAccount(rawAccount);
        this.loginInfo = {};
        this.userInfo = {};
        this.balanceBefore = undefined;
        this.balanceAfter = undefined;
        this.coinsBefore = undefined;
        this.coinsAfter = undefined;
        this.checkinStatus = "未执行";
        this.prizes = "";
    }

    async miniLogin() {
        if (!this.account.openid) throw new Error("账号格式错误，请配置 txdt_wxid 或 TXDT_WXID");
        const code = await getWxCode(this.account.openid);
        const body = {
            seqid: uuid(),
            app_id: APP.appid,
            auth_code: code,
            devHeader: {},
        };
        const { status, data } = await request({
            method: "POST",
            url: `${MINI_LOGIN_BASE}/minLogin/v2/login`,
            headers: {
                "content-type": "application/json",
                ...loginSign({ appId: APP.appid, postBody: body }),
            },
            data: body,
        });
        if (status !== 200 || Number(data?.err_code) !== 0) throw new Error(`登录失败 HTTP ${status}: ${short(data)}`);
        this.loginInfo = { ...data, appId: APP.appid };
        $.log(`登录：成功 userId=${data.user_id || "未知"}，openid=${data.openid || "未知"}`);
    }

    async queryUser() {
        const user = this.loginInfo;
        const body = {
            seqid: uuid(),
            app_id: APP.appid,
            userId: user.user_id,
            openId: user.openid,
            source: "mini-tencentmap",
        };
        const { status, data } = await request({
            method: "POST",
            url: `${MINI_LOGIN_BASE}/minLogin/v2/getUserInfo`,
            headers: {
                "content-type": "application/json",
                ...loginSign({
                    appId: APP.appid,
                    sessionId: user.session_id,
                    userId: user.user_id,
                    openId: user.openid,
                    postBody: body,
                }),
            },
            data: body,
        });
        if (status !== 200 || Number(data?.err_code) !== 0) {
            $.log(`用户信息：查询失败 HTTP ${status}: ${short(data)}`);
            return;
        }
        this.userInfo = data || {};
        $.log(`用户信息：${data.nickname || "微信用户"}，userId=${data.userid || user.user_id}`);
    }

    async mapApi(apiPath, data) {
        const { status, data: body } = await request({
            method: "POST",
            url: `${MAP_BASE}${apiPath}`,
            headers: {
                "content-type": "application/json",
                ...checkinHeader(this.loginInfo),
                ...mapH5Sign(apiPath, this.loginInfo),
            },
            data,
        });
        if (status !== 200 || Number(body?.code) !== 0) throw new Error(`${apiPath} HTTP ${status}: ${short(body)}`);
        return body.data || {};
    }

    async queryBalance(prefix = "现金余额") {
        const data = await this.mapApi("/activity/v1/withdraw/home", {
            activity_id: ACTIVITY_ID,
            game_id: 4,
            rule_id: "tencent_map_withdraw",
        });
        $.log(
            `${prefix}：金币=${formatCoin(data.coins)}，可提现=${formatCoin(data.withdrawable_amount)}，门槛=${formatCoin(data.current_withdraw_threshold)}，奖池=${formatCoin(data.jackpot_amount)}`
        );
        if (String(prefix).includes("签到前")) {
            this.balanceBefore = data.withdrawable_amount;
            this.coinsBefore = data.coins;
        }
        if (String(prefix).includes("签到后")) {
            this.balanceAfter = data.withdrawable_amount;
            this.coinsAfter = data.coins;
        }
        return data;
    }

    async queryAssets() {
        const data = await this.mapApi("/activity/v1/assert/home", { activity_id: ACTIVITY_ID });
        $.log(
            `资产信息：金币=${formatCoin(data.coins)}，优惠券=${data.coupons_total || 0}，抽奖券=${data.lottery_ticket_total || 0}`
        );
        return data;
    }

    todayKey() {
        const now = new Date();
        const year = now.getFullYear();
        const month = `${now.getMonth() + 1}`.padStart(2, "0");
        const day = `${now.getDate()}`.padStart(2, "0");
        return `${year}${month}${day}`;
    }

    async queryCalendar(prefix = "签到状态") {
        const data = await this.mapApi("/activity/v1/checkin/calendar", {
            activity_id: ACTIVITY_ID,
            game_id: 1,
            rule_id: "tencent_map_checkin",
        });
        const today = data.calendar?.[this.todayKey()] || {};
        const prizes = Array.isArray(today.prizes)
            ? today.prizes.map((item) => `${item.name || item.type || "奖励"}:${item.amount ?? ""}`).join("，")
            : "";
        $.log(`${prefix}：今日${today.checkin ? "已签" : "未签"}，周期已签=${data.checkin_days || 0}/${data.period || 0}${prizes ? `，奖励=${prizes}` : ""}`);
        if (String(prefix).includes("签到后") || today.checkin) {
            this.checkinStatus = today.checkin ? "今日已签" : "今日未签";
        }
        if (prizes) this.prizes = prizes;
        return { data, today };
    }

    async checkin() {
        const { today } = await this.queryCalendar("签到前");
        if (today.checkin) {
            this.checkinStatus = "今日已签到";
            $.log("签到：今日已签到");
            return;
        }
        const data = await this.mapApi("/activity/v1/checkin", {
            activity_id: ACTIVITY_ID,
            game_id: 1,
            rule_id: "tencent_map_checkin",
            nick: this.userInfo.nickname || "微信用户",
        });
        const prizes = Array.isArray(data.prizes)
            ? data.prizes.map((item) => `${item.name || item.type || "奖励"}:${item.amount ?? ""}`).join("，")
            : short(data);
        this.checkinStatus = "签到成功";
        this.prizes = prizes;
        $.log(`签到：成功${prizes ? `，${prizes}` : ""}`);
    }

    async run() {
        $.log(`\n========== ${APP.name} 账号[${this.index}] ${this.account.remark || this.account.openid} ==========`);
        await this.miniLogin();
        await this.queryUser();
        await this.queryBalance("签到前现金余额");
        await this.queryAssets();
        await this.checkin();
        await this.queryBalance("签到后现金余额");
        await this.queryCalendar("签到后");
        return {
            index: this.index,
            account: this.account.openid,
            ok: true,
            status: "success",
            balanceBefore: this.balanceBefore,
            balanceAfter: this.balanceAfter,
            coinsBefore: this.coinsBefore,
            coinsAfter: this.coinsAfter,
            checkinStatus: this.checkinStatus,
            prizes: this.prizes,
        };
    }
}

(async () => {
    const accounts = splitAccounts(getAccountEnv());
    if (!accounts.length) {
        $.log(`未配置 ${CK_NAME}`);
        appendNotifyResult({
            index: 1,
            account: CK_NAME,
            ok: false,
            status: "config_error",
            message: `未配置 ${CK_NAME}`,
        });
        await dispatchNotify();
        await $.done();
        return;
    }
    $.log(`共找到${accounts.length}个账号`);
    for (let i = 0; i < accounts.length; i++) {
        const runner = new TencentMap(accounts[i], i + 1);
        try {
            const result = await runner.run();
            appendNotifyResult(result);
        } catch (e) {
            const message = e.message || String(e);
            $.log(`账号[${i + 1}] 执行失败：${message}`);
            appendNotifyResult({
                index: i + 1,
                account: runner.account?.openid || accounts[i],
                ok: false,
                status: "failed",
                message,
            });
        }
        await $.wait(800);
    }
    await dispatchNotify();
    await $.done();
})().catch(async (e) => {
    const message = e.stack || e.message || String(e);
    $.log(`脚本异常：${message}`);
    appendNotifyResult({
        index: 1,
        account: SCRIPT_TITLE,
        ok: false,
        status: "failed",
        message,
    });
    await dispatchNotify();
    await $.done();
});
