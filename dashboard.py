#!/usr/bin/env python3
"""读取 usage.db，生成 dashboard.html（Chart.js，莫兰迪低饱和配色）。

用法: python3 dashboard.py   → 生成 data/dashboard.html
"""
import json
import os
import sqlite3
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(ROOT, 'data', 'usage.db')
OUT = os.path.join(ROOT, 'data', 'dashboard.html')

# 各站点信息：console = 官方管理台地址（点击 provider 标题跳转）
SITES = {
    'bigmodel':  {'name': '智谱 GLM',        'color': '#7d96b5', 'dark': '#5d7592',
                  'console': 'https://bigmodel.cn/coding-plan/personal/usage'},
    'kimi':      {'name': 'Kimi（订阅总额）', 'color': '#c89a9a', 'dark': '#a87373',
                  'console': 'https://www.kimi.com/membership/subscription?tab=quota'},
    'kimi-code': {'name': 'Kimi Code',       'color': '#a594b8', 'dark': '#806f96',
                  'console': 'https://www.kimi.com/membership/subscription?tab=quota'},
    'minimax':   {'name': 'MiniMax',         'color': '#8fb08f', 'dark': '#6a8a6a',
                  'console': 'https://platform.minimax.cn/console/usage'},
    'mimo':      {'name': 'MiMo',            'color': '#c9a88a', 'dark': '#a88668',
                  'console': 'https://platform.xiaomimimo.com/console/plan-manage?edition=PERSONAL'},
    'deepseek':  {'name': 'DeepSeek',        'color': '#6b9aa8', 'dark': '#4d7a87',
                  'mode': 'amount-cards',
                  'console': 'https://platform.deepseek.com/usage'},
}
WINDOWS = {'5h': '5小时额度', 'week': '周额度', 'billing': '账期/订阅', 'today': '当日'}


def load():
    conn = sqlite3.connect(DB)
    # 兼容旧表：dashboard 单独跑（不经 fetch.py.init_db）也要保证 meta 列存在
    cols = {r[1] for r in conn.execute('PRAGMA table_info(snapshots)').fetchall()}
    if 'meta' not in cols:
        conn.execute('ALTER TABLE snapshots ADD COLUMN meta TEXT')
        conn.commit()
    rows = conn.execute(
        'SELECT ts, site, window, used_pct, used, total, remaining, reset_at, unlimited, meta '
        'FROM snapshots ORDER BY ts').fetchall()
    conn.close()
    data = {}
    for ts, site, window, pct, used, total, remaining, reset_at, unlimited, meta in rows:
        data.setdefault(site, {}).setdefault(window, []).append({
            'ts': ts, 'pct': pct, 'used': used, 'total': total,
            'remaining': remaining, 'reset_at': reset_at,
            'unlimited': bool(unlimited),
            'meta': json.loads(meta) if meta else None,
        })
    return data


