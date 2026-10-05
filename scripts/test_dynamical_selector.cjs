// Exercise the actual browser registry used for selectors and URL restoration.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const repo = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(repo, 'index.html'), 'utf8');
const asset = html.match(/src="(assets\/forecast-app\.[a-f0-9]+\.js)"/)[1];
let source = fs.readFileSync(path.join(repo, asset), 'utf8');
source = source.replace('let storedPreferences = {};',
  'globalThis.qa = {order: OPERATIONAL_MODEL_ORDER, colours: MODEL_TRACK_COLOURS}; return; let storedPreferences = {};');
const root = {querySelector: () => ({})};
const context = {document: {getElementById: id => id === 'mla-data-config' ? {textContent: '{}'} : root}};
vm.runInNewContext(source, context);
assert(context.qa.order.includes('gefs-extended'), 'extended stream must survive Latest selection and shared URL restoration');
assert(context.qa.order.includes('gefs'), 'six-hourly GEFS must remain available');
assert(context.qa.order.includes('aifs'), 'historical AIFS must retain its existing model identity');
assert.notEqual(context.qa.colours.gefs, context.qa.colours['gefs-extended']);
console.log('Dynamical forecast selector tests passed.');
