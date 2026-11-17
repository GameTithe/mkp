"""Run the full unittest suite in isolated subprocess shards.
Default unittest discovery also works. Parallelism changes scheduling only.
"""
from __future__ import annotations
import argparse, concurrent.futures, json, os, subprocess, sys, time, unittest
from pathlib import Path


def flatten(suite):
    for item in suite:
        if isinstance(item,unittest.TestSuite):yield from flatten(item)
        else:yield item

def main():
    root=Path(__file__).resolve().parents[1]
    p=argparse.ArgumentParser();p.add_argument('--workers',type=int,default=4);p.add_argument('--output',required=True);p.add_argument('--worker',type=int)
    args=p.parse_args();dest=Path(args.output).resolve();dest.mkdir(parents=True,exist_ok=True)
    if not 1<=args.workers<=8:raise ValueError('workers must be 1..8')
    sys.path.insert(0,str(root));sys.path.insert(0,str(root/'tests'))
    tests=list(flatten(unittest.defaultTestLoader.discover(str(root/'tests'))))
    if args.worker is not None:
        selected=[t for i,t in enumerate(tests) if i%args.workers==args.worker]
        start=time.time()
        with (dest/f'shard_{args.worker}.txt').open('w',encoding='utf-8') as out:
            result=unittest.TextTestRunner(stream=out,verbosity=2).run(unittest.TestSuite(selected))
        data={'shard':args.worker,'tests_run':result.testsRun,'passed':result.wasSuccessful(),'skipped':result.skipped,
              'failures':[(x.id(),err) for x,err in result.failures], 'errors':[(x.id(),err) for x,err in result.errors],
              'elapsed_seconds':round(time.time()-start,3),'test_ids':[x.id() for x in selected]}
        (dest/f'shard_{args.worker}.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        return 0 if result.wasSuccessful() else 1
    start=time.time()
    def run(n):
        proc=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--workers',str(args.workers),'--worker',str(n),'--output',str(dest)],cwd=root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8')
        if proc.stderr:(dest/f'shard_{n}_stderr.txt').write_text(proc.stderr,encoding='utf-8')
        return proc.returncode
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:codes=list(pool.map(run,range(args.workers)))
    results=[json.loads((dest/f'shard_{n}.json').read_text()) for n in range(args.workers)]
    ids=[i for r in results for i in r['test_ids']]
    summary={'passed':not any(codes) and len(ids)==len(set(ids))==len(tests),'discovered':len(tests),'tests_run':sum(x['tests_run'] for x in results),
             'skipped':sum(len(x['skipped']) for x in results),'failures':sum(len(x['failures']) for x in results),'errors':sum(len(x['errors']) for x in results),
             'workers':args.workers,'elapsed_seconds':round(time.time()-start,3),'python':sys.version,'platform':sys.platform,
             'live_llm':False,'note':'Isolated processes; test doubles validate contracts, not LLM accuracy.'}
    (dest/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(summary,ensure_ascii=False,indent=2))
    return 0 if summary['passed'] else 1

if __name__=='__main__':sys.exit(main())
