import './style.css';
import { createIcons, Scan, Plus, ArrowLeft, Camera, Upload, Box, Layers, Activity, ChevronRight, Play, Square, Download, RotateCcw, Check, CircleHelp, Cpu, X, Menu, Aperture, Settings2, Images, FileText, MoreHorizontal, ChevronLeft, Maximize2, FolderOpen, Trash2, Video } from 'lucide';
import { enqueue, pending, acknowledge, type PendingFrame } from './queue';
import { UploadPipeline } from './upload-pipeline';
const mountViewer = async (...args: Parameters<typeof import('./viewer')['mountViewer']>) => (await import('./viewer')).mountViewer(...args);
import { importVideo } from './media';
import { ReconstructionDrafts, defaultDraft, matchesJobTarget, type Backend, type ReconstructionDraft } from './session-state';

type Job = { id:string; status:string; stage:string; progress:number; message:string; updated:number; report:Record<string,any>|null; config:Record<string,any> };
type Frame = { id:string; width:number; height:number };
type Session = { id:string; name:string; kind:string; status:string; created:number; frame_count?:number; frames?:Frame[]; jobs?:Job[]; job?:Job };
type PhotoSample = { id:string; title:string; description:string; name:string; authors:string; images:number; available:boolean; previews:string[]; url:string; archive_url:string; license:string; license_url:string; prepare_command:string };
const icons = { Scan, Plus, ArrowLeft, Camera, Upload, Box, Layers, Activity, ChevronRight, Play, Square, Download, RotateCcw, Check, CircleHelp, Cpu, X, Menu, Aperture, Settings2, Images, FileText, MoreHorizontal, ChevronLeft, Maximize2, FolderOpen, Trash2, Video };
const app = document.querySelector<HTMLDivElement>('#app')!;
const drafts = new ReconstructionDrafts();
const compactScreen = window.matchMedia('(max-width:760px)');
const initialProject=new URLSearchParams(location.search).get('p');
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

