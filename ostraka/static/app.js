'use strict';
const $ = (s, root=document) => root.querySelector(s);
const state = {paper:null, tab:'text', readingMode:'formatted', config:null, selection:null, poll:null, load:0};
const workspace = $('#workspace');
const welcome = workspace.innerHTML;
function el(tag, cls, text){ const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n; }
function button(text, fn, cls=''){const n=el('button',cls,text);n.type='button';n.addEventListener('click',fn);return n;}
function toast(message){const n=$('#toast');n.textContent=message;n.hidden=false;clearTimeout(toast.timer);toast.timer=setTimeout(()=>n.hidden=true,6500);}
async function api(path, options={}){
 const headers={'X-Ostraka':'1',...(options.headers||{})};
 if(options.body && !(options.body instanceof FormData)){headers['Content-Type']='application/json';options.body=JSON.stringify(options.body);}
 const response=await fetch(path,{...options,headers});
 if(!response.ok){let data;try{data=await response.json();}catch{}throw new Error(typeof data?.detail==='string'?data.detail:`Request failed (${response.status}).`);}
 return response.json();
}
function guarded(fn){return async(...args)=>{try{await fn(...args);}catch(e){toast(e.message||'Something went wrong. Please retry.');}};}
function modal(id){$('#'+id).showModal();}
function setupWelcome(){const b=$('#welcome-add');if(b)b.onclick=()=>modal('import-dialog');}
setupWelcome();
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>$('#'+b.dataset.close).close());
$('#add-paper').onclick=()=>modal('import-dialog');
$('#pdf').onchange=()=>{$('#file-label').textContent=$('#pdf').files[0]?.name||'Choose a PDF';if($('#pdf').files.length)$('#arxiv').value='';};
$('#arxiv').oninput=()=>{if($('#arxiv').value){$('#pdf').value='';$('#file-label').textContent='Choose a PDF';}};
$('#import-form').onsubmit=async e=>{
 e.preventDefault();const submit=$('#import-submit');submit.disabled=true;$('#import-error').textContent='';submit.textContent='Adding paper…';
 try{let result;const file=$('#pdf').files[0];const url=$('#arxiv').value.trim();
 if(file){if(file.size>30*1024*1024)throw new Error('Choose a PDF under 30 MB.');const data=new FormData();data.append('file',file);result=await api('/api/papers/upload',{method:'POST',body:data});}
 else if(url)result=await api('/api/papers/arxiv',{method:'POST',body:{url}});
 else throw new Error('Add an arXiv link or choose a PDF.');
 $('#import-dialog').close();$('#import-form').reset();$('#file-label').textContent='Choose a PDF';state.tab='text';await refreshLibrary();await openPaper(result.paper.id);toast(result.duplicate?'This paper is already in your library.':'Paper added. Your reading room is ready.');
 }catch(err){$('#import-error').textContent=err.message;}finally{submit.disabled=false;submit.textContent='Add to reading room ↗';}
};
async function refreshLibrary(){
 const papers=await api('/api/papers');const tiles=$('#tiles');tiles.replaceChildren();$('#paper-count').textContent=`${papers.length} paper${papers.length===1?'':'s'}`;
 for(const p of papers){const tile=button('',guarded(()=>{state.tab='text';return openPaper(p.id);}),'paper-tile'+(state.paper?.id===p.id?' active':''));tile.dataset.id=p.id;tile.append(el('span','tile-title',p.title),el('span','tile-meta',`${p.page_count} PAGES  ·  ${p.guide_complete?'VISUAL GUIDE READY':'READY TO READ'}${p.has_deck?'  ·  DECK':''}`));tiles.append(tile);}
 return papers;
}
async function openPaper(id, preserve=false){
 const load=++state.load;clearTimeout(state.poll);const p=await api('/api/papers/'+id);if(load!==state.load)return;state.paper=p;localStorage.setItem('ostraka:last-paper',id);renderPaper();
 document.querySelectorAll('.paper-tile').forEach(t=>t.classList.toggle('active',t.dataset.id===id));renderNotes();
 const active=p.jobs.find(j=>['queued','running'].includes(j.status));
 if(active)watchJob(active);
 if(!preserve)window.scrollTo({top:0,behavior:'instant'});
}
function renderPaper(){
 const p=state.paper;workspace.replaceChildren();const head=el('section','paper-heading');head.append(el('div','eyebrow','YOUR READING ROOM / PAPER'),el('h1','',p.title));
 const meta=el('div','paper-meta');meta.append(el('span','',`${p.pages.length} PDF pages`),el('span','','Stored on this computer'));
 const pdf=el('a','','Original PDF ↗');pdf.href=`/api/papers/${p.id}/pdf`;pdf.target='_blank';pdf.rel='noopener';meta.append(pdf);
 if(p.source){const source=el('a','','arXiv ↗');source.href=p.source;source.target='_blank';source.rel='noopener';meta.append(source);}head.append(meta);
 const actions=el('div','paper-actions');actions.append(button('Your margins',()=>{$('#notes-panel').hidden=false;renderNotes();}),button(p.guide_complete?'Visual guide ready ✓':'Generate visual guide',guarded(()=>generateGuide())),button('Create presentation ↗',()=>modal('deck-dialog'),'primary'));
 const exportLink=el('a','text-link','Export paper & artifacts');exportLink.href=`/api/papers/${p.id}/export`;actions.append(exportLink);actions.append(button('Remove',guarded(deleteCurrent),'danger'));head.append(actions);
 const tabs=el('nav','tabs');tabs.setAttribute('aria-label','Paper views');[['text','Full paper'],['guide','Visual guide'],['deck','Presentation']].forEach(([id,label])=>{const b=button(label,()=>{state.tab=id;renderPaper();},'tab'+(state.tab===id?' active':''));b.setAttribute('aria-current',state.tab===id?'page':'false');tabs.append(b);});head.append(tabs);workspace.append(head);
 const job=p.jobs?.find(j=>['queued','running'].includes(j.status))||p.jobs?.filter(j=>j.status==='failed').at(-1);if(job)renderJob(job);
 if(state.tab==='text')renderText();else if(state.tab==='guide')renderGuide();else renderDeck();
}
function renderExtractedText(){
 const layout=el('div','content-layout'),nav=el('aside','contents'),article=el('article','paper-text');nav.append(el('h3','','IN THE PAPER'));
 const search=el('input','search-box');search.type='search';search.placeholder='Find in paper…';search.setAttribute('aria-label','Find in paper');nav.append(search);article.append(el('p','reader-hint','Select any passage to highlight it or add a note. PDF text extraction may simplify equations and reading order; refer to the original PDF for exact formatting.'));
 for(const page of state.paper.pages){const card=el('section','page-card');card.id='page-'+page.number;const label=el('div','page-label');label.append(el('span','',`PDF PAGE ${String(page.number).padStart(2,'0')}`));const content=el('div','page-text');content.dataset.page=page.number;paintHighlights(content,page);card.append(label,content);article.append(card);nav.append(button(`Page ${page.number}`,()=>card.scrollIntoView({behavior:'smooth'})));}
 search.oninput=()=>{const q=search.value.toLocaleLowerCase();for(const card of article.querySelectorAll('.page-card'))card.hidden=q&&!state.paper.pages[Number(card.querySelector('.page-text').dataset.page)-1].text.toLocaleLowerCase().includes(q);};layout.append(nav,article);workspace.append(layout);
}
function paintHighlights(target,page){
 const notes=(state.paper.annotations||[]).filter(n=>!n.anchor&&n.page===page.number);const chars=Array.from(page.text);const boundaries=[0,chars.length,...notes.flatMap(n=>[n.start,n.end])].sort((a,b)=>a-b);target.replaceChildren();
 for(let i=0;i<boundaries.length-1;i++){const a=boundaries[i],b=boundaries[i+1];if(a===b)continue;const n=notes.find(n=>n.start<=a&&n.end>=b);const text=chars.slice(a,b).join('');if(n){const mark=el('mark',n.color,text);mark.title=n.note||'Highlight · click to view margins';mark.onclick=()=>{$('#notes-panel').hidden=false;renderNotes();};target.append(mark);}else target.append(document.createTextNode(text));}
 if(!page.text)target.textContent='No extractable text on this page. See the original PDF.';
}
function diagram(d){
 const wrap=el('figure','diagram');wrap.append(el('h3','',d.title));const nodes=el('div','diagram-nodes');d.nodes.forEach((node,index)=>{const card=el('div','diagram-node');const label=d.kind==='compare'?`COMPARE ${index+1}`:d.kind==='cycle'?`${index+1} / ${index===d.nodes.length-1?'↻ BACK TO 1':'↓ NEXT'}`:`${String(index+1).padStart(2,'0')} ${index<d.nodes.length-1?'→':''}`;card.append(el('i','',label),el('b','',node.label));if(node.detail)card.append(el('span','',node.detail));nodes.append(card);});wrap.append(nodes,el('figcaption','diagram-caption',d.caption+' · Explanatory diagram, not an original paper figure.'));return wrap;
}
function pageLinks(pages){const wrap=el('div','source-links');for(const p of [...new Set(pages)])wrap.append(button(`PDF p. ${p}`,()=>{state.tab='text';renderPaper();$('#page-'+p)?.scrollIntoView({behavior:'smooth'});}));return wrap;}
function renderGuide(){
 const chapters=state.paper.guide||[];if(!chapters.length){empty('See the ideas take shape.','Generate a visual companion to follow the paper through its concepts, methods, evidence, and limitations.','Generate visual guide',guarded(generateGuide));return;}
 const layout=el('div','content-layout'),nav=el('aside','contents'),article=el('article');nav.append(el('h3','','READING COMPANION'));article.append(el('p','reader-hint',`Generated by ${state.paper.guide_provider?.provider||'your model'} · ${state.paper.guide_provider?.model||''}. This companion covers consecutive source excerpts. Verify claims, equations and results against the PDF.`));
 chapters.forEach((c,i)=>{const card=el('section','chapter');card.id='chapter-'+i;card.append(el('div','eyebrow',`READING SECTION ${String(i+1).padStart(2,'0')}`),el('h2','',c.title),pageLinks(c.pages),el('p','',c.summary),diagram(c.diagram),el('h3','','What to carry forward'));const list=el('ul');c.takeaways.forEach(t=>list.append(el('li','',t)));card.append(list);if(Object.keys(c.glossary).length){card.append(el('h3','','A few useful terms'));const dl=el('dl','glossary');Object.entries(c.glossary).forEach(([k,v])=>dl.append(el('dt','',k),el('dd','',v)));card.append(dl);}card.append(el('p','caveat',c.caveat));article.append(card);nav.append(button(`${i+1}. ${c.title}`,()=>card.scrollIntoView({behavior:'smooth'})));});layout.append(nav,article);workspace.append(layout);
}
function empty(title,message,action,fn){const wrap=el('div','empty-view');wrap.append(el('div','eyebrow','A LITTLE CONTEXT GOES A LONG WAY'),el('h2','',title),el('p','',message));if(action)wrap.append(button(action,fn,'primary'));workspace.append(wrap);}
function renderDeck(){
 const d=state.paper.deck;if(!d){empty('Bring your team along.','Turn the paper into a paced presentation with diagrams, source references, and speaker notes.','Create presentation ↗',()=>modal('deck-dialog'));return;}
 const wrap=el('div','presentation-preview');const toolbar=el('div','deck-toolbar');const a=el('a','','Download standalone HTML ↗');a.href=`/api/papers/${state.paper.id}/deck.html`;toolbar.append(a,el('span','muted',`${d.slides.length} slides · ${d.minutes} minutes · ${d.audience}`));wrap.append(toolbar,el('p','reader-hint','Open the downloaded HTML to present offline. Use arrow keys or Space to move, N for speaker notes, and F for full screen.'));
 d.slides.forEach((s,i)=>{const card=el('section','slide-preview');card.append(el('div','eyebrow',`SLIDE ${String(i+1).padStart(2,'0')} / ${d.slides.length}`),el('h2','',s.title),el('p','',s.message));const ul=el('ul');s.bullets.forEach(b=>ul.append(el('li','',b)));card.append(ul);if(s.diagram)card.append(diagram(s.diagram));card.append(pageLinks(s.pages));const notes=el('details');notes.append(el('summary','','Speaker notes'),el('p','',s.notes));card.append(notes);wrap.append(card);});workspace.append(wrap);
}
async function generateGuide(){const job=await api(`/api/papers/${state.paper.id}/guide`,{method:'POST'});state.paper.jobs.push(job);renderJob(job);watchJob(job);toast('Visual guide started. You can keep reading.');}
$('#deck-form').onsubmit=guarded(async e=>{e.preventDefault();const job=await api(`/api/papers/${state.paper.id}/deck`,{method:'POST',body:{minutes:Number($('#minutes').value),audience:$('#audience').value}});$('#deck-dialog').close();state.paper.jobs.push(job);renderJob(job);watchJob(job);toast('Presentation started. You can keep reading.');});
function renderJob(job){
 let banner=$('#job-banner');if(!banner){banner=el('div','job-banner');banner.id='job-banner';$('.paper-heading').after(banner);}banner.replaceChildren();banner.classList.toggle('failed',job.status==='failed');const progress=el('progress');progress.max=100;progress.value=job.progress;progress.setAttribute('aria-label','Generation progress');banner.append(progress,el('span','',job.message));if(job.status==='failed')banner.append(button('Dismiss',()=>banner.remove()));if(job.status==='complete')setTimeout(()=>banner.remove(),4000);
}
function watchJob(job){clearTimeout(state.poll);const paperId=state.paper.id;state.poll=setTimeout(guarded(async()=>{if(state.paper?.id!==paperId)return;try{const fresh=await api('/api/jobs/'+job.id);state.paper.jobs=state.paper.jobs.filter(j=>j.id!==fresh.id).concat(fresh);renderJob(fresh);if(['queued','running'].includes(fresh.status)){watchJob(fresh);}else{const y=scrollY;await openPaper(paperId,true);scrollTo(0,y);await refreshLibrary();toast(fresh.status==='complete'?'Your '+(fresh.kind==='deck'?'presentation':'visual guide')+' is ready.':fresh.message);}}catch(e){toast('Progress connection interrupted. Reopening this paper will reconnect.');}}),1800);}
async function deleteCurrent(){if(!confirm('Remove this paper, its notes, and generated artifacts from this computer?'))return;await api('/api/papers/'+state.paper.id,{method:'DELETE'});state.paper=null;clearTimeout(state.poll);$('#notes-panel').hidden=true;localStorage.removeItem('ostraka:last-paper');const papers=await refreshLibrary();if(papers.length)await openPaper(papers[0].id);else{workspace.innerHTML=welcome;setupWelcome();}toast('Paper removed.');}
function captureSelection(){
 const selected=window.getSelection();if(!selected||selected.isCollapsed||!selected.rangeCount){$('#selection-tools').hidden=true;return;}
 const range=selected.getRangeAt(0),parent=range.commonAncestorContainer.nodeType===1?range.commonAncestorContainer:range.commonAncestorContainer.parentElement;const page=parent.closest?.('.page-text,.reader-block');if(!page||!page.contains(range.startContainer)||!page.contains(range.endContainer)){return;}
 const before=range.cloneRange();before.selectNodeContents(page);before.setEnd(range.startContainer,range.startOffset);const start=Array.from(before.toString()).length;const quote=range.toString();if(!quote.trim()||quote.length>15000)return;
 state.selection={page:Number(page.dataset.page||1),start,end:start+Array.from(quote).length,quote,...(page.dataset.anchor?{anchor:page.dataset.anchor}:{})};const box=range.getBoundingClientRect(),tools=$('#selection-tools');tools.hidden=false;tools.style.left=Math.max(10,Math.min(innerWidth-180,box.left))+'px';tools.style.top=Math.max(10,Math.min(innerHeight-50,box.top-48))+'px';
}
document.addEventListener('mouseup',e=>{if(!e.target.closest('#selection-tools,dialog'))setTimeout(captureSelection,20);});document.addEventListener('keyup',e=>{if(e.key==='Shift')captureSelection();});document.addEventListener('scroll',()=>$('#selection-tools').hidden=true,{passive:true});
$('#selection-tools').onmousedown=e=>e.preventDefault();
$('#quick-highlight').onclick=guarded(()=>saveAnnotation('','yellow'));
$('#add-note').onclick=()=>{if(!state.selection)return;$('#note-quote').textContent=state.selection.quote;$('#note-text').value='';$('#selection-tools').hidden=true;modal('note-dialog');$('#note-text').focus();};
$('#note-form').onsubmit=guarded(async e=>{e.preventDefault();await saveAnnotation($('#note-text').value,$('#note-color').value);$('#note-dialog').close();});
async function saveAnnotation(note,color){if(!state.selection||!state.paper)return;const saved=await api(`/api/papers/${state.paper.id}/annotations`,{method:'POST',body:{...state.selection,note,color}});state.paper.annotations.push(saved);$('#selection-tools').hidden=true;window.getSelection()?.removeAllRanges();const page=state.paper.pages[saved.page-1],target=$(`.page-text[data-page="${saved.page}"]`);if(saved.anchor){paintFormattedHighlights();}else if(target)paintHighlights(target,page);renderNotes();toast('Saved to your margins.');}
function renderNotes(){if(!state.paper)return;const root=$('#notes-list');root.replaceChildren();$('#export-notes').href=`/api/papers/${state.paper.id}/notes.md`;const notes=state.paper.annotations||[];if(!notes.length)root.append(el('p','muted','Your notes will appear here.'));
 for(const n of notes){const card=el('div','note-card');card.append(button(n.anchor?'Passage in paper':`PDF page ${n.page}`,()=>{state.tab='text';state.readingMode=n.anchor?'formatted':'text';renderPaper();(n.anchor?$(`.reader-block[data-anchor="${n.anchor}"]`):$('#page-'+n.page))?.scrollIntoView({behavior:'smooth'});}),el('blockquote',n.color,n.quote));const area=el('textarea');area.value=n.note;area.setAttribute('aria-label','Edit note for page '+n.page);area.placeholder='Add a thought…';card.append(area,button('Save note',guarded(async()=>{await api(`/api/papers/${state.paper.id}/annotations/${n.id}`,{method:'PATCH',body:{note:area.value}});n.note=area.value;toast('Note saved.');})),button('Delete',guarded(async()=>{await api(`/api/papers/${state.paper.id}/annotations/${n.id}`,{method:'DELETE'});state.paper.annotations=state.paper.annotations.filter(x=>x.id!==n.id);renderNotes();const target=$(`.page-text[data-page="${n.page}"]`);if(n.anchor)paintFormattedHighlights();else if(target)paintHighlights(target,state.paper.pages[n.page-1]);}),'danger'));root.append(card);}
}
$('#close-notes').onclick=()=>$('#notes-panel').hidden=true;
$('#provider').onclick=()=>{const root=$('#settings-content');root.replaceChildren();const c=state.config;if(c?.configured){root.append(el('p','',`Connected: ${c.provider} / ${c.model}`),el('p','muted',c.provider==='codex'?'Uses your ChatGPT plan’s Codex allowance. No API key or API fallback.':'Generation sends extracted paper text to this provider and uses API credits.'));}else root.append(el('p','',c?.message||'No provider configured.'));root.append(el('pre','','# Use your ChatGPT subscription:\ncodex login\nexport OSTRAKA_PROVIDER=codex\nuv run --no-editable ostraka\n\n# Or use a separately billed provider API key.'));modal('settings-dialog');};
guarded(async()=>{state.config=await api('/api/config');$('#provider').textContent=state.config.configured?`${state.config.provider==='codex'?'ChatGPT plan':state.config.provider} connected`:'Connect a model';$('#provider').classList.toggle('ready',state.config.configured);const papers=await refreshLibrary();const last=localStorage.getItem('ostraka:last-paper');if(papers.length)await openPaper(papers.some(p=>p.id===last)?last:papers[0].id);})();


