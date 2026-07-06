/*
美的会员 v1.1.0（mywc网关聚合推送版）

功能：自动执行美的会员小程序每日签到和游戏签到，支持多账号执行，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL   必填其一，自建授权服务器域名
   - 示例：http://127.0.0.1:8110
   - 脚本会自动拼接 /mywc
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=wx49a622805968d156
   - 请求头：auth=账号标识

2. 账号变量：
   midea_wxid 或 MIDEA_WXID                     推荐，美的会员专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：wxid_a&wxid_b&openida 或 wxid_a,wxid_b,openida

3. 推送变量：
   JS 脚本内置 axios 企业微信机器人聚合推送，配置 QYWX_KEY 即可。
   QYWX_KEY                                         企业微信机器人 key

4. 青龙任务建议：
   名称：美的会员签到
   命令：node 美的会员.js
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
const APPID = "wx49a622805968d156";
const MIDEA_WXID_RAW = process.env.midea_wxid || process.env.MIDEA_WXID || "";
const WX_SERVER_URL = process.env.wx_server_url || process.env.WX_SERVER_URL || "";
const QYWX_KEY = process.env.QYWX_KEY || "";

const PROXY_API = process.env.PROXY_API || "";
const PROXY_TYPE = (process.env.PROXY_TYPE || "http").toLowerCase();
const PROXY_RETRY_TIMES = 3;
const PROXY_VALIDATE_URL = "http://httpbin.org/ip";
const PROXY_FETCH_INTERVAL = 3000;
const ENABLE_DIRECT_FALLBACK = true;
const REQUEST_TIMEOUT = 30000;
const LOGIN_APP_ID = "ee07f27990db48109efcccd322d3a873";
const LOGIN_APP_SECRET = "2646746f07bb46199aff49002e6dce81";
const LOGIN_API_KEY = "b6db9d5cf2d449538d3a0dd5d77b2e35";
const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF WindowsWechat(0x63090a13) UnifiedPCWindowsWechat(0xf2541938) XWEB/19823";

// 全局精简数据缓存
const GLOBAL_NOTIFY_BUFFERS = [];

// ── 原生免依赖跨通道推送核心 ──
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

function preview(value, limit = 800) {
    try {
        return JSON.stringify(value).slice(0, limit);
    } catch (e) {
        return String(value).slice(0, limit);
    }
}

function parseProxyResponse(text) {
    if (typeof text !== "string") text = JSON.stringify(text);
    text = text.trim();
    if (!text) return null;

    try {
        const data = JSON.parse(text);
        let proxyObj = null;

        if (data.data && Array.isArray(data.data) && data.data.length > 0) {
            proxyObj = data.data[0];
        } else if (data.data && typeof data.data === "object") {
            proxyObj = data.data;
        } else if (data.ip && data.port) {
            proxyObj = data;
        } else if (data.result && data.result.ip && data.result.port) {
            proxyObj = data.result;
        }

        if (proxyObj) {
            return {
                host: proxyObj.ip || proxyObj.host,
                port: proxyObj.port,
                username: proxyObj.user || proxyObj.username || "",
                password: proxyObj.pass || proxyObj.password || "",
            };
        }
    } catch (e) {}

    if (text.includes(":")) {
        const parts = text.split(":");
        if (parts.length >= 2) {
            return {
                host: parts[0],
                port: Number(parts[1]),
                username: parts[2] || "",
                password: parts[3] || "",
            };
        }
    }

    return null;
}

function buildProxyAgent(proxyInfo) {
    if (!proxyInfo) return null;
    const { host, port, username, password } = proxyInfo;
    let auth = "";
    if (username && password) {
        auth = `${encodeURIComponent(username)}:${encodeURIComponent(password)}@`;
    }

    try {
        if (PROXY_TYPE === "socks5") {
            const proxyUrl = `socks5://${auth}${host}:${port}`;
            return {
                httpAgent: new SocksProxyAgent(proxyUrl),
                httpsAgent: new SocksProxyAgent(proxyUrl),
                proxy: false,
            };
        }

        const proxyUrl = `http://${auth}${host}:${port}`;
        return {
            httpAgent: new HttpProxyAgent(proxyUrl),
            httpsAgent: new HttpsProxyAgent(proxyUrl),
            proxy: false,
        };
    } catch (e) {
        return null;
    }
}

async function validateProxy(agent) {
    if (!agent) return { ok: false, ip: "" };
    try {
        const res = await axios({
            method: "get",
            url: PROXY_VALIDATE_URL,
            timeout: 15000,
            ...agent,
        });
        if (res.status === 200) {
            const ip = res.data?.origin || "未知";
            return { ok: true, ip };
        }
    } catch (e) {}

    return { ok: false, ip: "" };
}

async function getValidProxy(wxid) {
    if (!PROXY_API) return { agent: null, ip: "" };

    for (let i = 1; i <= PROXY_RETRY_TIMES; i++) {
        try {
            const res = await axios.get(PROXY_API, {
                timeout: 15000,
                proxy: false,
            });
            const proxyInfo = parseProxyResponse(res.data);

            if (!proxyInfo) continue;

            const agent = buildProxyAgent(proxyInfo);
            const valid = await validateProxy(agent);

            if (valid.ok) {
                return { agent, ip: valid.ip };
            }
        } catch (e) {}

        if (i < PROXY_RETRY_TIMES) {
            await sleep(2000);
        }
    }

    return { agent: null, ip: "" };
}

async function requestWithProxy(config, proxyAgent) {
    if (proxyAgent) {
        try {
            return await axios({
                timeout: REQUEST_TIMEOUT,
                ...config,
                ...proxyAgent,
            });
        } catch (e) {
            if (!ENABLE_DIRECT_FALLBACK) {
                throw e;
            }
        }
    }

    return await axios({
        timeout: REQUEST_TIMEOUT,
        proxy: false,
        ...config,
    });
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
            proxy: false,
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

// ── 痛点修复：模糊/忽略大小写深度提取接口字段 ──
function findValueDeep(obj, keys) {
    if (!obj || typeof obj !== "object") return null;
    const lowerKeys = keys.map(k => k.toLowerCase());
    
    // 优先遍历当前层
    for (const [key, value] of Object.entries(obj)) {
        if (lowerKeys.includes(key.toLowerCase()) && value !== undefined && value !== null && value !== "") {
            return value;
        }
    }
    // 递归子层
    for (const value of Object.values(obj)) {
        if (value && typeof value === "object") {
            const found = findValueDeep(value, keys);
            if (found) return found;
        }
    }
    return null;
}

function extractCookies(headers) {
    const setCookie = headers?.["set-cookie"];
    if (!setCookie) return "";

    const arr = Array.isArray(setCookie) ? setCookie : [setCookie];
    const parts = [];
    for (const item of arr) {
        const first = String(item).split(";")[0];
        if (/^(uid|sukey)=/i.test(first)) {
            parts.push(first);
        }
    }
    return parts.length ? parts.join("; ") + ";" : "";
}

function extractLoginInfo(data, headers) {
    const ucAccessToken = findValueDeep(data, ["ucAccessToken", "accessToken", "token", "userToken", "access_token"]);
    let uid = findValueDeep(data, ["uid", "userId", "userCode", "uidCookie"]);
    let sukey = findValueDeep(data, ["sukey", "suKey", "sukeyCookie"]);

    const cookieFromHeader = extractCookies(headers);
    let cookie = cookieFromHeader;

    // 兜底策略：如果 Response Headers 没返回 set-cookie，直接拿 body 里的字段强制拼装
    if (!cookie && uid && sukey) {
        cookie = `uid=${uid}; sukey=${sukey};`;
    }

    return {
        ucAccessToken: ucAccessToken ? String(ucAccessToken) : "",
        cookie,
        uid: uid ? String(uid) : "",
        sukey: sukey ? String(sukey) : "",
    };
}

async function loginByCode(code, proxyAgent) {
    const config = {
        method: "POST",
        url: "https://mcsp.midea.com/api/cms_bff/mcsp-uc-mvip-bff/app/login/wx/mini/getLoginInfo.do",
        headers: {
            Host: "mcsp.midea.com",
            appId: LOGIN_APP_ID,
            xweb_xhr: "1",
            appsecret: LOGIN_APP_SECRET,
            "User-Agent": UA,
            "Content-Type": "application/json",
            userKey: "",
            miniAppVersion: "3.0.269",
            apikey: LOGIN_API_KEY,
            Accept: "*/*",
            Referer: `https://servicewechat.com/${APPID}/554/page-frame.html`,
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
        data: {
            jsCode: code,
            loginMode: 1,
            platformType: "WX_MEIDIDAOJIA_MINI",
            _timeStamp: Date.now(),
        },
    };

    try {
        const res = await requestWithProxy(config, proxyAgent);
        const data = res.data;
        const info = extractLoginInfo(data, res.headers);
        return {
            ...info,
            raw: data,
            headers: res.headers,
        };
    } catch (e) {
        return { ucAccessToken: "", cookie: "", uid: "", sukey: "", raw: null, headers: null };
    }
}

