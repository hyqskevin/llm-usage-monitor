#!/bin/bash
# <bitbar.title>LLM 额度</bitbar.title>
# <bitbar.version>v2.0</bitbar.version>
# <bitbar.author>kevin_w</bitbar.author>
# <bitbar.desc>多模型额度看板：状态栏 logo 切换 provider，下拉菜单只展示当前模型的额度</bitbar.desc>
# <bitbar.dependencies>python3</bitbar.dependencies>
#streamable
#
# SwiftBar 插件 v2（2026-11-10 新增，与 llm-usage.10m.sh 并存）
#
# 交互结构：
#   状态栏        → 当前 provider 的 logo + 剩余额度（点开即展开下拉）
#   下拉菜单顶部  → 各 provider 的 logo 横排，每个 logo 可点击切换
#   下拉菜单主体  → 只展示当前选中 provider 的额度进度条
#   下拉菜单底部  → 刷新 / 仪表盘 / 重新登录 / Chrome 同步（同 v1）
#
# 动效（2026-11-10 追加）：
#   切换 provider 时，状态栏 logo 做「缩放弹入 + 进度条填充」动画。
#   实现方式：streamable 插件 + ~~~ 分帧。每帧输出一张不同缩放比例的
#   logo PNG，SwiftBar 收到 ~~~ 就重绘菜单栏，从而形成动画。
#   动效只在「检测到 provider 刚被切换」时播放一次，随后进入常驻轮询。

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="/Users/hanamaki_mac_mini/.workbuddy/binaries/python/versions/3.13.12/bin/python3"

exec "$PY" "$ROOT/scripts/llm_bar_v2.py"
