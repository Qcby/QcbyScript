/**
 * 同程旅行抽奖 v1.1.0
 *
 * 功能：
 *   同程旅行活动打卡任务、积分查询、积分抽奖、聚合推送。
 *
 * 活动入口：
 *   #小程序://同程旅行/hdzLEBSUiDmYoZH
 *
 * 微信 code 网关：
 *   GET {wx_server_url}/mywc
 *   Query: wxid=当前账号, appId=wx3827070276e49e30
 *   Header: auth=当前账号
 *
 * 青龙环境变量：
 *   wx_server_url / WX_SERVER_URL
 *     自建微信 code 网关，例如 http://127.0.0.1:8110
 *   tongcheng_wxid / TONGCHENG_WXID
 *     同程账号 wxid，支持 &、英文逗号、中文逗号、换行分隔
 *   tongcheng_name / TONGCHENG_NAME
 *     可选，账号备注，支持与 tongcheng_wxid 相同分隔方式
 *   QYWX_KEY
 *     可选，企业微信机器人推送 key
 *
 * 可选变量：
 *   tongcheng_lottery_notify / TONGCHENG_LOTTERY_NOTIFY
 *     是否推送，默认 1；填 0 关闭
 *   tongcheng_lottery_cookie_file / TONGCHENG_LOTTERY_COOKIE_FILE
 *     idenId 缓存文件，默认 tc_lottery_cookie.json
 *   tc_lottery / TC_LOTTERY
 *     是否抽奖，默认 1；填 0 只做任务不抽奖
 *   tc_lottery_max / TC_LOTTERY_MAX
 *     单账号最多抽奖次数，默认 0 表示不限制
 *   tc_nick / TC_NICK
 *     可选，活动昵称，默认 同程用户
 *   tc_icon / TC_ICON
 *     可选，活动头像
 *
 * 兼容变量：
 *   qywx_am / QYWX_AM
 *     仅作为 QYWX_KEY 不可用时的兜底推送。
 *
 * 依赖：
 *   axios
 *
 * 青龙定时：
 *   10 9 * * * node 同程抽奖_yyb_go.js
 */

"use strict";

const axios = require("axios");
const dns = require("dns");
const https = require("https");
const fs = require("fs");
const path = require("path");
// 注意：不要 const { URL } = require("url")，会覆盖全局 URL，导致 axios/undici 报 URL is not defined

try {
  dns.setDefaultResultOrder("ipv4first");
} catch (_) {}

const NAME = "同程旅行抽奖";
const TZ = "Asia/Shanghai";
const SCRIPT_TITLE = "同程旅行抽奖";
const GLOBAL_NOTIFY_BUFFERS = [];

// 抽奖页 h5 公众号 appid（不是小程序 wx336dcaf6a1ecf632）
const H5_APPID = "wx3827070276e49e30";
const ACTIVITY_URL =
  "https://wx.17u.cn/cvgzt/20250718signin/index?refid=1000";

const WX_SERVER_URL = (
  process.env.wx_server_url ||
  process.env.WX_SERVER_URL ||
  ""
).replace(/\/$/, "");

const Notify = Number(
  process.env.tongcheng_lottery_notify ??
    process.env.tongcheng_notify ??
    process.env.Notify ??
    1
);

const CACHE_FILE = (
  process.env.tongcheng_lottery_cookie_file ||
  process.env.TONGCHENG_LOTTERY_COOKIE_FILE ||
  "tc_lottery_cookie.json"
).trim() || "tc_lottery_cookie.json";

const QYWX_KEY = process.env.QYWX_KEY || process.env.qywx_key || "";

