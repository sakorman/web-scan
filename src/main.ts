import './style.css';
import { createIcons, Scan, Plus, ArrowLeft, Camera, Upload, Box, Layers, Activity, ChevronRight, Play, Square, Download, RotateCcw, Check, CircleHelp, Cpu, X, Menu, Aperture, Settings2, Images, FileText, MoreHorizontal, ChevronLeft, Maximize2, FolderOpen } from 'lucide';
import { enqueue, pending, acknowledge } from './queue';
import { mountViewer } from './viewer';
import { ReconstructionDrafts, defaultDraft, matchesJobTarget, type Backend, type ReconstructionDraft } from './session-state';

type Job = { id:string; status:string; stage:string; progress:number; message:string; updated:number; report:Record<string,any>|null; config:Record<string,any> };
type Frame = { id:string; width:number; height:number };
type Session = { id:string; name:string; kind:string; status:string; created:number; frame_count?:number; frames?:Frame[]; jobs?:Job[]; job?:Job };
const icons = { Scan, Plus, ArrowLeft, Camera, Upload, Box, Layers, Activity, ChevronRight, Play, Square, Download, RotateCcw, Check, CircleHelp, Cpu, X, Menu, Aperture, Settings2, Images, FileText, MoreHorizontal, ChevronLeft, Maximize2, FolderOpen };
const app = document.querySelector<HTMLDivElement>('#app')!;
const drafts = new ReconstructionDrafts();
const compactScreen = window.matchMedia('(max-width:760px)');
let sessions:Session[] = [], selected:Session|null = null, job:Job|null = null;
let health:any = null, busy = false, polling = false, menuOpen = false, inspectorOpen = false, page = 0;
let cameraStream:MediaStream|null = null, captureTimer = 0, capturing = false, takingFrame = false;
let sensor:any = null, sensorEnabled = false, navigationRequest = 0;
let viewerCleanup:(()=>void)|null = null;
const esc = (value:unknown) => String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
const icon = (name:string) => `<i data-lucide="${name}" aria-hidden="true"></i>`;
const date = (n:number) => new Date(n*1000).toLocaleDateString('zh-CN',{month:'short',day:'numeric'});
const active = (value:Job|null|undefined) => !!value && ['queued','running'].includes(value.status);
const label = (value:Job|null|undefined) => value ? ({queued:'排队中',running:'重建中',succeeded:'已完成',failed:'需处理',cancelled:'已取消'}[value.status]??value.status) : '待重建';
const thumb = (s:Session,f:Frame) => `/api/sessions/${s.id}/frames/${f.id}`;
const number = (n:unknown) => typeof n==='number' ? n.toLocaleString('zh-CN') : '—';
const badge = (j:Job|null|undefined) => `<span class="badge ${j?.status??'draft'}">${active(j)?'<span class="status-dot"></span>':''}${label(j)}</span>`;
class ApiError extends Error { constructor(message:string, public status:number){super(message);} }

async function api<T=any>(path:string,init:RequestInit={}):Promise<T>{
  const headers = new Headers(init.headers);
  if(!(init.body instanceof FormData)) headers.set('Content-Type','application/json');
  const response = await fetch('/api'+path,{...init,headers});
  const data = await response.json();
  if(!response.ok) throw new ApiError(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail??data),response.status);
  return data;
}
function toast(message:string,error=false){
  document.querySelector('.toast')?.remove();
  const element = document.createElement('div');
  element.className='toast'+(error?' error':'');element.setAttribute('role','status');element.textContent=message;
  document.body.append(element);setTimeout(()=>element.remove(),6000);
}
async function action(fn:()=>Promise<void>){
  if(busy||takingFrame)return;
  closeDialog();navigationRequest++;busy=true;render();
  try{await fn();}catch(error){toast((error as Error).message,true);}
  finally{busy=false;render();}
}
function stopCamera(){capturing=false;clearInterval(captureTimer);cameraStream?.getTracks().forEach(track=>track.stop());cameraStream=null;}
function settingsDraft(){
  if(!selected)return defaultDraft('reference');
  return drafts.get(selected.id,(job?.config as ReconstructionDraft|undefined)??defaultDraft(health?.brush_available&&selected.kind!=='fixture'?'brush':'reference'));
}

