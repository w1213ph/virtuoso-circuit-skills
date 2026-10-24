"""Conservative scoped comparison; never discard values, port order, models or analyses."""
from pathlib import Path
import argparse,re,json,hashlib
def rename_masters(text,mapping):
 def repl(m):return m[1]+mapping.get(m[2],m[2])
 text=re.sub(r'^(\s*(?:subckt|ends)\s+)(\w+)',repl,text,flags=re.M)
 return re.sub(r'^(\s*[^\s/]+\s+\([^\n)]*\)\s+)(\w+)',repl,text,flags=re.M)
def canonical(text,mapping=None,prefixes=()):
 text=re.sub(r'\\\n\s*',' ',text);mapping=mapping or {};mapping=mapping.get('cells',mapping)
 text=rename_masters(text,mapping)
 # Full header + body fingerprint: two different PCells must never collapse.
 aliases={}
 for m in re.finditer(r'^\s*subckt\s+(\w+)([^\n]*)\n(.*?)^\s*ends\s+\1\s*$',text,re.M|re.S):
  name=m[1]
  if not any(re.fullmatch(re.escape(p)+r'\d+',name) for p in prefixes):continue
  body=' '.join((m[2]+' '+m[3]).split());aliases[name]='PCELL_'+hashlib.sha256(body.encode()).hexdigest()
 text=rename_masters(text,aliases)
 out=[];scope='<top>'
 for l in text.splitlines():
  l=re.sub(r'\s+',' ',l.strip())
  if not l or l.startswith('//') or l.startswith('*'):continue
  if l.startswith('subckt '):
   if scope!='<top>':raise ValueError('Nested subcircuits need a parser adapted to their syntax')
   scope=l.split()[1]
  out.append(scope+' | '+l)
  if l.startswith('ends '):scope='<top>'
 if scope!='<top>':raise ValueError('Unclosed subcircuit')
 return sorted(out)
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--before',required=True,type=Path);p.add_argument('--after',required=True,type=Path);p.add_argument('--cell-map',type=Path);p.add_argument('--pcell-prefix',action='append',default=[]);a=p.parse_args();m=json.loads(a.cell_map.read_text()) if a.cell_map else None
 x=canonical(a.before.read_text(),m,a.pcell_prefix);y=canonical(a.after.read_text(),None,a.pcell_prefix)
 if x!=y:
  import difflib
  print('\n'.join(difflib.unified_diff(x,y,fromfile='before',tofile='after')));raise SystemExit(1)
 print('PROVIDED_NATIVE_NETLIST_TEXT_EQUIVALENT; GUI, source completeness and behavior remain separate.')
if __name__=='__main__':main()
