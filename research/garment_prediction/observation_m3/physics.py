"""Synthetic isothermal finite garment + well-mixed local air; no caloric closure."""
import math, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from model import rho_sat

def numeric(x):
    if isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x): raise ValueError('finite numeric input required')
    return x

def params(p):
    fields=['dry_kg','xb','area_open_m2','area_cover_m2','r_open_s_m','r_cover_s_m','volume_m3','flow_m3_s','wall_source_kg_s','sensor_tau_s','sensor_bias_rh','temperature_c','ambient_rh']
    if type(p)!=dict or set(p)!=set(fields): raise ValueError('parameter fields mismatch')
    for v in p.values(): numeric(v)
    if not (p['dry_kg']>0 and p['xb']>0 and p['area_open_m2']>=0 and p['area_cover_m2']>=0 and p['r_open_s_m']>0 and p['r_cover_s_m']>0 and p['volume_m3']>0 and p['flow_m3_s']>=0 and p['wall_source_kg_s']>=0 and p['sensor_tau_s']>0 and -1<=p['sensor_bias_rh']<=1 and 0<=p['temperature_c']<=50 and 0<=p['ambient_rh']<=1): raise ValueError('parameter outside domain')

def check(p,y):
    for x in y: numeric(x)
    w,r,z,ledger=y; sat=rho_sat(p['temperature_c'])
    if not (0<=w<=5*p['dry_kg'] and 0<=r<=sat and 0<=z<=sat and 0<=z/sat+p['sensor_bias_rh']<=1): raise ValueError('state outside unsaturated domain')

def rates(p,y):
    check(p,y); w,r,z,_=y; sat=rho_sat(p['temperature_c']); ambient=p['ambient_rh']*sat
    aw=-math.expm1(-w/(p['dry_kg']*p['xb']))
    open_flux=p['area_open_m2']/p['r_open_s_m']*(aw*sat-ambient)
    cover_flux=p['area_cover_m2']/p['r_cover_s_m']*(aw*sat-r)
    ventilation=p['flow_m3_s']*(ambient-r)
    wall=p['wall_source_kg_s']
    return (-open_flux-cover_flux,(cover_flux+ventilation+wall)/p['volume_m3'],(r-z)/p['sensor_tau_s'],-open_flux+ventilation+wall)

def advance(p,y,dt,method='rk4'):
    numeric(dt)
    if dt<=0 or method not in ('rk4','midpoint'): raise ValueError('invalid step/method')
    def add(y,k,h):return tuple(a+h*b for a,b in zip(y,k))
    k1=rates(p,y)
    if method=='midpoint': out=add(y,rates(p,add(y,k1,dt/2)),dt)
    else:
        k2=rates(p,add(y,k1,dt/2)); k3=rates(p,add(y,k2,dt/2)); k4=rates(p,add(y,k3,dt))
        out=tuple(a+dt*(b+2*c+2*d+e)/6 for a,b,c,d,e in zip(y,k1,k2,k3,k4))
    check(p,out);return out

def simulate(p, x0, rh0, sensor_rh0, duration_s, dt, method='rk4'):
    params(p)
    for n in (x0,rh0,sensor_rh0,duration_s,dt):numeric(n)
    if not (0<=x0<=5 and 0<=rh0<=1 and 0<=sensor_rh0<=1 and duration_s>=0 and dt>0 and method in ('rk4','midpoint')): raise ValueError('invalid initial input')
    sat=rho_sat(p['temperature_c']); y=(p['dry_kg']*x0,sat*rh0,sat*sensor_rh0,0.)
    check(p,y);initial=y; t=0.;samples=[]
    def sample(t,y):return {'time_s':t,'ambient_temp_c':p['temperature_c'],'ambient_rh':p['ambient_rh'],'local_temp_c':p['temperature_c'],'local_rh':y[2]/sat+p['sensor_bias_rh']}
    samples.append(sample(t,y)); trap=0.
    while t<duration_s:
        h=min(dt,duration_s-t); f0=rates(p,y); z=advance(p,y,h,method); f1=rates(p,z)
        trap+=h*(f0[3]+f1[3])/2;y=z;t+=h;samples.append(sample(t,y))
    delta=(y[0]+p['volume_m3']*y[1])-(initial[0]+p['volume_m3']*initial[1])
    return {'label':'SYNTHETIC_UNCALIBRATED','collection_authorized':False,'samples':samples,'producer_only':{'initial_water_kg':initial[0],'water_kg':y[0],'initial_air_density_kg_m3':initial[1],'air_density_kg_m3':y[1],'boundary_integral_kg':y[3],'independent_trapezoid_kg':trap,'total_water_change_kg':delta,'balance_residual_kg':delta-trap}}
