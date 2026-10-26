"""Generate additive native schematic notes; no wires, instances, pins or symbols are edited."""
from pathlib import Path
import argparse
import json
import math
import re

def q(value):
    return json.dumps(value, ensure_ascii=False)

def point(value):
    if not isinstance(value,list) or len(value)!=2 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in value):
        raise ValueError('Coordinates must be two finite numbers')
    return 'list(%.12g %.12g)'%tuple(value)

def emit(library,cell,data):
    if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*',library) or not re.fullmatch(r'[A-Z][A-Z0-9_]*',cell):
        raise ValueError('Use a library identifier and an uppercase cell identifier')
    if data.get('schema_version')!=1 or not re.fullmatch(r'[A-Z][A-Z0-9_]*',data.get('owner','')):
        raise ValueError('Expected schema_version 1 and an uppercase owner identifier')
    notes=data.get('notes',[])
    if not notes:raise ValueError('At least one note is required')
    owner=q(data['owner'])
    out=['; Review and back up the saved target before loading this file.',
         '; Additive text and annotation geometry only. It does not place or route a circuit.',
         'procedure(vcsAddEngineeringNotes() let((cv fig before after count)',
         'unless(ddGetObj('+q(library)+' '+q(cell)+' "schematic") error("Existing schematic required"))',
         'cv=dbOpenCellViewByType('+q(library)+' '+q(cell)+' "schematic" "" "a") unless(cv error("Writable isolated view required"))',
         'when(setof(ss cv~>shapes ss~>vcsNoteOwner=='+owner+') error("This note set already exists; preserve later edits"))',
         'before=schCheck(cv) count=0']
    def tag():
        out.append('unless(fig error("Annotation creation failed")) dbReplaceProp(fig "vcsNoteOwner" "string" '+owner+') count=count+1')
    if data.get('frame') is not None:
        a,b=data['frame']
        if a[0]>=b[0] or a[1]>=b[1]:raise ValueError('Frame corners must be increasing')
        out.append('fig=dbCreateRect(cv list("text" "drawing1") list('+point(a)+' '+point(b)+'))');tag()
    for a,b in data.get('rules',[]):
        out.append('fig=dbCreateLine(cv list("text" "drawing1") list('+point(a)+' '+point(b)+'))');tag()
    for note in notes:
        text=note['text'];height=note.get('height',.1);just=note.get('justify','lowerLeft')
        if not isinstance(text,str) or not text or '\n' in text or '\r' in text:
            raise ValueError('Use one text line per native label')
        if not isinstance(height,(int,float)) or isinstance(height,bool) or not .025<=height<=1:
            raise ValueError('Text height must be between .025 and 1 user units')
        if just not in ['lowerLeft','lowerRight','centerCenter','upperLeft','upperRight']:
            raise ValueError('Unsupported label justification')
        if note.get('status')=='measured' and not note.get('conditions'):
            raise ValueError('Measured annotations need conditions in the record')
        out.append('fig=dbCreateLabel(cv list("text" "drawing") '+point(note['at'])+' '+q(text)+' '+q(just)+' "R0" "roman" %.12g)'%height);tag()
    out+=['after=schCheck(cv) unless(equal(before after) error("Check counts changed; inspect the unsaved test view"))',
          'dbSave(cv) dbClose(cv) printf("VCS_NOTES_PASS shapes=%d check=%L\\n" count after) t))',
          'unless(errset(vcsAddEngineeringNotes() t) error("Notes failed; inspect the isolated target and restore its backup if needed"))']
    return '\n'.join(out)+'\n'

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--library',required=True);p.add_argument('--cell',required=True)
    p.add_argument('--annotations',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();source=emit(a.library,a.cell,json.loads(a.annotations.read_text(encoding='utf8')))
    if a.out.exists() and a.out.read_text(encoding='utf8')!=source:raise SystemExit('Output differs; choose a new file name')
    a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(source,encoding='utf8')
    print('NOTES_SKILL_GENERATED_NOT_EXECUTED',a.out)

if __name__=='__main__':main()
