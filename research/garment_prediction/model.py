"""Synthetic two-zone open garment model. SI units; temperature in Celsius."""
import math
from dataclasses import dataclass

LABEL = 'SYNTHETIC_UNCALIBRATED'
SIGMA = 5.670374419e-8
CW, CD, LV, CV = 4180., 1400., 2500000., 1850.

@dataclass(frozen=True)
class Zone:
    dry_kg: float
    evap_m2: float
    proj_m2: float
    heat_m2: float
    rad_m2: float
    xb: float
    resistance_s_m: float
    drain_per_s: float
    alpha: float
    emissivity: float

@dataclass(frozen=True)
class Drive:
    air_c: float
    rh: float
    wind_m_s: float
    solar_w_m2: float
    surround_c: float
    rain_kg_s: float
    rain_c: float


def finite(*xs):
    if any(not isinstance(x, (int, float)) or isinstance(x, bool) or not math.isfinite(x) for x in xs):
        raise ValueError('all inputs must be finite numbers')

def validate(z, d, w, u):
    finite(*vars(z).values(), *vars(d).values(), w, u)
    if not (z.dry_kg > 0 and z.evap_m2 >= 0 and z.heat_m2 >= 0 and z.rad_m2 >= 0 and z.proj_m2 >= 0 and z.xb > 0 and z.resistance_s_m > 0 and z.drain_per_s >= 0 and 0 <= z.alpha <= 1 and 0 <= z.emissivity <= 1):
        raise ValueError('invalid zone')
    if not (0 <= d.air_c <= 50 and 0 <= d.surround_c <= 50 and 0 <= d.rain_c <= 50 and 0 <= d.rh <= 1 and 0 <= d.wind_m_s <= 10 and 0 <= d.solar_w_m2 <= 1200 and 0 <= d.rain_kg_s <= .01):
        raise ValueError('drive outside research domain')
    if w < 0 or w / z.dry_kg > 5:
        raise ValueError('water outside research domain')
    t = u / (z.dry_kg * CD + w * CW)
    if not 0 <= t <= 50:
        raise ValueError('garment temperature outside research domain')
    return t

def rho_sat(t):
    return 610.94 * math.exp(17.625 * t / (t + 243.04)) / (461.5 * (t + 273.15))

def state(z, x, t):
    finite(x, t)
    w = z.dry_kg * x
    u = (z.dry_kg * CD + w * CW) * t
    return (w, u)

def flux(z, d, y):
    w, u = y
    t = validate(z, d, w, u)
    x = w / z.dry_kg
    aw = -math.expm1(-x / z.xb)
    # Positive m is evaporation; negative is uptake. Natural exchange remains at zero wind.
    m = z.evap_m2 * (aw * rho_sat(t) - d.rh * rho_sat(d.air_c)) / (z.resistance_s_m / (1 + d.wind_m_s))
    drain = z.drain_per_s * w
    solar = z.alpha * d.solar_w_m2 * z.proj_m2
    conv = (4.0 + 4.0 * math.sqrt(d.wind_m_s)) * z.heat_m2 * (d.air_c - t)
    rad = z.emissivity * SIGMA * z.rad_m2 * ((d.surround_c + 273.15)**4 - (t + 273.15)**4)
    # Vapor enthalpy referenced to liquid water at 0 C; same transfer enthalpy for uptake.
    hv = LV + CV * t
    rain_h = CW * d.rain_c
    drain_h = CW * t
    return (m, d.rain_kg_s, drain, solar, conv, rad, hv, rain_h, drain_h)

def rhs(z, d, y):
    m, rain, drain, solar, conv, rad, hv, hr, hd = flux(z,d,y)
    return (-m + rain - drain, solar + conv + rad - m*hv + rain*hr - drain*hd)

class IntegrationStepError(ValueError):
    """An intermediate or final integration state left the declared domain."""

def validate_method(method):
    if method not in ('rk4', 'midpoint'):
        raise ValueError('unknown method')

def _advance(z, d, y, dt, method='rk4'):
    def add(a,b,k): return tuple(x+k*v for x,v in zip(a,b))
    a = rhs(z,d,y)
    if method == 'midpoint':
        b = rhs(z,d,add(y,a,dt/2))
        out = add(y,b,dt)
    elif method == 'rk4':
        b = rhs(z,d,add(y,a,dt/2)); c = rhs(z,d,add(y,b,dt/2)); e = rhs(z,d,add(y,c,dt))
        out = tuple(v + dt*(aa+2*bb+2*cc+ee)/6 for v,aa,bb,cc,ee in zip(y,a,b,c,e))
    else: raise ValueError('unknown method')
    validate(z,d,*out)
    return out

def advance(z, d, y, dt, method='rk4'):
    # Initial invalid inputs are distinct from a numerical stage leaving the domain.
    finite(dt)
    if dt <= 0: raise ValueError('dt must be positive')
    validate_method(method)
    validate(z, d, *y)
    try:
        return _advance(z, d, y, dt, method)
    except ValueError as exc:
        raise IntegrationStepError('integration step failed: ' + str(exc)) from exc

def run(z,d,y,duration,dt,method='rk4'):
    finite(duration,dt)
    if duration < 0 or dt <= 0: raise ValueError('invalid time')
    validate_method(method)
    validate(z,d,*y)
    # Independent endpoint trapezoidal quadrature of physical boundary fluxes.
    initial=y; ledger=[0.,0.]; elapsed=0.; steps=0
    while elapsed < duration:
        h=min(dt,duration-elapsed)
        f0=flux(z,d,y); new=advance(z,d,y,h,method); f1=flux(z,d,new)
        def terms(f):
            m,r,dr,sol,con,rad,hv,hr,hd=f
            return (-m+r-dr, sol+con+rad-m*hv+r*hr-dr*hd)
        q0,q1=terms(f0),terms(f1)
        for i in range(2): ledger[i] += h*(q0[i]+q1[i])/2
        y=new; elapsed+=h; steps+=1
    return {'water_kg':y[0], 'energy_j':y[1], 'temperature_c':validate(z,d,*y), 'delta_water_kg':y[0]-initial[0], 'delta_energy_j':y[1]-initial[1], 'ledger_water_kg':ledger[0], 'ledger_energy_j':ledger[1], 'water_residual_kg':y[0]-initial[0]-ledger[0], 'energy_residual_j':y[1]-initial[1]-ledger[1], 'steps':steps, 'collection_authorized':False, 'label':LABEL}

def run_two(zones, drives, states, duration, dt, method='rk4'):
    if len(zones)!=2 or len(drives)!=2 or len(states)!=2: raise ValueError('exactly two zones required')
    return [run(z,d,y,duration,dt,method) for z,d,y in zip(zones,drives,states)]
