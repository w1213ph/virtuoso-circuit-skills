"""Check public content, local links and complete reference data; no network/Cadence."""
from pathlib import Path
import json,re,sys,hashlib
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/paper-to-virtuoso/scripts'))
from symbol_data import load
def files():
 return [p for p in ROOT.rglob('*') if p.is_file() and not any(x in ['.git','build','work','__pycache__','.venv'] for x in p.relative_to(ROOT).parts)]
def main():
 bad=[];n=0
 patterns=[r'\b192\.168\.\d{1,3}\.\d{1,3}\b',r'/home/(?!USER/|user/|example/|YOUR_USER/)[A-Za-z0-9_.-]+/',r'[A-Z]:[\\/](?:Users|zotero|desktop)[\\/]',r'\b(?:github_pat_|ghp_)[A-Za-z0-9_]{25,}', '-----BEGIN '+'(?:OPENSSH |RSA |EC )?PRIVATE KEY-----']
 for p in files():
  rel=str(p.relative_to(ROOT));n+=1
  if p.suffix.lower() in ['.oa','.sdb','.psf','.scs','.pdf','.zip','.pem','.key'] or '.cdslck' in p.name:bad.append([rel,'Forbidden raw/private artifact type'])
  if p.suffix in ['.png']:continue
  s=p.read_text(encoding='utf8')
  for r in patterns:
   if re.search(r,s):bad.append([rel,'Sensitive path/credential pattern'])
  if p.suffix=='.md':
   for target in re.findall(r'\[[^\]]*\]\(([^)]+)\)',s):
    target=target.strip('<>').split('#')[0]
    if not target or re.match(r'\w+://',target) or target.startswith('mailto:'):continue
    if not (p.parent/target).exists():bad.append([rel,'Missing link',target])
 d=load();assert len(d['symbols'])==18
 for p in [ROOT/'skills/paper-to-virtuoso/assets/ctdsm18-layouts.json',ROOT/'examples/ctdsm18/validation.json']:json.loads(p.read_text())
 manifest=ROOT/'MANIFEST.sha256'
 if manifest.exists():
  for line in manifest.read_text().splitlines():
   digest,name=line.split('  ',1);f=ROOT/name
   if '..' in Path(name).parts or not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest()!=digest:bad.append([name,'Release hash mismatch'])
 print(json.dumps(dict(files_checked=n,reference_symbols=len(d['symbols']),issues=bad),ensure_ascii=False,indent=2));raise SystemExit(1 if bad else 0)
if __name__=='__main__':main()
