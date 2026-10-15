// Run on the isolated synthetic gateway fixture. Checks the rendered list while a swipe is in progress.
async function runClientSwipeLayoutTests(){
  const checks=[],nav=clientNavigation;
  const check=(condition,message)=>{if(!condition)throw Error(message);checks.push(message);};
  const waitForRows=async()=>{
    for(let i=0;i<100;i++){
      if(nav.swipeRows()?.querySelector('.session'))return;
      await new Promise(resolve=>setTimeout(resolve,20));
    }
    throw Error('Timed out waiting for synthetic sessions');
  };
  for(const provider of ['codex','claude','deepseek']){
    await nav.choose(provider);
    await waitForRows();
    const source=nav.swipeRows();
    const original=source.querySelector('.session');
    check(!!original,provider+' has a rendered session');
    const badgeSelector=provider==='codex'?'.session-source':'.ds-session-type';
    const measure=row=>{
      const title=row.querySelector('strong').getBoundingClientRect();
      const badge=row.querySelector(badgeSelector)?.getBoundingClientRect();
      return {display:getComputedStyle(row).display,height:row.getBoundingClientRect().height,width:row.getBoundingClientRect().width,
        badgeOffset:badge&&badge.top-title.top,badgeRight:badge?.right,rowRight:row.getBoundingClientRect().right};
    };
    const before=measure(original),scroll=source.scrollTop;
    const list=provider==='codex'?document.getElementById('sidebar'):nav.view.list;
    nav.dragListSwipe({provider,list},provider==='codex'?-110:110);
    try{
      const preview=nav.swipePreview,copy=preview.current.querySelector('.session');
      check(!!preview&&preview.root.inert&&preview.root.getAttribute('aria-hidden')==='true',provider+' preview is inert');
      check(preview.root.querySelectorAll('[id]').length===0,provider+' preview has no duplicate IDs');
      check(preview.root.querySelectorAll('[href], [autofocus]').length===0,provider+' preview has no active targets');
      const during=measure(copy);
      check(during.display===before.display,provider+' row keeps its layout while dragging');
      check(Math.abs(during.width-before.width)<1,provider+' row width stays stable while dragging');
      check(Math.abs(during.height-before.height)<1,provider+' row height stays stable while dragging');
      if(before.badgeOffset!==undefined){
        check(Math.abs(during.badgeOffset-before.badgeOffset)<1,provider+' type badge stays beside the title');
        check(Math.abs((during.rowRight-during.badgeRight)-(before.rowRight-before.badgeRight))<1,provider+' type badge stays right aligned');
      }
    }finally{nav.cancelListSwipe();}
    check(!document.querySelector('.client-swipe-preview')&&source.scrollTop===scroll,provider+' canceled preview restores the live list');
  }
  return checks;
}
