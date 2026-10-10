const { app, BrowserWindow, ipcMain, screen, Tray, Menu, globalShortcut, clipboard } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const { net } = require('electron');

// --- Auto-start registry helper (Windows) ---
const os = require('os');
let Registry;
try {
  Registry = require('winreg');
} catch {
  Registry = null;
}

// Set userData to project-local directory to avoid sandbox restrictions
const userDataPath = app.isPackaged
  ? path.join(process.resourcesPath, 'data')
  : path.join(__dirname, '..', '.electron-data');
app.setPath('userData', userDataPath);

// Disable GPU cache to avoid permission issues in sandbox
app.commandLine.appendSwitch('disable-gpu-shader-disk-cache');
app.commandLine.appendSwitch('disable-gpu-driver-bug-workarounds');

let mainWindow;
let floatingWindow = null;
let selectionWindow = null;
let tray = null;
let backendProcess;
let backendRestartCount = 0;
const MAX_BACKEND_RESTARTS = 3;
const BACKEND_PORT = 18200;
const FLOATING_SHORTCUT = 'Ctrl+Shift+Space';
const SELECTION_SHORTCUT = 'Ctrl+Shift+C';

// Clipboard polling for text selection assistant
let lastClipboardText = '';
let clipboardPollingInterval = null;
let ignoreNextClipboardChange = false;

// Auto-start setting key
const AUTO_START_KEY = 'jarvis_auto_start';

function getBackendPath() {
  // In development: use python directly
  // In production: use the packaged backend.exe
  if (app.isPackaged && process.resourcesPath !== path.join(__dirname, '..')) {
    const exePath = path.join(process.resourcesPath, 'backend', 'backend.exe');
    if (require('fs').existsSync(exePath)) {
      return exePath;
    }
  }
  return null; // signal to use python dev mode
}

function isBackendPortInUse() {
  return new Promise((resolve) => {
    const tester = require('net').createServer();
    tester.once('error', (err) => {
      if (err.code === 'EADDRINUSE') {
        resolve(true);
      } else {
        resolve(false);
      }
    });
    tester.once('listening', () => {
      tester.close(() => resolve(false));
    });
    tester.listen(BACKEND_PORT, '127.0.0.1');
  });
}

/**
 * 期望的后端构建标识，取自随包发布的 backend/BUILD_ID.txt。
 *
 * 只需维护 server/BUILD_ID.txt 一处：打包时它会被放到 exe 同级，Electron
 * 直接读同一个文件，不会和后端代码写歪。读不到时返回 null，此时走保守
 * 分支（不去动已经占用端口的进程）。
 */
function getExpectedBackendBuild() {
  try {
    const p = path.join(process.resourcesPath, 'backend', 'BUILD_ID.txt');
    return require('fs').readFileSync(p, 'utf8').trim() || null;
  } catch (_) {
    return null;
  }
}

/**
 * 探测已经在端口上跑着的后端。
 *
 * 必须把"可达但没有 build 标识"和"压根连不上"分开：前者正是旧版本后端
 * （build 字段是本次才加的），恰好是最该被换掉的情况；后者通常是后端还在
 * 启动中，这时贸然 kill 会误伤，只能保守复用。
 */
function probeRunningBackend() {
  return new Promise((resolve) => {
    const req = net.request(`http://127.0.0.1:${BACKEND_PORT}/health`);
    let settled = false;
    const done = (v) => { if (!settled) { settled = true; resolve(v); } };
    req.on('response', (res) => {
      let body = '';
      res.on('data', (c) => { body += c.toString(); });
      res.on('end', () => {
        let build = null;
        try { build = JSON.parse(body).build || null; } catch (_) { /* 非 JSON */ }
        done({ reachable: true, build });
      });
    });
    req.on('error', () => done({ reachable: false, build: null }));
    req.end();
    setTimeout(() => done({ reachable: false, build: null }), 3000);
  });
}

/** 结束占用 18200 的进程。该端口是 JARVIS 专用，不属于它的进程不会监听它。 */
function killProcessOnBackendPort() {
  try {
    const out = require('child_process')
      .execSync(`netstat -ano | findstr :${BACKEND_PORT} | findstr LISTENING`, { encoding: 'utf8' });
    const pids = [...new Set(
      out.split('\n').map((l) => l.trim().split(/\s+/).pop()).filter((p) => /^\d+$/.test(p))
    )];
    for (const pid of pids) {
      console.log(`Killing stale backend on port ${BACKEND_PORT} (pid=${pid})`);
      require('child_process').execSync(`taskkill /PID ${pid} /F`, { stdio: 'ignore' });
    }
    return pids.length > 0;
  } catch (_) {
    return false;
  }
}

