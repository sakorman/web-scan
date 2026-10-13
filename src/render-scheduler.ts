/** Coalesce camera, resize and asynchronous splat updates into requested frames. */
export class RenderScheduler {
  private pending:number|null=null;
  private paused=false;
  private disposed=false;

  constructor(
    private draw:()=>boolean|void,
    private request:(callback:FrameRequestCallback)=>number=callback=>globalThis.requestAnimationFrame(callback),
    private cancel:(id:number)=>void=id=>globalThis.cancelAnimationFrame(id),
  ){}

  invalidate=()=>{
    if(this.disposed||this.paused||this.pending!==null)return;
    this.pending=this.request(()=>{
      this.pending=null;
      if(this.disposed||this.paused)return;
      if(this.draw())this.invalidate();
    });
  };

  setPaused(paused:boolean){
    if(this.disposed||this.paused===paused)return;
    this.paused=paused;
    if(paused)this.cancelPending();else this.invalidate();
  }

  private cancelPending(){
    if(this.pending!==null){this.cancel(this.pending);this.pending=null;}
  }

  dispose(){this.disposed=true;this.cancelPending();}
}
