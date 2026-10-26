from __future__ import annotations
import json
from pathlib import Path
from zoneinfo import ZoneInfo


def load(path):
    path=Path(path).resolve()
    cfg=json.loads(path.read_text(encoding='utf-8-sig'))
    ZoneInfo(cfg['timezone'])
    base=path.parent.parent
    for key in ('database','skill_lab'):
        p=Path(cfg[key])
        cfg[key]=str(p if p.is_absolute() else base/p)
    b=cfg['budgets']
    b.setdefault('daily_replies',100)
    for key,minimum,maximum in [('turn_tokens',500,24000),('daily_tokens',500,5280000),('daily_model_calls',1,220),('daily_replies',1,100),('turn_seconds',10,180),('tools_per_turn',0,5),('input_chars',4000,40000)]:
        if type(b[key]) is not int or not minimum<=b[key]<=maximum:raise ValueError('invalid budget '+key)
    a=cfg['autonomy']
    if len(a['windows'])>2 or not 1<=a['window_minutes']<=60 or not 0<=a['max_shares_per_day']<=3:
        raise ValueError('autonomy frequency exceeds prototype boundary')
    for clock in [a['quiet_start'],a['quiet_end'],*a['windows']]:
        h,m=map(int,clock.split(':'))
        if not 0<=h<24 or not 0<=m<60:raise ValueError('invalid schedule time')
    transport=cfg['transport']
    allowed={f"onebot:{transport['bot_id']}:private:{uid}" for uid in transport['allowed_private']}
    allowed|={f"onebot:{transport['bot_id']}:group:{gid}" for gid in transport['allowed_groups']}
    if any(scope not in allowed for scope in a['scopes']):raise ValueError('autonomy scope must be explicitly allowed for transport')
    if len({s['id'] for s in cfg['sources']})!=len(cfg['sources']):raise ValueError('duplicate source id')
    return cfg
