/*
WPS v1.1.0（mywc网关聚合推送版）

功能：自动执行 WPS 签到、日常浏览任务、抽奖次数查询和抽奖尝试，支持多账号执行，执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL   必填其一，自建授权服务器域名
   - 示例：http://127.0.0.1:8110
   - 脚本会自动拼接 /mywc
   - 请求格式：GET {网关}/mywc?wxid=账号标识&appId=wx2f333d84a103825d
   - 请求头：auth=账号标识

2. 账号变量：
   wps_wxid 或 WPS_WXID                         推荐，WPS 专属账号变量
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔
   - 示例：wxid_a&wxid_b&openida 或 wxid_a,wxid_b,openida
   - 兼容 wxid / wxid#cookie / JSON：{"openid":"","cookie":"","secret":""}


3. 推送变量：
   JS 脚本内置 axios 企业微信机器人聚合推送，配置 QYWX_KEY 即可。
   QYWX_KEY                                         企业微信机器人 key

4. 青龙任务建议：
   名称：WPS 签到任务
   命令：node wps.js
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
        this.userList = [];
        this.userCount = 0;
    }
    log(msg) {
        const t = new Date().toLocaleTimeString("zh-CN");
        console.log(`[${t}] ${msg}`);
    }
    getdata(key) {
        return process.env[key] || process.env[key.toUpperCase()] || "";
    }
    wait(min, max) {
        const ms = max ? Math.floor(Math.random() * (max - min + 1) + min) : min;
        return new Promise(resolve => setTimeout(resolve, ms));
    }
    checkEnv(ckName) {
        const raw = this.getdata(ckName) || this.getdata("WPS_WXID") || this.getdata("wps");
        if (!raw) {
            this.log(`缺少环境变量: ${ckName} 或 WPS_WXID`);
            return;
        }
        this.userList = raw.replace(/，/g, ",").replace(/,/g, "&").replace(/&/g, "\n").split("\n").map(i => i.trim()).filter(Boolean);
        this.userCount = this.userList.length;
        this.log(`共加载 ${this.userCount} 个账号`);
    }
    done() {
        this.log(`\n${this.name} 全部执行完毕`);
    }
}

const $ = new SimpleEnv("WPS");

const ckName = "wps_wxid";
const MINI_APP_ID = "wx2f333d84a103825d";
const ACCOUNT_PLUGIN_APPID = "wxe5f87d6a233b5aab";
const WPS_MPID = "app_op_act";
const CLIENT_TYPE = 1;
const TOKEN_CACHE_FILE = path.join(__dirname, "wps_token_cache.json");

const ACCOUNT_BASE = "https://account.wps.cn";
const PERSONAL_BUS = "https://personal-bus.wps.cn";
const PERSONAL_ACT = "https://personal-act.wps.cn";
const CLOCK_INFO = `${PERSONAL_BUS}/activity/clock_in/v1/info`;
const CLOCK_IN = `${PERSONAL_BUS}/activity/clock_in/v1/clock_in`;
const TASK_OUTLINE = `${PERSONAL_BUS}/activity/clock_in/v1/task/outline`;
const TASK_START_BROWSE = `${PERSONAL_BUS}/activity/clock_in/v1/task/start_browse`;
const TASK_FINISH_BROWSE = `${PERSONAL_BUS}/activity/clock_in/v1/task/finish_browse`;
const LOTTERY_TIMES = `${PERSONAL_BUS}/activity/clock_in/v1/task/lottery_times`;
const ACTIVITY_CONFIG = "https://personal-act.wpscdn.cn/srcapi/act/rubik-service/honeycomb-adapter/client/module-info?pid=113&mg_id=47736&id=48312";
const LOTTERY_PAGE = "https://personal-act.wps.cn/rubik2/portal/HD2024082815116866/YM2024082815122017";
const COMPONENT_ACTION = `${PERSONAL_ACT}/activity-rubik/activity/component_action`;
const LOTTERY_ACTIVITY = "HD2024082815116866";
const LOTTERY_PAGE_NO = "YM2024082815122017";
const LOTTERY_COMPONENT_NO = "ZJ2025092916516585";
const LOTTERY_COMPONENT_NODE_ID = "FN1766995952bvx3";

const WX_SERVER_URL = $.getdata("wx_server_url") || $.getdata("WX_SERVER_URL") || "";
const QYWX_KEY = $.getdata("QYWX_KEY");

const wechat = {
  serverUrl: WX_SERVER_URL,
  appid: MINI_APP_ID,
  auth: $.getdata("wx_auth") || "",
};

// ============ 微信授权接口强制修正拦截器 ============
async function wxServerCode(openid, appid) {
  if (!WX_SERVER_URL) throw new Error("未配置 wx_server_url 或 WX_SERVER_URL");
  const baseUrl = wechat.serverUrl.replace(/\/$/, '');
  const targetUrl = `${baseUrl}/mywc`;
  
  // 核心痛点：将 POST 修改为 GET，参数名重构为你的自建系统规范：wxid、appId
  try {
    const { data } = await axios({
      method: "GET",
      url: targetUrl,
      params: {
        wxid: openid,
        appId: appid
      },
      headers: { auth: openid },
      timeout: 30000,
      validateStatus: () => true,
    });

    if (data?.status !== "ok" && !data?.code) {
      throw new Error(data?.msg || "自建授权服务器获取 code 失败");
    }
    const code = data?.code;
    if (!code) throw new Error(`自建授权服务未返回 code`);
    return code;
  } catch (e) {
    throw new Error(`请求授权服务失败: ${e.message}`);
  }
}


async function sendNativeNotify(title, content) {
  if (!QYWX_KEY) {
    $.log("⚠️ [通知] 未配置 QYWX_KEY，跳过企业微信机器人推送");
    return;
  }
  const qywxUrl = `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=${QYWX_KEY}`;
  try {
    const res = await axios.post(qywxUrl, { msgtype: "text", text: { content: `${title}

${content}` } }, { timeout: 15000, proxy: false });
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
    if (item.ok) lines.push(`📝 结果：签到/浏览/抽奖流程已执行`);
    else lines.push(`🧨 原因：${item.message}`);
    lines.push("------------------------------");
  }
  return lines.join("\n");
}

async function dispatchNotify() {
  if (!GLOBAL_NOTIFY_BUFFERS.length) return;
  const report = buildNotifyReport();
  console.log("\n[聚合推送报表阅览]\n" + report);
  await sendNativeNotify("🔔 WPS 任务执行总结", report);
}

// ============ 工具函数 ============
function readCache() {
  try {
    if (!fs.existsSync(TOKEN_CACHE_FILE)) return {};
    return JSON.parse(fs.readFileSync(TOKEN_CACHE_FILE, "utf8")) || {};
  } catch {
    return {};
  }
}

function writeCache(cache) {
  try {
    fs.writeFileSync(TOKEN_CACHE_FILE, JSON.stringify(cache, null, 2), "utf8");
  } catch (e) {
    $.log(`token缓存写入失败: ${e.message || e}`);
  }
}

function md5(text) {
  return crypto.createHash("md5").update(String(text)).digest("hex");
}

function hmacSha256Hex(text, key) {
  return crypto.createHmac("sha256", key).update(String(text)).digest("hex");
}

function base64url(input) {
  return Buffer.from(input).toString("base64url");
}

// 变量的注释标注应该是 .xx变量 xxx, xx, , , ' <--- xxxx 而不是 .xx变量 xx, xx ' <--- xxxx
function mask(value = "") {
  value = String(value);
  if (!value) return "";
  if (value.length <= 12) return `${value.slice(0, 3)}***`;
  return `${value.slice(0, 6)}***${value.slice(-6)}`;
}

function parseCookie(cookie = "") {
  if (!cookie || typeof cookie !== "string") return {};
  return Object.fromEntries(
    cookie
      .split(/;\s*/)
      .filter((v) => v.includes("="))
      .map((v) => [v.slice(0, v.indexOf("=")).trim(), v.slice(v.indexOf("=") + 1).trim()])
  );
}

