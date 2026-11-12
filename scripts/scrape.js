// 用量抓取脚本：复用持久化登录态，抓取三个站点用量页
// 输出: data/raw/<site>-<ts>.json (API响应), <site>-<ts>.txt (页面文本), <site>-<ts>.png (截图)
//       data/latest.json (结构化摘要，供 SwiftBar / dashboard 使用)
// 用法: node scrape.js [--headful]
const { chromium } = require('playwright');
const { launchLocked } = require('./profile_lock');
const path = require('path');
const fs = require('fs');

const ROOT = path.join(__dirname, '..');
const PROFILE_DIR = path.join(ROOT, 'browser-profile');
const RAW_DIR = path.join(ROOT, 'data', 'raw');
const LATEST = path.join(ROOT, 'data', 'latest.json');

const HEADFUL = process.argv.includes('--headful');

const SITES = [
  {
    key: 'bigmodel',
    name: '智谱 BigModel',
    url: 'https://bigmodel.cn/coding-plan/personal/usage',
    apiHints: [/usage/i, /quota/i, /billing/i, /coding/i],
  },
  {
    key: 'kimi',
    name: 'Kimi Code',
    url: 'https://www.kimi.com/membership/subscription?tab=quota&from=kfc_console_upgrade',
    apiHints: [/usage/i, /quota/i, /membership/i, /subscription/i],
  },
  {
    key: 'minimax',
    name: 'MiniMax',
    url: 'https://platform.minimaxi.com/console/usage',
    apiHints: [/usage/i, /quota/i, /billing/i, /balance/i],
  },
];

(async () => {
  const context = await launchLocked(chromium, PROFILE_DIR, {
    headless: !HEADFUL,
    viewport: { width: 1400, height: 900 },
    args: ['--disable-blink-features=AutomationControlled'],
  }, { timeoutMs: 20000, label: 'scrape' })

  const ts = new Date().toISOString().replace(/[:.]/g, '-');
  const result = { scraped_at: new Date().toISOString(), sites: {} };

  for (const site of SITES) {
    const page = await context.newPage();
    const apiResponses = [];
    page.on('response', async res => {
      try {
        const url = res.url();
        if (res.request().resourceType() === 'xhr' || res.request().resourceType() === 'fetch') {
          if (site.apiHints.some(re => re.test(url))) {
            const ct = res.headers()['content-type'] || '';
            if (ct.includes('json')) {
              const body = await res.json().catch(() => null);
              if (body) apiResponses.push({ url, body });
            }
          }
        }
      } catch (_) {}
    });

    const entry = { ok: false, url: site.url };
    try {
      await page.goto(site.url, { waitUntil: 'domcontentloaded', timeout: 45000 });
      await page.waitForTimeout(8000); // 等待前端渲染与 XHR 完成

      const text = await page.evaluate(() => document.body.innerText).catch(() => '');
      const shot = path.join(RAW_DIR, `${site.key}-${ts}.png`);
      await page.screenshot({ path: shot, fullPage: false }).catch(() => {});

      fs.writeFileSync(path.join(RAW_DIR, `${site.key}-${ts}.txt`), text);
      fs.writeFileSync(path.join(RAW_DIR, `${site.key}-${ts}.json`),
        JSON.stringify(apiResponses, null, 2));

      // 未登录探测：页面出现登录/手机号/验证码等关键词
      const needLogin = /登录|登陆|手机号|验证码|sign in|log in/i.test(text) && text.length < 500;
      entry.ok = !needLogin;
      entry.need_login = needLogin;
      entry.text_len = text.length;
      entry.api_count = apiResponses.length;
      entry.text_sample = text.slice(0, 800);
      entry.api_urls = apiResponses.map(r => r.url);
      console.log(`[${site.name}] ok=${entry.ok} textLen=${text.length} api=${apiResponses.length}`);
    } catch (e) {
      entry.error = String(e).slice(0, 300);
      console.log(`[${site.name}] ERROR: ${entry.error}`);
    }
    result.sites[site.key] = entry;
    await page.close();
  }

  fs.writeFileSync(LATEST, JSON.stringify(result, null, 2));
  await context.close();
  console.log('完成，摘要写入', LATEST);
})();
