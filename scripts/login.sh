#!/bin/bash
# 打开有头浏览器，供人工逐站重新登录；关闭浏览器后登录态自动落盘到 browser-profile/。
#
# 由 SwiftBar 插件菜单「重新登录（浏览器）」调用（bash=... terminal=true）。
#
# 2026-11-10 修：原先插件里写的是
#     bash=/bin/zsh param0=-c param1="cd ... && node scripts/login.js"
# SwiftBar 的 bash= 语义是「把 bash= 的值当作**待执行的脚本**」，
# param0/param1 只是附加参数，并不会变成「用 zsh -c 执行 param1」。
# 结果 SwiftBar 去执行 /bin/zsh 这个二进制文件 → cannot execute binary file，
# 浏览器压根没被拉起，点「重新登录」毫无反应（2026-09-24 排障记录里的
# 「登录窗口必须手动打开、SwiftBar 菜单打不开」即此问题）。
#
# 正确写法：bash= 指向一个真实的 shell 脚本，由脚本自己负责 cd 与调用 node。
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NODE="/opt/homebrew/bin/node"
NODE_MOD="/Users/hanamaki_mac_mini/.workbuddy/binaries/node/workspace/node_modules"

cd "$ROOT"
export NODE_PATH="$NODE_MOD"

# login.js 内部已处理「关窗即保存登录态」+ 落盘 api-requests.json + 同步 cookies
exec "$NODE" scripts/login.js
