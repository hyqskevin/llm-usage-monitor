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
    'bigmodel':  {'name': '智谱 GLM',        'color': '#8fa8c8'},
    'kimi':      {'name': 'Kimi（订阅总额）', 'color': '#c8a2a2'},
    'kimi-code': {'name': 'Kimi Code',       'color': '#b5a6c9'},
    'minimax':   {'name': 'MiniMax',         'color': '#a3b8a3'},
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


def latest_of(series):
    return series[-1] if series else {}


def build_html(data):
    payload = json.dumps(data, ensure_ascii=False)
    site_meta = json.dumps(
        {k: {'name': v['name'], 'color': v['color']} for k, v in SITES.items()},
        ensure_ascii=False)
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
    --bg: #f4f2ee; --card: #fbfaf7; --text: #4a4a48; --muted: #8a8680;
    --line: #e3ded6;
  }}
  * {{ box-sizing: border-box; margin: 0; }}
  body {{ background: var(--bg); color: var(--text);
         font-family: -apple-system, "PingFang SC", sans-serif; padding: 24px; }}
  h1 {{ font-size: 22px; font-weight: 600; margin-bottom: 4px; }}
  .sub {{ color: var(--muted); font-size: 13px; margin-bottom: 20px; }}
  .site {{ background: var(--card); border: 1px solid var(--line); border-radius: 14px;
           padding: 20px; margin-bottom: 20px; }}
  .site h2 {{ font-size: 17px; margin-bottom: 14px; display: flex; align-items: center; gap: 8px; }}
  .dot {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; }}
  .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 14px; margin-bottom: 14px; }}
  .card {{ border: 1px solid var(--line); border-radius: 10px; padding: 16px; }}
  .card .w {{ font-size: 13px; color: var(--muted); margin-bottom: 10px;
              display: flex; justify-content: space-between; }}
  .card .w .pct {{ color: var(--text); font-weight: 600; }}
  .bar {{ position: relative; height: 26px; background: #ece8e1; border-radius: 13px;
          overflow: hidden; }}
  .bar i {{ display: block; height: 100%; border-radius: 13px; transition: width .4s; }}
  .bar span {{ position: absolute; inset: 0; display: flex; align-items: center;
               justify-content: center; font-size: 12px; font-weight: 600; color: #4a4a48; }}
  .card .d {{ font-size: 12px; color: var(--muted); margin-top: 8px; }}
  canvas {{ max-height: 240px; }}
  .charts {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }}
  .chart-box {{ border: 1px solid var(--line); border-radius: 10px; padding: 12px; }}
  .chart-box .t {{ font-size: 13px; color: var(--muted); margin-bottom: 8px; }}
</style>
</head>
<body>
<h1>LLM 用量仪表盘</h1>
<div class="sub">数据生成于 {generated} · 数据来源：bigmodel / kimi / minimax 官方 API</div>
<div id="root"></div>
<script>
const DATA = {payload};
const SITES = {site_meta};
const WINDOWS = {win_meta};

function fmtReset(iso) {{
  if (!iso) return '';
  const d = new Date(iso);
  return '重置: ' + d.toLocaleString('zh-CN', {{month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit'}});
}}

const root = document.getElementById('root');
for (const [site, meta] of Object.entries(SITES)) {{
  const windows = DATA[site] || {{}};
  const sec = document.createElement('div');
  sec.className = 'site';
  let cards = '', charts = '';
  for (const [w, wname] of Object.entries(WINDOWS)) {{
    const series = windows[w] || [];
    const last = series[series.length - 1] || {{}};
    if (!series.length) continue;
    const pct = last.pct;
    const pctText = pct == null ? '—' : pct.toFixed(1) + '%';
    let detail = '';
    if (last.total) detail = `已用 ${{last.used?.toFixed(0)}} / ${{last.total.toFixed(0)}} 积分 · 剩 ${{last.remaining?.toFixed(0)}}`;
    else if (w === 'billing' && last.used) detail = '近30天 ' + (last.used/1e6).toFixed(0) + 'M tokens';
    const barW = pct == null ? 0 : Math.min(100, pct);
    cards += `<div class="card"><div class="w"><span>${{wname}}</span><span class="pct">${{pctText}}</span></div>
      <div class="bar"><i style="width:${{barW}}%;background:${{meta.color}}"></i><span>${{pct == null ? '' : '已用 ' + pctText}}</span></div>
      <div class="d">${{detail}} ${{fmtReset(last.reset_at)}}</div></div>`;
    charts += `<div class="chart-box"><div class="t">${{wname}}使用趋势</div>
      <canvas id="c-${{site}}-${{w}}"></canvas></div>`;
  }}
  sec.innerHTML = `<h2><span class="dot" style="background:${{meta.color}}"></span>${{meta.name}}</h2>
    <div class="cards">${{cards}}</div><div class="charts">${{charts}}</div>`;
  root.appendChild(sec);

  for (const [w] of Object.entries(WINDOWS)) {{
    const series = (windows[w] || []);
    if (!series.length) continue;
    new Chart(document.getElementById(`c-${{site}}-${{w}}`), {{
      type: 'line',
      data: {{
        labels: series.map(p => new Date(p.ts).toLocaleString('zh-CN',
          {{month:'numeric', day:'numeric', hour:'2-digit', minute:'2-digit'}})),
        datasets: [{{
          data: series.map(p => p.pct),
          borderColor: meta.color,
          backgroundColor: meta.color + '33',
          fill: true, tension: 0.35, pointRadius: 2,
        }}],
      }},
      options: {{
        plugins: {{ legend: {{ display: false }} }},
        scales: {{
          y: {{ min: 0, max: 100, ticks: {{ callback: v => v + '%' }},
                grid: {{ color: '#ece8e1' }} }},
          x: {{ grid: {{ display: false }}, ticks: {{ maxTicksLimit: 8 }} }},
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
