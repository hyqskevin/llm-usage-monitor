// 重新登录脚本：一次性打开全部站点的登录页，逐站检测登录态，人工登录后关闭浏览器即保存。
//
// 2026-11-02 改版原因：
//   旧版只是把页面打开就等着，用户无法知道「哪个站已经登录成功、哪个还没」，
//   且站点跳转登录页时没有任何提示。现在改为逐站轮询检测 + 实时状态表。
//
// 登录态判定方式：**以「是否被重定向到登录页」为主判据**，接口命中作补充。
//   理由：各站登录态失效时都会把页面重定向到登录/passport 页，这个信号最稳定；
//   DOM 文案不可靠（实测 deepseek 的「充值余额」标签抓不到）；接口命中也不完全
//   可靠（bigmodel 的 quota/limit 接口在页面上不自动发起，实测捕获 0 条）。
//
// 用法:
//   node scripts/login.js          # 有头：打开浏览器，逐站检测，人工登录
//   node scripts/login.js --check  # 无头：只检测当前各站登录态并打印结果（不打开窗口）
const { chromium } = require('playwright');
const path = require('path');

const CHECK_ONLY = process.argv.includes('--check');
const PROFILE_DIR = path.join(__dirname, '..', 'browser-profile');

const SITES = [
  {
    key: 'bigmodel', name: '智谱 GLM',
    url: 'https://bigmodel.cn/coding-plan/personal/usage',
    loginUrlRe: /\/login|passport|user\/login/i,
    apiMatch: /api\/monitor\/usage\/quota\/limit/,
    apiValid: (t) => t.includes('"code":200') || t.includes('"code": 200'),
    // 智谱的网页 session 与 API authorization token 是两套鉴权：
    // 网页 session 过期会跳登录页，但只要 api-requests.json 里还留有有效
    // authorization 头，fetch.py 仍能采集成功。此处显式标注，避免误判。
    note: '网页 session 与 API token 独立；session 过期不影响采集（只要 API token 还在）',
    // 兜底：页面不自动发起该接口时，登录成功后主动在页面内 fetch 一次
    probeUrl: '/api/monitor/usage/quota/limit',
    probeMethod: 'GET',
  },
  {
    key: 'kimi', name: 'Kimi',
    url: 'https://www.kimi.com/membership/subscription?tab=quota',
    loginUrlRe: /\/login|passport|sign_?in/i,
    apiMatch: /GetSubscriptionStats/,
    apiValid: (t) => t.includes('ratelimitCode5h') || t.includes('subscriptionBalance'),
    // kimi 的页面会自发请求该接口并带 authorization，被动捕获即可；
    // 这里不配 probeUrl（实测主动 fetch 反而拿不到 authorization）。
    // 2026-11-12：kimi 未登录时不跳 /login，而是重定向回裸首页 ——
    // 纯 URL 判据会误报「已登录」，用 homeRedirect 兜底（见 check()）。
    homeRedirect: 'https://www.kimi.com/',
  },
  {
    key: 'minimax', name: 'MiniMax',
    url: 'https://platform.minimax.cn/console/usage',
    loginUrlRe: /unified-login|passport|\/login/i,
    apiMatch: /token_plan\/remains_percent/,
    apiValid: (t) => t.includes('"status_code":0'),
  },
  {
    key: 'deepseek', name: 'DeepSeek',
    url: 'https://platform.deepseek.com/usage',
    loginUrlRe: /passport|sign_?in|\/login/i,
    apiMatch: /api\/v0\/users\/get_user_summary/,
    apiValid: (t) => t.includes('normal_wallets'),
  },
  {
    key: 'mimo', name: 'MiMo',
    url: 'https://platform.xiaomimimo.com/console/plan-manage?edition=PERSONAL',
    loginUrlRe: /account\.xiaomi\.com|passport|\/login/i,
    apiMatch: /api\/v1\/tokenPlan\/usage/,
    apiValid: (t) => t.includes('plan_total_token'),
  },
];

