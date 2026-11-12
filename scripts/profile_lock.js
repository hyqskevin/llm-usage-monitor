// browser-profile 互斥锁（跨进程）。
//
// 问题（2026-11-12 实锤）：
//   所有 Playwright 脚本共用同一个 browser-profile 目录。Chromium 用
//   SingletonLock 保证「一个 user-data-dir 同时只能有一个实例」。当
//   后台采集（fetch.py → discover_api.js / deepseek_discover.js 等）与
//   人工登录（node scripts/login.js）同时启动时，后启动的实例会被
//   Chrome 判定为「重复启动」：
//     [out] 正在现有的浏览器会话中打开。
//   然后进程立刻退出（exitCode=0），Playwright 抛
//   「Target page, context or browser has been closed」。
//
// 既有防护的缺口：
//   fetch.py 自己的 flock（data/.fetch.lock）只能防止**采集任务之间**重叠，
//   挡不住人工在终端里直接跑 node scripts/login.js。
//
// 方案：
//   用「mkdir 原子性」做互斥锁（POSIX mkdir 在同一时刻只可能有一个成功）。
//   所有要起 Chromium 的脚本都先 acquire，拿到后再 launchPersistentContext。
//   锁目录里写 pid + 时间戳，进程异常退出留下陈旧锁时能自动识别并清理。
//
// 用法：
//   const { withProfileLock } = require('./profile_lock');
//   await withProfileLock(async () => { ...launch context... });

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const LOCK_DIR = path.join(__dirname, '..', 'data', '.profile.lock');
const PID_FILE = path.join(LOCK_DIR, 'pid');
// 人工登录标记：login.js 会持有 profile 最长 15 分钟等人登录，
// 这期间后台采集必须让路（fetch.py 读到它就跳过所有 Playwright 步骤）。
//
// 关键：标记文件**不能**放在 LOCK_DIR 里。
// LOCK_DIR 会在 context 关闭时整个被删掉，标记会跟着消失 ——
// 而 login.js 恰恰要在「浏览器已关、子脚本还要跑」这段窗口里保持标记。
// 所以单独放一个文件。
const MANUAL_FLAG = path.join(__dirname, '..', 'data', '.profile.manual');

// 锁被视为陈旧的阈值：进程还在但超过这个时间没释放，也当作无效
const STALE_MS = 15 * 60 * 1000;

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function pidAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (e) {
    return e.code === 'EPERM';   // 存在但不属于当前用户
  }
}

function readLockInfo() {
  try {
    const raw = fs.readFileSync(PID_FILE, 'utf8').trim();
    const [pidStr, tsStr] = raw.split(' ');
    return { pid: parseInt(pidStr, 10), ts: parseInt(tsStr, 10) };
  } catch (e) {
    return null;
  }
}

function cleanStale() {
  const info = readLockInfo();
  if (!info) {
    // 锁目录存在但没有有效 pid 文件 → 陈旧
    try { fs.rmSync(LOCK_DIR, { recursive: true, force: true }); } catch (e) {}
    return;
  }
  const age = Date.now() - (info.ts || 0);
  if (!pidAlive(info.pid) || age > STALE_MS) {
    try { fs.rmSync(LOCK_DIR, { recursive: true, force: true }); } catch (e) {}
  }
}

/**
 * 获取 profile 锁。
 * @param {number} timeoutMs 等待上限；超时抛错
 * @param {string} label 日志前缀
 */
async function acquireProfileLock(timeoutMs = 120000, label = 'profile') {
  const deadline = Date.now() + timeoutMs;
  let waited = false;
  while (true) {
    try {
      fs.mkdirSync(LOCK_DIR);                 // 原子：只可能有一个成功
      fs.writeFileSync(PID_FILE, `${process.pid} ${Date.now()}`);
      registerExitRelease();
      return;
    } catch (e) {
      if (e.code !== 'EEXIST') throw e;
      if (!waited) {
        waited = true;
        const info = readLockInfo();
        const who = info ? `pid ${info.pid}` : '未知进程';
        console.log(`[${label}] browser-profile 正被 ${who} 占用，等待中…（最多 ${Math.round(timeoutMs / 1000)}s）`);
      }
      cleanStale();
      if (Date.now() > deadline) {
        throw new Error(
          `等待 browser-profile 锁超时（${Math.round(timeoutMs / 1000)}s）。` +
          `说明另一个 Chromium 长时间占着 profile。` +
          `可用 lsof +D browser-profile 查占用者，或关掉那个浏览器后重试。`
        );
      }
      await sleep(1000);
    }
  }
}