// 请求默认用curl形式拼接头
function cookieHeader(cookies = {}) {
  return Object.entries(cookies)
    .filter(([, v]) => v !== undefined && v !== null && String(v) !== "")
    .map(([k, v]) => `${k}=${v}`)
    .join("; ");
}

function mergeSetCookie(setCookie = [], current = {}) {
  const cookies = { ...current };
  for (const line of Array.isArray(setCookie) ? setCookie : [setCookie]) {
    const first = String(line || "").split(";")[0];
    if (!first.includes("=")) continue;
    const key = first.slice(0, first.indexOf("=")).trim();
    const value = first.slice(first.indexOf("=") + 1).trim();
    if (key) cookies[key] = value;
  }
  return cookies;
}

function parseAccount(raw) {
  const text = String(raw || "").trim();
  if (!text) return { openid: "", cookie: "", secret: "" };
  if (text.startsWith("{")) {
    try {
      const data = JSON.parse(text);
      return {
        openid: data.openid || data.openId || data.account || "",
        cookie: data.cookie || data.Cookie || "",
        secret: data.secret || data.jsrsasign_secret || "",
      };
    } catch {}
  }

  for (const sep of ["#", "|"]) {
    if (text.includes(sep)) {
      const [openid, cookie, secret] = text.split(sep);
      return { openid: openid.trim(), cookie: (cookie || "").trim(), secret: (secret || "").trim() };
    }
  }

  if (text.includes("wps_sid=") || text.includes("kso_sid=")) return { openid: "", cookie: text, secret: "" };
  return { openid: text, cookie: "", secret: "" };
}