async function getUserInfo(cookie, proxyAgent) {
    const config = {
        method: "GET",
        url: "https://mvip.midea.cn/next/mucuserinfo/getmucuserinfo",
        headers: {
            Host: "mvip.midea.cn",
            Connection: "keep-alive",
            charset: "utf-8",
            cookie,
            "User-Agent": UA,
            "Content-Type": "application/json",
            Referer: "https://servicewechat.com/wx03925a39ca94b161/409/page-frame.html",
        },
    };
    try {
        const { data } = await requestWithProxy(config, proxyAgent);
        if (data?.errcode === 0) {
            const mobile = data?.data?.userinfo?.Mobile || "-";
            const points = data?.data?.userinfo?.VipGrow ?? "-";
            return { success: true, mobile, points, raw: data };
        }
        return { success: false, mobile: "-", points: "-", raw: data };
    } catch (e) {
        return { success: false, mobile: "-", points: "-", raw: null };
    }
}

async function signIn(cookie, proxyAgent) {
    if (!cookie) return { success: false, message: "⚠️ 跳过签到1(无Cookie)" };

    const config = {
        method: "GET",
        url: "https://mvip.midea.cn/my/score/create_daily_score",
        headers: {
            "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
            cookie,
            "User-Agent": UA,
            Referer: "https://servicewechat.com/wx03925a39ca94b161/409/page-frame.html",
        },
    };
    try {
        const { data } = await requestWithProxy(config, proxyAgent);
        if (data?.errcode === 0) return { success: true, message: "✅ 成功", raw: data };
        const msg = data?.errmsg || data?.msg || preview(data);
        return { success: false, message: msg.includes("已") ? "⚠️ 今日已签到过" : `❌ 失败 (${msg})`, raw: data };
    } catch (e) {
        return { success: false, message: `❌ 异常 (${e.message})`, raw: null };
    }
}

