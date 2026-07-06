/*
拼多多果园 v1.1.0（mywc网关聚合推送版）

功能：自动执行拼多多果园签到、浇水和任务领取，支持多账号执行，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL   必填其一，自建授权服务器域名
   - 示例：http://127.0.0.1:8110
   - 脚本会自动拼接 /mywc
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=wx32540bd863b27570
   - 请求头：auth=账号标识

2. 账号变量：
   pdd_wxid 或 PDD_WXID                         推荐，拼多多果园专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：wxid_a&wxid_b&openida 或 wxid_a,wxid_b,openida
   - 兼容 openid / openid#token / token；兼容旧变量 pdd_orchard；可选 PDD_COOKIE 跳过微信登录


3. 推送变量：
   JS 脚本内置 axios 企业微信机器人聚合推送，配置 QYWX_KEY 即可。
   QYWX_KEY                                         企业微信机器人 key

4. 青龙任务建议：
   名称：拼多多果园
   命令：node 拼多多果园.js
   定时：每天运行 1 次即可，具体时间自行调整
*/
const axios = require("axios");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

// ============ Env 兼容 (彻底重构，规避外部残缺 env.js 干扰) ============
class SimpleEnv {
    constructor(name) {
        this.name = name;
        this.userIdx = 1;
    }
    log(msg) {
        const t = new Date().toLocaleTimeString("zh-CN");
        console.log(`[${t}] ${msg}`);
    }
    getdata(key) {
        return process.env[key] || process.env[key.toUpperCase()] || "";
    }
    done() {
        this.log(`\n${this.name} 全部执行完毕`);
    }
}

// 强制使用原生沙箱环境，防止外部库方法缺失导致崩溃
const $ = new SimpleEnv("拼多多果园修复版");

// ============ 全局常量（和Python完全对齐） ============
const MINI_APP_ID = "wx32540bd863b27570";
const XCX_ID = "20161201";
const XCX_VERSION = "v8.6.21";
const PDD_APP_ID = 33;
const API_BASE = "https://api.pinduoduo.com";
const ORCHARD_API_BASE = "https://mobile.yangkeduo.com";
const MANOR_BASE = `${ORCHARD_API_BASE}/proxy/api/api`;
const TOKEN_CACHE_FILE = path.join(__dirname, "pdd_orchard_token_cache.json");
const COOKIE_CACHE_FILE = path.join(__dirname, "pdd_cookie_cache.json");

const UA = `Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20.1781(0x6700143B) NetType/WIFI MiniProgramEnv/Windows WindowsWechat/WMPF XWEB/19895 miniProgram/wx32540bd863b27570`;

// ============ 环境变量读取 (原生兼容适配) ============
const RAW_ACCOUNT = $.getdata("pdd_wxid") || $.getdata("PDD_WXID") || $.getdata("pdd_orchard");
const WX_SERVER_URL = $.getdata("wx_server_url") || $.getdata("WX_SERVER_URL");
const QYWX_KEY = $.getdata("QYWX_KEY");
const WX_AUTH = $.getdata("wx_auth"); // 兼容旧变量，mywc 请求实际使用 auth=wxid
const PDD_NO_RELOGIN = ["true", "1"].includes($.getdata("PDD_NO_RELOGIN").toLowerCase());
const DIRECT_COOKIE = $.getdata("PDD_COOKIE");

const wechat = {
    serverUrl: WX_SERVER_URL,
    appid: MINI_APP_ID,
    auth: WX_AUTH || ""
};

// ============ 微信授权接口强制修正拦截器 ============
wechat.getCode = async function(openid) {
    console.log(`[重定向拦截] 正在请求自建授权服务器获取 code, wxid: ${openid}`);
    if (!WX_SERVER_URL) throw new Error("未配置 wx_server_url 或 WX_SERVER_URL");
    const baseUrl = WX_SERVER_URL.trim().replace(/\/$/, "");
    const targetUrl = `${baseUrl}/mywc`;
    
    try {
        const resp = await axios({
            method: "GET",
            url: targetUrl,
            params: {
                wxid: openid,
                appId: this.appid
            },
            headers: { auth: openid },
            timeout: 30000,
            validateStatus: () => true
        });

        // 适配原脚本登录逻辑的解析结构
        return {
            data: {
                status: resp.data?.status === "ok" ? true : false,
                code: resp.data?.code,
                message: resp.data?.msg || "获取失败"
            }
        };
    } catch (err) {
        $.log(`重定向授权请求失败: ${err.message}`);
        throw err;
    }
};


