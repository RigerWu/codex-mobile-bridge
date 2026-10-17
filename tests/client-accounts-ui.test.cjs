'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function fixture({desktop=false,provider='deepseek',toolbar=false}={}){
  function node(tag){return {tag,children:[],dataset:{},textContent:'',hidden:false,disabled:false,attributes:{},classList:{add(){}},append(...nodes){this.children.push(...nodes);},replaceChildren(...nodes){this.children=nodes;},setAttribute(key,value){this.attributes[key]=value;},closest(){return null;},querySelectorAll(selector){return this.children.flatMap(child=>[...(selector==='[data-i18n]'&&child.dataset.i18n?[child]:[]),...child.querySelectorAll(selector)]);}};}
  const root=node('div'),outerToolbar=toolbar?node('div'):null,listeners={},timers=new Map(),calls=[],prompts=[],updates=[];let time=1000000,tid=0,visible=true,confirmed=true,value={accounts:[],activeId:null,canSwitch:true},handler=async()=>value,readHandler=async()=>value;
  const document={hidden:false,documentElement:{},createElement:node,querySelectorAll:()=>[],addEventListener:(type,fn)=>(listeners[type]??=[]).push(fn),removeEventListener:(type,fn)=>listeners[type]=listeners[type].filter(row=>row!==fn),dispatchEvent(event){for(const fn of listeners[event.type]||[])fn(event);}};
  class Clock extends Date{static now(){return time;}}
  const context=vm.createContext({document,Date:Clock,CustomEvent:class{constructor(type){this.type=type;}},localStorage:{getItem(){return null;},setItem(){}},window:{confirm(message){prompts.push(message);return confirmed;}},setTimeout(fn,ms){timers.set(++tid,{fn,due:time+ms});return tid;},clearTimeout:id=>timers.delete(id)});
  for(const path of ['web/shared/i18n.js','web/features/accounts/client-accounts.js'])vm.runInContext(fs.readFileSync(path,'utf8'),context);
  const Panel=vm.runInContext('DesktopClientAccountsPanel',context),panel=new Panel({root,provider,desktop,toolbar:outerToolbar,read:()=>{calls.push({action:'list'});return readHandler();},request:body=>{calls.push(body);return handler(body);},readModels:()=>handler({action:'models'}),visible:()=>visible,onUpdate:result=>updates.push(result)});
  const all=node=>[node,...node.children.flatMap(all)];
  return {panel,root,outerToolbar,calls,prompts,updates,timers,setValue:next=>value=next,setHandler:next=>handler=next,setRead:next=>readHandler=next,setVisible:next=>visible=next,confirm:next=>confirmed=next,advance:async ms=>{time+=ms;for(const [id,timer]of [...timers])if(timer.due<=time){timers.delete(id);await timer.fn();}},tick:ms=>time+=ms,now:()=>time,all:()=>all(root),text:()=>all(root).map(row=>row.textContent).join('\n'),english:()=>vm.runInContext("BridgeI18n.setLanguage('en')",context)};
}
const row=(id,kind='account',usage)=>({id,name:id,kind,usage});
const snapshot=(accounts,activeIds=['current'])=>({accounts,activeId:activeIds[0],activeIds,current:{name:'Current desktop access'},canSwitch:true});
test('multiple native active connections are first and cannot be switched again',()=>{
  const ui=fixture();ui.panel.accept(snapshot([row('other'),row('api','api'),row('current')],['current','api']));
  assert.deepEqual(ui.panel.rows.children.map(card=>card.children[0].children[0].children[0].children[0].textContent),['api','current','other']);
  const buttons=ui.all().filter(node=>node.tag==='button'&&['当前接入','切换到此接入'].includes(node.textContent));assert.deepEqual(buttons.map(button=>button.disabled),[true,true,false]);
  ui.panel.choose(row('api'));assert.equal(ui.prompts.length,0);ui.panel.value.canSwitch=false;ui.panel.choose(row('other'));assert.equal(ui.prompts.length,0);
});
test('first visible visit queries automatically; manual quota query resets the five minute clock',async()=>{
  const ui=fixture();ui.setValue(snapshot([row('current')]));await ui.panel.resume();assert.deepEqual(ui.calls.map(call=>call.action),['list','details']);assert.equal(ui.calls[1].refresh,false);
  await ui.advance(299000);assert.equal(ui.calls.length,2);await ui.panel.perform({action:'details',id:'current',refresh:true});assert.equal(ui.calls.length,3);
  await ui.advance(1000);assert.equal(ui.calls.length,3);await ui.advance(299000);assert.deepEqual(ui.calls.map(call=>call.action),['list','details','details','list','details']);
});
test('hidden panels do not query quota and resume when overdue',async()=>{
  const ui=fixture();ui.setValue(snapshot([row('current')]));await ui.panel.resume();ui.setVisible(false);await ui.advance(300000);assert.equal(ui.calls.length,2);assert.equal(ui.timers.size,0);
  ui.setVisible(true);await ui.panel.resume();assert.equal(ui.calls.length,4);
});
test('Claude list, timer, and return visits only read saved quota snapshots',async()=>{
  const ui=fixture({provider:'claude'});ui.setValue(snapshot([row('current'),row('saved')]));
  await ui.panel.resume();await ui.advance(300000);ui.setVisible(false);await ui.advance(300000);ui.setVisible(true);await ui.panel.resume();
  assert.deepEqual(ui.calls.map(call=>call.action),['list','list','list']);
  assert.match(ui.text(),/尚未查询/);
  ui.english();assert.match(ui.text(),/Not queried yet/);
});
test('Claude explicit quota query is single-flight and failed refresh keeps the last success time',async()=>{
  const ui=fixture({provider:'claude'}),saved={status:'ready',checkedAt:1000,limits:[{name:'5h',windows:[{remainingPercent:70}]}]};
  ui.panel.accept(snapshot([row('current','account',saved)]));assert.match(ui.text(),/查询时间：/);assert.match(ui.text(),/1970-01-01 \d\d:16/);
  const card=ui.panel.rows.children[0],controls=card.children[0].children[1];
  assert.equal(controls.className,'accounts-usage-controls');assert.equal(controls.children[0].textContent,'查看剩余额度');
  assert.match(controls.children[1].textContent,/查询时间：/);assert.equal(card.children[1].children[1].children.some(node=>node.className==='account-updated'),false);
  let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));
  const button=ui.all().find(node=>node.tag==='button'&&node.textContent==='查看剩余额度');const pending=button.onclick();
  assert.equal(ui.calls.length,1);assert.equal(ui.calls[0].refresh,true);
  assert.equal(ui.all().find(node=>node.tag==='button'&&node.textContent==='查询中…').disabled,true);
  await ui.panel.perform({action:'details',id:'current',refresh:true});assert.equal(ui.calls.length,1);
  finish(snapshot([row('current','account',{...saved,status:'error',error:'无法查询 Claude 剩余额度，请确认客户端登录有效并解锁系统凭据存储后重试'})]));await pending;
  assert.equal(ui.all().find(node=>node.tag==='button'&&node.textContent==='查看剩余额度').disabled,false);
  assert.match(ui.text(),/70%/);assert.match(ui.text(),/无法查询 Claude 剩余额度/);assert.match(ui.text(),/保留上次结果/);assert.match(ui.text(),/1970-01-01 \d\d:16/);
  ui.english();assert.match(ui.text(),/Queried at: /);assert.match(ui.text(),/Showing the last result/);assert.match(ui.text(),/Cannot check Claude remaining usage/);
});
test('Claude quota button shows an English pending state',async()=>{
  const ui=fixture({provider:'claude'});ui.english();ui.panel.accept(snapshot([row('current')]));
  let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));
  const pending=ui.all().find(node=>node.tag==='button'&&node.textContent==='View remaining quota').onclick();
  assert.equal(ui.all().find(node=>node.tag==='button'&&node.textContent==='Checking…').disabled,true);
  finish(snapshot([row('current')]));await pending;
  assert.equal(ui.all().find(node=>node.tag==='button'&&node.textContent==='View remaining quota').disabled,false);
});
test('closing a panel discards its late account response and refresh timers',async()=>{
  const ui=fixture();let finish;ui.setRead(()=>new Promise(resolve=>finish=resolve));const pending=ui.panel.resume();ui.panel.dispose();finish(snapshot([row('current')]));await pending;assert.equal(ui.panel.value,null);assert.equal(ui.timers.size,0);assert.equal(ui.updates.length,0);
});
test('DSH wallet amounts stay separate from percent quota and never invent missing balances',()=>{
  const ui=fixture();ui.panel.accept(snapshot([row('current','account',{status:'ready',limits:[],balance:{wallets:[{currency:'CNY',remaining:'12.30',toppedUp:'10.00',granted:'2.30'},{currency:'USD',remaining:'4.00'}]}})]));
  assert.match(ui.text(),/CNY 12.30/);assert.match(ui.text(),/USD 4.00/);assert.match(ui.text(),/10.00/);assert.equal(ui.all().filter(node=>node.tag==='progress').length,0);
  const card=ui.panel.rows.children[0];assert.match(card.className,/accounts-client-card is-current/);assert.match(card.children[1].className,/accounts-client-usage/);
  assert.equal(card.children[1].children[0].children[0].textContent,'账户余额');assert.equal(card.children[0].children[1].children[0].textContent,'刷新余额');
  ui.panel.accept(snapshot([row('current','account',{status:'unsupported',limits:[]})]));assert.match(ui.text(),/余额信息暂不可用/);assert.doesNotMatch(ui.text(),/0%|12.30|USD/);
});
test('DeepSeek balance time stays beside its refresh control',()=>{
  const ui=fixture(),checkedAt=1000;
  ui.panel.accept(snapshot([row('current','account',{status:'ready',checkedAt,balance:{wallets:[{currency:'CNY',remaining:'12.30'}]}})]));
  const controls=ui.panel.rows.children[0].children[0].children[1];
  assert.equal(controls.className,'accounts-usage-controls');assert.equal(controls.children[0].textContent,'刷新余额');
  assert.match(controls.children[1].textContent,/更新于：/);
});
test('client cards keep provider usage and API actions distinct',async()=>{
  const claude=fixture({provider:'claude'});claude.panel.accept(snapshot([row('current'),row('api','api')]));
  assert.match(claude.panel.rows.children[0].children[1].className,/accounts-client-usage/);
  assert.equal(claude.panel.rows.children[0].children[0].children[1].children[0].textContent,'查看剩余额度');
  assert.equal(claude.panel.rows.children[1].children.length,2,'API card has identity and actions, no invented quota');
  const dsh=fixture();dsh.panel.accept(snapshot([row('current'),row('api','api')]));
  assert.equal(dsh.panel.rows.children[1].children.length,2);
  dsh.english();assert.match(dsh.text(),/Refresh balance/);assert.doesNotMatch(dsh.text(),/[\u4e00-\u9fff]/);
  await dsh.panel.rows.children[0].children[0].children[1].children[0].onclick();
  assert.deepEqual(JSON.parse(JSON.stringify(dsh.calls)),[{action:'details',id:'current',refresh:true}]);
});
test('DSH API wallet appears only when the backend supplies a balance',()=>{
  const ui=fixture();ui.panel.accept(snapshot([row('api','api',{status:'ready',balance:{wallets:[{currency:'CNY',remaining:'9.50'}]}})],['api']));
  assert.match(ui.text(),/CNY 9.50/);assert.equal(ui.panel.rows.children[0].children.length,3);
  ui.panel.accept(snapshot([row('api','api',{status:'unsupported'})],['api']));
  assert.equal(ui.panel.rows.children[0].children.length,2);assert.doesNotMatch(ui.text(),/CNY 9.50|余额信息暂不可用/);
});
test('external toolbar places refresh beside the title and models below cards',async()=>{
  const ui=fixture({toolbar:true});ui.setValue(snapshot([row('current')]));ui.panel.accept(snapshot([row('current')]));
  assert.equal(ui.outerToolbar.children.length,1);assert.equal(ui.outerToolbar.children[0].textContent,'刷新');
  assert.equal(ui.root.children.some(node=>node.className==='accounts-toolbar'),false);
  assert.ok(ui.root.children.indexOf(ui.panel.modelsButton)>ui.root.children.indexOf(ui.panel.rows));
  await ui.outerToolbar.children[0].onclick();assert.deepEqual(ui.calls.map(call=>call.action),['list','details']);
});
test('Claude enrollment confirms restart while DSH API import remains a read-only native snapshot',async()=>{
  const ui=fixture({desktop:true,provider:'claude'});ui.setValue(snapshot([row('current')]));ui.panel.accept(snapshot([row('current')]));ui.confirm(false);await ui.panel.importCurrent();assert.equal(ui.calls.length,0);
  assert.match(ui.prompts[0],/解锁状态/);ui.confirm(true);ui.panel.nameInput.value='Work account';await ui.panel.importCurrent();assert.equal(ui.calls[0].action,'import-current');assert.equal(ui.calls[0].name,'Work account');assert.equal(ui.panel.nameInput.value,'');assert.ok(ui.prompts[0].includes('短暂重启'));
  const dsh=fixture({desktop:true});dsh.setValue(snapshot([]));await dsh.panel.importCurrent('api');assert.equal(dsh.calls[0].kind,'api');assert.equal(dsh.prompts.length,0);
  const web=fixture();await web.panel.importCurrent();assert.equal(web.calls.length,0);assert.equal(web.panel.importButton,undefined);
});
test('Claude account switch explains unlock requirement in the same confirmation and cancel submits nothing',async()=>{
 const ui=fixture({provider:'claude'});ui.panel.accept(snapshot([row('current'),row('other')]));ui.confirm(false);await ui.panel.choose(row('other'));assert.equal(ui.calls.length,0);assert.equal(ui.prompts.length,1);assert.match(ui.prompts[0],/切换会重启/);assert.match(ui.prompts[0],/解锁状态/);
 ui.english();await ui.panel.choose(row('other'));assert.equal(ui.calls.length,0);assert.match(ui.prompts[1],/unlocked/);assert.doesNotMatch(ui.prompts[1],/[\u4e00-\u9fff]/);
 ui.confirm(true);await ui.panel.choose(row('other'));assert.equal(ui.calls.length,1);assert.equal(ui.calls[0].action,'switch');assert.equal(ui.prompts.length,3,'each attempt has only one confirmation');
 const dsh=fixture();dsh.panel.accept(snapshot([row('current'),row('other')]));dsh.confirm(false);await dsh.panel.choose(row('other'));assert.doesNotMatch(dsh.prompts[0],/解锁|Claude/);assert.equal(dsh.calls.length,0);
});
test('Claude API switch sends local CLI choice only after checkbox selection',async()=>{
 const ui=fixture({provider:'claude'});ui.panel.accept(snapshot([row('current'),row('other','api')]));
 const box=ui.all().find(node=>node.tag==='input'&&node.type==='checkbox');
 assert.ok(box);box.checked=true;box.onchange();ui.confirm(true);await ui.panel.choose(row('other','api'));
 assert.equal(ui.calls[0].syncLocalCli,true);
});
test('switch remains pending until the backend finishes and rejects duplicate clicks',async()=>{
  const ui=fixture();ui.panel.accept(snapshot([row('current'),row('other')]));let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));const pending=ui.panel.choose(row('other'));assert.equal(ui.panel.busy,true);await ui.panel.choose(row('other'));assert.equal(ui.calls.length,1);
  finish(snapshot([row('current'),row('other')],['other']));await pending;assert.equal(ui.panel.busy,false);assert.equal(ui.panel.value.activeId,'other');
});
test('late model results cannot leak across account changes',async()=>{
  const ui=fixture();ui.panel.accept(snapshot([row('current'),row('other')]));let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));const pending=ui.panel.toggleModels();ui.panel.accept(snapshot([row('current'),row('other')],['other']));finish({models:[{id:'old-model'}]});await pending;assert.equal(ui.panel.models,null);assert.equal(ui.panel.modelsLoading,false);assert.doesNotMatch(ui.text(),/old-model/);
});
test('English account content uses translated state, wallet labels and restart confirmation',async()=>{
  const ui=fixture({desktop:true,provider:'claude'});ui.english();ui.panel.accept(snapshot([row('current','account',{status:'unsupported',limits:[]})]));
  assert.doesNotMatch(ui.text(),/[\u4e00-\u9fff]/);ui.confirm(false);await ui.panel.importCurrent();assert.doesNotMatch(ui.prompts[0],/[\u4e00-\u9fff]/);
});

