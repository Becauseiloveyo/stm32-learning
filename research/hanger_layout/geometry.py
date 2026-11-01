"""Synthetic axis-aligned boxes, x horizontal, y up, z depth, mm."""
import math,itertools

def box(a):
 if type(a)!=list or len(a)!=6 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in a):raise ValueError('box: six finite numbers required')
 if not all(a[i]<a[i+3] for i in range(3)):raise ValueError('box min must be less than max')
 return a

def overlap_all(*args):
 for a in args:box(a)
 return all(max(a[i] for a in args)<min(a[i+3] for a in args) for i in range(3))

def overlaps(a,b):return overlap_all(a,b)

ENTITIES=('main','battery','hanger_bar','central_probe','reference_head','rain_head','charge_port','garment')
ALLOWED={frozenset(('main','charge_port')),frozenset(('central_probe','garment'))}

def evaluate(case):
 names=list(ENTITIES)+['hook_clearance','neck_clearance','neighbor_clearance','contact_zone','reference_vent','vent_obstacle','rain_exposure','rain_obstacle','port_insertion','port_obstacle']
 if set(case)!=set(names):raise ValueError('missing geometry')
 for a in case.values():box(a)
 collisions=[list(pair) for pair in itertools.combinations(ENTITIES,2) if frozenset(pair) not in ALLOWED and overlaps(case[pair[0]],case[pair[1]])]
 vent_blockers=['vent_obstacle']+[x for x in ENTITIES if x!='reference_head']
 rain_blockers=['rain_obstacle']+[x for x in ENTITIES if x!='rain_head']
 port_blockers=['port_obstacle']+[x for x in ENTITIES if x not in ('main','charge_port')]
 return {'no_unintended_intersections':not collisions,'unintended_intersections':collisions,'hook_clear':not any(overlaps(case[x],case['hook_clearance']) for x in ('main','battery')),'neck_clear':not any(overlaps(case[x],case['neck_clearance']) for x in ('main','battery')),'neighbor_clear':not any(overlaps(case[x],case['neighbor_clearance']) for x in ('main','battery','central_probe','reference_head','rain_head')),'central_contact_proxy':overlap_all(case['central_probe'],case['contact_zone'],case['garment']),'reference_vent_clear':not any(overlaps(case['reference_vent'],case[x]) for x in vent_blockers),'rain_exposure_clear':not any(overlaps(case['rain_exposure'],case[x]) for x in rain_blockers),'port_insertion_clear':not any(overlaps(case['port_insertion'],case[x]) for x in port_blockers)}
