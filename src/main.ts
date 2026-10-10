import './style.css';
import { createIcons, Scan, Plus, ArrowUpRight, ArrowLeft, Camera, Upload, Box, Layers, Activity, ChevronRight, Play, Square, Download, RotateCcw, Check, CircleHelp, Radio, FolderOpen, Cpu, X, Menu, Aperture } from 'lucide';
import { enqueue, pending, acknowledge } from './queue';
import { mountViewer } from './viewer';
import { ReconstructionDrafts, defaultDraft, matchesJobTarget, type Backend, type ReconstructionDraft } from './session-state';

type Job = {id:string;status:string;stage:string;progress:number;message:string;updated:number;report:Record<string,any>|null;config:Record<string,any>};
type Frame = {id:string;width:number;height:number};
type Session = {id:string;name:string;kind:string;status:string;created:number;frame_count?:number;frames?:Frame[];jobs?:Job[];job?:Job};
const icons={Scan,Plus,ArrowUpRight,ArrowLeft,Camera,Upload,Box,Layers,Activity,ChevronRight,Play,Square,Download,RotateCcw,Check,CircleHelp,Radio,FolderOpen,Cpu,X,Menu,Aperture};
const app=document.querySelector<HTMLDivElement>('#app')!;
let sessions:Session[]=[], selected:Session|null=null, job:Job|null=null;
let screen='library', busy=false, cameraStream:MediaStream|null=null, captureTimer=0, capturing=false;
let viewerCleanup:(()=>void)|null=null, health:any=null, uploadCount=0, sensor:any=null, sensorEnabled=false;
let polling=false, menuOpen=false, navigationRequest=0;
const drafts=new ReconstructionDrafts();
const esc=(x:unknown)=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]!));
const icon=(name:string,cls='')=>`<i data-lucide="${name}" class="${cls}"></i>`;
const date=(timestamp:number)=>new Date(timestamp*1000).toLocaleDateString('zh-CN',{month:'short',day:'numeric'});
const active=(j:Job|null)=>j&&['queued','running'].includes(j.status);
class ApiError extends Error { constructor(message:string,public status:number){super(message);} }

async function api<T=any>(path:string,init:RequestInit={}):Promise<T>{
  const headers=new Headers(init.headers);
  if(!(init.body instanceof FormData))headers.set('Content-Type','application/json');
  const response=await fetch('/api'+path,{...init,headers});
  const data=await response.json();
  if(!response.ok)throw new ApiError(typeof data.detail==='string'?data.detail:JSON.stringify(data.detail??data),response.status);
  return data;
}
function toast(message:string,error=false){
  document.querySelector('.toast')?.remove();
  const box=document.createElement('div');box.className='toast'+(error?' error':'');box.setAttribute('role','status');box.textContent=message;document.body.append(box);setTimeout(()=>box.remove(),6000);
}
async function action(fn:()=>Promise<void>){if(busy)return;const previousSession=selected?.id;busy=true;render();try{await fn();}catch(e){toast((e as Error).message,true);}finally{busy=false;render();if(previousSession!==selected?.id)window.scrollTo({top:0});}}
function status(j?:Job|null){return !j?'待重建':({queued:'排队中',running:'重建中',succeeded:'已完成',failed:'需处理',cancelled:'已取消'}[j.status]??j.status);}
function stopCamera(){
  capturing=false;clearInterval(captureTimer);cameraStream?.getTracks().forEach(t=>t.stop());cameraStream=null;
}
function resetView(){viewerCleanup?.();viewerCleanup=null;}