function startBackend() {
  return new Promise(async (resolve, reject) => {
    const backendAlreadyRunning = await isBackendPortInUse();
    if (backendAlreadyRunning) {
      // 端口被占用不能直接跳过：那很可能是一个旧版本的残留后端（上一版没
      // 退干净、或之前 dev 模式启动的 uvicorn）。以前这里只是打一行日志就
      // 复用它，结果是用户明明换了新包、界面却照旧 —— 而且完全看不出来。
      const expected = getExpectedBackendBuild();
      const running = await probeRunningBackend();

      // 可达但标识对不上（包括压根没有标识的旧版本）→ 换掉它。
      // 连不上 → 多半还在启动中，别误杀，保守复用。
      const mismatched = running.reachable && running.build !== expected;

      if (expected && mismatched) {
        console.log(
          `Stale backend detected (running=${running.build || 'none'}, expected=${expected}); replacing it.`
        );
        if (killProcessOnBackendPort()) {
          // 给内核一点时间真正释放端口，否则自己启动时又撞上 EADDRINUSE
          await new Promise((r) => setTimeout(r, 1500));
        } else {
          console.warn('Could not kill stale backend; will reuse it.');
          resolve();
          return;
        }
      } else {
        console.log(
          `Backend port ${BACKEND_PORT} already in use` +
          `${running.reachable ? ` by build ${running.build || 'none'}` : ' (not answering /health)'}; reusing it.`
        );
        resolve();
        return;
      }
    }

    const backendExe = getBackendPath();
    const serverDir = app.isPackaged
      ? path.join(process.resourcesPath, 'backend')
      : path.join(__dirname, '..', 'server');
    let cmd, args;

    if (backendExe) {
      cmd = backendExe;
      args = [];
    } else {
      // Dev mode: use python full path (prefer project venv)
      const venvPython = path.join(serverDir, '.venv', 'Scripts', 'python.exe');
      const pythonPaths = [
        venvPython,
        'C:\\Users\\HONOR\\AppData\\Local\\Programs\\Python\\Python313\\python.exe',
        'C:\\Python313\\python.exe',
        'C:\\Python39\\python.exe',
      ];
      cmd = pythonPaths.find(p => require('fs').existsSync(p)) || 'python';
      args = ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT)];
    }

    backendProcess = spawn(cmd, args, {
      cwd: serverDir,
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env },
    });

    backendProcess.stdout.on('data', (data) => {
      console.log(`[Backend] ${data.toString().trim()}`);
    });

    backendProcess.stderr.on('data', (data) => {
      console.error(`[Backend] ${data.toString().trim()}`);
    });

    backendProcess.on('error', (err) => {
      console.error('Failed to start backend:', err);
      reject(err);
    });

    backendProcess.on('exit', (code, signal) => {
      console.log(`Backend process exited (code=${code}, signal=${signal})`);
      // Restart unless we are shutting down the app
      if (!app.isQuiting) {
        restartBackendIfNeeded();
      }
    });

    // Poll until backend is ready
    const checkInterval = setInterval(() => {
      const req = net.request(`http://127.0.0.1:${BACKEND_PORT}/health`);
      req.on('response', (res) => {
        clearInterval(checkInterval);
        resolve();
      });
      req.on('error', () => {
        // Not ready yet
      });
      req.end();
    }, 500);

    // Timeout after 30 seconds
    setTimeout(() => {
      clearInterval(checkInterval);
      resolve(); // Continue anyway
    }, 30000);
  });
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    title: 'JARVIS',
    icon: path.join(__dirname, '..', 'public', 'favicon.ico'),
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
    },
    autoHideMenuBar: true,
  });

  // In production, load from dist folder
  // In development, load from Vite dev server or dist
  if (app.isPackaged) {
    mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
  } else {
    // Try Vite dev server first, fall back to dist
    const devUrl = 'http://localhost:5173';
    mainWindow.loadURL(devUrl).catch(() => {
      mainWindow.loadFile(path.join(__dirname, '..', 'dist', 'index.html'));
    });
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });

  // Minimize to tray instead of taskbar
  mainWindow.on('minimize', (event) => {
    event.preventDefault();
    mainWindow.hide();
  });

  // Close button minimizes to tray unless forced via tray menu
  mainWindow.on('close', (event) => {
    if (!app.isQuiting) {
      event.preventDefault();
      mainWindow.hide();
    }
  });
}

