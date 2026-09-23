'use strict';

// 文件管理页只保存“当前目录列表”的短期标识；实际执行仍由服务端重新校验。
const explorerPanes = {
  left: {path:'', parent:'', token:'', entries:[], selected:new Set(), anchor:null, history:[], historyIndex:-1, sort:'name', direction:1, loading:false},
  right:{path:'', parent:'', token:'', entries:[], selected:new Set(), anchor:null, history:[], historyIndex:-1, sort:'name', direction:1, loading:false},
};
let activeExplorerSide = 'left';
let explorerClipboard = null;
let lastQuickRequest = null;
const EXPLORER_PAGE_SIZE = 200;
let explorerFavorites = JSON.parse(localStorage.getItem('filenest-favorites') || '[]');
let explorerRecent = JSON.parse(localStorage.getItem('filenest-recent') || '[]');

const explorerName = entry => entry.path.split(/[\\/]/).filter(Boolean).pop() || entry.path;
const explorerSize = value => value >= 1073741824 ? `${(value/1073741824).toFixed(1)} GB` :
  value >= 1048576 ? `${(value/1048576).toFixed(1)} MB` : value >= 1024 ? `${(value/1024).toFixed(1)} KB` : `${value} B`;
const explorerEscape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));

function explorerApi(path, data) {
  return api(path, data);
}

function explorerVisible(side) {
  const pane = explorerPanes[side];
  const query = $(`#search-${side}`).value.trim().toLocaleLowerCase('zh-CN');
  const type = $(`#type-${side}`).value;
  const rows = pane.entries.map((entry, index) => ({entry, index})).filter(({entry}) =>
    (!query || explorerName(entry).toLocaleLowerCase('zh-CN').includes(query)) && (type === 'all' || entry.category === type));
  const field = pane.sort;
  rows.sort((a,b) => {
    if (a.entry.kind !== b.entry.kind) return a.entry.kind === 'directory' ? -1 : 1;
    const av = field === 'name' ? explorerName(a.entry).toLocaleLowerCase('zh-CN') : a.entry[field];
    const bv = field === 'name' ? explorerName(b.entry).toLocaleLowerCase('zh-CN') : b.entry[field];
    return (typeof av === 'string' ? av.localeCompare(bv, 'zh-CN', {numeric:true}) : av - bv) * pane.direction;
  });
  return rows;
}

function setActiveExplorer(side) {
  activeExplorerSide = side;
  $$('.file-pane').forEach(node => node.classList.toggle('active-pane', node.dataset.pane === side));
  renderExplorerActions();
}

function renderExplorerPane(side) {
  const pane = explorerPanes[side];
  const visible = explorerVisible(side);
  $(`#path-${side}`).value = pane.path;
  $(`#count-${side}`).textContent = pane.loading ? '读取中…' : `${visible.length} 项${visible.length < pane.entries.length ? ` / 共 ${pane.entries.length} 项` : ''}`;
  $(`#error-${side}`).textContent = pane.error || '';
  const shown = visible.slice(0, pane.renderLimit || EXPLORER_PAGE_SIZE);
  $(`#rows-${side}`).innerHTML = pane.loading ? '<tr><td colspan="4" class="empty">正在读取当前目录…</td></tr>' : shown.map(({entry,index}) => {
    const name = explorerName(entry), folder = entry.kind === 'directory';
    return `<tr data-entry="${index}" class="${pane.selected.has(index)?'selected':''}" title="${explorerEscape(entry.path)}"><td><input type="checkbox" data-entry-check="${index}" aria-label="选择 ${explorerEscape(name)}" ${pane.selected.has(index)?'checked':''}></td><td><div class="entry-name"><span class="entry-icon ${folder?'folder':''}">${folder?'▱':explorerEscape((name.split('.').pop()||'FILE').slice(0,4).toUpperCase())}</span><span>${explorerEscape(name)}</span></div></td><td>${folder?'—':explorerSize(entry.size)}</td><td>${entry.mtime_ns ? new Date(entry.mtime_ns/1e6).toLocaleString('zh-CN',{dateStyle:'short',timeStyle:'short'}) : '—'}</td></tr>`;
  }).join('') + (visible.length > shown.length ? `<tr><td colspan="4" class="more-row"><button type="button" data-more="${side}">继续显示（已显示 ${shown.length} / ${visible.length} 项）</button></td></tr>` : '') || '<tr><td colspan="4" class="empty">当前目录没有符合条件的项目</td></tr>';
  const eligible = visible.map(row => row.index);
  const all = $(`#all-${side}`);
  all.checked = eligible.length > 0 && eligible.every(index => pane.selected.has(index));
  all.indeterminate = eligible.some(index => pane.selected.has(index)) && !all.checked;
  all.disabled = pane.loading || !eligible.length;
  $(`#star-${side}`).textContent = pane.path && explorerFavorites.includes(pane.path) ? '★' : '☆';
  renderExplorerActions();
}

