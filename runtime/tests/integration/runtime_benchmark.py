#!/usr/bin/env python3
"""Compare repeated image-cached deploys on the same host with isolated sites."""
import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline',required=True)
    parser.add_argument('--candidate',required=True)
    parser.add_argument('--php',default='8.4')
    parser.add_argument('--repeats',type=int,default=5)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.repeats<5:parser.error('At least five samples are required')
    args.output.mkdir(parents=True,exist_ok=True)
    results={'baseline':[],'candidate':[]}
    for sample in range(args.repeats):
        groups=['baseline','candidate'] if sample%2==0 else ['candidate','baseline']
        for group in groups:
            reference=getattr(args,group)
            path=args.output/f'{group}-{sample+1}.json'
            with (args.output/f'{group}-{sample+1}.log').open('w') as stream:
                subprocess.run([sys.executable,str(Path(__file__).with_name('runtime_smoke.py')),
                    '--image',reference,'--php',args.php,'--benchmark-only','--report',str(path)],
                    stdout=stream,stderr=subprocess.STDOUT,check=True)
            results[group].append(json.loads(path.read_text()))
            print(group+' '+str(sample+1)+': '+str(round(results[group][-1]['deploy_seconds'],2))+'s',flush=True)
    for group in results:
        if len({r['image_id'] for r in results[group]})!=1:
            raise RuntimeError('Image identity changed during benchmark')
    if len({r['wordpress_version'] for group in results.values() for r in group})!=1:
        raise RuntimeError('The WordPress versions must match for a valid comparison')
    baseline=statistics.median(r['deploy_seconds'] for r in results['baseline'])
    candidate=statistics.median(r['deploy_seconds'] for r in results['candidate'])
    report={'baseline_median':baseline,'candidate_median':candidate,'ratio':candidate/baseline,
            'result':'passed' if candidate<=baseline*1.15 else 'regressed','samples':results}
    (args.output/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='samples'},indent=2))
    return 0 if report['result']=='passed' else 1

if __name__=='__main__':raise SystemExit(main())
