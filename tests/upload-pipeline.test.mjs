import assert from 'node:assert/strict';
import test from 'node:test';
import { UploadPipeline } from '../.qa/unit/upload-pipeline.js';

const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('three uploads overlap while producers stop at the queue capacity',async()=>{
  let active=0,peak=0;const release=new Map(),seen=[];
  const pool=new UploadPipeline(async item=>{
    active++;peak=Math.max(peak,active);seen.push(item);
    await new Promise(resolve=>release.set(item,resolve));active--;
  },3,4);
  for(let i=0;i<4;i++)await pool.submit(i);
  await tick();assert.equal(peak,3);assert.equal(pool.pending,4);
  let accepted=false;const blocked=pool.submit(4).then(()=>accepted=true);
  await tick();assert.equal(accepted,false);
  release.get(0)();await blocked;await tick();
  assert.equal(pool.pending,4);assert.ok(seen.includes(3));
  for(let i=1;i<=4;i++){await tick();release.get(i)();}
  await pool.finish();assert.equal(pool.pending,0);assert.equal(peak,3);
});

test('failures wait for in-flight requests and leave unstarted work for recovery',async()=>{
  const durable=new Set([0,1,2,3]);let releaseOther;const started=[];
  const pool=new UploadPipeline(async item=>{
    started.push(item);
    if(item===0)throw new Error('network interrupted');
    await new Promise(resolve=>releaseOther=resolve);durable.delete(item);
  },2,4);
  await pool.submit(0);await pool.submit(1);
  await assert.rejects(async()=>{await tick();await pool.submit(2);},/network interrupted/);
  let finished=false;const ending=pool.finish().catch(e=>{finished=true;return e;});
  await tick();assert.equal(finished,false);
  releaseOther();assert.match((await ending).message,/network interrupted/);
  assert.deepEqual(started,[0,1]);assert.deepEqual([...durable],[0,2,3]);
});

test('finish waits until every accepted item completes',async()=>{
  const completed=[];
  const pool=new UploadPipeline(async value=>{await tick();completed.push(value);},2,3);
  for(let i=0;i<12;i++)await pool.submit(i);
  await pool.finish();assert.deepEqual(completed,[...Array(12).keys()]);
});
