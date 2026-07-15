/*
南方航空签到 v1.1.0（mywc网关聚合推送版）

功能：自动完成南方航空签到及奖励领取，支持微信小程序账号与旧 H5 Cookie，多账号执行结束后统一聚合推送。

配置说明：
1. 微信 code 网关：
   wx_server_url 或 WX_SERVER_URL   微信账号模式必填其一，自建授权服务器地址
   - 脚本自动请求：GET {网关}/mywc?wxid=账号标识&appId=wx729238547ac7a14c
   - 请求头：auth=账号标识

2. 账号变量：
   csair_wxid 或 CSAIR_WXID        推荐，南方航空专属微信账号变量
   csairCookie                     可选，兼容旧 H5 Cookie（TOKEN=xxx; ...）
   - 多账号支持使用 &、英文逗号、中文逗号或换行分隔

3. 推送变量：
   QYWX_KEY                        可选，企业微信机器人 Key

依赖：Node.js 18+，无第三方 npm 依赖。
青龙任务建议：task CodeScript/南方航空.js
*/

const fs = require('fs');
const path = require('path');

const CSAIR_APPID = 'wx729238547ac7a14c';
const WX_SERVER_URL = (process.env.wx_server_url || process.env.WX_SERVER_URL || '').trim().replace(/\/$/, '');
const QYWX_KEY = (process.env.QYWX_KEY || '').trim();
const CSAIR_MINI_BASE = 'https://wxapi.csair.com';
const CSAIR_MINI_VERSION = '459';
const CACHE_FILE = resolveCacheFile();
const MINI_USER_AGENT =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) MicroMessenger/3.9.12 MiniProgramEnv/Windows WindowsWechat/WMPF';
const H5_USER_AGENT =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36 MicroMessenger/7.0.20 MiniProgramEnv/Windows WindowsWechat/WMPF miniProgram/wx729238547ac7a14c';
const H5_REFERER = 'https://wxapi.csair.com/h5/sign/';
const RECEIVABLE_AWARD_STATUSES = new Set(['waitReceive', 'benefitWaitReceive', 'lotteryWaitReceive']);
const FINISHED_AWARD_STATUSES = new Set(['received', 'expired', 'nostock', 'waitSend']);

const ENV_NAME = 'csair_wxid / CSAIR_WXID';
const ACCOUNT_LIST = [
  ...splitAccounts(process.env.csair_wxid || process.env.CSAIR_WXID),
  ...splitAccounts(process.env.csairCookie, true),
].filter((item, index, list) => list.indexOf(item) === index);
const NOTIFY_RESULTS = [];

function splitAccounts(raw = '', legacyCookie = false) {
  const value = String(raw || '').trim();
  if (!value) return [];
  const separator = legacyCookie && /(^|;\s*)TOKEN=/i.test(value) ? /[&\r\n]+/ : /[&,，\r\n]+/;
  return value.split(separator).map((item) => item.trim()).filter(Boolean);
}

function resolveCacheFile() {
  const custom = process.env.CSAIR_CACHE || process.env.csairCache || '';
  if (!custom) return path.join(__dirname, 'csair_ck_cache.json');
  return path.isAbsolute(custom) ? custom : path.join(__dirname, custom);
}

function readCache() {
  try {
    if (!fs.existsSync(CACHE_FILE)) return {};
    return JSON.parse(fs.readFileSync(CACHE_FILE, 'utf8')) || {};
  } catch (error) {
    console.log(`读取CK缓存失败: ${error.message || error}`);
    return {};
  }
}

function writeCache(cache) {
  try {
    fs.writeFileSync(CACHE_FILE, JSON.stringify(cache, null, 2), 'utf8');
  } catch (error) {
    console.log(`写入CK缓存失败: ${error.message || error}`);
  }
}

function getCachedAuth(account) {
  const cache = readCache();
  return cache[account] || null;
}

function saveCachedAuth(account, data) {
  const cache = readCache();
  cache[account] = {
    token: data.token || '',
    sessionId: data.sessionId || '',
    openId: data.openId || '',
    unionId: data.unionId || '',
    updatedAt: new Date().toISOString(),
  };
  writeCache(cache);
}

function removeCachedAuth(account) {
  const cache = readCache();
  if (cache[account]) {
    delete cache[account];
    writeCache(cache);
  }
}

function isCookieAccount(account) {
  return /(^|;\s*)TOKEN=/i.test(account) || account.includes(';');
}

function cookieValue(value) {
  return String(value ?? '').replace(/[;\r\n\t\x00-\x1f\x7f]/g, '');
}

function maskValue(value) {
  const text = String(value || '');
  if (!text) return '';
  if (text.length <= 8) return '***';
  return `${text.slice(0, 4)}***${text.slice(-4)}`;
}