test('leaving during quota refresh stops further upstream account queries',async()=>{
 const ui=fixture();ui.setValue(snapshot([row('current'),row('saved')]));let finish,started;const querying=new Promise(resolve=>started=resolve);ui.setHandler(()=>{started();return new Promise(resolve=>finish=resolve);});const loading=ui.panel.resume();await querying;assert.equal(ui.calls.filter(call=>call.action==='details').length,1);ui.setVisible(false);finish(snapshot([row('current'),row('saved')]));await loading;assert.equal(ui.calls.filter(call=>call.action==='details').length,1);
});

test('account model catalog renders native provider groups and reasoning options without a chat',async()=>{
 const ui=fixture();ui.english();ui.panel.accept(snapshot([]));ui.setHandler(async()=>({groups:[{id:'deepseek-account',name:'Official access',models:[{id:'deepseek-account/deepseek-v4-pro',name:'DeepSeek Pro',efforts:['low','high']}]},{id:'custom',name:'Configured API',models:[{id:'custom/model-x',name:'Custom model'}]}]}));await ui.panel.toggleModels();assert.match(ui.text(),/Official access/);assert.match(ui.text(),/DeepSeek Pro/);assert.match(ui.text(),/low.*high/);assert.match(ui.text(),/Custom model/);assert.doesNotMatch(ui.text(),/进入会话|可用模型/);
});
test('flat model responses are preserved when the backend has no groups',async()=>{
 const ui=fixture();ui.setHandler(async()=>({groups:[],models:[{id:'flat/model',name:'Flat model'}]}));await ui.panel.toggleModels();assert.match(ui.text(),/Flat model/);
});
test('model read errors are retryable and are not presented as a successful empty catalog',async()=>{
 const ui=fixture();ui.setHandler(async()=>{throw Error('model catalog unavailable');});await ui.panel.toggleModels();assert.match(ui.text(),/model catalog unavailable/);assert.doesNotMatch(ui.text(),/未返回可用模型|进入会话/);ui.setHandler(async()=>({models:[{id:'recovered-model'}]}));await ui.all().find(node=>node.tag==='button'&&node.textContent==='重试').onclick();assert.match(ui.text(),/recovered-model/);assert.doesNotMatch(ui.text(),/model catalog unavailable/);
});

