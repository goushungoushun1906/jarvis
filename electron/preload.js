const { contextBridge, ipcRenderer } = require('electron');

// preload 是渲染进程通往主进程的唯一一道门，所以这里把渲染侧传来的内容一律
// 当作不可信输入：即使前端某处出现 XSS，也不该让它通过 IPC 把任意类型、任意
// 长度的数据塞进主进程（非字符串会触发 TypeError，超长字符串会白白占内存）。
const MAX_QUERY_LENGTH = 4000;
const MAX_ACTION_LENGTH = 32;

function safeString(value, max = MAX_QUERY_LENGTH) {
  return typeof value === 'string' ? value.slice(0, max) : '';
}

/**
 * 注册监听并返回取消函数。
 *
 * 之前 onFloatingFocus 没有返回取消函数，组件每次挂载都会再叠一个监听器，
 * 卸载时又删不掉 —— 反复开关浮窗后会累积出一堆重复回调。
 */
function subscribe(channel, callback) {
  const handler = (_event, ...args) => callback(...args);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
}

contextBridge.exposeInMainWorld('electronAPI', {
  captureRegion: () => ipcRenderer.invoke('capture-region'),

  // Floating input window
  submitFloatingQuery: (query) =>
    ipcRenderer.send('floating-query', safeString(query)),
  hideFloatingWindow: () => ipcRenderer.send('floating-hide'),
  onFloatingFocus: (callback) => subscribe('floating-focus', callback),
  onFloatingQuery: (callback) => subscribe('floating-query', callback),

  // Selection assistant
  submitSelectionAction: (payload) =>
    ipcRenderer.send('selection-action', {
      text: safeString(payload && payload.text),
      action: safeString(payload && payload.action, MAX_ACTION_LENGTH),
    }),
  hideSelectionWindow: () => ipcRenderer.send('selection-hide'),
  onSelectionText: (callback) => subscribe('selection-text', callback),
  onSelectionQuery: (callback) => subscribe('selection-query', callback),

  // 语音唤醒兜底快捷键。
  // 存在的理由：关键词引擎可能因缺少密钥、模型文件缺失或麦克风被占用而
  // 起不来，此时用户必须还能用键盘开门，否则等于整个语音入口死掉。
  onWakeToggle: (callback) => subscribe('wake-toggle', callback),
  onWakePttStart: (callback) => subscribe('wake-ptt-start', callback),
  onWakePttStop: (callback) => subscribe('wake-ptt-stop', callback),
});
