'use strict';
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('aiNekoConsent', Object.freeze({
  terms: () => ipcRenderer.invoke('ai-neko:consent-terms'),
  accept: () => ipcRenderer.invoke('ai-neko:consent-accept'),
  decline: () => ipcRenderer.send('ai-neko:consent-decline'),
}));
