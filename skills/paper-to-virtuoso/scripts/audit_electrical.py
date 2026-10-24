"""Compare complete supplied electrical snapshots; geometry is deliberately separate."""
from pathlib import Path
import argparse,json
PORT_MASTERS={('basic','ipin'),('basic','opin'),('basic','iopin')}
def canonical(data,mapping=None):
 mapping=mapping or {};cm=mapping.get('cells',mapping if 'libraries' not in mapping else {});lm=mapping.get('libraries',{})
 cells=data.get('cells',[data]);cells=list(cells.values()) if isinstance(cells,dict) else cells;out={}
 for c in cells:
  name=cm.get(c['cell'],c['cell']);assert name not in out,'Duplicate mapped cell'
  ports={}
  for p in c['ports']:
   assert p['name'] not in ports,'Duplicate port'
   assert isinstance(p.get('net'),str) and p['net'],'Every port needs its actual net name'
   assert p['direction'] in ['input','output','inputOutput'];ports[p['name']]=[p['direction'],p['net']]
  insts={}
  for i in c['instances']:
   if (i['lib'],i['cell']) in PORT_MASTERS:continue
   assert i['name'] not in insts,'Duplicate instance'
   props=i['props'];props={p[0]:p[1:] for p in props} if isinstance(props,list) else props
   terms={}
   for t in i['terms']:
    assert t['name'] not in terms,'Duplicate terminal';assert 'net' in t,'Missing terminal net';terms[t['name']]=t['net']
   insts[i['name']]=dict(lib=lm.get(i['lib'],i['lib']),cell=cm.get(i['cell'],i['cell']),props=props,terms=terms)
  out[name]=dict(ports=ports,instances=insts)
 return out
def compare(before,after,mapping=None):
 a,b=canonical(before,mapping),canonical(after);errors=[]
 for cell in sorted(set(a)|set(b)):
  if cell not in a or cell not in b:errors.append([cell,'cell_added_or_removed']);continue
  if a[cell]['ports']!=b[cell]['ports']:errors.append([cell,'ports_direction_or_net_changed'])
  aa,bb=a[cell]['instances'],b[cell]['instances']
  for n in sorted(set(aa)|set(bb)):
   if n not in aa or n not in bb:errors.append([cell,n,'instance_added_or_removed']);continue
   for k in ['lib','cell','props','terms']:
    if aa[n][k]!=bb[n][k]:errors.append([cell,n,k+'_changed'])
 return dict(supplied_electrical_data_equal=not errors,cells=len(a),differences=errors,scope='Provided masters, properties, terminal nets and port direction/nets only; not a completeness/CDF/GUI/simulation proof')
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--before',type=Path,required=True);p.add_argument('--after',type=Path,required=True);p.add_argument('--cell-map',type=Path);p.add_argument('--report',type=Path);a=p.parse_args()
 try:r=compare(json.loads(a.before.read_text()),json.loads(a.after.read_text()),json.loads(a.cell_map.read_text()) if a.cell_map else None)
 except (AssertionError,KeyError,ValueError) as e:raise SystemExit('Incomplete or invalid snapshot: '+str(e))
 s=json.dumps(r,indent=2)+'\n'
 if a.report:a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(s)
 print(s);raise SystemExit(0 if r['supplied_electrical_data_equal'] else 1)
if __name__=='__main__':main()