function renderText(){
 const p=state.paper,bar=el('div','reading-toolbar');
 const modes=[['formatted','Formatted paper'],['pdf','Original PDF'],['text','Text & notes']];
 modes.forEach(([mode,label])=>{if(mode==='formatted'&&!p.reader)return;bar.append(button(label,()=>{state.readingMode=mode;renderPaper();},state.readingMode===mode?'selected':''));});
 if(p.source&&!p.reader)bar.append(button('Restore formatted paper',guarded(async()=>{const b=bar.querySelector('button:last-child');b.disabled=true;b.textContent='Importing formatted paper…';try{await api(`/api/papers/${p.id}/reader`,{method:'POST'});state.readingMode='formatted';await openPaper(p.id,true);}finally{b.disabled=false;}}),'primary'));
 workspace.append(bar);
 if(state.readingMode==='text'){renderExtractedText();return;}
 if(state.readingMode==='pdf'||!p.reader){const wrap=el('div','pdf-reader');wrap.append(el('p','reader-hint','Original layout, figures, and equations. To highlight or add notes to extracted text, choose Text & notes.'));
 const frame=el('iframe');frame.title='Original paper PDF';frame.src=`/api/papers/${p.id}/pdf#view=FitH`;wrap.append(frame);workspace.append(wrap);return;}
 const layout=el('div','formatted-layout'),nav=el('aside','contents'),article=el('article','formatted-article');nav.append(el('h3','','IN THE PAPER'));
 // This HTML is sanitized and resource-restricted on the server, not model output.
 article.innerHTML=p.reader.html;
 article.querySelectorAll('a').forEach(link=>{if(link.getAttribute('href')?.startsWith('#'))link.onclick=e=>{e.preventDefault();article.querySelector(`[id="${CSS.escape(link.getAttribute('href').slice(1))}"]`)?.scrollIntoView({behavior:'smooth'});};else{link.target='_blank';link.rel='noopener noreferrer';}});
 p.reader.contents.forEach(c=>nav.append(button(c.title,()=>article.querySelector(`[id="${CSS.escape(c.id)}"]`)?.scrollIntoView({behavior:'smooth'}))));
 layout.append(nav,article);workspace.append(el('p','formatted-hint','Full paper · original text, figures, tables, and equations, adapted from arXiv HTML. Select a paragraph to highlight or add a note. Refer to the PDF for exact pagination.'),layout);paintFormattedHighlights();
}
function paintFormattedHighlights(){
 if(!state.paper?.reader)return;
 for(const block of document.querySelectorAll('.reader-block')){
  for(const mark of block.querySelectorAll('mark[data-annotation]'))mark.replaceWith(...mark.childNodes);
  block.normalize();const notes=state.paper.annotations.filter(n=>n.anchor===block.dataset.anchor);if(!notes.length)continue;
  const walker=document.createTreeWalker(block,NodeFilter.SHOW_TEXT),nodes=[];let node,offset=0;
  while((node=walker.nextNode())){const length=Array.from(node.textContent).length;nodes.push({node,start:offset,length});offset+=length;}
  for(const item of nodes){const chars=Array.from(item.node.textContent),limits=new Set([0,item.length]);
   for(const note of notes){if(note.end>item.start&&note.start<item.start+item.length){limits.add(Math.max(0,note.start-item.start));limits.add(Math.min(item.length,note.end-item.start));}}
   const bounds=[...limits].sort((a,b)=>a-b),fragment=document.createDocumentFragment();
   for(let i=0;i<bounds.length-1;i++){const a=bounds[i],b=bounds[i+1],note=notes.find(n=>n.start<=item.start+a&&n.end>=item.start+b),text=chars.slice(a,b).join('');
    if(note&&!item.node.parentElement.closest('math')){const mark=el('mark',note.color,text);mark.dataset.annotation=note.id;mark.title=note.note||'Highlight';mark.onclick=()=>{$('#notes-panel').hidden=false;renderNotes();};fragment.append(mark);}else fragment.append(document.createTextNode(text));}
   item.node.replaceWith(fragment);
  }
 }
}