async function signIn2(ucAccessToken, proxyAgent) {
    if (!ucAccessToken) return { success: false, message: "⚠️ 跳过签到2(无Token)" };

    const config = {
        method: "POST",
        url: "https://mvip.midea.cn/mscp_mscp/api/cms_api/activity-center-im-service/im-svr/im/game/page/sign",
        headers: {
            "User-Agent": UA, Accept: "application/json, text/plain, */*", "Content-Type": "application/json",
            ucAccessToken, intercept: "1", apiKey: "3660663068894a0d9fea574c2673f3c0",
            Origin: "https://mvip.midea.cn", "X-Requested-With": "com.tencent.mm",
            Referer: "https://mvip.midea.cn/mscp_weixin/apps/h5-pro-wx-interaction-marketing/", "Accept-Language": "zh-CN,zh;q=0.9",
        },
        data: {
            headParams: { language: "CN", originSystem: "MCSP", timeZone: "", userCode: "", tenantCode: "", userKey: "TEST_", transactionId: "" },
            pagination: null,
            restParams: { gameId: 22, actvId: "401671388248692763", rootCode: "MDHY", appCode: "MDHY_XCX", imUserId: "", uid: "", openId: "", unionId: "" },
        },
    };
    try {
        const { data } = await requestWithProxy(config, proxyAgent);
        
        // ── 核心痛点修复：完美兼容“操作成功”或 success 状态返回，防止误判失败 ──
        if (data?.code === "0" || data?.success === true || (data?.msg && data.msg.includes("操作成功"))) {
            return { success: true, message: "✅ 成功", raw: data };
        }
        const errMsg = data?.msg || data?.message || "未知";
        return { success: false, message: errMsg.includes("已") ? "⚠️ 今日已签到过" : `❌ 失败 (${errMsg})`, raw: data };
    } catch (e) {
        return { success: false, message: `❌ 异常 (${e.message})`, raw: null };
    }
}

