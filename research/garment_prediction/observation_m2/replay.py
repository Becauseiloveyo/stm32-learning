"""Replay from repository root; verify accepted baseline before writing own outputs."""
import hashlib, json
from pathlib import Path
from witness import build
ROOT=Path('research/garment_prediction')
HERE=ROOT/'observation_m2'; OUT=HERE/'results'
def read(p): return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def digest(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,value): Path(p).write_bytes((json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode('utf-8'))
snapshot=read('docs/team/baseline-t10-m2/snapshot.json'); receipt=read('docs/verification/T10-M1-tests.json')
expected={**snapshot['inputs_sha256'],**receipt['reviewed_sha256']}
for path,sha in expected.items():
    if digest(path)!=sha: raise RuntimeError('accepted input changed: '+path)
config=read(HERE/'config.json'); m1=read(ROOT/'config.json')
result=build(config,m1)
OUT.mkdir(exist_ok=True)
artifact=OUT/'witness.json'; save(artifact,result)
sources=set(expected)|{'docs/team/baseline-t10-m2/snapshot.json','docs/team/assignments/algorithm-t10-m2.md'}|{(HERE/p).as_posix() for p in ('contract.py','witness.py','test_contract.py','config.json','replay.py')}
manifest={'label':'SYNTHETIC_UNCALIBRATED','seed':None,'commands':['python -B -X utf8 research/garment_prediction/observation_m2/test_contract.py','python -B -X utf8 research/garment_prediction/observation_m2/replay.py'],'inputs_sha256':{p:digest(p) for p in sorted(sources)},'artifacts_sha256':{artifact.as_posix():digest(artifact)},'replay_limits':'UTF-8 LF; POSIX paths; floating-point byte identity across platforms not guaranteed.'}
save(OUT/'manifest.json',manifest)
print('Generated',artifact,'and manifest; accepted hashes verified:',len(expected))
