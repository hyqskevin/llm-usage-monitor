// 端到端验证 dashboard.html：provider 标题可点击进管理台 + 无 console error
const path = require('path');
const { chromium } = require('/Users/hanamaki_mac_mini/.workbuddy/binaries/node/workspace/node_modules/playwright');

const FILE = 'file://' + path.join(__dirname, '..', 'data', 'dashboard.html');
const OUT = path.join(__dirname, '..', 'data', 'dashboard-verify.png');

(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });

  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));

  await page.goto(FILE, { waitUntil: 'load', timeout: 120000 });
  await page.waitForTimeout(3000);

  // 1) provider 标题链接
  const titles = await page.$$eval('.site h2', els => els.map(el => {
    const a = el.querySelector('a');
    return {
      text: el.textContent.trim(),
      isLink: !!a,
      href: a ? a.getAttribute('href') : null,
      target: a ? a.getAttribute('target') : null,
    };
  }));

  // 2) 点击首个 provider 链接，验证 target=_blank 且 href 正确（不真跳，拦截）
  let clickResult = 'no link';
  const first = await page.$('.site h2 a');
  if (first) {
    const [popup] = await Promise.all([
      page.waitForEvent('popup', { timeout: 8000 }).catch(() => null),
      first.click(),
    ]);
    if (popup) {
      clickResult = 'popup url: ' + popup.url();
      await popup.close();
    } else {
      clickResult = 'clicked, no popup (target may be _blank blocked)';
    }
  }

  await page.screenshot({ path: OUT, fullPage: false });

  console.log('=== provider 标题链接 ===');
  titles.forEach(t => console.log(JSON.stringify(t)));
  console.log('\n=== 点击验证 ===');
  console.log(clickResult);
  console.log('\n=== console errors ===');
  console.log(errors.length ? errors.join('\n') : '(none)');

  await browser.close();
})();
