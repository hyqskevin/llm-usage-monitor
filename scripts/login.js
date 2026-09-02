// 登录态保存脚本：打开三个站点，手动登录后回车保存会话
// 用法: node login.js
const { chromium } = require('playwright');
const path = require('path');
const readline = require('readline');

const PROFILE_DIR = path.join(__dirname, '..', 'browser-profile');

const SITES = [
  { key: 'bigmodel', name: '智谱 BigModel', url: 'https://bigmodel.cn/coding-plan/personal/usage' },
  { key: 'kimi',     name: 'Kimi Code',     url: 'https://www.kimi.com/membership/subscription?tab=quota&from=kfc_console_upgrade' },
  { key: 'minimax',  name: 'MiniMax',       url: 'https://platform.minimaxi.com/console/usage' },
];

(async () => {
  const context = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: false,
    viewport: { width: 1400, height: 900 },
    args: ['--disable-blink-features=AutomationControlled'],
  });

  console.log('浏览器已打开，请在各标签页中完成登录：');
  for (const site of SITES) {
    const page = await context.newPage();
    await page.goto(site.url, { waitUntil: 'domcontentloaded' }).catch(() => {});
    console.log(`  - ${site.name}: ${site.url}`);
  }

  console.log('\n登录完成后，直接关闭浏览器窗口即自动保存登录态。');
  await new Promise(resolve => context.on('close', resolve));
  console.log('登录态已保存到:', PROFILE_DIR);
})();
