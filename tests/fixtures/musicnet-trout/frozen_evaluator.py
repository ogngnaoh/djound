"""Apply preregistered thresholds to a recorded source trace. No inference or application code."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np

D = Path(__file__).resolve().parents[1]

def read_notes(path):
    if path.suffix == '.csv':
        with path.open() as stream:
            return sorted((float(r['start_s']), float(r['end_s']), int(r['midi'])) for r in csv.DictReader(stream))
    with path.open() as stream:
        data = json.load(stream)
    return sorted((float(a), float(b), int(p)) for a, b, p, *_ in data['melody'])

def align(ref, out, tolerance, split):
    n, m = len(ref), len(out)
    dp = [[(0, 0, 0.)] * (m+1) for _ in range(n+1)]
    back = np.zeros((n+1, m+1), dtype='int8')
    for i, (a, b, p) in enumerate(ref, 1):
        for j, (x, y, q) in enumerate(out, 1):
            parents = [(dp[i-1][j], 1), (dp[i][j-1], 2)]
            overlap = min(b,y)-max(a,x)
            close = abs(a-x) <= .18 and overlap >= .04
            contained = abs(a-x) <= .25 and overlap >= .5*min(y-x,b-a)
            left_ref = a < split - 1e-7
            left_out = x < split - 1e-7
            second_pickup = abs(a-split) < 1e-7 and split-.18 <= x < split
            same_phrase = left_ref == left_out or second_pickup
            if same_phrase and abs(p-q) <= tolerance and (close or contained):
                count, exact, diff = dp[i-1][j-1]
                parents.append(((count+1, exact+int(p==q), diff-abs(a-x)-abs((y-x)-(b-a))), 3))
            dp[i][j], back[i,j] = max(parents)
    result = []
    i, j = n,m
    while i and j:
        action = back[i,j]
        if action == 3:
            result.append((i-1,j-1))
        i -= action in (1,3)
        j -= action in (2,3)
    return result[::-1]

def raster(notes, grid):
    pitch = np.full(len(grid), -1000)
    for a,b,p in notes:
        pitch[(grid>=a)&(grid<b)]=p
    return pitch

def ratio(count, total):
    return round(int(count)/int(total),4) if total else None

def assess(path):
    manifest = json.loads((D/'manifest.json').read_text())
    ref = read_notes(D/'reference.csv')
    out = read_notes(path)
    split = manifest['phrases'][1]['start_s']
    within = align(ref,out,1,split)
    exact = align(ref,out,0,split)
    idx = {r for r,o in within}
    rows = list(csv.DictReader((D/'parts.csv').open()))
    grid = np.arange(.01,manifest['phrases'][-1]['end_s'],.02)
    src = raster(ref,grid)
    result = raster(out,grid)
    voiced = src>0
    sounding = result>0
    correct = np.abs(result-src)<=1
    sameclass = ((result-src)%12==0)&(~correct)&sounding&voiced
    competitors = {}
    for r in rows:
        if r['instrument']=='42': continue
        mask=(grid>=float(r['start_s']))&(grid<float(r['end_s']))
        match=(np.abs(result-int(r['midi']))<=1)&~correct&voiced&mask
        if r['instrument'] not in competitors:
            competitors[r['instrument']]=np.zeros(len(grid),dtype=bool)
        competitors[r['instrument']] |= match
    competing = np.any(np.stack(list(competitors.values())),axis=0)
    switch_mask = competing | sameclass
    # Fill isolated holes for run grouping; never count them as attributed frames.
    filled=switch_mask.copy()
    filled[1:-1] |= switch_mask[2:] & switch_mask[:-2]
    sections = np.diff(np.r_[False,filled,False].astype(int))
    switches=[]
    for lo,hi in zip(np.flatnonzero(sections==1),np.flatnonzero(sections==-1)):
        if np.count_nonzero(switch_mask[lo:hi])<6: continue
        voices={k:round(.02*int(mask[lo:hi].sum()),3) for k,mask in competitors.items() if mask[lo:hi].any()}
        switches.append({'start_s':round(float(grid[lo]-.01),3),'end_s':round(float(grid[hi-1]+.01),3),'competing_program_seconds':voices,'wrong_octave_seconds':round(.02*int(sameclass[lo:hi].sum()),3)})
    phrases=[]
    for phr in manifest['phrases']:
        ids={i for i,(a,*_) in enumerate(ref) if phr['start_s']-1e-7<=a<phr['end_s']-1e-7}
        assert len(ids)==phr['count']
        mask=voiced&(grid>=phr['start_s'])&(grid<phr['end_s'])
        phrases.append({'name':phr['name'],'matched':len(ids&idx),'total':len(ids),'recall':ratio(len(ids&idx),len(ids)),'exact':len(ids&{r for r,o in exact}),'voiced_frame_within_one':ratio((mask&correct).sum(),mask.sum())})
    collapsed=[]
    for k,(a,b,p) in enumerate(ref[1:],1):
        x,y,last=ref[k-1]
        if last==p and (k not in idx or k-1 not in idx):
            for j,(s,e,q) in enumerate(out):
                if abs(p-q)<=1 and s<y-.04 and e>a+.04:
                    collapsed.append({'ref_pair':[k-1,k],'output_index':j}); break
    return {'trace_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'counts':{'reference':len(ref),'output':len(out),'within_one':len(within),'exact':len(exact)},'global_recall':ratio(len(within),len(ref)),'global_precision':ratio(len(within),len(out)),'global_exact_recall':ratio(len(exact),len(ref)),'phrases':phrases,'passed_note_gate':len(within)>=.8*len(ref) and all(p['matched']>=.7*p['total'] for p in phrases),'frame_evidence':{'window_s':.02,'voiced_samples':int(voiced.sum()),'reference_voiced_seconds':round(.02*int(voiced.sum()),3),'within_one':ratio((correct&voiced).sum(),voiced.sum()),'exact':ratio(((src==result)&voiced).sum(),voiced.sum()),'wrong_octave_same_pitch_class':ratio(sameclass.sum(),voiced.sum()),'attributable_competing':ratio(competing.sum(),voiced.sum()),'correct_silence_on_ref_rests':ratio((~sounding&~voiced).sum(),(~voiced).sum()),'missed_voicing':ratio((~sounding&voiced).sum(),voiced.sum())},'switches':switches,'adjacent_repeated_notes_merged':collapsed,'matched_reference_output_index_within_one':within}

if __name__ == '__main__':
    p=argparse.ArgumentParser(); p.add_argument('trace',type=Path); p.add_argument('save',type=Path,nargs='?')
    args=p.parse_args(); report=assess(args.trace); output=json.dumps(report,indent=2)
    if args.save: args.save.write_text(output+'\n')
    else: print(output)