function render(){
  resetView();
  const ready=sessions.filter(s=>s.job?.status==='succeeded').length;
  app.innerHTML=`<aside class="sidebar ${menuOpen?'open':''}">
    <a class="brand" href="#" id="brand">${icon('scan')}<span>web<span class="brand-light">scan</span><small>空间采集工作室</small></span></a>
    <div class="workspace"><span class="avatar">S</span><div>个人工作空间<small>LOCAL WORKSPACE</small></div><span class="live-dot"></span></div>
    <span class="nav-label">工作台</span>
    <button class="nav-item ${screen==='library'?'selected':''}" data-nav="library">${icon('layers')}我的采集<span>${sessions.length}</span></button>
    <button class="nav-item ${screen==='guide'?'selected':''}" data-nav="guide">${icon('circle-help')}拍摄指南</button>
    <div class="sidebar-bottom"><div class="local-label"><span class="live-dot"></span>${health?"本地服务已连接":"正在连接本地服务"}</div><p>你的照片与模型<br>保存在这台电脑上。</p><span class="version">WEB SCAN / V0.1</span></div>
  </aside>
  <div class="main-shell"><header><button class="icon-button mobile-only" id="menu" aria-label="打开导航">${icon('menu')}</button><div class="breadcrumb">工作空间 ${icon('chevron-right')} <span>${screen==='guide'?'拍摄指南':selected?esc(selected.name):'我的采集'}</span></div><span class="header-status">${icon('radio')}LOCAL<span class="header-separator"></span>${health?.brush_available?'GPU READY':health?.versions?.torch?'CPU READY':'CONNECTING'}</span></header>
  <main>${screen==='guide'?guide():selected?detail():library(ready)}</main><footer>把真实空间，留在三维里。<span>Built for exploration · Local first</span></footer></div>
  <div id="dialog-root"></div>`;
  createIcons({icons});bind();
  const backend=document.getElementById('backend') as HTMLSelectElement|null;
  if(backend&&selected){
    const initial=job?.config as ReconstructionDraft|undefined;
    const draft=drafts.get(selected.id,initial??defaultDraft(health?.brush_available&&selected.kind!=='fixture'?'brush':'reference'));
    backend.value=draft.backend;configureBackend(draft.backend,draft);
    backend.onchange=()=>{const next=drafts.switchBackend(selected!.id,backend.value as Backend);configureBackend(next.backend,next);};
    for(const id of ['steps','points'])document.getElementById(id)!.onchange=()=>saveSettings();
  }
  if(cameraStream){const video=document.querySelector<HTMLVideoElement>('#camera-video');if(video){video.srcObject=cameraStream;void video.play();}}
  if(selected&&job?.status==='succeeded'){
    const container=document.querySelector<HTMLElement>('#viewer');
    if(container)void mountViewer(container,`/api/jobs/${job.id}/artifacts/preview.splat`,job.report?.backend==='brush').then(cleanup=>{if(container.isConnected){viewerCleanup=cleanup;document.querySelector('#viewer-loading')?.remove();}else cleanup();}).catch(e=>{const hint=container.querySelector('#viewer-loading');if(hint)hint.textContent='预览载入失败：'+e.message;});
  }
}

function library(ready:number){return `
  <section class="heading"><div><div class="eyebrow">YOUR SPACES, IN 3D</div><h1>我的采集<span class="count">${sessions.length}</span></h1><p>从一个视角出发，还原一个完整的空间。</p></div><button class="button primary" id="new">${icon('plus')}新建采集</button></section>
  <section class="hero"><div class="hero-copy"><span class="tag">CAPTURE REALITY</span><h2>把眼前的世界<br>变成可探索的<span>三维记忆。</span></h2><p>用手机绕着目标拍摄，或导入一组照片。<br>在本地完成重建，随时换个角度看世界。</p><div class="hero-actions"><button class="button light" id="hero-new">开始一次采集 ${icon('arrow-up-right')}</button><button class="text-button" id="demo" ${busy?'disabled':''}>${busy?'正在准备…':'试试校准示例'} ${icon('play')}</button></div><small>示例使用程序生成的校准图像，实际执行高斯训练。</small>${health?.indoor_sample_available?`<button class="text-button mint" id="indoor-demo" ${busy?"disabled":""}>体验真实室内场景 · ETH3D ${icon("arrow-up-right")}</button>`:""}</div><div class="orbital" aria-hidden="true"><div class="orbit o1"></div><div class="orbit o2"></div><div class="orbit o3"></div><div class="point-sphere"></div><div class="float-label f1"><span></span>192 GAUSSIANS</div><div class="float-label f2">x / y / z <b>→</b></div><div class="crosshair c1">+</div><div class="crosshair c2">+</div></div></section>
  <section class="stats"><div>${icon('layers')}<span>采集项目<strong>${String(sessions.length).padStart(2,'0')}</strong></span><small>从现实到数字</small></div><div>${icon('box')}<span>已生成模型<strong>${String(ready).padStart(2,'0')}</strong></span><small>可以自由探索</small></div><div>${icon('cpu')}<span>计算环境<strong class="text-value">${health?.brush_available?"Apple GPU":"本地 CPU"}</strong></span><small>${health?.brush_available?"室内质量模式":"小场景验证模式"}</small></div></section>
  <div class="section-heading"><h3>最近的采集</h3><span>${sessions.length} 个项目 · 按创建时间排序</span></div>
  ${sessions.length?`<div class="projects">${sessions.map(card).join('')}<button class="new-card" id="card-new"><span>${icon('plus')}</span>开启新的空间采集<small>手机拍摄 / 导入照片</small></button></div>`:`<div class="empty"><div>${icon('aperture')}</div><h3>你的第一个空间，从这里开始</h3><p>准备好一件物品或一个角落，保持光线稳定，<br>从不同方向拍摄有重叠的照片。</p><button class="text-button mint" id="empty-new">创建第一个采集 ${icon('arrow-up-right')}</button></div>`}`;}

