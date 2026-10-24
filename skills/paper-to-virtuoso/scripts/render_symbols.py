"""Render reference SVGs from the exact symbol scene, without connecting to Cadence."""
from pathlib import Path
import argparse,html
from symbol_data import load
def svg(s,d):
 scale=200;pad=.5;w=(s['width']+2*pad)*scale;h=(s['height']+2*pad)*scale
 def xy(x,y):return ((x+pad)*scale,(s['height']-y+pad)*scale)
 out=['<svg xmlns="http://www.w3.org/2000/svg" width="%g" height="%g" viewBox="0 0 %g %g">'%(w,h,w,h),'<rect width="100%" height="100%" fill="white"/>']
 for shape in s['shapes']:
  k,a=shape['kind'],shape['data']
  if k=='line':out.append('<polyline points="'+' '.join('%g,%g'%xy(*p) for p in a)+'" fill="none" stroke="#287740" stroke-width="2"/>')
  elif k in ['rect','circle']:
   x0,y0,x1,y1=a;x,y=xy(x0,y1);ww=(x1-x0)*scale;hh=(y1-y0)*scale
   out.append('<rect x="%g" y="%g" width="%g" height="%g" fill="none" stroke="#287740" stroke-width="2"/>'%(x,y,ww,hh) if k=='rect' else '<ellipse cx="%g" cy="%g" rx="%g" ry="%g" fill="white" stroke="#287740" stroke-width="2"/>'%(x+ww/2,y+hh/2,ww/2,hh/2))
  elif k=='text':
   x,y,text,size=a;x,y=xy(x,y);out.append('<text x="%g" y="%g" font-family="Times New Roman" font-size="%g" fill="#216038" text-anchor="middle" dominant-baseline="middle">%s</text>'%(x,y,size*scale,html.escape(text)))
  else:raise ValueError(k)
 for p in s['pins']:
  x,y=xy(p['x'],p['y']);out.append('<rect x="%g" y="%g" width="6" height="6" fill="#c53e32"/>'%(x-3,y-3));side=p['side'];dx=.0625 if side=='L' else -.0625 if side=='R' else .0625;dy=.0625 if side in ['L','R'] else -.125 if side=='T' else .125;x,y=xy(p['x']+dx,p['y']+dy)
  out.append('<text x="%g" y="%g" font-family="Times New Roman" font-size="12.5" fill="#c53e32" text-anchor="%s">%s</text>'%(x,y,'end' if side=='R' else 'start',html.escape(p['name'])))
 x,y=xy(s['width']/2,s['height']+.3);out.append('<text x="%g" y="%g" font-family="Times New Roman" font-size="15" fill="#216038" text-anchor="middle">%s</text></svg>'%(x,y,s['cell']));return '\n'.join(out)+'\n'
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--out',type=Path,required=True);a=p.parse_args();d=load();a.out.mkdir(parents=True,exist_ok=True)
 for s in d['symbols']:(a.out/(s['cell']+'.svg')).write_text(svg(s,d),encoding='utf8')
 print('REFERENCE_SVG_CREATED',len(d['symbols']),'Native OA/GUI verification is separate.')
if __name__=='__main__':main()
