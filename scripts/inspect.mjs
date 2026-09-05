import {createRequire} from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
const [source,resources,platform,arch,logs]=process.argv.slice(2);
const root=path.resolve(import.meta.dirname,'..');
const pin=JSON.parse(fs.readFileSync(path.join(root,'upstream.json')));
const require=createRequire(path.join(source,'apps/desktop/package.json'));
const asar=require('@electron/asar');
const archive=path.join(resources,'app.asar');
const entries=asar.listPackage(archive).map(x=>x.replace(/^\//,''));
const inventory=[];
const syntaxDir=path.join(logs,'syntax');fs.mkdirSync(syntaxDir,{recursive:true});
for(const name of entries){
  const stat=asar.statFile(archive,name);
  if(stat.files||stat.link)continue;
  assert(!/(^|\/)(\.env(?:\..*)?|auth\.json|config\.yaml|state\.db|id_rsa|id_ed25519|venv|\.venv|hermes-agent)(\/|$)|\.(py|pyc|pyo|p8|p12|pfx)$/.test(name),`Disallowed payload: ${name}`);
  const data=asar.extractFile(archive,name);
  const sha256=crypto.createHash('sha256').update(data).digest('hex');
  assert.equal(data.length,stat.size,name);
  if(stat.integrity){assert.equal(stat.integrity.algorithm,'SHA256');assert.equal(stat.integrity.hash,sha256,name);}
  if(platform!=='linux')assert(!data.subarray(0,4).equals(Buffer.from([0x7f,0x45,0x4c,0x46])),`ELF in non-Linux asar: ${name}`);
  if(/\.(mjs|js|json|html)$/.test(name)){
    assert(!/-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\r\n]+[A-Za-z0-9+/]/.test(data.toString()),`Private key: ${name}`);
    assert(!/\b(?:ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{70,}|sk-proj-[A-Za-z0-9_-]{40,}|AKIA[A-Z0-9]{16})\b/.test(data.toString()),`Credential-shaped literal: ${name}`);
  }
  if(/\.(mjs|js|cjs)$/.test(name)){
    const file=path.join(syntaxDir,'check'+(/\b(?:import|export)\s/.test(data.toString())?'.mjs':'.cjs'));
    fs.writeFileSync(file,data);
    const result=spawnSync(process.execPath,['--check',file],{encoding:'utf8'});
    assert.equal(result.status,0,`${name}: ${result.stderr}`);
  }
  inventory.push({name,size:stat.size,sha256,unpacked:!!stat.unpacked});
}
fs.rmSync(syntaxDir,{recursive:true});
const pkg=JSON.parse(asar.extractFile(archive,'package.json'));
assert.equal(pkg.main,'dist/electron-main.mjs');assert.equal(pkg.version,pin.version);
for(const required of [pkg.main,'dist/electron-preload.js','dist/index.html','dist/node_modules/node-pty/lib/index.js','dist/node_modules/get-windows/index.js'])assert(inventory.some(x=>x.name===required),required);
assert(inventory.some(x=>x.name.startsWith('dist/node_modules/node-pty/')&&x.name.endsWith('.node')&&x.unpacked),'Native node-pty missing');
if(platform==='darwin')for(const required of [`dist/node_modules/node-pty/prebuilds/darwin-${arch}/pty.node`,`dist/node_modules/node-pty/prebuilds/darwin-${arch}/spawn-helper`,'dist/node_modules/get-windows/main'])assert(inventory.some(x=>x.name===required&&x.unpacked),required);
if(platform==='win32')assert(inventory.some(x=>x.name.includes('get-windows/lib/binding/')&&x.name.endsWith('node-get-windows.node')&&x.unpacked),'Real Windows window-enumeration binding required');
const renderer=inventory.filter(x=>x.name.startsWith('dist/assets/')&&x.name.endsWith('.js')).map(x=>asar.extractFile(archive,x.name).toString()).join('\n');
assert(renderer.includes('Connect to existing Hermes'));
assert(renderer.includes('No local install will start.'));
const stamp=JSON.parse(fs.readFileSync(path.join(resources,'install-stamp.json')));
assert.equal(stamp.commit,pin.commit);assert.equal(stamp.dirty,false);
fs.writeFileSync(path.join(logs,'asar.json'),JSON.stringify({platform,arch,files:inventory.length,integrity:'all entries verified',stamp,inventory},null,2)+'\n');
console.log('ASAR integrity/native presence/credential-pattern scan/compiled JS syntax verified:',inventory.length,'files');