// ====== Floating input window ======
function createFloatingWindow() {
  if (floatingWindow && !floatingWindow.isDestroyed()) {
    return floatingWindow;
  }

  const primaryDisplay = screen.getPrimaryDisplay();
  const { width: screenWidth } = primaryDisplay.workAreaSize;
  const winWidth = 640;
  const winHeight = 80;

  floatingWindow = new BrowserWindow({
    width: winWidth,
    height: winHeight,
    x: Math.round((screenWidth - winWidth) / 2),
    y: 80,
    show: false,
    frame: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    resizable: false,
    minimizable: false,
    maximizable: false,
    transparent: true,
    hasShadow: true,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  floatingWindow.loadFile(path.join(__dirname, 'floating-window.html'));

  floatingWindow.on('blur', () => {
    hideFloatingWindow();
  });

  floatingWindow.on('closed', () => {
    floatingWindow = null;
  });

  return floatingWindow;
}

function showFloatingWindow() {
  const win = createFloatingWindow();
  if (!win) return;

  const primaryDisplay = screen.getPrimaryDisplay();
  const { width: screenWidth } = primaryDisplay.workAreaSize;
  const bounds = win.getBounds();
  const cursor = screen.getCursorScreenPoint();
  const currentDisplay = screen.getDisplayNearestPoint(cursor);
  const workArea = currentDisplay.workArea;

  // Center on the active display, near the top
  const x = Math.round(workArea.x + (workArea.width - bounds.width) / 2);
  const y = Math.round(workArea.y + 80);

  win.setBounds({ x, y, width: bounds.width, height: bounds.height });

  win.show();
  win.focus();
  win.webContents.send('floating-focus');
}

function hideFloatingWindow() {
  if (floatingWindow && !floatingWindow.isDestroyed()) {
    floatingWindow.hide();
  }
}

function toggleFloatingWindow() {
  if (floatingWindow && floatingWindow.isVisible() && !floatingWindow.isDestroyed()) {
    hideFloatingWindow();
  } else {
    showFloatingWindow();
  }
}

// ====== Selection assistant window ======
function createSelectionWindow() {
  if (selectionWindow && !selectionWindow.isDestroyed()) {
    return selectionWindow;
  }

  selectionWindow = new BrowserWindow({
    width: 320,
    height: 180,
    show: false,
    frame: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    resizable: false,
    minimizable: false,
    maximizable: false,
    transparent: true,
    hasShadow: true,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  });

  selectionWindow.loadFile(path.join(__dirname, 'selection-window.html'));

  selectionWindow.on('blur', () => {
    hideSelectionWindow();
  });

  selectionWindow.on('closed', () => {
    selectionWindow = null;
  });

  return selectionWindow;
}

function showSelectionWindow() {
  const text = clipboard.readText().trim();
  if (!text) return;

  const win = createSelectionWindow();
  if (!win) return;

  const cursor = screen.getCursorScreenPoint();
  const currentDisplay = screen.getDisplayNearestPoint(cursor);
  const workArea = currentDisplay.workArea;
  const bounds = win.getBounds();

  let x = Math.round(cursor.x + 16);
  let y = Math.round(cursor.y + 16);

  // Keep inside work area
  if (x + bounds.width > workArea.x + workArea.width) {
    x = Math.max(workArea.x, cursor.x - bounds.width - 8);
  }
  if (y + bounds.height > workArea.y + workArea.height) {
    y = Math.max(workArea.y, cursor.y - bounds.height - 8);
  }

  win.setBounds({ x, y, width: bounds.width, height: bounds.height });
  win.webContents.send('selection-text', text);
  win.show();
  win.focus();
}

function hideSelectionWindow() {
  if (selectionWindow && !selectionWindow.isDestroyed()) {
    selectionWindow.hide();
  }
}

function startClipboardPolling() {
  if (clipboardPollingInterval) return;
  clipboardPollingInterval = setInterval(() => {
    const text = clipboard.readText();
    if (text && text !== lastClipboardText && !ignoreNextClipboardChange) {
      lastClipboardText = text;
    }
    ignoreNextClipboardChange = false;
  }, 500);
}

function stopClipboardPolling() {
  if (clipboardPollingInterval) {
    clearInterval(clipboardPollingInterval);
    clipboardPollingInterval = null;
  }
}

// ====== Tray helpers ======
function getTrayIcon() {
  const iconName = process.platform === 'win32' ? 'favicon.ico' : 'favicon.ico';
  return path.join(__dirname, '..', 'public', iconName);
}

function createTray() {
  tray = new Tray(getTrayIcon());
  tray.setToolTip('JARVIS');
  updateTrayMenu();

  tray.on('click', () => {
    if (mainWindow) {
      if (mainWindow.isVisible()) {
        mainWindow.hide();
      } else {
        mainWindow.show();
        mainWindow.focus();
      }
    } else {
      createWindow();
    }
  });
}

function updateTrayMenu() {
  if (!tray) return;
  const autoStart = isAutoStartEnabled();
  const template = [
    {
      label: '显示 JARVIS',
      click: () => {
        if (mainWindow) {
          mainWindow.show();
          mainWindow.focus();
        } else {
          createWindow();
        }
      },
    },
    {
      label: '隐藏 JARVIS',
      click: () => {
        if (mainWindow) mainWindow.hide();
      },
    },
    { type: 'separator' },
    {
      label: `开机自启: ${autoStart ? '已开启' : '已关闭'}`,
      click: () => {
        setAutoStartEnabled(!autoStart);
        updateTrayMenu();
      },
    },
    { type: 'separator' },
    {
      label: '退出',
      click: () => {
        app.isQuiting = true;
        app.quit();
      },
    },
  ];
  const contextMenu = Menu.buildFromTemplate(template);
  tray.setContextMenu(contextMenu);
}

// ====== Auto-start helpers ======
function getAutoStartRegKey() {
  if (!Registry) return null;
  return new Registry({
    hive: Registry.HKCU,
    key: '\\Software\\Microsoft\\Windows\\CurrentVersion\\Run',
  });
}

function isAutoStartEnabled() {
  try {
    const regKey = getAutoStartRegKey();
    if (!regKey) return false;
    let enabled = false;
    regKey.values((err, items) => {
      if (!err && items) {
        enabled = items.some((item) => item.name === 'JARVIS');
      }
    });
    // Fallback to stored preference if registry read is async
    return enabled;
  } catch {
    return false;
  }
}

function setAutoStartEnabled(enabled) {
  const regKey = getAutoStartRegKey();
  if (!regKey) return;

  if (enabled) {
    const exePath = process.execPath;
    regKey.set('JARVIS', Registry.REG_SZ, `"${exePath}" --hidden`, (err) => {
      if (err) console.error('Failed to enable auto-start:', err);
      else console.log('Auto-start enabled');
    });
  } else {
    regKey.remove('JARVIS', (err) => {
      if (err && err.code !== 2) console.error('Failed to disable auto-start:', err);
      else console.log('Auto-start disabled');
    });
  }
}

// ====== Backend lifecycle with restart limit ======
function restartBackendIfNeeded() {
  if (backendRestartCount >= MAX_BACKEND_RESTARTS) {
    console.error(`Backend crashed ${backendRestartCount} times; stopping automatic restart.`);
    return;
  }
  backendRestartCount += 1;
  console.log(`Backend process exited; restart attempt ${backendRestartCount}/${MAX_BACKEND_RESTARTS}`);
  setTimeout(() => {
    startBackend().catch((err) => console.error('Backend restart failed:', err));
  }, 1000);
}

// ====== Region screenshot selector window ======
let regionSelectorWindow = null;
let regionSelectorResolve = null;

function createRegionSelectorWindow() {
  return new Promise((resolve) => {
    regionSelectorResolve = resolve;

    const cursorPoint = screen.getCursorScreenPoint();
    const display = screen.getDisplayNearestPoint(cursorPoint);
    const { x, y, width, height } = display.bounds;

    regionSelectorWindow = new BrowserWindow({
      x,
      y,
      width,
      height,
      fullscreen: false,
      frame: false,
      transparent: true,
      alwaysOnTop: true,
      skipTaskbar: true,
      resizable: false,
      movable: false,
      minimizable: false,
      maximizable: false,
      closable: true,
      focusable: true,
      hasShadow: false,
      webPreferences: {
        nodeIntegration: true,
        contextIsolation: false,
      },
    });

    regionSelectorWindow.setIgnoreMouseEvents(false);
    regionSelectorWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });

    regionSelectorWindow.loadFile(path.join(__dirname, 'region-selector.html'));

    regionSelectorWindow.on('closed', () => {
      regionSelectorWindow = null;
      if (regionSelectorResolve) {
        regionSelectorResolve(null);
        regionSelectorResolve = null;
      }
    });
  });
}

