// 探测脚本：无头打开 MiMo 套餐用量页，截图 + innerText + 抓 API 请求，判断登录态。
// 用法: node scripts/mimo_probe.js
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const ROOT = path.join(__dirname, '..');
const URL = 'https://platform.xiaomimimo.com/console/plan-manage?edition=PERSONAL';

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
    if (u.includes('xiaomimimo.com') && (u.includes('/api') || u.includes('/console') || req.resourceType() === 'xhr' || req.resourceType() === 'fetch')) {
      apiRequests.push({
        url: u, method: req.method(), resourceType: req.resourceType(),
        headers: req.headers(), postData: req.postData() || null,
        status: null, responseBody: null,
      });
    }
  });
  const respMap = new Map();
  page.on('response', async resp => {
    const u = resp.url();
    if (u.includes('xiaomimimo.com') && (u.includes('/api') || u.includes('/console') || resp.request().resourceType() === 'xhr' || resp.request().resourceType() === 'fetch')) {
      let body = null;
      try { body = (await resp.body()).toString().slice(0, 20_000); } catch (e) {}
      respMap.set(u, { status: resp.status(), body });
    }
  });

  console.log('→ 打开', URL);
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(e => console.log('goto:', e.message));
  await page.waitForTimeout(10000);

  for (const r of apiRequests) {
    const resp = respMap.get(r.url);
    if (resp) { r.status = resp.status; r.responseBody = resp.body; }
  }

  const text = await page.evaluate(() => document.body ? document.body.innerText : '');
  fs.writeFileSync(path.join(ROOT, 'data', 'mimo-probe.json'), JSON.stringify({
    url: page.url(), title: await page.title(), innerText: text.slice(0, 6000),
  }, null, 2));
  await page.screenshot({ path: path.join(ROOT, 'data', 'mimo-probe.png'), fullPage: false });

  console.log('=== 最终 URL ===');
  console.log(page.url());
  console.log('=== innerText（前 3000 字）===');
  console.log(text.slice(0, 3000));
  console.log('\n=== API 请求 ===');
  for (const r of apiRequests) {
    console.log(`  ${r.method} ${r.status || '?'} ${r.url}`);
  }
  fs.writeFileSync(path.join(ROOT, 'data', 'mimo-requests-raw.json'), JSON.stringify(apiRequests, null, 2));

  await context.close();
  console.log('\n已保存: data/mimo-probe.json / mimo-probe.png / mimo-requests-raw.json');
})().catch(e => { console.error('ERROR:', e); process.exit(1); });