function maskWcsMessage(message) {
  return String(message || '')
    .replace(/(->\s*)([A-Za-z0-9_-]{10,})/g, (_, prefix, value) => `${prefix}${maskValue(value)}`)
    .replace(/(openid[=:]\s*)([A-Za-z0-9_-]{10,})/gi, (_, prefix, value) => `${prefix}${maskValue(value)}`);
}

function parseJsonBody(body) {
  if (body && typeof body === 'object') return body;
  try {
    return JSON.parse(body || '{}');
  } catch (error) {
    throw new Error(`响应不是JSON: ${String(body || '').slice(0, 120)}`);
  }
}

// 基于 Node 内置 http/https 的请求函数, 替代 got (避免 got v12+ ESM 的 require 兼容问题)
// options: { method, headers, json, timeout }
async function httpRequest(url, options = {}) {
  const https = require('https');
  const http = require('http');
  const urlObj = url instanceof URL ? url : new URL(url);
  const lib = urlObj.protocol === 'https:' ? https : http;
  const method = (options.method || 'GET').toUpperCase();
  const headers = { ...(options.headers || {}) };
  let payload = null;

  if (options.json !== undefined && options.json !== null) {
    payload = JSON.stringify(options.json);
    if (!headers['Content-Type'] && !headers['content-type']) {
      headers['Content-Type'] = 'application/json';
    }
    headers['Content-Length'] = Buffer.byteLength(payload);
  }

  return new Promise((resolve, reject) => {
    const req = lib.request(
      {
        hostname: urlObj.hostname,
        port: urlObj.port || (urlObj.protocol === 'https:' ? 443 : 80),
        path: `${urlObj.pathname}${urlObj.search}`,
        method,
        headers,
        timeout: options.timeout || 15000,
      },
      (res) => {
        let body = '';
        res.on('data', (chunk) => (body += chunk));
        res.on('end', () => resolve({ statusCode: res.statusCode, headers: res.headers, body }));
      },
    );
    req.on('error', reject);
    req.on('timeout', () => req.destroy(new Error('请求超时')));
    if (payload) req.write(payload);
    req.end();
  });
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function buildMiniUrl(apiPath) {
  const url = new URL(apiPath, CSAIR_MINI_BASE);
  url.searchParams.set('appid', CSAIR_APPID);
  url.searchParams.set('wxchannel', 'wxopen');
  url.searchParams.set('envVersion', 'release');
  return url.toString();
}

function buildWxopenUrl(apiPath) {
  const url = new URL(`/wxopen${apiPath.startsWith('/') ? apiPath : `/${apiPath}`}`, CSAIR_MINI_BASE);
  url.searchParams.set('appid', CSAIR_APPID);
  url.searchParams.set('wxchannel', 'wxopen');
  url.searchParams.set('envVersion', 'release');
  return url.toString();
}

function buildH5Url(apiPath) {
  const url = new URL(apiPath, CSAIR_MINI_BASE);
  url.searchParams.set('type', 'APPTYPE');
  url.searchParams.set('chanel', 'ss');
  url.searchParams.set('lang', 'zh');
  return url.toString();
}

function buildH5Cookie(tokenOrCookie) {
  if (isCookieAccount(tokenOrCookie)) return tokenOrCookie;
  const token = cookieValue(tokenOrCookie);
  if (!token) return '';
  return `TOKEN=${token}; cs1246643sso=${token}; channel=csair;`;
}

function h5Headers(cookie) {
  return {
    'User-Agent': H5_USER_AGENT,
    Accept: 'application/json, text/plain, */*',
    'Content-Type': 'application/json',
    Origin: CSAIR_MINI_BASE,
    Referer: H5_REFERER,
    'Sec-Fetch-Site': 'same-origin',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Dest': 'empty',
    'Accept-Language': 'zh-CN,zh;q=0.9',
    Cookie: cookie,
  };
}

function miniHeaders(session = {}) {
  return {
    'User-Agent': MINI_USER_AGENT,
    Accept: 'application/json',
    'Content-Type': 'application/json',
    Referer: `https://servicewechat.com/${CSAIR_APPID}/${CSAIR_MINI_VERSION}/page-frame.html`,
    sessionId: session.sessionId || '',
    channel: 'ecsair',
    activityChannel: '1',
    unnecessaryParam: session.openId || '',
  };
}

async function postMiniApi(apiPath, data, session = {}) {
  const { body } = await httpRequest(buildMiniUrl(apiPath), {
    method: 'POST',
    headers: miniHeaders(session),
    json: data,
    timeout: 15000,
  });
  return parseJsonBody(body);
}

async function postWxopenApi(apiPath, data, session = {}) {
  const { body } = await httpRequest(buildWxopenUrl(apiPath), {
    method: 'POST',
    headers: miniHeaders(session),
    json: data,
    timeout: 15000,
  });
  return parseJsonBody(body);
}

async function getMiniApi(apiPath, data = {}, session = {}, headerParam = {}) {
  const url = new URL(buildMiniUrl(apiPath));
  Object.entries(data || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, value);
  });
  const { body } = await httpRequest(url, {
    method: 'GET',
    headers: { ...miniHeaders(session), ...headerParam },
    timeout: 15000,
  });
  return parseJsonBody(body);
}

