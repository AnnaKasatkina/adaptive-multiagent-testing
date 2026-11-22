import argparse
import asyncio
import json
from pathlib import Path

from . import METHODS
from .session import run_session, export_trial
from .spaces import context


def main():
    p=argparse.ArgumentParser(description='Адаптивное тестирование: продолжения, параметры, топология')
    sub=p.add_subparsers(dest='command',required=True)
    run=sub.add_parser('run')
    run.add_argument('--family',choices=['schedule','parameters','topology'],default='schedule')
    run.add_argument('--method',choices=list(METHODS)+['logical','feedback','guided'],default='combined')
    run.add_argument('--graph',choices=['ring','bridge','directed'],default='bridge')
    run.add_argument('--algorithm',choices=['homework','raps'],default='homework')
    run.add_argument('--regime',choices=['fast','window'],default='window')
    run.add_argument('--variant',choices=['individual','phase','agent_episode','link_episode','joint_episode'],default='individual')
    run.add_argument('--directed',action='store_true')
    run.add_argument('--joint',action='store_true')
    run.add_argument('--seed',type=int,default=0)
    run.add_argument('--budget',type=int,default=100)
    run.add_argument('--output',type=Path,required=True)
    export=sub.add_parser('export')
    export.add_argument('directory',type=Path)
    export.add_argument('--trial',type=int)
    export.add_argument('--output',type=Path)
    export.add_argument('--all-archive',action='store_true')
    a=p.parse_args()
    if a.command=='export':
        ids=json.loads((a.directory/'summary.json').read_text(encoding='utf-8'))['archive'] if a.all_archive else [a.trial]
        for trial in ids: asyncio.run(export_trial(a.directory,trial,a.output))
        print('Экспорт и проверка завершены')
        return
    if not 1<=a.budget<=100: p.error('Бюджет: 1…100')
    allowed=METHODS if a.family=='schedule' else ('logical','independent','feedback','guided') if a.family=='parameters' else ('independent','feedback')
    if a.method not in allowed: p.error(f'Для {a.family} доступны: {", ".join(allowed)}')
    job=dict(configuration=context(a.graph,a.algorithm,a.regime),family=a.family,method=a.method,variant=a.variant,
             directed=a.directed,topology_mode='joint' if a.joint else 'graph',seed=a.seed,budget=a.budget,directory=str(a.output))
    summary=run_session(job)
    if summary['best_trial']: asyncio.run(export_trial(a.output,summary['best_trial']))
    print(json.dumps(summary,ensure_ascii=False))


if __name__=='__main__': main()
