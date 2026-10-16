'use strict';
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../web/features/accounts/cli-access.js'),'utf8');
const context={BridgeI18n:{t:key=>({'正在读取…':'Loading…','正在保存…':'Saving…','请填写有效 API Key':'Enter a valid API key.'}[key]||key)}};
vm.createContext(context);vm.runInContext(source+'\nglobalThis.CliAccessPanel=CliAccessPanel;',context);
function deferred(){let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function panel(){
  const controls=[{disabled:false},{disabled:false}],instance=Object.create(context.CliAccessPanel.prototype);
  instance.root={querySelectorAll:()=>controls};instance.value=null;instance.busy=false;instance.generation=0;instance.apiResults=new Map();
  instance.provider='claude';instance.key={input:{value:'private-key'}};
  instance.cancel={hidden:false};instance.status={textContent:''};instance.error={textContent:''};
  instance.form={reset(){instance.key.input.value='';}};
  instance.render=()=>{instance.rendered=(instance.rendered||0)+1;};
  return {instance,controls};
}
test('clearing CLI access scrubs the key and ignores a late read',async()=>{
  const {instance,controls}=panel(),pending=deferred();instance.read=()=>pending.promise;
  const reading=instance.refresh();assert.equal(instance.busy,true);assert.equal(controls[0].disabled,true);
  instance.clear();assert.equal(instance.key.input.value,'');assert.equal(instance.busy,false);assert.equal(controls[0].disabled,false);
  pending.resolve({claude:{accounts:[{name:'stale'}]}});await reading;
  assert.equal(instance.value,null);assert.equal(instance.status.textContent,'');assert.equal(instance.error.textContent,'');
});
test('closing during save ignores its late result and translates a later error',async()=>{
  const {instance}=panel(),pending=deferred(),request={provider:'claude',action:'saveApi',apiKey:'private-key'};
  instance.request=()=>pending.promise;
  const saving=instance.perform(request);instance.clear();pending.resolve({savedId:'stale'});await saving;
  assert.equal(instance.value,null);assert.equal(instance.key.input.value,'');assert.equal(request.apiKey,'');
  instance.request=async()=>{throw Error('请填写有效 API Key');};
  await instance.perform({provider:'claude',action:'saveApi'});
  assert.equal(instance.error.textContent,'Enter a valid API key.');assert.equal(instance.busy,false);
});
test('scoped model probe never switches accounts and ignores a late response after closing',async()=>{
  const {instance}=panel(),pending=deferred(),calls=[];
  instance.request=value=>{calls.push(value);return pending.promise;};
  const row={id:'saved-api'},checking=instance.probe(row,'models');
  assert.equal(JSON.stringify(calls),JSON.stringify([{provider:'claude',action:'models',id:'saved-api'}]));
  instance.clear();pending.resolve({probe:{provider:'claude',id:'saved-api',status:'reachable',scope:'models',models:['model-a']}});
  await checking;assert.equal(instance.value,null);assert.equal(instance.apiResults.size,0);
});
