// 抓取目标 API 的完整请求信息（method/headers/postData），用于构造直接 HTTP 调用
// 注意：deepseek 不在此列 —— deepseek 由 scripts/deepseek_discover.js 独立负责（鉴权靠 localStorage
// 复用 Playwright persistent context，跟其他站点的 cookie+header 鉴权方式完全不同）。
// 在这里跑 deepseek 只会覆盖 api-requests.json 里已有的 6 条 API 为空数组。
const { chromium } = require('playwright');
const { launchLocked } = require('./profile_lock');
const path = require('path');
const fs = require('fs');

const ROOT = path.join(__dirname, '..');

const TARGETS = [
  {
    key: 'bigmodel',
    url: 'https://bigmodel.cn/coding-plan/personal/usage',
    match: /api\/monitor\/usage\/quota\/limit/,
    // 2026-11-10 修：bigmodel 的 quota/limit 接口在页面上**不会自动发起**，
    // 原实现只被动监听，抓包必然 0 条 —— 会话一失效就无法自动恢复，
    // 只能人工重登（这正是 11-05 起 GLM 连续失败 5 天的根因）。
    // 改为：加载页面后主动在页面上下文中 fetch 一次该接口，
    // 让浏览器自动带上该站的鉴权头/cookie。
    probeUrl: '/api/monitor/usage/quota/limit',
    probeMethod: 'GET',
  },
  {
    key: 'kimi',
    url: 'https://www.kimi.com/membership/subscription?tab=quota&from=kfc_console_upgrade',
    match: /GetSubscriptionStats/,
    // kimi 的页面会自发请求该接口并带 authorization，被动捕获即可；
    // 主动 fetch 反而拿不到 authorization（实测），故不配 probeUrl。
  },
];

