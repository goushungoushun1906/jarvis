const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  captureRegion: () => ipcRenderer.invoke('capture-region'),

  // Floating input window
  submitFloatingQuery: (query) => ipcRenderer.send('floating-query', query),
  hideFloatingWindow: () => ipcRenderer.send('floating-hide'),
  onFloatingFocus: (callback) => ipcRenderer.on('floating-focus', (_event) => callback()),
  onFloatingQuery: (callback) => {
    const handler = (_event, query) => callback(query);
    ipcRenderer.on('floating-query', handler);
    return () => ipcRenderer.removeListener('floating-query', handler);
  },

  // Selection assistant
  submitSelectionAction: (payload) => ipcRenderer.send('selection-action', payload),
  hideSelectionWindow: () => ipcRenderer.send('selection-hide'),
  onSelectionText: (callback) => {
    const handler = (_event, text) => callback(text);
    ipcRenderer.on('selection-text', handler);
    return () => ipcRenderer.removeListener('selection-text', handler);
  },
  onSelectionQuery: (callback) => {
    const handler = (_event, query) => callback(query);
    ipcRenderer.on('selection-query', handler);
    return () => ipcRenderer.removeListener('selection-query', handler);
  },
});
