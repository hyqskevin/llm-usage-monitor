// MiMo 登录态保存脚本：打开 MiMo 套餐用量页，手动登录后关闭浏览器窗口即自动保存到持久化 profile。
// 用法: node scripts/mimo_login.js
const { chromium } = require('playwright');
const { launchLocked } = require('./profile_lock');
const path = require('path');

const PROFILE_DIR = path.join(__dirname, '..', 'browser-profile');
const URL = 'https://platform.xiaomimimo.com/console/plan-manage?edition=PERSONAL';

(async () => {
  const context = await launchLocked(chromium, PROFILE_DIR, {
    headless: false,
    viewport: { width: 1400, height: 900 },
    args: ['--disable-blink-features=AutomationControlled'],
  }, { timeoutMs: 20000, label: 'mimo_login' })

  const page = await context.newPage();
  await page.goto(URL, { waitUntil: 'domcontentloaded' }).catch(() => {});
  console.log('浏览器已打开: ' + URL);
  console.log('请完成小米账号登录（扫码或账号密码均可），登录成功看到套餐用量页后，直接关闭浏览器窗口即自动保存。');
  await new Promise(resolve => context.on('close', resolve));
  console.log('登录态已保存到:', PROFILE_DIR);
})();
