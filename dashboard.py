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

SITES = {
    'bigmodel':  {'name': '智谱 GLM',        'color': '#7d96b5', 'dark': '#5d7592'},
    'kimi':      {'name': 'Kimi（订阅总额）', 'color': '#c89a9a', 'dark': '#a87373'},
    'kimi-code': {'name': 'Kimi Code',       'color': '#a594b8', 'dark': '#806f96'},
    'minimax':   {'name': 'MiniMax',         'color': '#8fb08f', 'dark': '#6a8a6a'},
}
WINDOWS = {'5h': '5小时额度', 'week': '周额度', 'billing': '账期/订阅'}


def load():
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        'SELECT ts, site, window, used_pct, used, total, remaining, reset_at '
        'FROM snapshots ORDER BY ts').fetchall()
    conn.close()
    data = {}
    for ts, site, window, pct, used, total, remaining, reset_at in rows:
        data.setdefault(site, {}).setdefault(window, []).append({
            'ts': ts, 'pct': pct, 'used': used, 'total': total,
            'remaining': remaining, 'reset_at': reset_at,
        })
    return data


def make_bar(uid, pct, color, dark):
    """生成一段 SVG 进度条：渐变 + 圆角 + 已用/剩余双段 + 刻度点。"""
    W, H = 360, 28
    p = 0 if pct is None else max(0, min(100, pct))
    p_text = '—' if pct is None else f'{pct:.1f}%'
    return f'''<svg class="bar-svg" viewBox="0 0 {W} {H}" preserveAspectRatio="none"
       data-uid="{uid}" data-pct="{p:.1f}">
  <defs>
    <linearGradient id="g-{uid}" x1="0" x2="1" y1="0" y2="0">
      <stop offset="0%" stop-color="{color}"/>
      <stop offset="100%" stop-color="{dark}"/>
    </linearGradient>
    <clipPath id="c-{uid}"><rect x="0" y="0" width="{W}" height="{H}" rx="14"/></clipPath>
  </defs>
  <rect x="0" y="0" width="{W}" height="{H}" rx="14" fill="#ece8e1"/>
  <rect class="fill" x="0" y="0" width="0" height="{H}" rx="14"
        fill="url(#g-{uid})" clip-path="url(#c-{uid})"/>
  <g clip-path="url(#c-{uid})" stroke="rgba(255,255,255,.35)" stroke-width="1">
    <line x1="{W*0.25:.1f}" y1="0" x2="{W*0.25:.1f}" y2="{H}"/>
    <line x1="{W*0.50:.1f}" y1="0" x2="{W*0.50:.1f}" y2="{H}"/>
    <line x1="{W*0.75:.1f}" y1="0" x2="{W*0.75:.1f}" y2="{H}"/>
  </g>
  <circle class="knob" cx="0" cy="{H/2:.1f}" r="6" fill="#fff" stroke="{dark}" stroke-width="2"
          style="filter: drop-shadow(0 1px 2px rgba(0,0,0,.18))"/>
  <text class="lbl" x="12" y="{H/2+4:.1f}" font-size="12" fill="#4a4a48" font-weight="600">已用</text>
  <text class="val" x="{W-12}" y="{H/2+4:.1f}" text-anchor="end" font-size="13"
        fill="#4a4a48" font-weight="700">{p_text}</text>
</svg>'''


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
  .bar-svg {{ width: 100%; height: 28px; display: block; }}
  .card .d {{ font-size: 12px; color: var(--muted); margin-top: 10px;
              display: flex; justify-content: space-between; }}
  .card .d b {{ color: var(--text); font-weight: 600; }}
  canvas {{ max-height: 220px; }}
  .charts {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }}
  .chart-box {{ border: 1px solid var(--line); border-radius: 12px; padding: 14px; background: var(--card); }}
  .chart-box .t {{ font-size: 13px; color: var(--muted); margin-bottom: 10px; font-weight: 500; }}
