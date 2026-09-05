import {createRequire} from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import assert from 'node:assert/strict';
import {createServer} from 'node:net';

const [source,binary,home,logs]=process.argv.slice(2);
assert(source&&binary&&home&&logs,'Usage: smoke.mjs source binary isolatedHome logs');
const require=createRequire(path.join(source,'apps/desktop/package.json'));
const {_electron}=require('playwright');
assert.equal(fs.existsSync(path.join(home,'.hermes')),false,'Smoke HOME must be fresh');
const env={...process.env};
env.PATH=process.platform==='win32'?path.join(env.SystemRoot||env.SYSTEMROOT||'C:\\Windows','System32'):'/usr/bin:/bin:/usr/sbin:/sbin';
const args=['--user-data-dir='+path.join(home,'u'),'--enable-logging=stderr'];
// Linux CI only: no host kernel/security changes; never Mac/Windows install advice.
if(process.platform==='linux'){delete env.DISPLAY;delete env.XAUTHORITY;args.push('--disable-gpu','--no-sandbox','--ozone-platform=headless');}
const errors=[];
let app;
try {
  app=await _electron.launch({executablePath:binary,args,env,timeout:90000});
  const page=await app.firstWindow({timeout:90000});
  page.on('pageerror',err=>errors.push(String(err)));
  await page.getByText('Connect to existing Hermes',{exact:true}).waitFor({timeout:90000});
  fs.writeFileSync(path.join(logs,'first-run.txt'),await page.locator('body').innerText());
  const paths=await app.evaluate(({app})=>({packaged:app.isPackaged,path:app.getAppPath(),home:app.getPath('home'),userData:app.getPath('userData'),hermesHome:process.env.HERMES_HOME,platform:process.platform,arch:process.arch,versions:process.versions}));
  assert.equal(paths.packaged,true);
  assert.equal(paths.platform,process.platform);
  assert.equal(paths.arch,process.arch);
  assert.equal(paths.hermesHome,path.join(home,'.hermes'));
  await page.getByText('Connect to existing Hermes',{exact:true}).click();
  await page.getByPlaceholder('https://gateway.example.com/hermes').waitFor();
  fs.writeFileSync(path.join(logs,'remote-form.txt'),await page.locator('body').innerText());
  const bootstrap=await page.evaluate(()=>window.hermesDesktop.getBootstrapState());
  assert.equal(bootstrap.active,false);
  assert.equal(bootstrap.manifest,null);
  assert.deepEqual(bootstrap.stages,{});
  assert(bootstrap.setupChoice,'No local installation started');
  const guard=createServer();
  await new Promise(resolve=>guard.listen(0,'127.0.0.1',resolve));
  const port=guard.address().port;
  await new Promise(resolve=>guard.close(resolve));
  await page.getByPlaceholder('https://gateway.example.com/hermes').fill(`http://127.0.0.1:${port}`);
  const test=page.getByRole('button',{name:'Test connection',exact:true});
  await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent==='Test connection'&&!b.disabled));
  await test.click();
  await page.waitForFunction(()=>[...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='Test connection'&&!b.disabled));
  const remoteProbe=await page.evaluate(url=>window.hermesDesktop.probeConnectionConfig(url),`http://127.0.0.1:${port}`);
  assert.equal(remoteProbe.reachable,false);
  assert(remoteProbe.error,'Actual native IPC/network probe must return an error');
  assert.equal(await page.getByRole('button',{name:'Apply and reconnect',exact:true}).isDisabled(),true);
  fs.writeFileSync(path.join(logs,'unreachable-remote.txt'),await page.locator('body').innerText());
  const ptyResult=await app.evaluate(async({app})=>{
    const {createRequire}=process.getBuiltinModule('module');
    const req=createRequire(app.getAppPath()+'/dist/electron-main.mjs');
    const pty=req('node-pty');
    return await new Promise((resolve,reject)=>{
      let output='';
      const win=process.platform==='win32';
      const t=pty.spawn(win?process.env.ComSpec||process.env.COMSPEC||'C:\\Windows\\System32\\cmd.exe':'/bin/sh',win?['/d','/c','echo HERMES_NATIVE_PTY_OK']:['-c','printf HERMES_NATIVE_PTY_OK'],{name:'xterm',cols:80,rows:24,cwd:process.env.HOME,env:{...process.env}});
      const timeout=setTimeout(()=>{t.kill();reject(new Error('PTY timeout'));},15000);
      t.onData(s=>output+=s);
      t.onExit(({exitCode})=>{clearTimeout(timeout);resolve({exitCode,output});});
    });
  });
  assert.equal(ptyResult.exitCode,0);
  assert(ptyResult.output.includes('HERMES_NATIVE_PTY_OK'));
  const after=await page.evaluate(()=>window.hermesDesktop.getBootstrapState());
  assert.equal(after.active,false);assert.deepEqual(after.stages,{});
  assert(!fs.existsSync(path.join(home,'.hermes/hermes-agent')),'No local agent checkout');
  assert.equal(errors.length,0,JSON.stringify(errors));
  const result={platform:process.platform,arch:process.arch,paths,firstRun:true,remoteForm:true,unreachableRemoteBlocksApply:true,bootstrap,ptyResult,errors,noAgentCheckout:true,localInstallStarted:false};
  fs.writeFileSync(path.join(logs,'smoke.json'),JSON.stringify(result,null,2)+'\n');
  console.log('REAL NATIVE PACKAGED APP SMOKE:',JSON.stringify(result,null,2));
} finally {if(app)await app.close();}
