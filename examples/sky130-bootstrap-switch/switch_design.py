"""Sampling conditions, process-dependent sizing, and the physical circuits to compare."""
import csv
import io
import json
import math
from pathlib import Path
from switch_models import HERE, MIM, fetch_models
from mos_table import STATES, table

EDGE_NS = 1.0
CYCLES = 128
DISCARD = 12
TONE_BIN = 11
DELAY_NS = 500.0
WIDTHS = (2, 3, 4, 6, 8, 12, 16, 24)
SCANS = 40
MODELS = {"N18":"nfet_01v8", "P18":"pfet_01v8", "N5":"nfet_g5v0d10v5", "P5":"pfet_g5v0d10v5"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def requirements(path):
    r = json.loads(Path(path).read_text(encoding="utf-8"))
    fields = {"purpose","sample_MHz","input_common_V","input_vpp_V","source_ohm","load_pF","track_ns",
              "read_after_off_ns","max_thd_dBc","max_forward_V"}
    require(isinstance(r,dict) and set(r)==fields and isinstance(r["purpose"],str) and r["purpose"].strip(), "Incomplete switch specification")
    def number(x): return type(x) in (int,float) and math.isfinite(x)
    require(all(number(r[k]) for k in fields-{"purpose","input_vpp_V"}), "Expected finite scalar requirements")
    levels = r["input_vpp_V"]
    require(isinstance(levels,list) and 1<=len(levels)<=3 and all(number(x) and x>0 for x in levels)
            and len(set(levels))==len(levels), "Specify one to three different positive peak-to-peak swings")
    require(0 < r["input_common_V"]-max(levels)/2 and r["input_common_V"]+max(levels)/2 < min(x[2] for x in STATES), "Input exceeds the available rails")
    require(1<=r["sample_MHz"]<=30 and 4<r["track_ns"] and r["read_after_off_ns"]>=1
            and r["track_ns"]+EDGE_NS+r["read_after_off_ns"] < 1000/r["sample_MHz"], "Track and hold do not fit in the clock period")
    require(0<r["source_ohm"]<=500 and .1<=r["load_pF"]<=5 and -110<=r["max_thd_dBc"]<=-20
            and 0<r["max_forward_V"]<=.30, "Source, load, distortion or junction budget outside the supported range")
    return r


def budget(r):
    return dict(period_ns=1000/r["sample_MHz"], tone_Hz=r["sample_MHz"]*1e6*TONE_BIN/CYCLES,
                # Give the switch and source 15% of the track window to settle a 10-bit step to half an LSB.
                sizing_Ron_ohm=.15*r["track_ns"]*1000/(math.log(2048)*r["load_pF"])-r["source_ohm"],
                gain_bounds=[.98,1.02], min_track_vgs_ratio=.70, min_precharge_ratio=.95, max_hold_gate_V=.10)


def geometry(w,l,m=1):
    return dict(w=w,l=l,ad=.3*w,pd=2*(w+.3),mult=m)


def geometries_text(g):
    return 'w={w:g} l={l:g} nf=1 ad={ad:g} as={ad:g} pd={pd:g} ps={pd:g} m={mult:g}'.format(**g)


def char_branches(r,rail):
    levels=(r['input_common_V']-max(r['input_vpp_V'])/2,r['input_common_V']+max(r['input_vpp_V'])/2)
    for typ in ('N5','P5'):
        for j,level in enumerate(levels if typ=='N5' else (rail,)):
            for width in WIDTHS:
                # The small drain step measures R_on. A separate 0.5 V branch measures finite-difference gm/ID.
                for mode in ('ron','gm'):
                    yield '{}{}w{}{}'.format(typ.lower(),j,width,mode),typ,level,width,mode


def char_deck(r,state,libs):
    corner,temp,rail=state
    body=['SKY130 sampling MOS characteristics']+['.lib "{}" {}'.format(p,corner) for p in libs]+[
        '.temp '+str(temp),'VSW sweep 0 0']
    vectors=[]
    for key,typ,level,w,mode in char_branches(r,rail):
        sign=1 if typ=='N5' else -1
        s,g,d='s'+key,'g'+key,'d'+key
        body+=[f'Vs{key} {s} 0 {level:.12g}',f'Eg{key} {g} {s} sweep 0 {sign}',
               'Vd{} {} {} {:.12g}'.format(key,d,s,sign*(.001 if mode=='ron' else .5)),
               'X{} {} {} {} {} sky130_fd_pr__{} {}'.format(key,d,g,s,'0' if sign==1 else s,MODELS[typ],geometries_text(geometry(w,.5)))]
        vectors.append('i(vd'+key+')')
    return '\n'.join(body+['.control','set wr_singlescale','set wr_vecnames','set numdgt=12',
        'dc VSW 0 {:g} {:.12g}'.format(rail,rail/SCANS),'wrdata sweep.dat '+' '.join(vectors),'echo analysis_complete','quit','.endc','.end',''])


def char_results(path,r,state):
    corner,_,rail=state
    branches=list(char_branches(r,rail));raw=table(path,('i(vd'+b[0]+')' for b in branches))
    require(len(raw)==SCANS+1 and all(abs(row[0]-idx*rail/SCANS)<1e-8 for idx,row in enumerate(raw)), 'Incomplete MOS gate sweep')
    buf=io.StringIO();writer=csv.writer(buf,lineterminator='\n');writer.writerow(('corner','device','source_V','width_um','length_um','vgs_abs_V','ron_ohm_at_1mV','id_uA_at_0p5V','gm_id_per_V_at_0p5V'))
    resistance={}
    for i in range(0,len(branches),2):
        _,typ,src,w,_=branches[i]
        for k in range(1,SCANS):
            row=raw[k];amps=abs(row[i+1]);current=abs(row[i+2]);require(amps>0 and current>0,'Unreadable device current')
            ron=.001/amps;gm=(abs(raw[k+1][i+2])-abs(raw[k-1][i+2]))/(raw[k+1][0]-raw[k-1][0])
            writer.writerow((corner,typ,format(src,'.10g'),w,.5,format(row[0],'.10g'),format(ron,'.10g'),format(current*1e6,'.10g'),format(gm/current,'.10g')))
            if typ=='N5' and k==35:
                resistance[w]=max(resistance.get(w,0),ron)
    require(len(resistance)==len(WIDTHS) and all(resistance[a]>resistance[b]>0 for a,b in zip(WIDTHS,WIDTHS[1:])), 'Unresolved switch width response')
    return buf.getvalue(),resistance


def design_set(r,corner_resistances):
    worst={w:max(c[w] for c in corner_resistances) for w in WIDTHS}
    limit=budget(r)['sizing_Ron_ohm']
    # A width outside the sweep is never inferred. The largest measured choice remains a diagnostic if the RC budget cannot be met.
    w=next((w for w in WIDTHS if worst[w]<=limit),WIDTHS[-1])
    return {name:circuit(name,w) for name in ('transmission','thin','mixed','reset','plate','well','compact')},dict(width_um=w,ron_ohm=worst[w],target_ohm=limit,within_rc_budget=worst[w]<=limit)


def circuit(name,w):
    well_side=0
    if name=='transmission':
        devices={'XSN':('SAMPLE CLK IN VSS','N18',geometry(3,.15,2)), 'XSP':('SAMPLE CLKB IN VDD','P18',geometry(6,.15,2))}
        side=0
    else:
        require(name in ('thin','mixed','reset','plate','well','compact'), 'Unknown switch circuit')
        thick=name!='thin';balanced=name in ('reset','plate','well','compact');lv=.15;length=.5 if thick else lv
        n,p=('N5','P5') if thick else ('N18','P18')
        # Shape and plate sizing are measured again in the full transient with the real load.
        side=math.ceil((14 if name=='plate' else 8 if name=='compact' else 9)*math.sqrt(w/3))
        devices={
            'XMAIN':('SAMPLE GATE IN VSS',n,geometry(w,length)),
            'XLINK':('GATE CLKB TOP TOP',p,geometry(4,length)),
            'XPRE':('VDD GATE TOP TOP',p,geometry(2 if balanced else 3,length)),
            'XOFF':('GATE CLKB VSS VSS',n,geometry(8 if balanced else 3,length)),
            'XBOTN':('BOT CLK IN VSS','N18',geometry(4,lv)),
            'XBOTP':('BOT CLKB IN VDD','P18',geometry(8,lv)),
            'XZERO':('BOT CLKB VSS VSS','N18',geometry(.5 if balanced else 4,lv)),
        }
        if name in ('well','compact'):
            well_side=3.5 if name=='compact' else 3
            devices.update(XLINK=('GATE CLKB TOP WELL',p,geometry(4,length)),XPRE=('VDD GATE TOP WELL',p,geometry(2,length)),
                           XWREF=('WELL GATE VDD WELL',p,geometry(3,length)),
                           XWBOOST=('WELL CLKB TOP WELL',p,geometry(12 if name=='compact' else 8,length)))
    return dict(name=name,devices=devices,side_um=side,well_side_um=well_side,plate_um2=side*side+well_side*well_side,
                gate_um2=sum(g['w']*g['l']*g['mult'] for _,_,g in devices.values()))