function renderExplorerActions() {
  const source = explorerPanes[activeExplorerSide];
  const targetSide = activeExplorerSide === 'left' ? 'right' : 'left';
  const target = explorerPanes[targetSide];
  const count = source.selected.size;
  $('#explorerSelection').textContent = count ? `${activeExplorerSide === 'left'?'左':'右'}侧已选 ${count} 项` : '未选择文件';
  $$('[data-quick]').forEach(button => {
    button.disabled = !count || source.loading || (['move','copy'].includes(button.dataset.quick) && !target.path);
    const arrow = targetSide === 'left' ? '←' : '→';
    if (button.dataset.quick === 'move') button.textContent = `${arrow} 移动到${targetSide === 'left'?'左':'右'}侧`;
    if (button.dataset.quick === 'copy') button.textContent = `${arrow} 复制到${targetSide === 'left'?'左':'右'}侧`;
  });
}

function recordExplorerPath(path) {
  if (!path) return;
  explorerRecent = [path, ...explorerRecent.filter(item => item !== path)].slice(0, 6);
  localStorage.setItem('filenest-recent', JSON.stringify(explorerRecent));
  renderExplorerPlaces();
}

async function browseExplorer(side, path, options={}) {
  const pane = explorerPanes[side];
  if (pane.loading) return;
  pane.renderLimit = EXPLORER_PAGE_SIZE;
  pane.loading = true; pane.error = ''; pane.selected.clear();
  renderExplorerPane(side);
  try {
    const result = await explorerApi('browse', {path:path ?? pane.path});
    pane.path = result.path; pane.parent = result.parent; pane.token = result.token; pane.entries = result.entries; pane.error = (result.errors||[]).join('\n'); pane.anchor = null;
    if (!options.history && (pane.history[pane.historyIndex] !== result.path)) {
      pane.history = pane.history.slice(0, pane.historyIndex + 1);
      pane.history.push(result.path); pane.historyIndex = pane.history.length - 1;
    }
    recordExplorerPath(result.path);
  } catch (error) {
    pane.error = error.message;
  } finally {
    pane.loading = false;
    renderExplorerPane(side);
  }
}

function chooseExplorerEntry(side, index, event={}) {
  const pane = explorerPanes[side];
  setActiveExplorer(side);
  if (event.shiftKey && pane.anchor !== null) {
    if (!event.ctrlKey) pane.selected.clear();
    const [start,end] = [pane.anchor,index].sort((a,b)=>a-b);
    for (let i=start;i<=end;i++) pane.selected.add(i);
  } else if (event.ctrlKey) {
    pane.selected.has(index) ? pane.selected.delete(index) : pane.selected.add(index);
    pane.anchor = index;
  } else {
    pane.selected.clear(); pane.selected.add(index); pane.anchor = index;
  }
  renderExplorerPane(side);
}

function selectedExplorerItems() {
  const pane = explorerPanes[activeExplorerSide];
  return [...pane.selected].sort((a,b)=>a-b).map(index => pane.entries[index]);
}