async function sendNativeNotify(title, content) {
    if (!QYWX_KEY) {
        $.log("⚠️ [通知] 未配置 QYWX_KEY，跳过企业微信机器人推送");
        return;
    }
    const qywxUrl = `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=${QYWX_KEY}`;
    try {
        const res = await axios.post(qywxUrl, {
            msgtype: "text",
            text: { content: `${title}

${content}` }
        }, { timeout: 15000, proxy: false });
        $.log(res.data?.errcode === 0 ? "✅ [企业微信] 通知发送成功" : `❌ [企业微信] 通知发送失败: ${JSON.stringify(res.data)}`);
    } catch (err) {
        $.log(`❌ [企业微信] 通知发送异常: ${err.message}`);
    }
}

const GLOBAL_NOTIFY_BUFFERS = [];

function buildNotifyReport() {
    const total = GLOBAL_NOTIFY_BUFFERS.length;
    const success = GLOBAL_NOTIFY_BUFFERS.filter(i => i.ok).length;
    const failed = total - success;
    const lines = [
        "==============================",
        `🕒 执行时间：${new Date().toLocaleString("zh-CN", { hour12: false })}`,
        `📊 统计数据：成功 ${success} / 总计 ${total}`,
        `✅ 成功账号：${success} 个`,
        `❌ 失败账号：${failed} 个`,
        "==============================",
    ];
    for (const item of GLOBAL_NOTIFY_BUFFERS) {
        lines.push(`${item.ok ? "🧑‍💻" : "🧟"} 【账号${item.index}】${item.account}`);
        lines.push(`${item.ok ? "✅" : "❌"} 状态：${item.status}`);
        if (item.ok) lines.push(`💧 最终水滴：${item.finalWater ?? "-"}`);
        else lines.push(`🧨 原因：${item.message}`);
        lines.push("------------------------------");
    }
    return lines.join("\n");
}

async function dispatchNotify() {
    if (!GLOBAL_NOTIFY_BUFFERS.length) return;
    const report = buildNotifyReport();
    console.log("\n[聚合推送报表阅览]\n" + report);
    await sendNativeNotify("🔔 拼多多果园任务执行总结", report);
}

// ============ 工具函数 ============
function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function mask(str = "", head = 6, tail = 6) {
    str = String(str);
    if (str.length <= head + tail) return str.slice(0, head) + "***";
    return str.slice(0, head) + "***" + str.slice(-tail);
}

// 变量的注释标注应该是 .xx变量 xxx, xx, , , ' <--- xxxx 而不是 .xx变量 xx, xx ' <--- xxxx
function md5(text) {
    return crypto.createHash("md5").update(String(text)).digest("hex");
}

function shortJson(obj, limit = 180) {
    const s = typeof obj === "string" ? obj : JSON.stringify(obj);
    return s.length > limit ? s.slice(0, limit) + "..." : s;
}

function okCode(res) {
    return res?.success === true || Number(res?.error_code) === 0 || res?.code === 0;
}

function assertOk(res, tip) {
    if (!res || !okCode(res)) {
        const msg = res?.error_msg || res?.message || res?.msg || shortJson(res, 500);
        throw new Error(`${tip} 失败: ${msg}`);
    }
    return res.data || res.result || res;
}

// 账号解析
function parseAccount(raw) {
    const text = String(raw || "").trim();
    if (!text) return { openid: "", token: "" };
    if (text.startsWith("{")) {
        try {
            const d = JSON.parse(text);
            return {
                openid: d.openid || d.openId || d.account || "",
                token: d.token || d.accessToken || ""
            };
        } catch (e) { }
    }
    for (const sep of ["#", "|"]) {
        if (text.includes(sep)) {
            const [oid, ...rest] = text.split(sep);
            return { openid: oid.trim(), token: rest.join(sep).trim() };
        }
    }
    if (text.length > 40 && !text.startsWith("o")) return { openid: "", token: text };
    return { openid: text, token: "" };
}

// 多账号分割
function splitAccounts(raw) {
    return String(raw || "").replace(/，/g, ",").replace(/,/g, "&").replace(/&/g, "\n")
        .split("\n")
        .map(i => i.trim())
        .filter(Boolean);
}

