/*
蜜雪冰城 v1.1.0（mywc网关聚合推送版）

功能：自动登录蜜雪冰城小程序并访问魔法铺任务，支持多账号执行，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL                   必填其一，自建授权服务器域名
   - 示例：http://127.0.0.1:8110
   - 脚本会自动拼接 /mywc
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=wx7696c66d2245d107
   - 请求头：auth=账号标识

2. 账号变量：
   mixue_wxid 或 MIXUE_WXID                         推荐，蜜雪冰城专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：wxid_a&wxid_b 或 wxid_a,wxid_b

3. 推送变量：
   QYWX_KEY                                         企业微信机器人 key，脚本内置 axios 原生推送
   PUSH_PLUS_TOKEN 或 PLUSPLUS_TOKEN                 PushPlus token，可选兼容

4. 青龙任务建议：
   名称：蜜雪冰城
   命令：node 蜜雪冰城.js
   定时：每天运行 1 次即可，具体时间自行调整
*/

const axios = require("axios");
const rs = require("jsrsasign");

const SCRIPT_TITLE = "蜜雪冰城";
const QYWX_KEY = process.env.QYWX_KEY || "";
const PUSH_PLUS_TOKEN = process.env.PUSH_PLUS_TOKEN || process.env.PLUSPLUS_TOKEN || "";
const WX_SERVER_URL = (process.env.wx_server_url || process.env.WX_SERVER_URL || "").replace(/\/+$/, "");
const ACCOUNT_RAW = process.env.mixue_wxid || process.env.MIXUE_WXID || "";
const GLOBAL_NOTIFY_BUFFERS = [];

const APP_ID = "d82be6bbc1da11eb9dd000163e122ecb";
const MINI_APP_ID = "wx7696c66d2245d107";
const UA = "Mozilla/5.0 (Linux; Android 15; 22061218C Build/AQ3A.250226.002; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/146.0.7680.177 Mobile Safari/537.36 XWEB/1460075 MMWEBSDK/20260202 MMWEBID/6435 MicroMessenger/8.0.71.3080(0x28004761) WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64 miniProgram/wx7696c66d2245d107";

const privateKeyString = `-----BEGIN PRIVATE KEY-----
MIIEvwIBADANBgkqhkiG9w0BAQEFAASCBKkwggSlAgEAAoIBAQCtypUdHZJKlQ9L
L6lIJSphnhqjke7HclgWuWDRWvzov30du235cCm13mqJ3zziqLCwstdQkuXo9sOP
Ih94t6nzBHTuqYA1whrUnQrKfv9X4/h3QVkzwT+xWflE+KubJZoe+daLKkDeZjVW
nUku8ov0E5vwADACfntEhAwiSZUALX9UgNDTPbj5ESeII+VztZ/KOFsRHMTfDb1G
IR/dAc1mL5uYbh0h2Fa/fxRPgf7eJOeWGiygesl3CWj0Ue13qwX9PcG7klJXfToI
576MY+A7027a0aZ49QhKnysMGhTdtFCksYG0lwPz3bIR16NvlxNLKanc2h+ILTFQ
bMW/Y3DRAgMBAAECggEBAJGTfX6rE6zX2bzASsu9HhgxKN1VU6/L70/xrtEPp4SL
SpHKO9/S/Y1zpsigr86pQYBx/nxm4KFZewx9p+El7/06AX0djOD7HCB2/+AJq3iC
5NF4cvEwclrsJCqLJqxKPiSuYPGnzji9YvaPwArMb0Ff36KVdaHRMw58kfFys5Y2
HvDqh4x+sgMUS7kSEQT4YDzCDPlAoEFgF9rlXnh0UVS6pZtvq3cR7pR4A9hvDgX9
wU6zn1dGdy4MEXIpckuZkhwbqDLmfoHHeJc5RIjRP7WIRh2CodjetgPFE+SV7Sdj
ECmvYJbet4YLg+Qil0OKR9s9S1BbObgcbC9WxUcrTgECgYEA/Yj8BDfxcsPK5ebE
9N2teBFUJuDcHEuM1xp4/tFisoFH90JZJMkVbO19rddAMmdYLTGivWTyPVsM1+9s
tq/NwsFJWHRUiMK7dttGiXuZry+xvq/SAZoitgI8tXdDXMw7368vatr0g6m7ucBK
jZWxSHjK9/KVquVr7BoXFm+YxaECgYEAr3sgVNbr5ovx17YriTqe1FLTLMD5gPrz
ugJj7nypDYY59hLlkrA/TtWbfzE+vfrN3oRIz5OMi9iFk3KXFVJMjGg+M5eO9Y8m
14e791/q1jUuuUH4mc6HttNRNh7TdLg/OGKivE+56LEyFPir45zw/dqwQM3jiwIz
yPz/+bzmfTECgYATxrOhwJtc0FjrReznDMOTMgbWYYPJ0TrTLIVzmvGP6vWqG8rI
S8cYEA5VmQyw4c7G97AyBcW/c3K1BT/9oAj0wA7wj2JoqIfm5YPDBZkfSSEcNqqy
5Ur/13zUytC+VE/3SrrwItQf0QWLn6wxDxQdCw8J+CokgnDAoehbH6lTAQKBgQCE
67T/zpR9279i8CBmIDszBVHkcoALzQtU+H6NpWvATM4WsRWoWUx7AJ56Z+joqtPK
G1WztkYdn/L+TyxWADLvn/6Nwd2N79MyKyScKtGNVFeCCJCwoJp4R/UaE5uErBNn
OH+gOJvPwHj5HavGC5kYENC1Jb+YCiEDu3CB0S6d4QKBgQDGYGEFMZYWqO6+LrfQ
ZNDBLCI2G4+UFP+8ZEuBKy5NkDVqXQhHRbqr9S/OkFu+kEjHLuYSpQsclh6XSDks
5x/hQJNQszLPJoxvGECvz5TN2lJhuyCupS50aGKGqTxKYtiPHpWa8jZyjmanMKnE
dOGyw/X4SFyodv8AEloqd81yGg==
-----END PRIVATE KEY-----`;

