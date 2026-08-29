const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  captureRegion: () => ipcRenderer.invoke('capture-region'),
});
