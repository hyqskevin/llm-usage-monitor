// MiMo 会话刷新脚本：用持久化 profile 里的 passToken（.account.xiaomi.com，长效）走一次
// /sts 握手，签发 platform.xiaomimimo.com 的会话 cookie（api-platform_serviceToken 等，
// session 级、不落盘），导出到 data/mimo-session.json 供 fetch.py 纯 HTTP 重放。
// 用法: node scripts/mimo_refresh_session.js
const { chromium } = require('playwright');
const { launchLocked } = require('./profile_lock');
const path = require('path');
const fs = require('fs');

const ROOT = path.join(__dirname, '..');
const OUT = path.join(ROOT, 'data', 'mimo-session.json');
const URL = 'https://platform.xiaomimimo.com/console/plan-manage?edition=PERSONAL';

(async () => {
  const context = await launchLocked(chromium, path.join(ROOT, 'browser-profile'), {
    headless: true,
    args: ['--disable-blink-features=AutomationControlled'],
  }, { timeoutMs: 20000, label: 'mimo_refresh_session' })
  const page = await context.newPage();
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 60000 });

  // 轮询等会话 cookie 签发（/sts 握手完成），最多 30s
  let cookies = [];
  for (let i = 0; i < 30; i++) {
    cookies = (await context.cookies()).filter(c => c.domain.endsWith('xiaomimimo.com'));
    if (cookies.some(c => c.name === 'api-platform_serviceToken')) break;
    await page.waitForTimeout(1000);
  }
  const hasToken = cookies.some(c => c.name === 'api-platform_serviceToken');
  const finalUrl = page.url();

  if (!hasToken) {
    console.error('⚠️ 未拿到 api-platform_serviceToken（登录态可能失效）');
    console.error('   最终 URL: ' + finalUrl);
    await context.close();
    process.exit(2);
  }

  fs.writeFileSync(OUT, JSON.stringify({
    exported_at: new Date().toISOString(),
    cookies: { 'xiaomimimo.com': cookies },
  }, null, 2));
  await context.close();
  console.log(`已刷新 MiMo 会话: ${cookies.length} 条 cookie → data/mimo-session.json`);
})().catch(e => { console.error('ERROR:', e); process.exit(1); });
