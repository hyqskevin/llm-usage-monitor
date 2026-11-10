#!/usr/bin/env python3
"""记录/读取当前选中的模型（新 SwiftBar 插件 llm-usage-v2 使用）。

被 SwiftBar 菜单里的 logo 点击项调用（bash=... param0=<model_key>）。

状态文件：data/selected.json
  {"model": "kimi", "updated_at": "2026-11-10T..."}

用法:
  python3 scripts/select_model.py            # 读取当前选中（无参时打印）
  python3 scripts/select_model.py kimi       # 切换到 kimi
"""
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE = os.path.join(ROOT, 'data', 'selected.json')

# 合法模型 key（与插件内的 SITES 一致）
VALID = ('bigmodel', 'kimi', 'kimi-code', 'minimax', 'mimo', 'deepseek')

DEFAULT = 'bigmodel'


def read():
    try:
        with open(STATE) as f:
            data = json.load(f)
        model = data.get('model')
        if model in VALID:
            return model
    except Exception:
        pass
    return DEFAULT


def write(model):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    payload = {'model': model, 'updated_at': datetime.now(tz=timezone.utc).isoformat()}
    # 原子写入，避免读到半截 JSON
    tmp = STATE + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE)


def main():
    if len(sys.argv) < 2:
        print(read())
        return 0
    model = sys.argv[1].strip()
    if model not in VALID:
        print(f'未知模型: {model}（可选: {", ".join(VALID)}）', file=sys.stderr)
        return 1
    write(model)
    print(model)
    return 0


if __name__ == '__main__':
    sys.exit(main())
