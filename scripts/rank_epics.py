import sqlite3, json, sys
from datetime import datetime, timedelta
from collections import defaultdict
c=sqlite3.connect('file:%s?mode=ro'%sys.argv[1],uri=True)
P=lambda s: datetime.fromisoformat(s.replace('Z','+00:00')) if s else None
items={r[0]:r for r in c.execute("select id,source_key,type,title,status_category,estimate,created_at,resolved_at from work_item")}
bykey={r[1]:r for r in items.values()}
link_ev=defaultdict(list)  # epic key -> [(at, child_id, op)]
for item,at,frm,to in c.execute("select item_id,at,from_value,to_value from event where field='Epic Link' order by at"):
    if to: link_ev[to].append((at,item,'add'))
    if frm: link_ev[frm].append((at,item,'rm'))
est_changes=defaultdict(int)
for (item,) in c.execute("select item_id from event where kind='estimate_changed'"): est_changes[item]+=1
reopen=defaultdict(int)
for item,frm,to in c.execute("select item_id,from_value,to_value from event where kind='status_changed'"):
    if frm and to and frm.lower() in ('done','closed','resolved') and to.lower() not in ('done','closed','resolved'): reopen[item]+=1
blocks=defaultdict(int)
for f,t in c.execute("select from_id,to_id from link where kind='blocks'"): blocks[f]+=1; blocks[t]+=1
out=[]
for key,ev in link_ev.items():
    e=bykey.get(key)
    if not e or e[2]!='epic': continue
    created=P(e[6]); resolved=P(e[7])
    members=set(); day1=set()
    for at,item,op in ev:
        (members.add if op=='add' else members.discard)(item)
        if P(at)<=created+timedelta(days=7) and op=='add': day1.add(item)
    if len(members)<5: continue
    kids=[items[i] for i in members if i in items]
    pts=sum(k[5] or 0 for k in kids)
    ends=[P(k[7]) for k in kids if k[7]]
    last=max(ends) if ends else None
    out.append(dict(epic=key,title=e[3],status=e[4],created=e[6][:10],epic_resolved=(e[7] or '')[:10],
      last_child_resolved=last.isoformat()[:10] if last else '',
      span_days=(last-created).days if last else None,
      children=len(members),first_week=len(day1),added_later=len(members-day1),
      points=pts,re_estimated=sum(est_changes[i] for i in members),reopened=sum(reopen[i] for i in members),
      blocks_links=sum(blocks[i] for i in members)))
for o in out: o['score']=o['added_later']*2+o['re_estimated']+o['reopened']*2+o['blocks_links']*2
out.sort(key=lambda o:-o['score'])
json.dump(out,open(sys.argv[2],'w'),indent=1)
for o in out[:15]: print(o)
print(len(out),'epics with 5+ children')