// Cookie 转换
function cookieStrToDict(ckStr) {
    const dict = {};
    ckStr.split(";").forEach(item => {
        const [k, v] = item.trim().split("=", 1);
        if (k) dict[k.trim()] = v?.trim() || "";
    });
    return dict;
}
function cookieDictToStr(dict) {
    return Object.entries(dict).map(([k, v]) => `${k}=${v}`).join("; ");
}
function extractPdduid(ckStr) {
    const match = ckStr.match(/pdd_user_id=(\d+)/);
    return match ? match[1] : "";
}

// ============ 缓存读写 ============
function readTokenCache() {
    if (!fs.existsSync(TOKEN_CACHE_FILE)) return {};
    try {
        return JSON.parse(fs.readFileSync(TOKEN_CACHE_FILE, "utf8"));
    } catch (e) {
        $.log(`读取token缓存异常: ${e.message}`);
        return {};
    }
}
function writeTokenCache(cache) {
    try {
        fs.writeFileSync(TOKEN_CACHE_FILE, JSON.stringify(cache, null, 2), "utf8");
    } catch (e) {
        $.log(`写入token缓存异常: ${e.message}`);
    }
}
function readCookieCache() {
    if (!fs.existsSync(COOKIE_CACHE_FILE)) return {};
    try {
        return JSON.parse(fs.readFileSync(COOKIE_CACHE_FILE, "utf8"));
    } catch { return {}; }
}
function writeCookieCache(ckCache) {
    try {
        fs.writeFileSync(COOKIE_CACHE_FILE, JSON.stringify(ckCache, null, 2), "utf8");
    } catch { }
}

// ============ 登录逻辑（原版Node单步登录，无anti-token，解决43042） ============
async function singleStepLogin(openid) {
    if (!openid) throw new Error("缺少openid无法登录");
    const { data } = await wechat.getCode(openid);
    if (!data?.status) throw new Error(`wx_server 获取code失败: ${data.message}`);
    const code = data.data?.code || data.code;
    if (!code) throw new Error(`wx_server 未返回code: ${JSON.stringify(data)}`);
    $.log(`获取微信code成功: ${mask(code)}`);

    const loginParams = { xcx: XCX_ID, xcx_version: XCX_VERSION };
    const loginBody = {
        code,
        has_auth: false,
        app_id: PDD_APP_ID,
        support_enhance_type: 3,
        xcx_version: XCX_VERSION
    };
    const loginHeaders = {
        "Content-Type": "application/json;charset=UTF-8",
        "User-Agent": UA,
        Referer: `https://servicewechat.com/${MINI_APP_ID}/0/page-frame.html`,
        "x-xcx-queries": `mini_program_name=pdd;mp_theme_version=${XCX_VERSION}`,
        "xweb_xhr": "1",
        "hd-xcx-model": "microsoft"
    };

    const resp = await axios({
        method: "POST",
        url: `${API_BASE}/login`,
        params: loginParams,
        data: loginBody,
        headers: loginHeaders,
        timeout: 20000,
        validateStatus: () => true
    });
    const resData = resp.data;
    $.log(`登录返回片段: ${shortJson(resData, 300)}`);

    if (resData.error_code === 54002) {
        throw new Error(`登录风控验证54002，verify_auth_token=${resData.verify_auth_token}`);
    }

    const root = resData.data || resData;
    const token = root.token || root.access_token;
    const pdduid = String(root.user_id || root.uid || root.pdduid || "");
    const nickname = root.nickname || root.nick_name || "";
    const uin = root.uin || "";

    if (!token || !pdduid) throw new Error(`登录未返回token/pdduid: ${JSON.stringify(resData)}`);

    // 组装完整Cookie
    const ckParts = [
        `PDDAccessToken=${token}`,
        `pdd_user_id=${pdduid}`,
        `pdd_user_uin=${uin}`
    ];
    resp.headers["set-cookie"]?.forEach(rawCk => {
        const kv = rawCk.split(";")[0].split("=", 1);
        ckParts.push(`${kv[0]}=${kv[1]}`);
    });
    const fullCookie = ckParts.join("; ");
    $.log(`登录成功，昵称: ${nickname || pdduid} pdduid=${pdduid}`);
    return { token, pdduid, nickname, uin, cookieStr: fullCookie };
}

