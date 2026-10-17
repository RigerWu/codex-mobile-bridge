// Run after app.js on an isolated synthetic gateway page. No real session is contacted.
async function runPersistentDraftTests(){
  const checks=[],check=(value,label)=>{if(!value)throw Error(label);checks.push(label);};
  const old={id:currentId,host:currentHost,scope:codexDraftScope,gateway:draftGateway,touched:codexDraftTouched,text:$('message').value,skills:selectedSkills,mode:workModes.selection,state,fetch:window.fetch};
  const gateway='fixture-drafts-'+crypto.randomUUID(),a='11111111-1111-4111-8111-111111111111',b='22222222-2222-4222-8222-222222222222';
  const scope=(session,account='account-a')=>({gateway,provider:'codex',account,host:'local',session});
  try{
    check(typeof $('message').onkeydown==='function'&&typeof $('stop').onclick==='function'&&typeof $('history').onclick==='function','Existing composer keyboard, stop and history handlers remain bound');
    draftGateway=gateway;currentHost='local';currentId=a;codexDraftScope=null;codexDraftTouched=false;$('message').value='';selectedSkills=new Set();
    bindCodexDraft({draftAccountIdentity:'account-a'});
    $('message').value='draft A';$('message').setSelectionRange(3,3);$('message').dispatchEvent(new Event('input',{bubbles:true}));
    check(composerDrafts.load(scope(a)).text==='draft A','DOM input saves draft A immediately');
    currentId=b;codexDraftScope=null;codexDraftTouched=false;$('message').value='';bindCodexDraft({draftAccountIdentity:'account-a'});
    check($('message').value==='','Chat B does not display chat A draft');
    currentId=a;codexDraftScope=null;codexDraftTouched=false;$('message').value='';bindCodexDraft({draftAccountIdentity:'account-a'});
    check($('message').value==='draft A'&&$('message').selectionStart===3,'A restores text and caret from durable storage');
    bindCodexDraft({draftAccountIdentity:'account-b'});
    check($('message').value===''&&composerDrafts.load(scope(a)).text==='draft A','Account switch isolates the prior account draft');
    const reopened=new ComposerDrafts(localStorage);
    check(reopened.load(scope(a)).text==='draft A','A new document can read the stored draft');
    draftGateway=gateway;currentId=a;codexDraftScope=null;codexDraftTouched=false;$('message').value='';bindCodexDraft({draftAccountIdentity:'account-a'});
    state={status:'idle',access:{source:'desktop',canSend:true}};
    let resolveSend,sentPayload=null;
    window.fetch=async(url,options)=>{if(String(url).includes('/api/sessions/'+a+'/send')){sentPayload=JSON.parse(options.body);return new Promise(resolve=>{resolveSend=resolve;});}return old.fetch.call(window,url,options);};
    $('message').value='submitted';$('message').dispatchEvent(new Event('input',{bubbles:true}));
    const sendingPromise=$('composer').onsubmit({preventDefault(){}});
    check(sentPayload?.text==='submitted','DOM submission sends the captured revision');
    $('message').value='edited while sending';$('message').dispatchEvent(new Event('input',{bubbles:true}));
    resolveSend({ok:true,status:200,json:async()=>({status:'accepted'})});await sendingPromise;
    check($('message').value==='edited while sending'&&composerDrafts.load(scope(a)).text==='edited while sending','Delayed acknowledgement preserves the later DOM edit');
    check(composerDrafts.pending(scope(a))===null,'Confirmed send clears its durable pending id');
    draftGateway='';codexDraftScope=null;currentId=b;$('message').value='temporary';$('message').dispatchEvent(new Event('input',{bubbles:true}));
    check(sessionStorage.getItem('draft:local|'+b)==='temporary','Gateway without draft identity keeps temporary composer behavior');
    let sent=null;state={status:'idle',access:{source:'desktop',canSend:true}};
    window.fetch=async(url,options)=>{if(String(url).includes('/api/sessions/'+b+'/send')){sent=JSON.parse(options.body);return {ok:true,status:200,json:async()=>({status:'accepted'})};}return old.fetch.call(window,url,options);};
    await $('composer').onsubmit({preventDefault(){}});
    check(sent?.text==='temporary','Gateway without draft identity can still send');
    return {passed:checks.length,checks};
  }finally{
    for(const key of [scope(a),scope(b),scope(a,'account-b')])composerDrafts.clear(key);
    sessionStorage.removeItem('draft:local|'+b);
    window.fetch=old.fetch;currentId=old.id;currentHost=old.host;codexDraftScope=old.scope;draftGateway=old.gateway;codexDraftTouched=old.touched;$('message').value=old.text;selectedSkills=old.skills;workModes.selection=old.mode;state=old.state;
  }
}