function renameOptionsDialog() {
  const items = selectedExplorerItems();
  detail('批量重命名', `<form id="quickRename"><div class="editor"><label>名称前缀<input name="prefix"></label><label>名称后缀<input name="suffix"></label><label>替换文字<input name="replace_from"></label><label>替换为<input name="replace_to"></label><label><input type="checkbox" name="add_date"> 添加修改日期</label><label><input type="checkbox" name="sequence"> 添加三位序号</label></div><h3 style="margin-top:18px">名称预览</h3><div class="quick-review"><table><tbody id="renamePreview"></tbody></table></div><div class="actions"><button class="primary" type="submit">检查并继续</button></div></form>`);
  const form = $('#quickRename');
  const update = () => {
    const data = new FormData(form);
    $('#renamePreview').innerHTML = items.slice(0,100).map((entry,index) => {
      const oldName=explorerName(entry), dot=entry.kind==='file'?oldName.lastIndexOf('.'):-1, stem=dot>0?oldName.slice(0,dot):oldName, ext=dot>0?oldName.slice(dot):'';
      let next=stem;if(data.get('replace_from'))next=next.replaceAll(data.get('replace_from'),data.get('replace_to'));next=data.get('prefix')+next+data.get('suffix');
      if(data.has('add_date'))next+='_'+new Date(entry.mtime_ns/1e6).toISOString().slice(0,10).replaceAll('-','');if(data.has('sequence'))next+=`_${String(index+1).padStart(3,'0')}`;
      return `<tr class="rename-pair"><td>${explorerEscape(oldName)}</td><td>→ ${explorerEscape(next+ext)}</td></tr>`;
    }).join('');
  };
  form.oninput=update; update();
  form.onsubmit = async event => {event.preventDefault();const data=new FormData(form);$('#detail').close();await prepareQuickOperation('rename',{rename:{prefix:data.get('prefix'),suffix:data.get('suffix'),replace_from:data.get('replace_from'),replace_to:data.get('replace_to'),add_date:data.has('add_date'),sequence:data.has('sequence')}});};
}

function rulesQuickDialog() {
  detail('套用现有规则', `<form id="quickRules"><p>按规则管理中的顺序匹配，每个项目采用第一条命中的规则。</p><div class="quick-review">${rules.map(rule=>`<label><input type="checkbox" name="rule" value="${explorerEscape(rule.uid)}" ${selectedRules?.has(rule.uid)?'checked':''}> ${explorerEscape(rule.name)} · ${explorerEscape(labels[rule.action])}</label>`).join('')}</div><div class="actions"><button class="primary" type="submit">生成预览</button></div></form>`);
  $('#quickRules').onsubmit = async event => {event.preventDefault();const ids=new FormData(event.target).getAll('rule');if(!ids.length){toast('请至少选择一条规则');return;}$('#detail').close();await prepareQuickOperation('rules',{rule_ids:ids});};
}

async function waitExplorerJob() {
  let state;
  do {
    await new Promise(resolve => setTimeout(resolve, 250));
    state = await explorerApi('state');
    $('#explorerStatus').textContent = state.job.message;
  } while (state.job.busy);
  if (state.job.error) throw new Error(state.job.error);
  return state;
}

async function prepareQuickOperation(action, extra={}) {
  const pane = explorerPanes[activeExplorerSide];
  const other = explorerPanes[activeExplorerSide === 'left'?'right':'left'];
  if (!pane.selected.size) throw new Error('请先选择文件或文件夹');
  $('#explorerStatus').textContent = '正在生成操作预览…';
  lastQuickRequest = {token:pane.token,indices:[...pane.selected],action,target:other.path,conflict:settings.conflict||'skip',...extra};
  await explorerApi('quick-preview',lastQuickRequest);
  const state = await waitExplorerJob();
  plan = state.plan; planId = state.plan_id;
  showQuickConfirmation(action);
}

