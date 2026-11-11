#!/bin/bash
# <bitbar.title>LLM 额度</bitbar.title>
# <bitbar.version>v2.0</bitbar.version>
# <bitbar.author>kevin_w</bitbar.author>
# <bitbar.desc>多模型额度看板：状态栏 logo 切换 provider，下拉菜单只展示当前模型的额度</bitbar.desc>
# <bitbar.dependencies>python3</bitbar.dependencies>
# <swiftbar.type>streamable</swiftbar.type>
#
# SwiftBar 插件 v2（2026-11-10 新增，与 llm-usage.10m.sh 并存）
#
# 关键：streamable 标记必须用 SwiftBar 的 <swiftbar.type> 元数据包裹，
#   写成 `#streamable` 或 `# streamable`（BitBar 风格）SwiftBar 2.1.1 都不识别，
#   会把它当普通插件执行——普通插件 SwiftBar 等进程退出才渲染，
#   而本脚本是 while True 常驻，永不退出 → SwiftBar 永远卡在 Loading /
#   最终报 ⚠️（用户看到的「菜单栏展示的是！」就是它）。
#   必须用 <swiftbar.type>streamable</swiftbar.type> 才会被当作常驻流式插件。
#
# 交互结构：
#   状态栏        → 当前 provider 的 logo + 剩余额度（点开即展开下拉）
#   下拉菜单顶部  → 各 provider 的 logo 横排（一条图片带），直接点击 logo 切换
#   下拉菜单主体  → 只展示当前选中 provider 的额度进度条
#   下拉菜单底部  → 刷新 / 仪表盘 / 重新登录 / Chrome 同步（同 v1）
#
# 条带点击（2026-11-11 改造，取代原 fold 下拉列表）：
#   NSMenu 一行只能放一个菜单项，无法在图内按 x 坐标分区点击。
#   做法：条带渲染成**无 action 的纯图片项**（点击不触发 SwiftBar 动作、
#   菜单不 dismiss），由常驻进程自己识别点击：
#     CGEventSource 检测真实 leftMouseUp（免辅助功能权限，合成事件不计入）
#     → 枚举 layer=101 的 SwiftBar 菜单窗口，判定鼠标在菜单内
#     → 纯几何定位条带：条带是菜单第一个图项，其位置相对菜单窗口
#       左缘/顶缘的偏移是**常量**（实测 dx=14, dyTop=8，AppKit 弹出真实
#       NSMenu 量得，见 scripts/llm_bar_v2.py 的 MENU_IMG_INSET/MENU_BODY_TOP）
#     → 鼠标 x 相对条带左缘的偏移命中哪个 logo 格 → 进程内直接切换 + 播动效
#   菜单打开期间 SwiftBar 走 incrementalUpdateMenu 原地刷新，
#   因此切换后菜单保持打开、进度条即时更新、动效连续。
#
#   注：早期实现用「全屏截图 + 搜琥珀描边」定位条带，本机不可用——
#     macOS 15 已废弃 CGWindowListCreateImage；screencapture 在 TCC 下
#     只能拍到壁纸（拍不到 layer=101 弹出菜单）；AX 未授权。
#     故改为上面基于实测常量的纯几何定位。
#
# 动效（2026-11-10 追加）：
#   切换 provider 时，状态栏 logo 做「缩放弹入 + 进度条填充」动画。
#   实现方式：streamable 插件 + ~~~ 分帧。每帧输出一张不同缩放比例的
#   logo PNG，SwiftBar 收到 ~~~ 就重绘菜单栏，从而形成动画。
#   动效只在「检测到 provider 刚被切换」时播放一次，随后进入常驻轮询。

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="/Users/hanamaki_mac_mini/.workbuddy/binaries/python/versions/3.13.12/bin/python3"

exec "$PY" "$ROOT/scripts/llm_bar_v2.py"
