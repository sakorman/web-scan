/** Bounded producer/consumer queue. The caller persists work before submitting it. */
export class UploadPipeline<T> {
  private queued:T[] = [];
  private running = 0;
  private failed:unknown = null;
  private waiters:(()=>void)[] = [];

  constructor(private consume:(value:T)=>Promise<void>, private concurrency=3, private capacity=8) {
    if(!Number.isInteger(concurrency)||concurrency<1||capacity<concurrency)throw new Error('Invalid upload limits');
  }

  get pending(){return this.queued.length+this.running;}

  async submit(value:T):Promise<void> {
    while(this.pending>=this.capacity&&!this.failed)await this.changed();
    if(this.failed)throw this.failed;
    this.queued.push(value);
    this.pump();
  }

  /** Wait for all active requests even after a failure; queued work remains durable. */
  async finish():Promise<void> {
    while(this.running>0||(!this.failed&&this.queued.length>0))await this.changed();
    if(this.failed)throw this.failed;
  }

  private changed():Promise<void> {return new Promise(resolve=>this.waiters.push(resolve));}

  private notify(){const waiters=this.waiters;this.waiters=[];waiters.forEach(wake=>wake());}

  private pump(){
    while(!this.failed&&this.running<this.concurrency&&this.queued.length){
      const value=this.queued.shift()!;
      this.running++;
      Promise.resolve().then(()=>this.consume(value)).catch(error=>{this.failed=error instanceof Error?error:new Error(String(error));})
        .finally(()=>{this.running--;this.pump();this.notify();});
    }
  }
}
