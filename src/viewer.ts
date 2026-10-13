import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { SparkRenderer, SplatMesh } from '@sparkjsdev/spark';
import { RenderScheduler } from './render-scheduler';
import { applyPhotoPose, applyPhotoProjection, fitPhoto, type PhotoView } from './camera';

export async function mountViewer(container:HTMLElement,url:string,startWalk=false):Promise<()=>void>{
  const renderer=new THREE.WebGLRenderer({antialias:false});
  renderer.setPixelRatio(Math.min(devicePixelRatio,2));
  renderer.setClearColor(0x0b111c,1);
  renderer.outputColorSpace=THREE.SRGBColorSpace;
  renderer.domElement.setAttribute('aria-label','可交互的三维高斯模型，拖动旋转，滚轮缩放');
  const surface=document.createElement('div');surface.className='viewer-surface';surface.append(renderer.domElement);container.append(surface);
  const photo=document.createElement('img');photo.className='viewer-reference';photo.alt='当前拍摄视角的去畸变原照片';photo.hidden=true;surface.append(photo);
  const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(60,1,.01,1000);
  let ready=false,controlsMoving=false;
  const frames=new RenderScheduler(()=>{
    if(!ready||!container.isConnected)return;
    if(controlsMoving)controlsMoving=controls?.update()===true;
    renderer.render(scene,camera);
    return controlsMoving;
  });
  const requestControlsFrame=()=>{controlsMoving=true;frames.invalidate();};
  const spark=new SparkRenderer({renderer,enableLod:false,sortRadial:false,onDirty:frames.invalidate});scene.add(spark);
  const toolbar=document.createElement('div');toolbar.className='viewer-controls';
  toolbar.innerHTML='<button data-mode="orbit">环绕查看</button><button data-mode="walk">室内漫游</button><button data-mode="photo" disabled>拍摄视角</button><button data-mode="reset">重置视角</button>';
  const cameraBar=document.createElement('div');cameraBar.className='viewer-camera-bar';cameraBar.hidden=true;
  cameraBar.innerHTML='<label>拍摄视角 <select aria-label="选择拍摄视角"></select></label><button class="button secondary" aria-pressed="false">对照原照片</button>';
  const select=cameraBar.querySelector('select')!,compare=cameraBar.querySelector('button')!;
  const status=document.createElement('div');status.className='viewer-loading';status.textContent='加载完整高斯模型…';status.setAttribute('role','status');
  container.append(toolbar,cameraBar,status);
  let views:PhotoView[]=[],index=0,mode='orbit',disposed=false,mesh:SplatMesh|undefined;
  let controls:{update:()=>boolean|void;dispose:()=>void}|undefined;
  const selected=()=>views[index];
  const syncVisibility=()=>frames.setPaused(document.hidden||!photo.hidden);
  document.addEventListener('visibilitychange',syncVisibility);
  renderer.domElement.addEventListener('webglcontextrestored',frames.invalidate);
  syncVisibility();
  const resize=()=>{
    if(disposed)return;
    let width=Math.max(1,container.clientWidth),height=Math.max(1,container.clientHeight);
    if(mode==='photo'&&selected()){({width,height}=fitPhoto(width,height,selected()));}
    surface.style.width=width+'px';surface.style.height=height+'px';renderer.setSize(width,height,false);
    camera.aspect=width/height;camera.updateProjectionMatrix();
    if(mode==='photo'&&selected())applyPhotoProjection(camera,selected());
    frames.invalidate();
  };
  const showPhoto=()=>{
    if(!selected())return;
    applyPhotoPose(camera,selected());
    photo.src=url.replace('/artifacts/gaussians.ply',`/views/${index}`);
    photo.hidden=true;compare.textContent='对照原照片';compare.setAttribute('aria-pressed','false');
  };
  const change=(requested:string)=>{
    mode=requested==='reset'?mode:requested;controls?.dispose();controls=undefined;controlsMoving=false;
    cameraBar.hidden=mode!=='photo';photo.hidden=true;
    if((mode==='photo'||mode==='walk')&&selected()){
      showPhoto();
      if(mode==='walk')controls=walkControls(camera,renderer.domElement,requestControlsFrame);
    }else{
      camera.up.set(0,-1,0);camera.position.set(0,-.8,-3.5);camera.lookAt(0,0,0);
      const orbit=new OrbitControls(camera,renderer.domElement);orbit.enableDamping=true;orbit.minDistance=.03;orbit.maxDistance=30;orbit.addEventListener('change',requestControlsFrame);controls={update:()=>orbit.update(),dispose:()=>{orbit.removeEventListener('change',requestControlsFrame);orbit.dispose();}};
    }
    toolbar.querySelectorAll<HTMLButtonElement>('button').forEach(button=>{
      if(button.dataset.mode!=='reset'){button.classList.toggle('active',button.dataset.mode===mode);button.setAttribute('aria-pressed',String(button.dataset.mode===mode));}
    });
    syncVisibility();resize();
  };
  toolbar.querySelectorAll<HTMLButtonElement>('button').forEach(button=>button.onclick=()=>change(button.dataset.mode!));
  select.onchange=()=>{index=Number(select.value);change('photo');};
  compare.onclick=()=>{photo.hidden=!photo.hidden;compare.textContent=photo.hidden?'对照原照片':'返回模型';compare.setAttribute('aria-pressed',String(!photo.hidden));syncVisibility();};
  const observer=new ResizeObserver(resize);observer.observe(container);resize();
  const cleanup=()=>{if(disposed)return;disposed=true;frames.dispose();document.removeEventListener('visibilitychange',syncVisibility);renderer.domElement.removeEventListener('webglcontextrestored',frames.invalidate);observer.disconnect();controls?.dispose();mesh?.dispose();spark.dispose();renderer.dispose();surface.remove();toolbar.remove();cameraBar.remove();status.remove();};
  try{
    mesh=new SplatMesh({url,enableLod:false,lod:false,extSplats:true});scene.add(mesh);
    const response=await fetch(url.replace('gaussians.ply','cameras.json'));
    if(response.ok)views=(await response.json()).views??[];
    await mesh.initialized;
    if(!container.isConnected){cleanup();return ()=>{};}
    mesh.maxSh=3;mesh.updateGenerator();
    views.forEach((v,i)=>{const option=new Option(`照片 ${i+1}`,String(i));select.add(option);});
    const calibrated=!!views.length&&views.every(v=>v.width&&v.height&&v.image_name);
    toolbar.querySelector<HTMLButtonElement>('[data-mode="photo"]')!.disabled=!calibrated;
    ready=true;
    change(calibrated?'photo':startWalk&&views.length?'walk':'orbit');
    status.remove();
    frames.invalidate();
    return cleanup;
  }catch(error){cleanup();throw error;}
}