// ============ 账号类（缓存、自动重登） ============
class ManorTask {
    constructor(rawAcc) {
        this.index = $.userIdx++;
        const info = parseAccount(rawAcc);
        this.openid = info.openid;
        this.token = info.token || "";
        this.pdduid = "";
        this.cookieStr = "";
        this.reloginAttempts = 0;

        // 缓存key
        this.cacheKey = this.openid || (this.token ? md5(this.token).slice(0, 16) : `acc_${this.index}`);
        const cache = readTokenCache()[this.cacheKey] || {};
        if (!this.token && cache.token) {
            this.token = cache.token;
            this.pdduid = cache.pdduid || "";
            this.cookieStr = cache.cookieStr || "";
            $.log(`账号[${this.index}] 从缓存恢复token`);
        }
    }

    saveCache(extra = {}) {
        const allCache = readTokenCache();
        const old = allCache[this.cacheKey] || {};
        const newItem = {
            ...old,
            openid: this.openid || old.openid || "",
            token: this.token,
            pdduid: this.pdduid,
            cookieStr: this.cookieStr,
            updatedAt: new Date().toISOString(),
            ...extra
        };
        allCache[this.cacheKey] = newItem;
        writeTokenCache(allCache);
        // 同步cookie缓存
        const ckCache = readCookieCache();
        ckCache[this.cacheKey] = { cookieStr: this.cookieStr, update: newItem.updatedAt };
        writeCookieCache(ckCache);
    }

    clearToken() {
        const cache = readTokenCache();
        if (cache[this.cacheKey]) {
            delete cache[this.cacheKey].token;
            delete cache[this.cacheKey].cookieStr;
        }
        writeTokenCache(cache);
    }

    async silentLogin() {
        const res = await singleStepLogin(this.openid);
        this.token = res.token;
        this.pdduid = res.pdduid;
        this.cookieStr = res.cookieStr;
        this.saveCache({ nickname: res.nickname, uin: res.uin });
    }

    async ensureLogin() {
        if (!this.token || !this.cookieStr) await this.silentLogin();
    }

    // 接口自动重登包装
    async withRelogin(fn) {
        await this.ensureLogin();
        let res = await fn();
        const errMsg = `${res?.error_msg || ""}${res?.message || ""}${res?.msg || ""}`;
        const needRelogin = !okCode(res) && this.openid && /登录|授权|token|失效|过期|未认证/.test(errMsg);
        if (needRelogin) {
            if (PDD_NO_RELOGIN) throw new Error(`Token失效，已禁用自动重登：${errMsg}`);
            if (this.reloginAttempts >= 2) throw new Error(`重登次数用尽，错误：${errMsg}`);
            this.reloginAttempts++;
            $.log(`账号[${this.index}] Token失效，第${this.reloginAttempts}次重登`);
            this.clearToken();
            this.token = "";
            this.cookieStr = "";
            await this.silentLogin();
            res = await fn();
        }
        this.reloginAttempts = 0;
        return res;
    }

    // 统一请求头
    getManorHeaders() {
        return {
            "User-Agent": UA,
            Accept: "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            Origin: ORCHARD_API_BASE,
            Referer: `${ORCHARD_API_BASE}/garden_index_lz_0.html`,
            AccessToken: this.token,
            Cookie: this.cookieStr
        };
    }

    // ============ 果园业务接口（1:1同步Python任务逻辑） ============
    // 查询水滴
    async getWater() {
        const fn = async () => {
            const headers = this.getManorHeaders();
            const params = { pdduid: this.pdduid, is_back: 1, access_token: this.token };
            const resp = await axios({
                method: "GET",
                url: `${MANOR_BASE}/manor-gateway/manor/query/user/water`,
                headers, params, timeout: 15000, validateStatus: () => true
            });
            return resp.data;
        };
        const data = await this.withRelogin(fn);
        return data.water_amount || 0;
    }

    // 每日签到
    async dailySign() {
        const fn = async () => {
            const headers = this.getManorHeaders();
            const body = { pdduid: this.pdduid, is_back: 1, fun_pl: 2 };
            const resp = await axios({
                method: "POST",
                url: `${MANOR_BASE}/manor/common/apply/activity`,
                headers, data: body, timeout: 15000, validateStatus: () => true
            });
            return resp.data;
        };
        const res = await this.withRelogin(fn);
        if (res.success || res.water_amount) $.log(`账号[${this.index}] 签到成功`);
        else $.log(`账号[${this.index}] 今日已签到`);
        await sleep(500);
    }