ipcMain.handle('capture-region', async () => {
  if (regionSelectorWindow) {
    regionSelectorWindow.focus();
    return null;
  }
  return createRegionSelectorWindow();
});

ipcMain.on('region-selected', (_event, region) => {
  if (regionSelectorResolve) {
    regionSelectorResolve(region);
    regionSelectorResolve = null;
  }
  if (regionSelectorWindow) {
    regionSelectorWindow.close();
    regionSelectorWindow = null;
  }
});

// ====== Floating window IPC ======
ipcMain.on('floating-query', (_event, query) => {
  if (!query || !query.trim()) return;

  hideFloatingWindow();

  // Show and focus main window
  if (!mainWindow || mainWindow.isDestroyed()) {
    createWindow();
  } else {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  }

  // Forward the query to the renderer after a short delay to ensure window is ready
  setTimeout(() => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('floating-query', query.trim());
    }
  }, 300);
});

ipcMain.on('floating-hide', () => {
  hideFloatingWindow();
});

// ====== Selection assistant IPC ======
ipcMain.on('selection-action', (_event, { text, action }) => {
  if (!text || !action) return;

  hideSelectionWindow();

  // Build prompt based on action
  const prompts = {
    explain: `请解释以下内容：\n\n${text}`,
    translate: `请将以下内容翻译成中文：\n\n${text}`,
    summarize: `请总结以下内容的要点：\n\n${text}`,
    rewrite: `请改写以下内容，保持原意但让表达更流畅自然：\n\n${text}`,
  };
  const prompt = prompts[action] || `${action}: ${text}`;

  // Show and focus main window
  if (!mainWindow || mainWindow.isDestroyed()) {
    createWindow();
  } else {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
  }

  setTimeout(() => {
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.webContents.send('selection-query', prompt);
    }
  }, 300);
});

