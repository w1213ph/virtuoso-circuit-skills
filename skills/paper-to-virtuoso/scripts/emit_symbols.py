"""Emit native SKILL for a new isolated library. Never connects or overwrites a library."""
from pathlib import Path
import argparse,json,re
from symbol_data import load
def q(s):return json.dumps(s,ensure_ascii=False)
def point(x,y):return 'list(%.12g %.12g)'%(x,y)
def bbox(a):return 'list('+point(a[0],a[1])+' '+point(a[2],a[3])+')'
def emit(library,lib_path,tech,d):
 assert re.fullmatch(r'[A-Z][A-Z0-9_]*',library)
 assert re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',tech)
 assert lib_path.startswith('/') and '..' not in lib_path.split('/')
 out=['; Generated reference symbols only; no analog schematic or simulation.',
 'procedure(vcsCreateSymbols() let((lib cv net term fig cnt)\nwhen(ddGetObj('+q(library)+') error("Target library already exists; refusing to replace it"))\nwhen(isDir('+q(lib_path)+') error("Target directory already exists"))\nunless(ddGetObj('+q(tech)+') error("Technology library unavailable"))\nlib=ddCreateLib('+q(library)+' '+q(lib_path)+') unless(lib error("Could not create test library"))\ntechBindTechFile(lib '+q(tech)+')']
 for s in d['symbols']:
  out.append('cv=dbOpenCellViewByType('+q(library)+' '+q(s['cell'])+' "symbol" "schematicSymbol" "w") unless(cv error("No writable view"))\nunless(cv~>DBUPerUU==%d error("Reference requires DBU=%d; verify and adapt target geometry"))'%(d['reference_dbu'],d['reference_dbu']))
  for sh in s['shapes']:
   k,a=sh['kind'],sh['data']
   if k=='line':out.append('dbCreateLine(cv list("device" "drawing") list('+' '.join(point(*p) for p in a)+'))')
   elif k in ['rect','circle']:out.append(('dbCreateRect' if k=='rect' else 'dbCreateEllipse')+'(cv list("device" "drawing") '+bbox(a)+')')
   else:x,y,t,size=a;out.append('dbCreateLabel(cv list("device" "drawing") '+point(x,y)+' '+q(t)+' "centerCenter" "R0" "roman" %.12g)'%size)
  for p in s['pins']:
   x,y=p['x'],p['y'];half=d['pin_half_size'];side=p['side'];dx=.0625 if side=='L' else -.0625 if side=='R' else .0625;dy=.0625 if side in ['L','R'] else -.125 if side=='T' else .125
   out.append('net=dbCreateNet(cv '+q(p['name'])+') term=dbCreateTerm(net '+q(p['name'])+' '+q(p['direction'])+') fig=dbCreateRect(cv list("pin" "drawing") '+bbox([x-half,y-half,x+half,y+half])+') unless(dbCreatePin(net fig '+q(p['name'])+' term) error("Pin creation failed"))')
   out.append('unless(schCreateSymbolLabel(cv '+point(x+dx,y+dy)+' "pin name" '+q(p['name'])+' '+q('lowerRight' if side=='R' else 'lowerLeft')+' "R0" "roman" .0625 "normalLabel") error("Pin label failed"))')
  out.append('schCreateSymbolLabel(cv '+point(s['width']/2,s['height']+.3125)+' "instance label" "[@instanceName]" "centerCenter" "R0" "roman" .075 "NLPLabel")')
  out.append('schCreateSymbolLabel(cv '+point(s['width']/2,s['height']+.15625)+' "logical label" "[@cellName]" "centerCenter" "R0" "roman" .0625 "NLPLabel")')
  out.append('dbCreateRect(cv list("instance" "drawing") '+bbox([0,0,s['width'],s['height']])+')')
  order='list('+' '.join(q(n) for n in s['term_order'])+')';out.append('dbReplaceProp(cv "portOrder" "ILList" '+order+')')
  out.append('unless(schSymbolToPinList('+q(library)+' '+q(s['cell'])+' "symbol") error("Pin-list conversion failed")) unless(equal(schGetPinOrder(cv) '+order+') error("Effective terminal order changed")) dbSave(cv) dbClose(cv)')
 out+=['printf("VCS_PASS NATIVE_REFERENCE_SYMBOLS '+str(len(d['symbols']))+'\\n") t))','unless(errset(vcsCreateSymbols() t) error("Reference creation failed; inspect only its isolated library"))']
 return '\n'.join(out)+'\n'
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--library',required=True);p.add_argument('--lib-path',required=True);p.add_argument('--tech-library',required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();s=emit(a.library,a.lib_path,a.tech_library,load());a.out.parent.mkdir(parents=True,exist_ok=True)
 if a.out.exists() and a.out.read_text(encoding='utf8')!=s:raise SystemExit('Output exists with different contents; choose another name')
 a.out.write_text(s,encoding='utf8');print('SKILL_GENERATED_NOT_EXECUTED',a.out)
if __name__=='__main__':main()