function card(s:Session){const j=s.job;return `<button class="project-card" data-session="${s.id}"><div class="card-preview ${s.kind==='fixture'?'fixture':''}">${j?.status==='succeeded'?`<img src="/api/jobs/${j.id}/artifacts/thumbnail.jpg" alt="${esc(s.name)}项目预览">`:`<div class="preview-symbol">${icon(s.kind==='fixture'?'aperture':'scan')}</div>`}<span class="badge ${j?.status??'draft'}">${active(j??null)?'<span class="pulse-dot"></span>':''}${status(j)}</span><span class="preview-arrow">${icon('arrow-up-right')}</span></div><div class="card-info"><h4>${esc(s.name)}</h4><p><span>${s.frame_count??0} 张照片 ${s.kind==='fixture'?'· 校准示例':''}</span><span>${date(s.created)}</span></p></div></button>`;}

function detail(){const s=selected!;const frames=s.frames??[];const running=active(job);return `
  <button class="back text-button" id="back">${icon('arrow-left')}返回采集列表</button>
  <section class="heading detail-heading"><div><div class="eyebrow">${s.kind==='fixture'?'CALIBRATED FIXTURE':'CAPTURE SESSION'}</div><h1>${esc(s.name)}</h1><p>${s.kind==='fixture'?'程序生成的已校准图像 · 验证真实训练和模型导出':'保持视角重叠，让每个细节都被看见。'}</p></div><span class="badge ${job?.status??'draft'}">${status(job)}</span></section>
  <div class="detail-grid"><div class="detail-primary">${job?.status==='succeeded'?result():cameraStream?`<div class="capture-stage"><video id="camera-video" autoplay muted playsinline></video><div class="capture-guide"></div><span class="capture-note"><span class="pulse-dot"></span>采集中 · 每秒一帧 · 已上传 <b id="capture-count">${frames.length}</b> 张</span></div><div class="capture-toolbar"><button class="button danger" id="stop">${icon('square')}结束拍摄</button><span>请缓慢平移，保持画面重叠</span></div>`:`<section class="capture-placeholder"><div class="corner tl"></div><div class="corner tr"></div><div class="corner bl"></div><div class="corner br"></div><div class="scan-icon">${icon('scan')}</div><h2>${running?'正在重建你的空间':s.status==='ready'?'照片已就绪':'准备记录这个空间'}</h2><p>${running?'后台任务正在执行，离开页面不会中断计算。':s.status==='ready'?'选择训练设置，即可开始生成三维高斯。':'从不同角度拍摄同一个目标，推荐 20–80 张。'}</p>${s.status==='capturing'?`<div class="capture-buttons"><button class="button primary" id="camera" ${busy?'disabled':''}>${icon('camera')}打开相机</button><button class="button secondary" id="upload" ${busy?'disabled':''}>${icon('upload')}导入照片</button></div><span class="subtle">相机需要 HTTPS 或本机 localhost</span>`:''}</section>`}
  <section class="panel photo-panel"><div class="section-heading"><h3>采集照片 <span>${frames.length}</span></h3>${s.status==='capturing'?`<button class="text-button" id="add-photos" ${busy||capturing?'disabled':''}>${icon('plus')}添加照片</button>`:'<span>输入已冻结</span>'}</div><div class="filmstrip">${frames.length?frames.map((f,i)=>`<div><img src="/api/sessions/${s.id}/frames/${f.id}" alt="采集照片 ${i+1}" loading="lazy"><span>${String(i+1).padStart(2,'0')}</span></div>`).join(''):`<div class="filmstrip-empty">照片会在这里显示，上传成功后保存在本地。</div>`}</div><div id="upload-status" class="upload-status">${busy&&uploadCount?`正在上传 · 已处理 ${uploadCount} 张`:''}</div><button id="resume" class="text-button mint">${icon('rotate-ccw')}检查并恢复待上传照片</button></section></div>
  <aside class="detail-aside"><section class="panel settings"><div class="panel-title">${icon('cpu')}重建设置</div><label>计算模式<select id="backend" ${running||busy?'disabled':''}><option value="brush" ${!health?.brush_available||s.kind==="fixture"?"disabled":""}>本地 GPU · 室内质量</option><option value="reference">本地 CPU · 参考实现</option><option value="cuda" ${!health?.cuda_backend_installed?'disabled':''}>CUDA · gsplat</option></select></label><div class="two-cols"><label>训练步数<select id="steps" ${running?'disabled':''}><option value="60">60 · 快速检查</option><option value="120" selected>120 · 标准验证</option><option value="300">300 · 更多迭代</option></select></label><label>高斯上限<select id="points" ${running?'disabled':''}><option value="192">192</option><option value="384" selected>384</option><option value="768">768</option></select></label></div><p class="settings-note" id="engine-note">选择适合当前场景的重建模式。</p><button class="button primary full" id="reconstruct" ${busy||capturing||running||frames.length<3?'disabled':''}>${icon('play')}${running?'重建进行中':job?.status==='succeeded'?'再次重建':'开始重建'}</button>${running?`<button class="button secondary full" id="cancel">${icon('square')}取消任务</button>`:''}</section>
  ${job?jobPanel():`<section class="panel steps-guide"><div class="panel-title">三步，记录一个空间</div><p><b>01</b><span>采集照片<small>环绕目标，保持相邻画面重叠</small></span></p><p><b>02</b><span>本地重建<small>求解相机，优化三维高斯</small></span></p><p><b>03</b><span>自由探索<small>旋转、缩放，导出模型资产</small></span></p></section>`}
  ${s.status==='capturing'?`<section class="panel sensor-panel"><div class="panel-title">${icon('activity')}运动数据 <span class="optional">可选</span></div><p>记录帧附近的 IMU 观测，供后续研究。当前重建使用照片求解位姿。</p><button class="button secondary full" id="sensor" ${sensorEnabled?'disabled':''}>${sensorEnabled?'已开启记录':'启用运动传感器'}</button></section>`:''}</aside></div><input id="file-input" type="file" accept="image/jpeg,image/png,image/webp" multiple hidden>`;}

