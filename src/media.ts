import { sampleVideoTimes } from './session-state';

function mediaEvent(video:HTMLVideoElement, event:string, start:()=>void, timeoutMs=15000):Promise<void>{
  return new Promise((resolve,reject)=>{
    const cleanup=()=>{clearTimeout(timer);video.removeEventListener(event,done);video.removeEventListener('error',failed);};
    const done=()=>{cleanup();resolve();};
    const failed=()=>{cleanup();reject(new Error('当前浏览器无法读取这个视频，请尝试 MP4，或直接导入照片。'));};
    const timer=setTimeout(()=>{cleanup();reject(new Error('视频读取超时，请尝试较短的视频。'));},timeoutMs);
    video.addEventListener(event,done,{once:true});video.addEventListener('error',failed,{once:true});start();
  });
}

/** Decode locally: only sampled JPEG frames are sent to the backend. */
export async function importVideo(file:File, slots:number, consume:(frame:Blob,time:number,index:number,total:number)=>Promise<void>){
  if(file.size>250*1024*1024)throw new Error('单段视频不能超过 250 MB');
  const video=document.createElement('video'),url=URL.createObjectURL(file);
  video.preload='auto';video.muted=true;video.playsInline=true;
  try{
    await mediaEvent(video,'loadeddata',()=>{video.src=url;video.load();});
    const times=sampleVideoTimes(video.duration,slots);
    const factor=Math.min(1,1600/Math.max(video.videoWidth,video.videoHeight));
    const canvas=document.createElement('canvas');canvas.width=Math.round(video.videoWidth*factor);canvas.height=Math.round(video.videoHeight*factor);
    if(!canvas.width||!canvas.height)throw new Error('视频尺寸无效');
    const context=canvas.getContext('2d');if(!context)throw new Error('浏览器不支持视频抽帧');
    for(let i=0;i<times.length;i++){
      if(Math.abs(video.currentTime-times[i])>.001)await mediaEvent(video,'seeked',()=>{video.currentTime=times[i];});
      context.drawImage(video,0,0,canvas.width,canvas.height);
      const frame=await new Promise<Blob|null>(resolve=>canvas.toBlob(resolve,'image/jpeg',.94));
      if(!frame)throw new Error('视频帧编码失败');
      await consume(frame,times[i],i+1,times.length);
    }
    return times.length;
  }finally{video.pause();video.removeAttribute('src');video.load();URL.revokeObjectURL(url);}
}