function nowText() {
    const d = new Date();
    const pad = n => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function ts13() {
    return Date.now();
}

function parseAccounts(raw) {
    return String(raw || "")
        .split(/[&,，\n\r]+/)
        .map(item => item.trim())
        .filter(Boolean);
}

function maskAccount(account) {
    const text = String(account || "");
    if (text.length <= 4) return `${text.slice(0, 1)}***`;
    if (text.length <= 10) return `${text.slice(0, 2)}***${text.slice(-2)}`;
    return `${text.slice(0, 4)}***${text.slice(-4)}`;
}

function safeJson(data) {
    try {
        return JSON.stringify(data).slice(0, 300);
    } catch (_) {
        return String(data).slice(0, 300);
    }
}

function getSHA256withRSA(content) {
    const key = rs.KEYUTIL.getKey(privateKeyString);
    const sig = new rs.KJUR.crypto.Signature({ alg: "SHA256withRSA" });
    sig.init(key);
    sig.updateString(content);
    return rs.hextob64u(sig.sign());
}

function extractWxCode(data) {
    if (typeof data === "string" && data.trim()) return data.trim();

    const candidates = [
        data?.code,
        data?.wx_code,
        data?.wxCode,
        data?.data?.code,
        data?.data?.wx_code,
        data?.data?.wxCode,
        data?.data?.data?.code,
        data?.result?.code,
        data?.result?.wx_code,
    ];

    for (const value of candidates) {
        if (typeof value === "string" && value.trim()) return value.trim();
    }

    if (typeof data?.data === "string" && data.data.trim()) return data.data.trim();
    throw new Error(`mywc未返回有效code：${safeJson(data)}`);
}

async function getWxCode(wxid) {
    if (!WX_SERVER_URL) throw new Error("未配置 wx_server_url 或 WX_SERVER_URL");

    const { data } = await axios.get(`${WX_SERVER_URL}/mywc`, {
        params: { wxid, appId: MINI_APP_ID },
        headers: { auth: wxid },
        timeout: 15000,
    });

    return extractWxCode(data);
}

async function sendNativeNotify(title, content) {
    let pushed = false;

    if (QYWX_KEY) {
        try {
            await axios.post(`https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=${QYWX_KEY}`, {
                msgtype: "text",
                text: { content: `${title}\n\n${content}` },
            }, { timeout: 10000 });
            console.log("✅ 企业微信通知推送成功");
            pushed = true;
        } catch (e) {
            console.log(`❌ 企业微信通知推送失败：${e.message}`);
        }
    }

    if (PUSH_PLUS_TOKEN) {
        try {
            await axios.post("https://www.pushplus.plus/send", {
                token: PUSH_PLUS_TOKEN,
                title,
                content,
                template: "txt",
            }, { timeout: 10000 });
            console.log("✅ PushPlus通知推送成功");
            pushed = true;
        } catch (e) {
            console.log(`❌ PushPlus通知推送失败：${e.message}`);
        }
    }

    if (!pushed) console.log("⚠️ 未配置 QYWX_KEY / PUSH_PLUS_TOKEN / PLUSPLUS_TOKEN，跳过通知推送");
}

function buildNotifyReport() {
    const total = GLOBAL_NOTIFY_BUFFERS.length;
    const success = GLOBAL_NOTIFY_BUFFERS.filter(item => item.ok).length;
    const failed = total - success;
    const totalGain = GLOBAL_NOTIFY_BUFFERS.reduce((sum, item) => sum + Number(item.gain || 0), 0);

    const lines = [
        "==============================",
        `🕒 执行时间：${nowText()}`,
        `📊 统计数据：成功 ${success} / 总计 ${total}`,
        `✅ 成功账号：${success} 个`,
        `❌ 失败账号：${failed} 个`,
        `💰 累计雪王币：+${totalGain}`,
        "==============================",
    ];

    for (const item of GLOBAL_NOTIFY_BUFFERS) {
        const accountIcon = item.ok ? "🧑‍💻" : "🧟";
        lines.push(`${accountIcon} 【账号${item.index}】${item.account}`);
        lines.push(`${item.ok ? "✅" : "❌"} 状态：${item.status}`);

        if (item.ok) {
            lines.push(`💎 雪王币：始 ${item.before} ➔ 终 ${item.after}，获得 +${item.gain}`);
            if (item.message) lines.push(`📌 任务：${item.message}`);
        } else {
            lines.push(`🧨 原因：${item.message || "未知错误"}`);
        }
        lines.push("------------------------------");
    }

    return lines.join("\n");
}

async function dispatchNotify() {
    if (!GLOBAL_NOTIFY_BUFFERS.length) {
        GLOBAL_NOTIFY_BUFFERS.push({
            index: 1,
            account: "未获取到账号",
            ok: false,
            status: "配置错误",
            before: 0,
            after: 0,
            gain: 0,
            message: "未生成任何账号执行结果",
        });
    }

    await sendNativeNotify(`${SCRIPT_TITLE}任务执行结果`, buildNotifyReport());
}

async function getUserPoint(token) {
    const t = ts13();
    const sign = getSHA256withRSA(`appId=${APP_ID}&t=${t}`);
    const { data } = await axios.get("https://mxsa.mxbc.net/api/v1/customer/info", {
        params: { t, appId: APP_ID, sign },
        headers: {
            "Access-Token": token,
            version: "2.8.27",
            "User-Agent": UA,
        },
        timeout: 10000,
    });

    if (data?.code !== 0) throw new Error(`查询雪王币失败：${data?.msg || safeJson(data)}`);
    return Number.parseInt(data?.data?.customerPoint ?? 0, 10) || 0;
}

async function doMagicShop(token) {
    const t = ts13();
    const sign = getSHA256withRSA(`appId=${APP_ID}&t=${t}`);
    const { data } = await axios.get("https://mxsa.mxbc.net/api/v1/duiba/getLoginUrl", {
        params: { appId: APP_ID, t, sign, dbredirect: "" },
        headers: {
            "Access-Token": token,
            version: "2.8.27",
            "User-Agent": UA,
        },
        timeout: 10000,
    });

    if (data?.code !== 0) throw new Error(`访问魔法铺失败：${data?.msg || safeJson(data)}`);
    return true;
}

async function code2Session(code) {
    const t = ts13();
    const { data } = await axios.post("https://mxsa.mxbc.net/api/v1/app/code2Session", {
        code,
        miniAppId: MINI_APP_ID,
        t,
        appId: APP_ID,
        sign: getSHA256withRSA(`appId=${APP_ID}&code=${code}&miniAppId=${MINI_APP_ID}&t=${t}`),
    }, {
        headers: { version: "2.8.27" },
        timeout: 15000,
    });

    const openid = data?.data?.openid;
    const unionid = data?.data?.unionid;
    if (!openid || !unionid) throw new Error(`code2Session失败：${data?.msg || safeJson(data)}`);
    return { openid, unionid };
}

async function loginByAuthCode(code, openid, unionid) {
    const t = ts13();
    const { data } = await axios.post("https://mxsa.mxbc.net/api/v2/app/loginByAuthCode", {
        authCode: code,
        openId: openid,
        unionid,
        third: "wxmini",
        miniAppId: MINI_APP_ID,
        t,
        appId: APP_ID,
        sign: getSHA256withRSA(`appId=${APP_ID}&authCode=${code}&miniAppId=${MINI_APP_ID}&openId=${openid}&t=${t}&third=wxmini&unionid=${unionid}`),
    }, {
        headers: { version: "2.8.27", "x-ssos-cid": unionid },
        timeout: 15000,
    });

    const token = data?.data?.accessToken;
    if (!token) throw new Error(`登录失败：${data?.msg || safeJson(data)}`);
    return token;
}

async function runAccount(wxid, index) {
    const result = {
        index,
        account: maskAccount(wxid),
        ok: false,
        status: "执行失败",
        before: 0,
        after: 0,
        gain: 0,
        message: "",
    };

    console.log(`\n==============================`);
    console.log(`${SCRIPT_TITLE} - 账号${index} ${result.account}`);
    console.log(`==============================`);

    try {
        const code = await getWxCode(wxid);
        console.log("✅ 获取微信code成功");

        const { openid, unionid } = await code2Session(code);
        const token = await loginByAuthCode(code, openid, unionid);
        console.log("✅ 登录成功");

        result.before = await getUserPoint(token);
        console.log(`💎 执行前雪王币：${result.before}`);

        await doMagicShop(token);
        await sleep(1500);

        result.after = await getUserPoint(token);
        result.gain = Math.max(0, result.after - result.before);
        result.ok = true;
        result.status = "执行成功";
        result.message = "魔法铺访问完成";

        console.log(`✅ 本次获得：${result.gain} 雪王币`);
        console.log(`💎 执行后雪王币：${result.after}`);
    } catch (e) {
        result.message = e?.message || String(e);
        console.log(`❌ 账号${index}执行失败：${result.message}`);
    } finally {
        GLOBAL_NOTIFY_BUFFERS.push(result);
    }
}

async function run() {
    const accounts = parseAccounts(ACCOUNT_RAW);

    if (!accounts.length) {
        GLOBAL_NOTIFY_BUFFERS.push({
            index: 1,
            account: "未配置",
            ok: false,
            status: "配置错误",
            before: 0,
            after: 0,
            gain: 0,
            message: "请配置 mixue_wxid 或 MIXUE_WXID，多个账号用 &、英文逗号、中文逗号或换行分隔",
        });
        return;
    }

    if (!WX_SERVER_URL) {
        accounts.forEach((account, idx) => {
            GLOBAL_NOTIFY_BUFFERS.push({
                index: idx + 1,
                account: maskAccount(account),
                ok: false,
                status: "配置错误",
                before: 0,
                after: 0,
                gain: 0,
                message: "请配置 wx_server_url 或 WX_SERVER_URL",
            });
        });
        return;
    }

    for (const [idx, account] of accounts.entries()) {
        await runAccount(account, idx + 1);
        if (idx < accounts.length - 1) await sleep(2000);
    }
}

(async () => {
    console.log(`🚀 ${SCRIPT_TITLE} 魔法铺多账号任务`);
    try {
        await run();
    } catch (e) {
        GLOBAL_NOTIFY_BUFFERS.push({
            index: GLOBAL_NOTIFY_BUFFERS.length + 1,
            account: "全局异常",
            ok: false,
            status: "执行失败",
            before: 0,
            after: 0,
            gain: 0,
            message: e?.message || String(e),
        });
        console.log(`❌ 全局异常：${e?.message || e}`);
    } finally {
        await dispatchNotify();
        console.log("\n🏁 所有账号任务执行完成");
    }
})();
