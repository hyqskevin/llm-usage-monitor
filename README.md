# llm-usage-monitor

macOS 状态栏多模型额度看板。SwiftBar 插件形态，采集 LLM 平台订阅用量并在菜单栏展示剩余额度。

支持站点：智谱 GLM、Kimi、Kimi Code、MiniMax、DeepSeek、MiMo。

## 两版插件

两版可并存安装，菜单栏各自一个图标。

| | v1 `llm-usage.10m.sh` | v2 `llm-usage-v2.10m.sh` |
|---|---|---|
| 形态 | 普通插件，SwiftBar 定时拉起 | streamable 常驻进程 |
| 状态栏 | 全部站点的剩余额度数值 + 分段进度条 | 当前 provider 的 logo + 进度条 |
| 下拉菜单 | 一次展示所有站点的额度 | 只展示当前选中的 provider |
| 切换 provider | 无 | 点菜单顶部横排 logo 直接切换，菜单不收起 |
| 采集频率 | 跟随 v2（v1 只读 `data/latest.json` 快照） | 每 60 秒空闲刷新 |

v2 是交互版，v1 保留全站点总览。

## 目录结构

```
swiftbar/
  llm-usage.10m.sh      v1 插件：全站点总览
  llm-usage-v2.10m.sh   v2 插件：logo 切换 + 单 provider 视图
scripts/
  llm_bar_v2.py         v2 插件主体（渲染 + 点击探测 + 动效）
  fetch.py              采集器：直连各站 API，写 SQLite 与 latest.json
  login.js              浏览器登录各站点
  login.sh              登录脚本入口
  mimo_*.js             MiMo 会话签发与刷新
  minimax_*.js          MiniMax 凭证刷新
  deepseek_discover.js  DeepSeek 接口发现
  sync_chrome_auth.sh   从本机 Chrome 同步登录态
  open_dashboard.sh     打开本地仪表盘
dashboard.py            生成 data/dashboard.html
assets/logos/           各 provider logo
tests/                  单元测试
```

## 安装

### 1. 依赖

- SwiftBar 2.1.1+（v2 需要 streamable 支持）
- Python 3.13
- Node.js + Playwright（仅登录和凭证刷新时需要）

### 2. 放置插件

把 `swiftbar/` 目录加入 SwiftBar 的插件目录，或在 SwiftBar 设置里直接指定：

```
~/Documents/github/project/llm-usage-monitor/swiftbar
```

### 3. 登录各站点

菜单栏点插件 → 「重新登录（浏览器）」，会依次打开各站点登录页。

已有 Chrome 登录态的站点可以用「从 Chrome 同步登录态」，跳过手动登录。

### 4. 手动修正路径

脚本里的 Node / Python 路径是本机绝对路径，换机器时需要改这几处：

```bash
grep -rn "hanamaki_mac_mini" swiftbar/ scripts/ fetch.py dashboard.py
```

## 数据采集

`fetch.py` 直连各平台 API，不依赖浏览器会话：

| 站点 | 鉴权方式 |
|---|---|
| 智谱 GLM | `data/api-requests.json` 的 authorization header |
| Kimi / Kimi Code | `data/api-requests.json` 的 authorization header |
| MiniMax | `data/minimax-auth.json` 的 `_token` cookie |
| MiMo | `data/mimo-session.json` 的 `api-platform_serviceToken` + `ph` cookie |
| DeepSeek | `data/api-requests.json` 的 authorization header |

凭证文件由登录脚本生成，都在 `.gitignore` 里，不入库。

鉴权失效时 `fetch.py` 会自动跑对应的刷新脚本重新签发，无需人工重登。

## 测试

```bash
python3 -m pytest tests/ -q
```

| 测试文件 | 覆盖 |
|---|---|
| `test_fetch_auth.py` | 鉴权错误分类、各站 token fingerprint |
| `test_site_colors.py` | Kimi（粉）/ Kimi Code（紫）配色与分段条像素 |
| `test_strip_hit.py` | v2 logo 条带几何与点击命中映射 |

## 仪表盘

菜单栏 → 「打开仪表盘」，生成 `data/dashboard.html`，本地浏览器打开即可看各站点用量卡片。

## 说明

- `data/` 下的凭证、数据库、快照、运行日志都在 `.gitignore` 中
- 脚本内的 Python / Node 绝对路径针对 macOS 本机环境，其他平台需自行替换
- v2 的 logo 点击定位依赖 AppKit 弹出菜单的实测几何常量，跨 macOS 版本可能需要重新标定
