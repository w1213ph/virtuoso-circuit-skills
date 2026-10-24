"""Shared deterministic reference data. No Cadence installation needed."""
from pathlib import Path
import json,math,re
ASSET=Path(__file__).resolve().parents[1]/'assets/ctdsm18-symbols.json'
def load(path=ASSET):
 d=json.loads(Path(path).read_text(encoding='utf8'));assert d['schema']==1
 names=set()
 for s in d['symbols']:
  assert re.fullmatch(r'[A-Z][A-Z0-9_]*',s['cell']) and s['cell'] not in names;names.add(s['cell'])
  pins={p['name']:p for p in s['pins']};assert len(pins)==len(s['pins']);assert set(s['term_order'])==set(pins) and len(s['term_order'])==len(pins)
  assert s['width']>0 and s['height']>0
  for p in pins.values():
   assert p['side'] in ['L','R','T','B'] and p['direction'] in ['input','output','inputOutput']
   assert 0<=p['x']<=s['width'] and 0<=p['y']<=s['height']
   assert all(abs(x*d['reference_dbu']-round(x*d['reference_dbu']))<1e-8 for x in [p['x'],p['y'],p['x']+d['pin_half_size'],p['y']-d['pin_half_size']])
  assert s['shapes'] and any(x['kind']=='line' for x in s['shapes'])
 return d