    // 浇水
    async waterTree(maxTimes = 50) {
        const water = await this.getWater();
        $.log(`账号[${this.index}] 当前水滴: ${water}`);
        if (water < 10) {
            $.log(`账号[${this.index}] 水滴不足10，跳过浇水`);
            return 0;
        }
        const maxLoop = Math.min(maxTimes, Math.floor(water / 10));
        let watered = 0;
        let currWater = water;
        for (let i = 0; i < maxLoop; i++) {
            const fn = async () => {
                const headers = this.getManorHeaders();
                const body = { pdduid: this.pdduid, is_back: 1 };
                const params = { pdduid: this.pdduid, access_token: this.token };
                const resp = await axios({
                    method: "POST",
                    url: `${API_BASE}/proxy/api/api/manor/water/cost`,
                    headers, data: body, params, timeout: 15000, validateStatus: () => true
                });
                return resp.data;
            };
            const res = await this.withRelogin(fn);
            const left = res.now_water_amount;
            if (left !== undefined && left < currWater) {
                watered++;
                currWater = left;
                if (watered % 10 === 0 || watered === maxLoop) {
                    $.log(`账号[${this.index}] 浇水 ${watered}/${maxLoop}，剩余${left}`);
                }
                if (left < 10) break;
                await sleep(200);
            } else {
                $.log(`账号[${this.index}] 浇水未扣水滴，停止循环`);
                break;
            }
        }
        const final = await this.getWater();
        $.log(`账号[${this.index}] 浇水完成，共浇${watered}次，剩余水滴${final}`);
        await sleep(500);
        return watered;
    }

    // 获取任务列表（同步Python完整解析）
    async getMissionList() {
        const fn = async () => {
            const headers = this.getManorHeaders();
            const body = {
                pdduid: this.pdduid,
                is_back: 1,
                activity_id_list: [201015, 201036],
                mission_types: [
                    38160, 38242, 38090, 38451, 37859, 38428,
                    38500, 38501, 38502, 38503, 38504, 38505,
                    38600, 38601, 38700, 38701, 38800, 38900,
                    37900, 37950, 38000, 38050, 38100, 38150
                ],
                fun_pl: 2
            };
            const resp = await axios({
                method: "POST",
                url: `${MANOR_BASE}/manor/mission/list`,
                headers, data: body, timeout: 15000, validateStatus: () => true
            });
            return resp.data;
        };
        const data = await this.withRelogin(fn);
        const activityMap = data.activity_vo_map || {};
        const tasks = [];
        for (const actIdStr of Object.keys(activityMap)) {
            const actId = Number(actIdStr);
            const misObj = activityMap[actIdStr].mission_list || {};
            for (const midStr of Object.keys(misObj)) {
                const mid = Number(midStr);
                const m = misObj[midStr];
                const rewardInfo = m.reward_info || [];
                let ra = 0, rt = "";
                for (const r of rewardInfo) {
                    if (r.reward_type === 1) {
                        ra = r.min_reward_amount || 0;
                        rt = "水滴";
                        break;
                    }
                }
                if (!ra && rewardInfo.length) {
                    ra = rewardInfo[0].min_reward_amount || 0;
                    rt = `T${rewardInfo[0].reward_type || "?"}`;
                }
                tasks.push({
                    activity_id: actId,
                    mission_id: mid,
                    type: m.type,
                    unified_status: m.unified_status,
                    is_draw: !!m.is_draw,
                    is_open: !!m.is_open,
                    finished_count: m.finished_count || 0,
                    max_count: m.max_count || 0,
                    reward_amount: ra,
                    reward_type: rt
                });
            }
        }
        const canClaim = tasks.filter(t => !t.is_draw && t.is_open && t.finished_count >= 1);
        const needAccept = tasks.filter(t => !t.is_draw && !t.is_open && t.finished_count >= 1);
        $.log(`账号[${this.index}] 总任务${tasks.length}，可领取${canClaim.length}，待接受${needAccept.length}`);
        return { canClaim, needAccept };
    }

    // 接受任务
    async acceptMission(actId, misId) {
        const fn = async () => {
            const headers = this.getManorHeaders();
            const body = { pdduid: this.pdduid, is_back: 1, mission_id: misId, activity_id: actId, fun_pl: 2 };
            const resp = await axios({
                method: "POST",
                url: `${MANOR_BASE}/manor/mission/accept`,
                headers, data: body, timeout: 15000, validateStatus: () => true
            });
            return resp.data;
        };
        const res = await this.withRelogin(fn);
        if (res.success) {
            $.log(`账号[${this.index}] 接受任务 act=${actId} id=${misId} 成功`);
            return true;
        }
        $.log(`账号[${this.index}] 接受失败: ${res.error_msg || ""}`);
        return false;
    }

