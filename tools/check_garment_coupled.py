"""Independent coordinator checks for the M5 synthetic dynamic coupling."""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys

sys.dont_write_bytecode = True
ROOT=Path(__file__).resolve().parents[1]
P='research/garment_prediction/coupled_m5/'
RECEIPT=ROOT/'docs/verification/T10-M5-tests.json'


def require(ok,message):
    if not ok:raise AssertionError(message)


def read(p):return json.loads((ROOT/p).read_text(encoding='utf-8-sig'))


def sha(p):
    full=(ROOT/p).resolve();require(full.is_relative_to(ROOT),'path outside workspace')
    return hashlib.sha256(full.read_bytes()).hexdigest()


def main():
    ap=argparse.ArgumentParser(description=__doc__);g=ap.add_mutually_exclusive_group()
    g.add_argument('--check-receipt',action='store_true');g.add_argument('--write-receipt',action='store_true');args=ap.parse_args()
    if args.check_receipt:
        r=read('docs/verification/T10-M5-tests.json')
        require(r['passed'] and r['collection_authorized'] is False and r['checks'] and r['reviewed_sha256'],'unpassed receipt')
        require(all(c['passed'] for c in r['checks']),'missing checks')
        for p,h in r['reviewed_sha256'].items():require(sha(p)==h,'stale '+p)
        print('M5 receipt current and passed');return 0
    sys.path.insert(0,str(ROOT/P))
    from coupled import Zone,Drive,integrate,run_segments,transition,fluxes
    cfg=read(P+'config.json');manifest=read(P+'results/manifest.json')
    watched=set(manifest['inputs_sha256'])|set(manifest['artifacts_sha256'])|{
        P+'results/manifest.json','docs/GARMENT-COUPLED.md','docs/team/reports/algorithm-t10-m5.md','tools/check_garment_coupled.py'}
    before={p:sha(p) for p in sorted(watched)};checks=[]
    def check(name,action):
        try:checks.append(dict(name=name,passed=True,detail=action()))
        except Exception as e:checks.append(dict(name=name,passed=False,error=f'{type(e).__name__}: {e}'))
    def sat(t):return 610.94*math.exp(17.625*t/(243.04+t))/(461.5*(273.15+t))
    def vapor_h(t):return 2500000.+1850.*t
    def vapor_u(t):return vapor_h(t)-461.5*(273.15+t)
    def fixture():
        z=[Zone(**cfg['ordinary']),Zone(**cfg['slow'])];d=Drive(**cfg['segments'][0]['drive']);c=dict(cfg['coupling'])
        y=[]
        for a in z:y.extend((a.dry_kg*.6,(a.dry_kg*1400.+a.dry_kg*.6*4180.)*25.))
        return z,d,c,tuple(y+[c['volume_m3']*.5*sat(25.),.5,0.,0.])
    def solve(z,d,c,y,duration,dt,**kw):
        r=integrate(z,d,c,y,duration,dt,**kw)
        require(r['collection_authorized'] is False and r['label']=='SYNTHETIC_UNCALIBRATED','unsupported claim')
        return r
    def suite():
        cmd=[sys.executable,'-B','-X','utf8',P+'test_coupled.py'];p=subprocess.run(cmd,cwd=ROOT,capture_output=True,encoding='utf-8',timeout=120)
        require(p.returncode==0,p.stdout+p.stderr);m=re.search(r'Ran (\d+) tests?',p.stdout+p.stderr);require(m is not None,'no test count')
        return dict(command=cmd,test_count=int(m.group(1)),output=p.stdout+p.stderr)
    def integrity():
        required=set();counts={}
        for path,key in [('docs/team/baseline-t10-m5/snapshot.json','inputs_sha256')]+[('docs/verification/T10-'+m+'-tests.json','reviewed_sha256') for m in ('M1','M2a','M2b','M3','M4')]:
            hashes=read(path)[key]
            for name,h in hashes.items():require(sha(name)==h,'protected input changed '+name)
            counts[path]=len(hashes);required.update(hashes);required.add(path)
        required.update(P+x for x in ('coupled.py','config.json','test_coupled.py','replay.py'))
        require(required<=set(manifest['inputs_sha256']),'incomplete provenance')
        for key in ('inputs_sha256','artifacts_sha256'):
            for path,h in manifest[key].items():require(sha(path)==h,'manifest mismatch '+path)
        require(manifest['label']=='SYNTHETIC_UNCALIBRATED' and manifest['seed'] is None,'label/seed')
        return dict(inputs=len(manifest['inputs_sha256']),artifacts=len(manifest['artifacts_sha256']),protected=counts)
    def heat_analytics():
        rows=[]
        for mode in ('solar','convection'):
            z,d,c,y=fixture();c.update(area_cover_m2=0.,flow_m3_s=0.);y=list(y);y[5]=.2
            z=[replace(a,evap_m2=0.,rad_m2=0.,heat_m2=0. if mode=='solar' else a.heat_m2,proj_m2=a.proj_m2 if mode=='solar' else 0.) for a in z]
            if mode=='convection':
                d=replace(d,air_c=30.,solar_w_m2=0.);y[4]=c['volume_m3']*.5*sat(30.)
            r=solve(z,d,c,tuple(y),60.,.17);errors=[]
            for i,a in enumerate(z):
                capacity=a.dry_kg*1400.+y[2*i]*4180.
                expected=25.+a.alpha*d.solar_w_m2*a.proj_m2*60./capacity if mode=='solar' else d.air_c+(25.-d.air_c)*math.exp(-(4+4*math.sqrt(d.wind_m_s))*a.heat_m2*60./capacity)
                actual=r['state'][2*i+1]/capacity;errors.append(actual-expected);require(abs(actual-expected)<1e-9,'analytic temperature '+mode)
            expected_sensor=.5+(.2-.5)*math.exp(-60./4.)
            require(abs(r['state'][5]-expected_sensor)<1e-10,'analytic RH response')
            rows.append(dict(mode=mode,temperature_errors_c=errors,sensor_error=r['state'][5]-expected_sensor))
        return rows
    def liquid_analytic():
        z,d,c,y=fixture();c.update(area_cover_m2=0.,flow_m3_s=0.);d=replace(d,rain_kg_s=1e-5,rain_c=25.,solar_w_m2=0.)
        z=[replace(a,evap_m2=0.,heat_m2=0.,rad_m2=0.,proj_m2=0.,drain_per_s=k) for a,k in zip(z,(.001,.002))]
        r=solve(z,d,c,y,600.,.5);rows=[]
        for i,a in enumerate(z):
            w=d.rain_kg_s/a.drain_per_s+(y[2*i]-d.rain_kg_s/a.drain_per_s)*math.exp(-a.drain_per_s*600.)
            u=(a.dry_kg*1400.+w*4180.)*25.
            require(abs(r['state'][2*i]-w)<1e-12 and abs(r['state'][2*i+1]-u)<1e-7,'liquid reference mismatch')
            rows.append(dict(water_reference_kg=w,water_error_kg=r['state'][2*i]-w,energy_error_j=r['state'][2*i+1]-u))
        return rows
    def independent_flux():
        rows=[]
        for x,t in ((.6,20.),(.001,30.)):
            z,d,c,y=fixture();z=[replace(z[0],drain_per_s=.0001),z[1]];d=replace(d,rain_kg_s=1e-6);y=list(y)
            y[0]=z[0].dry_kg*x;y[1]=(z[0].dry_kg*1400.+y[0]*4180.)*t
            mass,heat,dm,bm,be,res,cover,terms=fluxes(z,d,c,tuple(y))
            ra=d.rh*sat(d.air_c);rho=y[4]/c['volume_m3'];expected_mass=[];expected_heat=[];open_flux=[];qs=[];liquids=[]
            for i,a in enumerate(z):
                ti=y[2*i+1]/(a.dry_kg*1400.+y[2*i]*4180.);aw=1-math.exp(-y[2*i]/a.dry_kg/a.xb)
                mo=(a.evap_m2-(c['area_cover_m2'] if i==0 else 0.))*(1+d.wind_m_s)/a.resistance_s_m*(aw*sat(ti)-ra)
                mc=c['area_cover_m2']/c['cover_resistance_s_m']*(aw*sat(ti)-rho) if i==0 else 0.
                q=a.alpha*d.solar_w_m2*a.proj_m2+(4+4*math.sqrt(d.wind_m_s))*a.heat_m2*(d.air_c-ti)+a.emissivity*5.670374419e-8*a.rad_m2*((d.surround_c+273.15)**4-(ti+273.15)**4)
                liquid=d.rain_kg_s*4180*d.rain_c-a.drain_per_s*y[2*i]*4180*ti
                expected_mass.append(-mo-mc+d.rain_kg_s-a.drain_per_s*y[2*i]);expected_heat.append(q-(mo+mc)*vapor_h(ti)+liquid)
                open_flux.append(-mo*vapor_h(ti));qs.append(q);liquids.append(liquid)
                if i==0:expected_cover=mc
            vent=c['flow_m3_s']*(ra-rho)
            reservoir=vapor_u(d.air_c)*(expected_cover+vent)-expected_cover*vapor_h(t)-vent*vapor_h(d.air_c)
            expected_boundary=sum(qs)+sum(open_flux)+sum(liquids)+vent*vapor_h(d.air_c)+reservoir
            require(max(abs(a-b) for a,b in zip(mass,expected_mass))<1e-15,'double counted water')
            require(max(abs(a-b) for a,b in zip(heat,expected_heat))<1e-9,'heat flow mismatch')
            require(abs(be-expected_boundary)<1e-9 and abs(res-reservoir)<1e-9,'external boundary/reservoir mismatch')
            require(abs(dm-(expected_cover+vent))<1e-15 and abs(bm-(sum(mass)+dm))<1e-15,'internal mass not cancelled')
            rows.append(dict(x=x,temperature_c=t,cover_kg_s=cover,external_energy_W=be,reservoir_W=res))
        return rows
    def transitions():
        z,d,c,y=fixture();a=solve(z,d,c,y,12.3,.11)['state'];new=replace(d,air_c=27.,rh=.4,solar_w_m2=50.,wind_m_s=1.2)
        b,jump=transition(z,d,new,c,a);expected=a[4]*(1850.-461.5)*2.
        require(b[:7]==a[:7] and abs(jump-expected)<1e-14,'transition reset or wrong energy jump')
        whole=run_segments(z,c,y,[(12.3,d),(30.7,new)],.11,d)
        resumed=run_segments(z,c,a,[(18.4,new)],.11,d)
        require(max(abs(v-w) for v,w in zip(whole['final_state'],resumed['final_state']))<1e-8,'restart lost state or boundary')
        end=whole['final_state'];energy=end[1]+end[3]+end[4]*vapor_u(new.air_c)-(y[1]+y[3]+y[4]*vapor_u(d.air_c))
        require(abs(energy-(end[7]-y[7]))<1e-7,'cumulative energy ledger')
        mass=(end[0]+end[2]+end[4])-(y[0]+y[2]+y[4]);require(abs(mass-(end[6]-y[6]))<1e-13,'cumulative mass ledger')
        return dict(jump_reference_j=expected,jump_computed_j=jump,total_energy_error_j=energy-(end[7]-y[7]),total_water_error_kg=mass-(end[6]-y[6]))
    def convergence_and_domain():
        z,d,c,y=fixture();rows=[]
        for h in (.4,.2,.1):
            r=solve(z,d,c,y,60.,h);rows.append(dict(dt=h,energy_residual_j=r['energy_residual_j'],water_residual_kg=r['water_residual_kg']))
        require(all(abs(b['energy_residual_j'])<.4*abs(a['energy_residual_j']) for a,b in zip(rows,rows[1:])),'boundary quadrature not converging')
        ref=solve(z,d,c,y,60.,.01,method='midpoint');fine=solve(z,d,c,y,60.,.1)
        require(abs(ref['local_rh']-fine['local_rh'])<1e-6 and abs(ref['state'][1]-fine['state'][1])<.001,'independent integrator differs')
        rejected=[]
        for label,action in [('condensation',lambda:transition(z,d,replace(d,air_c=0.),c,y)),
                             ('bad_cover',lambda:solve(z,d,dict(c,area_cover_m2=10.),y,1.,.1)),
                             ('string_clamp',lambda:solve(z,d,c,y,1.,.1,clamped='false')),
                             ('zero_dt',lambda:solve(z,d,c,y,1.,0.))]:
            try:action()
            except ValueError:rejected.append(label)
            else:raise AssertionError('accepted '+label)
        return dict(rows=rows,rejected=rejected,rh_difference=ref['local_rh']-fine['local_rh'])
    def replay():
        old={p:sha(p) for p in manifest['artifacts_sha256']};old[P+'results/manifest.json']=sha(P+'results/manifest.json')
        cmd=[sys.executable,'-B','-X','utf8',P+'replay.py'];p=subprocess.run(cmd,cwd=ROOT,capture_output=True,encoding='utf-8',timeout=120)
        require(p.returncode==0,p.stdout+p.stderr);require(all(sha(k)==h for k,h in old.items()),'replay changed outputs')
        return dict(command=cmd,output=p.stdout)
    for name,action in [('worker_suite',suite),('source_integrity',integrity),('heat_and_sensor_analytic',heat_analytics),('liquid_input_drain_analytic',liquid_analytic),('independent_boundary_flux',independent_flux),('boundary_transition_and_resume',transitions),('convergence_and_domain',convergence_and_domain),('artifact_replay',replay)]:check(name,action)
    require(all(sha(p)==h for p,h in before.items()),'inputs changed during verification')
    passed=all(c['passed'] for c in checks)
    r=dict(schema_version=1,task='T10',milestone='M5',verified_at=datetime.now(timezone.utc).isoformat(),python=sys.version,label='SYNTHETIC_UNCALIBRATED',collection_authorized=False,passed=passed,checks=checks,reviewed_sha256=before,limits='Prescribed local air temperature and synthetic RH response, not full moist-air/structure thermal closure or calibrated real-garment prediction.')
    if args.write_receipt:RECEIPT.write_bytes((json.dumps(r,ensure_ascii=False,indent=2)+'\n').encode())
    print(json.dumps(dict(passed=passed,checks=[{k:c[k] for k in ('name','passed','error') if k in c} for c in checks]),ensure_ascii=False,indent=2));return 0 if passed else 1


if __name__=='__main__':raise SystemExit(main())
