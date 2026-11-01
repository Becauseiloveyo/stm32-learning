import json,math,copy,sys,unittest
from pathlib import Path
from coupled import *
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'observation_m3'))
from physics import simulate as m3
from model import state,run
C=json.loads(Path('research/garment_prediction/coupled_m5/config.json').read_text(encoding='utf-8'))
def fixture():
 z=[Zone(**C['ordinary']),Zone(**C['slow'])];d=Drive(**C['drive']);c=dict(C['coupling']);a=state(z[0],.6,25);b=state(z[1],.6,25);return z,d,c,(a[0],a[1],b[0],b[1],c['volume_m3']*.5*rho_sat(25),.5,0.,0.)
class Tests(unittest.TestCase):
 def test_analytic_solar_and_sensor(self):
  z,d,c,y=fixture();z=[Zone(**{**vars(a),'evap_m2':0,'heat_m2':0,'rad_m2':0}) for a in z];c['area_cover_m2']=0;c['flow_m3_s']=0
  y=list(y);y[5]=.2;y=tuple(y);out=integrate(z,d,c,y,60,.1)
  for i in (0,1):
   expected=y[2*i+1]+z[i].alpha*d.solar_w_m2*z[i].proj_m2*60
   self.assertAlmostEqual(out['state'][2*i+1],expected,places=7)
  self.assertAlmostEqual(out['state'][5],.5+(.2-.5)*math.exp(-60/c['sensor_tau_s']),places=10)
  self.assertAlmostEqual(out['water_residual_kg'],0,places=12);self.assertLess(abs(out['energy_residual_j']),1e-7)
 def test_analytic_same_temperature_rain_drain(self):
  z,d,c,y=fixture();z=[Zone(**{**vars(a),'evap_m2':0,'heat_m2':0,'rad_m2':0,'proj_m2':0,'drain_per_s':.001}) for a in z];c['area_cover_m2']=0;c['flow_m3_s']=0
  d=Drive(**{**vars(d),'solar_w_m2':0,'rain_kg_s':.00001,'rain_c':25})
  out=integrate(z,d,c,y,60,.1)
  for i in (0,1):
   w=y[2*i]*math.exp(-.001*60)+(.00001/.001)*(1-math.exp(-.001*60))
   self.assertAlmostEqual(out['state'][2*i],w,places=12)
   self.assertAlmostEqual(out['state'][2*i+1],(z[i].dry_kg*CD+CW*w)*25,places=7)
  self.assertLess(abs(out['water_residual_kg']),1e-10);self.assertLess(abs(out['energy_residual_j']),.001)
 def test_boundary_terms_independent(self):
  z,d,c,y=fixture();f=fluxes(z,d,c,y);t=f[7]
  self.assertAlmostEqual(f[3],t['open_mass_kg_s']+t['vent_mass_kg_s']+t['liquid_mass_kg_s'],places=17)
  self.assertAlmostEqual(f[4],sum(t[k] for k in ('solar_W','convection_W','radiation_W','open_vapor_W','vent_vapor_W','liquid_W','gas_reservoir_W','clamp_reservoir_W')),places=10)
  self.assertAlmostEqual(f[4],f[1][0]+f[1][1]+uv(d.air_c)*f[2],places=9)
 def test_m1_zero_cover(self):
  z,d,c,y=fixture();c['area_cover_m2']=0; o=integrate(z,d,c,y,60,.1)['state']
  for i in (0,1):
   ref=run(z[i],d,(y[2*i],y[2*i+1]),60,.1)
   self.assertAlmostEqual(o[2*i],ref['water_kg'],places=12);self.assertAlmostEqual(o[2*i+1],ref['energy_j'],places=7)
 def test_m1_rain_drain_zero_cover(self):
  z,d,c,y=fixture();z=[Zone(**{**vars(a),'drain_per_s':.00001}) for a in z];d=Drive(**{**vars(d),'rain_kg_s':.000001,'rain_c':22});c['area_cover_m2']=0
  o=integrate(z,d,c,y,60,.1)['state']
  for i in (0,1):
   ref=run(z[i],d,(y[2*i],y[2*i+1]),60,.1)
   self.assertAlmostEqual(o[2*i],ref['water_kg'],places=12);self.assertAlmostEqual(o[2*i+1],ref['energy_j'],places=7)
 def test_m3_isothermal(self):
  z,d,c,y=fixture();o=integrate(z,d,c,y,60,.1,clamped=True)
  p={'dry_kg':z[0].dry_kg,'xb':z[0].xb,'area_open_m2':z[0].evap_m2-c['area_cover_m2'],'area_cover_m2':c['area_cover_m2'],'r_open_s_m':z[0].resistance_s_m/(1+d.wind_m_s),'r_cover_s_m':c['cover_resistance_s_m'],'volume_m3':c['volume_m3'],'flow_m3_s':c['flow_m3_s'],'wall_source_kg_s':0,'sensor_tau_s':c['sensor_tau_s'],'sensor_bias_rh':c['sensor_bias_rh'],'temperature_c':25,'ambient_rh':d.rh}
  ref=m3(p,.6,.5,.5,60,.1);self.assertAlmostEqual(o['state'][0],ref['producer_only']['water_kg'],places=12);self.assertAlmostEqual(o['local_rh'],ref['samples'][-1]['local_rh'],places=10)
  self.assertLess(abs(o['energy_residual_j']),.02)
 def test_closed_mass_and_thermal_ledger(self):
  z,d,c,y=fixture();z=[Zone(**{**vars(a),'evap_m2':0 if i else c['area_cover_m2']}) for i,a in enumerate(z)];c['flow_m3_s']=0
  out=integrate(z,d,c,y,60,.1);self.assertLess(abs(out['water_residual_kg']),1e-12);self.assertLess(abs(out['energy_residual_j']),.02)
  self.assertAlmostEqual(sum((out['state'][0],out['state'][2],out['state'][4])),sum((y[0],y[2],y[4])),places=12)
 def test_absorption_and_empty(self):
  z,d,c,y=fixture();p=list(y);p[0]=p[1]=0.;r=rhs(z,d,c,p);self.assertGreater(r[0],0)
 def test_transition(self):
  z,d,c,y=fixture();a=integrate(z,d,c,y,20,.1)['state'];new=Drive(**{**vars(d),'air_c':27,'rh':.4});b,j=transition(z,d,new,c,a)
  self.assertEqual(a[:7],b[:7]);self.assertAlmostEqual(b[7]-a[7],a[4]*(CV-RV)*2,places=9);self.assertAlmostEqual(j,a[4]*(CV-RV)*2,places=12)
  out=integrate(z,new,c,b,20,.1);self.assertLess(abs(out['energy_residual_j']),.02)
 def test_series_ledger(self):
  z,d,c,y=fixture();d2=Drive(**{**vars(d),'air_c':27,'rh':.4,'solar_w_m2':50,'wind_m_s':1.2})
  r=run_segments(z,c,y,[(20,d),(40,d2)],.1,d);a=r['initial_state'];b=r['final_state']
  self.assertAlmostEqual((b[0]+b[2]+b[4])-(a[0]+a[2]+a[4]),b[6]-a[6],places=12)
  actual=b[1]+b[3]+b[4]*uv(d2.air_c)-(a[1]+a[3]+a[4]*uv(d.air_c))
  self.assertAlmostEqual(actual,b[7]-a[7],places=7)
  self.assertNotEqual(r['segments'][1]['boundary_jump_j'],0)
 def test_reference_and_convergence(self):
  z,d,c,y=fixture();a=integrate(z,d,c,y,60,.2);b=integrate(z,d,c,y,60,.1);r=integrate(z,d,c,y,60,.01,'midpoint')
  self.assertLess(abs(b['local_rh']-r['local_rh']),1e-6);self.assertLess(abs(b['state'][1]-r['state'][1]),1e-3)
  self.assertLess(abs(b['energy_residual_j']),abs(a['energy_residual_j']))
 def test_domain(self):
  z,d,c,y=fixture()
  with self.assertRaises(ValueError):integrate(z,d,c,y,60,0)
  with self.assertRaises(ValueError):integrate(z,d,c,y,1,.1,clamped='true')
  with self.assertRaises(ValueError):fluxes(z,d,c,y,clamped=1)
  with self.assertRaises(ValueError):transition(z,d,Drive(**{**vars(d),'air_c':0}),c,y)
  with self.assertRaises(ValueError):integrate(z,d,{**c,'area_cover_m2':z[0].evap_m2+1},y,10,.1)
  with self.assertRaises(ValueError):integrate(z,Drive(**{**vars(d),'rain_kg_s':-.001}),c,y,10,.1)
 def test_response_state_is_rh(self):
  z,d,c,y=fixture();new=Drive(**{**vars(d),'air_c':27,'rh':.4});b,_=transition(z,d,new,c,y)
  self.assertEqual(b[5],y[5]);self.assertNotEqual(b[4]/c['volume_m3']/rho_sat(new.air_c),y[4]/c['volume_m3']/rho_sat(d.air_c))
if __name__=='__main__':unittest.main(verbosity=2)