(async () => {
  // 预检：Playwright 版本与已下载的浏览器不匹配时，launchPersistentContext 会抛
  // 一大段英文安装提示，很难看出是「版本错配」。这里提前检测并给出中文处置指引。
  try {
    const exe = chromium.executablePath();
    const fs = require('fs');
    if (!fs.existsSync(exe)) {
      console.error('❌ Playwright 浏览器缺失或版本不匹配：');
      console.error('   期望路径: ' + exe);
      console.error('   修复: cd ~/.workbuddy/binaries/node/workspace && \\');
      console.error('        node node_modules/playwright-core/cli.js install chromium');
      process.exit(3);
    }
  } catch (e) {
    // executablePath 在某些版本下不抛错，忽略即可
  }

  const context = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: CHECK_ONLY,
    viewport: { width: 1400, height: 900 },
    args: ['--disable-blink-features=AutomationControlled'],
  });

  if (!CHECK_ONLY) console.log('浏览器已打开，正在为各站点加载页面并检测登录态：\n');

  // 每个站点一个独立 tab，避免互相干扰
  const pages = {};
  const hits = {};   // site.key -> true 表示已观测到有效接口响应
  const errors = {}; // site.key -> 最近一次接口错误摘要
  const capturedAuth = {}; // site.key -> 带 authorization 头的请求（供落盘）

  for (const site of SITES) {
    hits[site.key] = false;
    errors[site.key] = null;
    const page = await context.newPage();
    pages[site.key] = page;

    page.on('request', req => {
      if (!site.apiMatch.test(req.url())) return;
      // 记录带鉴权头的请求，供落盘到 api-requests.json
      const h = req.headers();
      if (h['authorization']) {
        capturedAuth[site.key] = {
          url: req.url(),
          method: req.method(),
          headers: h,
          postData: req.postData() || null,
        };
      }
    });

    page.on('response', async resp => {
      if (!site.apiMatch.test(resp.url())) return;
      let body = '';
      try { body = (await resp.text()).slice(0, 20000); } catch (e) {}
      if (resp.status() === 200 && site.apiValid(body)) {
        hits[site.key] = true;
      } else {
        errors[site.key] = `HTTP ${resp.status()}`;
        // 登录态失效时 Kimi 会返回 401
        if (resp.status() === 401 || resp.status() === 403) errors[site.key] = `HTTP ${resp.status()}（登录态失效）`;
      }
    });
  }

  // 加载各站点页面（先挂监听再 goto，确保首次加载的响应也能捕获）
  for (const site of SITES) {
    await pages[site.key].goto(site.url, { waitUntil: 'domcontentloaded', timeout: 45000 }).catch(() => {});
  }

  // 兜底探测：页面不自动发起用量接口时（如 bigmodel），在页面上下文中主动 fetch 一次。
  // 用页面内 fetch 而非 context.request，是为了让浏览器自动带上该站的鉴权头/cookie。
  async function probe(site) {
    if (!site.probeUrl) return;
    const page = pages[site.key];
    if (site.loginUrlRe.test(page.url())) return;   // 还没登录，不探测
    try {
      await page.evaluate(async ({ url, method, body }) => {
        const opt = { method, credentials: 'include', headers: { 'content-type': 'application/json' } };
        if (method !== 'GET' && body) opt.body = body;
        await fetch(url, opt).then(r => r.text()).catch(() => {});
      }, { url: site.probeUrl, method: site.probeMethod || 'GET', body: site.probeBody || null });
    } catch (e) {}
  }

  // 判定：未被重定向到登录页 = 已登录；被重定向 = 未登录。
  // 接口命中只作为补充证据（bigmodel 的接口不自动发起，只能靠 URL 判定）。
  //
  // 2026-11-12 修：kimi 未登录时**不跳登录页**，而是把受保护页重定向回
  // 首页（www.kimi.com/），URL 判据永远为真 → --check 误报「✅ 已登录」。
  // 给 kimi 加 homeRedirect 判据：目标页带着 ?tab=quota 打开、几秒后
  // 落在裸首页 = 登录态被踢。
  const check = (site) => {
    const url = pages[site.key].url();
    if (site.loginUrlRe.test(url)) return false;
    if (site.homeRedirect && url === site.homeRedirect) return false;
    return true;
  };

  // --check 模式：等待页面加载完再判定
  if (CHECK_ONLY) {
    await new Promise(r => setTimeout(r, 8000));
    for (const site of SITES) await probe(site);
    await new Promise(r => setTimeout(r, 2000));
    let bad = 0;
    for (const site of SITES) {
      const ok = check(site);
      if (!ok) bad++;
      const url = pages[site.key].url();
      console.log(`${ok ? '✅ 已登录' : '❌ 未登录'}  ${site.name}`);
      console.log(`     ${url.slice(0, 100)}`);
      if (!ok && site.note) console.log(`     ⚠️  ${site.note}`);
    }
    await context.close();
    console.log(bad === 0 ? '\n全部站点均已登录。' : `\n${bad} 个站点网页登录态失效，需运行 node scripts/login.js 人工登录。`);
    process.exit(bad === 0 ? 0 : 1);
  }

  // 轮询：人工登录后页面会重新发起用量接口，hits 随之翻转
  const deadline = Date.now() + 15 * 60 * 1000; // 15 分钟
  let lastLine = '';
  const probed = {};
  while (Date.now() < deadline) {
    const results = SITES.map(s => [s.name, check(s)]);
    const line = results.map(([n, ok]) => `${ok ? '✅' : '⭕️'} ${n}`).join('   ');
    if (line !== lastLine) {
      console.log(line);
      lastLine = line;
    }
    // 刚转为已登录的站点：主动 probe 一次，确保能抓到鉴权头
    for (const site of SITES) {
      if (check(site) && !probed[site.key]) {
        probed[site.key] = true;
        await probe(site);
      }
    }
    if (results.every(([, ok]) => ok)) {
      console.log('\n✅ 全部站点均已登录。直接关闭浏览器窗口即保存登录态。');
      break;
    }
    await new Promise(r => setTimeout(r, 2000));
  }

  if (!lastLine) console.log('（检测超时，可继续手动登录后关闭浏览器）');

  // 关闭浏览器 → persistent context 自动落盘 cookie / localStorage
  await new Promise(resolve => context.on('close', resolve));
  console.log('\n登录态已保存到:', PROFILE_DIR);

  // 把 probe 期间捕获到的鉴权头直接并入 api-requests.json。
  // 优先于后续 discover_api.js 的结果——discover_api.js 依赖页面自发请求，
  // 对 bigmodel 这类不自动发接口的站点会抓到 0 条，误覆盖有效 token。
  //
  // 2026-11-10 追加：写入前**校验 token 是否真的可用**。
  // 只看「有没有 authorization」是不够的——会话失效时页面仍可能带出一个
  // 已被服务端拒绝的旧 token（bigmodel 的 token 无 exp 字段，从 JWT 判断不了）。
  // 实调一次接口确认 code==200 才写入，避免把失效 token 落盘导致采集永久失败。
  const REQ_JSON = path.join(__dirname, '..', 'data', 'api-requests.json');
  try {
    const fs = require('fs');
    const merged = fs.existsSync(REQ_JSON)
      ? JSON.parse(fs.readFileSync(REQ_JSON, 'utf8'))
      : {};
    let wrote = 0;
    for (const [key, req] of Object.entries(capturedAuth)) {
      if (!req || !req.headers || !req.headers['authorization']) continue;
      // 校验：实调一次该接口，确认服务端接受这个 token
      let valid = true, reason = '';
      try {
        const resp = await context.request.fetch(req.url, {
          method: req.method || 'GET',
          headers: req.headers,
          data: req.postData || undefined,
          timeout: 20000,
        });
        const body = await resp.text();
        if (resp.status() === 401 || resp.status() === 403) {
          valid = false; reason = `HTTP ${resp.status()}`;
        } else {
          let biz = null;
          try { biz = JSON.parse(body); } catch (e) {}
          // bigmodel：HTTP 200 但 body 里 code!=200 表示业务鉴权失败
          if (biz && typeof biz.code !== 'undefined' && biz.code !== 200) {
            valid = false; reason = biz.msg || `code=${biz.code}`;
          }
        }
      } catch (e) {
        valid = false; reason = e.message;
      }
      if (!valid) {
        console.log(`  ⚠️  ${key} 捕获的 token 校验未通过（${reason}），不写入`);
        continue;
      }
      merged[key] = [req];
      wrote++;
      console.log(`  ✅ ${key} 鉴权头已写入 api-requests.json（校验通过）`);
    }
    if (wrote) fs.writeFileSync(REQ_JSON, JSON.stringify(merged, null, 2));
  } catch (e) {
    console.log('  ⚠️ 写入 api-requests.json 失败:', e.message);
  }

  // 立即把新登录态转成可直连 API 的鉴权材料，避免用户还要等 fetch.py 失败一次。
  // dump_cookies.js         导出 cookie           → data/cookies.json
  // minimax_refresh_auth.js 抓 minimax 鉴权头     → data/minimax-auth.json
  // mimo_refresh_session.js 签发 mimo 会话 cookie  → data/mimo-session.json
  // 注意：不跑 discover_api.js —— 它在页面不自发请求时会抓到 0 条，
  // 反而覆盖上面刚写入的有效 authorization 头。
  console.log('正在同步鉴权材料（cookie / authorization）...');
  const { execFileSync } = require('child_process');
  for (const s of ['dump_cookies.js', 'minimax_refresh_auth.js', 'mimo_refresh_session.js']) {
    try {
      const out = execFileSync(process.execPath, [path.join(__dirname, s)], {
        cwd: path.join(__dirname, '..'),
        env: { ...process.env },
        encoding: 'utf8',
        timeout: 120000,
      });
      console.log(`  ✅ ${s}: ${out.trim().split('\n').filter(Boolean).slice(-1)[0]}`);
    } catch (e) {
      const msg = (e.stderr || e.message || '').toString().trim().split('\n').filter(Boolean).slice(-2).join(' | ');
      console.log(`  ⚠️  ${s} 失败: ${msg}`);
    }
  }
  console.log('\n完成。SwiftBar 下次刷新（最多 10 分钟）即会显示最新数据。');
})().catch(e => { console.error('ERROR:', e); process.exit(1); });
