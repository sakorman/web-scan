from pathlib import Path
import json
from PIL import Image,ImageDraw,ImageFont
from scripts.compare_quality import metrics
from webscan.evaluation import validate_evaluation
root=Path('.data/benchmarks/depth-prior-20261101')
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
canvas.save('docs/screenshots/186-depth-prior-baseline.png')
print(json.dumps(summary,indent=2))

# Sparse-anchor diagnostic: colors mark the predetermined spatial split.
import numpy as np
from scripts.benchmark_depth_prior import fit_cells, sample_depth
analysis=json.loads((root/'analysis.json').read_text())
anchors=json.loads((root/'anchors-v2/anchors.json').read_text())['views']
by_key={(v['job'],v['name']):v for v in anchors}
rows=[]
for scene,index in [('pipes','0006'),('office','0004'),('office','0019')]:
 row=next(v for v in analysis['views'] if v['job']==f'{scene}-baseline' and v['name'].startswith(index))
 rows.append(row)
canvas=Image.new('RGB',(1048,403*len(rows)),'#111827');draw=ImageDraw.Draw(canvas)
for si,row in enumerate(rows):
 a=by_key[(row['job'],row['name'])];y=si*403
 ref=Image.open(a['image']).convert('RGB').resize((512,341),Image.Resampling.LANCZOS)
 pred=next(v for v in analysis['inference']['views'] if v['job']==row['job'] and v['name']==row['name'])
 depth=np.load(root/'predictions'/pred['prediction'])
 relative=((depth-depth.min())/(depth.max()-depth.min())*255).astype(np.uint8)
 relative=Image.fromarray(relative).convert('RGB').resize((512,341),Image.Resampling.LANCZOS)
 canvas.paste(ref,(8,y+36));canvas.paste(relative,(528,y+36))
 xy=np.asarray(a['xy']);samples,inside=sample_depth(depth,xy)
 z=np.asarray(a['z']);xy=xy[inside & np.isfinite(samples) & np.isfinite(z) & (z>0)]
 fit=fit_cells(xy,a['width'],a['height'])
 for (x0,y0),is_fit in zip(xy,fit):
  x1=8+x0/a['width']*512;y1=y+36+y0/a['height']*341
  draw.ellipse((x1-2,y1-2,x1+2,y1+2),fill='#28e5ec' if is_fit else '#ff718f')
 for k in range(1,4):
  draw.line((8+128*k,y+36,8+128*k,y+377),fill='#9ba8b8',width=1)
  draw.line((8,y+36+341*k/4,520,y+36+341*k/4),fill='#9ba8b8',width=1)
 draw.text((12,y+8),f'{row["job"]} {row["name"][:4]} | training photo + anchors | relative prediction (unitless)',font=font,fill='white')
 message=f'Cyan: fit {row["fit_anchors"]}; pink: check {row["check_anchors"]}'
 if 'check' in row: message+=f'; check median {row["check"]["median_abs_rel"]:.1%}, p90 {row["check"]["p90_abs_rel"]:.1%}'
 else: message+='; insufficient spatial anchors'
 draw.text((12,y+379),message,font=font,fill='white')
canvas.save('docs/screenshots/187-depth-prior-screening.png')
public={"config":json.loads((root/'config.json').read_text()),"baseline_scores":summary,
        "baseline_runs":[json.loads(p.read_text()) for p in sorted((root/'jobs').glob('*/result.json'))],
        "depth_screening":analysis}
Path('docs/benchmarks/depth-prior.json').write_text(json.dumps(public,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
Path('docs/benchmarks/depth-prior-config.json').write_text((root/'config.json').read_text())