function showQuickConfirmation(action) {
  const ready = plan.map((item,index)=>({item,index})).filter(row=>row.item.status==='ready');
  const blocked = plan.filter(item=>item.status!=='ready');
  const deleting = action==='delete';
  const canConflict = ['move','copy','rename'].includes(action);
  detail('核对操作', `<p>${ready.length} 项可以${explorerEscape(labels[action]||'处理')}，${blocked.length} 项不会执行。</p><div class="quick-review"><table><thead><tr><th>项目</th><th>操作结果</th></tr></thead><tbody>${plan.map(item=>`<tr class="${item.status==='ready'?'':'blocked'}"><td>${explorerEscape(item.source)}</td><td>${item.status==='ready'?explorerEscape(item.destination):explorerEscape(item.reason)}${(item.warnings||[]).map(w=>`<br>${explorerEscape(w)}`).join('')}</td></tr>`).join('')}</tbody></table></div>${deleting?'<p class="danger-note">永久删除不进入回收站，无法恢复。</p>':''}<form id="quickExecute"><label><input name="risk" type="checkbox" required> 我已核对项目、目标和风险提示</label>${deleting?'<p>输入「永久删除」确认</p><input class="confirm-input" name="deleteText" autocomplete="off" required>':''}${canConflict?'<div class="quick-confirm-options"><label>同名文件 <select id="quickConflict"><option value="skip">跳过</option><option value="number">自动编号</option></select></label></div>':''}<p id="quickExecuteError" role="alert"></p><div class="actions"><button class="primary" type="submit" ${ready.length?'':'disabled'}>确认执行</button></div></form>`);
  const conflictSelect = $('#quickConflict');
  if (conflictSelect) {
    conflictSelect.value=lastQuickRequest?.conflict||settings.conflict||'skip';
    conflictSelect.onchange=guard(async event=>{
      settings.conflict=event.target.value;
      const request={...lastQuickRequest,conflict:event.target.value};
      $('#detail').close();
      await explorerApi('quick-preview',request);
      const state=await waitExplorerJob();
      plan=state.plan;planId=state.plan_id;lastQuickRequest=request;
      showQuickConfirmation(action);
    });
  }
  $('#quickExecute').onsubmit = async event => {
    event.preventDefault(); const data=new FormData(event.target);
    if(deleting&&data.get('deleteText')!=='永久删除'){$('#quickExecuteError').textContent='请输入完整的“永久删除”';return;}
    const indices=ready.map(row=>row.index);$('#detail').close();
    await explorerApi('execute',{plan_id:planId,indices,risk_ack:data.has('risk'),delete_confirmation:data.get('deleteText')||''});
    const state=await waitExplorerJob();
    await Promise.all([browseExplorer('left',explorerPanes.left.path,{history:true}),browseExplorer('right',explorerPanes.right.path,{history:true})]);
    await renderExplorerResult(state.job.result?.batch);
  };
}

async function renderExplorerResult(batch) {
  if (!batch) return;
  const rows=await explorerApi('operations',{batch});
  const success=rows.filter(row=>row.status==='success').length, failed=rows.length-success;
  $('#explorerResult').hidden=false;
  $('#explorerResult').innerHTML=`<strong>操作完成</strong><span>成功 ${success} · 未完成 ${failed}</span><button data-result-detail>查看结果</button>${rows.some(row=>row.status==='success'&&['move','rename'].includes(row.action))?`<button data-result-undo="${explorerEscape(batch)}">撤销移动</button>`:''}`;
  $('#explorerStatus').textContent=failed?'部分项目未完成，请查看结果。':'操作完成。';
  $('[data-result-detail]').onclick=()=>detail('逐项结果',rows.map(row=>`<div class="setting" style="display:block"><p>${explorerEscape(row.source)} → ${explorerEscape(row.destination)}</p><strong>${explorerEscape(labels[row.status]||row.status)}</strong><p>${explorerEscape(row.reason)}</p></div>`).join(''));
  const undo=$('[data-result-undo]');if(undo)undo.onclick=()=>confirmExplorerUndo(batch);
  await updateExplorerUndo();
}

async function confirmExplorerUndo(batch) {
  const rows=await explorerApi('operations',{batch});
  detail('撤销移动',`<p>将恢复 ${rows.filter(row=>row.status==='success'&&['move','rename'].includes(row.action)).length} 个符合条件的项目。已变化或原位置冲突的项目会停止恢复。</p><div class="actions"><button id="confirmExplorerUndo" class="primary">确认撤销</button></div>`);
  $('#confirmExplorerUndo').onclick=async()=>{$('#detail').close();await explorerApi('undo',{batch});const state=await waitExplorerJob();await Promise.all([browseExplorer('left',explorerPanes.left.path,{history:true}),browseExplorer('right',explorerPanes.right.path,{history:true})]);await renderExplorerResult(state.job.result?.batch||batch);};
}

async function updateExplorerUndo(state) {
  state ??= await explorerApi('state');
  const latest=state.history.find(batch=>batch.undoable>0);
  $('#explorerUndo').disabled=!latest;$('#explorerUndo').dataset.batch=latest?.id||'';
}

