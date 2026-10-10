import assert from 'node:assert/strict';
import test from 'node:test';
import { ReconstructionDrafts, defaultDraft, matchesJobTarget, sampleVideoTimes } from '../.qa/unit/session-state.js';

test('editing a new run survives a rerender with the old completed job config', () => {
  const drafts = new ReconstructionDrafts();
  const oldJob = { backend: 'brush', steps: 6000, max_points: 150000 };
  drafts.get('room', oldJob);
  drafts.switchBackend('room', 'reference');
  drafts.save('room', { backend: 'reference', steps: 60, max_points: 192 });
  assert.deepEqual(drafts.get('room', oldJob), { backend: 'reference', steps: 60, max_points: 192 });
});

test('projects and backend modes keep independent drafts', () => {
  const drafts = new ReconstructionDrafts();
  drafts.save('a', { backend: 'brush', steps: 6000, max_points: 300000 });
  drafts.switchBackend('a', 'reference');
  drafts.save('a', { backend: 'reference', steps: 60, max_points: 192 });
  assert.deepEqual(drafts.get('b', defaultDraft('reference')), defaultDraft('reference'));
  assert.equal(drafts.switchBackend('a', 'brush').steps, 6000);
  assert.equal(drafts.switchBackend('a', 'reference').steps, 60);
});

test('returned drafts cannot mutate saved settings', () => {
  const drafts = new ReconstructionDrafts();
  const exposed = drafts.get('a', defaultDraft('reference'));
  exposed.steps = 999;
  assert.equal(drafts.get('a', defaultDraft('reference')).steps, 120);
});

test('a delayed response cannot update another project or a newer run', () => {
  const request = { sessionId: 'room-a', jobId: 'job-1' };
  assert.equal(matchesJobTarget(request, request), true);
  assert.equal(matchesJobTarget(request, null), false);
  assert.equal(matchesJobTarget(request, { sessionId: 'room-b', jobId: 'job-1' }), false);
  assert.equal(matchesJobTarget(request, { sessionId: 'room-a', jobId: 'job-2' }), false);
});

test('video sampling respects duration and available frame slots',()=>{
  assert.equal(sampleVideoTimes(12,200).length,12);
  assert.equal(sampleVideoTimes(300,200).length,120);
  const times=sampleVideoTimes(20,3);
  assert.equal(times.length,3);
  assert.ok(times.every((t,i)=>t>=0&&t<20&&(i===0||t>times[i-1])));
  assert.deepEqual(sampleVideoTimes(.03,20),[0]);
  for(const duration of [0,Infinity,NaN,301])assert.throws(()=>sampleVideoTimes(duration,20));
  assert.throws(()=>sampleVideoTimes(10,0));
});
