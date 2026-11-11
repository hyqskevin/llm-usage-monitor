// 一次性脚本：复用已登录的 profile（localStorage 含 isAuthenticated:true），
// 抓 deepseek /usage 的所有 API 请求 + DOM 数据，用于写 fetch_deepseek() 真实解析。
// 用法: node deepseek_discover.js
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
const PROFILE_DIR = path.join(ROOT, 'browser-profile');
const OUT_JSON = path.join(ROOT, 'data', 'api-requests.json');
const OUT_DOM = path.join(ROOT, 'data', 'deepseek-dom.json');

(async () => {
  const context = await chromium.launchPersistentContext(PROFILE_DIR, {
    headless: true,
    viewport: { width: 1400, height: 900 },
    args: ['--disable-blink-features=AutomationControlled'],
  });
  const page = await context.newPage();

  // 抓所有 API 请求 + 响应 body
  const apiRequests = [];
  page.on('request', req => {
    const url = req.url();
    if (url.includes('platform.deepseek.com/api') || url.includes('fe-api')) {
      apiRequests.push({
        url, method: req.method(),
        resourceType: req.resourceType(),
        headers: req.headers(),
        postData: req.postData() || null,
        status: null, responseBody: null,
      });
    }
  });
  const respMap = new Map();
  page.on('response', async resp => {
    const url = resp.url();
    if (url.includes('platform.deepseek.com/api') || url.includes('fe-api')) {
      let body = null;
      // by_api_key/cost / amount 24h buckets 数据量大，给到 200KB；其他 8KB 够用
      const limit = url.includes('by_api_key') ? 200_000 : 8_000;
      try { body = (await resp.body()).toString().slice(0, limit); } catch (e) {}
      respMap.set(url, { status: resp.status(), body });
    }
  });

  console.log('→ 打开 deepseek /usage（用登录态）...');
  await page.goto('https://platform.deepseek.com/usage', { waitUntil: 'domcontentloaded', timeout: 60000 });
  // 等 hydrate：余额数字出现
  await page.waitForSelector('text=/¥\\s*[0-9]+\\.[0-9]{2}/', { timeout: 30000 }).catch(() => {});
  await page.waitForTimeout(8000); // 额外等一段时间让所有 API 调用完成

  // 补全 status + body
  for (const r of apiRequests) {
    const resp = respMap.get(r.url);
    if (resp) { r.status = resp.status; r.responseBody = resp.body; }
  }

  // 从 DOM 抓核心数据
  const dom = await page.evaluate(() => {
    function findCardByLabel(label) {
      const els = Array.from(document.querySelectorAll('*'));
      for (const e of els) {
        const own = Array.from(e.childNodes)
          .filter(n => n.nodeType === 3)
          .map(n => n.textContent.trim())
          .join('');
        if (own && own.includes(label) && e.children.length <= 2) {
          // 找同一容器里的金额
          const all = e.innerText || '';
          const m = all.match(/¥\s*([\d,]+\.\d{2})/);
          if (m) return m[1];
          // 或者子节点的金额
          for (const c of e.children) {
            const cm = (c.innerText || '').match(/¥\s*([\d,]+\.\d{2})/);
            if (cm) return cm[1];
          }
        }
      }
      return null;
    }

    // 抓所有 ¥金额
    const yuan = Array.from(document.querySelectorAll('*'))
      .map(e => (e.innerText || '').trim())
      .filter(t => /^¥\s*[\d,]+\.\d{2}/.test(t)
        || /^[\d,]+\.\d{2}\s*CNY$/.test(t)
        || /^CNY\s*[\d,]+\.\d{2}$/.test(t))
      .slice(0, 30);

    // 抓"Tokens" / "API 请求次数" 数字
    const tokens = Array.from(document.querySelectorAll('*'))
      .map(e => (e.innerText || '').trim())
      .filter(t => /^\d{1,3}(,\d{3})+$/.test(t))
      .slice(0, 10);

    return {
      url: location.href,
      title: document.title,
      label_yuan: {
        '充值余额': findCardByLabel('充值余额'),
        '累计消费金额': findCardByLabel('累计消费金额'),
        '消费金额': findCardByLabel('消费金额'),
      },
      all_yuan_text: yuan,
      token_counts: tokens,
    };
  });

  // 保存（保持向后兼容 + api-requests.json 保留其他站点）
  const merged = { deepseek: apiRequests };
  if (fs.existsSync(OUT_JSON)) {
    try {
      const existing = JSON.parse(fs.readFileSync(OUT_JSON, 'utf8'));
      Object.assign(merged, existing, { deepseek: apiRequests });
    } catch (e) {}
  }
  fs.writeFileSync(OUT_JSON, JSON.stringify(merged, null, 2));
  fs.writeFileSync(OUT_DOM, JSON.stringify(dom, null, 2));

  // === 解析为 fetch.py 用的格式，stdout 输出 ===
  // 找 amount/cost/summary 三个 API 的响应
  const findResp = (substr) => apiRequests.find(r => r.url.includes(substr));
  const summaryResp = findResp('get_user_summary');
  const amountResp = findResp('/by_api_key/amount');
  const costResp = findResp('/by_api_key/cost');

  const rows = [];
  const errors = [];

  let out = { rows: [], errors: [] };
  try {
    // --- billing: 充值余额 / 累计消费 / 充值总额 ---
    if (summaryResp && summaryResp.responseBody) {
      const biz = JSON.parse(summaryResp.responseBody).data.biz_data;
      const balance = parseFloat(biz.normal_wallets[0].balance) + parseFloat(biz.bonus_wallets?.[0]?.balance || 0);
      const totalCost = parseFloat(biz.total_costs[0].amount);
      const totalRecharge = balance + totalCost;

      rows.push({
        window: 'billing',
        used: totalCost,
        total: totalRecharge,
        remaining: balance,
        used_pct: null,
        reset_at: null,
        unlimited: false,
        meta: {},  // 2026-09-15 21:15 移除 monthly_budget_cny 字段（不再做预算展示）
      });
    } else {
      errors.push('get_user_summary 响应缺失');
    }

    // --- today: 当日消费金额 + 当日 tokens ---
    let todayCost = 0, todayTokens = 0;
    if (costResp && costResp.responseBody) {
      const biz = JSON.parse(costResp.responseBody).data.biz_data;
      for (const s of (biz.data?.[0]?.series || [])) {
        for (const b of (s.buckets || [])) {
          todayCost += parseFloat(b.cost || 0);
        }
      }
    } else {
      errors.push('cost API 响应缺失');
    }
    if (amountResp && amountResp.responseBody) {
      const biz = JSON.parse(amountResp.responseBody).data.biz_data;
      for (const s of (biz.series || [])) {
        for (const b of (s.buckets || [])) {
          const u = b.usage || {};
          todayTokens += (u.RESPONSE_TOKEN || 0) + (u.PROMPT_CACHE_HIT_TOKEN || 0) + (u.PROMPT_CACHE_MISS_TOKEN || 0);
        }
      }
    } else {
      errors.push('amount API 响应缺失');
    }

    rows.push({
      window: 'today',
      used: Math.round(todayCost * 100) / 100,
      total: null,
      remaining: null,
      used_pct: null,
      reset_at: null,
      unlimited: false,
      meta: {
        today_tokens: todayTokens,
      },
    });
  } catch (e) {
    errors.push('parse error: ' + e.message);
  }
  out = { rows, errors };

  console.log('\n=== ROWS ===');
  console.log(JSON.stringify(out));

  // 写独立 row 文件供 fetch.py 读取
  fs.writeFileSync(path.join(ROOT, 'data', 'deepseek-rows.json'), JSON.stringify(out, null, 2));

  console.log(`\n=== 抓包结果 ===`);
  console.log(`API 请求数: ${apiRequests.length}`);
  for (const r of apiRequests) {
    console.log(`  ${r.method} ${r.status || '?'} ${r.url.replace('https://platform.deepseek.com', '')}`);
  }
  console.log(`\n=== DOM 数据 ===`);
  console.log(JSON.stringify(dom, null, 2));

  await context.close();
  console.log(`\n已保存: ${OUT_JSON}`);
  console.log(`已保存: ${OUT_DOM}`);
})().catch(e => { console.error('ERROR:', e); process.exit(1); });