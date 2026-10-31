from pathlib import Path
import json
from PIL import Image,ImageDraw,ImageFont
from scripts.compare_quality import metrics
from webscan.evaluation import validate_evaluation
root=Path('.data/benchmarks/depth-consistency-20261101')
font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',18)
canvas=Image.new('RGB',(1048,806),'#111827');draw=ImageDraw.Draw(canvas)
summary={}
for si,scene in enumerate(['pipes','office']):
 work=root/'jobs'/f'{scene}-baseline'
 result=json.loads((work/'result.json').read_text());r=result['report']
 checked,_=validate_evaluation(json.loads((work/'sfm/scene.json').read_text()),work/'brush',{'steps':3000,'resolution':512,'evaluation_mode':'holdout'},(work/'brush.log').read_text())
 assert checked['evaluation_images']==r['evaluation_images']
 rows=[]
 for v in r['evaluation_images']:
  ref=Image.open(work/'sfm/undistorted/images'/Path(v['name']).with_suffix('.jpg')).convert('RGB').resize((512,341),Image.Resampling.LANCZOS)
  render=Image.open(work/'brush/eval_3000'/v['name']).convert('RGB'); assert render.size==(512,341)
  score=metrics(render,ref)
  rows.append({'view':v['name'],'dimensions':[512,341],**score})
  if v['name'].startswith('0008' if scene=='pipes' else '0016'):
   y=si*403
   draw.text((12,y+8),f'{scene} {v["name"][:4]}: undistorted reference | unchanged Brush baseline',font=font,fill='white')
   canvas.paste(ref,(8,y+36));canvas.paste(render,(528,y+36))
   draw.text((12,y+379),f'512 x 341, 3000 steps; PSNR {score["psnr"]:.4f} dB / SSIM {score["ssim"]:.6f}',font=font,fill='white')
 summary[scene]={'views':rows,'mean':{k:sum(v[k] for v in rows)/len(rows) for k in ['psnr','ssim']}}
(root/'baseline-scores.json').write_text(json.dumps(summary,indent=2)+'\n')
canvas.save('docs/screenshots/188-depth-consistency-baseline.png')
print(json.dumps(summary,indent=2))

# Dense-grid consistency illustration: no inferred points are exported.
import numpy as np
cross=json.loads((root/'cross-view-v2/consistency.json').read_text())
anchors=json.loads((root/'anchors/anchors.json').read_text())['views']
by_key={(v['job'],v['name']):v for v in anchors}
chosen=[('pipes','0004'),('office','0005'),('office','0020')]
canvas=Image.new('RGB',(1048,403*len(chosen)+28),'#111827');draw=ImageDraw.Draw(canvas)
for si,(scene,index) in enumerate(chosen):
 s=next(v for v in cross['scenes'] if v['job']==f'{scene}-baseline')
 v=next(v for v in s['views'] if v['name'].startswith(index));a=by_key[(s['job'],v['name'])];y=si*403
 ref=Image.open(a['image']).convert('RGB').resize((512,341),Image.Resampling.LANCZOS)
 with np.load(root/'cross-view-v2'/v['map']) as data:
  counts,projected=data['support'],data['projected']
  rgb=np.zeros((*counts.shape,3),dtype=np.uint8);rgb[:]=[28,43,65]
  rgb[projected==0]=[104,113,128];rgb[counts==1]=[230,166,59];rgb[counts>=2]=[39,196,234]
 mask=Image.fromarray(rgb).resize((512,341),Image.Resampling.NEAREST)
 canvas.paste(ref,(8,y+36));canvas.paste(mask,(528,y+36))
 draw.text((12,y+8),f'{scene} {index} training photo | cross-view support on 8px grid',font=font,fill='white')
 text=f'>=2 supporters: {v["retained_fraction"]:.2%}; {v["retained_samples"]:,} / {v["valid_samples"]:,} samples'
 draw.text((12,y+379),text,font=font,fill='white')
draw.text((12,403*len(chosen)+3),'Cyan: >=2  |  Amber: 1  |  Navy: 0  |  Gray: no sampleable target  (not depth truth)',font=font,fill='white')
canvas.save('docs/screenshots/189-depth-consistency-support.png')
public={'config':json.loads((root/'config.json').read_text()),'baseline_scores':summary,
        'baseline_runs':[json.loads(p.read_text()) for p in sorted((root/'jobs').glob('*/result.json'))],
        'single_view':json.loads((root/'analysis.json').read_text()),'cross_view':cross}
Path('docs/benchmarks/depth-consistency.json').write_text(json.dumps(public,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
Path('docs/benchmarks/depth-consistency-config.json').write_text((root/'config.json').read_text())
