import * as SPLAT from 'gsplat';

export async function mountViewer(container: HTMLElement, url: string, startWalk=false): Promise<() => void> {
  const renderer = new SPLAT.WebGLRenderer();
  renderer.backgroundColor = new SPLAT.Color32(13, 20, 25, 255);
  renderer.canvas.setAttribute('aria-label','可交互的三维高斯模型，拖动旋转，滚轮缩放');
  container.append(renderer.canvas);
  const scene = new SPLAT.Scene();
  const camera = new SPLAT.Camera();
  let controls: {update:()=>void;dispose:()=>void};
  const orbit=()=>{const value=new SPLAT.OrbitControls(camera,renderer.canvas,0.6,0.5,3.5,false);value.minZoom=.1;value.maxZoom=30;return value;};
  controls=orbit();
  const toolbar=document.createElement('div');toolbar.className='viewer-controls';
  toolbar.innerHTML='<button class="active" data-mode="orbit">环绕查看</button><button data-mode="walk">室内漫游</button><button data-mode="reset">重置视角</button>';
  const pad=document.createElement('div');pad.className='walk-pad';pad.hidden=true;
  pad.innerHTML='<span>拖动看向 · WASD / 方向按钮移动 · 无碰撞检测</span><div><button data-key="KeyW" aria-label="向前移动">↑</button><button data-key="KeyA" aria-label="向左移动">←</button><button data-key="KeyS" aria-label="向后移动">↓</button><button data-key="KeyD" aria-label="向右移动">→</button></div>';
  container.append(toolbar,pad);
  let firstView:number[][]|null=null;
  const home=()=>{if(!firstView)return;const v=firstView;camera.position=new SPLAT.Vector3(...[0,1,2].map(i=>-v[0][i]*v[0][3]-v[1][i]*v[1][3]-v[2][i]*v[2][3]) as [number,number,number]);camera.rotation=SPLAT.Quaternion.FromMatrix3(new SPLAT.Matrix3(v[0][0],v[1][0],v[2][0],v[0][1],v[1][1],v[2][1],v[0][2],v[1][2],v[2][2]));};
  const change=(mode:string)=>{controls.dispose();pad.hidden=mode!=='walk';if(mode==='walk'){home();controls=walkControls(camera,renderer.canvas,pad);}else controls=orbit();toolbar.querySelectorAll('button').forEach(b=>b.classList.toggle('active',b.dataset.mode===(mode==='walk'?'walk':'orbit')));};
  toolbar.querySelectorAll('button').forEach(b=>b.onclick=()=>change(b.dataset.mode!));
  let animation=0, disposed=false;
  const resize=()=>{ if (!disposed) { const w=Math.max(container.clientWidth,1),h=Math.max(container.clientHeight,1); renderer.setSize(w,h); camera.data.fx=Math.min(w,h)*0.95;camera.data.fy=Math.min(w,h)*0.95; } };
  const observer=new ResizeObserver(resize); observer.observe(container); resize();
  const cleanup=()=>{disposed=true;cancelAnimationFrame(animation);observer.disconnect();controls.dispose();renderer.dispose();renderer.canvas.remove();toolbar.remove();pad.remove();};
  try {
    await SPLAT.Loader.LoadAsync(url, scene, undefined, false);
    try{const response=await fetch(url.replace('preview.splat','cameras.json'));const data=await response.json();firstView=data.views?.[0]?.view??null;}catch{/* Orbit navigation remains available without camera metadata. */}
    if (!container.isConnected) { cleanup(); return ()=>{}; }
    if(startWalk&&firstView)change('walk');
    const tick=()=>{ if(disposed)return; controls.update(); renderer.render(scene,camera); animation=requestAnimationFrame(tick); };
    tick(); return cleanup;
  } catch(error) { cleanup(); throw error; }
}

function walkControls(camera:SPLAT.Camera,canvas:HTMLCanvasElement,pad:HTMLElement){
  const keys=new Set<string>();let dragging=false,x=0,y=0;
  const euler=camera.rotation.toEuler();let pitch=euler.x,yaw=euler.y,last=performance.now();
  canvas.tabIndex=0;canvas.focus({preventScroll:true});
  const down=(e:PointerEvent)=>{dragging=true;x=e.clientX;y=e.clientY;canvas.setPointerCapture(e.pointerId);canvas.focus({preventScroll:true});};
  const move=(e:PointerEvent)=>{if(!dragging)return;yaw+=(e.clientX-x)*.004;pitch-=(e.clientY-y)*.004;pitch=Math.max(-1.5,Math.min(1.5,pitch));x=e.clientX;y=e.clientY;camera.rotation=SPLAT.Quaternion.FromEuler(new SPLAT.Vector3(pitch,yaw,0));};
  const up=()=>{dragging=false;};
  const keydown=(e:KeyboardEvent)=>{if(document.activeElement!==canvas)return;if(['KeyW','KeyA','KeyS','KeyD','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','KeyQ','KeyE'].includes(e.code)){e.preventDefault();keys.add(e.code);}};
  const keyup=(e:KeyboardEvent)=>keys.delete(e.code);const clear=()=>keys.clear();
  const buttons=[...pad.querySelectorAll<HTMLButtonElement>('button')];
  for(const b of buttons){b.onpointerdown=e=>{e.preventDefault();b.setPointerCapture(e.pointerId);keys.add(b.dataset.key!);};b.onpointerup=b.onpointercancel=()=>{keys.delete(b.dataset.key!);};}
  canvas.addEventListener('pointerdown',down);canvas.addEventListener('pointermove',move);canvas.addEventListener('pointerup',up);canvas.addEventListener('pointercancel',up);
  window.addEventListener('keydown',keydown);window.addEventListener('keyup',keyup);window.addEventListener('blur',clear);
  return {update:()=>{const now=performance.now(),dt=Math.min(.05,(now-last)/1000);last=now;const forward=camera.rotation.apply(new SPLAT.Vector3(0,0,1));const right=camera.rotation.apply(new SPLAT.Vector3(1,0,0));const f=Number(keys.has('KeyW')||keys.has('ArrowUp'))-Number(keys.has('KeyS')||keys.has('ArrowDown'));const r=Number(keys.has('KeyD')||keys.has('ArrowRight'))-Number(keys.has('KeyA')||keys.has('ArrowLeft'));const u=Number(keys.has('KeyQ'))-Number(keys.has('KeyE'));camera.position=camera.position.add(forward.multiply(f*dt*.6)).add(right.multiply(r*dt*.6)).add(new SPLAT.Vector3(0,-u*dt*.6,0));},dispose:()=>{canvas.removeEventListener('pointerdown',down);canvas.removeEventListener('pointermove',move);canvas.removeEventListener('pointerup',up);canvas.removeEventListener('pointercancel',up);window.removeEventListener('keydown',keydown);window.removeEventListener('keyup',keyup);window.removeEventListener('blur',clear);for(const b of buttons)b.onpointerdown=b.onpointerup=b.onpointercancel=null;keys.clear();}};
}