function isCaptureDetail(){return !!selected&&job?.status!=='succeeded';}
function minimumPhotos(){return selected?.kind==='fixture'?3:(health?.min_capture_frames??12);}
function render(){
  const oldStage=app.querySelector<HTMLElement>('.stage');
  const key=selected&&(job?.status==='succeeded'?`model-${job.id}`:cameraStream?`camera-${selected.id}`:'');
  const preserve=!!key&&oldStage?.dataset.key===key;
  if(preserve)oldStage!.remove();else{viewerCleanup?.();viewerCleanup=null;}
  const ready=sessions.filter(s=>s.job?.status==='succeeded').length;
  app.innerHTML=`<div class="workbench ${selected?'project-open':''} ${isCaptureDetail()?'capture-layout':''} ${menuOpen?'rail-open':''} ${inspectorOpen?'inspector-open':''}">
    <header class="topbar">
      <button class="brand" id="brand" aria-label="返回项目列表">${icon('scan')}<span>Web Scan</span></button>
      <button class="icon-button mobile-only" id="menu" aria-label="打开项目列表">${icon('menu')}</button>
      <div class="topbar-title"><span class="title-prefix">${selected?'项目':'本地工作台'}</span>${selected?`<h1>${isCaptureDetail()?'工作台':esc(selected.name)}</h1>${isCaptureDetail()?'':badge(job)}`:'<h1>我的采集</h1>'}</div>
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
  </div><div id="dialog-root"></div><input id="file-input" type="file" accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime,video/webm,.mp4,.mov,.webm" multiple hidden>`;
  const newStage=app.querySelector<HTMLElement>('.stage');
  if(preserve&&newStage)newStage.replaceWith(oldStage!);
  createIcons({icons});bind();bindSettings();syncPanels();
  if(cameraStream&&!preserve){const video=document.querySelector<HTMLVideoElement>('#camera-video');if(video){video.srcObject=cameraStream;void video.play().catch(()=>{});}}
  if(selected&&job?.status==='succeeded'&&!preserve){
    const container=document.querySelector<HTMLElement>('#viewer');
    if(container)void mountViewer(container,`/api/jobs/${job.id}/artifacts/gaussians.ply`,job.report?.backend==='brush').then(cleanup=>{
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
function card(s:Session){return `<button class="project-card" data-session="${s.id}"><div class="card-image">${s.job?.status==='succeeded'?`<img src="/api/jobs/${s.job.id}/artifacts/thumbnail.jpg" alt="${esc(s.name)}${s.job?.report?.thumbnail_source==='input_photo'?'采集封面':'重建预览'}">${s.job?.report?.thumbnail_source==='input_photo'?'<span class="cover-label">采集封面</span>':''}`:`<span class="card-empty-icon">${icon(s.kind==='fixture'?'aperture':'scan')}</span>`}${badge(s.job)}</div><div class="card-caption"><strong>${esc(s.name)}</strong><div><span>${s.frame_count??0} 张照片</span><span>${date(s.created)}</span></div></div></button>`;}
function projectView(){
  if(isCaptureDetail())return captureDetail();
  const s=selected!,frames=s.frames??[],running=active(job),report=job?.report;
  return `<main class="project-view"><div class="viewport-area"><div class="viewport-heading"><div>${icon(job?.status==='succeeded'?'box':cameraStream?'camera':'scan')}<span>${job?.status==='succeeded'?'模型预览':cameraStream?'拍摄预览':'采集工作区'}</span></div><div><button class="tool-button" id="photos">${icon('images')}<span>照片</span><b>${frames.length}</b></button>${job?.status==='succeeded'?`<button class="tool-button" id="history">历史结果</button><button class="tool-button download-trigger" id="viewport-downloads">${icon('download')}<span>下载模型</span></button>`:''}<button class="icon-button" id="view-help" aria-label="查看操作说明">${icon('circle-help')}</button></div></div>
      ${stageView()}
      <div class="photo-dock"><button class="dock-heading" id="all-photos">${icon('images')}<span>采集照片<strong>${frames.length} 张</strong></span></button><div class="photo-strip">${frames.slice(0,8).map((f,i)=>`<button class="strip-photo" data-photo="${i}" aria-label="查看照片 ${i+1}"><img src="${thumb(s,f)}" alt="采集照片 ${i+1}"><small>${i+1}</small></button>`).join('')||'<span class="dock-empty">照片上传后会显示在这里</span>'}</div><button class="dock-more" id="photo-more">${frames.length>8?`+${frames.length-8}`:icon('maximize-2')}<small>全部照片</small></button></div>
    </div>
    <aside class="inspector" aria-label="重建设置"><div class="inspector-heading"><h2>项目概览</h2><button class="icon-button inspector-close" id="inspector-close" aria-label="关闭重建设置">${icon('x')}</button>${badge(job)}</div>
      <div class="project-metrics"><div><strong>${frames.length}</strong><span>照片</span></div><div><strong>${number(report?.gaussians)}</strong><span>高斯</span></div><div><strong>${report?.seconds?`${Math.round(report.seconds)}s`:'—'}</strong><span>重建耗时</span></div></div>
      <section class="reconstruction-settings"><div class="section-label"><h3>下次重建</h3><button class="icon-button" id="advanced" aria-label="更多设置">${icon('more-horizontal')}</button></div><label>计算模式<select id="backend" ${busy||running||capturing?'disabled':''}><option value="brush" ${!health?.brush_available||s.kind==='fixture'?'disabled':''}>本地 GPU · 室内质量</option><option value="reference">本地 CPU · 调试</option><option value="cuda" ${!health?.cuda_backend_installed?'disabled':''}>CUDA · gsplat</option></select></label><div class="setting-row"><label>训练步数<select id="steps" ${busy||running||capturing?'disabled':''}></select></label><label>训练分辨率<select id="resolution" ${busy||running||capturing?'disabled':''}></select></label></div><p class="engine-note" id="engine-note"></p></section>
      ${progressView()}${job?.status==='succeeded'?`<button class="text-button quality-report-link" id="quality-report">照片定位 ${number(report?.registered_images)} / ${number(report?.input_images)} · 查看质量报告</button>`:''}
      <div class="inspector-actions">${capturing?`<button class="button danger full" id="stop">${icon('square')}结束拍摄</button>`:`<button class="button primary full" id="reconstruct" ${busy||running||frames.length<3?'disabled':''}>${icon('play')}${running?'重建进行中':job?.status==='succeeded'?'再次重建':'开始重建'}</button>`}${running?`<button class="button secondary full" id="cancel">${icon('square')}取消任务</button>`:''}${s.status==='capturing'&&!capturing?`<div class="action-pair"><button class="button secondary" id="camera" ${busy?'disabled':''}>${icon('camera')}拍摄</button><button class="button secondary" id="upload" ${busy?'disabled':''}>${icon('upload')}导入照片</button></div>`:''}${job?.status==='succeeded'?`<button class="button secondary full" id="downloads">${icon('download')}下载模型</button>`:''}<div class="inspector-links"><button class="text-button" id="photos-secondary">全部照片</button>${job?`<button class="text-button" id="log">任务日志</button>`:`<button class="text-button" id="capture-guide">拍摄指南</button>`}</div></div>
    </aside></main>`;
}
function captureDetail(){
  const s=selected!,frames=s.frames??[],running=active(job),missing=Math.max(0,minimumPhotos()-frames.length);
  return `<main class="capture-detail ${frames.length?'has-photos':''}">
    <div class="capture-title"><button class="back-project" id="capture-back" aria-label="返回项目列表">${icon('arrow-left')}</button><h1>${esc(s.name)}</h1><button class="icon-button" id="capture-settings" aria-label="重建设置">${icon('settings-2')}</button></div>
    <div class="capture-summary"><div class="capture-total"><strong>${frames.length}</strong><span>张</span><small>${missing?`还需 ${missing} 张`:'照片已就绪'}</small></div><div class="capture-main-action">${running?`<span class="capture-task-label">${label(job)} ${Math.round(job!.progress*100)}%</span><button class="button secondary" id="cancel">${icon('square')}取消任务</button>`:`<button class="button primary" id="reconstruct" ${busy||capturing||missing>0?'disabled':''}>${icon('layers')}完成并重建</button>`}</div></div>
    ${cameraStream?`<div class="capture-live">${stageView()}</div>`:running?`<section class="capture-running"><div class="running-heading">${icon('aperture')}正在生成三维场景</div><div class="progress-track"><span style="width:${job!.progress*100}%"></span></div><p>${esc(job!.message)}</p><button class="text-button" id="log">查看任务日志</button></section>`:`<section class="capture-callout"><p>围绕场景缓慢移动，保持相邻画面重叠。房间的天花板和地面也记得补拍。</p><div class="capture-entry-actions"><button class="button primary" id="camera" ${busy||s.status!=='capturing'?'disabled':''}>${icon('video')}相机采集</button><button class="button secondary" id="upload" ${busy||s.status!=='capturing'?'disabled':''}>${icon('images')}导入照片 / 视频</button>${s.status==='ready'?'<button class="text-button" id="capture-settings-ready">调整重建设置</button>':''}</div>${job?.status==='failed'?`<p class="capture-error">${esc(job.message)}</p><button class="text-button" id="log">查看任务日志</button>`:''}<div class="capture-import-progress" role="status">${busy?'正在处理文件，请稍候…':''}</div></section>`}
    ${frames.length&&!cameraStream?`<section class="capture-preview"><div><span>已采集 ${frames.length} 张</span><button class="text-button" id="photos">查看全部照片${icon('chevron-right')}</button></div><div class="capture-preview-grid">${frames.slice(0,6).map((f,i)=>`<button data-photo="${i}" aria-label="查看照片 ${i+1}"><img src="${thumb(s,f)}" alt="采集照片 ${i+1}"><small>${i+1}</small></button>`).join('')}</div></section>`:''}
    <div class="capture-footer"><button class="delete-project" id="delete-project" ${busy||capturing||running?'disabled':''}>${icon('trash-2')}删除项目</button></div>
  </main>`;
}
function showCaptureSettings(){
  dialog('重建设置',`<div class="capture-settings-form"><label>计算模式<select id="backend" ${active(job)||busy?'disabled':''}><option value="brush" ${!health?.brush_available||selected?.kind==='fixture'?'disabled':''}>本地 GPU · 室内质量</option><option value="reference">本地 CPU · 调试</option><option value="cuda" ${!health?.cuda_backend_installed?'disabled':''}>CUDA · gsplat</option></select></label><div class="setting-row"><label>训练步数<select id="steps" ${active(job)||busy?'disabled':''}></select></label><label>训练分辨率<select id="resolution" ${active(job)||busy?'disabled':''}></select></label></div><p class="engine-note" id="engine-note"></p><p class="dialog-intro">调整参数后关闭窗口即可。点“完成并重建”时使用这些设置。</p><button class="text-button" id="quality-options">镜头与点数设置</button></div>`,'small-dialog');
  bindSettings();
}
function confirmDeleteProject(){
  if(!selected||active(job)||busy||capturing)return;const sid=selected.id;
  const root=dialog('删除项目',`<p class="dialog-intro">确定删除“${esc(selected.name)}”吗？项目会从列表隐藏，本地照片和模型保留。</p><div class="dialog-actions"><button class="button secondary" id="keep-project">保留项目</button><button class="button danger" id="confirm-delete">删除项目</button></div>`,'small-dialog');
  root.querySelector('#keep-project')!.addEventListener('click',closeDialog);
  root.querySelector('#confirm-delete')!.addEventListener('click',()=>void action(async()=>{await api(`/sessions/${sid}`,{method:'DELETE'});selected=null;job=null;setProjectUrl(null);await refreshList();}));
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
  document.getElementById('quality-options')?.addEventListener('click',showAdvanced);
  const backend=document.querySelector<HTMLSelectElement>('#backend');
  if(!backend||!selected)return;
  const draft=settingsDraft();backend.value=draft.backend;configureBackend(draft);
  backend.onchange=()=>configureBackend(drafts.switchBackend(selected!.id,backend.value as Backend));
  for(const id of ['steps','resolution'])document.getElementById(id)!.onchange=saveSettings;
}
function configureBackend(draft:ReconstructionDraft){
  const gpu=draft.backend==='brush';
  const fill=(id:string,values:number[],value:number,suffix='')=>{
    const select=document.getElementById(id) as HTMLSelectElement|null;if(!select)return;
    select.innerHTML=[...new Set([...values,value])].sort((a,b)=>a-b).map(n=>`<option value="${n}">${n.toLocaleString()}${suffix}</option>`).join('');select.value=String(value);
  };
  fill('steps',gpu?[3000,6000,10000,30000]:[60,120,300],draft.steps,' 步');fill('resolution',gpu?[512,1024,1600]:[64,96,128],draft.resolution??(gpu?512:96),' px');
  const note=document.getElementById('engine-note');if(note)note.textContent=gpu?`${draft.resolution??512} 像素 · ${draft.camera_mode==='shared'?'同镜头':'自动镜头'} · 上限 ${number(draft.max_points)} 点`:'低分辨率参考训练，适合检查流程。';
}
function saveSettings(){
  if(!selected||!document.getElementById('backend'))return;
  drafts.save(selected.id,{...settingsDraft(),backend:(document.getElementById('backend') as HTMLSelectElement).value as Backend,steps:Number((document.getElementById('steps') as HTMLSelectElement).value),resolution:Number((document.getElementById('resolution') as HTMLSelectElement).value)});
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
  on('brand',()=>goLibrary());on('library',()=>goLibrary());on('capture-back',()=>goLibrary());
  for(const id of ['capture-settings','capture-settings-ready'])on(id,showCaptureSettings);on('delete-project',confirmDeleteProject);on('menu',()=>toggleRail(true));
  for(const id of ['rail-close','rail-scrim'])on(id,()=>toggleRail(false));
  on('inspector-toggle',()=>isCaptureDetail()?showCaptureSettings():toggleInspector(true));for(const id of ['inspector-close','inspector-scrim'])on(id,()=>toggleInspector(false));
  for(const id of ['help','view-help','capture-guide'])on(id,showGuide);
  on('quality-report',showQualityReport);on('history',()=>void showHistory());
  for(const id of ['samples','sample-entry','empty-sample'])on(id,showSamples);
  document.querySelectorAll<HTMLElement>('[data-session]').forEach(el=>el.onclick=()=>void openSession(el.dataset.session!));
  document.querySelectorAll<HTMLElement>('[data-photo]').forEach(el=>el.onclick=()=>showPhoto(Number(el.dataset.photo)));
  on('previous-page',()=>{page--;render();});on('next-page',()=>{page++;render();});
  for(const id of ['photos','all-photos','photo-more','photos-secondary','stage-photos'])on(id,showPhotos);
  for(const id of ['upload','stage-upload'])on(id,pickFiles);
  for(const id of ['camera','stage-camera'])on(id,()=>void startCamera());for(const id of ['stop','stage-stop'])on(id,()=>{stopCamera();void reloadSelected();});
  on('advanced',showAdvanced);for(const id of ['downloads','viewport-downloads'])on(id,()=>void showDownloads());on('log',()=>void showLog());
  on('reconstruct',()=>void reconstruct());
  on('cancel',()=>void action(async()=>{job=await api(`/jobs/${job!.id}/cancel`,{method:'POST'});await refreshList();}));
  document.getElementById('file-input')?.addEventListener('change',event=>void uploadFiles([...(event.target as HTMLInputElement).files??[]]));
}
function setProjectUrl(id:string|null,jid?:string){const next=id?`${location.pathname}?p=${id}${jid?`&j=${jid}`:''}`:location.pathname;if(location.pathname+location.search!==next)history.pushState(null,'',next);}
window.addEventListener('popstate',()=>{const id=new URLSearchParams(location.search).get('p');if(id&&/^[a-f0-9]{32}$/.test(id))void openSession(id,false);else goLibrary(false);});
async function refreshList(){sessions=await api('/sessions');}
async function openSession(id:string,updateUrl=true){
  if(busy||takingFrame)return;
  closeDialog();stopCamera();toggleRail(false);toggleInspector(false);
  const request=++navigationRequest;
  try{const next:Session=await api(`/sessions/${id}`);if(request!==navigationRequest)return;selected=next;const requested=!updateUrl?new URLSearchParams(location.search).get('j'):null;job=next.jobs?.find(j=>j.id===requested)??next.jobs?.[0]??null;if(updateUrl)setProjectUrl(id);render();}
  catch(error){toast((error as Error).message,true);}
}
async function reloadSelected(){
  if(selected){const sid=selected.id;const next:Session=await api(`/sessions/${sid}`);if(selected?.id!==sid)return;selected=next;job=next.jobs?.[0]??null;}
  await refreshList();render();
}
function goLibrary(updateUrl=true){
  if(busy||takingFrame){toast('请等待当前照片处理完成');return;}
  closeDialog();stopCamera();navigationRequest++;selected=null;job=null;menuOpen=false;inspectorOpen=false;if(updateUrl)setProjectUrl(null);render();
  void refreshList().then(()=>{if(!selected)render();}).catch(error=>toast(error.message,true));
}
async function reconstruct(){
  if(!selected)return;
  if(takingFrame){toast('最后一张照片正在上传，请稍候');return;}
  saveSettings();const sid=selected.id,config=settingsDraft();
  await action(async()=>{await flushPending(sid);await api(`/sessions/${sid}/finalize`,{method:'POST'});job=await api('/jobs',{method:'POST',body:JSON.stringify({session_id:sid,...config,resolution:config.resolution??(config.backend==='brush'?512:96)})});selected=await api(`/sessions/${sid}`);await refreshList();});
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
  const root=dialog('新建采集',`<form id="new-form"><p class="dialog-intro">给这次拍摄起个名字，之后方便找到。</p><label>项目名称<input id="session-name" maxlength="80" placeholder="例如：书房、窗边的椅子" value="新场景 ${new Date().toLocaleDateString('zh-CN')}" required autofocus></label><div class="dialog-actions"><button class="button primary" type="submit">创建项目</button></div></form>`,'small-dialog');
  root.querySelector<HTMLInputElement>('#session-name')!.focus();
  root.querySelector<HTMLFormElement>('#new-form')!.onsubmit=event=>{event.preventDefault();const name=(root.querySelector('#session-name') as HTMLInputElement).value.trim();if(!name)return;void action(async()=>{stopCamera();selected=await api('/sessions',{method:'POST',body:JSON.stringify({name})});job=null;menuOpen=false;inspectorOpen=false;setProjectUrl(selected!.id);await refreshList();});};
}
async function showSamples(){
  const root=dialog('公开照片示例','<p class="dialog-intro" role="status">正在检查本地照片…</p>','samples-dialog');
  const el=root.querySelector('dialog')!;
  try{
    const samples=await api<PhotoSample[]>('/samples');
    if(!el.isConnected||!el.open)return;
    el.querySelector('.dialog-body')!.innerHTML=`<p class="dialog-intro">选一处空间，从真实拍摄的多视角照片开始。导入后点击“完成并重建”。</p>
      <div class="sample-grid">${samples.map(sample=>`<article class="dataset-card">
        <div class="dataset-photos">${sample.previews.length?sample.previews.map((url,i)=>`<img src="${esc(url)}" alt="${esc(sample.title)}实拍视角 ${i+1}">`).join(''):`<span>${icon('images')}照片尚未准备</span>`}</div>
        <div class="dataset-content"><div class="dataset-title"><h3>${esc(sample.title)}</h3><span>${sample.images} 张照片</span></div>
          <p>${esc(sample.description)}</p><a class="dataset-source" href="${esc(sample.url)}" target="_blank" rel="noopener noreferrer">${esc(sample.name)} · 查看来源</a>
          <p class="dataset-attribution">${esc(sample.authors)} · <a href="${esc(sample.license_url)}" target="_blank" rel="noopener noreferrer">${esc(sample.license)}</a></p>
          <button class="button primary" data-sample="${esc(sample.id)}" ${busy||!sample.available?'disabled':''}>${icon('images')}${sample.available?'导入这组照片':'请先准备照片'}</button>
          ${sample.available?'':`<details class="sample-setup"><summary>准备方法</summary><p>下载<a href="${esc(sample.archive_url)}">官方照片归档</a>到 /tmp，再在项目目录运行：</p><code>${esc(sample.prepare_command)}</code><button class="text-button" data-refresh-samples>重新检查</button></details>`}
        </div></article>`).join('')}</div>
      <p class="dataset-license">每组照片按各自许可使用；ETH3D 限非商业并要求相同方式共享，TUM 要求署名。照片和生成模型保存在本机。</p>
      <details class="sample-debug"><summary>调试用校准场景</summary><button class="sample-option" id="demo" ${busy?'disabled':''}>${icon('aperture')}<span><strong>校准小场景</strong><small>程序生成的 10 张图像，用于检查 CPU 训练。</small></span>${icon('chevron-right')}</button></details>`;
    const load=(url:string)=>void action(async()=>{stopCamera();selected=await api(url,{method:'POST'});job=null;menuOpen=false;inspectorOpen=false;setProjectUrl(selected!.id);await refreshList();});
    el.querySelectorAll<HTMLElement>('[data-sample]').forEach(button=>button.onclick=()=>load(`/samples/${button.dataset.sample}/import`));
    el.querySelectorAll<HTMLElement>('[data-refresh-samples]').forEach(button=>button.onclick=()=>void showSamples());
    el.querySelector('#demo')!.addEventListener('click',()=>load('/fixtures'));
    createIcons({icons});
  }catch(error){
    if(el.isConnected&&el.open)el.querySelector('.dialog-body')!.innerHTML=`<p class="dialog-intro" role="alert">${esc((error as Error).message)}</p>`;
  }
}
async function showHistory(){
  if(!selected)return;const sid=selected.id;
  try{const value=await api<Session>(`/sessions/${sid}`);if(selected?.id!==sid)return;selected=value;
    const complete=(value.jobs??[]).filter(j=>j.status==='succeeded');
    const root=dialog('历史重建结果',`<p class="dialog-intro">之前的模型都保留在本机。选择一次结果查看，训练参数可以继续单独调整。</p>${complete.map(j=>`<button class="sample-option" data-history-job="${j.id}"><span><strong>${j.config.steps.toLocaleString()} 步 · ${j.config.resolution} 像素 ${j.id===job?.id?'· 当前':''}</strong><small>${j.config.camera_mode==='shared'?'同镜头':'自动镜头'} · ${number(j.report?.registered_images)} 张定位 · ${number(j.report?.gaussians)} 个高斯 · ${new Date(j.updated*1000).toLocaleString('zh-CN')}</small></span></button>`).join('')}`,'small-dialog');
    root.querySelectorAll<HTMLButtonElement>('[data-history-job]').forEach(b=>b.onclick=()=>{job=complete.find(j=>j.id===b.dataset.historyJob)!;closeDialog();setProjectUrl(sid,job.id);render();});
  }catch(error){toast((error as Error).message,true);}
}
function showQualityReport(){
  if(!job?.report)return;const r=job.report,missing=(r.unregistered_images??[]) as string[];
  const original=(name:string)=>{const index=Number(name.slice(0,4));const frame=selected?.frames?.[index];return frame?`照片 ${index+1}`:name;};
  const retry=r.camera_mode_retry??{},auto=retry.auto??{},shared=retry.shared??{};
  const coverage=r.sparse_view_coverage,weakViews=Array.isArray(coverage?.weak_views)?coverage.weak_views:[],shownWeakViews=weakViews.slice(0,6);
  const spacing=r.camera_spacing_coverage,isolatedViews=Array.isArray(spacing?.isolated_views)?spacing.isolated_views:[],shownIsolatedViews=isolatedViews.slice(0,6);
  const directions=r.camera_direction_coverage;
  const retryNote=r.camera_mode_effective==='shared'&&job.config.camera_mode==='auto'?`<section class="detail-section"><h3>相机定位</h3><p>自动内参先定位 ${number(auto.registered_images)} / ${number(auto.input_images)} 张，已在同尺寸照片上重试同镜头内参，最终定位 ${number(shared.registered_images)} / ${number(shared.input_images)} 张。</p></section>`:'';
  const coverageNote=coverage?`<section class="detail-section"><h3>视角覆盖</h3><p>每张已定位照片按参与有效稀疏轨迹的数量检查。中位数 ${number(coverage.median_observations)}，本次相对支撑线为 ${number(coverage.support_floor)}。</p>${weakViews.length?`<p>${shownWeakViews.map((view:any)=>`${esc(original(String(view.image_name)))}（${number(view.observations)}）`).join('、')}${weakViews.length>shownWeakViews.length?`，另有 ${number(weakViews.length-shownWeakViews.length)} 张` : ''} 的支撑偏低。若这些位置在模型中发软或有空洞，建议补拍与相邻画面重叠的过渡视角。</p>`:'<p>没有发现相对其它视角明显偏低的已定位照片。该项只反映稀疏几何支撑，不能证明所有表面已拍全。</p>'}</section>`:'';
  const spacingNote=spacing?(spacing.reason==='coincident_camera_centers'?`<section class="detail-section"><h3>拍摄位置</h3><p>已定位相机的位置几乎重合。仅在原地转动镜头无法提供三维深度所需的视差；请围绕目标做小幅横移后补拍。</p><p>该项只检查相机中心，不衡量遮挡、纹理或每个表面的完整覆盖。</p></section>`:`<section class="detail-section"><h3>拍摄位置</h3><p>按每张已定位相机到最近相机的距离复查位置间隔。中位数 ${number(spacing.median_nearest_distance)}，本次相对提示线为 ${number(spacing.spacing_floor)}（归一化场景尺度）。</p>${isolatedViews.length?`<p>${shownIsolatedViews.map((view:any)=>`${esc(original(String(view.image_name)))}（${number(view.nearest_distance)}）`).join('、')}${isolatedViews.length>shownIsolatedViews.length?`，另有 ${number(isolatedViews.length-shownIsolatedViews.length)} 张` : ''} 与其它已定位相机的最近间隔偏大。请复查这些位置前后的照片是否有重叠，不要只在原地转动相机。</p>`:'<p>没有发现相对其它已定位相机明显孤立的位置。该项不使用照片顺序，也不能证明所有表面已拍全。</p>'}</section>`):'';
  const directionNote=directions?`<section class="detail-section"><h3>拍摄朝向</h3><p>按相机光轴两两夹角复查朝向变化。中位数 ${number(directions.median_pairwise_angle_degrees)}°，90 分位 ${number(directions.p90_pairwise_angle_degrees)}°，最大 ${number(directions.max_pairwise_angle_degrees)}°。</p>${directions.narrow_coverage?`<p>多数相机朝向接近。若目标不是平面，请补拍侧向或反向视角，避免只沿同一方向移动。</p>`:'<p>没有发现大多数相机朝向都接近的情况。该项只反映相机方向分布，不能证明每个表面均已覆盖。</p>'}</section>`:'';
  dialog('重建质量',`<p class="dialog-intro">${number(r.registered_images)} / ${number(r.input_images)} 张照片成功定位。定位成功也不代表所有区域都已拍全。</p>${retryNote}${coverageNote}${spacingNote}${directionNote}<section class="detail-section"><h3>本次训练</h3><p>${number(r.steps)} 步 · ${number(r.resolution??job.config.resolution)} 像素 · ${number(r.gaussians)} 个高斯<br>训练 ${number(r.training_views)} 张，评估 ${number(r.evaluation_views)} 张${r.evaluation_mode==='all'?' · 使用全部定位照片':''}<br>网页读取完整 PLY，保留高阶颜色。</p></section>${missing.length?`<section class="detail-section"><h3>未定位照片</h3><p>${missing.map(n=>esc(original(n))).join('、')}</p><p>补拍这些位置与相邻画面的过渡视角，避免只在原地转动相机。</p></section>`:''}<section class="detail-section"><h3>检查画质</h3><p>在模型里选择“拍摄视角”，点击“对照原照片”检查纹理和结构。再切换漫游检查相邻位置；未拍到的区域可能仍有空洞。</p><p>评估 PSNR ${typeof r.evaluation?.psnr==='number'?r.evaluation.psnr.toFixed(2):'—'} / SSIM ${typeof r.evaluation?.ssim==='number'?r.evaluation.ssim.toFixed(3):'—'}。${r.evaluation_mode==='all'?'本次使用全部定位照片训练，没有留出评估指标。':'评估照片参与过位姿计算；不同分辨率、不同视图集合的指标不直接比较。'}</p></section>`,'small-dialog');
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
  saveSettings();const draft=settingsDraft();
  const root=dialog('更多设置',`${selected?`<section class="detail-section"><h3>重建参数</h3><div class="quality-presets"><button class="button secondary" id="preset-quick">快速预览</button><button class="button secondary" id="preset-detail">精细重建</button></div><label>高斯上限<select id="quality-points">${(draft.backend==='brush'?[50000,150000,300000,500000]:[192,384,768]).map(n=>`<option value="${n}" ${n===draft.max_points?'selected':''}>${number(n)}</option>`).join('')}</select></label><label>照片来自<select id="quality-camera"><option value="auto" ${draft.camera_mode!=='shared'?'selected':''}>混合照片 / 不确定</option><option value="shared" ${draft.camera_mode==='shared'?'selected':''}>同一镜头，拍摄时未变焦</option></select></label><p>同镜头模式会共享相机内参。只有照片来自同一镜头、固定焦距和相同尺寸时才使用；它不适合混合相机或变焦照片。</p><label>训练照片<select id="quality-evaluation"><option value="all" ${draft.evaluation_mode==='all'?'selected':''}>全部定位照片 · 生成最终模型</option><option value="holdout" ${draft.evaluation_mode!=='all'?'selected':''}>留出部分照片 · 做评测对照</option></select></label><p>精细重建通常需要数分钟；未拍到的区域仍需补拍。</p><p id="quality-preset-note">${draft.steps.toLocaleString()} 步 · ${draft.resolution??512} 像素</p></section>`:''}<section class="detail-section"><h3>计算模式</h3><p>本地 GPU 使用 Brush，支持动态增点和高阶颜色。CPU 模式用于小规模调试，画面会更粗。参数只对下一次重建生效。</p></section><section class="detail-section"><h3>运动数据</h3><p>可记录照片附近的 IMU 观测。当前位姿仍由照片求解，传感器数据不会直接参与重建。</p><button class="button secondary" id="sensor" ${sensorEnabled?'disabled':''}>${sensorEnabled?'已开启记录':'启用运动传感器'}</button></section><section class="detail-section"><h3>采集限制</h3><p>最多 200 张照片，每张不超过 16 MB。进入后台会暂停拍摄；暂存的照片可在照片面板里恢复上传。</p></section>`,'small-dialog');
  const saveQuality=()=>{if(!selected)return;drafts.save(selected.id,{...settingsDraft(),max_points:Number((root.querySelector('#quality-points') as HTMLSelectElement).value),camera_mode:(root.querySelector('#quality-camera') as HTMLSelectElement).value as 'auto'|'shared',evaluation_mode:(root.querySelector('#quality-evaluation') as HTMLSelectElement).value as 'all'|'holdout'});configureBackend(settingsDraft());};
  root.querySelector('#quality-points')?.addEventListener('change',saveQuality);root.querySelector('#quality-camera')?.addEventListener('change',saveQuality);root.querySelector('#quality-evaluation')?.addEventListener('change',saveQuality);
  for(const [id,steps,resolution,max_points] of [['preset-quick',3000,512,50000],['preset-detail',6000,1024,150000]] as const)root.querySelector<HTMLButtonElement>('#'+id)?.addEventListener('click',()=>{if(!selected)return;const current=settingsDraft();if(current.backend!=='brush'){toast('精细重建需要选择本地 GPU 模式');return;}drafts.save(selected.id,{...current,steps,resolution,max_points});(root.querySelector('#quality-points') as HTMLSelectElement).value=String(max_points);root.querySelector('#quality-preset-note')!.textContent=`${steps.toLocaleString()} 步 · ${resolution} 像素`;configureBackend(settingsDraft());});
  root.querySelector('#sensor')!.addEventListener('click',()=>void enableSensor());
}
function formatFileSize(bytes:number){
  if(bytes<1024)return `${bytes} B`;
  if(bytes<1024*1024)return `${(bytes/1024).toFixed(1)} KB`;
  return `${(bytes/1024/1024).toFixed(1)} MB`;
}
async function showDownloads(){
  if(job?.status!=='succeeded')return;
  const jid=job.id;
  const root=dialog('下载 3DGS 模型','<p class="dialog-intro">正在读取可下载文件…</p>','small-dialog');
  const pane=root.querySelector('dialog')!;
  try{
    const result:{files:{name:string;bytes:number;filename:string;url:string}[]}=await api(`/jobs/${jid}/artifacts`);
    if(job?.id!==jid||!pane.isConnected)return;
    const labels:Record<string,[string,string]>={
      'gaussians.ply':['高斯模型 PLY','完整训练产物，保留高斯参数'],
      'preview.splat':['网页模型 splat','轻量格式，适合网页查看'],
      'cameras.json':['相机参数','相机位置与坐标信息'],
      'report.json':['训练报告','配置、耗时和质量指标'],
    };
    const files=result.files.filter(file=>labels[file.name]);
    pane.querySelector('.dialog-body')!.innerHTML=`<p class="dialog-intro">优先下载 PLY，其他 3DGS 工具也能使用。文件会带上项目名，方便区分。</p><div class="download-list">${files.map(file=>`<a class="download-option ${file.name==='gaussians.ply'?'recommended':''}" href="${esc(file.url)}" download="${esc(file.filename)}">${icon('download')}<span><strong>${labels[file.name][0]}${file.name==='gaussians.ply'?'<em>推荐</em>':''}</strong><small>${labels[file.name][1]}</small><span class="file-size">${formatFileSize(file.bytes)}</span></span></a>`).join('')||'<p class="download-empty">文件已不在本机，请重新重建。</p>'}</div><p class="download-note">模型为相对尺度。splat 只保留基础颜色，PLY 保留训练输出的颜色参数。</p>`;
    createIcons({icons});
  }catch(error){if(pane.isConnected)pane.querySelector('.dialog-body')!.innerHTML=`<p class="dialog-intro">无法读取下载文件：${esc((error as Error).message)}</p>`;}
}
async function showLog(){
  if(!job)return;const jid=job.id;
  try{const data=await api(`/jobs/${jid}/log`);if(job?.id!==jid)return;dialog('任务日志',`<pre class="log-text">${esc(data.text||"暂时还没有日志输出。")}</pre>`,'log-dialog');}
  catch(error){toast((error as Error).message,true);}
}
function showGuide(){
  dialog('拍摄与操作',`<div class="guide-content"><section><h3>围绕目标移动</h3><p>相邻照片保留 60–80% 的共同内容，缓慢平移。只站在原地转手机，通常得不到足够的视差。</p></section><section><h3>保持场景稳定</h3><p>优先选择有纹理的静态场景，避开强反光、透明物体和移动人物。高处、低处也补拍几张。</p></section><section><h3>在模型里查看</h3><p>环绕模式：拖动旋转，滚轮或双指缩放。漫游模式：拖动看向，WASD 或方向键移动，Q / E 升降。目前没有碰撞检测。</p></section><section><h3>手机连接</h3><p>手机访问需要受信任的 HTTPS；电脑上的 localhost 可以直接使用相机。配置步骤见 README。</p></section></div>`,'help-dialog');
}
function pickFiles(){if(!selected||busy||capturing)return;document.getElementById('file-input')?.click();}
async function uploadFrame(frame:PendingFrame){
  const body=new FormData();body.append('file',frame.blob,'capture.jpg');body.append('metadata',JSON.stringify(frame.metadata));
  try{await api(`/sessions/${frame.session}/frames`,{method:'POST',body});}
  catch(error){if(error instanceof ApiError&&[404,409,413,422].includes(error.status))await acknowledge(frame.key);throw error;}
  await acknowledge(frame.key);
}
async function flushPending(sid:string){
  const queue=new UploadPipeline(uploadFrame,3,3);
  try{for(const frame of await pending(sid))await queue.submit(frame);}
  finally{await queue.finish();}
}
async function uploadFiles(files:File[]){
  if(!files.length||!selected||busy)return;const sid=selected.id;
  await action(async()=>{
    await flushPending(sid);
    let prepared=0,uploaded=0;
    const uploadBatch=crypto.randomUUID().replaceAll('-','');
    const reportProgress=()=>{
      const text=`已上传 ${uploaded} 张，待上传 ${Math.max(0,prepared-uploaded)} 张`;
      const note=document.querySelector('.capture-import-progress');if(note)note.textContent=text;
      const status=document.querySelector('.statusbar>span:last-child');if(status)status.textContent=text;
    };
    const queue=new UploadPipeline<PendingFrame>(async frame=>{await uploadFrame(frame);uploaded++;reportProgress();},3,3);
    const send=async(blob:Blob,metadata:Record<string,unknown>)=>{
      const frame={key:crypto.randomUUID(),session:sid,blob,metadata:{...metadata,upload_batch:uploadBatch,capture_index:prepared}};
      // Persist before dispatch, so an interrupted request can be resumed.
      await enqueue(frame);prepared++;reportProgress();await queue.submit(frame);
    };
    let budget=200-(selected?.frames?.length??0);
    try{
      for(const file of files){
        if(budget<1)throw new Error('照片数量已达到上限');
        const isVideo=file.type.startsWith('video/')||/\.(mp4|mov|webm)$/i.test(file.name);
        if(isVideo){
          const count=await importVideo(file,budget,async(frame,time)=>{
            await send(frame,{source:'video',original_name:file.name,video_time_seconds:time});
          });budget-=count;
        }else{
          if(file.size>16*1024*1024)throw new Error(`${file.name} 超过 16 MB`);
          await send(file,{source:'import',original_name:file.name});budget--;
        }
      }
    }finally{await queue.finish();}
    selected=await api(`/sessions/${sid}`);job=selected!.jobs?.[0]??null;await refreshList();toast(`导入完成，现有 ${selected!.frames?.length??0} 张照片`);
  });
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
    if(next.frames!.length>=200)stopCamera();render();
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
void Promise.all([api('/health'),refreshList()]).then(([value])=>{health=value;if(initialProject&&/^[a-f0-9]{32}$/.test(initialProject))void openSession(initialProject,false);else render();}).catch(error=>toast('后端尚未连接：'+error.message,true));