</style>
</head>
<body>
<h1>LLM 用量仪表盘</h1>
<div class="sub">数据生成于 {generated} · 来源 bigmodel / kimi / minimax 官方 API · 每 10 分钟自动刷新</div>
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
  for (const [w, wname] of Object.entries(WINDOWS)) {{
    const series = windows[w] || [];
    if (!series.length) continue;
    const last = series[series.length - 1] || {{}};
    const pct = last.pct;
    const uidStr = site + '-' + w + '-' + (++uid);
    cards += `<div class="card">
      <div class="w"><span>${{wname}}</span><span class="pct">${{pct == null ? '—' : pct.toFixed(1) + '%'}}</span></div>
      <svg class="bar-svg" viewBox="0 0 360 28" preserveAspectRatio="none"
           data-uid="${{site}}-${{w}}" data-pct="${{pct == null ? 0 : pct}}" data-c="${{meta.color}}" data-d="${{meta.dark}}">
        <defs>
          <linearGradient id="gr-${{site}}-${{w}}" x1="0" x2="1" y1="0" y2="0">
            <stop offset="0%" stop-color="${{meta.color}}"/>
            <stop offset="100%" stop-color="${{meta.dark}}"/>
          </linearGradient>
          <clipPath id="cl-${{site}}-${{w}}"><rect x="0" y="0" width="360" height="28" rx="14"/></clipPath>
        </defs>
        <rect x="0" y="0" width="360" height="28" rx="14" fill="#ece8e1"/>
        <rect class="fill" x="0" y="0" width="0" height="28" rx="14"
              fill="url(#gr-${{site}}-${{w}})" clip-path="url(#cl-${{site}}-${{w}})"/>
        <g clip-path="url(#cl-${{site}}-${{w}})" stroke="rgba(255,255,255,.35)" stroke-width="1">
          <line x1="90" y1="0" x2="90" y2="28"/>
          <line x1="180" y1="0" x2="180" y2="28"/>
          <line x1="270" y1="0" x2="270" y2="28"/>
        </g>
        <circle class="knob" cx="0" cy="14" r="6" fill="#fff" stroke="${{meta.dark}}" stroke-width="2"/>
        <text class="lbl" x="12" y="18" font-size="12" fill="#4a4a48" font-weight="600">已用</text>
        <text class="val" x="348" y="18" text-anchor="end" font-size="13"
              fill="#4a4a48" font-weight="700">${{pct == null ? '—' : pct.toFixed(1) + '%'}}</text>
      </svg>
      <div class="d"><span>${{last.total ? '<b>' + Math.round(last.used).toLocaleString() + '</b> / ' + Math.round(last.total).toLocaleString() + ' 积分' : (w === 'billing' && last.used ? '近30天 <b>' + fmtBig(last.used) + '</b> tokens' : '&nbsp;')}}</span><span>重置 ${{fmtReset(last.reset_at)}}</span></div>
    </div>`;
    charts += `<div class="chart-box"><div class="t">${{wname}}使用趋势</div>
      <canvas id="c-${{site}}-${{w}}"></canvas></div>`;
  }}
  sec.innerHTML = `<h2><span class="dot" style="background:${{meta.color}}"></span>${{meta.name}}</h2>
    <div class="cards">${{cards}}</div><div class="charts">${{charts}}</div>`;
  root.appendChild(sec);

  // SVG 进度条动画
  sec.querySelectorAll('.bar-svg').forEach(svg => {{
    const pct = parseFloat(svg.dataset.pct) || 0;
    const W = 360;
    const fill = svg.querySelector('.fill');
    const knob = svg.querySelector('.knob');
    requestAnimationFrame(() => {{
      fill.setAttribute('width', (W * pct / 100));
      fill.style.transition = 'width 1.1s cubic-bezier(.22,.61,.36,1)';
      knob.setAttribute('cx', W * pct / 100);
      knob.style.transition = 'cx 1.1s cubic-bezier(.22,.61,.36,1)';
    }});
  }});

  for (const [w] of Object.entries(WINDOWS)) {{
    const series = (windows[w] || []);
    if (!series.length) continue;
    new Chart(document.getElementById(`c-${{site}}-${{w}}`), {{
      type: 'line',
      data: {{
        labels: series.map(p => new Date(p.ts).toLocaleString('zh-CN',
          {{month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit'}})),
        datasets: [{{
          data: series.map(p => p.pct ?? 0),
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
          callbacks: {{ label: c => c.parsed.y.toFixed(1) + '%' }} }} }},
        scales: {{
          y: {{ min: 0, max: 100, ticks: {{ callback: v => v + '%', color: '#8a8680' }},
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


def main():
    data = load()
    html = build_html(data)
    with open(OUT, 'w') as f:
        f.write(html)
    n = sum(len(v) for s in data.values() for v in s.values())
    print(f'已生成 {OUT}（{n} 条历史序列）')


if __name__ == '__main__':
    main()