async function postMiniApiWithParams(apiPath, urlParam = {}, data = {}, session = {}, headerParam = {}) {
  const url = new URL(buildMiniUrl(apiPath));
  Object.entries(urlParam || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, value);
  });
  const { body } = await httpRequest(url, {
    method: 'POST',
    headers: { ...miniHeaders(session), ...headerParam },
    json: data,
    timeout: 15000,
  });
  return parseJsonBody(body);
}

function assertMiniSession(auth) {
  return !!(auth && auth.sessionId && auth.openId && auth.unionId);
}

async function loadMemberByAuth(auth) {
  if (!assertMiniSession(auth)) throw new Error('小程序登录缓存缺少session/openId/unionId');
  const member = await postMiniApi(
    '/mini/api/login/isLogin',
    { ssoKey: auth.unionId },
    { sessionId: auth.sessionId, openId: auth.openId },
  );
  const token = member && member.token ? member.token : '';
  if (!token) throw new Error(member && (member.message || member.msg) ? member.message || member.msg : '南航isLogin接口未返回会员TOKEN');
  return member;
}

async function getWxCodeFromServer(wxid, index) {
  if (!WX_SERVER_URL) throw new Error('未配置 wx_server_url 或 WX_SERVER_URL');
  const url = new URL(`${WX_SERVER_URL}/mywc`);
  url.searchParams.set('wxid', wxid);
  url.searchParams.set('appId', CSAIR_APPID);
  const { body } = await httpRequest(url, { method: 'GET', headers: { auth: wxid }, timeout: 20000 });
  const data = parseJsonBody(body);
  const code = data?.data?.code || data?.code || data?.data;
  if (!code || typeof code !== 'string') {
    throw new Error(`获取微信Code失败: ${maskWcsMessage(JSON.stringify(data).slice(0, 160))}`);
  }
  console.log(`账号 ${index} 获取微信Code成功: ${maskValue(code)}`);
  return code;
}

async function loginByWcs(account, index) {
  const wxCode = await getWxCodeFromServer(account, index);

  const loginInfo = await postMiniApi('/mini/api/login/login', { code: wxCode });
  const sessionId = loginInfo.sessionId || '';
  const openId = loginInfo.openId || loginInfo.openid || '';
  const unionId = loginInfo.unionId || loginInfo.unionid || '';
  if (!sessionId || !openId || !unionId) {
    throw new Error('南航login接口未返回完整session/openId/unionId');
  }

  const member = await loadMemberByAuth({ sessionId, openId, unionId });
  const token = member.token || '';

  const auth = {
    token,
    sessionId,
    openId,
    unionId,
  };
  saveCachedAuth(account, auth);
  console.log(`账号 ${index} 小程序登录成功: TOKEN=${maskValue(token)}`);
  return { auth, member };
}

async function getMiniAuthForAccount(account, index, forceLogin = false) {
  if (!forceLogin) {
    const cached = getCachedAuth(account);
    if (cached && assertMiniSession(cached)) {
      try {
        const member = await loadMemberByAuth(cached);
        console.log(`账号 ${index} 使用缓存小程序登录态: TOKEN=${maskValue(member.token)}`);
        return { auth: { ...cached, token: member.token || cached.token || '' }, member, fromCache: true };
      } catch (error) {
        console.log(`账号 ${index} 缓存小程序登录态失效: ${error.message || error}`);
        removeCachedAuth(account);
      }
    }
  }

  const result = await loginByWcs(account, index);
  return { ...result, fromCache: false };
}

/**
 * 南航签到客户端
 * @class
 * @example
 * const client = new CsairSignClient();
 */
class CsairSignClient {
  /**
   * 创建签到客户端
   * @param {string} cookie - Cookie字符串
   * @example
   * const client = new CsairSignClient('TOKEN=xxx; channel=csair; ...');
   */
  constructor(cookie) {
    /**
     * Cookie字符串
     * @type {string}
     */
    this.cookie = cookie;
  }