const CONFIG = {
  host: "cvg.17usoft.com",
  protocol: "https",
  pid: 501,
  refId: "1000",
  headers: {
    "User-Agent":
      "Mozilla/5.0 (Linux; Android 16; PJZ110) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/121.0.0.0 Mobile Safari/537.36 XWEB/1210117 MMWEBSDK/20240404 MMWEBID/5830 MicroMessenger/8.0.49.2600(0x28003137) WeChat/arm64 Weixin NetType/WIFI Language/zh_CN ABI/arm64 MiniProgramEnv/android",
    "Content-Type": "application/json",
    Origin: "https://wx.17u.cn",
    Referer: "https://wx.17u.cn/",
    Accept: "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
  },
  timeout: 15000,
};

const httpsAgent = new https.Agent({ keepAlive: true, family: 4 });

// ---------- utils ----------
function envGet(...keys) {
  for (const k of keys) {
    const v = process.env[k];
    if (v !== undefined && String(v).trim() !== "") return String(v).trim();
  }
  return "";
}

function envBool(val, defaultTrue = true) {
  if (val === undefined || val === null || String(val).trim() === "") return defaultTrue;
  return !["0", "false", "off", "no", "n"].includes(String(val).trim().toLowerCase());
}

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function rand(min, max) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

function maskId(id) {
  const s = String(id || "");
  if (s.length <= 10) return s.slice(0, 2) + "***";
  return `${s.slice(0, 6)}...${s.slice(-4)}`;
}

function nowStr() {
  return new Date().toLocaleString("zh-CN", { timeZone: TZ, hour12: false });
}

function short(value, max = 220) {
  if (value === undefined || value === null) return "";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > max ? text.slice(0, max) + "..." : text;
}

