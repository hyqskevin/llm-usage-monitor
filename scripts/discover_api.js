// 抓取目标 API 的完整请求信息（method/headers/postData），用于构造直接 HTTP 调用
const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const ROOT = path.join(__dirname, '..');

const TARGETS = [
  {
    key: 'bigmodel',
    url: 'https://bigmodel.cn/coding-plan/personal/usage',
    match: /api\/monitor\/usage\/quota\/limit/,
  },
  {
    key: 'kimi',
    url: 'https://www.kimi.com/membership/subscription?tab=quota&from=kfc_console_upgrade',
    match: /GetSubscriptionStats/,
  },
];

(async () => {
  const context = await chromium.launchPersistentContext(path.join(ROOT, 'browser-profile'), {
    headless: true,
    args: ['--disable-blink-features=AutomationControlled'],
  });
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
    await page.waitForTimeout(8000);
    out[t.key] = found;
    console.log(`[${t.key}] 捕获 ${found.length} 个匹配请求`);
    await page.close();
  }
  fs.writeFileSync(path.join(ROOT, 'data', 'api-requests.json'), JSON.stringify(out, null, 2));
  await context.close();
  console.log('已写入 data/api-requests.json');
})();