  async h5Post(apiPath, data = {}) {
    const { body } = await httpRequest(buildH5Url(apiPath), {
      method: 'POST',
      headers: h5Headers(this.cookie),
      json: data,
      timeout: 15000,
    });
    return parseJsonBody(body);
  }

  async h5Get(apiPath, params = {}) {
    const url = new URL(buildH5Url(apiPath));
    Object.entries(params || {}).forEach(([key, value]) => {
      if (value !== undefined && value !== null && value !== '') url.searchParams.set(key, value);
    });
    const { body } = await httpRequest(url, {
      method: 'GET',
      headers: h5Headers(this.cookie),
      timeout: 15000,
    });
    return parseJsonBody(body);
  }

  /**
   * 执行签到请求
   * @param {string} signDate - 签到日期 YYYY-MM-DD
   * @returns {Promise<Object>} 返回签到结果
   * @throws {Error} 请求失败时抛出错误
   * @example
   * const result = await client.sign('2025-01-01');
   */
  async sign(signDate) {
    return this.h5Post('/marketing-tools/activity/join', {
      activityType: 'sign',
      channel: 'mini',
      entrance: 1,
      signDate,
    });
  }

  async getAwardContent() {
    return this.h5Get('/marketing-tools/sign/getUserAwardContent');
  }

  async getAwardList(awardStatus = 'waitReceive', pageNum = 1) {
    return this.h5Post('/marketing-tools/award/awardList', {
      activityType: 'sign',
      awardStatus,
      pageNum,
    });
  }

  async getAward(signUserRewardId) {
    return this.h5Post('/marketing-tools/award/getAward', {
      activityType: 'sign',
      signUserRewardId,
    });
  }
}

class CsairMiniSignClient {
  constructor(auth, member) {
    this.auth = auth;
    this.member = member || {};
  }

  miniSession() {
    return { sessionId: this.auth.sessionId, openId: this.auth.openId };
  }

  memberNo() {
    return cookieValue(this.member.cardNo || '');
  }

  async getSignInfo() {
    const memberNo = this.memberNo();
    return getMiniApi(
      '/mini/foundZone/signInPageView',
      { memberNo, signinChannel: 'foundTheZone' },
      this.miniSession(),
      { 'memberNo-encrypt': memberNo },
    );
  }

  async sign() {
    const cert = Array.isArray(this.member.certs)
      ? this.member.certs.find((item) => item && item.certType === 'NI')
      : null;
    return postMiniApiWithParams(
      '/mini/foundZone/signIn',
      { signinChannel: 'foundTheZone' },
      {
        certNo: cert && cert.certNo ? cert.certNo : '',
        memberNo: this.memberNo(),
        phone: this.member.mobile || '',
        userName: this.member.cnFullName || '',
        email: this.member.email || '',
      },
      this.miniSession(),
    );
  }

  canSign() {
    const pear = this.member.pearMemberInfoDto || {};
    return this.member.loginType === 'EM_Y' || pear.identifyStatus === 'Y';
  }

  async getMilesEarnActivity(lotteryParams) {
    const randomId = lotteryParams.randomId || '';
    return postWxopenApi(
      '/activity/api/mileagegift/getActivityInfo',
      {
        randomId,
        memberNo: this.memberNo(),
        helpHandgroupId: lotteryParams.helperId || '',
        pageName: '首页',
        page: `/page/jump?action=milesEarn&MEID=${randomId}`,
        flightInfo: lotteryParams.flightInfo || '',
      },
      this.miniSession(),
    );
  }

  async drawMilesEarn(lotteryParams, signInFlag) {
    return postWxopenApi(
      '/activity/api/mileagegift/draw',
      {
        randomId: lotteryParams.randomId,
        memberNo: this.memberNo(),
        signInGiftId: lotteryParams.signInGiftId,
        signInActivityId: lotteryParams.signInActivityId,
        rewardId: lotteryParams.rewardId,
        signInFlag,
      },
      this.miniSession(),
    );
  }
}

function responseMessage(data) {
  if (!data || typeof data !== 'object') return '';
  return String(data.msg || data.message || data.respMsg || data.resultMessage || data.errorMsg || '');
}

function isH5Success(data) {
  if (!data || typeof data !== 'object') return false;
  const code = data.respCode ?? data.code ?? data.errorCode ?? data.resultCode;
  return (
    code === 0 ||
    code === '0' ||
    code === '0000' ||
    data.success === true ||
    data.status === true ||
    /成功|已签到|已经签到|已领取|已参与|已发放/.test(responseMessage(data))
  );
}

function isH5SignPending(data) {
  return /签到中|请稍等/.test(responseMessage(data));
}