    // 领取任务奖励
    async claimMission(actId, misId) {
        const fn = async () => {
            const headers = this.getManorHeaders();
            const body = { pdduid: this.pdduid, is_back: 1, mission_id: misId, activity_id: actId, fun_pl: 2 };
            const resp = await axios({
                method: "POST",
                url: `${MANOR_BASE}/manor/mission/draw`,
                headers, data: body, timeout: 15000, validateStatus: () => true
            });
            return resp.data;
        };
        const res = await this.withRelogin(fn);
        if (res.success) {
            const add = res.water || res.reward_amount || 0;
            $.log(`账号[${this.index}] 领取任务 act=${actId} id=${misId} +${add}水滴`);
            return true;
        }
        $.log(`账号[${this.index}] 领取失败: ${res.error_msg || ""}`);
        return false;
    }

    // 执行全部任务流程
    async runAllMission() {
        const { canClaim, needAccept } = await this.getMissionList();
        // 先接受
        for (const t of needAccept) {
            await this.acceptMission(t.activity_id, t.mission_id);
            await sleep(300);
        }
        // 再领取
        for (const t of canClaim) {
            await this.claimMission(t.activity_id, t.mission_id);
            await sleep(300);
        }
    }

    // 单账号完整主流程
    async runAccount() {
        $.log(`
========== 账号[${this.index}] 开始执行 ==========`);
        const notifyItem = {
            index: this.index,
            account: mask(this.openid || this.cacheKey),
            ok: false,
            status: "执行失败",
            finalWater: "-",
            message: "未执行",
        };

        try {
            await this.ensureLogin();
            await this.dailySign();
            await this.waterTree(50);
            await this.runAllMission();
            const finalWater = await this.getWater();
            notifyItem.ok = true;
            notifyItem.status = "执行成功";
            notifyItem.finalWater = finalWater;
            notifyItem.message = "";
            $.log(`账号[${this.index}] 执行完成，最终水滴：${finalWater}`);
        } catch (e) {
            notifyItem.message = e.message || String(e);
            $.log(`账号[${this.index}] 执行异常: ${notifyItem.message}`);
        } finally {
            GLOBAL_NOTIFY_BUFFERS.push(notifyItem);
        }
    }
}

// ============ Cookie直连模式 ============
async function runDirectCookie(cookieStr) {
    $.log("==== 使用 PDD_COOKIE 跳过登录 ====");
    const pdduid = extractPdduid(cookieStr);
    if (!pdduid) return $.log("Cookie 缺少 pdd_user_id");
    const ckDict = cookieStrToDict(cookieStr);
    const token = ckDict.PDDAccessToken;
    if (!token) return $.log("Cookie 缺少 PDDAccessToken");

    // 构造临时账号实例
    const tempTask = new ManorTask("");
    tempTask.token = token;
    tempTask.pdduid = pdduid;
    tempTask.cookieStr = cookieStr;
    await tempTask.runAccount();
}

// ============ 程序入口 ============
async function main() {
    $.log("===== 拼多多果园JS修复版（任务逻辑同步Python） =====");
    // 优先Cookie直连
    if (DIRECT_COOKIE) {
        await runDirectCookie(DIRECT_COOKIE);
        await dispatchNotify();
        $.done();
        return;
    }
    // 校验登录环境
    if (!RAW_ACCOUNT) {
        $.log("缺少环境变量：pdd_wxid / PDD_WXID，或配置 PDD_COOKIE");
        GLOBAL_NOTIFY_BUFFERS.push({
            index: 0,
            account: "未配置",
            ok: false,
            status: "配置错误",
            finalWater: "-",
            message: "缺少环境变量 pdd_wxid 或 PDD_WXID",
        });
        await dispatchNotify();
        return;
    }
    const accList = splitAccounts(RAW_ACCOUNT);
    $.log(`共加载 ${accList.length} 个账号`);
    for (const raw of accList) {
        const task = new ManorTask(raw);
        await task.runAccount();
        await sleep(1000);
    }
    await dispatchNotify();
    $.done();
}

main().catch(e => {
    $.log(`全局异常: ${e.message}`);
    process.exit(1);
});