// 进程正常退出 / 未捕获异常退出时释放锁。
// 注意：process.exit() 会触发 'exit' 事件，所以这里必须注册，
// 否则锁目录会残留（陈旧锁要靠 pid 检测兜底清理）。
let _exitHooked = false;
function registerExitRelease() {
  if (_exitHooked) return;
  _exitHooked = true;
  process.once('exit', releaseProfileLock);
}

function releaseProfileLock() {
  try { fs.rmSync(LOCK_DIR, { recursive: true, force: true }); } catch (e) {}
}

/**
 * 标记「人工登录进行中」。
 * @param {boolean} on
 */
function setManualLogin(on) {
  try {
    if (on) {
      // 标记文件独立于 LOCK_DIR —— 锁目录会在 context 关闭时被整体删除，
      // 而 login.js 恰恰要在「浏览器已关、子脚本还要跑」这段窗口里保持标记。
      fs.writeFileSync(MANUAL_FLAG, `${process.pid} ${Date.now()}`);
    } else {
      try { fs.rmSync(MANUAL_FLAG, { force: true }); } catch (e) {}
    }
  } catch (e) {}
}

/** 是否有「人工登录进行中」标记（供 fetch.py 读取）。 */
function isManualLogin() {
  try {
    return fs.existsSync(MANUAL_FLAG);
  } catch (e) {
    return false;
  }
}

/**
 * 包装：拿锁 → 执行 → 必定释放（含异常路径）。
 * @param {Function} fn
 * @param {{timeoutMs?:number, label?:string}} opts
 */
async function withProfileLock(fn, opts = {}) {
  const { timeoutMs = 120000, label = 'profile' } = opts;
  await acquireProfileLock(timeoutMs, label);
  let released = false;
  const release = () => {
    if (released) return;
    released = true;
    releaseProfileLock();
  };
  registerExitRelease();
  try {
    return await fn();
  } finally {
    release();
  }
}

/**
 * 带锁启动 persistent context —— 所有脚本的标准入口。
 * 锁在以下任一条件满足时释放：
 *   - context.close() 被显式调用
 *   - context 触发 'close' 事件（用户手动关浏览器窗口）—— login.js 走的是这条路
 *   - 进程退出
 *
 * @param {object} chromium playwright.chromium
 * @param {string} profileDir user-data-dir 路径
 * @param {object} launchOpts launchPersistentContext 的选项
 * @param {{timeoutMs?:number, label?:string}} opts
 */
async function launchLocked(chromium, profileDir, launchOpts = {}, opts = {}) {
  const { timeoutMs = 20000, label = 'profile' } = opts;
  await acquireProfileLock(timeoutMs, label);
  let context;
  try {
    context = await chromium.launchPersistentContext(profileDir, launchOpts);
  } catch (e) {
    releaseProfileLock();
    throw e;
  }
  const origClose = context.close.bind(context);
  let closed = false;
  const release = () => {
    if (closed) return;
    closed = true;
    releaseProfileLock();
  };
  context.close = async (...args) => {
    try {
      return await origClose(...args);
    } finally {
      release();
    }
  };
  // 用户手动关浏览器窗口时 context 也会触发 'close'。
  // login.js 就是靠这个事件等「用户关窗」→ 必须在这里释放锁，
  // 否则它后续调用的子脚本（mimo_refresh_session.js 等）会拿不到锁 → 自死锁。
  try {
    context.on('close', release);
  } catch (e) {}
  return context;
}

module.exports = {
  withProfileLock, acquireProfileLock, releaseProfileLock, launchLocked,
  setManualLogin, isManualLogin, LOCK_DIR, MANUAL_FLAG,
};