async function signH5WithRetry(h5Client, signDate, index) {
  let result;
  for (let attempt = 1; attempt <= 3; attempt += 1) {
    result = await h5Client.sign(signDate);
    if (!isH5SignPending(result) || attempt === 3) return result;
    console.log(`↻ 账号 ${index} 签到处理中，等待5秒后复查...`);
    await sleep(5000);
  }
  return result;
}

function isAwardLike(item) {
  return !!(
    item &&
    typeof item === 'object' &&
    !Array.isArray(item) &&
    (item.awardStatus || item.signUserRewardId || item.awardName || item.awardNameNum || item.rewardType || item.awardType || item.awardDesc)
  );
}

function collectAwardItems(value, depth = 0) {
  if (!value || depth > 4) return [];
  if (Array.isArray(value)) return value.flatMap((item) => collectAwardItems(item, depth + 1));
  if (typeof value !== 'object') return [];

  const items = isAwardLike(value) ? [value] : [];
  Object.values(value).forEach((item) => {
    items.push(...collectAwardItems(item, depth + 1));
  });
  return items;
}

function extractAwardItems(data) {
  const root = data && data.data !== undefined ? data.data : data;
  const seen = new Set();
  return collectAwardItems(root).filter((item) => {
    const key = getAwardId(item) || `${getAwardStatus(item)}:${getAwardName(item)}:${item.signDay || ''}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function getAwardStatus(item) {
  return String((item && (item.awardStatus || item.status || item.receiveStatus)) || '');
}

function getAwardId(item) {
  if (!item || typeof item !== 'object') return '';
  return String(item.signUserRewardId || item.userRewardId || item.rewardId || item.id || item.awardId || '');
}

function getAwardName(item) {
  if (!item || typeof item !== 'object') return '未知奖励';
  return String(item.awardNameNum || item.awardName || item.rewardName || item.name || item.title || '未知奖励');
}

function isLotteryAward(item) {
  return item && String(item.awardType || '').toLowerCase() === 'lotteryaward';
}

function isReceivableAward(item) {
  const status = getAwardStatus(item);
  return (
    RECEIVABLE_AWARD_STATUSES.has(status) ||
    (isLotteryAward(item) && (status === 'received' || !FINISHED_AWARD_STATUSES.has(status)))
  );
}

function parseAwardDesc(item) {
  if (!item || !item.awardDesc) return {};
  if (typeof item.awardDesc === 'object') return item.awardDesc;
  try {
    return JSON.parse(item.awardDesc);
  } catch (error) {
    return {};
  }
}

function getLotteryUrlFromAward(item) {
  const desc = parseAwardDesc(item);
  return String(desc.lotteryUrl || desc.url || item.lotteryUrl || '');
}

function getResultData(data) {
  if (!data || typeof data !== 'object') return {};
  const root = data.data && typeof data.data === 'object' ? data.data : data;
  return root.result && typeof root.result === 'object' ? root.result : root;
}

function parseLotteryParams(item, result = {}) {
  const lotteryUrl = getLotteryUrlFromAward(item) || String(result.lotteryUrl || '');
  if (!lotteryUrl) return null;
  try {
    const url = new URL(lotteryUrl, CSAIR_MINI_BASE);
    const rewardId = String(item.id || item.signUserRewardId || item.userRewardId || item.rewardId || '');
    if (item.awardId && !url.searchParams.get('signInGiftId')) url.searchParams.set('signInGiftId', item.awardId);
    if (item.activityId && !url.searchParams.get('signInActivityId')) url.searchParams.set('signInActivityId', item.activityId);
    if (rewardId && !url.searchParams.get('rewardId')) url.searchParams.set('rewardId', rewardId);
    return {
      randomId: url.searchParams.get('MEID') || url.searchParams.get('randomId') || String(result.lotteryId || ''),
      signInGiftId: url.searchParams.get('signInGiftId') || '',
      signInActivityId: url.searchParams.get('signInActivityId') || '',
      rewardId: url.searchParams.get('rewardId') || '',
      jumpUrl: lotteryUrl.startsWith('/') ? `${url.pathname}${url.search}` : url.toString(),
    };
  } catch (error) {
    return null;
  }
}

function buildLotteryJumpUrl(item, result) {
  const params = parseLotteryParams(item, result);
  return params ? params.jumpUrl : '';
}

function isMilesDrawSuccess(data) {
  if (!data || typeof data !== 'object') return false;
  return data.code === 'W200' || data.respCode === '0000' || data.success === true || data.status === true;
}

function milesDrawMessage(data) {
  if (!data || typeof data !== 'object') return '未知结果';
  return String(data.message || data.msg || data.respMsg || data.resultMessage || data.giftName || data.giftType || '未知结果');
}

function summarizeAwardItems(items) {
  return items
    .slice(0, 5)
    .map((item) => `${getAwardName(item)}[${getAwardStatus(item) || '-'},${item.awardType || '-'},id=${getAwardId(item) || '-'}]`)
    .join('；');
}

async function getAwardTargets(h5Client, index, awardStatus) {
  const awardList = await h5Client.getAwardList(awardStatus, 1);
  if (!isH5Success(awardList)) {
    console.log(`⚠️ 账号 ${index} ${awardStatus}奖励列表异常: ${responseMessage(awardList) || '未知结果'}`);
    return [];
  }
  const items = extractAwardItems(awardList);
  const totalCount = awardList && awardList.data && awardList.data.totalCount !== undefined ? awardList.data.totalCount : items.length;
  console.log(`🎁 账号 ${index} ${awardStatus}奖励列表: total=${totalCount}, parsed=${items.length}${items.length ? `，${summarizeAwardItems(items)}` : ''}`);
  return items.filter(isReceivableAward);
}

async function handleLotteryAward(miniClient, item, result, index, awardName) {
  const lotteryParams = parseLotteryParams(item, result);
  if (!lotteryParams || !lotteryParams.randomId) {
    console.log(`⚠️ 账号 ${index} 奖励 ${awardName}: 缺少大转盘 MEID，无法抽奖`);
    return;
  }

  console.log(`🎯 账号 ${index} 奖励 ${awardName}: 抽奖入口=${lotteryParams.jumpUrl}`);
  if (!miniClient) {
    console.log(`⚠️ 账号 ${index} 缺少小程序登录态，无法自动点击戳我抽奖`);
    return;
  }
  if (!lotteryParams.signInGiftId || !lotteryParams.signInActivityId || !lotteryParams.rewardId) {
    console.log(`⚠️ 账号 ${index} 奖励 ${awardName}: 缺少签到抽奖参数，无法抽奖`);
    return;
  }

  try {
    const activity = await miniClient.getMilesEarnActivity(lotteryParams);
    const activityData = activity && activity.data && typeof activity.data === 'object' ? activity.data : activity || {};
    const signInFlag = activityData.signInFlag;
    const remain = activityData.freeDrawRemainQuantity;
    console.log(`🎯 账号 ${index} 大转盘状态: 剩余免费抽奖次数=${remain === undefined ? '未知' : remain}`);
    const drawResult = await miniClient.drawMilesEarn(lotteryParams, signInFlag);
    const success = isMilesDrawSuccess(drawResult);
    const prizeName = drawResult && (drawResult.giftName || (drawResult.mileageGiftInfo && drawResult.mileageGiftInfo.name));
    const message = prizeName || milesDrawMessage(drawResult);
    const noChance = /暂无抽奖机会|没有抽奖机会|抽奖次数已用完|已抽奖|已参与/.test(message);
    console.log(`${success ? '✅' : noChance ? '🎯' : '❌'} 账号 ${index} 戳我抽奖: ${message}`);
  } catch (error) {
    console.log(`⚠️ 账号 ${index} 戳我抽奖失败: ${error.message || error}`);
  }
}

async function checkAndClaimAwards(h5Client, index, miniClient = null) {
  if (!h5Client || !h5Client.cookie) return;
  console.log(`🎁 账号 ${index} 开始检查签到奖励`);

  let detailTargets = [];
  try {
    detailTargets = await getAwardTargets(h5Client, index, 'waitReceive');
    if (detailTargets.length === 0) detailTargets = await getAwardTargets(h5Client, index, 'all');
  } catch (error) {
    console.log(`⚠️ 账号 ${index} 待领取奖励列表查询失败: ${error.message || error}`);
  }

  let content;
  try {
    content = await h5Client.getAwardContent();
  } catch (error) {
    console.log(`⚠️ 账号 ${index} 奖励状态查询失败: ${error.message || error}`);
    content = null;
  }

  if (content && !isH5Success(content)) {
    console.log(`⚠️ 账号 ${index} 奖励状态异常: ${responseMessage(content) || '未知结果'}`);
  }

  const contentTargets = content && isH5Success(content) ? extractAwardItems(content).filter(isReceivableAward) : [];
  const targets = detailTargets.length > 0 ? detailTargets : contentTargets;
  if (targets.length === 0) {
    console.log(`🎁 账号 ${index} 暂无待领取/待参与签到奖励`);
    return;
  }

  console.log(`🎁 账号 ${index} 发现 ${targets.length} 个可处理奖励`);
  for (const item of targets) {
    const awardName = getAwardName(item);
    const awardId = getAwardId(item);
    const awardStatus = getAwardStatus(item);
    if (isLotteryAward(item) && awardStatus === 'received') {
      await handleLotteryAward(miniClient, item, {}, index, awardName);
      continue;
    }
    if (!awardId) {
      console.log(`⚠️ 账号 ${index} 奖励 ${awardName} 缺少 signUserRewardId，跳过`);
      continue;
    }

    try {
      const result = await h5Client.getAward(awardId);
      const success = isH5Success(result);
      const data = getResultData(result);
      const lotteryJumpUrl = buildLotteryJumpUrl(item, data);
      const lotteryInfo = [data.lotteryId ? `lotteryId=${data.lotteryId}` : '', lotteryJumpUrl ? `lotteryUrl=${lotteryJumpUrl}` : '']
        .filter(Boolean)
        .join('，');
      console.log(`${success ? '✅' : '❌'} 账号 ${index} 奖励 ${awardName}: ${responseMessage(result) || (success ? '处理成功' : '处理失败')}`);
      if (lotteryInfo) console.log(`🎯 账号 ${index} 抽奖信息: ${lotteryInfo}`);
      if (success && isLotteryAward(item) && lotteryJumpUrl) await handleLotteryAward(miniClient, item, data, index, awardName);
    } catch (error) {
      console.log(`⚠️ 账号 ${index} 奖励 ${awardName} 处理失败: ${error.message || error}`);
    }
  }
}

/**
 * 主流程函数
 * @returns {Promise<void>} 无返回值
 * @throws {Error} 当环境变量未设置或请求失败时抛出错误
 * @example
 * await main();
 */
async function main() {
  if (ACCOUNT_LIST.length === 0) {
    const message = `请先设置环境变量 ${ENV_NAME}；旧 H5 Cookie 可继续使用 csairCookie`;
    console.log(`❌ ${message}`);
    NOTIFY_RESULTS.push({ index: 0, account: '未配置', ok: false, status: '配置错误', message });
    await sendAggregateNotify();
    return;
  }

  console.log(`✅ 共找到 ${ACCOUNT_LIST.length} 个账号配置`);
  for (let i = 0; i < ACCOUNT_LIST.length; i += 1) {
    NOTIFY_RESULTS.push(await handleAccount(ACCOUNT_LIST[i], i + 1));
  }
  await sendAggregateNotify();
}

async function handleAccount(account, index) {
  console.log(`\n======= 账号 ${index} 开始处理 =======`);
  const errcodes = ['ECONNRESET', 'ETIMEDOUT', 'EAI_AGAIN'];
  const today = new Date().toISOString().split('T')[0];
  const accountName = isCookieAccount(account) ? `旧Cookie-${index}` : maskValue(account);

  for (let retry = 1; retry <= 3; retry += 1) {
    try {
      if (isCookieAccount(account)) {
        const client = new CsairSignClient(account);
        const signResult = await signH5WithRetry(client, today, index);
        const signSuccess = showResult(signResult, index);
        const pending = isH5SignPending(signResult);
        if (signSuccess || pending) await checkAndClaimAwards(client, index);
        return { index, account: accountName, ok: signSuccess || pending, status: signSuccess ? '签到成功' : pending ? '签到处理中' : '签到失败', message: getResponseMessage(signResult) };
      }

      const { auth, member } = await getMiniAuthForAccount(account, index);
      const client = new CsairMiniSignClient(auth, member);
      const h5Client = new CsairSignClient(buildH5Cookie(member.token || auth.token));
      if (!client.canSign()) {
        const message = '会员未实名或会员信息不完整';
        console.log(`❌ 账号 ${index} ${message}，跳过签到`);
        return { index, account: accountName, ok: false, status: '账号异常', message };
      }
      const signResult = await signH5WithRetry(h5Client, today, index);
      const signSuccess = showResult(signResult, index);
      const pending = isH5SignPending(signResult);
      if (signSuccess || pending) await checkAndClaimAwards(h5Client, index, client);
      else console.log(`❌ 账号 ${index} 未确认签到成功，跳过奖励检查`);
      return { index, account: accountName, ok: signSuccess || pending, status: signSuccess ? '签到成功' : pending ? '签到处理中' : '签到失败', message: getResponseMessage(signResult) };
    } catch (error) {
      if (errcodes.includes(error.code) && retry < 3) {
        console.log(`↻ 第${retry}次重试中...`);
        await sleep(2000);
        continue;
      }
      const message = error.message || String(error);
      console.log(`⚠️ 账号 ${index} 请求失败: ${message}`);
      return { index, account: accountName, ok: false, status: '执行失败', message };
    }
  }
  return { index, account: accountName, ok: false, status: '执行失败', message: '超过最大重试次数' };
}

function getResponseMessage(data) {
  if (!data || typeof data !== 'object') return '无有效响应';
  return String(data.msg || data.message || data.respMsg || data.resultMessage || '接口未返回说明');
}

function buildNotifyReport() {
  const total = NOTIFY_RESULTS.length;
  const success = NOTIFY_RESULTS.filter((item) => item.ok).length;
  const lines = [
    '==============================',
    `🕒 执行时间：${new Date().toLocaleString('zh-CN', { hour12: false })}`,
    `📊 统计数据：成功 ${success} / 总计 ${total}`,
    `✅ 成功账号：${success} 个`,
    `❌ 失败账号：${total - success} 个`,
    '==============================',
  ];
  for (const item of NOTIFY_RESULTS) {
    lines.push(`${item.ok ? '🧑‍💻' : '🧟'} 【账号${item.index || '-'}】${item.account}`, `${item.ok ? '✅' : '❌'} 状态：${item.status}`, `${item.ok ? '🎁 结果' : '🧨 原因'}：${item.message}`, '------------------------------');
  }
  return lines.join('\n');
}

async function sendAggregateNotify() {
  const content = buildNotifyReport();
  console.log(`\n${content}`);
  if (!QYWX_KEY) return;
  try {
    await httpRequest(`https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=${encodeURIComponent(QYWX_KEY)}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      json: { msgtype: 'text', text: { content: `南方航空签到\n${content}` } },
      timeout: 15000,
    });
    console.log('✅ 企业微信聚合推送成功');
  } catch (error) {
    console.log(`❌ 企业微信聚合推送失败: ${error.message || error}`);
  }
}

