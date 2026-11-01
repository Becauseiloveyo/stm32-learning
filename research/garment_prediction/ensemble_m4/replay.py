import json,hashlib
from pathlib import Path
from ensemble import compare
P=Path('research/garment_prediction/ensemble_m4');O=P/'results'
def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):Path(p).write_bytes((json.dumps(x,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
inputs={}
for path,key in [('docs/team/baseline-t10-m4/snapshot.json','inputs_sha256'),('docs/verification/T10-M1-tests.json','reviewed_sha256'),('docs/verification/T10-M2a-tests.json','reviewed_sha256'),('docs/verification/T10-M2b-tests.json','reviewed_sha256'),('docs/verification/T10-M3-tests.json','reviewed_sha256')]:
 for name,expected in read(path)[key].items():
  if sha(name)!=expected:raise RuntimeError('changed accepted input '+name)
  inputs[name]=expected
 inputs[path]=sha(path)
for f in ('ensemble.py','test_ensemble.py','config.json','replay.py'):inputs[(P/f).as_posix()]=sha(P/f)
c=read(P/'config.json');r=compare(*[c[k] for k in ('packets','policy','history','gate','assumptions','candidates','as_of_s')]);O.mkdir(exist_ok=True);out=O/'compatibility.json';save(out,r)
save(O/'manifest.json',{'label':'SYNTHETIC_UNCALIBRATED','seed':None,'commands':['python -B -X utf8 research/garment_prediction/ensemble_m4/test_ensemble.py','python -B -X utf8 research/garment_prediction/ensemble_m4/replay.py'],'inputs_sha256':dict(sorted(inputs.items())),'artifacts_sha256':{out.as_posix():sha(out)},'limits':'finite discrete conditional assumptions, numerical error bound not independently calibrated; cross-platform byte identity not guaranteed'})
print('Verified inputs',len(inputs),'set',r['set_status'],'candidates',[(x['id'],x['status']) for x in r['candidates']])
