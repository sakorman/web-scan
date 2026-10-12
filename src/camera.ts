import { Matrix4, PerspectiveCamera } from 'three';

export type PhotoView = { view:number[][]; K:number[][]; width?:number; height?:number; image_name?:string };

/** COLMAP uses right/down/forward; Three cameras use right/up/backward. */
export function applyPhotoPose(camera:PerspectiveCamera, view:PhotoView){
  const values=view.view.flat();
  if(values.length!==16||values.some(v=>!Number.isFinite(v)))throw new Error('相机位姿无效');
  const worldToCamera=new Matrix4().set(...values as Parameters<Matrix4['set']>);
  if(Math.abs(worldToCamera.determinant())<1e-8)throw new Error('相机位姿不可逆');
  const cameraToWorld=worldToCamera.invert().multiply(new Matrix4().makeScale(1,-1,-1));
  cameraToWorld.decompose(camera.position,camera.quaternion,camera.scale);
  camera.up.set(0,1,0).applyQuaternion(camera.quaternion);
  camera.updateMatrixWorld(true);
}

export function applyPhotoProjection(camera:PerspectiveCamera, view:PhotoView){
  const {K,width:w,height:h}=view;
  if(!w||!h||!K.flat().every(Number.isFinite)||K[0][0]<=0||K[1][1]<=0)throw new Error('缺少有效的原始相机内参');
  const n=camera.near,f=camera.far;
  camera.projectionMatrix.set(2*K[0][0]/w,0,1-2*K[0][2]/w,0, 0,2*K[1][1]/h,2*K[1][2]/h-1,0, 0,0,-(f+n)/(f-n),-2*f*n/(f-n), 0,0,-1,0);
  camera.projectionMatrixInverse.copy(camera.projectionMatrix).invert();
}

export function fitPhoto(width:number,height:number,view:PhotoView){
  if(!view.width||!view.height)return {width,height};
  const scale=Math.min(width/view.width,height/view.height);
  return {width:Math.max(1,Math.round(view.width*scale)),height:Math.max(1,Math.round(view.height*scale))};
}
