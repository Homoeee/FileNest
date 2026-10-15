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
let quickAccess = {common:[], pinned:[]};

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
  const value = entry => {
    const name = explorerName(entry);
    if (field === 'name') return name;
    if (field === 'extension') {
      const dot = name.lastIndexOf('.');
      return entry.kind === 'directory' || dot <= 0 ? '' : name.slice(dot + 1);
    }
    return entry[field];
  };
  const compare = (a,b) => typeof a === 'string'
    ? a.localeCompare(b, 'zh-CN', {numeric:true, sensitivity:'base'}) : a - b;
  rows.sort((a,b) => {
    if (a.entry.kind !== b.entry.kind) return a.entry.kind === 'directory' ? -1 : 1;
    const primary = compare(value(a.entry), value(b.entry));
    const byName = compare(explorerName(a.entry), explorerName(b.entry));
    return (primary || byName || a.index - b.index) * pane.direction;
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
  $(`#sort-${side}`).value = pane.sort;
  const direction = $(`[data-direction="${side}"]`);
  direction.textContent = pane.direction === 1 ? '升序 ↑' : '降序 ↓';
  direction.setAttribute('aria-label', `${side === 'left' ? '左' : '右'}侧切换为${pane.direction === 1 ? '降序' : '升序'}`);
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
  const pinned = [...quickAccess.common, ...quickAccess.pinned].some(place => place.path === pane.path);
  $(`#star-${side}`).textContent = pane.path && pinned ? '★' : '☆';
  $(`#star-${side}`).title = pinned ? '取消固定当前目录' : '固定当前目录到快速访问';
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
    if (button.dataset.quick === 'rename') button.textContent = count === 1 ? '快捷重命名' : '批量重命名';
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

function quickRenameDialog() {
  const items = selectedExplorerItems();
  if (items.length !== 1) throw new Error('快捷重命名只支持一个项目');
  const entry = items[0], oldName = explorerName(entry);
  const dot = entry.kind === 'file' ? oldName.lastIndexOf('.') : -1;
  const extension = dot > 0 ? oldName.slice(dot) : '';
  const stem = extension ? oldName.slice(0, -extension.length) : oldName;
  detail('快捷重命名', `<form id="quickRenameOne"><label for="newExplorerName">新名称</label><input id="newExplorerName" class="confirm-input" value="${explorerEscape(stem)}" required><p>原名称：${explorerEscape(oldName)}${extension ? `；扩展名 ${explorerEscape(extension)} 保持不变` : ''}</p><p id="renameOneError" role="alert"></p><div class="actions"><button class="primary" type="submit">预览重命名</button></div></form>`);
  const input = $('#newExplorerName'); input.focus(); input.select();
  $('#quickRenameOne').onsubmit = guard(async event => {
    event.preventDefault();
    const newName = input.value + extension;
    if (newName === oldName) {$('#renameOneError').textContent = '请输入不同的名称';return;}
    $('#detail').close();
    await prepareQuickOperation('rename', {new_name:newName});
  });
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
  const placeButton = (place, icon, removable=false) => `<div class="place-item"><button type="button" data-place="${explorerEscape(place.path)}" title="${explorerEscape(place.path||'此电脑')}">${icon} ${explorerEscape(place.name)}</button>${removable?`<button type="button" class="unpin" data-quick-unpin="${explorerEscape(place.path)}" aria-label="取消固定 ${explorerEscape(place.name)}">×</button>`:''}</div>`;
  const common=[{name:'此电脑',path:''},...quickAccess.common];
  const recent=explorerRecent.filter(path=>![...quickAccess.common,...quickAccess.pinned].some(place=>place.path===path)).slice(0,4);
  const legacy=explorerFavorites.filter(path=>![...quickAccess.common,...quickAccess.pinned].some(place=>place.path===path));
  $('#explorerPlaces').innerHTML=`<h3>快速访问</h3>${common.map(place=>placeButton(place,'▱')).join('')}${quickAccess.pinned.map(place=>placeButton(place,'★',true)).join('')}<button type="button" class="add-place" data-add-place>＋ 选择文件夹固定</button><h3>最近访问</h3>${recent.length?recent.map(path=>placeButton({name:explorerName({path}),path},'◷')).join(''):'<div class="small">暂无最近访问</div>'}${legacy.length?`<h3>旧收藏（此浏览器）</h3>${legacy.map(path=>`<div class="place-item"><button type="button" data-place="${explorerEscape(path)}" title="${explorerEscape(path)}">☆ ${explorerEscape(explorerName({path}))}</button><button type="button" class="unpin" data-unpin="${explorerEscape(path)}" aria-label="移除旧收藏 ${explorerEscape(path)}">×</button></div>`).join('')}`:''}`;
}

async function updateQuickAccess(action,path){
  quickAccess=await explorerApi('quick-access',{action,path});
  renderExplorerPlaces();
  renderExplorerPane('left');renderExplorerPane('right');
  toast(action==='add'?'已固定到快速访问':'已从快速访问移除');
}

function showExplorerContext(event,side,index) {
  event.preventDefault();
  if (paneHas(side,index)) setActiveExplorer(side);
  else chooseExplorerEntry(side,index);
  const entry = explorerPanes[side].entries[index];
  const menu=$('#fileContextMenu');
  const pinned=quickAccess.pinned.some(place=>place.path===entry.path),common=quickAccess.common.some(place=>place.path===entry.path);
  menu.innerHTML=`<button data-menu="open">${entry.kind === 'directory' ? '打开文件夹' : '打开'}</button>${entry.kind === 'file' ? '<button data-menu="openas">选择其他应用打开…</button>' : `<button data-menu="${pinned?'unpin':'pin'}" ${common?'disabled':''}>${common?'默认快速访问位置':pinned?'从快速访问移除':'固定到快速访问'}</button>`}<button data-menu="move">移动到另一侧</button><button data-menu="copy">复制到另一侧</button><button data-menu="rename">${explorerPanes[side].selected.size === 1 ? '快捷重命名' : '批量重命名'}</button><button data-menu="recycle">移入回收站</button><button data-menu="delete" class="danger-text">永久删除…</button>`;
  menu.hidden=false;
  menu.style.left=`${Math.max(8,Math.min(event.clientX,innerWidth-menu.offsetWidth-8))}px`;
  menu.style.top=`${Math.max(8,Math.min(event.clientY,innerHeight-menu.offsetHeight-8))}px`;
  menu.onclick=guard(async event=>{const action=event.target.dataset.menu;if(!action)return;menu.hidden=true;if(action==='open'||action==='openas')await openExplorerEntry(side,index,action==='openas'?'choose':'default');else if(action==='pin'||action==='unpin')await updateQuickAccess(action==='pin'?'add':'remove',entry.path);else startExplorerAction(action);});
}
function paneHas(side,index){return explorerPanes[side].selected.has(index);}
async function openExplorerEntry(side,index,mode='default'){
  const pane=explorerPanes[side],entry=pane.entries[index];
  if(!entry)throw new Error('文件列表已变化，请刷新后重试');
  setActiveExplorer(side);
  if(entry.kind==='directory'){
    if(mode==='choose')throw new Error('请选择文件');
    await browseExplorer(side,entry.path);
    return;
  }
  await explorerApi('open',{token:pane.token,index,mode});
  $('#explorerStatus').textContent=mode==='choose'?'已打开系统应用选择窗口。':'已交给系统默认应用打开。';
}
function openSelectedEntry(){const pane=explorerPanes[activeExplorerSide];if(pane.selected.size===1)return openExplorerEntry(activeExplorerSide,[...pane.selected][0]);}
function startExplorerAction(action){$('#quickMore').open=false;if(action==='rename')return guard(()=>explorerPanes[activeExplorerSide].selected.size===1?quickRenameDialog():renameOptionsDialog())();if(action==='rules')return rulesQuickDialog();return guard(()=>prepareQuickOperation(action))();}

$$('.file-pane').forEach(paneNode=>paneNode.onmousedown=()=>setActiveExplorer(paneNode.dataset.pane));
for (const side of ['left','right']) {
  $(`#address-${side}`).onsubmit=event=>{event.preventDefault();browseExplorer(side,$(`#path-${side}`).value);};
  $(`#search-${side}`).oninput=()=>{explorerPanes[side].renderLimit=EXPLORER_PAGE_SIZE;renderExplorerPane(side);};$(`#type-${side}`).onchange=()=>{explorerPanes[side].renderLimit=EXPLORER_PAGE_SIZE;renderExplorerPane(side);};
  $(`#all-${side}`).onchange=event=>{const pane=explorerPanes[side];explorerVisible(side).forEach(({index})=>event.target.checked?pane.selected.add(index):pane.selected.delete(index));setActiveExplorer(side);renderExplorerPane(side);};
  $(`#rows-${side}`).onclick=event=>{if(event.target.closest('[data-more]')){explorerPanes[side].renderLimit=(explorerPanes[side].renderLimit||EXPLORER_PAGE_SIZE)+EXPLORER_PAGE_SIZE;renderExplorerPane(side);return;}const check=event.target.closest('[data-entry-check]'),row=event.target.closest('[data-entry]');if(!row)return;const index=Number(row.dataset.entry);chooseExplorerEntry(side,index,{ctrlKey:event.ctrlKey||!!check,shiftKey:event.shiftKey});};
  $(`#rows-${side}`).ondblclick=guard(async event=>{if(event.target.closest('[data-entry-check]'))return;const row=event.target.closest('[data-entry]');if(row)await openExplorerEntry(side,Number(row.dataset.entry));});
  $(`#rows-${side}`).oncontextmenu=event=>{const row=event.target.closest('[data-entry]');if(row)showExplorerContext(event,side,Number(row.dataset.entry));};
  $(`#star-${side}`).onclick=guard(async()=>{const path=explorerPanes[side].path;if(!path)return;if(quickAccess.common.some(place=>place.path===path)){toast('这是默认快速访问位置');return;}await updateQuickAccess(quickAccess.pinned.some(place=>place.path===path)?'remove':'add',path);});
}
$$('[data-nav]').forEach(button=>button.onclick=()=>{const side=button.dataset.side,pane=explorerPanes[side];if(button.dataset.nav==='up')browseExplorer(side,pane.parent);else if(button.dataset.nav==='refresh')browseExplorer(side,pane.path,{history:true});else if(button.dataset.nav==='back'&&pane.historyIndex>0){pane.historyIndex--;browseExplorer(side,pane.history[pane.historyIndex],{history:true});}});
$$('[data-pick-pane]').forEach(button=>button.onclick=guard(()=>openPicker(`path-${button.dataset.pickPane}`)));
$$('[data-sort]').forEach(button=>button.onclick=()=>{const pane=explorerPanes[button.dataset.side];if(pane.sort===button.dataset.sort)pane.direction*=-1;else{pane.sort=button.dataset.sort;pane.direction=1;}renderExplorerPane(button.dataset.side);});
for(const side of ['left','right']){
  $(`#sort-${side}`).onchange=event=>{explorerPanes[side].sort=event.target.value;renderExplorerPane(side);};
  $(`[data-direction="${side}"]`).onclick=()=>{explorerPanes[side].direction*=-1;renderExplorerPane(side);};
}
$$('[data-quick]').forEach(button=>button.onclick=()=>startExplorerAction(button.dataset.quick));
$('#explorerCancel').onclick=()=>explorerApi('cancel',{});$('#explorerUndo').onclick=()=>confirmExplorerUndo($('#explorerUndo').dataset.batch);
$('#explorerPlaces').onclick=guard(async event=>{const place=event.target.closest('[data-place]'),unpin=event.target.closest('[data-unpin]'),quickUnpin=event.target.closest('[data-quick-unpin]');if(place)await browseExplorer(activeExplorerSide,place.dataset.place);if(quickUnpin)await updateQuickAccess('remove',quickUnpin.dataset.quickUnpin);if(unpin){explorerFavorites=explorerFavorites.filter(item=>item!==unpin.dataset.unpin);localStorage.setItem('filenest-favorites',JSON.stringify(explorerFavorites));renderExplorerPlaces();}if(event.target.closest('[data-add-place]'))await openPicker('quick-access');});
$('#explorerPlaces').oncontextmenu=event=>{const place=event.target.closest('[data-place]');if(!place||!place.dataset.place)return;event.preventDefault();const path=place.dataset.place,alreadyPinned=quickAccess.pinned.some(item=>item.path===path),common=quickAccess.common.some(item=>item.path===path);const menu=$('#fileContextMenu');menu.innerHTML=`<button type="button" data-place-action="${alreadyPinned?'remove':'add'}" ${common?'disabled':''}>${common?'默认快速访问位置':alreadyPinned?'从快速访问移除':'固定到快速访问'}</button>`;menu.hidden=false;menu.style.left=`${Math.max(8,Math.min(event.clientX,innerWidth-menu.offsetWidth-8))}px`;menu.style.top=`${Math.max(8,Math.min(event.clientY,innerHeight-menu.offsetHeight-8))}px`;menu.onclick=guard(async e=>{const action=e.target.dataset.placeAction;if(action){menu.hidden=true;await updateQuickAccess(action,path);}});};
window.addEventListener('focus',guard(async()=>{await window.filenestInitialState;quickAccess=await explorerApi('quick-access',{action:'list'});renderExplorerPlaces();renderExplorerPane('left');renderExplorerPane('right');}));
document.addEventListener('click',event=>{if(!event.target.closest('#fileContextMenu'))$('#fileContextMenu').hidden=true;});
document.addEventListener('keydown',event=>{
  if($('#explorer').hidden||['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)||$('#detail').open)return;
  const pane=explorerPanes[activeExplorerSide];
  if(event.ctrlKey&&event.key.toLowerCase()==='a'){event.preventDefault();explorerVisible(activeExplorerSide).forEach(({index})=>pane.selected.add(index));renderExplorerPane(activeExplorerSide);}
  if(event.ctrlKey&&['c','x'].includes(event.key.toLowerCase())&&pane.selected.size){event.preventDefault();explorerClipboard={action:event.key.toLowerCase()==='c'?'copy':'move',token:pane.token,indices:[...pane.selected],source:activeExplorerSide};$('#explorerStatus').textContent=`已${explorerClipboard.action==='copy'?'复制':'剪切'} ${pane.selected.size} 项；切换到目标栏按 Ctrl+V。`;}
  if(event.ctrlKey&&event.key.toLowerCase()==='v'&&explorerClipboard){event.preventDefault();const target=explorerPanes[activeExplorerSide];const oldSide=activeExplorerSide,sourceSide=explorerClipboard.source;activeExplorerSide=sourceSide;const source=explorerPanes[sourceSide],oldToken=source.token,oldSelected=source.selected;source.token=explorerClipboard.token;source.selected=new Set(explorerClipboard.indices);prepareQuickOperation(explorerClipboard.action,{target:target.path}).finally(()=>{source.token=oldToken;source.selected=oldSelected;activeExplorerSide=oldSide;});}
  if(event.key==='F2'&&pane.selected.size){event.preventDefault();startExplorerAction('rename');}
  if(event.key==='Delete'&&pane.selected.size){event.preventDefault();startExplorerAction('recycle');}
  if(event.key==='Enter')guard(()=>openSelectedEntry())();
});

renderExplorerPlaces();
guard(async()=>{const state=await window.filenestInitialState;quickAccess=state.quick_access||{common:[],pinned:[]};renderExplorerPlaces();const left=state.settings.sources?.[0]||'',right=state.settings.target||'';await Promise.all([browseExplorer('left',left),browseExplorer('right',right)]);await updateExplorerUndo(state);})();
