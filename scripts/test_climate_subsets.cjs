const assert = require('node:assert/strict');
const fs = require('node:fs');
const zlib = require('node:zlib');
const S = require('../assets/climate-subsets.js');
const json = path => JSON.parse(path.endsWith('.gz') ? zlib.gunzipSync(fs.readFileSync(path)) : fs.readFileSync(path));
assert.equal(S.mean([null,undefined,2,4]),3);
assert.equal(S.mean([null]),null);
assert.equal(S.changes([0,0],[1,2]).percent_change,null);
assert.equal(S.changes([null],[1]).absolute_change,null);
assert(Math.abs(S.aggregateRecord([{historical:2,future:4,ci05:1},{historical:8,future:8,ci05:2}]).percent_change-20)<1e-12);
assert.equal(S.aggregateRecord([{historical:2,future:4,ci05:1}]).ci05,null);
const explorer=json('climate-change/explorer.json');
let annualChecks=0,densityChecks=0;
for(const [url,reference] of Object.entries(explorer.runs)) {
 const run=json('climate-change/'+url), payload=json('climate-change/'+reference.url);
 for(const season of ['all','jjas']) {
  const actual=S.subsetRun(run,payload,{season,region:'all',location:'passage',month:0,category:1},{});
  assert.equal(actual.selectedEvents.length,run.seasonal[season].counts.events);
  for(const [i,row] of run.seasonal[season].annual.entries()) for(const [key,value] of Object.entries(row)) {
   const got=actual.seasonal[season].annual[i][key];
   if(value===null)assert.equal(got,null,`${url}/${season}/${key}`);
   else assert(Math.abs(got-value)<1e-8,`${url}/${season}/${key}: ${got} != ${value}`);
   annualChecks++;
  }
  for(const kind of ['track_density','genesis_density','lysis_density']) {
   assert.deepEqual(actual.seasonal[season][kind].unique_track_counts,run.seasonal[season][kind].unique_track_counts,`${url}/${season}/${kind}`);densityChecks++;
  }
 }
 const july=S.subsetRun(run,payload,{season:'jjas',region:'all',location:'passage',month:7,category:2},{});
 assert(july.selectedEvents.every(e=>e.genesis_month===7&&e.peak_category>=2));
 const box=S.filter(S.decode(payload),{season:'all',region:'box',location:'passage',box:[75,15,85,25],category:1},{});
 assert(box.every(e=>e.path.some(p=>p[0]>=75&&p[0]<=85&&p[1]>=15&&p[1]<=25)));
 const empty=S.annual([],run.coverage,payload.metric_definitions);
 assert(empty.every(r=>r.systems===0&&r.mean_peak_wind_ms===null));
}
const manifest=json('climate-change/manifest.json'),index=json('climate-change/'+manifest.index.path);
const load=pair=>({pair,...Object.fromEntries(['historical','future','change','impact'].map(role=>[role,pair[role]?json('climate-change/'+pair[role].url):null]))});
let ensembleChecks=0;
for(const pair of index.pairs.filter(p=>p.kind==='multi-model')) {
 const base=load(pair),entries=pair.model_ids.map(id=>load(index.pairs.find(p=>p.id===id)));
 const calculated=S.combine(entries,base,'jjas',base.historical.metric_definitions);
 for(const [key,expected] of Object.entries(base.change.seasonal_changes.jjas))for(const field of ['historical','future','absolute_change','percent_change']) {
  const actual=calculated.change.seasonal_changes.jjas[key][field];
  if(expected[field]===null)assert.equal(actual,null);else assert(Math.abs(actual-expected[field])<1e-8,`${pair.id}/${key}/${field}: ${actual} vs ${expected[field]}`);ensembleChecks++;
 }
 for(const role of ['historical','future'])for(const [key,value] of Object.entries(base[role].seasonal.jjas.class_counts))assert(Math.abs(calculated[role].seasonal.jjas.class_counts[key]-value)<1e-8);
 assert.deepEqual(calculated.change.track_density_agreement.jjas.summary,base.change.track_density_agreement.jjas.summary);
}
console.log(JSON.stringify({annualChecks,densityChecks,ensembleChecks,sourceReferences:Object.keys(explorer.runs).length,passed:true}));