function configureBackend(backend:string,config?:Record<string,any>){
  const gpu=backend==='brush';
  const steps=document.getElementById('steps') as HTMLSelectElement,points=document.getElementById('points') as HTMLSelectElement;
  const stepOptions=[...new Set([...(gpu?[1000,3000,6000]:[60,120,300]),...(config?.steps?[config.steps]:[])])].sort((a,b)=>a-b);
  const pointOptions=[...new Set([...(gpu?[50000,150000,300000]:[192,384,768]),...(config?.max_points?[config.max_points]:[])])].sort((a,b)=>a-b);
  steps.innerHTML=stepOptions.map(n=>`<option value="${n}">${n} 步</option>`).join('');
  points.innerHTML=pointOptions.map(n=>`<option value="${n}">${n.toLocaleString()}</option>`).join('');
  steps.value=String(config?.steps??(gpu?3000:120));points.value=String(config?.max_points??(gpu?150000:384));
  document.getElementById('engine-note')!.textContent=gpu?'Brush 本地 GPU 训练，包含动态增点和高阶颜色。较大场景建议更多训练步数。':'CPU / CUDA 参考训练使用低分辨率和固定高斯数量，适合调试；结果精细度有限。';
}

function saveSettings(){
  if(!selected)return;
  drafts.save(selected.id,{backend:(document.getElementById('backend') as HTMLSelectElement).value as Backend,steps:Number((document.getElementById('steps') as HTMLSelectElement).value),max_points:Number((document.getElementById('points') as HTMLSelectElement).value)});
}

