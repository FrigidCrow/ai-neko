'use strict';

const { contextBridge, ipcRenderer } = require('electron');
const subscribe = (channel, callback) => {
  if (typeof callback !== 'function') throw new TypeError('Callback required');
  const listener = (_event, value) => callback(value);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
};

contextBridge.exposeInMainWorld('aiNeko', Object.freeze({
  request: (value) => ipcRenderer.invoke('ai-neko:request', value),
  status: () => ipcRenderer.invoke('ai-neko:status'),
  onStatus: (callback) => subscribe('ai-neko:status', callback),
  setInteractive: (value) => ipcRenderer.send('ai-neko:interactive', value),
  drag: (phase) => ipcRenderer.send('ai-neko:drag', phase),
  setPreferences: (value) => ipcRenderer.invoke('ai-neko:preferences', value),
  getPreferences: () => ipcRenderer.invoke('ai-neko:get-preferences'),
  showMenu: () => ipcRenderer.send('ai-neko:menu'),
  quit: () => ipcRenderer.send('ai-neko:quit'),
  openExternal: (value) => ipcRenderer.invoke('ai-neko:external', value),
  onAction: (callback) => subscribe('ai-neko:action', callback),
}));
