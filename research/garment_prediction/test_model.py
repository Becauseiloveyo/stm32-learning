import json, math, unittest
from model import Zone, Drive, state, rhs, run, run_two, rho_sat, advance, IntegrationStepError

Z=Zone(.3,.45,.18,.45,.4,.15,140.,.00001,.6,.9)
S=Zone(.1,.10,.035,.12,.10,.22,220.,.000005,.6,.9)
D=Drive(25,.5,0,0,25,0,25)
class Checks(unittest.TestCase):
    def test_balance_and_cooling(self):
        x=-Z.xb*math.log(1-D.rh)
        ze=Zone(**{**vars(Z),'drain_per_s':0})
        self.assertAlmostEqual(rhs(ze,D,state(ze,x,25))[0],0,places=15)
        a=run(Z,D,state(Z,.6,25),300,2)
        self.assertLess(a['water_kg'],.18); self.assertLess(a['temperature_c'],25)
        self.assertFalse(a['collection_authorized'])
    def test_uptake_and_empty_boundary(self):
        e=state(Z,0,25)
        self.assertGreater(rhs(Z,D,e)[0],0)
        self.assertGreater(run(Z,D,e,60,1)['water_kg'],0)
        dry=Drive(25,0,0,0,25,0,25)
        self.assertEqual(rhs(Z,dry,e)[0],0)
        self.assertEqual(run(Z,dry,e,30,1)['water_kg'],0)
    def test_rain_and_drain_accounting(self):
        d=Drive(25,.5,0,200,25,.00001,20)
        a=run(Z,d,state(Z,.6,25),600,1)
        self.assertLess(abs(a['water_residual_kg']),3e-8)
        self.assertLess(abs(a['energy_residual_j']),.1)
    def test_invalid(self):
        for bad in [float('nan'),float('inf'),-1,51]:
            d=Drive(bad,.5,0,0,25,0,25)
            with self.assertRaises(ValueError): run(Z,d,state(Z,.2,25),10,1)
        with self.assertRaises(ValueError): run(Z,D,state(Z,-.1,25),10,1)
        with self.assertRaises(ValueError): run(Z,Drive(25,2,0,0,25,0,25),state(Z,.1,25),10,1)
        with self.assertRaises(TypeError): Drive(25,.5,0,0,25,0)
        with self.assertRaises(IntegrationStepError): advance(Z,D,state(Z,.1,25),1e6)
        with self.assertRaises(ValueError): run_two([Z],[D],[state(Z,.2,25)],10,1)
    def test_convergence_and_reference(self):
        y=state(Z,.6,25); d=Drive(25,.5,.5,200,25,0,25)
        coarse=run(Z,d,y,1800,20); fine=run(Z,d,y,1800,10)
        ref=run(Z,d,y,1800,.25,'midpoint')
        for key,tol in [('water_kg',1e-7),('temperature_c',1e-4)]: self.assertLess(abs(fine[key]-ref[key]),tol)
        self.assertLess(abs(fine['energy_residual_j']),abs(coarse['energy_residual_j']))
        self.assertLess(abs(fine['water_residual_kg']),abs(coarse['water_residual_kg']))
        self.assertLess(abs(fine['energy_residual_j']),20.0)
    def test_drive_change(self):
        y=state(Z,.6,25); a=run(Z,D,y,300,2)
        d=Drive(30,.8,1,500,30,0,25)
        b=run(Z,d,(a['water_kg'],a['energy_j']),300,2)
        aa=run(Z,D,y,300,.25,'midpoint'); bb=run(Z,d,(aa['water_kg'],aa['energy_j']),300,.25,'midpoint')
        self.assertLess(abs(b['temperature_c']-bb['temperature_c']),1e-4)
        self.assertLess(abs(b['water_kg']-bb['water_kg']),1e-7)
    def test_analytic_drain_limit(self):
        # Degenerate artificial setup: no environmental heat, radiation or vapor exchange.
        z=Zone(.3,0,0,0,0,.15,140.,.0005,.6,.9)
        y=state(z,.6,25); duration=1800
        result=run(z,D,y,duration,1)
        expected_water=y[0]*math.exp(-z.drain_per_s*duration)
        expected_energy=(z.dry_kg*1400 + expected_water*4180)*25
        self.assertAlmostEqual(result['water_kg'],expected_water,places=12)
        self.assertAlmostEqual(result['energy_j'],expected_energy,places=7)
        self.assertAlmostEqual(result['temperature_c'],25,places=9)
        self.assertLess(abs(result['water_residual_kg']),1e-8)
        self.assertLess(abs(result['energy_residual_j']),.002)
    def test_two_zone_total_ledgers(self):
        zones=[Z,S]; drives=[D,Drive(28,.7,.5,300,26,.000005,20)]
        initial=[state(Z,.6,25),state(S,.9,26)]
        out=run_two(zones,drives,initial,1200,1)
        for key in ('water','energy'):
            total_change=sum(a['delta_'+key+('_kg' if key=='water' else '_j')] for a in out)
            total_ledger=sum(a['ledger_'+key+('_kg' if key=='water' else '_j')] for a in out)
            residual=sum(a[key+'_residual_'+('kg' if key=='water' else 'j')] for a in out)
            self.assertAlmostEqual(total_change-total_ledger,residual,places=9)
            self.assertLess(abs(residual), 1e-7 if key=='water' else .4)
    def test_nonnegative_boundary_near_zero(self):
        for humidity in (0,.5,1):
            d=Drive(25,humidity,0,0,25,0,25)
            self.assertGreaterEqual(rhs(Z,d,state(Z,0,25))[0],0)
            if humidity == 0:
                r=run(Z,d,state(Z,1e-7,25),1000,1)
                self.assertGreater(r['water_kg'],0)
                self.assertLess(r['water_kg'],Z.dry_kg*1e-7)
            else:
                self.assertGreater(run(Z,d,state(Z,0,25),10,1)['water_kg'],0)
    def test_integrator_input_contract(self):
        y=state(Z,.1,25)
        for bad in (-1,0,float('nan'),float('inf')):
            with self.assertRaises(ValueError) as error: advance(Z,D,y,bad)
            self.assertNotIsInstance(error.exception,IntegrationStepError)
        for duration in (0,1):
            with self.assertRaisesRegex(ValueError,'unknown method') as error: run(Z,D,y,duration,1,'typo')
            self.assertNotIsInstance(error.exception,IntegrationStepError)
        with self.assertRaisesRegex(ValueError,'unknown method') as error: advance(Z,D,y,1,'typo')
        self.assertNotIsInstance(error.exception,IntegrationStepError)
        with self.assertRaises(IntegrationStepError) as error: advance(Z,D,y,1e6)
        self.assertIsInstance(error.exception.__cause__,ValueError)
        self.assertIn('outside research domain',str(error.exception.__cause__))
        self.assertEqual(run(Z,D,y,0,1,'midpoint')['steps'],0)
    def test_two(self):
        a=run_two([Z,S],[D,D],[state(Z,.6,25),state(S,.6,25)],600,2)
        self.assertEqual(len(a),2)
        self.assertGreater(a[1]['water_kg']/S.dry_kg,a[0]['water_kg']/Z.dry_kg)
if __name__ == '__main__': unittest.main(verbosity=2)

