"""Render fixed-view comparison and diagnostic sparse-track coverage figures."""
from pathlib import Path
import json
from PIL import Image,ImageDraw

result=json.loads(Path('docs/benchmarks/track-observations.json').read_text())
for scene,e in result['scenes'].items():
 old,new=e['quality']['runs'];audit=e['audits']['new']['exclude-image']
 work=Path('.data/jobs')/new['job_id'];filename=Path(audit['focus_image']).with_suffix('.png')
 with Image.open(work/'sfm/undistorted/images'/audit['focus_image']) as im:
  ref=im.convert('RGB');ref.thumbnail((512,512),Image.Resampling.LANCZOS)
 images=[ref]
 for run in (old,new):
  with Image.open(Path('.data/jobs')/run['job_id']/'brush/eval_3000'/filename) as im:images.append(im.convert('RGB'))
 assert all(im.size==(512,341) for im in images)
 canvas=Image.new('RGB',(1536,369),'#081627');draw=ImageDraw.Draw(canvas)
 for i,(im,label) in enumerate(zip(images,['Reference','Baseline Nov 21','Unchanged training control Nov 23'])):
  canvas.paste(im,(512*i,28));draw.text((512*i+10,8),label,fill='white')
 canvas.save(f'docs/screenshots/track-observations-{scene}.png')
 overlay=ref.copy();draw=ImageDraw.Draw(overlay)
 recovered={r['point_id'] for r in audit['recovered_tracks']};w,h=audit['focus_dimensions']
 for pt in audit['focus_points']:
  x,y=pt['xy'];x=(x-.5)*512/w;y=(y-.5)*341/h
  color='#ffe000' if pt['point_id'] in recovered else '#00ffff'
  draw.ellipse((x-2,y-2,x+2,y+2),outline=color,width=1)
 for i in range(1,4):
  draw.line((i*128,0,i*128,341),fill='#2266bb');draw.line((0,int(i*341/4),512,int(i*341/4)),fill='#2266bb')
 for row in range(4):
  for col in range(4):
   x,y=128*col+4,int(row*341/4)+4
   draw.rectangle((x,y,x+28,y+13),fill='#081627');draw.text((x+2,y),str(e['focus_grid_4x4'][row][col]),fill='white')
 canvas=Image.new('RGB',(1024,369),'#081627');draw=ImageDraw.Draw(canvas)
 for i,(im,label) in enumerate([(ref,'Reference (display only)'),(overlay,'Cyan: existing; yellow: recovered; labels: total tracks')]):
  canvas.paste(im,(512*i,28));draw.text((512*i+10,8),label,fill='white')
 canvas.save(f'docs/screenshots/track-observations-{scene}-coverage.png')
