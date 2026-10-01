const assert = require('node:assert/strict');
const {validBox, contains, segmentIntersects, pathIntersects} = require('../assets/atlas-regions.js');
const box = [70, 10, 90, 30];
assert.equal(validBox(box), true);
for (const invalid of [null, [], [90,10,70,30], [70,30,90,30], [-181,0,0,10], [0,-91,10,0], [0,0,181,10], [0,0,10,91], [0,0,NaN,10]]) assert.equal(validBox(invalid), false);
assert.equal(contains(box, [10,70]), true);
assert.equal(contains(box, [20,95]), false);
for (const [a,b,expected] of [
 [[20,60],[20,100],true], [[0,80],[40,80],true], [[0,60],[40,100],true],
 [[0,65],[15,95],true], [[0,85],[15,100],false], [[10,60],[10,100],true],
 [[20,80],[20,80],true], [[0,80],[0,80],false], [[0,80],[0,100],false]
]) {
 assert.equal(segmentIntersects(box,a,b),expected,`${a} → ${b}`);
 assert.equal(segmentIntersects(box,b,a),expected,`${b} → ${a}`);
}
assert.equal(pathIntersects(box, [[20,60],[20,100]]), true);
assert.equal(pathIntersects(box, [[20,60],[20,100]], [0,1]), false);
assert.equal(pathIntersects(box, [[20,60],[20,100]], [0,0,0,1], 2), false);
assert.equal(pathIntersects(box, [[20,60],[20,80]], [0,1]), true);
assert.equal(pathIntersects(box, [[20,60],null,[20,100]]), false);
assert.equal(pathIntersects(box, [[20,60],[NaN,80],[20,100]]), false);
assert.equal(pathIntersects(box, []), false);
assert.equal(pathIntersects(null, [[20,80]]), false);
console.log('PASS box validation, point inclusion, segment clipping and track-gap exclusions');