function canonicalJson(data = {}) {
  const sorted = {};
  Object.keys(data || {})
    .sort()
    .forEach((key) => {
      sorted[key] = data[key];
    });
  return JSON.stringify(sorted);
}

function makeSignature(data, config = {}) {
  const key = config.key || "";
  const ss = config.ss || "";
  if (!key || !ss) return {};
  const date = new Date().toUTCString();
  const payload = `${key}${md5(canonicalJson(data))}${date}`;
  return {
    Date: date,
    Signature: hmacSha256Hex(payload, ss),
  };
}

function randomHex(bytes = 16) {
  return crypto.randomBytes(bytes).toString("hex");
}

function makePopToken(method, url, secret, cv = "") {
  if (!secret) return "";
  const header = base64url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const body = {
    htm: String(method || "GET").toUpperCase(),
    htu: new URL(url).pathname,
    iat: Math.floor(Date.now() / 1000),
  };
  if (cv) body.cv = cv;
  const payload = base64url(JSON.stringify(body));
  const sig = crypto.createHmac("sha256", Buffer.from(secret)).update(`${header}.${payload}`).digest("base64url");
  return `${header}.${payload}.${sig}`;
}

function genEcKeyPair() {
  return crypto.generateKeyPairSync("ec", { namedCurve: "prime256v1" });
}

function signEc(privateKey, text) {
  return crypto
    .sign("sha256", Buffer.from(String(text)), {
      key: privateKey.export({ type: "pkcs8", format: "pem" }),
      dsaEncoding: "der",
    })
    .toString("base64url");
}

function deriveSecret(privateKey, serverJwkB64) {
  const jwk = JSON.parse(Buffer.from(serverJwkB64, "base64url").toString("utf8"));
  const publicKey = crypto.createPublicKey({ key: jwk, format: "jwk" });
  return crypto.diffieHellman({ privateKey, publicKey }).toString("base64url");
}