(async () => {
  const context = await launchLocked(chromium, path.join(ROOT, 'browser-profile'), {
    headless: true,
    args: ['--disable-blink-features=AutomationControlled'],
  }, { timeoutMs: 20000, label: 'discover_api' })
  const out = {};
  for (const t of TARGETS) {
    const page = await context.newPage();
    const found = [];
    page.on('request', req => {
      if (t.match.test(req.url())) {
        found.push({
          url: req.url(),
          method: req.method(),
          headers: req.headers(),
          postData: req.postData(),
        });
      }
    });
    await page.goto(t.url, { waitUntil: 'domcontentloaded', timeout: 45000 });
    // 2026-09-24 修：kimi 在 access_token 过期窗口内会先发一条「不带 authorization」的请求，
    // 等 refresh 完成后才发带 token 的那条。原实现固定等 8 秒 + 按 [0] 取第一条，
    // 会拿到那条无效请求，导致 fetch 端稳定 401。改为轮询等「带 authorization 的请求」出现。
    for (let i = 0; i < 15; i++) {
      if (found.some(r => r.headers && r.headers['authorization'])) break;
      await page.waitForTimeout(1000);
    }
    // 2026-11-10 修：bigmodel 这类「页面不自动发接口」的站点，
    // 等再久也等不到被动捕获 —— 必须主动在页面上下文中 fetch 一次。
    if (t.probeUrl && !found.some(r => r.headers && r.headers['authorization'])) {
      try {
        await page.evaluate(async ({ url, method }) => {
          const opt = { method, credentials: 'include', headers: { 'content-type': 'application/json' } };
          await fetch(url, opt).then(r => r.text()).catch(() => {});
        }, { url: t.probeUrl, method: t.probeMethod || 'GET' });
        await page.waitForTimeout(1500);
      } catch (e) {
        console.log(`[${t.key}] 主动探测失败: ${e.message}`);
      }
    }
    await page.waitForTimeout(2000);
    const withAuth = found.filter(r => r.headers && r.headers['authorization']);
    if (withAuth.length) {
      out[t.key] = withAuth;
      console.log(`[${t.key}] 捕获 ${found.length} 个匹配请求（其中 ${withAuth.length} 条带 authorization）`);
    } else {
      out[t.key] = found;
      console.log(`[${t.key}] 捕获 ${found.length} 个匹配请求（均无 authorization）`);
    }
    await page.close();
  }
  // 合并写入（2026-09-24 修）：原实现是全量覆盖，只写 {bigmodel, kimi}，
  // 会抹掉 deepseek 条目；且某站点抓 0 条时会把可用鉴权覆盖成空数组，
  // 导致 fetch_bigmodel/fetch_kimi 抛 list index out of range。
  //
  // 2026-11-02 追加：只抓到了「不带 authorization」的请求时也不能覆盖。
  // kimi 在 access_token 刷新窗口内会先发一条无 token 的请求，若把它当成新鉴权
  // 写入，fetch_kimi 会稳定 401。判定标准：有且仅有「带 authorization 的条目」才覆盖。
  //
  // 2026-11-10 追加：**校验捕获到的 token 是否真的可用**。
  // 只看「有没有 authorization」不够——会话失效时页面仍可能带出一个
  // 已被服务端拒绝的旧 token（bigmodel 的 token 无 exp 字段，从 JWT 上
  // 判断不了）。这里实调一次接口确认 code==200 才写入，
  // 避免把失效 token 覆盖到本来还能用的旧鉴权上。
  const OUT = path.join(ROOT, 'data', 'api-requests.json');

  // 用捕获到的请求头实调一次接口，验证 token 是否被服务端接受
  async function verifyAuth(key, req) {
    if (!req || !req.headers) return { ok: false, reason: '无请求头' };
    const auth = req.headers['authorization'];
    if (!auth) return { ok: false, reason: '无 authorization' };
    try {
      const resp = await context.request.fetch(req.url, {
        method: req.method || 'GET',
        headers: req.headers,
        data: req.postData || undefined,
        timeout: 20000,
      });
      const body = await resp.text();
      // bigmodel：HTTP 200 但 body 里 code!=200 表示业务鉴权失败
      let biz = null;
      try { biz = JSON.parse(body); } catch (e) {}
      if (resp.status() === 401 || resp.status() === 403) {
        return { ok: false, reason: `HTTP ${resp.status()}` };
      }
      if (biz && typeof biz.code !== 'undefined' && biz.code !== 200) {
        return { ok: false, reason: biz.msg || `code=${biz.code}` };
      }
      return { ok: true };
    } catch (e) {
      return { ok: false, reason: e.message };
    }
  }

  let merged = { ...out };
  if (fs.existsSync(OUT)) {
    try {
      const existing = JSON.parse(fs.readFileSync(OUT, 'utf8'));
      merged = { ...existing, ...out };
      for (const k of Object.keys(out)) {
        const newHasAuth = (out[k] || []).some(r => r.headers && r.headers['authorization']);
        const oldHasAuth = Array.isArray(existing[k]) &&
                          existing[k].some(r => r.headers && r.headers['authorization']);
        if (!newHasAuth && oldHasAuth) {
          merged[k] = existing[k];
          console.log(`[${k}] 本次未捕获带 authorization 的请求，保留旧鉴权 ${existing[k].length} 条`);
          continue;
        }
        // 有新 token：校验是否真的可用，不可用则保留旧鉴权
        if (newHasAuth) {
          const cand = (out[k] || []).filter(r => r.headers && r.headers['authorization'])[0];
          const v = await verifyAuth(k, cand);
          if (!v.ok) {
            merged[k] = existing[k];
            console.log(`[${k}] 新 token 校验未通过（${v.reason}），保留旧鉴权`);
          } else {
            console.log(`[${k}] 新 token 校验通过`);
          }
        }
      }
    } catch (e) {
      console.log('读取已有 api-requests.json 失败，按全量写入:', e.message);
    }
  }
  fs.writeFileSync(OUT, JSON.stringify(merged, null, 2));
  await context.close();
  console.log('已写入 data/api-requests.json');
})();