function parseOpenidFilter(raw) {
  return String(raw || "")
    .split(/[\n&,;|，]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function splitAccounts(raw) {
  return String(raw || "")
    .split(/[\n&,;|，]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

function pickNick(account, openid, index) {
  return (
    account.nickname ||
    account.nick ||
    account.name ||
    account.remark ||
    (openid ? String(openid).slice(0, 8) : "") ||
    `账号${index}`
  );
}

// ---------- cache ----------
function cachePath() {
  return path.isAbsolute(CACHE_FILE) ? CACHE_FILE : path.join(__dirname, CACHE_FILE);
}

function loadCache() {
  try {
    const p = cachePath();
    if (!fs.existsSync(p)) return {};
    const data = JSON.parse(fs.readFileSync(p, "utf8"));
    if (data && typeof data === "object" && !Array.isArray(data)) return data;
  } catch (e) {
    console.log(`[cache] 读取失败: ${e.message || e}`);
  }
  return {};
}

function saveCache(cache) {
  try {
    const p = cachePath();
    fs.writeFileSync(p, JSON.stringify(cache || {}, null, 2), "utf8");
    console.log(`[cache] 已写入 ${p} · ${Object.keys(cache || {}).length} 条`);
  } catch (e) {
    console.log(`[cache] 写入失败: ${e.message || e}`);
  }
}

// ---------- notify ----------
function appendNotifyResult(result) {
  GLOBAL_NOTIFY_BUFFERS.push(result);
}

function buildNotifyReport() {
  const successItems = GLOBAL_NOTIFY_BUFFERS.filter((item) => item.ok);
  const failedItems = GLOBAL_NOTIFY_BUFFERS.filter((item) => !item.ok);
  const totalPrize = GLOBAL_NOTIFY_BUFFERS.reduce(
    (sum, item) => sum + Number(item.lotteryCount || 0),
    0
  );
  const lines = [
    "==============================",
    `🕒 执行时间：${nowStr()}`,
    `📊 统计数据：成功 ${successItems.length} / 总计 ${GLOBAL_NOTIFY_BUFFERS.length}`,
    `✅ 成功账号：${successItems.length} 个`,
    `❌ 失败账号：${failedItems.length} 个`,
    `🎁 抽奖次数：${totalPrize}`,
    "==============================",
  ];

  for (const item of GLOBAL_NOTIFY_BUFFERS) {
    const icon = item.ok ? "🧑‍💻" : "🧟";
    const statusIcon = item.ok ? "✅" : "❌";
    lines.push(`${icon} 【账号${item.index || "-"}】${item.account || "-"}`);
    lines.push(`${statusIcon} 状态：${item.statusText || "-"}`);
    lines.push(`🆔 idenId：${item.idenId || "-"}`);
    lines.push(`🧩 任务：${item.taskText || "-"}`);
    lines.push(`💰 积分：${item.points ?? "-"}`);
    lines.push(`🎁 抽奖：${item.lotteryText || "-"}`);
    if (!item.ok) lines.push(`🧨 原因：${item.message || "-"}`);
    lines.push("------------------------------");
  }
  return lines.join("\n");
}

async function sendNativeNotify(title, content) {
  if (!(Notify > 0)) {
    console.log("ℹ️ 推送关闭");
    return false;
  }
  if (QYWX_KEY) {
    try {
      const res = await axios.post(
        `https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=${encodeURIComponent(QYWX_KEY)}`,
        {
          msgtype: "markdown",
          markdown: { content: `## ${title}\n${content}` },
        },
        { timeout: 10000, httpsAgent }
      );
      if (res.data && res.data.errcode === 0) {
        console.log("✅ 推送已发送 · QYWX_KEY");
        return true;
      }
      console.log("⚠️ QYWX_KEY 推送失败:", res.data);
    } catch (e) {
      console.log("⚠️ QYWX_KEY 推送异常:", e.message);
    }
  }

  const qy = await sendQywxAm(title, content);
  if (qy) {
    console.log("✅ 推送已发送 · QYWX_AM 兜底");
    return true;
  }
  console.log("ℹ️ 未配置推送或推送失败，仅控制台输出");
  return false;
}

async function dispatchNotify() {
  const content = buildNotifyReport();
  console.log("\n================ 汇总 ================");
  console.log(content);
  await sendNativeNotify(SCRIPT_TITLE, content);
}

async function sendQywxAm(title, content) {
  const am = envGet("qywx_am", "QYWX_AM");
  if (!am) return false;
  const parts = am.split(",").map((x) => x.trim());
  if (parts.length < 4) {
    console.log("⚠️ qywx_am 格式错误，需要 corpid,corpsecret,touser,agentid");
    return false;
  }
  const [corpid, corpsecret, touser, agentid] = parts;
  try {
    const tokenRes = await axios.get(
      `https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid=${encodeURIComponent(
        corpid
      )}&corpsecret=${encodeURIComponent(corpsecret)}`,
      { timeout: 10000, httpsAgent }
    );
    const token = tokenRes.data && tokenRes.data.access_token;
    if (!token) {
      console.log("⚠️ 企微 token 获取失败:", tokenRes.data);
      return false;
    }
    const pushRes = await axios.post(
      `https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token=${token}`,
      {
        touser,
        msgtype: "text",
        agentid: Number(agentid),
        text: { content: `${title}\n${content}` },
        safe: 0,
      },
      { timeout: 10000, httpsAgent }
    );
    if (pushRes.data && pushRes.data.errcode === 0) return true;
    console.log("⚠️ 企微推送失败:", pushRes.data);
    return false;
  } catch (e) {
    console.log("⚠️ 企微推送异常:", e.message);
    return false;
  }
}

// ---------- http ----------
async function yybRequest(options) {
  const headers = Object.assign({}, options.headers || {});
  try {
    const res = await axios({
      method: options.method || "GET",
      url: options.url,
      headers,
      params: options.params,
      data: options.data,
      timeout: options.timeout || 20000,
      validateStatus: () => true,
      httpsAgent,
    });
    return { status: res.status, data: res.data, headers: res.headers };
  } catch (e) {
    throw new Error(`yyb 请求失败: ${e.message}`);
  }
}

async function activityRequest(method, apiPath, data) {
  try {
    const res = await axios({
      method,
      url: `${CONFIG.protocol}://${CONFIG.host}${apiPath}`,
      headers: CONFIG.headers,
      data,
      timeout: CONFIG.timeout,
      validateStatus: () => true,
      httpsAgent,
    });
    return res.data;
  } catch (e) {
    console.log(`请求网络错误 [${method} ${apiPath}]: ${e.message}`);
    return null;
  }
}

function basePayload(idenId, nick, icon, extra = {}) {
  return {
    idenId,
    pid: CONFIG.pid,
    refId: CONFIG.refId,
    nick,
    icon,
    ...extra,
  };
}

// ---------- wx gateway accounts / code ----------
async function getAccounts() {
  const wxids = splitAccounts(envGet("tongcheng_wxid", "TONGCHENG_WXID"));
  const names = splitAccounts(envGet("tongcheng_name", "TONGCHENG_NAME"));
  if (wxids.length) {
    return wxids.map((wxid, index) => ({
      wxid,
      ref: wxid,
      name: names[index] || `账号${index + 1}`,
    }));
  }

  throw new Error("未配置 tongcheng_wxid / TONGCHENG_WXID");
}

function extractWxCode(data) {
  if (data === null || data === undefined) return "";
  if (typeof data === "string") {
    const text = data.trim();
    if (!text) return "";
    if (text[0] === "{" || text[0] === "[") {
      try {
        return extractWxCode(JSON.parse(text));
      } catch (_) {
        return "";
      }
    }
    return "";
  }
  if (Array.isArray(data)) {
    for (const item of data) {
      const found = extractWxCode(item);
      if (found) return found;
    }
    return "";
  }
  if (typeof data === "object") {
    for (const key of ["code", "wxCode", "wx_code", "authCode", "auth_code"]) {
      if (data[key]) return String(data[key]).trim();
    }
    for (const value of Object.values(data)) {
      const found = extractWxCode(value);
      if (found) return found;
    }
  }
  return "";
}

async function getWxCode(appid, wxid) {
  if (!WX_SERVER_URL) {
    throw new Error("未配置 wx_server_url / WX_SERVER_URL");
  }
  const { status, data } = await yybRequest({
    method: "GET",
    url: `${WX_SERVER_URL}/mywc`,
    headers: { auth: wxid },
    params: { wxid, appId: appid },
  });
  const code = extractWxCode(data);
  if (status !== 200 || !code) {
    throw new Error(`获取 code 失败 HTTP ${status}: ${short(data)}`);
  }
  return code;
}

function extractIdenFromRedirect(loc, setCookies) {
  let idenId = "";
  let token = "";
  if (loc) {
    try {
      const u = new URL(loc, "https://wx.17u.cn");
      idenId = u.searchParams.get("code") || "";
      token = u.searchParams.get("token") || "";
    } catch (_) {}
  }
  const cookies = Array.isArray(setCookies) ? setCookies : setCookies ? [setCookies] : [];
  for (const c of cookies) {
    const s = String(c || "");
    if (!idenId) {
      const m = s.match(/(?:^|;\s*|,?\s*)(?:WxUser|cookieOpenSource|CooperateWxUser)=[^;]*openid=([^&;]+)/i)
        || s.match(/openid=([^&;]+)/i);
      if (m) idenId = decodeURIComponent(m[1]);
    }
    if (!token) {
      const m = s.match(/(?:^|[;&])token=([^&;]+)/i);
      if (m) token = decodeURIComponent(m[1]);
    }
  }
  return { idenId, token };
}

async function exchangeIdenId(code) {
  const url =
    "https://wx.17u.cn/flight/getopenid.html?url=" +
    encodeURIComponent(ACTIVITY_URL) +
    `&code=${encodeURIComponent(code)}&state=123`;

  try {
    const res = await axios({
      method: "GET",
      url,
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Linux; Android 16; PJZ110) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/121.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.71",
        Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
      },
      timeout: 20000,
      maxRedirects: 0,
      validateStatus: (s) => s >= 200 && s < 400,
      httpsAgent,
    });

    const loc = res.headers.location || res.headers.Location || "";
    const setCookie = res.headers["set-cookie"] || res.headers["Set-Cookie"] || [];
    let body = "";
    if (typeof res.data === "string") body = res.data;
    if (!loc && body) {
      const m = body.match(/href=["']([^"']+)["']/i);
      if (m) {
        const href = m[1].replace(/&/g, "&");
        return extractIdenFromRedirect(href, setCookie);
      }
    }
    return extractIdenFromRedirect(loc, setCookie);
  } catch (e) {
    // axios 对 302 有时仍抛错（取决于版本/配置）
    if (e.response) {
      const loc = e.response.headers.location || e.response.headers.Location || "";
      const setCookie =
        e.response.headers["set-cookie"] || e.response.headers["Set-Cookie"] || [];
      let body = e.response.data;
      if (typeof body !== "string") body = "";
      if (!loc && body) {
        const m = body.match(/href=["']([^"']+)["']/i);
        if (m) {
          const href = m[1].replace(/&/g, "&");
          return extractIdenFromRedirect(href, setCookie);
        }
      }
      return extractIdenFromRedirect(loc, setCookie);
    }
    throw e;
  }
}

async function probeIdenId(idenId, nick, icon) {
  const res = await activityRequest(
    "POST",
    "/activity/checkin/getIndexInfo",
    basePayload(idenId, nick, icon)
  );
  return !!(res && res.code === 1000);
}

async function resolveIdenId(account, index, nick, icon, cache) {
  const wxid = String(account.wxid || account.ref || account.openid || "").trim();
  const ref = String(account.id || account.ref || account.uin || "").trim();
  const openid = String(account.openid || account.openId || "").trim();
  const key = wxid || ref || openid || `idx_${index}`;
  const hit = cache[key];

  if (hit && hit.idenId) {
    const ok = await probeIdenId(hit.idenId, nick, icon);
    if (ok) {
      console.log(`   使用缓存 idenId ${maskId(hit.idenId)}`);
      return {
        idenId: hit.idenId,
        token: hit.token || "",
        fromCache: true,
        key,
      };
    }
    console.log(`   缓存 idenId 失效，重新登录`);
  }

  if (!wxid) {
    throw new Error("账号缺少 wxid，无法 getCode");
  }

  const code = await getWxCode(H5_APPID, wxid);
  console.log(`   获取 h5 code 成功 ${String(code).slice(0, 8)}****`);

  const exchanged = await exchangeIdenId(code);
  if (!exchanged.idenId || exchanged.idenId === "0") {
    throw new Error(`getopenid 未返回 idenId: ${short(exchanged)}`);
  }

  const ok = await probeIdenId(exchanged.idenId, nick, icon);
  if (!ok) {
    throw new Error(`idenId 探活失败 ${maskId(exchanged.idenId)}`);
  }

  cache[key] = {
    ref: key,
    wxid,
    openid,
    idenId: exchanged.idenId,
    token: exchanged.token || "",
    h5_appid: H5_APPID,
    updated_at: new Date().toISOString().replace("T", " ").slice(0, 19),
  };
  globalThis.__tc_lottery_dirty = true;

  return {
    idenId: exchanged.idenId,
    token: exchanged.token || "",
    fromCache: false,
    key,
  };
}

// ---------- activity api ----------
async function getTaskList(idenId, nick, icon) {
  const res = await activityRequest(
    "POST",
    "/activity/checkin/getClockinTaskInfo",
    basePayload(idenId, nick, icon)
  );
  if (res && res.code === 1000) return res.data.taskList || [];
  if (res) console.log(`   任务列表失败: ${res.message || res.code}`);
  return [];
}

async function completeTaskAction(idenId, nick, icon, taskType, rewardPoints) {
  await sleep(rand(800, 1800));
  const res = await activityRequest(
    "POST",
    "/activity/checkin/completeClockinTask",
    basePayload(idenId, nick, icon, { taskType, rewardPoints })
  );
  if (res && res.code === 1000 && res.data && res.data.taskId) return res.data.taskId;
  if (res) console.log(`   提交任务失败: ${res.message || res.code}`);
  return null;
}

async function collectReward(idenId, nick, icon, completeTaskId) {
  if (!completeTaskId) return false;
  await sleep(rand(500, 1200));
  const res = await activityRequest(
    "POST",
    "/activity/checkin/collectClockinTaskRewardPoints",
    basePayload(idenId, nick, icon, { completeTaskId })
  );
  return !!(res && res.code === 1000);
}

async function getLotteryInfo(idenId, nick, icon) {
  const res = await activityRequest(
    "POST",
    "/activity/checkin/getAttendLotteryInfo",
    basePayload(idenId, nick, icon)
  );
  if (res && res.code === 1000) {
    return {
      qsPlayId: res.data.qsPlayId,
      lotteryCostPoints: res.data.lotteryCostPoints,
      prizeList:
        (res.data.lotteryPrizeInfo &&
          res.data.lotteryPrizeInfo.playList &&
          res.data.lotteryPrizeInfo.playList[0] &&
          res.data.lotteryPrizeInfo.playList[0].prizeList) ||
        [],
    };
  }
  if (res) console.log(`   抽奖信息失败: ${res.message || res.code}`);
  return null;
}

async function getIndexInfo(idenId, nick, icon) {
  const res = await activityRequest(
    "POST",
    "/activity/checkin/getIndexInfo",
    basePayload(idenId, nick, icon)
  );
  if (res && res.code === 1000) return res.data;
  if (res) console.log(`   首页信息失败: ${res.message || res.code}`);
  return null;
}

async function performLotteryOnce(idenId, nick, icon, qsPlayId) {
  return activityRequest(
    "POST",
    "/activity/checkin/performLottery",
    basePayload(idenId, nick, icon, { qsPlayId })
  );
}

// ---------- account flow ----------
async function processAccount(account, index, total, opts, cache) {
  const openid = String(account.openid || account.openId || "").trim();
  const name = pickNick(account, openid, index);
  const nick = opts.nick;
  const icon = opts.icon;
  const lines = [];
  const result = {
    name,
    idenId: "",
    taskOk: 0,
    taskSkip: 0,
    taskFail: 0,
    lottery: [],
    points: null,
    error: "",
  };

  console.log(`\n========== [${index}/${total}] ${name} ==========`);
  lines.push(`【${name}】`);

  try {
    console.log(">>> [0/3] 登录换 idenId");
    const login = await resolveIdenId(account, index, nick, icon, cache);
    const idenId = login.idenId;
    result.idenId = maskId(idenId);
    lines[0] = `【${name}】${maskId(idenId)}${login.fromCache ? "(缓存)" : ""}`;
    console.log(`   idenId=${maskId(idenId)} ${login.fromCache ? "(cache)" : "(fresh)"}`);

    // 1. tasks
    console.log(">>> [1/3] 每日任务");
    const tasks = await getTaskList(idenId, nick, icon);
    if (!tasks.length) {
      console.log("   未获取到任务列表");
      lines.push("任务: 无列表");
    } else {
      for (const task of tasks) {
        const {
          type,
          title,
          couldComplete,
          rewardPoints,
          completeTimesToday,
          maxCompleteTimesPerDay,
        } = task;

        if (completeTimesToday >= maxCompleteTimesPerDay) {
          console.log(`   跳过 ${title} (今日已完成)`);
          result.taskSkip++;
          continue;
        }
        if (!couldComplete) {
          console.log(`   不可用 ${title}`);
          continue;
        }

        console.log(`   执行 ${title} (+${rewardPoints})`);
        const taskId = await completeTaskAction(
          idenId,
          nick,
          icon,
          type,
          rewardPoints
        );
        if (!taskId) {
          result.taskFail++;
          continue;
        }
        const ok = await collectReward(idenId, nick, icon, taskId);
        if (ok) {
          console.log(`   成功 ${title}`);
          result.taskOk++;
        } else {
          console.log(`   领奖失败 ${title}`);
          result.taskFail++;
        }
      }
      lines.push(
        `任务: 成功${result.taskOk}/跳过${result.taskSkip}/失败${result.taskFail}`
      );
      console.log(
        `✓ 任务统计: 成功 ${result.taskOk}, 跳过 ${result.taskSkip}, 失败 ${result.taskFail}`
      );
    }

    // 2. lottery prep
    console.log(">>> [2/3] 抽奖信息");
    let lotteryInfo = null;
    if (opts.doLottery) {
      lotteryInfo = await getLotteryInfo(idenId, nick, icon);
      if (!lotteryInfo || !lotteryInfo.qsPlayId) {
        console.log("   未拿到 qsPlayId");
        lines.push("抽奖: 无令牌");
      }
    } else {
      console.log("   已关闭抽奖 (tc_lottery=0)");
      lines.push("抽奖: 已关闭");
    }

    // 3. lottery
    console.log(">>> [3/3] 执行抽奖");
    if (opts.doLottery && lotteryInfo && lotteryInfo.qsPlayId) {
      let indexInfo = await getIndexInfo(idenId, nick, icon);
      if (!indexInfo) {
        console.log("   无法获取积分，跳过抽奖");
        lines.push("抽奖: 积分查询失败");
      } else {
        let points = Number(indexInfo.points || 0);
        const cost = Number(
          indexInfo.lotteryCostPoints || lotteryInfo.lotteryCostPoints || 100
        );
        console.log(`   当前积分 ${points}, 单次 ${cost}`);

        let count = 0;
        while (points >= cost) {
          if (opts.lotteryMax > 0 && count >= opts.lotteryMax) {
            console.log(`   已达上限 ${opts.lotteryMax} 次`);
            break;
          }
          await sleep(rand(1000, 2500));
          const res = await performLotteryOnce(
            idenId,
            nick,
            icon,
            lotteryInfo.qsPlayId
          );
          if (res && res.code === 1000 && res.data && res.data.prizeId) {
            count++;
            const prizeId = res.data.prizeId;
            const prize = (lotteryInfo.prizeList || []).find(
              (p) => p.prizeId === prizeId
            );
            const prizeName = prize
              ? prize.prizeTitle
              : `未知奖品(${prizeId})`;
            console.log(`   第${count}次: ${prizeName}`);
            result.lottery.push(prizeName);

            const fresh = await getIndexInfo(idenId, nick, icon);
            if (fresh) {
              points = Number(fresh.points || 0);
            } else {
              console.log("   积分刷新失败，停止抽奖");
              break;
            }
          } else {
            const errMsg = (res && (res.message || res.msg)) || "无响应";
            console.log(`   抽奖失败: ${errMsg}`);
            if (String(errMsg).includes("好友")) {
              console.log("   可能需要小程序内加好友/关注后再试");
              lines.push("抽奖: 需加好友");
            }
            break;
          }
        }

        if (count > 0) {
          lines.push(`抽奖: ${count}次 → ${result.lottery.join(" / ")}`);
          console.log(`★ 共抽 ${count} 次`);
        } else if (!lines.some((x) => x.includes("抽奖:"))) {
          if (points < cost) {
            lines.push(`抽奖: 积分不足(${points}<${cost})`);
          } else {
            lines.push("抽奖: 0次");
          }
        }
      }
    }

    const finalInfo = await getIndexInfo(idenId, nick, icon);
    if (finalInfo) {
      result.points = finalInfo.points;
      console.log(`★ 最终积分: ${finalInfo.points}`);
      lines.push(`积分: ${finalInfo.points}`);
    }
  } catch (e) {
    result.error = e.message || String(e);
    console.log(`💥 账户异常: ${result.error}`);
    lines.push(`异常: ${result.error}`);
  }

  return { result, lines };
}

function buildAccountNotifyResult(result, lines, index) {
  const taskText =
    lines.find((line) => line.startsWith("任务:")) ||
    `成功${result.taskOk || 0}/跳过${result.taskSkip || 0}/失败${result.taskFail || 0}`;
  const lotteryText =
    lines.find((line) => line.startsWith("抽奖:")) ||
    (result.lottery && result.lottery.length ? result.lottery.join(" / ") : "未抽奖");
  return {
    index,
    account: result.name || `账号${index}`,
    ok: !result.error,
    statusText: result.error ? "执行失败" : "执行成功",
    idenId: result.idenId || "-",
    taskText: taskText.replace(/^任务:\s*/, ""),
    points: result.points ?? "-",
    lotteryText: lotteryText.replace(/^抽奖:\s*/, ""),
    lotteryCount: Array.isArray(result.lottery) ? result.lottery.length : 0,
    message: result.error || "-",
  };
}

// ---------- main ----------
async function main() {
  console.log(`================ ${NAME} 启动 ${nowStr()} ================`);
  console.log(`wx gateway: ${WX_SERVER_URL || "未配置"}`);
  console.log(`h5 appid: ${H5_APPID}`);

  const opts = {
    nick: envGet("tc_nick", "TC_NICK") || "同程用户",
    icon:
      envGet("tc_icon", "TC_ICON") ||
      "https://file.40017.cn/huochepiao/activity/20200521supplies/img/defaultImg-fs8.png",
    doLottery: envBool(envGet("tc_lottery", "TC_LOTTERY"), true),
    lotteryMax: Number(envGet("tc_lottery_max", "TC_LOTTERY_MAX") || 0) || 0,
  };

  let accounts = [];
  try {
    accounts = await getAccounts();
  } catch (e) {
    appendNotifyResult({
      index: 1,
      account: "配置检查",
      ok: false,
      statusText: "执行失败",
      idenId: "-",
      taskText: "-",
      points: "-",
      lotteryText: "-",
      lotteryCount: 0,
      message: e.message || String(e),
    });
    await dispatchNotify();
    return;
  }
  if (!accounts.length) {
    appendNotifyResult({
      index: 1,
      account: "配置检查",
      ok: false,
      statusText: "执行失败",
      idenId: "-",
      taskText: "-",
      points: "-",
      lotteryText: "-",
      lotteryCount: 0,
      message: "未获取到有效账号",
    });
    await dispatchNotify();
    return;
  }

  console.log(
    `账号数: ${accounts.length} | 抽奖: ${opts.doLottery ? "开" : "关"}${
      opts.lotteryMax > 0 ? ` | 上限: ${opts.lotteryMax}` : ""
    }`
  );

  const cache = loadCache();
  globalThis.__tc_lottery_dirty = false;

  for (let i = 0; i < accounts.length; i++) {
    const { result, lines } = await processAccount(
      accounts[i],
      i + 1,
      accounts.length,
      opts,
      cache
    );
    appendNotifyResult(buildAccountNotifyResult(result, lines, i + 1));

    if (i < accounts.length - 1) {
      const wait = rand(3000, 7000);
      console.log(`\n等待 ${Math.round(wait / 1000)}s 后处理下一账号...`);
      await sleep(wait);
    }
  }

  if (globalThis.__tc_lottery_dirty) {
    saveCache(cache);
  }

  await dispatchNotify();
  console.log("\n================ 全部结束 ================");
}

main().catch(async (err) => {
  console.error("💥 主进程出错:", err);
  try {
    appendNotifyResult({
      index: 1,
      account: "主进程",
      ok: false,
      statusText: "执行失败",
      idenId: "-",
      taskText: "-",
      points: "-",
      lotteryText: "-",
      lotteryCount: 0,
      message: `脚本异常: ${err.message || err}`,
    });
    await dispatchNotify();
  } catch (_) {}
  process.exitCode = 1;
});