function result(){const r=job!.report!;return `<section class="result"><div id="viewer"><div id="viewer-loading">正在加载三维模型…</div></div><div class="viewer-overlay"><span class="viewer-tag">${icon('box')}3D GAUSSIAN</span><span>拖动旋转 · 滚轮 / 双指缩放</span></div><div class="result-bar"><div><span class="mint-dot"></span>重建完成 <small>${r.gaussians} 个高斯 · ${r.seconds} 秒</small></div><a class="button secondary" download href="/api/jobs/${job!.id}/artifacts/gaussians.ply">${icon('download')}下载 PLY</a></div></section>`;}
function jobPanel(){const j=job!;return `<section class="panel progress-panel"><div class="panel-title">${icon('activity')}任务进度<span>${Math.round(j.progress*100)}%</span></div><div class="progress-track"><div style="width:${j.progress*100}%"></div></div><p class="job-message ${j.status==='failed'?'failure':''}">${esc(j.message)}</p><div class="stage-list">${[['prepare','准备数据'],['sfm','相机位姿'],['train','高斯训练'],['export','导出模型']].map(([key,name])=>`<div class="${j.stage===key?'current':''}"><span></span>${name}${j.status==='succeeded'?icon('check'):''}</div>`).join('')}</div>${selected?.kind==='fixture'?'<p class="small-note">此示例使用已知相机位姿，跳过 COLMAP。</p>':''}<button class="text-button" id="log">查看任务日志 ${icon('arrow-up-right')}</button>${j.status==='succeeded'?`<a class="text-button" href="/api/jobs/${j.id}/artifacts/report.json" download>下载训练报告 ${icon('download')}</a>`:''}</section>`;}
function guide(){return `<section class="heading"><div><div class="eyebrow">A BETTER CAPTURE</div><h1>好模型，从好照片开始。</h1><p>给算法足够的信息，也给空间留一点耐心。</p></div></section><div class="guide-grid">${[['01','围绕目标移动','保持相机朝向目标，从不同位置拍摄。不要只站在原地转动手机。'],['02','让画面相互重叠','相邻照片尽量保留 60–80% 的共同内容，缓慢移动，少拍重复视角。'],['03','保持光线和场景稳定','优先选择有纹理的静态对象。镜面、玻璃、纯白墙面与移动人物会增加难度。'],['04','补拍高处和低处','环绕拍完后，再从不同高度补充视角。没有拍到的部分无法可靠重建。']].map(([n,t,d])=>`<article class="panel guide-card"><span>${n}</span><h2>${t}</h2><p>${d}</p></article>`).join('')}</div><section class="panel guide-note"><h3>本地调试说明</h3><p>CPU 参考模式执行真实的低分辨率高斯优化，输出 PLY 和 splat。它用于验证流程，不等同于生产级高精度重建。校准示例由程序生成并提供已知位姿；导入照片会运行 COLMAP。照片与结果默认仅保存在本机。</p><p>手机访问请使用受信任的 HTTPS，并通过后端配置允许的局域网主机名；详情见项目 README。当前服务为单用户本地工具。</p><button class="button primary" id="guide-new">${icon('plus')}新建采集</button></section>`;}