function walkControls(camera:THREE.PerspectiveCamera,canvas:HTMLCanvasElement,invalidate:()=>void){
  const keys=new Set<string>();let dragging=false,x=0,y=0,last=performance.now();
  const localUp=new THREE.Vector3(0,1,0).applyQuaternion(camera.quaternion);
  canvas.tabIndex=0;canvas.focus({preventScroll:true});
  const down=(e:PointerEvent)=>{dragging=true;x=e.clientX;y=e.clientY;canvas.setPointerCapture(e.pointerId);canvas.focus({preventScroll:true});};
  const move=(e:PointerEvent)=>{if(!dragging)return;
    const yaw=new THREE.Quaternion().setFromAxisAngle(localUp,-(e.clientX-x)*.004);
    const pitch=new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1,0,0),-(e.clientY-y)*.004);
    camera.quaternion.premultiply(yaw).multiply(pitch).normalize();x=e.clientX;y=e.clientY;invalidate();
  };
  const up=()=>{dragging=false;};
  const keydown=(e:KeyboardEvent)=>{if(document.activeElement!==canvas)return;if(['KeyW','KeyA','KeyS','KeyD','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','KeyQ','KeyE'].includes(e.code)){e.preventDefault();if(!keys.size)last=performance.now();keys.add(e.code);invalidate();}};
  const keyup=(e:KeyboardEvent)=>keys.delete(e.code),clear=()=>keys.clear();
  canvas.addEventListener('pointerdown',down);canvas.addEventListener('pointermove',move);canvas.addEventListener('pointerup',up);canvas.addEventListener('pointercancel',up);
  document.addEventListener('visibilitychange',clear);
  window.addEventListener('keydown',keydown);window.addEventListener('keyup',keyup);window.addEventListener('blur',clear);
  return {update:()=>{const now=performance.now(),dt=Math.min(.05,(now-last)/1000);last=now;const f=Number(keys.has('KeyW')||keys.has('ArrowUp'))-Number(keys.has('KeyS')||keys.has('ArrowDown'));const r=Number(keys.has('KeyD')||keys.has('ArrowRight'))-Number(keys.has('KeyA')||keys.has('ArrowLeft'));const u=Number(keys.has('KeyQ'))-Number(keys.has('KeyE'));camera.translateZ(-f*dt*.6);camera.translateX(r*dt*.6);camera.position.addScaledVector(localUp,u*dt*.6);return keys.size>0;},dispose:()=>{canvas.removeEventListener('pointerdown',down);canvas.removeEventListener('pointermove',move);canvas.removeEventListener('pointerup',up);canvas.removeEventListener('pointercancel',up);window.removeEventListener('keydown',keydown);window.removeEventListener('keyup',keyup);window.removeEventListener('blur',clear);document.removeEventListener('visibilitychange',clear);keys.clear();}};
}
