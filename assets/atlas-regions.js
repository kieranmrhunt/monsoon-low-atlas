(function (root, factory) {
	const api = factory();
	if (typeof module === 'object' && module.exports) module.exports = api;
	else root.LPSAtlasRegions = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
	'use strict';
	// Boxes are [west, south, east, north]; track points are [latitude, longitude].
	function validBox(box) {
		return Array.isArray(box) && box.length === 4 && box.every(Number.isFinite)
			&& box[0] >= -180 && box[2] <= 180 && box[1] >= -90 && box[3] <= 90
			&& box[0] < box[2] && box[1] < box[3];
	}
	function contains(box, point) {
		return point[1] >= box[0] && point[1] <= box[2] && point[0] >= box[1] && point[0] <= box[3];
	}
	function segmentIntersects(box, first, second) {
		let enter = 0, leave = 1;
		for (const [axis, minimum, maximum] of [[1, box[0], box[2]], [0, box[1], box[3]]]) {
			const delta = second[axis] - first[axis];
			if (delta === 0) {
				if (first[axis] < minimum || first[axis] > maximum) return false;
				continue;
			}
			const a = (minimum - first[axis]) / delta;
			const b = (maximum - first[axis]) / delta;
			enter = Math.max(enter, Math.min(a, b));
			leave = Math.min(leave, Math.max(a, b));
			if (enter > leave) return false;
		}
		return true;
	}
	function pathIntersects(box, points, breakBefore, offset = 0) {
		if (!validBox(box)) return false;
		for (let index = 0; index < points.length; index++) {
			const point = points[index];
			if (!point || !point.every(Number.isFinite)) continue;
			if (contains(box, point)) return true;
			if (index > 0 && points[index - 1] && !(breakBefore && breakBefore[offset + index])
				&& points[index - 1].every(Number.isFinite) && segmentIntersects(box, points[index - 1], point)) return true;
		}
		return false;
	}
	return {validBox, contains, segmentIntersects, pathIntersects};
});