test('API editor exists only in desktop and sends a native gateway draft without applying it',async()=>{
 const web=fixture();assert.equal(web.panel.editor,undefined);assert.equal(web.panel.addApiButton,undefined);await web.panel.editAccount(row('one'));await web.panel.removeAccount(row('one'));assert.equal(web.calls.length,0);
 const ui=fixture({desktop:true,provider:'claude'});ui.setValue(snapshot([]));ui.panel.addApiButton.onclick();ui.panel.editName.input.value='Upstream';ui.panel.editUrl.input.value='https://gateway.test';ui.panel.editKey.input.value='private-key';ui.panel.editModels.input.value='model-one\nmodel-two';ui.panel.editAuth.value='x-api-key';await ui.panel.saveEditor();
 assert.deepEqual(JSON.parse(JSON.stringify(ui.calls)),[{action:'save-api',name:'Upstream',apiKey:'private-key',baseUrl:'https://gateway.test',authScheme:'x-api-key',models:['model-one','model-two']}]);assert.equal(ui.panel.editKey.input.value,'');assert.equal(ui.panel.editor.hidden,true);assert.doesNotMatch(ui.text(),/private-key/);
});
test('API edits keep the existing key blank and DSH does not expose unsupported custom URL fields',async()=>{
 const ui=fixture({desktop:true});ui.panel.accept(snapshot([row('saved','api')]));ui.setHandler(async value=>value.action==='edit'?{id:'saved',name:'Saved API',kind:'api',api:{hasKey:true}}:snapshot([row('saved','api')]));await ui.panel.editAccount(row('saved','api'));
 assert.equal(ui.panel.editKey.input.value,'');assert.equal(ui.panel.editKey.input.required,false);assert.equal(ui.panel.editUrl.wrap.hidden,true);assert.equal(ui.panel.editModels.wrap.hidden,true);await ui.panel.saveEditor();assert.deepEqual(JSON.parse(JSON.stringify(ui.calls[1])),{action:'save-api',id:'saved',name:'Saved API',apiKey:''});assert.equal(ui.calls.some(call=>call.action==='switch'),false);
});
test('official account edit is a name-only operation and deletion requires confirmation',async()=>{
 const ui=fixture({desktop:true});ui.panel.accept(snapshot([row('saved')]));ui.setHandler(async value=>value.action==='edit'?{id:'saved',name:'Saved',kind:'official'}:snapshot([]));await ui.panel.editAccount(row('saved'));assert.equal(ui.panel.editKey.wrap.hidden,true);ui.panel.editName.input.value='Renamed';await ui.panel.saveEditor();assert.deepEqual(JSON.parse(JSON.stringify(ui.calls[1])),{action:'rename',id:'saved',name:'Renamed'});
 ui.confirm(false);await ui.panel.removeAccount(row('saved'));assert.equal(ui.calls.length,2);ui.confirm(true);await ui.panel.removeAccount(row('saved'));assert.equal(ui.calls[2].action,'remove');assert.match(ui.prompts.at(-1),/不会退出电脑上的账号/);
});
test('closing account panel discards a late edit response and erases typed key',async()=>{
 const ui=fixture({desktop:true});ui.panel.openEditor({kind:'api',api:{}});ui.panel.editKey.input.value='private-key';ui.panel.closeEditor();assert.equal(ui.panel.editKey.input.value,'');let finish;ui.setHandler(()=>new Promise(resolve=>finish=resolve));const pending=ui.panel.editAccount(row('saved'));ui.panel.dispose();finish({id:'saved',name:'Saved',kind:'api',api:{hasKey:true}});await pending;assert.equal(ui.panel.editor.hidden,true);assert.equal(ui.panel.editing,null);
});
