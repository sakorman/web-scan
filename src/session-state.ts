export type Backend = 'reference' | 'cuda' | 'brush';
export type ReconstructionDraft = { backend: Backend; steps: number; max_points: number };

export function defaultDraft(backend: Backend): ReconstructionDraft {
  return { backend, steps: backend === 'brush' ? 3000 : 120, max_points: backend === 'brush' ? 150000 : 384 };
}

/** A rerender or a background job response must not overwrite the user's next run. */
export class ReconstructionDrafts {
  private sessions = new Map<string, { backend: Backend; modes: Map<Backend, ReconstructionDraft> }>();

  get(session: string, initial: ReconstructionDraft): ReconstructionDraft {
    let entry = this.sessions.get(session);
    if (!entry) {
      entry = { backend: initial.backend, modes: new Map([[initial.backend, { ...initial }]]) };
      this.sessions.set(session, entry);
    }
    return { ...entry.modes.get(entry.backend)! };
  }

  save(session: string, draft: ReconstructionDraft): void {
    this.get(session, draft);
    const entry = this.sessions.get(session)!;
    entry.backend = draft.backend;
    entry.modes.set(draft.backend, { ...draft });
  }

  switchBackend(session: string, backend: Backend): ReconstructionDraft {
    const entry = this.sessions.get(session);
    const draft = entry?.modes.get(backend) ?? defaultDraft(backend);
    this.save(session, draft);
    return { ...draft };
  }
}

export type JobTarget = { sessionId: string; jobId: string };
export function matchesJobTarget(expected: JobTarget, current: JobTarget | null): boolean {
  return expected.sessionId === current?.sessionId && expected.jobId === current?.jobId;
}

export function sampleVideoTimes(duration:number, slots:number):number[]{
  if(!Number.isFinite(duration)||duration<=0||duration>300)throw new Error('请使用 5 分钟以内的有效视频');
  if(!Number.isInteger(slots)||slots<1)throw new Error('照片数量已达到上限');
  const count=Math.min(120,slots,Math.max(1,Math.ceil(duration)));
  const end=Math.max(0,duration-.08);
  return Array.from({length:count},(_,index)=>count===1?0:Math.min(end,index*duration/count));
}
