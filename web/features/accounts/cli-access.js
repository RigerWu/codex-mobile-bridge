'use strict';
class CliAccessPanel {
  constructor({root,read,request}){
    Object.assign(this,{root,read,request});this.value=null;this.busy=false;
    this.build();
  }
  t(key){return BridgeI18n.t(key);}
  node(tag,key,className=''){
    const node=document.createElement(tag);node.className=className;
    if(key){node.dataset.i18n=key;node.textContent=this.t(key);}
    return node;
  }
  button(key,action){const button=this.node('button',key);button.type='button';button.onclick=action;return button;}
  field(key,type='text'){
    const label=this.node('label'),input=document.createElement('input');input.type=type;
    label.append(this.node('span',key),input);return {label,input};
  }
  build(){
    this.root.classList.add('cli-access-panel');this.root.replaceChildren();
    const heading=this.node('h2','本机 CLI 接入'),refresh=this.button('刷新',()=>this.refresh());
    this.status=this.node('p',null,'account-muted');this.status.setAttribute('role','status');
    this.error=this.node('p',null,'error');this.error.setAttribute('role','alert');
    this.rows=this.node('div',null,'cli-access-rows');
    this.provider=this.node('select');for(const [id,name] of [['codex','Codex CLI'],['claude','Claude Code CLI']]){const option=this.node('option');option.value=id;option.textContent=name;this.provider.append(option);}
    this.provider.onchange=()=>{this.resetForm();this.render();};
    this.name=this.field('名称');this.baseUrl=this.field('API 地址','url');this.model=this.field('模型 ID');this.key=this.field('API Key','password');this.key.input.autocomplete='new-password';
    this.scheme=this.node('select');for(const [id,name] of [['x-api-key','x-api-key'],['bearer','Bearer']]){const option=this.node('option');option.value=id;option.textContent=name;this.scheme.append(option);}
    this.schemeRow=this.node('label');this.schemeRow.append(this.node('span','认证方式'),this.scheme);
    this.form=this.node('form');this.form.onsubmit=event=>{event.preventDefault();const provider=this.provider.value;this.perform(this.editKind==='rename'?{provider,action:'rename',id:this.editId,name:this.name.input.value}:{provider,action:'saveApi',id:this.editId||undefined,name:this.name.input.value,baseUrl:this.baseUrl.input.value,model:this.model.input.value,apiKey:this.key.input.value,authScheme:provider==='claude'?this.scheme.value:undefined});};
    this.save=this.node('button','保存 API 接入');this.save.type='submit';
    this.cancel=this.button('取消编辑',()=>this.resetForm());this.cancel.hidden=true;
    this.form.append(this.provider,this.name.label,this.baseUrl.label,this.model.label,this.key.label,this.schemeRow,this.save,this.cancel);
    this.native=this.button('保存当前原生接入',()=>this.perform({provider:this.provider.value,action:'importCurrent',name:this.t('本机原生接入')}));
    this.root.append(heading,refresh,this.status,this.error,this.rows,this.form,this.native);
    document.addEventListener('bridge-language',()=>this.render());
  }
  resetForm(){const provider=this.provider.value;this.editId=null;this.editKind=null;this.form.reset();this.provider.value=provider;this.key.input.value='';this.cancel.hidden=true;this.render();}
  async refresh(){
    if(this.busy)return;
    this.busy=true;this.status.textContent=this.t('正在读取…');this.error.textContent='';
    try{this.value=await this.read();this.render();}
    catch(error){this.error.textContent=error.message;this.status.textContent='';}
    finally{this.busy=false;}
  }
  async perform(value){
    if(this.busy)return;
    this.busy=true;this.error.textContent='';this.status.textContent=this.t('正在保存…');
    try{this.value=await this.request(value);this.resetForm();this.render();}
    catch(error){this.error.textContent=error.message;this.status.textContent='';}
    finally{this.busy=false;}
  }
  render(){
    const provider=this.provider.value||'codex',data=this.value?.[provider];
    this.schemeRow.hidden=provider!=='claude'||this.editKind==='rename';for(const field of [this.baseUrl,this.model,this.key])field.label.hidden=this.editKind==='rename';this.save.textContent=this.t(this.editKind==='rename'?'保存名称':'保存 API 接入');this.rows.replaceChildren();
    if(!data){this.status.textContent='';return;}
    this.status.textContent=provider==='codex'?this.t('Codex CLI 与 Desktop 共用本机接入配置。'):this.t('Claude Code CLI 使用所选配置目录。');
    if(provider==='claude'&&data.verification?.status==='configured')this.status.textContent+=' '+this.t((data.accounts||[]).find(row=>row.id===data.activeId)?.kind==='api'?'API 配置已核对；未验证 Key 的远端有效性。':'原生配置已核对。');
    if(data.cliSwitch?.phase==='pending')this.rows.append(this.node('p','正在切换接入…','account-muted'));
    if(data.cliSwitch?.phase==='failed'){const error=this.node('p',null,'error');error.textContent=data.cliSwitch.error;this.rows.append(error);}
    for(const row of data.accounts||[]){
      const card=this.node('section',null,'cli-access-row'),title=this.node('strong');title.textContent=row.name;
      const detail=this.node('small');detail.textContent=[row.kind==='api'?this.t('API 接入'):this.t('原生登录'),row.baseUrl,row.model].filter(Boolean).join(' · ');
      const actions=this.node('div',null,'cli-access-actions');
      if(row.id===data.activeId){actions.append(this.node('span','当前接入'),this.button('重命名',()=>this.edit(provider,row,true)));}
      else{
        const switchButton=this.button('切换到此接入',()=>this.switchTo(provider,row.id,data));actions.append(switchButton);
        const edit=this.button(row.kind==='api'?'编辑':'重命名',()=>this.edit(provider,row,row.kind!=='api'));actions.append(edit);
        const remove=this.button('删除',()=>this.perform({provider,action:'delete',id:row.id}));actions.append(remove);
      }
      card.append(title,detail,actions);this.rows.append(card);
    }
  }
  edit(provider,row,rename=false){
    this.provider.value=provider;this.editId=row.id;this.editKind=rename?'rename':'api';this.name.input.value=row.name;
    this.baseUrl.input.value=row.baseUrl||'';this.model.input.value=row.model||'';
    this.scheme.value=row.authScheme||'x-api-key';this.key.input.value='';this.cancel.hidden=false;this.render();
    this.name.input.focus();
  }
  switchTo(provider,id,data){
    if(provider==='codex'&&data.sharedWithDesktop){
      const confirm=this.node('div',null,'cli-access-confirm');
      const tasks=this.field('已结束正在运行的任务','checkbox'),restart=this.field('同意在需要时重启 Codex Desktop','checkbox');
      const submit=this.button('确认切换',()=>{
        if(!tasks.input.checked||!restart.input.checked)return;
        confirm.remove();this.perform({provider,action:'switch',id,confirmed:true,tasksConfirmed:true,desktopRestartConfirmed:true});
      });
      const cancel=this.button('取消',()=>confirm.remove());
      confirm.append(tasks.label,restart.label,submit,cancel);this.rows.prepend(confirm);
      return;
    }
    this.perform({provider,action:'switch',id,confirmed:true});
  }
}