async function runAccount(wxid) {
    console.log(`\n🔄 正在处理微信号: ${wxid}`);
    const summary = { wxid, mobile: "-", before: "-", after: "-", sign1: "未执行", sign2: "未执行" };

    const proxy = await getValidProxy(wxid);
    const proxyAgent = proxy.agent;

    await sleep(PROXY_FETCH_INTERVAL);
    await sleep(random(1000, 3000));

    const code = await getCode(wxid);
    if (!code) {
        summary["sign1"] = "❌ 获取 code 失败";
        GLOBAL_NOTIFY_BUFFERS.push(summary);
        return;
    }

    const login = await loginByCode(code, proxyAgent);
    if (!login.cookie && !login.ucAccessToken) {
        summary["sign1"] = "❌ 换绑账户失败";
        GLOBAL_NOTIFY_BUFFERS.push(summary);
        return;
    }

    if (login.cookie) {
        const before = await getUserInfo(login.cookie, proxyAgent);
        summary.mobile = before.mobile;
        summary.before = before.points;
        summary.after = before.points;
    }

    await sleep(random(1500, 3000));
    const s1 = await signIn(login.cookie, proxyAgent);
    summary.sign1 = s1.message;

    await sleep(random(1500, 3000));
    const s2 = await signIn2(login.ucAccessToken, proxyAgent);
    summary.sign2 = s2.message;

    if (login.cookie && (s1.success || s2.success)) {
        await sleep(random(1500, 3000));
        const after = await getUserInfo(login.cookie, proxyAgent);
        summary.after = after.points;
    }

    GLOBAL_NOTIFY_BUFFERS.push(summary);
}

// ============ 程序入口主逻辑 ============
(async () => {
    console.log("==================================================");
    console.log("🔷 美的会员小程序纯 WXID 聚合精简版启动...");
    console.log("==================================================");

    if (!MIDEA_WXID_RAW) {
        console.log("❌ 未找到有效 midea_wxid 账户配置！");
        return;
    }

    const wxids = parseAccounts(MIDEA_WXID_RAW);
    console.log(`📱 共加载 ${wxids.length} 个美的会员账户`);

    for (const wxid of wxids) {
        try {
            await runAccount(wxid);
            await sleep(random(2000, 4000));
        } catch (e) {
            console.log(`❌ 账户 ${wxid} 发生未知错误: ${e.message}`);
        }
    }

    if (GLOBAL_NOTIFY_BUFFERS.length > 0) {
        const title = "🔔 美的会员任务执行总结";
        const success = GLOBAL_NOTIFY_BUFFERS.filter(i => !String(i.sign1).startsWith('❌') && !String(i.sign2).startsWith('❌')).length;
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
            const ok = !String(item.sign1).startsWith("❌") && !String(item.sign2).startsWith("❌");
            desp_lines.push(`${ok ? "🧑‍💻" : "🧟"} 【${mask(item.wxid)}】手机：${item.mobile}`);
            desp_lines.push(`${ok ? "✅" : "❌"} 每日签到：${item.sign1}`);
            desp_lines.push(`🎮 游戏签到：${item.sign2}`);
            desp_lines.push(`💰 积分：始 ${item.before} ➔ 终 ${item.after}`);
            desp_lines.push("------------------------------");
        }

        const final_desp = desp_lines.join("\n");
        console.log("\n[精简推送报表阅览]\n" + final_desp);
        
        await sendNativeNotify(title, final_desp);
    }
})().catch(e => {
    console.log(`❌ [全局异常] ${e.message}`);
});