async function accountRequest(method, urlPath, { data = null, cookies = {}, secret = "" } = {}) {
  const url = `${ACCOUNT_BASE}${urlPath}`;
  const headers = {
    platform: "wx_mini",
    app_name: "account_wx_mini_plugin",
    Cookie: `csrf=1234567890${cookieHeader(cookies) ? `; ${cookieHeader(cookies)}` : ""}`,
    "X-CSRFToken": "1234567890",
    "User-Agent": "Mozilla/5.0 MicroMessenger MiniProgramEnv/Windows",
    Referer: `https://servicewechat.com/${MINI_APP_ID}/249/page-frame.html`,
  };
  const pop = makePopToken(method, url, secret, cookies.cv || "");
  if (pop) headers["X-Pop-Token"] = pop;
  const res = await axios({
    method,
    url,
    data,
    headers,
    timeout: 20000,
    validateStatus: () => true,
  });
  return { data: res.data, cookies: mergeSetCookie(res.headers["set-cookie"], cookies), status: res.status };
}

async function wpsRequest(method, url, { data = null, params = null, cookies = {}, secret = "", signed = false, clockConfig = {}, headers: extraHeaders = {} } = {}) {
  const csrf = "1234567890";
  const headers = {
    "X-CSRFToken": csrf,
    Cookie: `csrf=${csrf}${cookieHeader(cookies) ? `; ${cookieHeader(cookies)}` : ""}`,
    "User-Agent": "Mozilla/5.0 MicroMessenger MiniProgramEnv/Windows",
    Referer: `https://servicewechat.com/${MINI_APP_ID}/249/page-frame.html`,
    ...extraHeaders,
  };
  const pop = makePopToken(method, url, secret, cookies.cv || "");
  if (pop) headers["X-Pop-Token"] = pop;
  if (signed) Object.assign(headers, makeSignature(data || {}, clockConfig));

  const res = await axios({
    method,
    url,
    data,
    params,
    headers,
    timeout: 20000,
    validateStatus: () => true,
  });
  return { data: res.data, cookies: mergeSetCookie(res.headers["set-cookie"], cookies), status: res.status };
}

function assertOk(res, action) {
  if (!res || res.result !== "ok") {
    throw new Error(`${action}失败: ${res?.msg || res?.result || JSON.stringify(res)}`);
  }
  return res.data ?? res;
}

class Task {
  constructor(raw) {
    this.index = $.userIdx++;
    const account = parseAccount(raw);
    this.openid = account.openid;
    this.cookies = parseCookie(account.cookie);
    this.secret = account.secret || "";
    this.uid = this.cookies.uid || "";
    this.clockConfig = {};
    this.cacheKey = this.openid || this.cookies.uid || (account.cookie ? md5(account.cookie).slice(0, 16) : `account_${this.index}`);
  }

  getCached() {
    return readCache()[this.cacheKey] || {};
  }

  saveCache(extra = {}) {
    const cache = readCache();
    cache[this.cacheKey] = {
      ...(cache[this.cacheKey] || {}),
      openid: this.openid || this.getCached().openid || "",
      uid: this.uid || this.cookies.uid || "",
      cookie: cookieHeader(this.cookies),
      secret: this.secret,
      ...extra,
      updatedAt: new Date().toISOString(),
    };
    writeCache(cache);
  }

  removeLogin() {
    const cache = readCache();
    if (cache[this.cacheKey]) {
      delete cache[this.cacheKey].cookie;
      delete cache[this.cacheKey].secret;
      writeCache(cache);
    }
    this.cookies = {};
    this.secret = "";
  }

  loadCache() {
    const cache = this.getCached();
    if (!this.openid && cache.openid) this.openid = cache.openid;
    if (!this.secret && cache.secret) this.secret = cache.secret;
    if (!Object.keys(this.cookies).length && cache.cookie) this.cookies = parseCookie(cache.cookie);
    this.uid = this.cookies.uid || cache.uid || this.uid || "";
  }

