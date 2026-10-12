'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const {webcrypto,createHmac}=require('node:crypto');
const config={cwd:'/gateway-b',request:'request.json',response:'response.json',token:'fixture',generation:'generation-b',reconnect:true,connectorRevision:5};
const source=fs.readFileSync('bridge/clients/claude/connector.js','utf8').replace('__BRIDGE_CONFIG__',JSON.stringify(config));
function fixture(old,ownerCwd='/gateway-a'){
 let stopped=0,writes=0,starts=0;
 const payload=JSON.stringify({generation:config.generation,type:'idle',seq:0});
 const envelope=JSON.stringify({payload,signature:createHmac('sha256',config.token).update(payload).digest('hex')});
 const api={
  async getAll(){return [{sessionId:'legacy-session',cwd:ownerCwd,title:'Codex Bridge Connector'}];},
  async readFileAtCwd(){return {contents:envelope};},
  async writeSessionFile(){writes++;return {hash:'fixture'};},
  async start(){starts++;return {sessionId:'new-session'};}
 };
 const window={'claude.web':{LocalSessions:api},__claudeMobileBridge:{
  status:()=>old,stop(){stopped++;},...(old?.owns?{owns:old.owns}:{})}};
 const context={window,crypto:webcrypto,TextEncoder,Uint8Array,Date,setTimeout,clearTimeout,console:{info(){},error(){}}};
 return {window,run:()=>vm.runInNewContext(source,context),counts:()=>({stopped,writes,starts})};
}
for(const [name,status] of [
 ['live foreign legacy owner',{transport:'file',stopped:false,busy:false,sessionId:'legacy-session',lastWrite:Date.now()}],
 ['stale foreign legacy owner',{transport:'file',stopped:false,busy:false,sessionId:'legacy-session',lastWrite:1}],
 ['live foreign current owner',{transport:'file',stopped:false,busy:false,owns:()=>false,lastWrite:Date.now()}],
 ['unknown legacy owner',{transport:'file',stopped:false,busy:false,sessionId:'missing',lastWrite:Date.now()}]
])test(name+' remains intact and reports conflict',async()=>{
 const ui=fixture(status);
 await assert.rejects(ui.run(),/其他网关占用/);
 assert.deepEqual(ui.counts(),{stopped:0,writes:0,starts:0});
 assert.match(ui.window.__codingRemoteInjectionError,/其他网关占用/);
});
test('legacy same workspace may be replaced without exposing its token',async()=>{
 const ui=fixture({transport:'file',stopped:false,busy:false,sessionId:'legacy-session',lastWrite:Date.now()},config.cwd);
 const running=ui.run();
 for(let i=0;i<50&&!ui.window.__claudeMobileBridge?.owns;i++)await new Promise(resolve=>setTimeout(resolve,10));
 assert.equal(ui.counts().stopped,1);assert.equal(ui.window.__claudeMobileBridge.owns(config.cwd),true);
 assert.equal(ui.window.__claudeMobileBridge.owns('/gateway-a'),false);
 assert.ok(!JSON.stringify(ui.window.__claudeMobileBridge.status()).includes(config.token));
 ui.window.__claudeMobileBridge.stop();await running;
});
test('pending request or response write blocks even same-owner replacement',async()=>{
 for(const field of ['busy','writing']){
  const ui=fixture({transport:'file',stopped:false,sessionId:'legacy-session',[field]:true},config.cwd);
  await assert.rejects(ui.run(),/请求进行中/);
  assert.equal(ui.counts().stopped,0);
 }
});
test('legacy stopped flag alone does not prove a signed release',async()=>{
 const ui=fixture({transport:'file',stopped:true,busy:false,sessionId:'legacy-session'});
 await assert.rejects(ui.run(),/其他网关占用/);
 assert.equal(ui.counts().stopped,0);
});
