#!/bin/bash
# 切换当前展示的模型（新 SwiftBar 插件 llm-usage-v2 的 logo 点击项调用）。
#
# SwiftBar 调用形式：bash=/path/to/switch_model.sh param0=kimi
# SwiftBar 的 bash= 语义是「执行这个脚本」，param0 作为 $1 传入。
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="/Users/hanamaki_mac_mini/.workbuddy/binaries/python/versions/3.13.12/bin/python3"

MODEL="${1:-}"
if [ -z "$MODEL" ]; then
    echo "缺少模型参数" >&2
    exit 1
fi

cd "$ROOT"
"$PY" scripts/select_model.py "$MODEL"
