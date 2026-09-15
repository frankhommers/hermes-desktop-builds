// One-shot offline reader of a PRIVATE COPY; never loads Hermes application code.
const { app, BrowserWindow, session } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const [userData, output] = process.argv.slice(2);
if (!userData || !output) process.exit(2);
app.setPath('userData', path.resolve(userData));
app.setName('Hermes storage audit');
app.commandLine.appendSwitch('disable-background-networking');
const timer = setTimeout(() => app.exit(2), 30000);
app.whenReady().then(async () => {
  session.defaultSession.webRequest.onBeforeRequest({urls: ['http://*/*', 'https://*/*', 'ws://*/*', 'wss://*/*']}, (_d, cb) => cb({cancel: true}));
  const blank = path.join(userData, 'migration-blank.html');
  fs.writeFileSync(blank, '<!doctype html><title>Offline storage inspection</title>', {mode: 0o600});
  const win = new BrowserWindow({show: false, webPreferences: {nodeIntegration: false, contextIsolation: true, sandbox: true}});
  await win.loadFile(blank);
  const keys = await win.webContents.executeJavaScript(`(() => {
    if (location.protocol !== 'file:') throw new Error('Wrong origin');
    const keys = {};
    for (const key of ['hermes.desktop.sessionTiles.v1', 'hermes.desktop.sessionTiles.v2']) keys[key] = localStorage.getItem(key);
    return {auditVersion: 1, origin: 'file://', keys};
  })()`);
  fs.writeFileSync(output, JSON.stringify(keys), {mode: 0o600, flag: 'wx'});
  clearTimeout(timer);
  app.exit(0);
}).catch(() => app.exit(2));
