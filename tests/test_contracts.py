import sys,unittest,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/paper-to-virtuoso/scripts'))
from audit_electrical import compare
from compare_netlists import canonical
from symbol_data import load
class ElectricalContracts(unittest.TestCase):
 def setUp(self):
  self.d=dict(cell='AMP',ports=[dict(name='IP',direction='input',net='IP')],instances=[dict(name='M1',lib='PDK',cell='NMOS',xy=[0,0],props=[['l','string','180n']],terms=[dict(name='G',net='IP'),dict(name='D',net='OP')])])
 def test_display_only_change(self):
  a=copy.deepcopy(self.d);a['instances'][0]['xy']=[20,4];a['instances'][0]['orient']='MY';self.assertTrue(compare(self.d,a)['supplied_electrical_data_equal'])
 def test_gate_short_is_rejected(self):
  a=copy.deepcopy(self.d);a['instances'][0]['terms'][0]['net']='OP';self.assertFalse(compare(self.d,a)['supplied_electrical_data_equal'])
 def test_size_change_is_rejected(self):
  a=copy.deepcopy(self.d);a['instances'][0]['props'][0][2]='90n';self.assertFalse(compare(self.d,a)['supplied_electrical_data_equal'])
 def test_port_direction_is_rejected(self):
  a=copy.deepcopy(self.d);a['ports'][0]['direction']='output';self.assertFalse(compare(self.d,a)['supplied_electrical_data_equal'])
 def test_missing_port_net_is_not_passed(self):
  a=copy.deepcopy(self.d);del a['ports'][0]['net']
  with self.assertRaises(AssertionError):compare(self.d,a)
 def test_explicit_rename(self):
  a=copy.deepcopy(self.d);a['cell']='AFE_AMP';self.assertTrue(compare(self.d,a,{'AMP':'AFE_AMP'})['supplied_electrical_data_equal'])
class NetlistContracts(unittest.TestCase):
 def test_master_mapping_does_not_rename_nets(self):
  a='subckt A A O\nX1 (A O) B\nends A\n';b='subckt NEW_A A O\nX1 (A O) NEW_B\nends NEW_A\n';self.assertEqual(canonical(a,{'A':'NEW_A','B':'NEW_B'}),canonical(b))
 def test_scope_is_preserved(self):
  a='subckt A I O\nR0 (I O) resistor r=1k\nends A\nsubckt B I O\nR0 (I O) resistor r=2k\nends B\n';b=a.replace('r=1k','r=TEMP').replace('r=2k','r=1k').replace('r=TEMP','r=2k');self.assertNotEqual(canonical(a),canonical(b))
 def test_port_order_is_preserved(self):
  self.assertNotEqual(canonical('subckt A I O\nends A'),canonical('subckt A O I\nends A'))
 def test_pcell_alias_preserves_body(self):
  a='subckt r_pcell_0 P N\nR0 (P N) resistor r=1k\nends r_pcell_0\nX1 (A B) r_pcell_0\n';b=a.replace('r_pcell_0','r_pcell_12');self.assertEqual(canonical(a,prefixes=['r_pcell_']),canonical(b,prefixes=['r_pcell_']));self.assertNotEqual(canonical(a,prefixes=['r_pcell_']),canonical(b.replace('1k','2k'),prefixes=['r_pcell_']))
class ReferenceContracts(unittest.TestCase):
 def test_reference_interfaces_and_grid(self):
  d=load();self.assertEqual(len(d['symbols']),18);self.assertEqual(len({s['cell'] for s in d['symbols']}),18)
if __name__=='__main__':unittest.main()