ipcMain.on('selection-hide', () => {
  hideSelectionWindow();
});

app.whenReady().then(async () => {
  try {
    await startBackend();
  } catch (err) {
    console.error('Backend startup failed:', err);
  }

  createTray();

  // Register global shortcut for floating input window
  const floatingRegistered = globalShortcut.register(FLOATING_SHORTCUT, () => {
    toggleFloatingWindow();
  });
  if (!floatingRegistered) {
    console.warn(`Failed to register global shortcut: ${FLOATING_SHORTCUT}`);
  }

  // Register global shortcut for selection assistant
  const selectionRegistered = globalShortcut.register(SELECTION_SHORTCUT, () => {
    showSelectionWindow();
  });
  if (!selectionRegistered) {
    console.warn(`Failed to register global shortcut: ${SELECTION_SHORTCUT}`);
  }

  // Start clipboard polling to capture copied text
  startClipboardPolling();

  const hiddenStart = process.argv.includes('--hidden');
  if (!hiddenStart) {
    createWindow();
  }

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createWindow();
    } else if (mainWindow) {
      mainWindow.show();
      mainWindow.focus();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    // On Windows we keep the app running in tray by default
    // User must use tray -> 退出 to fully quit
  }
});

app.on('before-quit', () => {
  app.isQuiting = true;
  if (backendProcess) {
    backendProcess.kill();
  }
  if (tray) {
    tray.destroy();
    tray = null;
  }
  if (floatingWindow && !floatingWindow.isDestroyed()) {
    floatingWindow.destroy();
    floatingWindow = null;
  }
  if (selectionWindow && !selectionWindow.isDestroyed()) {
    selectionWindow.destroy();
    selectionWindow = null;
  }
  stopClipboardPolling();
  globalShortcut.unregisterAll();
});
