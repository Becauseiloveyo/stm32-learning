"""Two coupled zones, prescribed air-temperature reservoir; synthetic only."""
import math,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from model import Zone,Drive,validate,rho_sat,CW,CD,LV,CV,SIGMA,finite

RV = 461.5
def hv(t):return LV+CV*t
def uv(t):return hv(t)-RV*(t+273.15)

def check(zones,d,c,y):
    if len(zones)!=2 or len(y)!=8:raise ValueError('exactly two zones and eight states required')
    finite(*y,*c.values())
    if set(c)!={'area_cover_m2','cover_resistance_s_m','volume_m3','flow_m3_s','sensor_tau_s','sensor_bias_rh'}:raise ValueError('config keys')
    if not (0<=c['area_cover_m2']<=zones[0].evap_m2 and c['cover_resistance_s_m']>0 and c['volume_m3']>0 and c['flow_m3_s']>=0 and c['sensor_tau_s']>0 and -1<=c['sensor_bias_rh']<=1):raise ValueError('coupling outside domain')
    t0=validate(zones[0],d,y[0],y[1]); t1=validate(zones[1],d,y[2],y[3])
    rho=y[4]/c['volume_m3']; sat=rho_sat(d.air_c)
    if not (0<=rho<=sat and 0<=y[5]<=1 and 0<=y[5]+c['sensor_bias_rh']<=1):raise ValueError('condensation or sensor outside domain')
    return t0,t1,rho

def fluxes(zones,d,c,y,clamped=False):
    if type(clamped) is not bool:raise ValueError('clamped must be boolean')
    temps=check(zones,d,c,y);tc=temps[:2];rho=temps[2];ra=d.rh*rho_sat(d.air_c)
    mass=[];openflows=[];cover=0.;heat=[];solars=[];convs=[];rads=[];liquids=[];drains=[]
    for i,z in enumerate(zones):
        w=y[2*i];aw=-math.expm1(-w/(z.dry_kg*z.xb)); area=z.evap_m2-(c['area_cover_m2'] if i==0 else 0.)
        mo=area/(z.resistance_s_m/(1+d.wind_m_s))*(aw*rho_sat(tc[i])-ra)
        mc=c['area_cover_m2']/c['cover_resistance_s_m']*(aw*rho_sat(tc[i])-rho) if i==0 else 0.
        drain=z.drain_per_s*w; liquid=d.rain_kg_s*CW*d.rain_c-drain*CW*tc[i]
        solar=z.alpha*d.solar_w_m2*z.proj_m2;conv=(4+4*math.sqrt(d.wind_m_s))*z.heat_m2*(d.air_c-tc[i]);rad=z.emissivity*SIGMA*z.rad_m2*((d.surround_c+273.15)**4-(tc[i]+273.15)**4)
        mass.append(-mo-mc+d.rain_kg_s-drain);openflows.append(mo);heat.append(solar+conv+rad-(mo+mc)*hv(tc[i])+liquid);cover+=mc
        solars.append(solar);convs.append(conv);rads.append(rad);liquids.append(liquid);drains.append(drain)
    vent=c['flow_m3_s']*(ra-rho);dm=cover+vent
    # Reservoir absorbs the difference between vapor internal energy storage and enthalpy flows.
    gas_reservoir=uv(d.air_c)*dm-cover*hv(tc[0])-vent*hv(d.air_c)
    clamp_reservoir=0.
    if clamped:
        target_heat=[CW*tc[i]*mass[i] for i in range(2)]
        clamp_reservoir=sum(target_heat[i]-heat[i] for i in range(2));heat=target_heat
    terms={'solar_W':sum(solars),'convection_W':sum(convs),'radiation_W':sum(rads),
           'open_vapor_W':-sum(openflows[i]*hv(tc[i]) for i in range(2)),
           'vent_vapor_W':vent*hv(d.air_c),'liquid_W':sum(liquids),
           'gas_reservoir_W':gas_reservoir,'clamp_reservoir_W':clamp_reservoir,
           'open_mass_kg_s':-sum(openflows),'vent_mass_kg_s':vent,'liquid_mass_kg_s':2*d.rain_kg_s-sum(drains)}
    bmass=terms['open_mass_kg_s']+terms['vent_mass_kg_s']+terms['liquid_mass_kg_s']
    benergy=sum(terms[k] for k in ('solar_W','convection_W','radiation_W','open_vapor_W','vent_vapor_W','liquid_W','gas_reservoir_W','clamp_reservoir_W'))
    return mass,heat,dm,bmass,benergy,gas_reservoir+clamp_reservoir,cover,terms

