"""Make aligned reference/baseline/candidate panels from saved evaluation images."""
import argparse
import json
from pathlib import Path
from PIL import Image,ImageDraw

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
args=p.parse_args();args.output.mkdir(parents=True,exist_ok=True)
for scene,rows,suffix in [('pipes',[(1,8),(2,8)],'pipes'),('office',[(1,8),(2,8)],'office'),
                          ('office',[(1,24),(2,16)],'office-regression')]:
 canvas=Image.new('RGB',(1536,738),'#081627');draw=ImageDraw.Draw(canvas)
 for row,(repeat,index) in enumerate(rows):
  base=args.root/'jobs'/f'{scene}-baseline-{repeat}';candidate=args.root/'jobs'/f'{scene}-candidate-{repeat}'
  scene_data=json.loads((base/'sfm/scene.json').read_text());view=sorted(scene_data['views'],key=lambda v:v['image_name'])[index]
  filename=Path(view['image_name']).with_suffix('.png').name
  with Image.open(base/'sfm/undistorted/images'/view['image_name']) as im:
   ref=im.convert('RGB');ref.thumbnail((512,512),Image.Resampling.LANCZOS)
  images=[ref]
  for work in (base,candidate):
   with Image.open(work/'brush/eval_3000'/filename) as im:images.append(im.convert('RGB'))
  assert all(im.size==(512,341) for im in images)
  labels=[f'Reference | view {index} | repeat {repeat}','Baseline: all SfM seeds','Candidate: duplicate-image tracks removed']
  for col,(im,label) in enumerate(zip(images,labels)):
   canvas.paste(im,(col*512,row*369+28));draw.text((col*512+10,row*369+8),label,fill='white')
 canvas.save(args.output/f'track-filter-{suffix}.png')