function bind(){
  const on=(id:string,fn:()=>void)=>document.getElementById(id)?.addEventListener('click',fn);
  for(const id of ['new','hero-new','empty-new','card-new','guide-new'])on(id,newDialog);
  on('menu',()=>{menuOpen=!menuOpen;document.querySelector('.sidebar')?.classList.toggle('open',menuOpen);});on('brand',()=>goLibrary());on('back',goLibrary);
  document.querySelectorAll<HTMLElement>('[data-nav]').forEach(b=>b.onclick=()=>{if(busy||takingFrame){toast('请等待当前照片处理完成');return;}stopCamera();navigationRequest++;selected=null;job=null;screen=b.dataset.nav!;menuOpen=false;render();});
  document.querySelectorAll<HTMLElement>('[data-session]').forEach(b=>b.onclick=()=>void openSession(b.dataset.session!));
  on('indoor-demo',()=>void action(async()=>{selected=await api('/indoor-sample',{method:'POST'});job=null;screen='library';await refreshList();}));
  on('demo',()=>void action(async()=>{selected=await api('/fixtures',{method:'POST'});job=null;screen='library';await refreshList();}));
  for(const id of ['upload','add-photos'])on(id,()=>document.getElementById('file-input')?.click());
  document.getElementById('file-input')?.addEventListener('change',event=>{const files=[...(event.target as HTMLInputElement).files??[]];void uploadFiles(files);});
  on('camera',()=>void startCamera());on('stop',()=>{stopCamera();void reloadSelected();});
  on('sensor',()=>void enableSensor());on('resume',()=>void action(async()=>{await flushPending();await reloadSelected(false);toast('待上传照片已处理');}));
  on('reconstruct',()=>{const backend=(document.getElementById('backend') as HTMLSelectElement).value;const steps=Number((document.getElementById('steps') as HTMLSelectElement).value);const max_points=Number((document.getElementById('points') as HTMLSelectElement).value);void action(async()=>{await flushPending();await api(`/sessions/${selected!.id}/finalize`,{method:'POST'});job=await api('/jobs',{method:'POST',body:JSON.stringify({session_id:selected!.id,backend,steps,max_points,resolution:backend==='brush'?512:96})});await reloadSelected(false);});});
  on('cancel',()=>void action(async()=>{job=await api(`/jobs/${job!.id}/cancel`,{method:'POST'});await refreshList();}));
  on('log',()=>void showLog());
}
async function refreshList(){sessions=await api('/sessions');}
async function openSession(id:string){if(busy||takingFrame)return;stopCamera();const request=++navigationRequest;try{const next:Session=await api(`/sessions/${id}`);if(request!==navigationRequest)return;selected=next;job=selected!.jobs?.[0]??null;screen='library';render();window.scrollTo({top:0});}catch(e){toast((e as Error).message,true);}}
async function reloadSelected(redraw=true){if(selected){selected=await api(`/sessions/${selected.id}`);job=selected!.jobs?.[0]??null;}await refreshList();if(redraw)render();}
function goLibrary(){if(busy||takingFrame){toast('请等待当前照片处理完成');return;}stopCamera();navigationRequest++;selected=null;job=null;screen='library';void refreshList().then(()=>{render();window.scrollTo({top:0});}).catch(e=>toast(e.message,true));}

function newDialog(){
  if(busy||takingFrame)return;
  const root=document.getElementById('dialog-root')!;root.innerHTML=`<dialog open class="modal"><form id="new-form"><div class="modal-heading"><h2>新建采集</h2><button type="button" class="icon-button" id="close-dialog" aria-label="关闭">${icon('x')}</button></div><p>给即将记录的空间起个名字。</p><label>采集名称<input id="session-name" maxlength="80" placeholder="例如：窗边的绿植" required autofocus></label><button class="button primary full" type="submit">创建并开始 ${icon('arrow-up-right')}</button></form></dialog><div class="scrim"></div>`;createIcons({icons});document.getElementById('session-name')?.focus();
  document.getElementById('close-dialog')!.onclick=()=>root.replaceChildren();
  document.getElementById('new-form')!.onsubmit=event=>{event.preventDefault();const name=(document.getElementById('session-name') as HTMLInputElement).value.trim();if(!name)return;void action(async()=>{selected=await api('/sessions',{method:'POST',body:JSON.stringify({name})});job=null;screen='library';await refreshList();});};
}
async function showLog(){try{const data=await api(`/jobs/${job!.id}/log`);const root=document.getElementById('dialog-root')!;root.innerHTML=`<dialog open class="modal log-modal"><div class="modal-heading"><h2>任务日志</h2><button class="icon-button" id="close-dialog" aria-label="关闭">${icon('x')}</button></div><pre>${esc(data.text)}</pre></dialog><div class="scrim"></div>`;createIcons({icons});document.getElementById('close-dialog')!.onclick=()=>root.replaceChildren();}catch(e){toast((e as Error).message,true);}}