  async login() {
    if (!this.openid) throw new Error("缺少 openid，无法自动登录。可设置 wps=openid 或 wps=openid#Cookie");
    const authCode = await wxServerCode(this.openid, ACCOUNT_PLUGIN_APPID);
    const keyPair = genEcKeyPair();
    const body = {
      type: "wxapp",
      appid: ACCOUNT_PLUGIN_APPID,
      auth_code: authCode,
      public_key: base64url(JSON.stringify(keyPair.publicKey.export({ format: "jwk" }))),
      auth_code_sign: signEc(keyPair.privateKey, authCode),
      slv: "none",
    };
    const res = await accountRequest("POST", "/passport/secure/api/easy_login", { data: body });
    const data = assertOk(res.data, "WPS自动登录");
    if (data.further_action) throw new Error(`WPS登录需要额外操作: ${data.further_action}`);
    if (!data.jwk) throw new Error(`WPS登录未返回 jwk: ${JSON.stringify(res.data)}`);
    this.cookies = res.cookies;
    this.secret = deriveSecret(keyPair.privateKey, data.jwk);
    this.uid = data.user_id || this.cookies.uid || "";
    this.saveCache();
    $.log(`账号[${this.index}] WPS登录成功: ${mask(this.uid || this.cookies.wps_sid)}`);
  }

  async ensureLogin() {
    this.loadCache();
    if (this.cookies.kso_sid && this.cookies.wps_sid && this.secret) return;
    await this.login();
  }

  async request(method, url, options = {}) {
    await this.ensureLogin();
    const res = await wpsRequest(method, url, {
      ...options,
      cookies: this.cookies,
      secret: this.secret,
      clockConfig: this.clockConfig,
    });
    this.cookies = res.cookies;
    this.saveCache();
    if (res.data?.msg === "empty wps_sid" || res.data?.result === "userNotLogin") {
      if (!this.openid) throw new Error("WPS登录态失效，且缺少 openid 无法刷新");
      $.log(`账号[${this.index}] WPS登录态失效，重新登录`);
      this.removeLogin();
      await this.login();
      const retry = await wpsRequest(method, url, {
        ...options,
        cookies: this.cookies,
        secret: this.secret,
        clockConfig: this.clockConfig,
      });
      this.cookies = retry.cookies;
      this.saveCache();
      return retry.data;
    }
    return res.data;
  }

  async loadClockConfig() {
    const res = await axios.get(ACTIVITY_CONFIG, {
      timeout: 15000,
      validateStatus: () => true,
      headers: {
        "User-Agent": "Mozilla/5.0 MicroMessenger MiniProgramEnv/Windows",
        Referer: `https://servicewechat.com/${MINI_APP_ID}/249/page-frame.html`,
      },
    });
    if (res.data?.result === "ok" && res.data?.data?.value) {
      this.clockConfig = res.data.data.value;
      if (!this.clockConfig.key && this.clockConfig.s_key) this.clockConfig.key = this.clockConfig.s_key;
    }
  }

  async userInfo() {
    const data = assertOk(await this.request("POST", "https://account.wps.cn/p/auth/check"), "查询用户信息");
    this.uid = data.userid || data.id || this.uid || this.cookies.uid || "";
    this.saveCache({ nickname: data.nickname || data.username || "" });
    $.log(`账号[${this.index}] 用户: ${mask(data.nickname || data.username || this.uid)}`);
  }

  async sign() {
    const body = { client_type: CLIENT_TYPE };
    const res = await this.request("POST", CLOCK_IN, { data: body, signed: true });
    if (res.result === "ok") {
      const data = res.data || {};
      $.log(`账号[${this.index}] 签到成功: 连续${data.continuous_days ?? data.continuousDays ?? "未知"}天`);
      return;
    }
    if (res.msg === "already clocked in today") {
      $.log(`账号[${this.index}] 今日已签到`);
      return;
    }
    throw new Error(`签到失败: ${res.msg || JSON.stringify(res)}`);
  }

