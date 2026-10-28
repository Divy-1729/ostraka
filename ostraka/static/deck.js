'use strict';
const data=JSON.parse(document.getElementById('deck-data').textContent), deck=data.deck;
let index=Math.max(0,Math.min(deck.slides.length-1,Number((location.hash.match(/slide-(\d+)/)||[])[1]||1)-1));
const $=s=>document.querySelector(s);
function n(tag,cls,text){const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=text;return node;}
function diagram(d){const fig=n('figure','diagram');fig.append(n('h2','',d.title));const nodes=n('div','nodes');d.nodes.forEach((x,i)=>{const card=n('div','node');card.append(n('i','',d.kind==='compare'?`COMPARE ${i+1}`:d.kind==='cycle'?`${i+1} ${i===d.nodes.length-1?'↻':'→'}`:`${i+1} ${i<d.nodes.length-1?'→':''}`),n('b','',x.label),n('span','',x.detail));nodes.append(card);});fig.append(nodes,n('figcaption','',d.caption+' · Explanatory diagram.'));return fig;}
function content(s,i){const section=n('section');section.append(n('div','kicker',`PAPER BRIEFING / ${String(i+1).padStart(2,'0')}`),n('h1','',s.title),n('p','message',s.message));const body=n('div','slide-body'+(s.diagram?'':' no-diagram')),ul=n('ul');s.bullets.forEach(x=>ul.append(n('li','',x)));body.append(ul);if(s.diagram)body.append(diagram(s.diagram));section.append(body,n('div','sources','Source: PDF page'+(s.pages.length>1?'s ':' ')+s.pages.join(', ')+' · Verify generated explanations against the paper.'));return section;}
function render(){const s=deck.slides[index];$('#slide').replaceChildren(content(s,index));$('#counter').textContent=`${index+1} / ${deck.slides.length}`;$('#speaker-notes').textContent=s.notes;$('#previous').disabled=index===0;$('#next').disabled=index===deck.slides.length-1;history.replaceState(null,'','#slide-'+(index+1));document.title=s.title+' · Ostraka';}
function move(delta){index=Math.max(0,Math.min(deck.slides.length-1,index+delta));render();}
$('#previous').onclick=()=>move(-1);$('#next').onclick=()=>move(1);
$('#notes-toggle').onclick=()=>{$('#speaker-notes').hidden=!$('#speaker-notes').hidden;};
$('#fullscreen').onclick=async()=>{try{if(document.fullscreenElement)await document.exitFullscreen();else await document.documentElement.requestFullscreen();}catch{}};
document.addEventListener('keydown',e=>{if(['ArrowRight','PageDown',' '].includes(e.key)){e.preventDefault();move(1);}if(['ArrowLeft','PageUp'].includes(e.key)){e.preventDefault();move(-1);}if(e.key==='Home'){index=0;render();}if(e.key==='End'){index=deck.slides.length-1;render();}if(e.key.toLowerCase()==='n')$('#notes-toggle').click();if(e.key.toLowerCase()==='f')$('#fullscreen').click();});
$('#deck-title').textContent=deck.title;$('#source').textContent=data.source||data.paper_title;$('#duration').textContent=`${deck.minutes} min · ← → to navigate`;
window.addEventListener('beforeprint',()=>{$('#slide').replaceChildren(...deck.slides.map((s,i)=>{const node=content(s,i);node.classList.add('print-slide');return node;}));});window.addEventListener('afterprint',render);render();