async function flushPending(){
  if(!selected)return;
  const sid=selected.id;
  for(const frame of await pending(sid)){
    const body=new FormData();body.append('file',frame.blob,'capture.jpg');body.append('metadata',JSON.stringify(frame.metadata));
    try { await api(`/sessions/${sid}/frames`,{method:'POST',body}); }
    catch(error){if(error instanceof ApiError&&[404,409,413,422].includes(error.status))await acknowledge(frame.key);throw error;}
    await acknowledge(frame.key);
  }
}
async function uploadFiles(files:File[]){if(!files.length||!selected||busy)return;await action(async()=>{uploadCount=0;for(const file of files){if(file.size>16*1024*1024)throw new Error(`${file.name} 超过 16 MB`);await enqueue({key:crypto.randomUUID(),session:selected!.id,blob:file,metadata:{source:'import',original_name:file.name}});await flushPending();uploadCount++;const state=document.getElementById('upload-status');if(state)state.textContent=`正在上传 · ${uploadCount}/${files.length}`;}await reloadSelected(false);toast(`已处理 ${files.length} 张照片`);uploadCount=0;});await reloadSelected();}
async function startCamera(){
  if(!selected||busy)return;
  try{
    if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia)throw new Error('相机需要 HTTPS 或本机 localhost；也可以使用导入照片。');
    cameraStream=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1600}},audio:false});capturing=true;render();
    captureTimer=window.setInterval(()=>void captureFrame(),1200);
  }catch(e){stopCamera();toast('无法打开相机：'+(e as Error).message,true);render();}
}
let takingFrame=false;
async function captureFrame(){
  if(takingFrame||!capturing||!selected)return;takingFrame=true;
  try{const video=document.querySelector<HTMLVideoElement>('#camera-video');if(!video?.videoWidth)return;const canvas=document.createElement('canvas');const scale=Math.min(1,1600/video.videoWidth);canvas.width=Math.round(video.videoWidth*scale);canvas.height=Math.round(video.videoHeight*scale);canvas.getContext('2d')!.drawImage(video,0,0,canvas.width,canvas.height);const timestamp=performance.now();const blob=await new Promise<Blob|null>(resolve=>canvas.toBlob(resolve,'image/jpeg',0.9));if(!blob)return;await enqueue({key:crypto.randomUUID(),session:selected.id,blob,metadata:{source:'camera',timestamp_ms:timestamp,clock_domain:'performance',sensor:sensorEnabled?sensor:null}});await flushPending();selected=await api(`/sessions/${selected.id}`);const count=document.getElementById('capture-count');if(count)count.textContent=String(selected!.frames!.length);if(selected!.frames!.length>=200){stopCamera();render();}}
  catch(e){stopCamera();toast((e as Error).message+'；缓存照片可稍后恢复上传。',true);render();}finally{takingFrame=false;}
}
async function enableSensor(){try{const motion=DeviceMotionEvent as typeof DeviceMotionEvent & {requestPermission?:()=>Promise<string>};if(motion.requestPermission&&await motion.requestPermission()!=='granted')throw new Error('未获得传感器权限');window.addEventListener('devicemotion',event=>{sensor={timestamp_ms:performance.now(),acceleration:event.acceleration?{x:event.acceleration.x,y:event.acceleration.y,z:event.acceleration.z}:null,rotationRate:event.rotationRate?{alpha:event.rotationRate.alpha,beta:event.rotationRate.beta,gamma:event.rotationRate.gamma}:null};});sensorEnabled=true;render();toast('已启用运动观测记录；当前位姿仍由照片求解');}catch(e){toast((e as Error).message,true);}}
document.addEventListener('visibilitychange',()=>{if(document.hidden&&capturing){stopCamera();render();toast('页面进入后台，拍摄已暂停');}});
setInterval(async()=>{
  if(polling||busy||capturing||document.querySelector('dialog[open]'))return;
  const target=selected&&active(job)?{sessionId:selected.id,jobId:job!.id}:null;
  const inLibrary=!selected&&screen==='library';
  if(!target&&!inLibrary)return;
  polling=true;
  try{
    if(target){
      const next:Job=await api(`/jobs/${target.jobId}`);
      const current=()=>selected&&job?{sessionId:selected.id,jobId:job.id}:null;
      if(busy||!matchesJobTarget(target,current()))return;
      if(next.updated!==job!.updated){
        const updated:Session[]=await api('/sessions');
        if(busy||!matchesJobTarget(target,current()))return;
        if(document.querySelector('dialog[open]'))return;
        job=next;sessions=updated;render();
      }
    }else{
      const updated:Session[]=await api('/sessions');
      if(!busy&&!selected&&screen==='library'&&!document.querySelector('dialog[open]')&&JSON.stringify(updated)!==JSON.stringify(sessions)){
        sessions=updated;render();
      }
    }
  }catch{/* Keep the last confirmed state when the connection drops. */}finally{polling=false;}
},1500);
render();
void Promise.all([api('/health'),refreshList()]).then(([value])=>{health=value;render();}).catch(e=>toast('后端尚未连接：'+e.message,true));
