import assert from 'node:assert/strict';
import test from 'node:test';
import { PerspectiveCamera, Vector3 } from 'three';
import { applyPhotoPose, applyPhotoProjection, fitPhoto } from '../.qa/unit/camera.js';

const K=[[800,0,310],[0,600,220],[0,0,1]];
function pixel(camera,point){const p=new Vector3(...point).project(camera);return [(p.x+1)*320,(1-p.y)*240];}
function near(actual,expected){actual.forEach((x,i)=>assert.ok(Math.abs(x-expected[i])<1e-6,`${actual} != ${expected}`));}
test('COLMAP photo projects to the same pixels with off-center intrinsics',()=>{
 const camera=new PerspectiveCamera();const view={view:[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]],K,width:640,height:480};
 applyPhotoPose(camera,view);applyPhotoProjection(camera,view);
 near(pixel(camera,[1,.5,4]),[510,295]);
 near(pixel(camera,[0,0,4]),[310,220]);
});
test('rotated translated camera preserves the photo axes and camera center',()=>{
 const camera=new PerspectiveCamera();const view={view:[[0,-1,0,1],[1,0,0,-2],[0,0,1,1],[0,0,0,1]],K,width:640,height:480};
 applyPhotoPose(camera,view);applyPhotoProjection(camera,view);
 near(camera.position.toArray(),[2,1,-1]);near(pixel(camera,[1,.5,4]),[390,100]);
});
test('reference comparison fits the full photo without cropping on portrait screens',()=>{
 assert.deepEqual(fitPhoto(390,600,{width:1600,height:1000}),{width:390,height:244});
 assert.deepEqual(fitPhoto(1000,400,{width:1600,height:1000}),{width:640,height:400});
});
test('invalid calibration fails instead of displaying a fabricated camera',()=>{
 const camera=new PerspectiveCamera();assert.throws(()=>applyPhotoProjection(camera,{K,width:0,height:480}));
 assert.throws(()=>applyPhotoPose(camera,{view:Array.from({length:4},()=>[0,0,0,0])}));
});