function isMiniAlreadySigned(data) {
  const info = data && data.data && typeof data.data === 'object' ? data.data : data || {};
  return String(info.signInFlag || '').toLowerCase() === 'true';
}

function isMiniSuccess(data) {
  if (!data || typeof data !== 'object') return false;
  const code = data.code ?? data.resultCode ?? data.respCode ?? data.errorCode;
  const message = data.msg || data.message || data.resultMessage || data.respMsg || '';
  return (
    code === 0 ||
    code === '0' ||
    code === '0000' ||
    data.success === true ||
    data.status === true ||
    /成功|已签到/.test(String(message))
  );
}

function showMiniResult(data, index, action) {
  const info = data && data.data && typeof data.data === 'object' ? data.data : {};
  const already = isMiniAlreadySigned(data);
  const success = already || isMiniSuccess(data);
  const message = already
    ? '今日已签到'
    : data && (data.msg || data.message || data.resultMessage)
      ? data.msg || data.message || data.resultMessage
      : success
        ? `${action}成功`
        : '未知结果';
  const extraInfo = [];
  if (info.signInNum !== undefined) extraInfo.push(`连续签到天数: ${info.signInNum}`);
  if (info.signInFlag !== undefined) extraInfo.push(`今日签到标记: ${info.signInFlag}`);
  console.log(
    [
      `${success ? '✅' : '❌'} 账号 ${index}`,
      `操作结果: ${message}`,
      `签到状态: ${success ? '成功' : '失败'}`,
      ...(extraInfo.length ? ['--- 小程序签到详情 ---', ...extraInfo] : []),
      '',
    ].join('\n'),
  );
  return success;
}