function renderExplorerPlaces() {
  const defaults=[{name:'此电脑',path:''},...explorerRecent.slice(0,4).map(path=>({name:explorerName({path}),path}))];
  $('#explorerPlaces').innerHTML=`<h3>快捷位置</h3>${defaults.map(place=>`<div class="place-item"><button data-place="${explorerEscape(place.path)}" title="${explorerEscape(place.path||'此电脑')}">${place.path?'◷':'▱'} ${explorerEscape(place.name)}</button></div>`).join('')}<h3>收藏</h3>${explorerFavorites.length?explorerFavorites.map(path=>`<div class="place-item"><button data-place="${explorerEscape(path)}" title="${explorerEscape(path)}">★ ${explorerEscape(explorerName({path}))}</button><button class="unpin" data-unpin="${explorerEscape(path)}" aria-label="取消收藏 ${explorerEscape(path)}">×</button></div>`).join(''):'<div class="small">点击文件栏上的 ☆ 收藏</div>'}`;
}

function showExplorerContext(event,side,index) {
  event.preventDefault();
  if (paneHas(side,index)) setActiveExplorer(side);
  else chooseExplorerEntry(side,index);
  const menu=$('#fileContextMenu');menu.hidden=false;menu.style.left=`${Math.min(event.clientX,innerWidth-190)}px`;menu.style.top=`${Math.min(event.clientY,innerHeight-245)}px`;
  menu.innerHTML='<button data-menu="open">打开</button><button data-menu="move">移动到另一侧</button><button data-menu="copy">复制到另一侧</button><button data-menu="rename">重命名</button><button data-menu="recycle">移入回收站</button><button data-menu="delete" class="danger-text">永久删除…</button>';
  menu.onclick=event=>{const action=event.target.dataset.menu;menu.hidden=true;if(action==='open')openSelectedFolder();else startExplorerAction(action);};
}
function paneHas(side,index){return explorerPanes[side].selected.has(index);}
function openSelectedFolder(){const pane=explorerPanes[activeExplorerSide],items=selectedExplorerItems();if(items.length===1&&items[0].kind==='directory')browseExplorer(activeExplorerSide,items[0].path);}
function startExplorerAction(action){$('#quickMore').open=false;if(action==='rename')return renameOptionsDialog();if(action==='rules')return rulesQuickDialog();return guard(()=>prepareQuickOperation(action))();}

