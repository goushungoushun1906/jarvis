const { app, BrowserWindow, ipcMain, screen, Tray, Menu } = require('electron');
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
let tray = null;
let backendProcess;
let backendRestartCount = 0;
const MAX_BACKEND_RESTARTS = 3;
const BACKEND_PORT = 18200;

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

function startBackend() {
  return new Promise((resolve, reject) => {
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

app.whenReady().then(async () => {
  try {
    await startBackend();
  } catch (err) {
    console.error('Backend startup failed:', err);
  }

  createTray();

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
});
