// MiniMax 探测脚本：无头打开用量页（.com / .cn 各一次），看登录态落在哪、
// 抓 XHR/fetch 请求的 URL + 响应体，确认真实 API 与鉴权方式。
// 用法: node scripts/minimax_probe.js
const { chromium } = require('playwright');
const { launchLocked } = require('./profile_lock');
const path = require('path');
const fs = require('fs');

const ROOT = path.join(__dirname, '..');
const TARGETS = [
  { key: 'com', url: 'https://platform.minimaxi.com/console/usage' },
  { key: 'cn', url: 'https://platform.minimax.cn/console/usage' },
];

(async () => {
  const context = await launchLocked(chromium, path.join(ROOT, 'browser-profile'), {
    headless: true,
    viewport: { width: 1400, height: 900 },
    args: ['--disable-blink-features=AutomationControlled'],
  }, { timeoutMs: 20000, label: 'minimax_probe' })

  for (const t of TARGETS) {
    const page = await context.newPage();
    const apiRequests = [];
    page.on('request', req => {
      const u = req.url();
      const rt = req.resourceType();
      if ((rt === 'xhr' || rt === 'fetch') && (u.includes('minimaxi.com') || u.includes('minimax.cn'))) {
        apiRequests.push({ url: u, method: req.method(), headers: req.headers(), postData: req.postData() || null, status: null, responseBody: null });
      }
    });
    const respMap = new Map();
    page.on('response', async resp => {
      const u = resp.url();
      const rt = resp.request().resourceType();
      if ((rt === 'xhr' || rt === 'fetch') && (u.includes('minimaxi.com') || u.includes('minimax.cn'))) {
        let body = null;
        try { body = (await resp.body()).toString().slice(0, 8000); } catch (e) {}
        respMap.set(u, { status: resp.status(), body });
      }
    });

    console.log(`\n########## [${t.key}] ${t.url}`);
    await page.goto(t.url, { waitUntil: 'domcontentloaded', timeout: 60000 }).catch(e => console.log('goto:', e.message));
    await page.waitForTimeout(10000);
    for (const r of apiRequests) {
      const resp = respMap.get(r.url);
      if (resp) { r.status = resp.status; r.responseBody = resp.body; }
    }
    console.log('final url:', page.url());
    const text = await page.evaluate(() => document.body ? document.body.innerText.slice(0, 600) : '');
    console.log('innerText:', JSON.stringify(text));
    for (const r of apiRequests) {
      console.log(`  ${r.method} ${r.status || '?'} ${r.url}`);
    }
    fs.writeFileSync(path.join(ROOT, 'data', `minimax-probe-${t.key}.json`), JSON.stringify({ finalUrl: page.url(), innerText: text, apiRequests }, null, 2));
    await page.close();
  }
  await context.close();
  console.log('\n已保存: data/minimax-probe-com.json / minimax-probe-cn.json');
})().catch(e => { console.error('ERROR:', e); process.exit(1); });
