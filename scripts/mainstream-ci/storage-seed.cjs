// Seed real Chromium storage for the copy/audit test; test data only.
const {app, BrowserWindow} = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const [userData, mode] = process.argv.slice(2);
if (!userData || !['local','remote'].includes(mode)) process.exit(2);
app.setPath('userData', path.resolve(userData));
const timer = setTimeout(() => app.exit(2), 30000);
app.whenReady().then(async () => {
  const blank = path.join(userData, 'seed.html');
  fs.writeFileSync(blank, '<!doctype html><title>Native test storage</title>');
  const win = new BrowserWindow({show:false, webPreferences:{sandbox:true, contextIsolation:true, nodeIntegration:false}});
  await win.loadFile(blank);
  const tiles = {default:[{storedSessionId:'synthetic-native-fixture', ownerRoute:{mode, connectionId:mode==='local'?'local':'remote-proof', profile:'default'}}]};
  await win.webContents.executeJavaScript(`localStorage.setItem('hermes.desktop.sessionTiles.v2', ${JSON.stringify(JSON.stringify(tiles))}); localStorage.setItem('test.unrelated.preference','preserve-me');`);
  await win.webContents.session.flushStorageData();
  win.destroy(); clearTimeout(timer); app.quit();
}).catch(() => app.exit(2));