$$('.file-pane').forEach(paneNode=>paneNode.onmousedown=()=>setActiveExplorer(paneNode.dataset.pane));
for (const side of ['left','right']) {
  $(`#address-${side}`).onsubmit=event=>{event.preventDefault();browseExplorer(side,$(`#path-${side}`).value);};
  $(`#search-${side}`).oninput=()=>{explorerPanes[side].renderLimit=EXPLORER_PAGE_SIZE;renderExplorerPane(side);};$(`#type-${side}`).onchange=()=>{explorerPanes[side].renderLimit=EXPLORER_PAGE_SIZE;renderExplorerPane(side);};
  $(`#all-${side}`).onchange=event=>{const pane=explorerPanes[side];explorerVisible(side).forEach(({index})=>event.target.checked?pane.selected.add(index):pane.selected.delete(index));setActiveExplorer(side);renderExplorerPane(side);};
  $(`#rows-${side}`).onclick=event=>{if(event.target.closest('[data-more]')){explorerPanes[side].renderLimit=(explorerPanes[side].renderLimit||EXPLORER_PAGE_SIZE)+EXPLORER_PAGE_SIZE;renderExplorerPane(side);return;}const check=event.target.closest('[data-entry-check]'),row=event.target.closest('[data-entry]');if(!row)return;const index=Number(row.dataset.entry);chooseExplorerEntry(side,index,{ctrlKey:event.ctrlKey||!!check,shiftKey:event.shiftKey});};
  $(`#rows-${side}`).ondblclick=event=>{const row=event.target.closest('[data-entry]');if(!row)return;const entry=explorerPanes[side].entries[Number(row.dataset.entry)];if(entry.kind==='directory')browseExplorer(side,entry.path);};
  $(`#rows-${side}`).oncontextmenu=event=>{const row=event.target.closest('[data-entry]');if(row)showExplorerContext(event,side,Number(row.dataset.entry));};
  $(`#star-${side}`).onclick=()=>{const path=explorerPanes[side].path;if(!path)return;explorerFavorites=explorerFavorites.includes(path)?explorerFavorites.filter(item=>item!==path):[...explorerFavorites,path];localStorage.setItem('filenest-favorites',JSON.stringify(explorerFavorites));renderExplorerPlaces();renderExplorerPane(side);};
}
$$('[data-nav]').forEach(button=>button.onclick=()=>{const side=button.dataset.side,pane=explorerPanes[side];if(button.dataset.nav==='up')browseExplorer(side,pane.parent);else if(button.dataset.nav==='refresh')browseExplorer(side,pane.path,{history:true});else if(button.dataset.nav==='back'&&pane.historyIndex>0){pane.historyIndex--;browseExplorer(side,pane.history[pane.historyIndex],{history:true});}});
$$('[data-pick-pane]').forEach(button=>button.onclick=guard(()=>openPicker(`path-${button.dataset.pickPane}`)));
$$('[data-sort]').forEach(button=>button.onclick=()=>{const pane=explorerPanes[button.dataset.side];if(pane.sort===button.dataset.sort)pane.direction*=-1;else{pane.sort=button.dataset.sort;pane.direction=1;}renderExplorerPane(button.dataset.side);});
$$('[data-quick]').forEach(button=>button.onclick=()=>startExplorerAction(button.dataset.quick));
$('#explorerCancel').onclick=()=>explorerApi('cancel',{});$('#explorerUndo').onclick=()=>confirmExplorerUndo($('#explorerUndo').dataset.batch);
$('#explorerPlaces').onclick=event=>{const place=event.target.closest('[data-place]'),unpin=event.target.closest('[data-unpin]');if(place)browseExplorer(activeExplorerSide,place.dataset.place);if(unpin){explorerFavorites=explorerFavorites.filter(item=>item!==unpin.dataset.unpin);localStorage.setItem('filenest-favorites',JSON.stringify(explorerFavorites));renderExplorerPlaces();}};
document.addEventListener('click',event=>{if(!event.target.closest('#fileContextMenu'))$('#fileContextMenu').hidden=true;});
document.addEventListener('keydown',event=>{
  if($('#explorer').hidden||['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)||$('#detail').open)return;
  const pane=explorerPanes[activeExplorerSide];
  if(event.ctrlKey&&event.key.toLowerCase()==='a'){event.preventDefault();explorerVisible(activeExplorerSide).forEach(({index})=>pane.selected.add(index));renderExplorerPane(activeExplorerSide);}
  if(event.ctrlKey&&['c','x'].includes(event.key.toLowerCase())&&pane.selected.size){event.preventDefault();explorerClipboard={action:event.key.toLowerCase()==='c'?'copy':'move',token:pane.token,indices:[...pane.selected],source:activeExplorerSide};$('#explorerStatus').textContent=`已${explorerClipboard.action==='copy'?'复制':'剪切'} ${pane.selected.size} 项；切换到目标栏按 Ctrl+V。`;}
  if(event.ctrlKey&&event.key.toLowerCase()==='v'&&explorerClipboard){event.preventDefault();const target=explorerPanes[activeExplorerSide];const oldSide=activeExplorerSide,sourceSide=explorerClipboard.source;activeExplorerSide=sourceSide;const source=explorerPanes[sourceSide],oldToken=source.token,oldSelected=source.selected;source.token=explorerClipboard.token;source.selected=new Set(explorerClipboard.indices);prepareQuickOperation(explorerClipboard.action,{target:target.path}).finally(()=>{source.token=oldToken;source.selected=oldSelected;activeExplorerSide=oldSide;});}
  if(event.key==='F2'&&pane.selected.size){event.preventDefault();renameOptionsDialog();}
  if(event.key==='Delete'&&pane.selected.size){event.preventDefault();startExplorerAction('recycle');}
  if(event.key==='Enter')openSelectedFolder();
});

renderExplorerPlaces();
guard(async()=>{const state=await window.filenestInitialState;const left=state.settings.sources?.[0]||'',right=state.settings.target||'';await Promise.all([browseExplorer('left',left),browseExplorer('right',right)]);await updateExplorerUndo(state);})();
