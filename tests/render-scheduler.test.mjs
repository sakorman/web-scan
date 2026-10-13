import test from 'node:test';
import assert from 'node:assert/strict';
import {RenderScheduler} from '../.qa/unit/render-scheduler.js';

function clock(){
 const queued=new Map();let next=0;
 return {request:cb=>{queued.set(++next,cb);return next;},cancel:id=>queued.delete(id),size:()=>queued.size,
 tick:()=>{const callbacks=[...queued.values()];queued.clear();callbacks.forEach(cb=>cb(0));}};
}
test('static frames stop and independent invalidations coalesce',()=>{
 const c=clock();let renders=0;const frames=new RenderScheduler(()=>{renders++;},c.request,c.cancel);
 frames.invalidate();frames.invalidate();frames.invalidate();assert.equal(c.size(),1);
 c.tick();assert.equal(renders,1);assert.equal(c.size(),0);
 c.tick();assert.equal(renders,1);
 frames.invalidate();c.tick();assert.equal(renders,2);
});
test('async completion during rendering is not lost',()=>{
 const c=clock();let renders=0;
 const frames=new RenderScheduler(()=>{if(++renders===1)frames.invalidate();},c.request,c.cancel);
 frames.invalidate();c.tick();assert.equal(c.size(),1);c.tick();assert.equal(renders,2);assert.equal(c.size(),0);
});
test('continuous motion runs only until the control becomes idle',()=>{
 const c=clock();let renders=0,moving=true;
 const frames=new RenderScheduler(()=>{renders++;return moving;},c.request,c.cancel);
 frames.invalidate();c.tick();c.tick();moving=false;c.tick();c.tick();assert.equal(renders,3);assert.equal(c.size(),0);
});
test('hidden or photo-covered views cancel work and repaint on return',()=>{
 const c=clock();let renders=0;const frames=new RenderScheduler(()=>{renders++;},c.request,c.cancel);
 frames.invalidate();frames.setPaused(true);frames.invalidate();c.tick();assert.equal(renders,0);
 frames.setPaused(false);c.tick();assert.equal(renders,1);assert.equal(c.size(),0);
});
test('dispose cancels pending and late worker callbacks cannot restart rendering',()=>{
 const c=clock();let renders=0;const frames=new RenderScheduler(()=>{renders++;return true;},c.request,c.cancel);
 frames.invalidate();frames.dispose();frames.invalidate();frames.setPaused(false);c.tick();assert.equal(renders,0);assert.equal(c.size(),0);
});
test('native animation APIs keep their window receiver',()=>{
 const previousRequest=globalThis.requestAnimationFrame,previousCancel=globalThis.cancelAnimationFrame;
 let cancelled=false;
 globalThis.requestAnimationFrame=function(){assert.equal(this,globalThis);return 7;};
 globalThis.cancelAnimationFrame=function(id){assert.equal(this,globalThis);assert.equal(id,7);cancelled=true;};
 try{const frames=new RenderScheduler(()=>{});frames.invalidate();frames.dispose();assert.equal(cancelled,true);}
 finally{globalThis.requestAnimationFrame=previousRequest;globalThis.cancelAnimationFrame=previousCancel;}
});
