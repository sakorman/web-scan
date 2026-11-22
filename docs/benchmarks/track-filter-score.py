"""Score every fixed pair, preserving failed screening results and per-view metrics."""
import argparse
import json
from pathlib import Path
import re
import statistics
import torch
from scripts.compare_quality import compare


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();root=args.root
    complete=json.loads((root/'complete.json').read_text());assert complete['runs']==8
    runs=json.loads((root/'results.json').read_text());plan=json.loads((root/'plan.json').read_text())
    torch.set_num_threads(4);result=dict(plan=plan,runs=runs,scenes={})
    for r in runs:
        previous=next(x for x in runs if (x['scene'],x['variant'])==(r['scene'],r['variant']))
        assert r['sparse_sha256']==previous['sparse_sha256'], 'Seed model changed between repeats'
        log=(root/'jobs'/r['name']/'brush.log').read_text()
        seed=re.findall(r'Starting from colmap points (\d+)',log)
        assert seed==[str(r['filtering']['retained_points'])], 'Trainer seed count mismatch'
        assert r['report']['engine_sha256']==plan['engine_sha256']
        assert r['report']['training_seed']==42
    for scene in ('pipes','office'):
        pairs=[]
        for repeat in (1,2):
            score=compare(root,f'{scene}-baseline-{repeat}',f'{scene}-candidate-{repeat}',512,error_grid=4)
            pairs.append(dict(repeat=repeat,scores=score))
            print(scene,repeat,score['mean'])
        means={m:statistics.mean(p['scores']['mean']['detail'][m]-p['scores']['mean']['quick'][m] for p in pairs) for m in ('psnr','ssim')}
        pair_safe=all(p['scores']['mean']['detail']['psnr']-p['scores']['mean']['quick']['psnr']>=-.2
                      and p['scores']['mean']['detail']['ssim']-p['scores']['mean']['quick']['ssim']>=-.005 for p in pairs)
        passed=pair_safe and means['psnr']>=.2 and means['ssim']>=0
        result['scenes'][scene]=dict(pairs=pairs,mean_delta=means,passed=passed)
        print(scene,means,'passed',passed)
    result['passed']=all(s['passed'] for s in result['scenes'].values())
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
