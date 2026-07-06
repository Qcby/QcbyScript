/*
可口可乐 v1.1.0（mywc网关聚合推送版）

功能：自动执行可口可乐小程序签到/资产查询，支持多账号执行，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL   必填其一，自建授权服务器域名
   - 示例：http://127.0.0.1:8110
   - 脚本会自动拼接 /mywc
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=wxa5811e0426a94686
   - 请求头：auth=账号标识

2. 账号变量：
   coke_wxid 或 COKE_WXID                         推荐，可口可乐专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：wxid_a&wxid_b&openida 或 wxid_a,wxid_b,openida

3. 推送变量：
   JS 脚本内置 axios 企业微信机器人聚合推送，配置 QYWX_KEY 即可。
   QYWX_KEY                                         企业微信机器人 key

4. 青龙任务建议：
   名称：可口可乐签到
   命令：node 可口可乐.js
   定时：每天运行 1 次即可，具体时间自行调整
*/
const axios = require("axios");
const { SocksProxyAgent } = require("socks-proxy-agent");
const { HttpsProxyAgent } = require("https-proxy-agent");
const { HttpProxyAgent } = require("http-proxy-agent");

delete process.env.HTTP_PROXY;
delete process.env.HTTPS_PROXY;
delete process.env.http_proxy;
delete process.env.https_proxy;

// ============ 核心变量获取 ============
const APPID = "wxa5811e0426a94686";
const COKE_WXID_RAW = process.env.coke_wxid || process.env.COKE_WXID || "";
const WX_SERVER_URL = process.env.wx_server_url || process.env.WX_SERVER_URL || "";
const QYWX_KEY = process.env.QYWX_KEY || "";

const PROXY_API = process.env.PROXY_API || "";
const PROXY_TYPE = (process.env.PROXY_TYPE || "http").toLowerCase();
const PROXY_RETRY_TIMES = 3;
const PROXY_VALIDATE_URL = "http://httpbin.org/ip";
const PROXY_FETCH_INTERVAL = 3000;
const ENABLE_DIRECT_FALLBACK = true;
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf2541923) XWEB/19823";

// 全局精简数据缓存
const GLOBAL_NOTIFY_BUFFERS = [];