def build_html(data):
    payload = json.dumps(data, ensure_ascii=False)
    site_meta = json.dumps(SITES, ensure_ascii=False)
    win_meta = json.dumps(WINDOWS, ensure_ascii=False)
    generated = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>LLM 用量仪表盘</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  :root {{
    --bg: #f4f2ee; --card: #fbfaf7; --text: #3f3e3b; --muted: #8a8680;
    --line: #e3ded6; --line-soft: #ece8e1;
  }}
  * {{ box-sizing: border-box; margin: 0; }}
  body {{ background: var(--bg); color: var(--text);
         font-family: -apple-system, "PingFang SC", sans-serif; padding: 28px; }}
  h1 {{ font-size: 22px; font-weight: 600; margin-bottom: 4px; letter-spacing: .5px; }}
  .sub {{ color: var(--muted); font-size: 13px; margin-bottom: 22px; }}
  .site {{ background: var(--card); border: 1px solid var(--line); border-radius: 16px;
           padding: 22px 24px; margin-bottom: 22px;
           box-shadow: 0 1px 0 rgba(0,0,0,.02), 0 8px 24px -16px rgba(70,60,50,.12); }}
  .site h2 {{ font-size: 17px; margin-bottom: 18px; display: flex; align-items: center; gap: 10px; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 16px; margin-bottom: 18px; }}
  .card {{ border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px;
           background: linear-gradient(180deg, #fdfcf9 0%, #f8f5ef 100%); }}
  .card .w {{ font-size: 13px; color: var(--muted); margin-bottom: 12px;
              display: flex; justify-content: space-between; align-items: baseline; }}
  .card .w .pct {{ color: var(--text); font-weight: 700; font-size: 16px; }}
  .bar {{ position: relative; height: 28px; border-radius: 14px; background: #ece8e1; }}
  .bar-fill {{ position: absolute; top: 0; bottom: 0; left: 0; width: 0; border-radius: 14px;
               background: linear-gradient(90deg, var(--c), var(--d));
               transition: width 1.1s cubic-bezier(.22,.61,.36,1); }}
  .bar-ticks {{ position: absolute; inset: 0; pointer-events: none; }}
  .bar-ticks i {{ position: absolute; top: 0; bottom: 0; width: 1px;
                  background: rgba(255,255,255,.35); }}
  .bar-knob {{ position: absolute; top: 50%; left: 0; width: 12px; height: 12px;
               transform: translate(-50%, -50%); background: #fff; border: 2px solid var(--d);
               border-radius: 50%; box-shadow: 0 1px 2px rgba(0,0,0,.18);
               transition: left 1.1s cubic-bezier(.22,.61,.36,1), opacity .3s; z-index: 2; }}
  .bar-lbl {{ position: absolute; left: 12px; top: 50%; transform: translateY(-50%);
              font-size: 12px; font-weight: 600; color: #4a4a48; z-index: 1; }}
  .bar-val {{ position: absolute; right: 12px; top: 50%; transform: translateY(-50%);
              font-size: 13px; font-weight: 700; color: #4a4a48; }}
  .card .d {{ font-size: 12px; color: var(--muted); margin-top: 10px;
              display: flex; justify-content: space-between; }}
  .card .d b {{ color: var(--text); font-weight: 600; }}
  canvas {{ max-height: 220px; }}
  .charts {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }}
  .chart-box {{ border: 1px solid var(--line); border-radius: 12px; padding: 14px; background: var(--card); }}
  .chart-box .t {{ font-size: 13px; color: var(--muted); margin-bottom: 10px; font-weight: 500; }}
  /* provider 标题点击进管理台 */
  .site h2 a {{ color: inherit; text-decoration: none; display: inline-flex; align-items: center; gap: 6px; }}
  .site h2 a:hover {{ color: var(--muted); }}
  .site h2 a .ext {{ font-size: 11px; opacity: .55; transition: opacity .2s; }}
  .site h2 a:hover .ext {{ opacity: 1; }}
</style>
</head>
<body>
<h1>LLM 用量仪表盘</h1>
<div class="sub">数据生成于 {generated} · 来源 bigmodel / kimi / minimax / deepseek / mimo 官方 API · 每 10 分钟自动刷新</div>
<div id="root"></div>
<script>
const DATA = {payload};
const SITES = {site_meta};
const WINDOWS = {win_meta};

function fmtReset(iso) {{
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleString('zh-CN', {{month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit'}});
}}
function fmtBig(n) {{
  if (n >= 1e9) return (n/1e9).toFixed(1) + 'B';
  if (n >= 1e6) return (n/1e6).toFixed(1) + 'M';
  if (n >= 1e3) return (n/1e3).toFixed(1) + 'K';
  return Math.round(n).toString();
}}

const root = document.getElementById('root');
let uid = 0;

for (const [site, meta] of Object.entries(SITES)) {{
  const windows = DATA[site] || {{}};
  const sec = document.createElement('div');
  sec.className = 'site';
  let cards = '', charts = '';
  if (meta.mode === 'amount-cards') {{
    // DeepSeek: 账户余额 / 当日消费 / 当日 Token 三张数字卡片（无进度条）
    const billing = (windows['billing'] || []).slice(-1)[0] || {{}};
    const today = (windows['today'] || []).slice(-1)[0] || {{}};
    const todayMeta = today.meta || {{}};
    const fmtYuan = n => n != null ? '¥' + Number(n).toFixed(2) : '—';
    cards = `
      <div class="card">
        <div class="w"><span>账户余额</span><span class="pct">${{fmtYuan(billing.remaining)}}</span></div>
        <div class="d"><span>充值总额 <b>${{fmtYuan(billing.total)}}</b></span><span>累计已消费 <b>${{fmtYuan(billing.used)}}</b></span></div>
      </div>
      <div class="card">
        <div class="w"><span>当日消费</span><span class="pct">${{fmtYuan(today.used)}}</span></div>
        <div class="d"><span>&nbsp;</span><span>重置 ${{fmtReset(today.reset_at || (function(){{
          const d=new Date(); d.setHours(24,0,0,0); return d.toISOString();
        }})())}}</span></div>
      </div>
      <div class="card">
        <div class="w"><span>当日 Token</span><span class="pct">${{todayMeta.today_tokens != null ? fmtBig(todayMeta.today_tokens) : '—'}}</span></div>
        <div class="d"><span>输入 + 输出</span><span>&nbsp;</span></div>
      </div>
    `;
    if ((windows['billing'] || []).length) {{
      charts += `<div class="chart-box"><div class="t">账户余额趋势</div>
        <canvas id="c-${{site}}-billing"></canvas></div>`;
    }}
    if ((windows['today'] || []).length) {{
      charts += `<div class="chart-box"><div class="t">当日消费趋势</div>
        <canvas id="c-${{site}}-today"></canvas></div>`;
    }}
  }} else {{
    for (const [w, wname] of Object.entries(WINDOWS)) {{
      const series = windows[w] || [];
      if (!series.length) continue;
      const last = series[series.length - 1] || {{}};
      const pct = last.pct;
      const uidStr = site + '-' + w + '-' + (++uid);
      cards += `<div class="card">
        <div class="w"><span>${{wname}}</span><span class="pct">${{last.unlimited ? '∞ 无限制' : (pct == null ? '—' : pct.toFixed(1) + '%')}}</span></div>
        <div class="bar" data-pct="${{last.unlimited ? 100 : (pct == null ? 0 : pct)}}"
             style="--c:${{meta.color}}; --d:${{meta.dark}};">
          <div class="bar-fill"></div>
          <div class="bar-ticks"><i style="left:25%"></i><i style="left:50%"></i><i style="left:75%"></i></div>
          <div class="bar-knob"></div>
          <span class="bar-lbl">已用</span>
          <span class="bar-val">${{last.unlimited ? '∞' : (pct == null ? '—' : pct.toFixed(1) + '%')}}</span>
        </div>
        <div class="d"><span>${{last.total ? '<b>' + Math.round(last.used).toLocaleString() + '</b> / ' + Math.round(last.total).toLocaleString() + ' 积分' : (w === 'billing' && last.used ? '近30天 <b>' + fmtBig(last.used) + '</b> tokens' : (w === 'today' && last.used ? '当日 <b>' + fmtBig(last.used) + '</b> tokens' : '&nbsp;'))}}</span><span>重置 ${{fmtReset(last.reset_at)}}</span></div>
      </div>`;
      charts += `<div class="chart-box"><div class="t">${{wname}}使用趋势</div>
        <canvas id="c-${{site}}-${{w}}"></canvas></div>`;
    }}
  }}
  const consoleUrl = meta.console || '';
  const titleHtml = consoleUrl
    ? `<a href="${{consoleUrl}}" target="_blank" rel="noopener" title="打开 ${{meta.name}} 管理台">${{meta.name}}<span class="ext">↗</span></a>`
    : meta.name;
  sec.innerHTML = `<h2><span class="dot" style="background:${{meta.color}}"></span>${{titleHtml}}</h2>
    <div class="cards">${{cards}}</div><div class="charts">${{charts}}</div>`;
  root.appendChild(sec);

  // 进度条动画（HTML/CSS 实现，宽度百分比自适应，文字不变形）
  sec.querySelectorAll('.bar').forEach(bar => {{
    const pct = Math.max(0, Math.min(100, parseFloat(bar.dataset.pct) || 0));
    const fill = bar.querySelector('.bar-fill');
    const knob = bar.querySelector('.bar-knob');
    requestAnimationFrame(() => {{
      requestAnimationFrame(() => {{
        fill.style.width = pct + '%';
        knob.style.left = pct + '%';
        knob.style.opacity = pct > 0.5 ? '1' : '0';
      }});
    }});
  }});

  for (const [w] of Object.entries(WINDOWS)) {{
    const series = (windows[w] || []);
    if (!series.length) continue;
    const canvas = document.getElementById(`c-${{site}}-${{w}}`);
    if (!canvas) continue;
    const isAmount = meta.mode === 'amount-cards';
    // amount 模式：billing 画余额走势，today 画当日消费走势；y 轴金额自适应。
    // 无百分比但有 token 用量的窗口（如 mimo/today）改画 token 走势。
    const hasPct = series.some(p => p.pct != null);
    const isToken = !isAmount && !hasPct;
    const dataField = isAmount ? (w === 'billing' ? 'remaining' : 'used') : (hasPct ? 'pct' : 'used');
    const dataVals = series.map(p => p[dataField] ?? 0);
    const yMin = 0;
    const yMax = (isAmount || isToken) ? Math.max(...dataVals) * 1.1 || 1 : 100;
    const tickCallback = isAmount
      ? v => '¥' + Number(v).toFixed(2)
      : (isToken ? v => fmtBig(v) : v => v + '%');
    const tooltipLabel = isAmount
      ? c => '¥' + Number(c.parsed.y).toFixed(2)
      : (isToken ? c => fmtBig(c.parsed.y) + ' tokens' : c => c.parsed.y.toFixed(1) + '%');
    new Chart(canvas, {{
      type: 'line',
      data: {{
        labels: series.map(p => new Date(p.ts).toLocaleString('zh-CN',
          {{month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit'}})),
        datasets: [{{
          data: dataVals,
          borderColor: meta.color,
          backgroundColor: meta.color + '22',
          fill: true, tension: 0.4, pointRadius: 0,
          pointHoverRadius: 5, pointHoverBackgroundColor: meta.dark,
          borderWidth: 2,
        }}],
      }},
      options: {{
        animation: {{ duration: 800 }},
        plugins: {{ legend: {{ display: false }}, tooltip: {{
          callbacks: {{ label: tooltipLabel }} }} }},
        scales: {{
          y: {{ min: yMin, max: yMax, ticks: {{ callback: tickCallback, color: '#8a8680' }},
                grid: {{ color: '#ece8e1' }} }},
          x: {{ grid: {{ display: false }}, ticks: {{ maxTicksLimit: 8, color: '#8a8680' }} }},
        }},
      }},
    }});
  }}
}}
</script>
</body>
</html>'''


def generate():
    """生成 dashboard.html，返回写入的快照条数。"""
    data = load()
    html = build_html(data)
    with open(OUT, 'w') as f:
        f.write(html)
    return sum(len(v) for s in data.values() for v in s.values())


def main():
    n = generate()
    print(f'已生成 {OUT}（{n} 条历史序列）')


if __name__ == '__main__':
    main()