function render(){
  const oldStage=app.querySelector<HTMLElement>('.stage');
  const key=selected&&(job?.status==='succeeded'?`model-${job.id}`:cameraStream?`camera-${selected.id}`:'');
  const preserve=!!key&&oldStage?.dataset.key===key;
  if(preserve)oldStage!.remove();else{viewerCleanup?.();viewerCleanup=null;}
  const ready=sessions.filter(s=>s.job?.status==='succeeded').length;
  app.innerHTML=`<div class="workbench ${selected?'project-open':''} ${menuOpen?'rail-open':''} ${inspectorOpen?'inspector-open':''}">
    <header class="topbar">
      <button class="brand" id="brand" aria-label="返回项目列表">${icon('scan')}<span>Web Scan</span></button>
      <button class="icon-button mobile-only" id="menu" aria-label="打开项目列表">${icon('menu')}</button>
      <div class="topbar-title"><span class="title-prefix">${selected?'项目':'本地工作台'}</span>${selected?`<h1>${esc(selected.name)}</h1>${badge(job)}`:'<h1>我的采集</h1>'}</div>
      <div class="topbar-actions"><span class="connection"><span class="status-dot"></span>${health?'本地已连接':'连接中'}</span><button class="icon-button" id="help" aria-label="拍摄指南">${icon('circle-help')}</button>${selected?`<button class="icon-button inspector-toggle" id="inspector-toggle" aria-label="打开重建设置">${icon('settings-2')}<span>重建</span></button>`:''}</div>
    </header>
    <aside class="project-rail" aria-label="项目导航">
      <div class="rail-heading"><button id="library" class="rail-all ${selected?'':'active'}">${icon('layers')}全部项目 <span>${sessions.length}</span></button><button class="icon-button rail-close" id="rail-close" aria-label="关闭项目列表">${icon('x')}</button></div>
      <button class="button primary rail-new" id="new" ${busy?'disabled':''}>${icon('plus')}新建采集</button>
      <div class="rail-label">最近项目 <span>${sessions.length}</span></div>
      <nav class="project-list">${sessions.length?sessions.map(sidebarProject).join(''):'<p class="rail-empty">还没有项目<br>先导入一组照片试试</p>'}</nav>
      <div class="rail-footer"><button class="rail-link" id="samples">${icon('aperture')}示例项目 ${icon('chevron-right')}</button><div class="storage-note">${icon('folder-open')}照片和模型保存在本机</div></div>
    </aside>
    ${selected?projectView():libraryView(ready)}
    <div class="statusbar"><span>${icon('cpu')}${health?.brush_available?'本地 GPU 可用':'本地 CPU'}<span class="status-separator"></span>${selected?(selected.kind==='fixture'?'校准示例':'照片重建'):'Web Scan 0.1'}</span><span>${busy?'正在处理…':capturing?'正在采集':selected?`${selected.frames?.length??0} 张照片`:`${ready} 个模型已完成`}</span></div>
    <button class="rail-scrim" id="rail-scrim" tabindex="-1" aria-hidden="true"></button>
    <button class="inspector-scrim" id="inspector-scrim" tabindex="-1" aria-hidden="true"></button>
  </div><div id="dialog-root"></div><input id="file-input" type="file" accept="image/jpeg,image/png,image/webp" multiple hidden>`;
  const newStage=app.querySelector<HTMLElement>('.stage');
  if(preserve&&newStage)newStage.replaceWith(oldStage!);
  createIcons({icons});bind();bindSettings();syncPanels();
  if(cameraStream&&!preserve){const video=document.querySelector<HTMLVideoElement>('#camera-video');if(video){video.srcObject=cameraStream;void video.play().catch(()=>{});}}
  if(selected&&job?.status==='succeeded'&&!preserve){
    const container=document.querySelector<HTMLElement>('#viewer');
    if(container)void mountViewer(container,`/api/jobs/${job.id}/artifacts/preview.splat`,job.report?.backend==='brush').then(cleanup=>{
      if(container.isConnected){viewerCleanup=cleanup;container.querySelector('.viewer-loading')?.remove();}else cleanup();
    }).catch(error=>{const hint=container.querySelector('.viewer-loading');if(hint)hint.textContent='模型加载失败：'+error.message;});
  }
}
function sidebarProject(s:Session){return `<button class="project-item ${s.id===selected?.id?'active':''}" data-session="${s.id}" title="${esc(s.name)}"><span class="project-icon">${icon(s.kind==='fixture'?'aperture':'box')}</span><span class="project-item-copy"><strong>${esc(s.name)}</strong><small>${s.frame_count??s.frames?.length??0} 张照片 <span>${date(s.created)}</span></small></span><span class="project-status ${s.job?.status??'draft'}" title="${label(s.job)}"></span></button>`;}
function libraryView(ready:number){
  const pages=Math.max(1,Math.ceil(sessions.length/6));page=Math.min(page,pages-1);
  const visible=sessions.slice(page*6,page*6+6);
  return `<main class="library-view"><div class="library-toolbar"><div><h2>项目</h2><p>${sessions.length} 个采集，${ready} 个模型已完成</p></div><button class="button secondary" id="sample-entry">${icon('aperture')}打开示例</button></div>
    ${visible.length?`<div class="project-grid">${visible.map(card).join('')}</div>`:`<div class="library-empty"><div class="empty-mark">${icon('scan')}</div><h2>从一组照片开始</h2><p>导入同一个物体或房间的多视角照片，<br>在这里重建、查看和导出模型。</p><button class="button primary" id="empty-new">${icon('plus')}新建采集</button><button class="text-button" id="empty-sample">先试试示例项目</button></div>`}
    <div class="pagination"><span>照片、模型和任务都保留在本地</span><div><button class="icon-button" id="previous-page" aria-label="上一页" ${page===0?'disabled':''}>${icon('chevron-left')}</button><span>${page+1} / ${pages}</span><button class="icon-button" id="next-page" aria-label="下一页" ${page===pages-1?'disabled':''}>${icon('chevron-right')}</button></div></div>
  </main>`;
}
function card(s:Session){return `<button class="project-card" data-session="${s.id}"><div class="card-image">${s.job?.status==='succeeded'?`<img src="/api/jobs/${s.job.id}/artifacts/thumbnail.jpg" alt="${esc(s.name)}预览">`:`<span class="card-empty-icon">${icon(s.kind==='fixture'?'aperture':'scan')}</span>`}${badge(s.job)}</div><div class="card-caption"><strong>${esc(s.name)}</strong><div><span>${s.frame_count??0} 张照片</span><span>${date(s.created)}</span></div></div></button>`;}
function projectView(){
  const s=selected!,frames=s.frames??[],running=active(job),report=job?.report;
  return `<main class="project-view"><div class="viewport-area"><div class="viewport-heading"><div>${icon(job?.status==='succeeded'?'box':cameraStream?'camera':'scan')}<span>${job?.status==='succeeded'?'模型预览':cameraStream?'拍摄预览':'采集工作区'}</span></div><div><button class="tool-button" id="photos">${icon('images')}<span>照片</span><b>${frames.length}</b></button><button class="icon-button" id="view-help" aria-label="查看操作说明">${icon('circle-help')}</button></div></div>
      ${stageView()}
      <div class="photo-dock"><button class="dock-heading" id="all-photos">${icon('images')}<span>采集照片<strong>${frames.length} 张</strong></span></button><div class="photo-strip">${frames.slice(0,8).map((f,i)=>`<button class="strip-photo" data-photo="${i}" aria-label="查看照片 ${i+1}"><img src="${thumb(s,f)}" alt="采集照片 ${i+1}"><small>${i+1}</small></button>`).join('')||'<span class="dock-empty">照片上传后会显示在这里</span>'}</div><button class="dock-more" id="photo-more">${frames.length>8?`+${frames.length-8}`:icon('maximize-2')}<small>全部照片</small></button></div>
    </div>
    <aside class="inspector" aria-label="重建设置"><div class="inspector-heading"><h2>项目概览</h2><button class="icon-button inspector-close" id="inspector-close" aria-label="关闭重建设置">${icon('x')}</button>${badge(job)}</div>
      <div class="project-metrics"><div><strong>${frames.length}</strong><span>照片</span></div><div><strong>${number(report?.gaussians)}</strong><span>高斯</span></div><div><strong>${report?.seconds?`${Math.round(report.seconds)}s`:'—'}</strong><span>重建耗时</span></div></div>
      <section class="reconstruction-settings"><div class="section-label"><h3>重建</h3><button class="icon-button" id="advanced" aria-label="更多设置">${icon('more-horizontal')}</button></div><label>计算模式<select id="backend" ${busy||running||capturing?'disabled':''}><option value="brush" ${!health?.brush_available||s.kind==='fixture'?'disabled':''}>本地 GPU · 室内质量</option><option value="reference">本地 CPU · 调试</option><option value="cuda" ${!health?.cuda_backend_installed?'disabled':''}>CUDA · gsplat</option></select></label><div class="setting-row"><label>训练步数<select id="steps" ${busy||running||capturing?'disabled':''}></select></label><label>高斯上限<select id="points" ${busy||running||capturing?'disabled':''}></select></label></div><p class="engine-note" id="engine-note"></p></section>
      ${progressView()}
      <div class="inspector-actions">${capturing?`<button class="button danger full" id="stop">${icon('square')}结束拍摄</button>`:`<button class="button primary full" id="reconstruct" ${busy||running||frames.length<3?'disabled':''}>${icon('play')}${running?'重建进行中':job?.status==='succeeded'?'再次重建':'开始重建'}</button>`}${running?`<button class="button secondary full" id="cancel">${icon('square')}取消任务</button>`:''}${s.status==='capturing'&&!capturing?`<div class="action-pair"><button class="button secondary" id="camera" ${busy?'disabled':''}>${icon('camera')}拍摄</button><button class="button secondary" id="upload" ${busy?'disabled':''}>${icon('upload')}导入照片</button></div>`:''}${job?.status==='succeeded'?`<button class="button secondary full" id="downloads">${icon('download')}下载模型</button>`:''}<div class="inspector-links"><button class="text-button" id="photos-secondary">全部照片</button>${job?`<button class="text-button" id="log">任务日志</button>`:`<button class="text-button" id="capture-guide">拍摄指南</button>`}</div></div>
    </aside></main>`;
}
function stageView(){
  const s=selected!;
  if(job?.status==='succeeded')return `<section class="stage model-stage" data-key="model-${job.id}" aria-label="三维模型"><div id="viewer"><div class="viewer-loading">正在加载模型…</div></div></section>`;
  if(cameraStream)return `<section class="stage camera-stage" data-key="camera-${s.id}"><video id="camera-video" autoplay muted playsinline></video><div class="capture-reticle"></div><span class="capture-count">正在拍摄 · 已上传 <b id="capture-count">${s.frames?.length??0}</b> 张</span><button class="button danger capture-stop" id="stage-stop">${icon('square')}结束拍摄</button></section>`;
  const running=active(job),failed=job?.status==='failed';
  return `<section class="stage empty-stage" data-key=""><div class="stage-grid"></div><div class="stage-prompt"><span class="stage-symbol ${running?'working':''}">${icon(failed?'activity':running?'aperture':'scan')}</span><h2>${running?'正在重建':failed?'这次重建没有完成':s.status==='ready'?'照片已准备好':'准备拍摄或导入照片'}</h2><p>${running?'任务在本地运行，可以切换到其他项目。':failed?'打开任务日志查看原因，或调整设置后重试。':s.status==='ready'?`已收到 ${s.frames?.length??0} 张照片，右侧选择参数后开始。`:'同一个目标，多拍几个角度。推荐 20–80 张。'}</p>${running?`<div class="stage-progress"><span style="width:${Math.round(job!.progress*100)}%"></span></div><small>${esc(job!.message)}</small>`:s.status==='capturing'?`<div class="stage-capture-actions"><button class="button primary" id="stage-upload" ${busy?'disabled':''}>${icon('upload')}导入照片</button><button class="button secondary" id="stage-camera" ${busy?'disabled':''}>${icon('camera')}拍摄</button></div><span class="stage-tip">相机需要 HTTPS 或本机 localhost</span>`:`<button class="text-button" id="stage-photos">查看采集照片 ${icon('chevron-right')}</button>`}</div></section>`;
}
function progressView(){
  if(!job)return `<section class="job-summary"><div class="section-label"><h3>准备情况</h3><span>${selected?.frames?.length??0} / 3 张起</span></div><p>照片有重叠、画面清晰，重建会更顺利。</p><div class="compact-steps"><span class="current">采集照片</span><span>重建</span><span>查看</span></div></section>`;
  const steps=['prepare','sfm','train','export'],current=steps.indexOf(job.stage);
  return `<section class="job-summary"><div class="section-label"><h3>任务状态</h3><span>${Math.round(job.progress*100)}%</span></div><div class="progress-track"><span style="width:${job.progress*100}%"></span></div><p class="job-message ${job.status==='failed'?'failure':''}" title="${esc(job.message)}">${esc(job.message)}</p><div class="compact-steps">${['准备','位姿','训练','导出'].map((name,i)=>`<span class="${job!.status==='succeeded'||i<current?'done':i===current?'current':''}">${job!.status==='succeeded'||i<current?icon('check'):''}${name}</span>`).join('')}</div></section>`;
}
function bindSettings(){
  const backend=document.querySelector<HTMLSelectElement>('#backend');
  if(!backend||!selected)return;
  const draft=settingsDraft();backend.value=draft.backend;configureBackend(draft);
  backend.onchange=()=>configureBackend(drafts.switchBackend(selected!.id,backend.value as Backend));
  for(const id of ['steps','points'])document.getElementById(id)!.onchange=saveSettings;
}
function configureBackend(draft:ReconstructionDraft){
  const gpu=draft.backend==='brush';
  const fill=(id:string,values:number[],value:number,suffix='')=>{
    const select=document.getElementById(id) as HTMLSelectElement;
    select.innerHTML=[...new Set([...values,value])].sort((a,b)=>a-b).map(n=>`<option value="${n}">${n.toLocaleString()}${suffix}</option>`).join('');select.value=String(value);
  };
  fill('steps',gpu?[1000,3000,6000]:[60,120,300],draft.steps,' 步');fill('points',gpu?[50000,150000,300000]:[192,384,768],draft.max_points);
  document.getElementById('engine-note')!.textContent=gpu?'GPU 训练，支持动态增点。':'低分辨率参考训练，适合检查流程。';
}
function saveSettings(){
  if(!selected)return;
  drafts.save(selected.id,{backend:(document.getElementById('backend') as HTMLSelectElement).value as Backend,steps:Number((document.getElementById('steps') as HTMLSelectElement).value),max_points:Number((document.getElementById('points') as HTMLSelectElement).value)});
}
function syncPanels(){
  const rail=document.querySelector<HTMLElement>('.project-rail');
  const inspector=document.querySelector<HTMLElement>('.inspector');
  for(const [element,hidden] of [[rail,compactScreen.matches&&!menuOpen],[inspector,compactScreen.matches&&!inspectorOpen]] as const){
    if(!element)continue;element.toggleAttribute('inert',hidden);if(hidden)element.setAttribute('aria-hidden','true');else element.removeAttribute('aria-hidden');
  }
}
function toggleRail(open:boolean){
  menuOpen=open;if(open)inspectorOpen=false;
  const shell=document.querySelector('.workbench');shell?.classList.toggle('rail-open',menuOpen);shell?.classList.toggle('inspector-open',inspectorOpen);syncPanels();
}
function toggleInspector(open:boolean){
  inspectorOpen=open;if(open)menuOpen=false;
  const shell=document.querySelector('.workbench');shell?.classList.toggle('rail-open',menuOpen);shell?.classList.toggle('inspector-open',inspectorOpen);syncPanels();
}
compactScreen.addEventListener('change',()=>{if(!compactScreen.matches){toggleRail(false);toggleInspector(false);}syncPanels();});
function bind(){
  const on=(id:string,fn:()=>void)=>document.getElementById(id)?.addEventListener('click',fn);
  for(const id of ['new','empty-new'])on(id,newDialog);
  on('brand',goLibrary);on('library',goLibrary);on('menu',()=>toggleRail(true));
  for(const id of ['rail-close','rail-scrim'])on(id,()=>toggleRail(false));
  on('inspector-toggle',()=>toggleInspector(true));for(const id of ['inspector-close','inspector-scrim'])on(id,()=>toggleInspector(false));
  for(const id of ['help','view-help','capture-guide'])on(id,showGuide);
  for(const id of ['samples','sample-entry','empty-sample'])on(id,showSamples);
  document.querySelectorAll<HTMLElement>('[data-session]').forEach(el=>el.onclick=()=>void openSession(el.dataset.session!));
  document.querySelectorAll<HTMLElement>('[data-photo]').forEach(el=>el.onclick=()=>showPhoto(Number(el.dataset.photo)));
  on('previous-page',()=>{page--;render();});on('next-page',()=>{page++;render();});
  for(const id of ['photos','all-photos','photo-more','photos-secondary','stage-photos'])on(id,showPhotos);
  for(const id of ['upload','stage-upload'])on(id,pickFiles);
  for(const id of ['camera','stage-camera'])on(id,()=>void startCamera());for(const id of ['stop','stage-stop'])on(id,()=>{stopCamera();void reloadSelected();});
  on('advanced',showAdvanced);on('downloads',showDownloads);on('log',()=>void showLog());
  on('reconstruct',()=>void reconstruct());
  on('cancel',()=>void action(async()=>{job=await api(`/jobs/${job!.id}/cancel`,{method:'POST'});await refreshList();}));
  document.getElementById('file-input')?.addEventListener('change',event=>void uploadFiles([...(event.target as HTMLInputElement).files??[]]));
}
async function refreshList(){sessions=await api('/sessions');}
async function openSession(id:string){
  if(busy||takingFrame)return;
  closeDialog();stopCamera();toggleRail(false);toggleInspector(false);
  const request=++navigationRequest;
  try{const next:Session=await api(`/sessions/${id}`);if(request!==navigationRequest)return;selected=next;job=next.jobs?.[0]??null;render();}
  catch(error){toast((error as Error).message,true);}
}
async function reloadSelected(){
  if(selected){const sid=selected.id;const next:Session=await api(`/sessions/${sid}`);if(selected?.id!==sid)return;selected=next;job=next.jobs?.[0]??null;}
  await refreshList();render();
}
function goLibrary(){
  if(busy||takingFrame){toast('请等待当前照片处理完成');return;}
  closeDialog();stopCamera();navigationRequest++;selected=null;job=null;menuOpen=false;inspectorOpen=false;render();
  void refreshList().then(()=>{if(!selected)render();}).catch(error=>toast(error.message,true));
}
async function reconstruct(){
  if(!selected)return;
  saveSettings();const sid=selected.id,config=settingsDraft();
  await action(async()=>{await flushPending(sid);await api(`/sessions/${sid}/finalize`,{method:'POST'});job=await api('/jobs',{method:'POST',body:JSON.stringify({session_id:sid,...config,resolution:config.backend==='brush'?512:96})});selected=await api(`/sessions/${sid}`);await refreshList();});
}

