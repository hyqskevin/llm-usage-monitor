// 从持久化 profile 导出三个站点的 cookies 到 data/cookies.json（供直接 HTTP 调 API 用）
const { chromium } = require('playwright');
const { launchLocked } = require('./profile_lock');
const path = require('path');
const fs = require('fs');

const ROOT = path.join(__dirname, '..');
const OUT = path.join(ROOT, 'data', 'cookies.json');
const DOMAINS = ['bigmodel.cn', 'kimi.com', 'minimaxi.com', 'minimax.cn', 'deepseek.com', 'xiaomimimo.com'];

(async () => {
  const context = await launchLocked(chromium, path.join(ROOT, 'browser-profile'), {
    headless: true,
    args: ['--disable-blink-features=AutomationControlled'],
  }, { timeoutMs: 20000, label: 'dump_cookies' })
  const cookies = await context.cookies();
  const grouped = {};
  for (const d of DOMAINS) grouped[d] = cookies.filter(c => c.domain.includes(d));
  fs.writeFileSync(OUT, JSON.stringify({ exported_at: new Date().toISOString(), cookies: grouped }, null, 2));
  for (const d of DOMAINS) console.log(`${d}: ${grouped[d].length} cookies`);
  await context.close();
  console.log('已导出:', OUT);
})();
