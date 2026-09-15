// Real packaged Electron startup + roster under a transport outage.
// No fabricated server responses, provider credentials or successful chat.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import net from 'node:net';

assert.equal(process.platform, 'darwin', 'Native macOS proof only');
const [source, binary, root, logs] = process.argv.slice(2);
assert(source && binary && root && logs);
const require = createRequire(path.join(source, 'apps/desktop/package.json'));
const {_electron} = require('playwright');
const userData = path.join(root, 'user-data');
let dialCount = 0;
const outage = net.createServer(socket => { dialCount++; socket.destroy(); });
await new Promise((resolve, reject) => { outage.once('error', reject); outage.listen(0, '127.0.0.1', resolve); });
const url = `http://127.0.0.1:${outage.address().port}`;
const remoteId = 'remote-proof';
const globalRoute = {mode: 'remote', remote: {url, authMode: 'oauth'}, profiles: {}};
const registry = {version: 2, primary: remoteId, launchMode: 'primary', lastUsed: remoteId, connections: [
  {id: 'local', kind: 'local', label: 'This device'},
  {id: remoteId, kind: 'remote', label: 'Isolated unavailable test gateway', url, authMode: 'oauth'},
]};
fs.writeFileSync(path.join(userData, 'connection.json'), JSON.stringify(globalRoute), {mode: 0o600});
fs.writeFileSync(path.join(userData, 'connections.json'), JSON.stringify(registry), {mode: 0o600});
const evidence = {endpoint: 'loopback transport-reset test fixture, not the VPS', boots: []};
let app;
try {
  for (let boot = 0; boot < 2; boot++) {
    app = await _electron.launch({executablePath: binary, env: {...process.env}, timeout: 90000});
    const page = await app.firstWindow({timeout: 90000});
    await page.waitForFunction(() => !!window.hermesDesktop?.getAgentRoster, null, {timeout: 90000});
    const paths = await app.evaluate(({app}) => ({packaged: app.isPackaged, userData: app.getPath('userData'), hermesHome: process.env.HERMES_HOME}));
    assert.equal(paths.packaged, true);
    assert.equal(paths.userData, userData);
    assert.equal(paths.hermesHome, path.join(root, 'home/.hermes'));
    const saved = await page.evaluate(() => window.hermesDesktop.connections.list());
    assert.equal(saved.primary, remoteId);
    assert.equal(saved.launchMode, 'primary');
    const rosters = [];
    for (let n = 0; n < 3; n++) {
      const roster = await page.evaluate(() => window.hermesDesktop.getAgentRoster());
      const local = roster.sources.find(x => x.connectionId === 'local');
      assert(local, 'This device must remain present');
      assert.equal(local.reachable, false);
      assert.equal(local.error, 'connect-on-demand');
      assert.equal(roster.primaryConnectionId, remoteId);
      const remote = roster.sources.find(x => x.connectionId === remoteId);
      assert(remote);
      assert.equal(remote.reachable, false);
      rosters.push(roster);
    }
    const bootstrap = await page.evaluate(() => window.hermesDesktop.getBootstrapState());
    assert.equal(bootstrap.active, false);
    await page.screenshot({path: path.join(logs, `boot-${boot}.png`)});
    fs.writeFileSync(path.join(logs, `boot-${boot}.txt`), await page.locator('body').innerText());
    evidence.boots.push({paths, rosters, bootstrap});
    await app.close(); app = null;
  }
  assert(dialCount > 0, 'Must actually attempt the saved remote, not just display settings');
  evidence.actualRemoteDialCount = dialCount;
  const after = JSON.parse(fs.readFileSync(path.join(userData, 'connection.json'), 'utf8'));
  assert.equal(after.mode, 'remote');
  assert.equal(after.remote.url, url);
  evidence.remoteRoutePersisted = true;
  fs.writeFileSync(path.join(logs, 'startup.json'), JSON.stringify(evidence, null, 2)+'\n');
} finally {
  if (app) await app.close();
  await new Promise(resolve => outage.close(resolve));
}
