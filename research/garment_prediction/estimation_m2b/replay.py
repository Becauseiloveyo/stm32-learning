import json,hashlib
from pathlib import Path
from eta import calculate
ROOT=Path('research/garment_prediction/estimation_m2b'); OUT=ROOT/'results'
def read(p): return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x): Path(p).write_bytes((json.dumps(x,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
inputs={}
for path,key in [('docs/team/baseline-t10-m2b/snapshot.json','inputs_sha256'),('docs/verification/T10-M1-tests.json','reviewed_sha256'),('docs/verification/T10-M2a-tests.json','reviewed_sha256')]:
    for name,expected in read(path)[key].items():
        if sha(name)!=expected: raise RuntimeError('accepted input mismatch: '+name)
        inputs[name]=expected
    inputs[path]=sha(path)
for path in ['docs/team/assignments/algorithm-t10-m2b.md',*[str(ROOT/f).replace('\\','/') for f in ['eta.py','test_eta.py','config.json','replay.py']]]:inputs[path]=sha(path)
config=read(ROOT/'config.json'); out=calculate(config['request']);OUT.mkdir(exist_ok=True);p=OUT/'scenarios.json';save(p,out)
save(OUT/'manifest.json',{'label':'SYNTHETIC_UNCALIBRATED','seed':None,'commands':['python -B -X utf8 research/garment_prediction/estimation_m2b/test_eta.py','python -B -X utf8 research/garment_prediction/estimation_m2b/replay.py'],'inputs_sha256':dict(sorted(inputs.items())),'artifacts_sha256':{p.as_posix():sha(p)},'limits':'POSIX paths, UTF-8 LF; floating point may differ between platforms'})
print('Saved',p,'verified inputs',len(inputs));print('statuses',[(x['member_id'],x['scenario_id'],x['status'],x['first_joint_sample_s'],x['later_sample_above_target']) for x in out['cases']]);print('upper',out['upper_sample_s'])
