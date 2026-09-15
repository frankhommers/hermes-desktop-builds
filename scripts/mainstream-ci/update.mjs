// Genuine official in-app update, swap and automatic LaunchServices relaunch.
// Disposable runner canonical home only; no provider credentials or VPS calls.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

assert.equal(process.platform, 'darwin');
const [source, binary, logs] = process.argv.slice(2);
const home = process.env.HOME;
const state = path.join(home, '.hermes');
const userData = path.join(home, 'Library/Application Support/Hermes');
assert.equal(source, path.join(state, 'hermes-agent'));
assert.equal(process.env.HERMES_DESKTOP_USER_DATA_DIR, userData);
const require = createRequire(path.join(source, 'apps/desktop/package.json'));
const {_electron} = require('playwright');
const git = (...args) => execFileSync('git', args, {cwd:source, encoding:'utf8'}).trim();
const before = git('rev-parse', 'HEAD');
const configBefore = JSON.parse(fs.readFileSync(path.join(userData, 'connection.json'), 'utf8'));
const resultPath = path.join(state, '.hermes-update-result.json');
assert(!fs.existsSync(resultPath), 'No stale update result');
let result = null;
const collectResult = () => {
  try { result = JSON.parse(fs.readFileSync(resultPath, 'utf8')); }
  catch (error) { if (error.code !== 'ENOENT' && !(error instanceof SyntaxError)) throw error; }
  if (result) fs.writeFileSync(path.join(logs,'observed-official-update-result.json'), JSON.stringify(result,null,2));
};
const resultWatcher = fs.watch(state, (_event, filename) => {
  if (filename === '.hermes-update-result.json') collectResult();
});
const sleep = ms => new Promise(resolve => setTimeout(resolve,ms));
async function until(fn, milliseconds, label) {
  const end = Date.now()+milliseconds;
  while (Date.now()<end) { const value=fn(); if(value) return value; await sleep(250); }
  throw new Error(`Timed out: ${label}`);
}
function runningAppPid(excluding) {
  const text=execFileSync('/bin/ps',['-ww','-axo','pid=,comm='],{encoding:'utf8'});
  for (const line of text.split('\n')) {
    const match=line.match(/^\s*(\d+)\s+(.+?)\s*$/);
    if(match && match[2]===binary && Number(match[1])!==excluding) return Number(match[1]);
  }
  return null;
}
const vanillaEnv={...process.env};
vanillaEnv.PATH='/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin';
delete vanillaEnv.HERMES_HOME;
delete vanillaEnv.HERMES_DESKTOP_USER_DATA_DIR;
let app;
let relaunchedPid;
try {
  // Prove normal canonical discovery, not a test-only remote environment URL.
  app=await _electron.launch({executablePath:binary, env:vanillaEnv, timeout:90000});
  let page=await app.firstWindow({timeout:90000});
  await page.waitForFunction(()=>!!window.hermesDesktop?.updates, null, {timeout:90000});
  let check;
  const checks=[];
  for(let attempt=0; attempt<4; attempt++) {
    check=await page.evaluate(()=>window.hermesDesktop.updates.check({force:true}));
    checks.push(check);
    fs.writeFileSync(path.join(logs,'official-update-check-attempts.json'),JSON.stringify(checks,null,2));
    const sharedRunnerRateLimit=check.error==='fetch-failed' && /HTTP (403|429)/.test(check.message||'');
    if(!sharedRunnerRateLimit || attempt===3) break;
    // Real upstream API only; no synthesized success or updater monkeypatch.
    await sleep(60000 * 2**attempt);
  }
  fs.writeFileSync(path.join(logs,'official-update-check.json'),JSON.stringify(check,null,2));
  assert.equal(check.supported,true);
  assert.equal(check.hermesRoot,source);
  assert.equal(check.currentSha,before);
  assert.equal(check.updateAvailable,true, 'Must have a genuine newer official revision');
  const oldPid=app.process().pid;
  const startedAt=Math.floor(Date.now()/1000);
  const closed=app.waitForEvent('close',{timeout:90000});
  const apply=await page.evaluate(()=>window.hermesDesktop.updates.apply()).catch(error=>({bridgeError:String(error)}));
  fs.writeFileSync(path.join(logs,'official-update-apply.json'),JSON.stringify(apply,null,2));
  await closed; app=null;
  await until(()=>{collectResult(); return result;}, 2100000, 'official updater result');
  assert.equal(result.ok,true);
  assert.equal(result.exit_code,0);
  assert.equal(result.manual,false);
  assert(result.finished_at>=startedAt);
  relaunchedPid=await until(()=>runningAppPid(oldPid),90000,'automatic LaunchServices relaunch');
  const after=git('rev-parse','HEAD');
  assert.notEqual(after,before);
  git('merge-base','--is-ancestor',before,after);
  git('merge-base','--is-ancestor',check.targetSha,after);
  const bundle=path.resolve(binary,'../..');
  const stamp=JSON.parse(fs.readFileSync(path.join(bundle,'Resources/install-stamp.json'),'utf8'));
  assert.equal(stamp.commit,after);
  assert(!stamp.distribution, 'No community updater provider');
  execFileSync('/usr/bin/codesign',['--verify','--deep','--strict',path.resolve(bundle,'..')]);
  // Observe automatic restart before closing that exact process and exercising
  // a further cold launch through the native bridge on the newly built app.
  await sleep(10000);
  assert.equal(runningAppPid(oldPid),relaunchedPid);
  process.kill(relaunchedPid,'SIGTERM');
  await until(()=>!runningAppPid(oldPid),30000,'close exact automatically restarted app');
  relaunchedPid=null;
  app=await _electron.launch({executablePath:binary, env:vanillaEnv, timeout:90000});
  page=await app.firstWindow({timeout:90000});
  await page.waitForFunction(()=>!!window.hermesDesktop?.getAgentRoster,null,{timeout:90000});
  const roster=await page.evaluate(()=>window.hermesDesktop.getAgentRoster());
  const local=roster.sources.find(x=>x.connectionId==='local');
  assert.equal(local?.error,'connect-on-demand');
  assert.equal(local.reachable,false);
  assert.equal(roster.primaryConnectionId,'remote-proof');
  const paths=await app.evaluate(({app})=>({userData:app.getPath('userData'),home:app.getPath('home')}));
  assert.equal(paths.userData,userData);
  const configAfter=JSON.parse(fs.readFileSync(path.join(userData,'connection.json'),'utf8'));
  assert.equal(configAfter.mode,configBefore.mode);
  assert.equal(configAfter.remote.url,configBefore.remote.url);
  fs.writeFileSync(path.join(logs,'official-update-cycle.json'),JSON.stringify({
    status:'advanced',before,after,checkedTarget:check.targetSha,oldPid,automaticRelaunchObserved:true,
    stamp,result,postUpdateRoster:roster,paths,remoteRoutePreserved:true,
    authenticatedVpsChat:'not-run',claim:'real source/app update and natural restart with unavailable test remote'
  },null,2)+'\n');
} finally {
  resultWatcher.close();
  if(app) await app.close();
  if(relaunchedPid) { try {process.kill(relaunchedPid,'SIGTERM');} catch(error) {if(error.code!=='ESRCH') throw error;} }
  for(const name of ['desktop-update-handoff.log','update.log']) {
    const file=path.join(state,'logs',name);
    if(fs.existsSync(file)) fs.copyFileSync(file,path.join(logs,name));
  }
}