def rhs(zones,d,c,y,clamped=False):
    mass,heat,dm,bmass,benergy,_,_,_=fluxes(zones,d,c,y,clamped)
    return (mass[0],heat[0],mass[1],heat[1],dm,(y[4]/c['volume_m3']/rho_sat(d.air_c)-y[5])/c['sensor_tau_s'],bmass,benergy)

def step(zones,d,c,y,dt,method='rk4',clamped=False):
    finite(dt)
    if type(clamped) is not bool:raise ValueError('clamped must be boolean')
    if dt<=0 or method not in ('rk4','midpoint'):raise ValueError('step/method invalid')
    def add(y,k,h):return tuple(a+h*b for a,b in zip(y,k))
    k1=rhs(zones,d,c,y,clamped)
    if method=='midpoint':out=add(y,rhs(zones,d,c,add(y,k1,dt/2),clamped),dt)
    else:
        k2=rhs(zones,d,c,add(y,k1,dt/2),clamped);k3=rhs(zones,d,c,add(y,k2,dt/2),clamped);k4=rhs(zones,d,c,add(y,k3,dt),clamped);out=tuple(a+dt*(b+2*e+2*f+g)/6 for a,b,e,f,g in zip(y,k1,k2,k3,k4))
    check(zones,d,c,out);return out

def transition(zones,old,new,c,y):
    check(zones,old,c,y)
    # Vapor sensible storage follows imposed instantaneous air-temperature boundary.
    a=list(y); jump=y[4]*(CV-RV)*(new.air_c-old.air_c);a[7]+=jump
    check(zones,new,c,a)
    return tuple(a),jump

def integrate(zones,d,c,y,duration,dt,method='rk4',clamped=False):
    finite(duration,dt)
    if type(clamped) is not bool:raise ValueError('clamped must be boolean')
    if duration<0 or dt<=0 or method not in ('rk4','midpoint'):raise ValueError('invalid time')
    check(zones,d,c,y);initial=y;t=0.;trap_mass=0.;trap_energy=0.;reservoir=0.;terms_total={}
    while t<duration:
        h=min(dt,duration-t);f0=fluxes(zones,d,c,y,clamped);end=step(zones,d,c,y,h,method,clamped);f1=fluxes(zones,d,c,end,clamped)
        trap_mass+=h*(f0[3]+f1[3])/2;trap_energy+=h*(f0[4]+f1[4])/2;reservoir+=h*(f0[5]+f1[5])/2
        for key in f0[7]:
            outkey=key.replace('_kg_s','_kg').replace('_W','_J')
            terms_total[outkey]=terms_total.get(outkey,0.)+h*(f0[7][key]+f1[7][key])/2
        y=end;t+=h
    dm=(y[0]+y[2]+y[4])-(initial[0]+initial[2]+initial[4]);de=(y[1]+y[3]+y[4]*uv(d.air_c))-(initial[1]+initial[3]+initial[4]*uv(d.air_c))
    return {'mode':'clamped_verification' if clamped else 'dynamic','state':y,'water_residual_kg':dm-trap_mass,'energy_residual_j':de-trap_energy,'reservoir_energy_j':reservoir,'boundary_integrals':terms_total,'local_rh':y[5]+c['sensor_bias_rh'],'label':'SYNTHETIC_UNCALIBRATED','collection_authorized':False}

def run_segments(zones,c,y,segments,dt,prior_drive,method='rk4',clamped=False):
    """Propagate identical state continuously; boundary change has explicit vapor-energy jump."""
    if not segments:raise ValueError('segments required')
    first=y;last_drive=prior_drive;check(zones,prior_drive,c,y);records=[];previous_end=0.
    for end,d in segments:
        finite(end)
        if end<=previous_end:raise ValueError('segment ends must increase')
        jump=0.
        y,jump=transition(zones,last_drive,d,c,y)
        res=integrate(zones,d,c,y,end-previous_end,dt,method,clamped)
        y=res['state'];records.append({'end_s':end,'boundary_jump_j':jump,'water_residual_kg':res['water_residual_kg'],'energy_residual_j':res['energy_residual_j'],'reservoir_energy_j':res['reservoir_energy_j'],'boundary_integrals':res['boundary_integrals']})
        previous_end=end;last_drive=d
    return {'state_version':'m5-v1','mode':'clamped_verification' if clamped else 'dynamic','initial_drive':vars(prior_drive),'final_drive':vars(last_drive),'initial_state':first,'final_state':y,'segments':records,'label':'SYNTHETIC_UNCALIBRATED','collection_authorized':False}
