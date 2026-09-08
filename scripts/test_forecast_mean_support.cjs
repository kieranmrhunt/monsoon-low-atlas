// Test the actual browser implementation without booting network/UI services.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const zlib = require('node:zlib');
const repo = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(repo, 'index.html'), 'utf8');
const asset = html.match(/src="(assets\/forecast-app\.[a-f0-9]+\.js)"/)[1];
let source = fs.readFileSync(path.join(repo, asset), 'utf8');
source = source.replace('let reanalysisManifestPromise = null;', `
globalThis.qa = {state, ensembleMeanMinimum, splitPlotPath, systemMeanGeometry,
  systemReferenceTrack, meanTrack, forecastMapPaths, forecastSystemAt}; return;
let reanalysisManifestPromise = null;`);
const root = {querySelector: () => ({})};
const sandbox = {
  document: {getElementById: id => id === 'mla-data-config' ? {textContent: '{}'} : root},
  localStorage: {getItem: () => null}
};
vm.runInNewContext(source, sandbox);
const q = sandbox.qa;
const make = (count, total = 31, kind = 'ensemble') => {
  const tracks = Array.from({length: count}, (_, i) => ({id: `t${i}`, member: `p${i}`,
    points: [0, 1, 2, 3, 4].map(h => [h, 80 + .1*h + .01*i, 15])}));
  return {model: {kind}, members: {expected: total, available: total}, tracks,
    system: {id: 'S01', track_ids: tracks.map(t => t.id), member_count: count}};
};
for (const [total, expected] of [[30, 11], [31, 11], [50, 17], [51, 18], [64, 22]]) {
  const p = make(expected - 1, total);
  assert.equal(q.ensembleMeanMinimum(p), expected);
  assert.equal(q.meanTrack(p, p.system).length, 0, `no mean for ${expected-1}/${total}`);
  const supported = make(expected, total);
  assert.equal(q.meanTrack(supported, supported.system).length, 5);
}
const partial = make(20, 64);
partial.members.available = 45;
assert.equal(q.meanTrack(partial, partial.system).length, 0, 'missing downloads must not shrink the full-ensemble denominator');
const unknown = make(5); delete unknown.members;
assert.equal(q.meanTrack(unknown, unknown.system).length, 0, 'unknown denominator cannot certify a mean');
const duplicate = make(10);
duplicate.tracks.push({...duplicate.tracks[0], id: 'second-branch'});
duplicate.system.track_ids.push('second-branch');
assert.equal(q.meanTrack(duplicate, duplicate.system).length, 0, 'duplicate member branches must not add votes');
const low = make(1);
q.state.showMembers = false;
assert.equal(q.forecastMapPaths(low, low.system).memberPaths.length, 1, 'low-support tracks survive the members toggle');
assert.equal(q.systemReferenceTrack(low, low.system).length, 5, 'low-support systems remain in the explorer');
const gap = make(11);
gap.tracks[10].points = gap.tracks[10].points.filter(p => p[0] !== 2);
const paths = q.forecastMapPaths(gap, gap.system);
assert.deepEqual(Array.from(paths.meanPaths, p => Array.from(p, x => x[0])), [[0,1],[3,4]], 'never bridge unsupported hours');
assert(paths.memberPaths.some(p => p.some(x => x[0] === 2)), 'members cover the gap');
assert(paths.memberPaths.some(p => p.length >= 3), 'thin paths connect through a single unsupported hour');
const stable = make(11);
assert.equal(q.forecastMapPaths(stable, stable.system).memberPaths.length, 0);
q.state.showMembers = true;
assert.equal(q.forecastMapPaths(stable, stable.system).memberPaths.length, 11);
const deterministic = make(1, 1, 'deterministic');
assert.equal(q.meanTrack(deterministic, deterministic.system).length, 5);
assert.equal(q.forecastMapPaths(deterministic, deterministic.system).memberPaths.length, 0);
const jumping = make(11);
for (const track of jumping.tracks) for (const point of track.points) if (point[0] >= 2) point[1] += 20;
assert.equal(q.forecastMapPaths(jumping, jumping.system).meanPaths.length, 2, 'do not connect unphysical mean jumps');

if (process.argv[2]) {
  const payload = JSON.parse(zlib.gunzipSync(fs.readFileSync(process.argv[2])));
  const support = payload.systems.map(s => ({system: s.id, member_count: s.member_count,
    mean_hours: q.meanTrack(payload, s).length, member_paths: q.forecastMapPaths(payload, s).memberPaths.length}));
  console.log(JSON.stringify({cycle: payload.cycle, minimum_members: q.ensembleMeanMinimum(payload), systems: support}, null, 2));
}
console.log('Forecast mean-support tests passed.');
