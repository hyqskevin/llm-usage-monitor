// MiMo 抓包脚本：复用已登录的持久化 profile，打开套餐用量页，
// 抓 platform.xiaomimimo.com 的 XHR/fetch 请求（url/method/headers/postData + 响应 body），
// 合并写入 data/api-requests.json 的 mimo 键（不会抹掉其他站点条目），
// 同时把响应原文存 data/mimo-requests-raw.json 供人工核对 API 形状。
// 用法: node scripts/mimo_discover.js
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const ROOT = path.join(__dirname, '..');
const OUT_JSON = path.join(ROOT, 'data', 'api-requests.json');
const OUT_RAW = path.join(ROOT, 'data', 'mimo-requests-raw.json');
const URL = 'https://platform.xiaomimimo.com/console/plan-manage?edition=PERSONAL';

const isApi = (u) => u.includes('xiaomimimo.com') &&
  (u.includes('/api/') || u.includes('/console/') || u.includes('usage') || u.includes('quota') || u.includes('plan'));

(async () => {
  const context = await chromium.launchPersistentContext(path.join(ROOT, 'browser-profile'), {
    headless: true,
    viewport: { width: 1400, height: 900 },
    args: ['--disable-blink-features=AutomationControlled'],
  });
  const page = await context.newPage();

  const apiRequests = [];
  page.on('request', req => {
    const u = req.url();
    const rt = req.resourceType();
    if (isApi(u) && (rt === 'xhr' || rt === 'fetch' || u.includes('/api/'))) {
      apiRequests.push({
        url: u, method: req.method(), resourceType: rt,
        headers: req.headers(), postData: req.postData() || null,
        status: null, responseBody: null,
      });
    }
  });
  const respMap = new Map();
  page.on('response', async resp => {
    const u = resp.url();
    const rt = resp.request().resourceType();
    if (isApi(u) && (rt === 'xhr' || rt === 'fetch' || u.includes('/api/'))) {
      let body = null;
      try { body = (await resp.body()).toString().slice(0, 50_000); } catch (e) {}
      respMap.set(u, { status: resp.status(), body });
    }
  });

  console.log('→ 打开 MiMo 套餐用量页...');
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60000 });
  // 等 hydrate：额度数字出现（抓不到也没关系，后面兜底等 10s）
  await page.waitForFunction(() => /\d/.test(document.body.innerText) && !location.host.includes('account.xiaomi'), { timeout: 20000 }).catch(() => {});
  await page.waitForTimeout(10000);

  for (const r of apiRequests) {
    const resp = respMap.get(r.url);
    if (resp) { r.status = resp.status; r.responseBody = resp.body; }
  }

  // 登录态校验：若被重定向到 account.xiaomi.com 则判定未登录
  const finalUrl = page.url();
  const text = await page.evaluate(() => document.body ? document.body.innerText : '');
  fs.writeFileSync(path.join(ROOT, 'data', 'mimo-probe.json'), JSON.stringify({
    url: finalUrl, title: await page.title(), innerText: text.slice(0, 8000),
  }, null, 2));
  await page.screenshot({ path: path.join(ROOT, 'data', 'mimo-probe.png'), fullPage: false });

  // 合并写入 api-requests.json：只更新 mimo 键，且抓到 0 条时保留旧条目
  let merged = { mimo: apiRequests };
  if (fs.existsSync(OUT_JSON)) {
    try {
      const existing = JSON.parse(fs.readFileSync(OUT_JSON, 'utf8'));
      merged = { ...existing, mimo: apiRequests };
      if (apiRequests.length === 0 && Array.isArray(existing.mimo) && existing.mimo.length > 0) {
        merged.mimo = existing.mimo;
        console.log(`[mimo] 本次未捕获，保留旧条目 ${existing.mimo.length} 条`);
      }
    } catch (e) {
      console.log('读取已有 api-requests.json 失败，按全量写入:', e.message);
    }
  }
  fs.writeFileSync(OUT_JSON, JSON.stringify(merged, null, 2));
  fs.writeFileSync(OUT_RAW, JSON.stringify(apiRequests, null, 2));

  console.log(`=== 抓包结果（${apiRequests.length} 条）===`);
  for (const r of apiRequests) {
    console.log(`  ${r.method} ${r.status || '?'} ${r.url}`);
  }
  if (finalUrl.includes('account.xiaomi')) {
    console.log('⚠️ 当前处于小米账号登录页，登录态已失效，请运行: node scripts/mimo_login.js');
    process.exitCode = 2;
  }
  await context.close();
  console.log('已保存: data/api-requests.json / data/mimo-requests-raw.json / data/mimo-probe.png');
})().catch(e => { console.error('ERROR:', e); process.exit(1); });