/**
 * 显示签到结果
 * @param {Object} data - 返回的数据
 * @param {number} index - 账号索引
 * @returns {void} 无返回值
 * @example
 * showResult({ code: 0, msg: 'ok', success: true }, 1);
 */
function showResult(data, index) {
  if (!data) {
    console.log(`❌ 账号 ${index} 无效响应`);
    return false;
  }

  const success =
    data.code === 0 ||
    data.code === '0000' ||
    data.success === true ||
    data.status === true ||
    data.respCode === '0000' ||
    /签到成功|已签到|已经签到/.test(String(data.msg || data.message || data.respMsg || ''));
  const message = data.msg || data.message || data.respMsg || '未知结果';

  const baseInfo = [
    `${success ? '✅' : '❌'} 账号 ${index}`,
    `操作结果: ${message}`,
    `签到状态: ${success ? '成功' : '失败'}`,
  ];

  const extraInfo = [];
  if (data.data && typeof data.data === 'object') {
    if (data.data.result !== undefined) {
      extraInfo.push(`奖励提示: ${data.data.result}`);
    }
    if (data.data.award_num !== undefined) {
      extraInfo.push(`获得奖励数量: ${data.data.award_num}`);
    }
    if (data.data.score !== undefined) {
      extraInfo.push(`当前积分: ${data.data.score}`);
    }
  }

  console.log(
    [
      ...baseInfo,
      ...(extraInfo.length ? ['--- 奖励详情 ---', ...extraInfo] : []),
      '',
    ].join('\n'),
  );
  return success;
}

main().catch((error) => {
  console.error(`脚本执行异常: ${error.message || error}`);
});
