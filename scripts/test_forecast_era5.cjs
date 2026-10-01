// ERA5 must remain independently browsable with no forecast payload selected.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const zlib = require('node:zlib');
const repo = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(repo, 'index.html'), 'utf8');
const asset = html.match(/src="(assets\/forecast-app\.[a-f0-9]+\.js)"/)[1];
const config = JSON.parse(html.match(/id="mla-data-config"[^>]*>([^<]+)/)[1]);
let source = fs.readFileSync(path.join(repo, asset), 'utf8');
source = source.replace('let reanalysisManifestPromise = null;', `
globalThis.qa = {state, indexEra5Catalogue, era5CatalogueAvailable,
  era5AnalysisTracks, analysisOnlyTimeline, analysisTracksForDisplay, archiveMonths}; return;
let reanalysisManifestPromise = null;`);
const root = {querySelector: () => ({})};
const sandbox = {
  document: {getElementById: id => id === 'mla-data-config' ? {textContent: '{}'} : root},
  localStorage: {getItem: () => null}
};
vm.runInNewContext(source, sandbox);
const q = sandbox.qa;
const core = JSON.parse(zlib.gunzipSync(fs.readFileSync(path.join(repo, config.core))));
q.state.era5Catalogue = q.indexEra5Catalogue(core);
q.state.mode = 'archive';
q.state.manifest = {models: [], archive: [], tigge_archive: []};
assert(q.state.analysisSources.has('era5'), 'ERA5 is on by default');
assert.equal(q.state.archiveSelected.size, 0);
const when = Date.parse('2016-07-01T00:00:00Z');
const tracks = q.era5AnalysisTracks(when);
assert.deepEqual(Array.from(tracks, track => track.id), ['11158', '11159'], 'all ERA5 systems on the date appear without any forecasts');
const incompass = tracks.find(track => track.id === '11159');
assert.equal(incompass.marker[0], when / 3600000);
assert.equal(incompass.marker[1], 85.4418, 'longitude agrees with independently decoded catalogue');
assert.equal(incompass.marker[2], 17.9868, 'latitude agrees with independently decoded catalogue');
const later = q.era5AnalysisTracks(when + 6 * 3600000).find(track => track.id === '11159');
assert.equal(later.marker[0], when / 3600000 + 6, 'analysis-only slider moves the centre');
assert.notDeepEqual(Array.from(later.marker), Array.from(incompass.marker));
assert.equal(q.analysisOnlyTimeline(when).length, 4, 'analysis-only view has a six-hourly daily timeline');
q.state.isolateSystem = true;
assert.equal(q.analysisTracksForDisplay(tracks, null, track => track.marker).length, 2, 'stale forecast focus cannot hide independent ERA5 tracks');
const empty = Date.parse('1940-01-01T00:00:00Z');
assert(q.era5CatalogueAvailable(empty), 'a day without a storm is still covered');
assert.equal(q.era5AnalysisTracks(empty).length, 0);
assert.equal(q.analysisOnlyTimeline(empty).length, 4);
assert(q.archiveMonths().includes('1940-01'), 'calendar includes analysis-only months');
assert(!q.era5CatalogueAvailable(Date.parse('2026-01-01T00:00:00Z')), 'missing catalogue coverage is not reported as no storm');
assert.equal(q.era5AnalysisTracks(Date.parse('2026-01-01T00:00:00Z')).length, 0);
console.log('Forecast ERA5 independent-catalogue tests passed.');