  async clockInfo() {
    try {
      const res = await this.request("GET", CLOCK_INFO, {
        params: {
          client_type: CLIENT_TYPE,
          page_index: 0,
          page_size: 10,
        },
      });
      if (res.result === "ok") {
        const d = res.data || {};
        if (d.s_key) this.clockConfig.key = d.s_key;
        $.log(`账号[${this.index}] 签到信息: 连续${d.continuous_days ?? 0}天，累计${d.clock_in_total_num ?? "未知"}人打卡`);
      }
    } catch (e) {
      $.log(`账号[${this.index}] 查询签到信息失败: ${e.message || e}`);
    }
  }

  async getMainCode() {
    if (!this.openid) return "";
    return wxServerCode(this.openid, MINI_APP_ID);
  }

  async taskOutline() {
    const authCode = await this.getMainCode();
    if (!authCode) throw new Error("缺少 openid，无法获取任务 auth_code");
    const res = await this.request("GET", TASK_OUTLINE, {
      params: {
        mp_id: WPS_MPID,
        auth_code: authCode,
      },
    });
    return assertOk(res, "查询任务列表") || {};
  }

  async lotteryTimes() {
    const res = await this.request("GET", LOTTERY_TIMES, {
      params: {
        position: "wx_xcx_clock_activity",
      },
    });
    if (res.result === "ok") {
      $.log(`账号[${this.index}] 可抽奖次数: ${res.data ?? 0}`);
      return Number(res.data || 0);
    }
    $.log(`账号[${this.index}] 查询抽奖次数失败: ${res.msg || JSON.stringify(res)}`);
    return 0;
  }

  flattenTasks(outline = {}) {
    const tasks = [];
    const collect = (type, item) => {
      if (!item) return;
      if (Array.isArray(item)) {
        item.forEach((v) => collect(type, v));
      } else if (typeof item === "object") {
        tasks.push({ ...item, type });
      }
    };
    for (const [type, value] of Object.entries(outline)) collect(type, value);
    return tasks;
  }

  async doBrowseTask(task) {
    const authCode = await this.getMainCode();
    const clientType = task.client_type || "wechat";
    const startBody = {
      mp_id: WPS_MPID,
      auth_code: authCode,
      browse_app_id: task.app_id || MINI_APP_ID,
      client_type: clientType,
      version: "new",
    };
    if (clientType !== "wechat" && task.path) startBody.path = task.path;
    const start = await this.request("POST", TASK_START_BROWSE, { data: startBody });
    if (start.result !== "ok" && start.msg !== "任务已完成") {
      $.log(`账号[${this.index}] 浏览任务启动失败[${task.title || task.task_id || task.app_id}]: ${start.msg || JSON.stringify(start)}`);
      return false;
    }

    await $.wait(16000, 17000);

    const finish = await this.request("POST", TASK_FINISH_BROWSE, {
      data: {
        mp_id: WPS_MPID,
        auth_code: await this.getMainCode(),
        app_id: clientType === "wechat_web" ? MINI_APP_ID : task.app_id || MINI_APP_ID,
        client_type: clientType,
        path: clientType === "wechat_web" ? task.path || "" : "",
        version: "new",
        user_id: Number(this.uid || this.cookies.uid || 0),
      },
    });
    if (finish.result === "ok" || finish.msg === "任务已完成") {
      $.log(`账号[${this.index}] 浏览任务完成: ${task.title || task.task_id || task.app_id || task.path || ""}`);
      return true;
    }
    $.log(`账号[${this.index}] 浏览任务完成失败[${task.title || task.task_id || task.app_id}]: ${finish.msg || JSON.stringify(finish)}`);
    return false;
  }

  async browseTasks() {
    let outline;
    try {
      outline = await this.taskOutline();
    } catch (e) {
      $.log(`账号[${this.index}] 查询任务列表失败: ${e.message || e}`);
      return;
    }
    const tasks = this.flattenTasks(outline).filter((t) => t.type === "browse" && Number(t.status || 0) !== 1);
    if (!tasks.length) {
      $.log(`账号[${this.index}] 暂无待完成浏览任务`);
      return;
    }
    $.log(`账号[${this.index}] 待浏览任务: ${tasks.length}个`);
    for (const task of tasks) {
      try {
        await this.doBrowseTask(task);
      } catch (e) {
        $.log(`账号[${this.index}] 浏览任务异常: ${e.message || e}`);
      }
    }
  }

