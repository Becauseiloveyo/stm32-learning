import json,hashlib,time
from pathlib import Path
from integrated import predict
from verify_producer import verify
P=Path('research/garment_prediction/prediction_m6');O=P/'results'
def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,x):Path(p).write_bytes((json.dumps(x,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
inputs={}
for path,key in [('docs/team/baseline-t10-m6/snapshot.json','inputs_sha256'),('docs/verification/T10-M1-tests.json','reviewed_sha256'),('docs/verification/T10-M2a-tests.json','reviewed_sha256'),('docs/verification/T10-M2b-tests.json','reviewed_sha256'),('docs/verification/T10-M3-tests.json','reviewed_sha256'),('docs/verification/T10-M4-tests.json','reviewed_sha256'),('docs/verification/T10-M5-tests.json','reviewed_sha256')]:
 for name,expected in read(path)[key].items():
  if sha(name)!=expected:raise RuntimeError('accepted input changed '+name)
  inputs[name]=expected
 inputs[path]=sha(path)
for f in ('integrated.py','test_integrated.py','config.json','producer_fixture.json','verify_producer.py','replay.py'):inputs[(P/f).as_posix()]=sha(P/f)
producer_check=verify(); c=read(P/'config.json');t=time.perf_counter();r=predict(c['request']);elapsed=time.perf_counter()-t
O.mkdir(exist_ok=True);out=O/'prediction.json';save(out,r)
save(O/'manifest.json',{'label':'SYNTHETIC_UNCALIBRATED','seed':None,'commands':['python -B -X utf8 research/garment_prediction/prediction_m6/test_integrated.py','python -B -X utf8 research/garment_prediction/prediction_m6/replay.py'],'inputs_sha256':dict(sorted(inputs.items())),'artifacts_sha256':{out.as_posix():sha(out)},'producer_verification':producer_check,'limits':'producer hidden fixture never passed to predictor; float results platform dependent'})
print('inputs',len(inputs),'statuses',[(x['id'],x['status']) for x in r['rows']],'upper_remaining_s',r['upper_remaining_s'],'estimated_steps',r['estimated_max_steps'],'elapsed_s',round(elapsed,4))
