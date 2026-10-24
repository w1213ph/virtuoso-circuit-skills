"""Create an empty task record, never a fake completed circuit or a connection."""
from pathlib import Path
import argparse,json,re
def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--project',required=True);p.add_argument('--mode',choices=['paper','redraw','environment','export'],required=True);p.add_argument('--out',required=True,type=Path);a=p.parse_args()
 if not re.fullmatch(r'[A-Z][A-Z0-9_]*',a.project):raise SystemExit('Project prefix must use uppercase letters, digits and underscores')
 if a.out.exists():raise SystemExit('Task directory already exists; inspect existing work first')
 a.out.mkdir(parents=True)
 for n in ['evidence','screenshots','netlists','simulation','backup']:(a.out/n).mkdir()
 req=dict(project=a.project,mode=a.mode,paper=None,figure_numbers=[],pdk_library=None,process_version=None,supply_v=None,bandwidth_definition=None,goals=[],source_library=None,target_library=a.project+'_DESIGN',electrical_changes_authorized=a.mode=='paper',new_simulations_authorized=a.mode=='paper')
 (a.out/'requirements.json').write_text(json.dumps(req,indent=2)+'\n')
 assets=Path(__file__).resolve().parents[1]/'assets';(a.out/'acceptance.json').write_bytes((assets/'acceptance-template.json').read_bytes())
 (a.out/'CONNECTION.local.json').write_text(json.dumps(dict(connection_tool='PROVIDE_LOCALLY',workspace='PROVIDE_LOCALLY',credentials='USE_LOCAL_CREDENTIAL_PROVIDER'),indent=2)+'\n')
 (a.out/'TASK.md').write_text('# '+a.project+'\n\nRead the repository AGENTS.md and paper-to-virtuoso/SKILL.md.\nMode: '+a.mode+'. Requirements and acceptance fields are unverified until supported by actual evidence.\nPreserve existing saved design and manual edits. Do not reuse private paths or earlier device parameters.\n',encoding='utf8')
 print('EMPTY_TASK_CREATED',a.out,'No Cadence operation or simulation was run.')
if __name__=='__main__':main()