// ── 核心痛点：纯原生免依赖跨通道推送核心 ──
async function sendNativeNotify(title, content) {
    if (!QYWX_KEY) {
        console.log("⚠️ [通知] 未配置环境变量 QYWX_KEY，跳过企业微信机器人推送");
        return;
    }
    const qywxUrl = `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=${QYWX_KEY}`;
    try {
        const res = await axios.post(qywxUrl, {
            msgtype: "text",
            text: {
                content: `${title}\n\n${content}`
            }
        }, { timeout: 15000, proxy: false });
        
        if (res.data?.errcode === 0) {
            console.log("✅ [企业微信] 原生渠道通知发送成功！");
        } else {
            console.log(`❌ [企业微信] 通知发送失败: ${JSON.stringify(res.data)}`);
        }
    } catch (err) {
        console.log(`❌ [企业微信] 通知发送异常: ${err.message}`);
    }
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function random(min, max) {
    return Math.floor(Math.random() * (max - min + 1)) + min;
}

function parseAccounts(raw) {
    return String(raw || "").replace(/，/g, ",").replace(/,/g, "&").replace(/&/g, "\n").split("\n").map(i => i.trim()).filter(Boolean);
}

function mask(value) {
    value = String(value || "");
    if (value.length <= 12) return value;
    return `${value.slice(0, 6)}...${value.slice(-6)}`;
}

function parseProxyResponse(text) {
    if (typeof text !== "string") text = JSON.stringify(text);
    text = text.trim();
    if (!text) return null;
    try {
        const data = JSON.parse(text);
        let proxy_obj = null;
        if (data.data && Array.isArray(data.data) && data.data.length > 0) proxy_obj = data.data[0];
        else if (data.data && typeof data.data === "object") proxy_obj = data.data;
        else if (data.ip && data.port) proxy_obj = data;
        else if (data.result && data.result.ip && data.result.port) proxy_obj = data.result;

        if (proxy_obj) {
            return {
                host: proxy_obj.ip || proxy_obj.host,
                port: proxy_obj.port,
                username: proxy_obj.user || proxy_obj.username || "",
                password: proxy_obj.pass || proxy_obj.password || ""
            };
        }
    } catch (e) {}
    if (text.includes(":")) {
        const parts = text.split(":");
        if (parts.length >= 2) {
            return { host: parts[0], port: Number(parts[1]), username: parts[2] || "", password: parts[3] || "" };
        }
    }
    return null;
}

// 变量的注释标注应该是 .xx变量 xxx, xx, , , ' <--- xxxx 而不是 .xx变量 xx, xx ' <--- xxxx
function buildProxyAgent(proxyInfo) {
    if (!proxyInfo) return null;
    const { host, port, username, password } = proxyInfo;
    let auth = (username && password) ? `${encodeURIComponent(username)}:${encodeURIComponent(password)}@` : "";
    try {
        if (PROXY_TYPE === "socks5") {
            const proxyUrl = `socks5://${auth}${host}:${port}`;
            return { httpAgent: new SocksProxyAgent(proxyUrl), httpsAgent: new SocksProxyAgent(proxyUrl) };
        }
        const proxyUrl = `http://${auth}${host}:${port}`;
        return { httpAgent: new HttpProxyAgent(proxyUrl), httpsAgent: new HttpsProxyAgent(proxyUrl) };
    } catch (e) {
        return null;
    }
}

async function validateProxy(agent) {
    if (!agent) return { ok: false, ip: "" };
    try {
        const res = await axios({ method: "get", url: PROXY_VALIDATE_URL, timeout: 15000, ...agent });
        if (res.status === 200) return { ok: true, ip: res.data?.origin || "未知" };
    } catch (e) {}
    return { ok: false, ip: "" };
}

async function getValidProxy(wxid) {
    if (!PROXY_API) return { agent: null, ip: "" };
    for (let i = 1; i <= PROXY_RETRY_TIMES; i++) {
        try {
            const res = await axios.get(PROXY_API, { timeout: 15000, proxy: false });
            const proxyInfo = parseProxyResponse(res.data);
            if (!proxyInfo) continue;
            const agent = buildProxyAgent(proxyInfo);
            const valid = await validateProxy(agent);
            if (valid.ok) return { agent, ip: valid.ip };
        } catch (e) {}
        if (i < PROXY_RETRY_TIMES) await sleep(2000);
    }
    return { agent: null, ip: "" };
}

async function requestWithProxy(config, proxyAgent) {
    if (proxyAgent) {
        try { return await axios({ timeout: 30000, ...config, ...proxyAgent }); } 
        catch (e) { if (!ENABLE_DIRECT_FALLBACK) throw e; }
    }
    return await axios({ timeout: 30000, proxy: false, ...config });
}

async function getCode(wxid) {
    if (!WX_SERVER_URL) {
        console.log("❌ [授权] 未配置环境变量 wx_server_url");
        return null;
    }
    const baseUrl = WX_SERVER_URL.trim().replace(/\/$/, '');
    const url = `${baseUrl}/mywc`;
    try {
        const res = await axios.get(url, {
            params: { wxid: wxid, appId: APPID },
            headers: { auth: wxid },
            timeout: 20000,
            proxy: false
        });
        if (res.data?.status === "ok" && res.data?.code) {
            return res.data.code;
        }
        console.log(`❌ [授权] code 获取失败: ${JSON.stringify(res.data)}`);
        return null;
    } catch (e) {
        console.log(`❌ [授权] code 获取异常: ${e.message}`);
        return null;
    }
}

async function getUserToken(code, proxyAgent) {
    const config = {
        method: "GET",
        url: `https://member-api.icoke.cn/api/sp-portal/store/icoke/wechat/loginNoCache/${code}`,
        headers: {
            "User-Agent": UA, "Accept": "application/json, text/plain, */*", "xweb_xhr": "1", "Content-Type": "application/json",
            "Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Dest": "empty",
            "Referer": "https://servicewechat.com/wxa5811e0426a94686/496/page-frame.html", "Accept-Language": "zh-CN,zh;q=0.9"
        }
    };
    try {
        const { data } = await requestWithProxy(config, proxyAgent);
        if (data?.jwtString) return { token: data.jwtString, raw: data };
        return { token: null, raw: data };
    } catch (e) {
        return { token: null, raw: null };
    }
}

async function getUserInfo(token, proxyAgent) {
    const config = {
        method: "GET",
        url: "https://member-api.icoke.cn/api/icoke-customer/icoke/mini/customer/main/points",
        headers: {
            "accept": "application/json, text/plain, */*", "accept-language": "zh-CN,zh;q=0.9", "authorization": token, "content-type": "application/json",
            "sec-fetch-dest": "empty", "sec-fetch-mode": "cors", "sec-fetch-site": "cross-site", "xweb_xhr": "1",
            "Referer": "https://servicewechat.com/wxa5811e0426a94686/421/page-frame.html", "Referrer-Policy": "unsafe-url"
        }
    };
    try {
        const { data } = await requestWithProxy(config, proxyAgent);
        return data;
    } catch (e) {
        return null;
    }
}

async function addSign(token, proxyAgent) {
    const config = {
        method: "GET",
        url: "https://member-api.icoke.cn/api/icoke-sign/icoke/mini/sign/main/sign",
        headers: {
            "accept": "application/json, text/plain, */*", "accept-language": "zh-CN,zh;q=0.9", "authorization": token, "content-type": "application/json",
            "sec-fetch-dest": "empty", "sec-fetch-mode": "cors", "sec-fetch-site": "cross-site", "xweb_xhr": "1",
            "Referer": "https://servicewechat.com/wxa5811e0426a94686/421/page-frame.html", "Referrer-Policy": "unsafe-url"
        }
    };
    try {
        const { data } = await requestWithProxy(config, proxyAgent);
        if (data?.success === true) {
            return { success: true, message: `✅ 成功 +${data.point ?? "-"} 快乐瓶`, raw: data };
        }
        const msg = data?.message || data?.msg || "已签到/失败";
        return { success: false, message: msg.includes("已") ? "⚠️ 今日已签到过" : `❌ 失败 (${msg})`, raw: data };
    } catch (e) {
        return { success: false, message: `❌ 异常 (${e.message})`, raw: null };
    }
}

async function runAccount(wxid) {
    console.log(`\n🔄 正在处理微信号: ${wxid}`);
    const summary = { wxid, before: "-", after: "-", earned: "0", msg: "未执行" };

    const proxy = await getValidProxy(wxid);
    const proxyAgent = proxy.agent;

    await sleep(PROXY_FETCH_INTERVAL);
    const delay = random(500, 1000);
    await sleep(delay);

    const code = await getCode(wxid);
    if (!code) {
        summary["msg"] = "❌ 获取 code 失败";
        GLOBAL_NOTIFY_BUFFERS.push(summary);
        return;
    }

    const login = await getUserToken(code, proxyAgent);
    if (!login.token) {
        summary["msg"] = "❌ 获取 token 失败";
        GLOBAL_NOTIFY_BUFFERS.push(summary);
        return;
    }

    const beforeInfo = await getUserInfo(login.token, proxyAgent);
    summary.before = beforeInfo?.point ?? "-";
    summary.after = beforeInfo?.point ?? "-";
    await sleep(random(1000, 2000));

    const sign = await addSign(login.token, proxyAgent);
    summary.msg = sign.message;
    
    if (sign.success) {
        await sleep(random(1000, 2000));
        const afterInfo = await getUserInfo(login.token, proxyAgent);
        summary.after = afterInfo?.point ?? "-";
        summary.earned = String((afterInfo?.point ?? 0) - (beforeInfo?.point ?? 0));
    }

    GLOBAL_NOTIFY_BUFFERS.push(summary);
}

// ============ 主入口程序 ============
(async () => {
    console.log("==================================================");
    console.log("🥤 可口可乐小程序纯 WXID 聚合精简版启动...");
    console.log("==================================================");

    if (!COKE_WXID_RAW) {
        console.log("❌ 未找到有效 coke_wxid 账户配置！");
        return;
    }

    const wxids = parseAccounts(COKE_WXID_RAW);
    console.log(`📱 共加载 ${wxids.length} 个可口可乐账户`);

    for (const wxid of wxids) {
        try {
            await runAccount(wxid);
            await sleep(random(2000, 4000));
        } catch (e) {
            console.log(`❌ 账户 ${wxid} 发生错误: ${e.message}`);
        }
    }

    // 聚合提炼：多账号任务结束，生成高可读性精简报表并发送单次推送
    if (GLOBAL_NOTIFY_BUFFERS) {
        const title = "🔔 可口可乐任务执行总结";
        const success = GLOBAL_NOTIFY_BUFFERS.filter(i => !i.msg.startsWith('❌')).length;
        const failed = wxids.length - success;
        const desp_lines = [
            "==============================",
            `🕒 执行时间：${new Date().toLocaleString("zh-CN", { hour12: false })}`,
            `📊 统计数据：成功 ${success} / 总计 ${wxids.length}`,
            `✅ 成功账号：${success} 个`,
            `❌ 失败账号：${failed} 个`,
            "=============================="
        ];

        for (const item of GLOBAL_NOTIFY_BUFFERS) {
            const ok = !item.msg.startsWith("❌");
            desp_lines.push(`${ok ? "🧑‍💻" : "🧟"} 【${mask(item.wxid)}】`);
            desp_lines.push(`${ok ? "✅" : "❌"} 状态：${item.msg}`);
            desp_lines.push(`💰 快乐瓶：始 ${item.before} ➔ 终 ${item.after}，获得 +${item.earned}`);
            desp_lines.push("------------------------------");
        }

        const final_desp = desp_lines.join("\n");
        console.log("\n[精简推送报表阅览]\n" + final_desp);
        
        // 精准直击痛点：采用纯原生独立分发通道，必能送达
        await sendNativeNotify(title, final_desp);
    }
})().catch(e => {
    console.log(`❌ [全局异常] ${e.message}`);
});