  async tryLotteryOnce(index) {
    const actCsrf = randomHex(16);
    const body = {
      component_uniq_number: {
        activity_number: LOTTERY_ACTIVITY,
        page_number: LOTTERY_PAGE_NO,
        component_number: LOTTERY_COMPONENT_NO,
        component_node_id: LOTTERY_COMPONENT_NODE_ID,
        filter_params: {},
      },
      component_type: 45,
      component_action: "lottery_v2.exec",
      lottery_v2: {
        session_id: 1,
      },
    };
    const cookies = { ...this.cookies, act_csrf_token: actCsrf };
    const res = await wpsRequest("POST", COMPONENT_ACTION, {
      data: body,
      cookies,
      secret: this.secret,
      headers: {
        "Content-Type": "application/json",
        "X-Act-Csrf-Token": actCsrf,
        Referer: LOTTERY_PAGE,
      },
    }).catch((e) => ({ data: { result: "error", msg: e.message || String(e) }, cookies: this.cookies }));
    this.cookies = mergeSetCookie([], { ...this.cookies, ...res.cookies });
    this.saveCache();

    const result = res.data;
    if (result?.result === "ok" && result.data?.lottery_v2?.success) {
      const reward = result.data.lottery_v2;
      $.log(`账号[${this.index}] 抽奖[${index}]成功: ${reward.reward_name || reward.reward_type || JSON.stringify(reward).slice(0, 120)}`);
      return true;
    }
    const detail = result?.data?.lottery_v2?.error_code ? `错误码${result.data.lottery_v2.error_code}` : result?.msg || JSON.stringify(result);
    $.log(`账号[${this.index}] 抽奖[${index}]失败: ${detail}`);
    return false;
  }

  async lottery() {
    const count = await this.lotteryTimes();
    const limit = Math.min(count, Number(process.env.wps_lottery_limit || 5));
    for (let i = 1; i <= limit; i++) {
      const ok = await this.tryLotteryOnce(i);
      if (!ok) break;
      await $.wait(1000, 1800);
    }
  }

  async run() {
    const notifyItem = {
      index: this.index,
      account: mask(this.openid || this.cacheKey),
      ok: false,
      status: "执行失败",
      message: "未执行",
    };

    $.log(`
账号[${this.index}] ${mask(this.openid || this.cacheKey)}`);
    try {
      await this.ensureLogin();
      await this.loadClockConfig();
      await this.userInfo();
      await this.clockInfo();
      await this.sign();
      await this.browseTasks();
      await this.lottery();
      notifyItem.ok = true;
      notifyItem.status = "执行成功";
      notifyItem.message = "";
    } catch (e) {
      notifyItem.message = e.message || String(e);
      $.log(`账号[${this.index}] 执行失败: ${notifyItem.message}`);
    } finally {
      GLOBAL_NOTIFY_BUFFERS.push(notifyItem);
    }
  }
}

// ============ 主入口函数 ============
(async () => {
  $.checkEnv(ckName);
  if (!$.userCount) {
    GLOBAL_NOTIFY_BUFFERS.push({
      index: 0,
      account: "未配置",
      ok: false,
      status: "配置错误",
      message: "缺少环境变量 wps_wxid 或 WPS_WXID",
    });
    return;
  }

  for (let idx = 0; idx < $.userList.length; idx++) {
    const account = $.userList[idx];
    try {
      await new Task(account).run();
    } catch (e) {
      GLOBAL_NOTIFY_BUFFERS.push({
        index: idx + 1,
        account: mask(account),
        ok: false,
        status: "执行失败",
        message: e.message || String(e),
      });
      $.log(`账号执行失败: ${e.message || e}`);
    }
  }
})()
  .catch((e) => $.log(`脚本异常: ${e.message || e}`))
  .finally(async () => {
    await dispatchNotify();
    $.done && $.done();
  });