function closeDialog(){const root=document.getElementById('dialog-root');root?.querySelector('dialog')?.close();root?.replaceChildren();}
function dialog(title:string,content:string,kind=''){
  closeDialog();const root=document.getElementById('dialog-root')!;
  root.innerHTML=`<dialog class="dialog ${kind}" aria-labelledby="dialog-title"><div class="dialog-heading"><h2 id="dialog-title">${esc(title)}</h2><button class="icon-button" id="dialog-close" aria-label="关闭">${icon('x')}</button></div><div class="dialog-body">${content}</div></dialog>`;
  const el=root.querySelector<HTMLDialogElement>('dialog')!;
  root.querySelector('#dialog-close')!.addEventListener('click',closeDialog);
  el.addEventListener('click',event=>{if(event.target===el){const r=el.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)closeDialog();}});
  el.addEventListener('cancel',event=>{event.preventDefault();closeDialog();});
  createIcons({icons});el.showModal();return root;
}
function newDialog(){
  if(busy||takingFrame)return;
  const root=dialog('新建采集',`<form id="new-form"><p class="dialog-intro">给这次拍摄起个名字，之后方便找到。</p><label>项目名称<input id="session-name" maxlength="80" placeholder="例如：书房、窗边的椅子" required autofocus></label><div class="dialog-actions"><button class="button primary" type="submit">创建项目</button></div></form>`,'small-dialog');
  root.querySelector<HTMLInputElement>('#session-name')!.focus();
  root.querySelector<HTMLFormElement>('#new-form')!.onsubmit=event=>{event.preventDefault();const name=(root.querySelector('#session-name') as HTMLInputElement).value.trim();if(!name)return;void action(async()=>{stopCamera();selected=await api('/sessions',{method:'POST',body:JSON.stringify({name})});job=null;menuOpen=false;inspectorOpen=false;await refreshList();});};
}
function showSamples(){
  const root=dialog('示例项目',`<p class="dialog-intro">不急着拍，先用一组准备好的照片熟悉流程。</p><button class="sample-option" id="demo" ${busy?'disabled':''}>${icon('aperture')}<span><strong>校准小场景</strong><small>程序生成的 10 张图像，快速检查 CPU 训练。</small></span>${icon('chevron-right')}</button><button class="sample-option" id="indoor-demo" ${busy||!health?.indoor_sample_available?'disabled':''}>${icon('box')}<span><strong>真实办公室</strong><small>${health?.indoor_sample_available?'ETH3D 的 26 张多视角照片，适合 GPU 重建。':'先按 README 准备 ETH3D office 数据。'}</small></span>${icon('chevron-right')}</button>`,'small-dialog');
  const load=(url:string)=>void action(async()=>{stopCamera();selected=await api(url,{method:'POST'});job=null;menuOpen=false;inspectorOpen=false;await refreshList();});
  root.querySelector('#demo')!.addEventListener('click',()=>load('/fixtures'));root.querySelector('#indoor-demo')!.addEventListener('click',()=>load('/indoor-sample'));
}
function showPhotos(){
  if(!selected)return;const s=selected!,frames=s.frames??[];
  const root=dialog(`采集照片 · ${frames.length} 张`,`<div class="gallery-toolbar"><span>${s.status==='ready'?'照片已冻结，可开始重建':'按拍摄顺序排列'}</span><div>${s.status==='capturing'?'<button class="button secondary" id="gallery-upload">'+icon('upload')+'添加照片</button>':''}<button class="button secondary" id="resume">${icon('rotate-ccw')}恢复上传</button></div></div><div class="photo-gallery">${frames.map((f,i)=>`<button class="gallery-photo" data-gallery-photo="${i}"><img src="${thumb(s,f)}" alt="采集照片 ${i+1}" loading="lazy"><span>${i+1}<small>${f.width} × ${f.height}</small></span></button>`).join('')||'<p class="gallery-empty">还没有照片。关闭后拍摄，或在这里导入。</p>'}</div>`,'gallery-dialog');
  root.querySelector('#gallery-upload')?.addEventListener('click',pickFiles);
  root.querySelector('#resume')!.addEventListener('click',()=>void action(async()=>{await flushPending(s.id);await reloadSelected();toast('待上传照片已处理');}));
  root.querySelectorAll<HTMLElement>('[data-gallery-photo]').forEach(button=>button.onclick=()=>showPhoto(Number(button.dataset.galleryPhoto)));
}
function showPhoto(index:number){
  if(!selected)return;const frames=selected.frames??[];const f=frames[index];if(!f)return;
  const root=dialog(`照片 ${index+1} / ${frames.length}`,`<div class="photo-large"><img src="${thumb(selected,f)}" alt="采集照片 ${index+1}"></div><div class="photo-navigation"><button class="button secondary" id="photo-prev" ${index===0?'disabled':''}>${icon('chevron-left')}上一张</button><button class="text-button" id="photo-grid">全部照片</button><button class="button secondary" id="photo-next" ${index===frames.length-1?'disabled':''}>下一张${icon('chevron-right')}</button></div>`,'photo-dialog');
  root.querySelector('#photo-prev')!.addEventListener('click',()=>showPhoto(index-1));root.querySelector('#photo-next')!.addEventListener('click',()=>showPhoto(index+1));root.querySelector('#photo-grid')!.addEventListener('click',showPhotos);
}
function showAdvanced(){
  const root=dialog('更多设置',`<section class="detail-section"><h3>计算模式</h3><p>本地 GPU 使用 Brush，支持动态增点和高阶颜色。CPU 模式用于小规模调试，画面会更粗。参数只对下一次重建生效。</p></section><section class="detail-section"><h3>运动数据</h3><p>可记录照片附近的 IMU 观测。当前位姿仍由照片求解，传感器数据不会直接参与重建。</p><button class="button secondary" id="sensor" ${sensorEnabled?'disabled':''}>${sensorEnabled?'已开启记录':'启用运动传感器'}</button></section><section class="detail-section"><h3>采集限制</h3><p>最多 200 张照片，每张不超过 16 MB。进入后台会暂停拍摄；暂存的照片可在照片面板里恢复上传。</p></section>`,'small-dialog');
  root.querySelector('#sensor')!.addEventListener('click',()=>void enableSensor());
}
function showDownloads(){
  if(job?.status!=='succeeded')return;
  dialog('下载模型',`<p class="dialog-intro">模型为相对尺度。网页预览使用基础颜色，PLY 保留训练输出的颜色参数。</p><div class="download-list">${[['gaussians.ply','高斯模型 PLY','用于其他三维工具'],['preview.splat','网页预览 splat','轻量查看格式'],['cameras.json','相机参数','相机位置和坐标信息'],['report.json','训练报告','配置、耗时和质量指标']].map(([file,title,description])=>`<a class="download-option" href="/api/jobs/${job!.id}/artifacts/${file}" download>${icon('download')}<span><strong>${title}</strong><small>${description}</small></span></a>`).join('')}</div>`,'small-dialog');
}
async function showLog(){
  if(!job)return;const jid=job.id;
  try{const data=await api(`/jobs/${jid}/log`);if(job?.id!==jid)return;dialog('任务日志',`<pre class="log-text">${esc(data.text||"暂时还没有日志输出。")}</pre>`,'log-dialog');}
  catch(error){toast((error as Error).message,true);}
}
function showGuide(){
  dialog('拍摄与操作',`<div class="guide-content"><section><h3>围绕目标移动</h3><p>相邻照片保留 60–80% 的共同内容，缓慢平移。只站在原地转手机，通常得不到足够的视差。</p></section><section><h3>保持场景稳定</h3><p>优先选择有纹理的静态场景，避开强反光、透明物体和移动人物。高处、低处也补拍几张。</p></section><section><h3>在模型里查看</h3><p>环绕模式：拖动旋转，滚轮或双指缩放。漫游模式：拖动看向，WASD 或方向按钮移动，Q / E 升降。目前没有碰撞检测。</p></section><section><h3>手机连接</h3><p>手机访问需要受信任的 HTTPS；电脑上的 localhost 可以直接使用相机。配置步骤见 README。</p></section></div>`,'help-dialog');
}
function pickFiles(){if(!selected||busy||capturing)return;document.getElementById('file-input')?.click();}
async function flushPending(sid:string){
  for(const frame of await pending(sid)){
    const body=new FormData();body.append('file',frame.blob,'capture.jpg');body.append('metadata',JSON.stringify(frame.metadata));
    try{await api(`/sessions/${sid}/frames`,{method:'POST',body});}
    catch(error){if(error instanceof ApiError&&[404,409,413,422].includes(error.status))await acknowledge(frame.key);throw error;}
    await acknowledge(frame.key);
  }
}
async function uploadFiles(files:File[]){
  if(!files.length||!selected||busy)return;const sid=selected.id;
  await action(async()=>{let count=0;for(const file of files){if(file.size>16*1024*1024)throw new Error(`${file.name} 超过 16 MB`);await enqueue({key:crypto.randomUUID(),session:sid,blob:file,metadata:{source:'import',original_name:file.name}});await flushPending(sid);count++;const note=document.querySelector('.statusbar>span:last-child');if(note)note.textContent=`正在上传 ${count} / ${files.length}`;}selected=await api(`/sessions/${sid}`);job=selected!.jobs?.[0]??null;await refreshList();toast(`已处理 ${files.length} 张照片`);});
  if(selected?.id===sid)await reloadSelected();
}
async function startCamera(){
  if(!selected||busy||capturing)return;busy=true;render();
  try{if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia)throw new Error('相机需要 HTTPS 或本机 localhost，也可以导入照片。');cameraStream=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1600}},audio:false});capturing=true;captureTimer=window.setInterval(()=>void captureFrame(),1200);inspectorOpen=false;}
  catch(error){stopCamera();toast('无法打开相机：'+(error as Error).message,true);}
  finally{busy=false;render();}
}
async function captureFrame(){
  if(takingFrame||!capturing||!selected)return;takingFrame=true;const sid=selected.id;
  try{
    const video=document.querySelector<HTMLVideoElement>('#camera-video');if(!video?.videoWidth)return;
    const canvas=document.createElement('canvas'),scale=Math.min(1,1600/video.videoWidth);canvas.width=Math.round(video.videoWidth*scale);canvas.height=Math.round(video.videoHeight*scale);canvas.getContext('2d')!.drawImage(video,0,0,canvas.width,canvas.height);
    const timestamp=performance.now(),blob=await new Promise<Blob|null>(resolve=>canvas.toBlob(resolve,'image/jpeg',.9));if(!blob)return;
    await enqueue({key:crypto.randomUUID(),session:sid,blob,metadata:{source:'camera',timestamp_ms:timestamp,clock_domain:'performance',sensor:sensorEnabled?sensor:null}});await flushPending(sid);
    const next:Session=await api(`/sessions/${sid}`);if(selected?.id!==sid)return;selected=next;
    const count=document.getElementById('capture-count');if(count)count.textContent=String(next.frames!.length);
    if(next.frames!.length>=200){stopCamera();render();}
  }catch(error){stopCamera();toast((error as Error).message+'；可在照片面板恢复上传。',true);render();}
  finally{takingFrame=false;}
}
async function enableSensor(){
  try{const motion=DeviceMotionEvent as typeof DeviceMotionEvent&{requestPermission?:()=>Promise<string>};if(motion.requestPermission&&await motion.requestPermission()!=='granted')throw new Error('未获得传感器权限');window.addEventListener('devicemotion',event=>{sensor={timestamp_ms:performance.now(),acceleration:event.acceleration?{x:event.acceleration.x,y:event.acceleration.y,z:event.acceleration.z}:null,rotationRate:event.rotationRate?{alpha:event.rotationRate.alpha,beta:event.rotationRate.beta,gamma:event.rotationRate.gamma}:null};});sensorEnabled=true;showAdvanced();toast('已开启运动观测记录');}
  catch(error){toast((error as Error).message,true);}
}
document.addEventListener('visibilitychange',()=>{if(document.hidden&&capturing){stopCamera();render();toast('页面进入后台，拍摄已暂停');}});
setInterval(async()=>{
  if(polling||busy||capturing||document.querySelector('dialog[open]'))return;
  const target=selected&&active(job)?{sessionId:selected.id,jobId:job!.id}:null;
  if(selected&&!target)return;polling=true;
  try{
    if(target){
      const next:Job=await api(`/jobs/${target.jobId}`),current=()=>selected&&job?{sessionId:selected.id,jobId:job.id}:null;
      if(busy||!matchesJobTarget(target,current()))return;
      if(next.updated!==job!.updated){const updated:Session[]=await api('/sessions');if(busy||!matchesJobTarget(target,current())||document.querySelector('dialog[open]'))return;job=next;sessions=updated;render();}
    }else{const updated:Session[]=await api('/sessions');if(!busy&&!selected&&!document.querySelector('dialog[open]')&&JSON.stringify(updated)!==JSON.stringify(sessions)){sessions=updated;render();}}
  }catch{/* Retain the last confirmed state during a connection interruption. */}finally{polling=false;}
},1500);
render();
void Promise.all([api('/health'),refreshList()]).then(([value])=>{health=value;render();}).catch(error=>toast('后端尚未连接：'+error.message,true));
