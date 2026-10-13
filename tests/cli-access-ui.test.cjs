'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const app=fs.readFileSync(path.join(__dirname,'../web/shell/app.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../web/index.html'),'utf8');
const extract=(start,end)=>app.slice(app.indexOf(start),app.indexOf(end,app.indexOf(start)));
const accessCode=extract('function sessionAccess(access){','function renderList(){');
const listCode=extract('function renderList(){','async function loadList(');
const stateCode=extract('function renderState(view){',"$('send-mode').onchange");
function node(){return {hidden:false,disabled:false,value:'',textContent:'',title:'',children:[],scrollTop:0,classList:{toggle(){}},append(...parts){this.children.push(...parts);},replaceChildren(...parts){this.children=parts;},querySelector(){return node();}};}
function fixture(){
  const nodes=new Map();const $=id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id);};
  const ctx={$ ,state:null,listRows:[],listRenderStamp:'',currentId:'a',currentHost:'local',sending:false,BridgeI18n:{locale:()=> 'en-US'},t:key=>({'此电脑':'This computer','其他来源':'Other source','历史记录':'History','重新连接':'Reconnect','连接 CLI':'Connect CLI'}[key]||key),localStorage:{getItem:()=>null,setItem(){}},dateText:()=> 'today',el:(tag,cls='',value='')=>Object.assign(node(),{tag,cls,textContent:value,dataset:{}}),renderActivity(){},contextUsageControl:{update(){}},fastModeControl:{sync(){}},visibleChat:()=>false,sessionActivity:{read(){}},attachments:{ready:()=>true},workModes:{render(view){ctx.modesView=view;}},workbench:{current:{}},chatTimeline:{refreshActions(){}},renderApprovals(rows){ctx.approvals=rows;},renderQueue(){},approvalStamp:'',chatKey:()=> 'local|a'};
  vm.createContext(ctx);vm.runInContext(accessCode+listCode+stateCode,ctx);
  return {ctx,$,nodes};
}
const cli={source:'cli',runtime:null,readOnly:true,canSend:false,canInterrupt:false,canApprove:false,canReconnect:false};
test('CLI list rows retain source identity while legacy Desktop rows remain unchanged',()=>{
  const {ctx,$}=fixture();ctx.listRows=[{id:'a',host:'local',title:'CLI thread',projectName:'Project',projectKey:'p',recency:1,access:cli},{id:'b',host:'local',title:'Desktop thread',projectName:'Project',projectKey:'p',recency:2}];$('list-mode').value='recent';ctx.renderList();
  assert.match($('sessions').children[0].children[1].children[0].textContent,/CLI/);
  assert.doesNotMatch($('sessions').children[1].children[1].children[0].textContent,/CLI|Desktop/);
  assert.equal(ctx.sessionAccess().canSend,true);assert.equal(ctx.sessionAccess(cli).canSend,false);
});
test('history-only CLI hides join and write controls but keeps history and copy available',()=>{
  const {ctx,$}=fixture();ctx.listRows=[{id:'a',host:'local',access:cli}];ctx.renderState({host:'local',title:'CLI thread',cwd:'/work',access:cli,connected:false,status:'idle',requests:[],submissions:[],historyComplete:true});
  assert.equal($('reconnect').hidden,true);assert.equal($('send').disabled,true);assert.equal($('message').disabled,true);assert.equal($('stop').hidden,true);assert.equal($('permissions-button').disabled,true);assert.equal($('work-mode').disabled,true);assert.equal(ctx.approvals.length,0);
  assert.match($('notice-text').textContent,/CLI/);
});
test('joinable CLI presents an explicit join without enabling unsupported controls',()=>{
  const {ctx,$}=fixture();const joinable={...cli,canReconnect:true};ctx.renderState({host:'local',title:'CLI thread',cwd:'/work',access:joinable,connected:false,status:'idle',requests:[],submissions:[],historyComplete:true});
  assert.equal($('reconnect').hidden,false);assert.equal($('reconnect').textContent,'Connect CLI');assert.equal($('send').disabled,true);
});
test('session diagnostics is directly available from the list',()=>{
  assert.match(html,/<a id="session-diagnostics"[^>]+href="#"[^>]+download="session-diagnostics\.json"/);
  assert.match(app,/openingAccess\.source==='desktop'&&openingAccess\.canReconnect/);assert.match(app,/BridgeRelayURL\?\.\('\/api\/sessions\/diagnostics'\)/);
});
