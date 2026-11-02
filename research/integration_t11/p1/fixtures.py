"""Public synthetic inputs only; this file is never a consumer truth source."""
from copy import deepcopy
import json
from pathlib import Path
from research.garment_prediction.model import Zone, state

ROOT = Path(__file__).resolve().parents[3]


def request(target=5):
    req = json.loads((ROOT / 'research/garment_prediction/prediction_m6/config.json').read_text(encoding='utf-8'))['request']
    req['target_x'] = target
    return req


def analytic_request():
    """Finite-sample mathematical counterexample, not a clothing drying claim."""
    r = request(.3)
    r['step_s'] = 10
    r['candidates'] = r['candidates'][:1]
    c = r['candidates'][0]
    for z in c['zones']:
        z.update(evap_m2=0, proj_m2=0, heat_m2=0, rad_m2=0, drain_per_s=.001)
    c['coupling'].update(area_cover_m2=0, flow_m3_s=0)
    zones = [Zone(**z) for z in c['zones']]
    a, b = state(zones[0], 1, 25), state(zones[1], .8, 25)
    c['variants'][0]['initial_state'][:4] = [a[0], a[1], b[0], b[1]]
    for p in r['observations']:
        p['samples']['local_rh'].update(value=.5, uncertainty_abs=.001)
    r['history_segments'][0]['drive']['solar_w_m2'] = 0
    c['prior_drive']['solar_w_m2'] = 0
    r['futures'] = r['futures'][:1]
    r['futures'][0]['horizon_s'] = 2500
    r['futures'][0]['segments'] = [dict(end_s=2500, drive=deepcopy(c['prior_drive']), available_at_s=0)]
    return r
