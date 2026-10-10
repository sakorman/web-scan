export type PendingFrame = { key: string; session: string; blob: Blob; metadata: Record<string, unknown> };
const db = new Promise<IDBDatabase>((resolve, reject) => {
  const request = indexedDB.open('web-scan-uploads', 1);
  request.onupgradeneeded = () => request.result.createObjectStore('frames', { keyPath: 'key' });
  request.onsuccess = () => resolve(request.result);
  request.onerror = () => reject(request.error);
});
export async function pending(session: string): Promise<PendingFrame[]> {
  const database = await db;
  return new Promise((resolve, reject) => {
    const req = database.transaction('frames').objectStore('frames').getAll();
    req.onsuccess = () => resolve(req.result.filter((x: PendingFrame) => x.session === session));
    req.onerror = () => reject(req.error);
  });
}
async function mutate(action: (store: IDBObjectStore) => void) {
  const database = await db;
  return new Promise<void>((resolve,reject) => {
    const tx = database.transaction('frames','readwrite');
    action(tx.objectStore('frames')); tx.oncomplete=()=>resolve(); tx.onerror=()=>reject(tx.error);
  });
}
export async function enqueue(frame: PendingFrame) {
  const current = await pending(frame.session);
  if (current.length >= 20 || current.reduce((n,x)=>n+x.blob.size,0)+frame.blob.size > 64*1024*1024) throw new Error('待上传照片已达缓存上限，请恢复上传后继续。');
  await mutate(store=>store.put(frame));
}
export async function acknowledge(key: string) { await mutate(store=>store.delete(key)); }
