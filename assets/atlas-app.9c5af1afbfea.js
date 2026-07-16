(async function () {
	'use strict';

	const root = document.getElementById('monsoon-low-atlas');
	if (!root) return;

	const $ = (selector, scope) => (scope || root).querySelector(selector);
	const $$ = (selector, scope) => Array.from((scope || root).querySelectorAll(selector));
	const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
	const CLASS_SHORT = {1: 'L', 2: 'D', 3: 'DD', 4: 'CS', 5: 'SCS', 6: 'VSCS+'};
	const SYSTEM_CODES = {1: 'L', 2: 'D', 3: 'DD', 4: 'CS', 5: 'SCS', 6: 'VSCS'};
	const SEASON_MONTHS = {jjas: [6, 7, 8, 9], mam: [3, 4, 5], ond: [10, 11, 12], djf: [12, 1, 2], all: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]};
	const CLASS_COLOURS = ['#8b7b63', '#c3931d', '#c9631b', '#ad4328', '#8f2938', '#64224f', '#35204e'];
	const QC_LABELS = ['Strong support', 'Mixed support', 'Low support'];
	const QC_TONES = ['good', 'review', 'flag'];
	const COMPLETE_END_YEAR = 2025;
	const MAP_DIRTY = Object.freeze({BASE: 1, DATA: 2, OVERLAY: 4, ALL: 7});

	let CORE;
	let DETAIL;
	let detailPromise;
	let T;
	let S;
	let paths;
	let segmentIndex;
	let densityCells;
	let densityMonthCache = new Map();
	let catalogueBounds;
	let fallbackLabels = [];
	let nearStateCache = new Map();
	let profileCache = new Map();
	let toastTimer;
	let pointerFrame = 0;
	let pendingPointer = null;
	let suppressUrl = false;
	let pinnedA = null;
	let lastAutoFitSignature = '';
	let rainfallMapCache = null;

	const METRICS = {
		deficit: {label: 'pressure-deficit', title: 'Pressure deficit', pct: 'pct_deficit', raw: 'peak_deficit_x10', series: 'pressure_deficit_x10', divisor: 10, unit: 'hPa', colour: '#aa3d2d', direction: 1, peakMonth: 4},
		vort: {label: 'vorticity', title: 'Smoothed vorticity', pct: 'pct_vort', raw: 'peak_vort_x10', series: 'vort_smooth_x10', divisor: 10, unit: 'catalogue units', colour: '#233f78', direction: 1, peakMonth: 1},
		wind: {label: 'maximum-wind', title: 'Maximum wind', pct: 'pct_wind', raw: 'peak_wind_x10', series: 'max_wind_x10', divisor: 10, unit: 'm s⁻¹', colour: '#08736f', direction: 1, peakMonth: 2},
		mslp: {label: 'MSLP-depth', title: 'Minimum MSLP', pct: 'pct_mslp_depth', raw: 'min_mslp_x10', series: 'mslp_x10', divisor: 10, unit: 'hPa', colour: '#64224f', direction: -1, peakMonth: 3},
		rain: {label: 'rainfall', title: '24 h precipitation', pct: 'pct_precip', raw: 'peak_precip_x10', series: 'precip24_x10', divisor: 10, unit: 'mm', colour: '#c3931d', direction: 1, peakMonth: 0},
		q: {label: 'q850', title: 'q850', raw: 'peak_q850_x10', series: 'q850_x10', divisor: 10, unit: 'g kg⁻¹', colour: '#4360a0', direction: 1},
		rh: {label: 'RH850', title: 'RH850', series: 'rh850_x10', divisor: 10, unit: '%', colour: '#477a4a', direction: 1}
	};

	const state = {
		tab: 'explore',
		timeMode: 'years',
		yearMin: 1940,
		yearMax: 2025,
		dateMin: '1940-05-09',
		dateMax: '2025-12-31',
		months: new Set([6, 7, 8, 9]),
		monthMode: 'active',
		classes: new Set([1, 2, 3, 4, 5, 6]),
		metric: 'deficit',
		metricMin: 0,
		match: 'any',
		qc: 'any',
		stateIndex: -1,
		stateMin: 0,
		search: '',
		mapLayer: 'auto',
		mapColour: 'single',
		stateFill: 'none',
		mapScope: 'full',
		mapPath: 'months',
		mapZoom: 1,
		mapCenterLon: 82,
		mapCenterLat: 20,
		selected: null,
		hovered: null,
		active: [],
		activeBit: null,
		page: 1,
		pageSize: 50,
		sort: 'metric-desc',
		extremeMetric: 'duration',
		extremeEligibility: 'recommended',
		compareMetric: 'deficit',
		compareAlign: 'life',
		evolutionMetric: 'deficit'
	};

	function css(name, fallback) {
		const value = getComputedStyle(root).getPropertyValue(name).trim();
		return value || fallback;
	}

	function esc(value) {
		return String(value == null ? '' : value).replace(/[&<>"']/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]));
	}

	function fmt(value, digits) {
		if (!Number.isFinite(Number(value))) return 'n/a';
		return Number(value).toLocaleString('en-GB', {
			minimumFractionDigits: digits || 0,
			maximumFractionDigits: digits == null ? 0 : digits
		});
	}

	function date(ms) {
		return new Date(ms).toISOString().slice(0, 10);
	}

	function periodYearMin() {
		return state.timeMode === 'dates' ? Number(state.dateMin.slice(0, 4)) : state.yearMin;
	}

	function periodYearMax() {
		return state.timeMode === 'dates' ? Number(state.dateMax.slice(0, 4)) : state.yearMax;
	}

	function durationText(hours) {
		if (!Number.isFinite(hours)) return 'n/a';
		if (hours >= 72) return `${fmt(hours / 24, 1)} d`;
		return `${fmt(hours)} h`;
	}

	function clamp(value, minimum, maximum) {
		return Math.max(minimum, Math.min(maximum, value));
	}

	function debounce(fn, delay) {
		let timer;
		return function (...args) {
			clearTimeout(timer);
			timer = setTimeout(() => fn.apply(this, args), delay);
		};
	}

	function quantile(values, probability) {
		const sorted = values.filter(Number.isFinite).sort((a, b) => a - b);
		if (!sorted.length) return NaN;
		const position = (sorted.length - 1) * probability;
		const lower = Math.floor(position);
		const upper = Math.ceil(position);
		return lower === upper ? sorted[lower] : sorted[lower] * (upper - position) + sorted[upper] * (position - lower);
	}

	function median(values) {
		return quantile(values, .5);
	}

	function toast(message) {
		const node = $('#mlaToast');
		node.textContent = message;
		node.dataset.visible = 'true';
		clearTimeout(toastTimer);
		toastTimer = setTimeout(() => { node.dataset.visible = 'false'; }, 2600);
	}

	function setLoading(message) {
		$('#mlaLoadingText').textContent = message;
	}

	async function loadGzipJson(id) {
		const node = document.getElementById(id);
		let bytes;
		if (node && node.textContent.trim()) {
			const encoded = node.textContent.trim();
			const binary = atob(encoded);
			bytes = new Uint8Array(binary.length);
			for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
			node.textContent = '';
		} else {
			const configNode = document.getElementById('mla-data-config');
			if (!configNode) throw new Error(`Missing atlas payload ${id}`);
			const config = JSON.parse(configNode.textContent);
			const key = id === 'mla-core-gzip-b64' ? 'core' : id === 'mla-detail-gzip-b64' ? 'detail' : '';
			if (!key || !config[key]) throw new Error(`Missing atlas data URL for ${id}`);
			const response = await fetch(config[key], {cache: 'force-cache'});
			if (!response.ok) throw new Error(`Could not fetch ${key} data (${response.status})`);
			bytes = new Uint8Array(await response.arrayBuffer());
		}
		if (bytes[0] !== 0x1f || bytes[1] !== 0x8b) return JSON.parse(new TextDecoder().decode(bytes));
		if (!('DecompressionStream' in window)) throw new Error('This browser needs DecompressionStream support. Please use a current Chrome, Edge, Firefox or Safari release.');
		const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
		return new Response(stream).json();
	}

	async function ensureDetail(reason) {
		if (DETAIL) return DETAIL;
		if (!detailPromise) {
			detailPromise = (async () => {
				if (reason) toast(reason);
				const detail = await loadGzipJson('mla-detail-gzip-b64');
				DETAIL = detail;
				profileCache.clear();
				rainfallMapCache = null;
				if (state.stateFill !== 'none') mapScheduler.invalidate(MAP_DIRTY.ALL);
				return detail;
			})();
		}
		return detailPromise;
	}

	function decodePolyline(value) {
		let index = 0;
		let latitude = 0;
		let longitude = 0;
		const points = [];
		while (index < value.length) {
			let result = 0;
			let shift = 0;
			let item;
			do {
				item = value.charCodeAt(index++) - 63;
				result |= (item & 31) << shift;
				shift += 5;
			} while (item >= 32 && index <= value.length);
			latitude += result & 1 ? ~(result >> 1) : result >> 1;
			result = 0;
			shift = 0;
			do {
				item = value.charCodeAt(index++) - 63;
				result |= (item & 31) << shift;
				shift += 5;
			} while (item >= 32 && index <= value.length);
			longitude += result & 1 ? ~(result >> 1) : result >> 1;
			points.push([latitude / 10000, longitude / 10000]);
		}
		return points;
	}

	function buildPathRuntime() {
		const decoded = CORE.paths.map(decodePolyline);
		const total = decoded.reduce((sum, points) => sum + points.length, 0);
		const offsets = new Uint32Array(decoded.length + 1);
		const latitude = new Float32Array(total);
		const longitude = new Float32Array(total);
		const breakBefore = new Uint8Array(total);
		const month = new Uint8Array(total);
		let cursor = 0;
		decoded.forEach((points, track) => {
			offsets[track] = cursor;
			const trackOffset = cursor;
			const breaks = new Set((CORE.breaks[track] || []).map(item => Number(item[0])));
			points.forEach((point, pointIndex) => {
				latitude[cursor] = point[0];
				longitude[cursor] = point[1];
				if (breaks.has(pointIndex)) breakBefore[cursor] = 1;
				cursor++;
			});
			for (const run of (CORE.point_month_runs && CORE.point_month_runs[track]) || []) {
				month.fill(Number(run[2]), trackOffset + Number(run[0]), trackOffset + Number(run[1]) + 1);
			}
		});
		offsets[decoded.length] = cursor;
		paths = {decoded, offsets, latitude, longitude, breakBefore, month};
		catalogueBounds = {
			lonMin: Math.floor(Number(CORE.meta.lon_min)) - 2,
			lonMax: Math.ceil(Number(CORE.meta.lon_max)) + 2,
			latMin: Math.floor(Number(CORE.meta.lat_min)) - 2,
			latMax: Math.ceil(Number(CORE.meta.lat_max)) + 2
		};
		segmentIndex = new UniformSegmentIndex({
			lon: longitude,
			lat: latitude,
			offsets,
			breakBefore,
			cellSize: 1,
			bounds: {minLon: catalogueBounds.lonMin, maxLon: catalogueBounds.lonMax, minLat: catalogueBounds.latMin, maxLat: catalogueBounds.latMax}
		});
		densityCells = buildDensityCells(.5, catalogueBounds);
		densityMonthCache.clear();
	}

	function buildDensityCells(cellSize, bounds, months) {
		const minLon = bounds.lonMin;
		const minLat = bounds.latMin;
		const columns = Math.ceil((bounds.lonMax - minLon) / cellSize);
		const rows = Math.ceil((bounds.latMax - minLat) / cellSize);
		const perTrack = [];
		for (let track = 0; track < paths.decoded.length; track++) {
			const cells = new Set();
			const points = paths.decoded[track];
			const breaks = new Set((CORE.breaks[track] || []).map(item => Number(item[0])));
			for (let index = 0; index < points.length; index++) {
				const visible = !months || months.has(paths.month[paths.offsets[track] + index]);
				if (!visible) continue;
				const point = points[index];
				const col = clamp(Math.floor((point[1] - minLon) / cellSize), 0, columns - 1);
				const row = clamp(Math.floor((point[0] - minLat) / cellSize), 0, rows - 1);
				cells.add(row * columns + col);
				const previousVisible = index && (!months || months.has(paths.month[paths.offsets[track] + index - 1]));
				if (!previousVisible || breaks.has(index)) continue;
				const previous = points[index - 1];
				const steps = Math.ceil(Math.max(Math.abs(point[0] - previous[0]), Math.abs(point[1] - previous[1])) / cellSize);
				for (let step = 1; step < steps; step++) {
					const fraction = step / steps;
					const sampleLat = previous[0] + (point[0] - previous[0]) * fraction;
					const sampleLon = previous[1] + (point[1] - previous[1]) * fraction;
					const sampleCol = clamp(Math.floor((sampleLon - minLon) / cellSize), 0, columns - 1);
					const sampleRow = clamp(Math.floor((sampleLat - minLat) / cellSize), 0, rows - 1);
					cells.add(sampleRow * columns + sampleCol);
				}
			}
			perTrack.push(Uint16Array.from(cells));
		}
		return {cellSize, minLon, minLat, columns, rows, perTrack};
	}

	function currentDensityCells() {
		if (state.mapPath === 'full') return densityCells;
		const key = [...state.months].sort((a, b) => a - b).join(',');
		if (!densityMonthCache.has(key)) densityMonthCache.set(key, buildDensityCells(.5, catalogueBounds, state.months));
		return densityMonthCache.get(key);
	}

	function pointSegmentDistanceSquared(px, py, x1, y1, x2, y2) {
		let dx = x2 - x1;
		let dy = y2 - y1;
		const lengthSquared = dx * dx + dy * dy;
		if (!lengthSquared) return (px - x1) ** 2 + (py - y1) ** 2;
		const fraction = clamp(((px - x1) * dx + (py - y1) * dy) / lengthSquared, 0, 1);
		const x = x1 + fraction * dx;
		const y = y1 + fraction * dy;
		return (px - x) ** 2 + (py - y) ** 2;
	}

	class UniformSegmentIndex {
		constructor(options) {
			this.cellSize = options.cellSize || 1;
			this.minLon = options.bounds.minLon;
			this.maxLon = options.bounds.maxLon;
			this.minLat = options.bounds.minLat;
			this.maxLat = options.bounds.maxLat;
			this.columns = Math.ceil((this.maxLon - this.minLon) / this.cellSize) + 1;
			this.rows = Math.ceil((this.maxLat - this.minLat) / this.cellSize) + 1;
			this.cells = Array.from({length: this.columns * this.rows}, () => []);
			const x1 = [];
			const y1 = [];
			const x2 = [];
			const y2 = [];
			const owner = [];
			const pointIndex = [];
			for (let track = 0; track < options.offsets.length - 1; track++) {
				for (let point = options.offsets[track] + 1; point < options.offsets[track + 1]; point++) {
					if (options.breakBefore[point]) continue;
					const segment = owner.length;
					x1.push(options.lon[point - 1]);
					y1.push(options.lat[point - 1]);
					x2.push(options.lon[point]);
					y2.push(options.lat[point]);
					owner.push(track);
					pointIndex.push(point - options.offsets[track]);
					const a = this.cellCoordinates(Math.min(x1[segment], x2[segment]), Math.min(y1[segment], y2[segment]));
					const b = this.cellCoordinates(Math.max(x1[segment], x2[segment]), Math.max(y1[segment], y2[segment]));
					for (let row = a.row; row <= b.row; row++) {
						for (let col = a.col; col <= b.col; col++) this.cells[row * this.columns + col].push(segment);
					}
				}
			}
			this.x1 = Float32Array.from(x1);
			this.y1 = Float32Array.from(y1);
			this.x2 = Float32Array.from(x2);
			this.y2 = Float32Array.from(y2);
			this.owner = Uint32Array.from(owner);
			this.pointIndex = Uint32Array.from(pointIndex);
			this.seen = new Uint32Array(owner.length);
			this.stamp = 0;
		}

		cellCoordinates(lon, lat) {
			return {
				col: clamp(Math.floor((lon - this.minLon) / this.cellSize), 0, this.columns - 1),
				row: clamp(Math.floor((lat - this.minLat) / this.cellSize), 0, this.rows - 1)
			};
		}

		query(options) {
			const a = this.cellCoordinates(options.lon - options.radiusLon, options.lat - options.radiusLat);
			const b = this.cellCoordinates(options.lon + options.radiusLon, options.lat + options.radiusLat);
			let bestTrack = -1;
			let bestDistance = options.radiusPx ** 2;
			this.stamp = (this.stamp + 1) >>> 0;
			if (!this.stamp) { this.seen.fill(0); this.stamp = 1; }
			for (let row = a.row; row <= b.row; row++) {
				for (let col = a.col; col <= b.col; col++) {
					const bucket = this.cells[row * this.columns + col];
					for (const segment of bucket) {
						if (this.seen[segment] === this.stamp) continue;
						this.seen[segment] = this.stamp;
						const track = this.owner[segment];
						if (!options.active[track]) continue;
						if (options.segmentVisible && !options.segmentVisible(track, this.pointIndex[segment])) continue;
						const first = options.project(this.y1[segment], this.x1[segment]);
						const second = options.project(this.y2[segment], this.x2[segment]);
						const distance = pointSegmentDistanceSquared(options.x, options.y, first[0], first[1], second[0], second[1]);
						if (distance < bestDistance) { bestDistance = distance; bestTrack = track; }
					}
				}
			}
			return bestTrack;
		}
	}

	function track(index) {
		return CORE.tracks[index];
	}

	function atlasId(index) {
		return track(index)[T.id];
	}

	function metric() {
		return METRICS[state.metric];
	}

	function rawMetric(index, key) {
		const definition = METRICS[key || state.metric];
		return Number(track(index)[T[definition.raw]]) / definition.divisor;
	}

	function percentileMetric(index, key) {
		const definition = METRICS[key || state.metric];
		return Number(track(index)[T[definition.pct]]);
	}

	function crosswalk(index) {
		return CORE.crosswalk[index] || null;
	}

	function credibleIb(index) {
		const item = crosswalk(index);
		return item && item.ib && ['high', 'medium'].includes(item.ib.confidence) ? item.ib : null;
	}

	function officialName(index) {
		const item = crosswalk(index);
		if (!item) return '';
		if (item.imd && ['high', 'medium'].includes(item.imd.confidence) && item.imd.system.name) return item.imd.system.name;
		if (item.ib && ['high', 'medium'].includes(item.ib.confidence)) {
			const best = CORE.ibtracs_tracks[item.ib.sid];
			if (best && best.name) return best.name;
		}
		return '';
	}

	function buildFallbackLabels() {
		const counts = new Map();
		fallbackLabels = Array(CORE.tracks.length);
		const indexes = CORE.tracks.map((row, index) => index).sort((first, second) => {
			return track(first)[T.start_ms] - track(second)[T.start_ms] || atlasId(first) - atlasId(second);
		});
		for (const index of indexes) {
			const row = track(index);
			const year = row[T.start_year];
			const category = row[T.category];
			const key = `${year}-${category}`;
			const sequence = (counts.get(key) || 0) + 1;
			counts.set(key, sequence);
			fallbackLabels[index] = `${SYSTEM_CODES[category] || 'LPS'} ${year} ${String(sequence).padStart(2, '0')}`;
		}
	}

	function systemLabel(index) {
		const name = officialName(index);
		const item = credibleIb(index);
		if (name) return `Cyclone ${name}${item && item.segment_count > 1 ? ` · segment ${item.segment_index}/${item.segment_count}` : ''}`;
		return fallbackLabels[index] || `LPS ${atlasId(index)}`;
	}

	function buildSearchIndex() {
		CORE.search = CORE.tracks.map((row, index) => {
			const item = crosswalk(index);
			return [
				atlasId(index),
				`lps ${atlasId(index)}`,
				`track ${atlasId(index)}`,
				date(row[T.start_ms]),
				row[T.start_year],
				systemLabel(index),
				item && item.ib ? item.ib.sid : '',
				item && item.imd ? item.imd.id : ''
			].join(' ').toLowerCase();
		});
	}

	function monthPass(index) {
		const row = track(index);
		if (state.monthMode === 'genesis') return state.months.has(new Date(row[T.start_ms]).getUTCMonth() + 1);
		if (state.monthMode === 'peak') return state.months.has(CORE.peak_months[index][metric().peakMonth]);
		const mask = row[T.month_mask];
		for (const month of state.months) if (mask & (1 << (month - 1))) return true;
		return false;
	}

	function matchPass(index) {
		const item = crosswalk(index);
		const ib = item && item.ib;
		if (state.match === 'any') return true;
		if (state.match === 'unmatched') return !ib;
		if (!ib) return false;
		if (state.match === 'high') return ib.confidence === 'high';
		if (state.match === 'credible') return ['high', 'medium'].includes(ib.confidence);
		if (state.match === 'named') return ['high', 'medium'].includes(ib.confidence) && Boolean(officialName(index));
		return true;
	}

	function qcPass(index) {
		const severity = CORE.qc[index][4];
		if (state.qc === 'good') return severity === 0;
		if (state.qc === 'usable') return severity <= 1;
		if (state.qc === 'flagged') return severity === 2;
		return true;
	}

	function pointInRing(lon, lat, ring) {
		let inside = false;
		for (let current = 0, previous = ring.length - 1; current < ring.length; previous = current++) {
			const x1 = ring[current][0];
			const y1 = ring[current][1];
			const x2 = ring[previous][0];
			const y2 = ring[previous][1];
			if (((y1 > lat) !== (y2 > lat)) && lon < (x2 - x1) * (lat - y1) / ((y2 - y1) || 1e-9) + x1) inside = !inside;
		}
		return inside;
	}

	function pointInState(lon, lat, geometry) {
		let inside = false;
		for (const ring of geometry.rings || []) if (pointInRing(lon, lat, ring)) inside = !inside;
		return inside;
	}

	function trackPassesState(trackIndex, stateIndex) {
		const key = `${stateIndex}:passes`;
		if (!nearStateCache.has(key)) nearStateCache.set(key, new Int8Array(CORE.tracks.length).fill(-1));
		const cache = nearStateCache.get(key);
		if (cache[trackIndex] >= 0) return Boolean(cache[trackIndex]);
		const geometry = CORE.geo.states[stateIndex];
		if (!geometry) { cache[trackIndex] = 0; return false; }
		const bbox = geometry.bbox;
		const trackBounds = CORE.bounds[trackIndex];
		if (trackBounds[2] < bbox[0] || trackBounds[0] > bbox[2] || trackBounds[3] < bbox[1] || trackBounds[1] > bbox[3]) {
			cache[trackIndex] = 0;
			return false;
		}
		const points = paths.decoded[trackIndex];
		let passes = false;
		for (let pointIndex = 0; pointIndex < points.length; pointIndex++) {
			const point = points[pointIndex];
			if (point[1] < bbox[0] || point[1] > bbox[2] || point[0] < bbox[1] || point[0] > bbox[3]) continue;
			if (pointInState(point[1], point[0], geometry)) { passes = true; break; }
		}
		cache[trackIndex] = passes ? 1 : 0;
		return passes;
	}

	function statePass(index) {
		if (state.stateIndex < 0) return true;
		return trackPassesState(index, state.stateIndex);
	}

	function filterSignature() {
		return [state.timeMode, state.yearMin, state.yearMax, state.dateMin, state.dateMax, [...state.months].sort((a, b) => a - b).join('.'), state.monthMode, [...state.classes].sort().join('.'), state.metric, state.metricMin, state.match, state.qc, state.stateIndex, state.stateMin, state.search].join('|');
	}

	function applyFilters(options) {
		if (!CORE) return;
		const active = [];
		const bits = new Uint8Array(CORE.tracks.length);
		const query = state.search.trim().toLowerCase();
		const exactTrackId = /^\d+$/.test(query) ? Number(query) : null;
		const minimumGenesis = state.timeMode === 'dates' ? Date.parse(`${state.dateMin}T00:00:00Z`) : NaN;
		const maximumGenesis = state.timeMode === 'dates' ? Date.parse(`${state.dateMax}T23:59:59.999Z`) : NaN;
		for (let index = 0; index < CORE.tracks.length; index++) {
			const row = track(index);
			if (state.timeMode === 'dates') {
				if (row[T.start_ms] < minimumGenesis || row[T.start_ms] > maximumGenesis) continue;
			} else if (row[T.start_year] < state.yearMin || row[T.start_year] > state.yearMax) continue;
			if (!monthPass(index)) continue;
			if (!state.classes.has(row[T.category])) continue;
			if (percentileMetric(index) < state.metricMin) continue;
			if (!matchPass(index) || !qcPass(index) || !statePass(index)) continue;
			if (query && (exactTrackId == null ? !CORE.search[index].includes(query) : atlasId(index) !== exactTrackId)) continue;
			bits[index] = 1;
			active.push(index);
		}
		state.active = active;
		state.activeBit = bits;
		state.page = options && options.keepPage ? state.page : 1;
		if (state.selected != null && !bits[state.selected]) state.selected = null;
		rainfallMapCache = null;
		updateFilterReadout();
		mapScheduler.invalidate((state.stateFill === 'none' ? 0 : MAP_DIRTY.BASE) | MAP_DIRTY.DATA | MAP_DIRTY.OVERLAY);
		renderCurrentPanel();
		const autoFitKey = filterSignature();
		const narrowTime = state.timeMode === 'dates'
			? Date.parse(state.dateMax) - Date.parse(state.dateMin) <= 3 * 366 * 86400000
			: state.yearMax - state.yearMin <= 3;
		if (!(options && options.noAutoFit) && !state.selected && active.length > 0 && active.length <= 80 && narrowTime && lastAutoFitSignature !== autoFitKey) {
			lastAutoFitSignature = autoFitKey;
			requestAnimationFrame(() => fitCohort({quiet: true}));
		}
		if (!(options && options.noUrl)) writeUrl('replace');
	}

	function physicalThreshold() {
		if (!state.metricMin) return '';
		const values = CORE.tracks.map((row, index) => ({pct: percentileMetric(index), raw: rawMetric(index)})).filter(item => item.pct >= state.metricMin).map(item => item.raw);
		if (!values.length) return '';
		const definition = metric();
		const threshold = definition.direction < 0 ? Math.max(...values) : Math.min(...values);
		return `${definition.direction < 0 ? '≤' : '≥'} ${fmt(threshold, 1)} ${definition.unit}`;
	}

	function updateFilterReadout() {
		$('#mlaResultCount').textContent = `${fmt(state.active.length)} of ${fmt(CORE.tracks.length)} systems`;
		$('#mlaMetricLabel').textContent = metric().label;
		$('#mlaMetricMinValue').textContent = `${state.metricMin}%`;
		$('#mlaStateMinValue').textContent = `${state.stateMin}%`;
		const filters = [];
		if (state.timeMode === 'dates') filters.push(`Genesis ${state.dateMin} to ${state.dateMax}`);
		else if (state.yearMin !== 1940 || state.yearMax !== 2025) filters.push(`${state.yearMin}–${state.yearMax}`);
		if (state.months.size !== 12) filters.push(`${[...state.months].sort((a, b) => a - b).map(month => MONTHS[month - 1]).join(', ')} · ${state.monthMode}`);
		if (state.classes.size !== 6) filters.push(`${[...state.classes].sort().map(value => CLASS_SHORT[value]).join(', ')} class`);
		if (state.metricMin) filters.push(`P${state.metricMin} ${physicalThreshold()}`);
		if (state.qc !== 'any') filters.push(`Continuity: ${state.qc}`);
		if (state.stateIndex >= 0) filters.push(`Track crosses ${CORE.states[state.stateIndex]}`);
		if (state.search) filters.push(`Search: “${state.search}”`);
		$('#mlaActiveFilters').innerHTML = filters.length ? filters.map(value => `<span class="mla-active-filter">${esc(value)}</span>`).join('') : '<span class="mla-active-filter">Default JJAS cohort · complete through 2025</span>';
	}

	function buildFilterControls() {
		$('#mlaMonthChips').innerHTML = MONTHS.map((name, index) => `<button class="mla-chip" type="button" data-month="${index + 1}" aria-pressed="${state.months.has(index + 1)}">${name}</button>`).join('');
		$('#mlaClassChips').innerHTML = [1, 2, 3, 4, 5, 6].map(value => `<button class="mla-chip" type="button" data-class="${value}" aria-pressed="true">${esc(CLASS_SHORT[value])}</button>`).join('');
		const stateSelect = $('#mlaState');
		CORE.states.forEach((name, index) => {
			const option = document.createElement('option');
			option.value = String(index);
			option.textContent = name;
			stateSelect.appendChild(option);
		});
		const sort = $('#mlaSystemSort');
		sort.innerHTML = [
			['metric-desc', `${metric().title}: strongest percentile`],
			['date-desc', 'Genesis date: newest'],
			['date-asc', 'Genesis date: oldest'],
			['duration-desc', 'Duration: longest'],
			['distance-desc', 'Path length: longest']
		].map(([value, label]) => `<option value="${value}">${esc(label)}</option>`).join('');
		const matchField = $('#mlaMatch').closest('.mla-field');
		if (matchField) matchField.hidden = !CORE.crosswalk.some(Boolean);
	}

	function syncControls() {
		$('#mlaYearFields').hidden = state.timeMode !== 'years';
		$('#mlaDateFields').hidden = state.timeMode !== 'dates';
		$('#mlaTimeModeYears').setAttribute('aria-pressed', String(state.timeMode === 'years'));
		$('#mlaTimeModeDates').setAttribute('aria-pressed', String(state.timeMode === 'dates'));
		$('#mlaYearMin').value = state.yearMin;
		$('#mlaYearMax').value = state.yearMax;
		$('#mlaDateMin').value = state.dateMin;
		$('#mlaDateMax').value = state.dateMax;
		$('#mlaMonthMode').value = state.monthMode;
		$('#mlaMetric').value = state.metric;
		$('#mlaMetricMin').value = state.metricMin;
		$('#mlaMatch').value = state.match;
		$('#mlaQc').value = state.qc;
		$('#mlaState').value = state.stateIndex < 0 ? '' : String(state.stateIndex);
		$('#mlaStateMin').value = state.stateMin;
		$('#mlaSearch').value = state.search;
		$('#mlaMapLayer').value = state.mapLayer;
		$('#mlaMapColour').value = state.mapColour;
		$('#mlaStateFill').value = state.stateFill;
		$('#mlaMapScope').value = state.mapScope;
		$('#mlaMapPath').value = state.mapPath;
		$('#mlaPageSize').value = String(state.pageSize);
		$('#mlaSystemSort').value = state.sort;
		$('#mlaExtremeMetric').value = state.extremeMetric;
		$('#mlaExtremeEligibility').value = state.extremeEligibility;
		$('#mlaCompareMetric').value = state.compareMetric;
		$('#mlaCompareAlign').value = state.compareAlign;
		$('#mlaEvolutionMetric').value = state.evolutionMetric;
		$$('[data-month]').forEach(button => button.setAttribute('aria-pressed', String(state.months.has(Number(button.dataset.month)))));
		$$('[data-class]').forEach(button => button.setAttribute('aria-pressed', String(state.classes.has(Number(button.dataset.class)))));
		$$('[data-season]').forEach(button => {
			const preset = SEASON_MONTHS[button.dataset.season] || [];
			const selected = state.months.size === preset.length && preset.every(month => state.months.has(month));
			button.setAttribute('aria-pressed', String(selected));
		});
	}

	function setMonths(values) {
		state.months = new Set(values);
		syncControls();
		applyFilters();
	}

	function toggleMonth(month) {
		if (state.months.has(month) && state.months.size > 1) state.months.delete(month);
		else state.months.add(month);
		syncControls();
		applyFilters();
	}

	function toggleClass(category) {
		if (state.classes.has(category) && state.classes.size > 1) state.classes.delete(category);
		else state.classes.add(category);
		syncControls();
		applyFilters();
	}

	function resetFilters() {
		state.timeMode = 'years';
		state.yearMin = 1940;
		state.yearMax = 2025;
		state.dateMin = '1940-05-09';
		state.dateMax = '2025-12-31';
		state.months = new Set([6, 7, 8, 9]);
		state.monthMode = 'active';
		state.classes = new Set([1, 2, 3, 4, 5, 6]);
		state.metric = 'deficit';
		state.metricMin = 0;
		state.match = 'any';
		state.qc = 'any';
		state.stateIndex = -1;
		state.stateMin = 0;
		state.search = '';
		state.stateFill = 'none';
		state.selected = null;
		rainfallMapCache = null;
		profileCache.clear();
		syncControls();
		applyFilters();
	}

	const debouncedFilter = debounce(() => applyFilters(), 90);

	function setTimeMode(mode) {
		if (mode === state.timeMode) return;
		if (mode === 'dates') {
			state.dateMin = state.yearMin === 1940 ? '1940-05-09' : `${state.yearMin}-01-01`;
			state.dateMax = `${state.yearMax}-12-31`;
		} else {
			state.yearMin = Number(state.dateMin.slice(0, 4));
			state.yearMax = Number(state.dateMax.slice(0, 4));
		}
		state.timeMode = mode;
		syncControls();
		applyFilters();
	}

	function bindControls() {
		$('#mlaSearch').addEventListener('input', event => { state.search = event.target.value.trim(); debouncedFilter(); });
		$('#mlaTimeModeYears').addEventListener('click', () => setTimeMode('years'));
		$('#mlaTimeModeDates').addEventListener('click', () => setTimeMode('dates'));
		$('#mlaYearMin').addEventListener('change', event => {
			state.yearMin = clamp(Number(event.target.value) || 1940, 1940, state.yearMax);
			syncControls();
			applyFilters();
		});
		$('#mlaYearMax').addEventListener('change', event => {
			state.yearMax = clamp(Number(event.target.value) || 2025, state.yearMin, 2025);
			syncControls();
			applyFilters();
		});
		$('#mlaDateMin').addEventListener('change', event => {
			state.dateMin = event.target.value || '1940-05-09';
			if (state.dateMin > state.dateMax) state.dateMax = state.dateMin;
			syncControls();
			applyFilters();
		});
		$('#mlaDateMax').addEventListener('change', event => {
			state.dateMax = event.target.value || '2025-12-31';
			if (state.dateMax < state.dateMin) state.dateMin = state.dateMax;
			syncControls();
			applyFilters();
		});
		$('#mlaMonthMode').addEventListener('change', event => { state.monthMode = event.target.value; applyFilters(); });
		$('#mlaMetric').addEventListener('change', event => {
			state.metric = event.target.value;
			state.sort = 'metric-desc';
			$('#mlaSystemSort').options[0].textContent = `${metric().title}: strongest percentile`;
			profileCache.clear();
			syncControls();
			applyFilters();
		});
		$('#mlaMetricMin').addEventListener('input', event => {
			state.metricMin = Number(event.target.value);
			$('#mlaMetricMinValue').textContent = `${state.metricMin}%`;
			debouncedFilter();
		});
		$('#mlaMatch').addEventListener('change', event => { state.match = event.target.value; applyFilters(); });
		$('#mlaQc').addEventListener('change', event => { state.qc = event.target.value; applyFilters(); });
		$('#mlaState').addEventListener('change', event => {
			state.stateIndex = event.target.value === '' ? -1 : Number(event.target.value);
			nearStateCache.clear();
			applyFilters();
			mapScheduler.invalidate(MAP_DIRTY.BASE);
		});
		$('#mlaStateMin').addEventListener('input', event => {
			state.stateMin = Number(event.target.value);
			$('#mlaStateMinValue').textContent = `${state.stateMin}%`;
			if (state.stateIndex >= 0) debouncedFilter();
		});
		$('#mlaMonthChips').addEventListener('click', event => {
			const button = event.target.closest('[data-month]');
			if (button) toggleMonth(Number(button.dataset.month));
		});
		$('#mlaClassChips').addEventListener('click', event => {
			const button = event.target.closest('[data-class]');
			if (button) toggleClass(Number(button.dataset.class));
		});
		$('#mlaSeasonPresets').addEventListener('click', event => {
			const button = event.target.closest('[data-season]');
			if (!button) return;
			setMonths(SEASON_MONTHS[button.dataset.season]);
		});
		$('#mlaResetFilters').addEventListener('click', resetFilters);
		$('#mlaPinA').addEventListener('click', pinCurrentA);
		$('#mlaComparePin').addEventListener('click', pinCurrentA);
		$('#mlaCompareClear').addEventListener('click', () => { pinnedA = null; renderCompare(); });
		$('#mlaMapLayer').addEventListener('change', event => { state.mapLayer = event.target.value; mapScheduler.invalidate(MAP_DIRTY.DATA | MAP_DIRTY.OVERLAY); writeUrl('replace'); });
		$('#mlaMapColour').addEventListener('change', event => { state.mapColour = event.target.value; mapScheduler.invalidate(MAP_DIRTY.DATA); writeUrl('replace'); });
		$('#mlaStateFill').addEventListener('change', async event => {
			state.stateFill = event.target.value;
			rainfallMapCache = null;
			if (state.stateFill !== 'none') await ensureDetail('Opening IMD state rainfall context...');
			mapScheduler.invalidate(MAP_DIRTY.ALL);
			writeUrl('replace');
		});
		$('#mlaMapScope').addEventListener('change', event => { state.mapScope = event.target.value; resetMapView(); writeUrl('replace'); });
		$('#mlaMapPath').addEventListener('change', event => {
			state.mapPath = event.target.value;
			renderDossier();
			if (state.active.length && state.active.length <= 80) fitCohort({quiet: true});
			else mapScheduler.invalidate(MAP_DIRTY.DATA | MAP_DIRTY.OVERLAY);
			writeUrl('replace');
		});
		$('#mlaFitCohort').addEventListener('click', () => fitCohort());
		$('#mlaSystemSort').addEventListener('change', event => { state.sort = event.target.value; state.page = 1; renderSystems(); writeUrl('replace'); });
		$('#mlaPageSize').addEventListener('change', event => { state.pageSize = Number(event.target.value); state.page = 1; renderSystems(); });
		$('#mlaPrevPage').addEventListener('click', () => { state.page = Math.max(1, state.page - 1); renderSystems(); });
		$('#mlaNextPage').addEventListener('click', () => { state.page++; renderSystems(); });
		$('#mlaExtremeMetric').addEventListener('change', event => { state.extremeMetric = event.target.value; renderExtremes(); });
		$('#mlaExtremeEligibility').addEventListener('change', event => { state.extremeEligibility = event.target.value; renderExtremes(); });
		$('#mlaCompareMetric').addEventListener('change', event => { state.compareMetric = event.target.value; renderCompare(); });
		$('#mlaCompareAlign').addEventListener('change', event => { state.compareAlign = event.target.value; renderCompare(); });
		$('#mlaEvolutionMetric').addEventListener('change', event => { state.evolutionMetric = event.target.value; renderLifeCharts(); writeUrl('replace'); });
		$('#mlaLoadProfile').addEventListener('click', () => ensureDetail('Opening detailed cohort series…').then(renderLifeCharts).catch(showFatal));
		$('#mlaCopyLink').addEventListener('click', copyViewLink);
		$('#mlaQuickExport').addEventListener('click', downloadSummaries);
		$('#mlaSystemsCsv').addEventListener('click', downloadSummaries);
		$('#mlaSystemsGeojson').addEventListener('click', downloadGeojson);
		$('#mlaDownloadSummaries').addEventListener('click', downloadSummaries);
		$('#mlaDownloadGeojson').addEventListener('click', downloadGeojson);
		$('#mlaDownloadQuery').addEventListener('click', downloadQuery);
		$('#mlaDownloadFixes').addEventListener('click', downloadSelectedFixes);
		root.addEventListener('click', event => {
			const opener = event.target.closest('[data-open-tab]');
			if (opener) activateTab(opener.dataset.openTab, true);
			const selector = event.target.closest('[data-select-track]');
			if (selector) selectTrack(Number(selector.dataset.selectTrack), {openExplore: selector.dataset.openExplore === 'true', fit: true});
		});
	}

	function activateTab(name, push) {
		state.tab = name;
		$$('[role="tab"]').forEach(button => {
			const selected = button.dataset.tab === name;
			button.setAttribute('aria-selected', String(selected));
			button.tabIndex = selected ? 0 : -1;
		});
		$$('[data-panel]').forEach(panel => { panel.hidden = panel.dataset.panel !== name; });
		$('#mlaFilterDock').hidden = name === 'data';
		renderCurrentPanel();
		if (push) writeUrl('push');
	}

	function bindTabs() {
		const tabs = $$('[role="tab"]');
		tabs.forEach((button, index) => {
			button.addEventListener('click', () => activateTab(button.dataset.tab, true));
			button.addEventListener('keydown', event => {
				if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
				event.preventDefault();
				let target = index;
				if (event.key === 'ArrowLeft') target = (index - 1 + tabs.length) % tabs.length;
				if (event.key === 'ArrowRight') target = (index + 1) % tabs.length;
				if (event.key === 'Home') target = 0;
				if (event.key === 'End') target = tabs.length - 1;
				tabs[target].focus();
				activateTab(tabs[target].dataset.tab, true);
			});
		});
	}

	function currentUrlParameters() {
		const parameters = new URLSearchParams();
		if (state.tab !== 'explore') parameters.set('tab', state.tab);
		if (state.timeMode === 'dates') parameters.set('dates', `${state.dateMin},${state.dateMax}`);
		else if (state.yearMin !== 1940 || state.yearMax !== 2025) parameters.set('years', `${state.yearMin}-${state.yearMax}`);
		const months = [...state.months].sort((a, b) => a - b);
		if (months.join(',') !== '6,7,8,9') parameters.set('months', months.join(','));
		if (state.monthMode !== 'active') parameters.set('month', state.monthMode);
		const classes = [...state.classes].sort((a, b) => a - b);
		if (classes.join(',') !== '1,2,3,4,5,6') parameters.set('class', classes.join(','));
		if (state.metric !== 'deficit') parameters.set('metric', state.metric);
		if (state.metricMin) parameters.set('pmin', String(state.metricMin));
		if (state.match !== 'any') parameters.set('match', state.match);
		if (state.qc !== 'any') parameters.set('qc', state.qc);
		if (state.stateIndex >= 0) parameters.set('over', CORE.state_slugs[state.stateIndex]);
		if (state.search) parameters.set('q', state.search);
		if (state.mapLayer !== 'auto') parameters.set('layer', state.mapLayer);
		if (state.mapColour !== 'single') parameters.set('colour', state.mapColour);
		if (state.stateFill !== 'none') parameters.set('statefill', state.stateFill);
		if (state.mapScope !== 'full') parameters.set('scope', state.mapScope);
		if (state.mapPath !== 'months') parameters.set('path', state.mapPath);
		if (state.evolutionMetric !== 'deficit') parameters.set('evolve', state.evolutionMetric);
		if (Math.abs(state.mapZoom - 1) > .01) parameters.set('zoom', state.mapZoom.toFixed(2));
		if (Math.abs(state.mapZoom - 1) > .01 || state.mapScope !== 'full') parameters.set('centre', `${state.mapCenterLon.toFixed(2)},${state.mapCenterLat.toFixed(2)}`);
		if (state.selected != null) parameters.set('system', String(atlasId(state.selected)));
		return parameters;
	}

	function writeUrl(mode) {
		if (suppressUrl || !CORE) return;
		const url = new URL(window.location.href);
		url.search = currentUrlParameters().toString();
		history[mode === 'push' ? 'pushState' : 'replaceState'](null, '', url);
	}

	function readUrl() {
		const parameters = new URLSearchParams(window.location.search);
		const validTabs = new Set(['explore', 'systems', 'climatology', 'compare', 'extremes', 'data']);
		if (validTabs.has(parameters.get('tab'))) state.tab = parameters.get('tab');
		const years = parameters.get('years');
		if (years && /^\d{4}-\d{4}$/.test(years)) {
			const [first, last] = years.split('-').map(Number);
			state.yearMin = clamp(first, 1940, 2025);
			state.yearMax = clamp(last, state.yearMin, 2025);
		}
		const dates = (parameters.get('dates') || '').split(',');
		if (dates.length === 2 && dates.every(value => /^\d{4}-\d{2}-\d{2}$/.test(value)) && dates[0] <= dates[1]) {
			state.timeMode = 'dates';
			state.dateMin = dates[0];
			state.dateMax = dates[1];
		}
		const months = (parameters.get('months') || '').split(',').map(Number).filter(value => value >= 1 && value <= 12);
		if (months.length) state.months = new Set(months);
		if (['active', 'genesis', 'peak'].includes(parameters.get('month'))) state.monthMode = parameters.get('month');
		const classes = (parameters.get('class') || '').split(',').map(Number).filter(value => value >= 1 && value <= 6);
		if (classes.length) state.classes = new Set(classes);
		if (METRICS[parameters.get('metric')] && !['q', 'rh'].includes(parameters.get('metric'))) state.metric = parameters.get('metric');
		state.metricMin = clamp(Number(parameters.get('pmin')) || 0, 0, 100);
		if (['any', 'unmatched', 'high', 'credible', 'named'].includes(parameters.get('match'))) state.match = parameters.get('match');
		if (['any', 'good', 'usable', 'flagged'].includes(parameters.get('qc'))) state.qc = parameters.get('qc');
		const overIndex = CORE.state_slugs.indexOf(parameters.get('over'));
		if (overIndex >= 0) state.stateIndex = overIndex;
		state.search = parameters.get('q') || '';
		if (['auto', 'density', 'tracks', 'genesis', 'lysis'].includes(parameters.get('layer'))) state.mapLayer = parameters.get('layer');
		if (['single', 'class', 'metric', 'year', 'qc'].includes(parameters.get('colour'))) state.mapColour = parameters.get('colour');
		if (['none', 'selected', 'cohort'].includes(parameters.get('statefill'))) state.stateFill = parameters.get('statefill');
		if (['southasia', 'full'].includes(parameters.get('scope'))) state.mapScope = parameters.get('scope');
		if (['months', 'full'].includes(parameters.get('path'))) state.mapPath = parameters.get('path');
		if (METRICS[parameters.get('evolve')] && parameters.get('evolve') !== 'rain') state.evolutionMetric = parameters.get('evolve');
		state.mapZoom = clamp(Number(parameters.get('zoom')) || 1, 1, 16);
		const centre = (parameters.get('centre') || '').split(',').map(Number);
		if (centre.length === 2 && centre.every(Number.isFinite)) {
			const bounds = catalogueBounds || {lonMin: 45, lonMax: 125, latMin: -8, latMax: 50};
			state.mapCenterLon = clamp(centre[0], bounds.lonMin, bounds.lonMax);
			state.mapCenterLat = clamp(centre[1], bounds.latMin, bounds.latMax);
		} else if (catalogueBounds) {
			state.mapCenterLon = (catalogueBounds.lonMin + catalogueBounds.lonMax) / 2;
			state.mapCenterLat = (catalogueBounds.latMin + catalogueBounds.latMax) / 2;
		}
		const selected = Number(parameters.get('system'));
		if (Number.isInteger(selected)) {
			const selectedIndex = CORE.tracks.findIndex(row => Number(row[T.id]) === selected);
			if (selectedIndex >= 0) state.selected = selectedIndex;
		}
	}

	async function copyViewLink() {
		writeUrl('replace');
		try {
			await navigator.clipboard.writeText(window.location.href);
			toast('View link copied');
		} catch (error) {
			toast('The view URL is ready in the address bar');
		}
	}

	window.addEventListener('popstate', async () => {
		suppressUrl = true;
		readUrl();
		if (state.stateFill !== 'none') await ensureDetail();
		syncControls();
		applyFilters({noUrl: true});
		activateTab(state.tab, false);
		suppressUrl = false;
	});

	function createFrameScheduler(render) {
		let dirty = 0;
		let frame = 0;
		function run() {
			frame = 0;
			const mask = dirty;
			dirty = 0;
			render(mask);
			if (dirty && !frame) frame = requestAnimationFrame(run);
		}
		return {
			invalidate(mask) {
				dirty |= mask;
				if (!frame) frame = requestAnimationFrame(run);
			}
		};
	}

	const mapScheduler = createFrameScheduler(mask => {
		if (!CORE || $('#mlaPanelExplore').hidden) return;
		if (mask & MAP_DIRTY.BASE) drawMapBase();
		if (mask & MAP_DIRTY.DATA) drawMapData();
		if (mask & MAP_DIRTY.OVERLAY) drawMapOverlay();
	});

	function mapBounds() {
		if (state.mapScope === 'full') return catalogueBounds || {lonMin: 47, lonMax: 118, latMin: -6, latMax: 48};
		return {lonMin: 58, lonMax: 105, latMin: 0, latMax: 38};
	}

	function constrainMapView(width, height) {
		const bounds = mapBounds();
		const padding = 24;
		const scale = Math.min(
			(width - padding * 2) / (bounds.lonMax - bounds.lonMin),
			(height - padding * 2) / (bounds.latMax - bounds.latMin)
		) * state.mapZoom;
		const halfLongitude = width / (2 * scale);
		const halfLatitude = height / (2 * scale);
		const middleLongitude = (bounds.lonMin + bounds.lonMax) / 2;
		const middleLatitude = (bounds.latMin + bounds.latMax) / 2;
		state.mapCenterLon = halfLongitude * 2 >= bounds.lonMax - bounds.lonMin
			? middleLongitude
			: clamp(state.mapCenterLon, bounds.lonMin + halfLongitude, bounds.lonMax - halfLongitude);
		state.mapCenterLat = halfLatitude * 2 >= bounds.latMax - bounds.latMin
			? middleLatitude
			: clamp(state.mapCenterLat, bounds.latMin + halfLatitude, bounds.latMax - halfLatitude);
	}

	function mapProjection(width, height) {
		const bounds = mapBounds();
		const padding = 24;
		const baseScale = Math.min(
			(width - padding * 2) / (bounds.lonMax - bounds.lonMin),
			(height - padding * 2) / (bounds.latMax - bounds.latMin)
		);
		const scale = baseScale * state.mapZoom;
		return {
			scale,
			project(latitude, longitude) {
				return [width / 2 + (longitude - state.mapCenterLon) * scale, height / 2 - (latitude - state.mapCenterLat) * scale];
			},
			invert(x, y) {
				return [state.mapCenterLat - (y - height / 2) / scale, state.mapCenterLon + (x - width / 2) / scale];
			},
			viewBounds: {
				lonMin: state.mapCenterLon - width / (2 * scale),
				lonMax: state.mapCenterLon + width / (2 * scale),
				latMin: state.mapCenterLat - height / (2 * scale),
				latMax: state.mapCenterLat + height / (2 * scale)
			}
		};
	}

	function setupCanvas(id) {
		const canvas = document.getElementById(id);
		const rectangle = canvas.getBoundingClientRect();
		const coarse = matchMedia('(pointer: coarse)').matches;
		const ratio = coarse ? Math.min(1.5, devicePixelRatio || 1) : Math.min(2, devicePixelRatio || 1);
		const width = Math.max(1, Math.round(rectangle.width));
		const height = Math.max(1, Math.round(rectangle.height));
		if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
			canvas.width = Math.round(width * ratio);
			canvas.height = Math.round(height * ratio);
		}
		const context = canvas.getContext('2d');
		context.setTransform(ratio, 0, 0, ratio, 0, 0);
		context.clearRect(0, 0, width, height);
		return {canvas, context, width, height, ratio, projection: mapProjection(width, height)};
	}

	function drawRingPath(context, projection, rings) {
		for (const ring of rings || []) {
			if (!ring.length) continue;
			const first = projection.project(ring[0][1], ring[0][0]);
			context.moveTo(first[0], first[1]);
			for (let index = 1; index < ring.length; index++) {
				const point = projection.project(ring[index][1], ring[index][0]);
				context.lineTo(point[0], point[1]);
			}
			context.closePath();
		}
	}

	function niceRainfallMaximum(value) {
		if (!Number.isFinite(value) || value <= 0) return 1;
		const magnitude = 10 ** Math.floor(Math.log10(value));
		const scaled = value / magnitude;
		const step = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
		return step * magnitude;
	}

	function rainfallColour(fraction) {
		const stops = ['#f6f0df', '#c8dfcf', '#7eb6b6', '#33849b', '#1d557d', '#26365f'];
		const value = clamp(fraction, 0, 1) * (stops.length - 1);
		const lower = Math.floor(value);
		const upper = Math.min(stops.length - 1, Math.ceil(value));
		const mix = value - lower;
		const parse = colour => [1, 3, 5].map(index => parseInt(colour.slice(index, index + 2), 16));
		const first = parse(stops[lower]);
		const second = parse(stops[upper]);
		return `rgb(${first.map((channel, index) => Math.round(channel + (second[index] - channel) * mix)).join(',')})`;
	}

	function stateRainfallSummary() {
		if (state.stateFill === 'none' || !DETAIL || !DETAIL.state_mean_x10) return null;
		const indexes = state.stateFill === 'selected'
			? (state.selected == null ? [] : [state.selected])
			: state.active;
		if (!indexes.length) return null;
		const key = state.stateFill === 'selected' ? `selected:${state.selected}` : `cohort:${filterSignature()}`;
		if (rainfallMapCache && rainfallMapCache.key === key) return rainfallMapCache;
		const totals = new Float64Array(CORE.states.length);
		const weights = new Float64Array(CORE.states.length);
		let systemDays = 0;
		for (const index of indexes) {
			const days = Math.max(1, Number(track(index)[T.rain_days]) || 1);
			systemDays += days;
			const values = DETAIL.state_mean_x10[index] || [];
			for (let stateIndex = 0; stateIndex < CORE.states.length; stateIndex++) {
				const value = Number(values[stateIndex]);
				if (value < 0 || !Number.isFinite(value)) continue;
				totals[stateIndex] += value * days;
				weights[stateIndex] += days;
			}
		}
		const values = Array.from(totals, (total, index) => weights[index] ? total / weights[index] / 10 : NaN);
		const maximum = niceRainfallMaximum(Math.max(...values.filter(Number.isFinite)));
		rainfallMapCache = {key, values, maximum, tracks: indexes.length, systemDays, mode: state.stateFill};
		return rainfallMapCache;
	}

	function renderStateRainfallValues(summary) {
		const panel = $('#mlaStateRainfallPanel');
		if (!panel) return;
		panel.hidden = !summary;
		if (!summary) {
			$('#mlaStateRainfallData').innerHTML = '';
			return;
		}
		const rows = summary.values
			.map((value, index) => ({name: CORE.states[index], value}))
			.filter(item => Number.isFinite(item.value))
			.sort((first, second) => second.value - first.value)
			.map(item => [item.name, fmt(item.value, 1)]);
		const selection = summary.mode === 'selected' ? systemLabel(state.selected) : `${fmt(summary.tracks)} filtered systems`;
		$('#mlaStateRainfallData').innerHTML = `<p>${esc(selection)} · ${fmt(summary.systemDays)} system-days · IMD area-mean daily rainfall.</p>${accessibleTable(['State / UT', 'Mean rainfall (mm day⁻¹)'], rows)}`;
	}

	function drawMapGeography(context, projection, width, height, options) {
		context.fillStyle = css('--mla-sea', '#e7eee7');
		context.fillRect(0, 0, width, height);
		context.save();
		context.strokeStyle = 'rgba(67, 76, 64, .18)';
		context.fillStyle = 'rgba(67, 76, 64, .66)';
		context.lineWidth = 1;
		context.font = '11px ui-monospace, Consolas, monospace';
		const view = projection.viewBounds;
		const lonStart = Math.ceil(view.lonMin / 10) * 10;
		const latStart = Math.ceil(view.latMin / 5) * 5;
		for (let longitude = lonStart; longitude <= view.lonMax; longitude += 10) {
			const first = projection.project(view.latMin, longitude);
			const second = projection.project(view.latMax, longitude);
			context.beginPath(); context.moveTo(first[0], first[1]); context.lineTo(second[0], second[1]); context.stroke();
			if (second[0] > 0 && second[0] < width - 24) context.fillText(`${longitude}°E`, second[0] + 3, 14);
		}
		for (let latitude = latStart; latitude <= view.latMax; latitude += 5) {
			const first = projection.project(latitude, view.lonMin);
			const second = projection.project(latitude, view.lonMax);
			context.beginPath(); context.moveTo(first[0], first[1]); context.lineTo(second[0], second[1]); context.stroke();
			if (first[1] > 16 && first[1] < height - 8) context.fillText(`${latitude}°N`, 4, first[1] - 3);
		}
		context.beginPath();
		drawRingPath(context, projection, CORE.geo.land);
		context.fillStyle = css('--mla-land', '#f3e6c8');
		context.fill('evenodd');
		context.strokeStyle = 'rgba(66, 54, 40, .30)';
		context.lineWidth = .8;
		context.stroke();
		for (const border of CORE.geo.borders || []) {
			if (!border.p || border.p.length < 2) continue;
			context.beginPath();
			border.p.forEach((point, index) => {
				const projected = projection.project(point[1], point[0]);
				if (!index) context.moveTo(projected[0], projected[1]); else context.lineTo(projected[0], projected[1]);
			});
			context.setLineDash(border.c === 1 ? [4, 3] : []);
			context.strokeStyle = 'rgba(66, 54, 40, .32)';
			context.lineWidth = .65;
			context.stroke();
		}
		context.setLineDash([]);
		const rainfall = stateRainfallSummary();
		CORE.geo.states.forEach((geometry, index) => {
			context.beginPath();
			drawRingPath(context, projection, geometry.rings);
			if (rainfall && Number.isFinite(rainfall.values[index])) {
				context.fillStyle = rainfallColour(rainfall.values[index] / rainfall.maximum);
				context.fill('evenodd');
			}
			if (index === state.stateIndex) {
				context.fillStyle = 'rgba(195, 147, 29, .24)';
				context.fill('evenodd');
				context.strokeStyle = css('--mla-madder', '#aa3d2d');
				context.lineWidth = 1.8;
			} else {
				context.strokeStyle = 'rgba(35, 63, 120, .30)';
				context.lineWidth = .55;
			}
			context.stroke();
		});
		if (options && options.labels && state.mapZoom >= 1.6) {
			context.font = '11px Aptos, Segoe UI, sans-serif';
			context.fillStyle = 'rgba(23, 41, 79, .72)';
			for (const geometry of CORE.geo.states) {
				if (!geometry.anchor) continue;
				const point = projection.project(geometry.anchor[1], geometry.anchor[0]);
				if (point[0] > 12 && point[0] < width - 12 && point[1] > 12 && point[1] < height - 12) context.fillText(geometry.name.replace(' & ', '/'), point[0] + 2, point[1] - 2);
			}
		}
		context.restore();
	}

	function drawMapBase() {
		const drawing = setupCanvas('mlaMapBase');
		drawMapGeography(drawing.context, drawing.projection, drawing.width, drawing.height, {labels: true});
	}

	function boundsIntersect(first, second) {
		return !(first[2] < second.lonMin || first[0] > second.lonMax || first[3] < second.latMin || first[1] > second.latMax);
	}

	function ramp(fraction) {
		const stops = ['#d9e4d5', '#83b7a6', '#e0b43c', '#c9631b', '#aa3d2d', '#64224f'];
		const value = clamp(fraction, 0, 1) * (stops.length - 1);
		const lower = Math.floor(value);
		const upper = Math.min(stops.length - 1, Math.ceil(value));
		const mix = value - lower;
		const parse = colour => [1, 3, 5].map(index => parseInt(colour.slice(index, index + 2), 16));
		const a = parse(stops[lower]);
		const b = parse(stops[upper]);
		return `rgb(${a.map((channel, index) => Math.round(channel + (b[index] - channel) * mix)).join(',')})`;
	}

	function rgba(colour, alpha) {
		const rgb = String(colour).match(/^rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
		if (rgb) return `rgba(${rgb[1]},${rgb[2]},${rgb[3]},${alpha})`;
		const clean = String(colour).replace('#', '');
		const value = clean.length === 3 ? clean.split('').map(character => character + character).join('') : clean;
		return `rgba(${parseInt(value.slice(0, 2), 16)},${parseInt(value.slice(2, 4), 16)},${parseInt(value.slice(4, 6), 16)},${alpha})`;
	}

	function trackColour(index) {
		if (state.mapColour === 'single') return css('--mla-atlas-blue', '#3978a8');
		if (state.mapColour === 'class') return CLASS_COLOURS[track(index)[T.category]];
		if (state.mapColour === 'metric') return ramp(percentileMetric(index) / 100);
		if (state.mapColour === 'year') return ramp((track(index)[T.start_year] - 1940) / (2025 - 1940));
		if (state.mapColour === 'qc') return [css('--mla-good', '#2f7152'), css('--mla-review', '#a06912'), css('--mla-flag', '#a23d34')][CORE.qc[index][4]];
		const item = crosswalk(index);
		if (!item || !item.ib) return '#8b7b63';
		return item.ib.confidence === 'high' ? '#08736f' : item.ib.confidence === 'medium' ? '#c3931d' : '#aa3d2d';
	}

	function pointVisible(trackIndex, pointIndex) {
		return state.mapPath === 'full' || state.months.has(paths.month[paths.offsets[trackIndex] + pointIndex]);
	}

	function visiblePointCount(indexes) {
		if (state.mapPath === 'full') return indexes.reduce((sum, index) => sum + paths.decoded[index].length, 0);
		let total = 0;
		for (const index of indexes) {
			for (const run of CORE.point_month_runs[index] || []) {
				if (state.months.has(Number(run[2]))) total += Number(run[1]) - Number(run[0]) + 1;
			}
		}
		return total;
	}

	function visibleTrackBounds(indexes) {
		let lonMin = Infinity;
		let lonMax = -Infinity;
		let latMin = Infinity;
		let latMax = -Infinity;
		for (const trackIndex of indexes) {
			const points = paths.decoded[trackIndex];
			for (let pointIndex = 0; pointIndex < points.length; pointIndex++) {
				if (!pointVisible(trackIndex, pointIndex)) continue;
				latMin = Math.min(latMin, points[pointIndex][0]);
				latMax = Math.max(latMax, points[pointIndex][0]);
				lonMin = Math.min(lonMin, points[pointIndex][1]);
				lonMax = Math.max(lonMax, points[pointIndex][1]);
			}
		}
		return Number.isFinite(lonMin) ? [lonMin, latMin, lonMax, latMax] : null;
	}

	function effectiveLayer() {
		if (state.mapLayer !== 'auto') return state.mapLayer;
		return state.active.length > 650 && state.mapZoom < 2.5 ? 'density' : 'tracks';
	}

	function drawDensity(context, projection) {
		const cellsData = currentDensityCells();
		const counts = new Uint16Array(cellsData.columns * cellsData.rows);
		let maximum = 0;
		for (const trackIndex of state.active) {
			for (const cell of cellsData.perTrack[trackIndex]) {
				counts[cell]++;
				if (counts[cell] > maximum) maximum = counts[cell];
			}
		}
		for (let cell = 0; cell < counts.length; cell++) {
			if (!counts[cell]) continue;
			const row = Math.floor(cell / cellsData.columns);
			const col = cell % cellsData.columns;
			const lon = cellsData.minLon + col * cellsData.cellSize;
			const lat = cellsData.minLat + row * cellsData.cellSize;
			if (lon > projection.viewBounds.lonMax || lon + cellsData.cellSize < projection.viewBounds.lonMin || lat > projection.viewBounds.latMax || lat + cellsData.cellSize < projection.viewBounds.latMin) continue;
			const topLeft = projection.project(lat + cellsData.cellSize, lon);
			const bottomRight = projection.project(lat, lon + cellsData.cellSize);
			const fraction = Math.sqrt(counts[cell] / Math.max(1, maximum));
			context.fillStyle = rgba(ramp(fraction), .82);
			context.fillRect(topLeft[0], topLeft[1], Math.max(1, bottomRight[0] - topLeft[0] + .6), Math.max(1, bottomRight[1] - topLeft[1] + .6));
		}
		return maximum;
	}

	function appendTrackPath(context, projection, trackIndex, step, includeBreaks) {
		const points = paths.decoded[trackIndex];
		const breaks = new Set((CORE.breaks[trackIndex] || []).map(item => Number(item[0])));
		let started = false;
		let breakSince = false;
		for (let index = 0; index < points.length; index++) {
			if (breaks.has(index)) breakSince = true;
			if (!pointVisible(trackIndex, index)) { started = false; breakSince = false; continue; }
			if (index !== points.length - 1 && index % step) continue;
			const point = projection.project(points[index][0], points[index][1]);
			if (!started || (breakSince && !includeBreaks)) context.moveTo(point[0], point[1]);
			else context.lineTo(point[0], point[1]);
			started = true;
			breakSince = false;
		}
	}

	function appendObservedTrackPath(context, projection, trackIndex) {
		const points = paths.decoded[trackIndex];
		const breaks = new Set((CORE.breaks[trackIndex] || []).map(item => Number(item[0])));
		const posterior = new Uint8Array(points.length);
		for (const range of CORE.posterior_runs[trackIndex] || []) {
			for (let index = Number(range[0]); index <= Number(range[1]); index++) posterior[index] = 1;
		}
		let started = false;
		for (let index = 0; index < points.length; index++) {
			if (posterior[index] || !pointVisible(trackIndex, index)) { started = false; continue; }
			const point = projection.project(points[index][0], points[index][1]);
			if (!started || breaks.has(index)) context.moveTo(point[0], point[1]);
			else context.lineTo(point[0], point[1]);
			started = true;
		}
	}

	function drawTrackLayer(context, projection) {
		const groups = new Map();
		for (const index of state.active) {
			if (!boundsIntersect(CORE.bounds[index], projection.viewBounds)) continue;
			const colour = trackColour(index);
			if (!groups.has(colour)) groups.set(colour, []);
			groups.get(colour).push(index);
		}
		for (const [colour, indexes] of groups) {
			context.beginPath();
			for (const index of indexes) appendTrackPath(context, projection, index, state.mapZoom < 1.5 ? 3 : state.mapZoom < 3 ? 2 : 1, false);
			context.strokeStyle = rgba(colour, state.active.length > 1000 ? .34 : .58);
			context.lineWidth = state.mapZoom > 3 ? 1.5 : 1;
			context.lineCap = 'round';
			context.lineJoin = 'round';
			context.stroke();
		}
	}

	function drawPointLayer(context, projection, mode) {
		const radius = state.active.length > 1000 ? 1.6 : 2.4;
		for (const index of state.active) {
			const row = track(index);
			const latitude = Number(row[mode === 'lysis' ? T.end_lat_x1000 : T.gen_lat_x1000]) / 1000;
			const longitude = Number(row[mode === 'lysis' ? T.end_lon_x1000 : T.gen_lon_x1000]) / 1000;
			const point = projection.project(latitude, longitude);
			context.fillStyle = rgba(trackColour(index), .68);
			context.beginPath(); context.arc(point[0], point[1], radius, 0, Math.PI * 2); context.fill();
		}
	}

	function mapLegend(layer, maximum) {
		const node = $('#mlaMapLegend');
		let trackLegend;
		if (layer === 'density') {
			trackLegend = `<span class="mla-legend-item"><span class="mla-swatch" style="background:${ramp(.2)}"></span>fewer</span><span class="mla-legend-item"><span class="mla-swatch" style="background:${ramp(1)}"></span>up to ${fmt(maximum)} tracks/cell</span>`;
		} else if (state.mapColour === 'class') {
			trackLegend = [1, 2, 3, 4, 5, 6].map(value => `<span class="mla-legend-item"><span class="mla-swatch" style="background:${CLASS_COLOURS[value]}"></span>${CLASS_SHORT[value]}</span>`).join('');
		} else if (state.mapColour === 'single') {
			trackLegend = `<span class="mla-legend-item"><span class="mla-swatch" style="background:${trackColour(0)}"></span>v5.4 track</span>`;
		} else if (state.mapColour === 'qc') {
			trackLegend = QC_LABELS.map((value, index) => `<span class="mla-legend-item"><span class="mla-swatch" style="background:${[css('--mla-good', '#2f7152'), css('--mla-review', '#a06912'), css('--mla-flag', '#a23d34')][index]}"></span>${value}</span>`).join('');
		} else {
			trackLegend = `<span class="mla-legend-item"><span class="mla-swatch" style="background:${ramp(.1)}"></span>low</span><span class="mla-legend-item"><span class="mla-swatch" style="background:${ramp(.55)}"></span>middle</span><span class="mla-legend-item"><span class="mla-swatch" style="background:${ramp(1)}"></span>high</span>`;
		}
		const rainfall = stateRainfallSummary();
		const rainfallLegend = rainfall
			? `<span class="mla-legend-item"><span class="mla-swatch" style="background:${rainfallColour(0)}"></span>state rain 0</span><span class="mla-legend-item"><span class="mla-swatch" style="background:${rainfallColour(1)}"></span>${fmt(rainfall.maximum)} mm/day</span>`
			: state.stateFill === 'selected' ? '<span class="mla-legend-item">Select a system for state rainfall</span>' : '';
		node.innerHTML = rainfallLegend + trackLegend;
	}

	function drawMapData() {
		const drawing = setupCanvas('mlaMapData');
		const layer = effectiveLayer();
		let maximum = 0;
		if (layer === 'density') maximum = drawDensity(drawing.context, drawing.projection);
		else if (layer === 'tracks') drawTrackLayer(drawing.context, drawing.projection);
		else drawPointLayer(drawing.context, drawing.projection, layer);
		const pathLabel = state.mapPath === 'months' ? `${fmt(visiblePointCount(state.active))} selected-month positions` : 'whole lifecycles';
		const rainfall = stateRainfallSummary();
		const rainfallLabel = rainfall ? ` · IMD state mean across ${fmt(rainfall.systemDays)} system-days` : '';
		$('#mlaMapStatus').textContent = `${fmt(state.active.length)} systems · ${layer === 'density' ? 'unique-track density' : layer} · ${pathLabel}${rainfallLabel} · zoom ${fmt(state.mapZoom, 1)}×`;
		renderStateRainfallValues(rainfall);
		mapLegend(layer, maximum);
	}

	function strokeTrack(context, projection, index, colour, width, gapConnectors) {
		const hasPosterior = CORE.posterior_runs && (CORE.posterior_runs[index] || []).length;
		context.save();
		context.lineCap = 'round';
		context.lineJoin = 'round';
		if (hasPosterior) {
			context.beginPath();
			appendTrackPath(context, projection, index, 1, false);
			context.setLineDash([4, 4]);
			context.strokeStyle = rgba(colour, .42);
			context.lineWidth = width;
			context.stroke();
			context.beginPath();
			appendObservedTrackPath(context, projection, index);
			context.setLineDash([]);
			context.strokeStyle = colour;
			context.lineWidth = width;
			context.stroke();
		} else {
			context.beginPath();
			appendTrackPath(context, projection, index, 1, false);
			context.strokeStyle = colour;
			context.lineWidth = width;
			context.stroke();
		}
		context.restore();
		if (!gapConnectors) return;
		const points = paths.decoded[index];
		context.save();
		context.setLineDash([5, 4]);
		context.strokeStyle = rgba(colour, .50);
		context.lineWidth = Math.max(1, width * .45);
		for (const entry of CORE.breaks[index] || []) {
			const pointIndex = Number(entry[0]);
			const gap = Number(entry[1]);
			const speed = Number(entry[2]);
			if (pointIndex <= 0 || pointIndex >= points.length || gap > 18 || speed > 35) continue;
			const first = projection.project(points[pointIndex - 1][0], points[pointIndex - 1][1]);
			const second = projection.project(points[pointIndex][0], points[pointIndex][1]);
			context.beginPath(); context.moveTo(first[0], first[1]); context.lineTo(second[0], second[1]); context.stroke();
		}
		context.restore();
	}

	function drawMapOverlay() {
		const drawing = setupCanvas('mlaMapOverlay');
		if (state.hovered != null && state.hovered !== state.selected && state.activeBit[state.hovered]) strokeTrack(drawing.context, drawing.projection, state.hovered, css('--mla-madder', '#aa3d2d'), 2.5, false);
		if (state.selected == null) return;
		strokeTrack(drawing.context, drawing.projection, state.selected, css('--mla-card', '#fffaf0'), 6.4, false);
		strokeTrack(drawing.context, drawing.projection, state.selected, css('--mla-indigo-deep', '#17294f'), 3.6, true);
		const item = credibleIb(state.selected);
		if (item && CORE.ibtracs_tracks[item.sid] && CORE.ibtracs_tracks[item.sid].path) {
			const official = decodePolyline(CORE.ibtracs_tracks[item.sid].path);
			drawing.context.save();
			drawing.context.beginPath();
			official.forEach((point, index) => {
				const projected = drawing.projection.project(point[0], point[1]);
				if (!index) drawing.context.moveTo(projected[0], projected[1]); else drawing.context.lineTo(projected[0], projected[1]);
			});
			drawing.context.setLineDash([7, 5]);
			drawing.context.strokeStyle = css('--mla-peacock', '#08736f');
			drawing.context.lineWidth = 2.2;
			drawing.context.stroke();
			drawing.context.restore();
		}
		const selectedPoints = paths.decoded[state.selected];
		const visibleIndexes = selectedPoints.map((unused, index) => index).filter(index => pointVisible(state.selected, index));
		if (visibleIndexes.length) {
			const firstIndex = visibleIndexes[0];
			const lastIndex = visibleIndexes[visibleIndexes.length - 1];
			const genesis = drawing.projection.project(selectedPoints[firstIndex][0], selectedPoints[firstIndex][1]);
			const lysis = drawing.projection.project(selectedPoints[lastIndex][0], selectedPoints[lastIndex][1]);
			drawing.context.fillStyle = css('--mla-madder', '#aa3d2d'); drawing.context.beginPath(); drawing.context.arc(genesis[0], genesis[1], 5, 0, Math.PI * 2); drawing.context.fill();
			drawing.context.fillStyle = css('--mla-peacock', '#08736f'); drawing.context.beginPath(); drawing.context.arc(lysis[0], lysis[1], 5, 0, Math.PI * 2); drawing.context.fill();
		}
	}

	function resetMapView() {
		const bounds = mapBounds();
		state.mapZoom = 1;
		state.mapCenterLon = (bounds.lonMin + bounds.lonMax) / 2;
		state.mapCenterLat = (bounds.latMin + bounds.latMax) / 2;
		mapScheduler.invalidate(MAP_DIRTY.ALL);
	}

	const scheduleMapUrl = debounce(() => writeUrl('replace'), 180);

	function setMapZoom(value, x, y, options) {
		const canvas = $('#mlaMapOverlay');
		const rectangle = canvas.getBoundingClientRect();
		const before = mapProjection(rectangle.width, rectangle.height);
		const pointX = x == null ? rectangle.width / 2 : x;
		const pointY = y == null ? rectangle.height / 2 : y;
		const geographical = before.invert(pointX, pointY);
		state.mapZoom = clamp(value, 1, 16);
		const after = mapProjection(rectangle.width, rectangle.height);
		const current = after.invert(pointX, pointY);
		state.mapCenterLat += geographical[0] - current[0];
		state.mapCenterLon += geographical[1] - current[1];
		constrainMapView(rectangle.width, rectangle.height);
		mapScheduler.invalidate(MAP_DIRTY.ALL);
		if (options && options.immediateUrl) writeUrl('replace');
		else scheduleMapUrl();
	}

	function mapHitTest(clientX, clientY, touch) {
		const canvas = $('#mlaMapOverlay');
		const rectangle = canvas.getBoundingClientRect();
		const x = clientX - rectangle.left;
		const y = clientY - rectangle.top;
		const projection = mapProjection(rectangle.width, rectangle.height);
		const geographical = projection.invert(x, y);
		const radiusPx = touch ? 18 : 10;
		return segmentIndex.query({
			x, y,
			lat: geographical[0],
			lon: geographical[1],
			radiusPx,
			radiusLon: radiusPx / projection.scale,
			radiusLat: radiusPx / projection.scale,
			project: projection.project,
			active: state.activeBit,
			segmentVisible: (trackIndex, pointIndex) => pointVisible(trackIndex, pointIndex - 1) && pointVisible(trackIndex, pointIndex)
		});
	}

	function updateMapHover(clientX, clientY, touch) {
		const index = mapHitTest(clientX, clientY, touch);
		state.hovered = index >= 0 ? index : null;
		mapScheduler.invalidate(MAP_DIRTY.OVERLAY);
		const tip = $('#mlaMapTip');
		if (index < 0) { tip.dataset.visible = 'false'; return; }
		const rectangle = $('#mlaMapStack').getBoundingClientRect();
		const row = track(index);
		const item = credibleIb(index);
		tip.innerHTML = `<strong>${esc(systemLabel(index))}</strong><br>${esc(date(row[T.start_ms]))} · ${esc(CORE.cat_labels[String(row[T.category])])}<br>${esc(metric().title)} P${fmt(percentileMetric(index))} · ${fmt(rawMetric(index), 1)} ${esc(metric().unit)}${item ? `<br>IBTrACS ${esc(item.confidence)} · median ${fmt(item.median_km)} km` : ''}`;
		tip.style.left = `${clamp(clientX - rectangle.left, 150, rectangle.width - 150)}px`;
		tip.style.top = `${clamp(clientY - rectangle.top, 70, rectangle.height - 20)}px`;
		tip.dataset.visible = 'true';
	}

	function fitMapToBounds(bounds) {
		if (!bounds) return false;
		const focus = mapBounds();
		const full = bounds[2] > focus.lonMax || bounds[0] < focus.lonMin || bounds[3] > focus.latMax || bounds[1] < focus.latMin;
		if (full) state.mapScope = 'full';
		const canvas = $('#mlaMapOverlay');
		const rectangle = canvas.getBoundingClientRect();
		const scope = mapBounds();
		const baseScale = Math.min((rectangle.width - 48) / (scope.lonMax - scope.lonMin), (rectangle.height - 48) / (scope.latMax - scope.latMin));
		const neededScale = Math.min((rectangle.width - 90) / Math.max(1.5, bounds[2] - bounds[0]), (rectangle.height - 90) / Math.max(1.5, bounds[3] - bounds[1]));
		state.mapZoom = clamp(neededScale / baseScale, 1, 12);
		state.mapCenterLon = (bounds[0] + bounds[2]) / 2;
		state.mapCenterLat = (bounds[1] + bounds[3]) / 2;
		constrainMapView(rectangle.width, rectangle.height);
		syncControls();
		mapScheduler.invalidate(MAP_DIRTY.ALL);
		writeUrl('replace');
		return true;
	}

	function fitSelected() {
		if (state.selected == null) return;
		fitMapToBounds(visibleTrackBounds([state.selected]) || CORE.bounds[state.selected]);
	}

	function fitCohort(options) {
		if (!state.active.length) return;
		if (fitMapToBounds(visibleTrackBounds(state.active)) && !(options && options.quiet)) toast(`Fitted ${fmt(state.active.length)} systems`);
	}

	function bindMap() {
		const canvas = $('#mlaMapOverlay');
		const pointers = new Map();
		let drag = null;
		let pinch = null;
		let suppressTap = false;
		function pinchMetrics() {
			const points = [...pointers.values()].slice(0, 2);
			if (points.length < 2) return null;
			return {
				distance: Math.hypot(points[1].x - points[0].x, points[1].y - points[0].y),
				x: (points[0].x + points[1].x) / 2,
				y: (points[0].y + points[1].y) / 2
			};
		}
		canvas.addEventListener('pointerdown', event => {
			pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});
			if (pointers.size === 1) drag = {x: event.clientX, y: event.clientY, startX: event.clientX, startY: event.clientY, moved: false};
			if (pointers.size === 2) {
				const metrics = pinchMetrics();
				pinch = {distance: Math.max(1, metrics.distance), zoom: state.mapZoom};
				drag = null;
			}
			canvas.classList.add('is-dragging');
			if (canvas.setPointerCapture) canvas.setPointerCapture(event.pointerId);
		});
		canvas.addEventListener('pointermove', event => {
			if (pointers.has(event.pointerId)) pointers.set(event.pointerId, {x: event.clientX, y: event.clientY});
			if (pinch && pointers.size >= 2) {
				event.preventDefault();
				const metrics = pinchMetrics();
				const rectangle = canvas.getBoundingClientRect();
				setMapZoom(pinch.zoom * metrics.distance / pinch.distance, metrics.x - rectangle.left, metrics.y - rectangle.top);
				return;
			}
			if (drag) {
				const dx = event.clientX - drag.x;
				const dy = event.clientY - drag.y;
				if (Math.abs(event.clientX - drag.startX) + Math.abs(event.clientY - drag.startY) > 5) drag.moved = true;
				if (drag.moved) {
					event.preventDefault();
					const rectangle = canvas.getBoundingClientRect();
					const projection = mapProjection(rectangle.width, rectangle.height);
					state.mapCenterLon -= dx / projection.scale;
					state.mapCenterLat += dy / projection.scale;
					constrainMapView(rectangle.width, rectangle.height);
					drag.x = event.clientX;
					drag.y = event.clientY;
					mapScheduler.invalidate(MAP_DIRTY.ALL);
					return;
				}
			}
			pendingPointer = {x: event.clientX, y: event.clientY, touch: event.pointerType === 'touch'};
			if (!pointerFrame) pointerFrame = requestAnimationFrame(() => {
				pointerFrame = 0;
				if (pendingPointer) updateMapHover(pendingPointer.x, pendingPointer.y, pendingPointer.touch);
			});
		});
		canvas.addEventListener('pointerup', event => {
			const wasPinching = Boolean(pinch);
			const moved = drag && drag.moved;
			pointers.delete(event.pointerId);
			if (wasPinching) {
				suppressTap = true;
				pinch = null;
				const remaining = [...pointers.values()][0];
				drag = remaining ? {x: remaining.x, y: remaining.y, startX: remaining.x, startY: remaining.y, moved: false} : null;
				if (!pointers.size) canvas.classList.remove('is-dragging');
				scheduleMapUrl();
				return;
			}
			drag = null;
			canvas.classList.remove('is-dragging');
			if (suppressTap) { suppressTap = false; return; }
			if (moved) { writeUrl('replace'); return; }
			const index = mapHitTest(event.clientX, event.clientY, event.pointerType === 'touch');
			if (index >= 0) selectTrack(index);
		});
		canvas.addEventListener('pointercancel', event => {
			pointers.delete(event.pointerId);
			drag = null;
			pinch = null;
			canvas.classList.remove('is-dragging');
		});
		canvas.addEventListener('pointerleave', () => { if (!drag) { state.hovered = null; $('#mlaMapTip').dataset.visible = 'false'; mapScheduler.invalidate(MAP_DIRTY.OVERLAY); } });
		canvas.addEventListener('wheel', event => {
			event.preventDefault();
			const rectangle = canvas.getBoundingClientRect();
			setMapZoom(state.mapZoom * (event.deltaY < 0 ? 1.22 : 1 / 1.22), event.clientX - rectangle.left, event.clientY - rectangle.top);
		}, {passive: false});
		canvas.addEventListener('dblclick', event => {
			event.preventDefault();
			const rectangle = canvas.getBoundingClientRect();
			setMapZoom(state.mapZoom * 1.65, event.clientX - rectangle.left, event.clientY - rectangle.top, {immediateUrl: true});
		});
		$('#mlaZoomIn').addEventListener('click', () => setMapZoom(state.mapZoom * 1.35));
		$('#mlaZoomOut').addEventListener('click', () => setMapZoom(state.mapZoom / 1.35));
		$('#mlaZoomReset').addEventListener('click', () => { resetMapView(); writeUrl('replace'); });
		new ResizeObserver(() => mapScheduler.invalidate(MAP_DIRTY.ALL)).observe($('#mlaMapStack'));
	}

	function badge(text, tone) {
		return `<span class="mla-badge" data-tone="${esc(tone || 'official')}">${esc(text)}</span>`;
	}

	function officialGrade(index) {
		const item = crosswalk(index);
		if (item && item.imd && item.imd.system.peak_grade) return item.imd.system.peak_grade;
		if (item && item.ib && CORE.ibtracs_tracks[item.ib.sid] && CORE.ibtracs_tracks[item.ib.sid].imd_peak_grade) return CORE.ibtracs_tracks[item.ib.sid].imd_peak_grade;
		return '';
	}

	function qcExplanation(index) {
		const qc = CORE.qc[index];
		const row = track(index);
		return `${fmt(qc[2], 1)}% observed-position occupancy; longest missing run ${fmt(row[T.max_missing_run_hours])} h; maximum linked speed ${fmt(qc[1], 1)} m s⁻¹`;
	}

	function renderDossier() {
		const node = $('#mlaDossier');
		if (state.selected == null) {
			if (!state.active.length) {
				node.innerHTML = '<div class="mla-dossier-head"><div><h3>No matching systems</h3><p class="mla-dossier-sub">Adjust or reset the active filters.</p></div></div>';
				return;
			}
			const durations = state.active.map(index => Number(track(index)[T.duration_hours]));
			const support = state.active.map(index => Number(CORE.qc[index][2]));
			const named = state.active.filter(index => Boolean(officialName(index))).length;
			const strong = state.active.filter(index => Number(CORE.qc[index][4]) === 0).length;
			const facts = [
				['Systems', fmt(state.active.length)],
				['Median duration', durationText(median(durations))],
				['Median observed support', `${fmt(median(support), 1)}%`],
				['Strong continuity support', `${fmt(strong / state.active.length * 100, 1)}%`],
				['Named cyclone matches', fmt(named)],
				['Displayed fixes', fmt(visiblePointCount(state.active))]
			];
			node.innerHTML = `<div class="mla-dossier-head"><div><span class="mla-badge" data-tone="official">Current cohort</span><h3>${fmt(state.active.length)} systems</h3><p class="mla-dossier-sub">${state.mapPath === 'months' ? 'Selected-month fixes' : 'Whole lifecycles'} on the map</p></div></div><div class="mla-fact-grid">${facts.map(fact => `<div class="mla-fact"><span>${esc(fact[0])}</span><strong>${esc(fact[1])}</strong></div>`).join('')}</div><p class="mla-dossier-empty">Select a track for its weather evolution, continuity diagnostics and downloads.</p>`;
			return;
		}
		const index = state.selected;
		const row = track(index);
		const qc = CORE.qc[index];
		const badges = [
			badge(`Atlas ${CLASS_SHORT[row[T.category]]}`, 'official'),
			badge(QC_LABELS[qc[4]], QC_TONES[qc[4]])
		];
		const facts = [
			['Duration', durationText(row[T.duration_hours])],
			['Observed fixes', fmt(row[T.observed_positions])],
			['Interpolated', `${fmt(row[T.posterior_fraction_x1000] / 10, 1)}%`],
			['Qualifying fixes', fmt(row[T.qualifying_positions])],
			['Track stitches', fmt(row[T.stitch_count])],
			['Pressure deficit', `${fmt(row[T.peak_deficit_x10] / 10, 1)} hPa`],
			['Maximum wind', `${fmt(row[T.peak_wind_x10] / 10, 1)} m s⁻¹`],
			['Minimum MSLP', `${fmt(row[T.min_mslp_x10] / 10, 1)} hPa`],
			['Peak 24 h rain', `${fmt(row[T.peak_precip_x10] / 10, 1)} mm`],
			['Linked path', `${fmt(row[T.distance_km])} km`],
			['Peak q850', `${fmt(row[T.peak_q850_x10] / 10, 1)} g kg⁻¹`]
		];
		node.innerHTML = `
			<div class="mla-dossier-head"><div><div class="mla-badge-row">${badges.join('')}</div><h3>${esc(systemLabel(index))}</h3><p class="mla-dossier-sub">${date(row[T.start_ms])} to ${date(row[T.end_ms])} · stable track ID ${atlasId(index)}</p></div><button class="mla-btn mla-btn-icon mla-btn-small" id="mlaClearSelection" type="button" aria-label="Clear selected track">×</button></div>
			<div class="mla-fact-grid">${facts.map(fact => `<div class="mla-fact"><span>${esc(fact[0])}</span><strong>${esc(fact[1])}</strong></div>`).join('')}</div>
			<p class="mla-caution"><strong>Continuity:</strong> ${esc(qcExplanation(index))}. Dashed sections are interpolated between observed-support positions; v5.4 physics is resampled at every published centre.</p>
			<div class="mla-dossier-actions"><button class="mla-btn mla-btn-small" id="mlaPreviousTrack" type="button">Previous</button><button class="mla-btn mla-btn-small" id="mlaNextTrack" type="button">Next</button><button class="mla-btn mla-btn-small" id="mlaFitTrack" type="button">Fit track</button><button class="mla-btn mla-btn-small" id="mlaSelectedFixes" type="button">Download fixes</button></div>
			`;
		$('#mlaClearSelection').addEventListener('click', () => selectTrack(null));
		$('#mlaPreviousTrack').addEventListener('click', () => stepSelected(-1));
		$('#mlaNextTrack').addEventListener('click', () => stepSelected(1));
		$('#mlaFitTrack').addEventListener('click', fitSelected);
		$('#mlaSelectedFixes').addEventListener('click', downloadSelectedFixes);
	}

	function sortedActive(sortValue) {
		const indexes = state.active.slice();
		const confidenceRank = {high: 3, medium: 2, low: 1};
		indexes.sort((first, second) => {
			if (sortValue === 'date-desc') return track(second)[T.start_ms] - track(first)[T.start_ms];
			if (sortValue === 'date-asc') return track(first)[T.start_ms] - track(second)[T.start_ms];
			if (sortValue === 'duration-desc') return track(second)[T.duration_hours] - track(first)[T.duration_hours];
			if (sortValue === 'distance-desc') return track(second)[T.distance_km] - track(first)[T.distance_km];
			if (sortValue === 'match') {
				const a = crosswalk(first);
				const b = crosswalk(second);
				return (confidenceRank[b && b.ib ? b.ib.confidence : ''] || 0) - (confidenceRank[a && a.ib ? a.ib.confidence : ''] || 0) || percentileMetric(second) - percentileMetric(first);
			}
			return percentileMetric(second) - percentileMetric(first) || rawMetric(second) * metric().direction - rawMetric(first) * metric().direction;
		});
		return indexes;
	}

	function stepSelected(direction) {
		const indexes = sortedActive(state.sort);
		if (!indexes.length) return;
		const current = indexes.indexOf(state.selected);
		selectTrack(indexes[(current + direction + indexes.length) % indexes.length]);
	}

	function selectTrack(index, options) {
		state.selected = Number.isInteger(index) ? index : null;
		state.hovered = null;
		rainfallMapCache = null;
		$('#mlaMapTip').dataset.visible = 'false';
		if (options && options.openExplore) activateTab('explore', true);
		renderDossier();
		renderTopTable();
		mapScheduler.invalidate((state.stateFill === 'selected' ? MAP_DIRTY.BASE | MAP_DIRTY.DATA : 0) | MAP_DIRTY.OVERLAY);
		renderLifeCharts();
		if (state.selected != null && options && options.fit) requestAnimationFrame(fitSelected);
		writeUrl('push');
	}

	function tableHead() {
		return `<tr><th>System</th><th>Genesis</th><th>Peak class</th><th>${esc(metric().title)}</th><th>Duration</th><th>Observed support</th></tr>`;
	}

	function tableRow(index, openExplore) {
		const row = track(index);
		const qc = CORE.qc[index];
		return `<tr data-selected="${index === state.selected}"><td><button class="mla-row-button" type="button" data-select-track="${index}" data-open-explore="${openExplore}">${esc(systemLabel(index))}</button></td><td>${date(row[T.start_ms])}</td><td>${esc(CLASS_SHORT[row[T.category]])}</td><td class="mla-num">${fmt(rawMetric(index), 1)} ${esc(metric().unit)}<br><small>P${fmt(percentileMetric(index))}</small></td><td class="mla-num">${durationText(row[T.duration_hours])}</td><td>${badge(QC_LABELS[qc[4]], QC_TONES[qc[4]])}<br><small>${fmt(qc[2])}% observed</small></td></tr>`;
	}

	function renderTopTable() {
		const table = $('#mlaTopTable');
		table.querySelector('thead').innerHTML = tableHead();
		table.querySelector('tbody').innerHTML = sortedActive('metric-desc').slice(0, 12).map(index => tableRow(index, false)).join('') || '<tr><td colspan="6">No systems match the current filters.</td></tr>';
	}

	function renderSystems() {
		if ($('#mlaPanelSystems').hidden) return;
		const indexes = sortedActive(state.sort);
		const pages = Math.max(1, Math.ceil(indexes.length / state.pageSize));
		state.page = clamp(state.page, 1, pages);
		const start = (state.page - 1) * state.pageSize;
		const table = $('#mlaSystemsTable');
		table.querySelector('thead').innerHTML = tableHead();
		table.querySelector('tbody').innerHTML = indexes.slice(start, start + state.pageSize).map(index => tableRow(index, true)).join('') || '<tr><td colspan="6">No systems match the current filters.</td></tr>';
		$('#mlaPageReadout').textContent = indexes.length ? `Rows ${fmt(start + 1)}–${fmt(Math.min(indexes.length, start + state.pageSize))} of ${fmt(indexes.length)} · page ${state.page} of ${pages}` : 'No matching systems';
		$('#mlaPrevPage').disabled = state.page <= 1;
		$('#mlaNextPage').disabled = state.page >= pages;
	}

	function setupChart(id) {
		const canvas = document.getElementById(id);
		if (!canvas || canvas.offsetParent === null) return null;
		const rectangle = canvas.getBoundingClientRect();
		const ratio = Math.min(2, devicePixelRatio || 1);
		const width = Math.max(1, Math.round(rectangle.width));
		const height = Math.max(1, Math.round(rectangle.height));
		if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
			canvas.width = Math.round(width * ratio);
			canvas.height = Math.round(height * ratio);
		}
		const context = canvas.getContext('2d');
		context.setTransform(ratio, 0, 0, ratio, 0, 0);
		context.clearRect(0, 0, width, height);
		context.fillStyle = css('--mla-card-strong', '#fffaf0');
		context.fillRect(0, 0, width, height);
		return {canvas, context, width, height};
	}

	function emptyChart(id, message) {
		const drawing = setupChart(id);
		if (!drawing) return;
		drawing.context.fillStyle = css('--mla-muted', '#685c4d');
		drawing.context.font = '14px Aptos, Segoe UI, sans-serif';
		drawing.context.fillText(message || 'No data for this cohort', 18, 34);
	}

	function drawLinePlot(id, series, options) {
		const drawing = setupChart(id);
		if (!drawing) return;
		series = series.filter(item => item.points && item.points.some(point => Number.isFinite(point.y)));
		if (!series.length) { emptyChart(id); return; }
		const {context, width, height} = drawing;
		const padding = {left: 54, right: 18, top: 34, bottom: 38};
		const points = series.flatMap(item => item.points).filter(point => Number.isFinite(point.x) && Number.isFinite(point.y));
		const xMin = options && Number.isFinite(options.xMin) ? options.xMin : Math.min(...points.map(point => point.x));
		const xMax = options && Number.isFinite(options.xMax) ? options.xMax : Math.max(...points.map(point => point.x));
		const bandValues = series.flatMap(item => item.points.flatMap(point => [point.low, point.high])).filter(Number.isFinite);
		const yValues = points.map(point => point.y).concat(bandValues);
		let yMin = options && Number.isFinite(options.yMin) ? options.yMin : Math.min(...yValues);
		let yMax = options && Number.isFinite(options.yMax) ? options.yMax : Math.max(...yValues);
		if (yMin === yMax) { yMin -= 1; yMax += 1; }
		if (!(options && options.zero === false)) yMin = Math.min(0, yMin);
		const pad = (yMax - yMin) * .06;
		yMax += pad;
		if (yMin !== 0) yMin -= pad;
		const X = value => padding.left + (value - xMin) / ((xMax - xMin) || 1) * (width - padding.left - padding.right);
		const Y = value => height - padding.bottom - (value - yMin) / ((yMax - yMin) || 1) * (height - padding.top - padding.bottom);
		context.save();
		context.font = '11px ui-monospace, Consolas, monospace';
		context.fillStyle = css('--mla-muted', '#685c4d');
		context.strokeStyle = 'rgba(70, 60, 45, .16)';
		for (let tick = 0; tick <= 4; tick++) {
			const y = padding.top + tick * (height - padding.top - padding.bottom) / 4;
			const value = yMax - tick * (yMax - yMin) / 4;
			context.beginPath(); context.moveTo(padding.left, y); context.lineTo(width - padding.right, y); context.stroke();
			context.fillText(options && options.yFormat ? options.yFormat(value) : fmt(value, 1), 6, y + 4);
		}
		for (let tick = 0; tick <= 4; tick++) {
			const x = padding.left + tick * (width - padding.left - padding.right) / 4;
			const value = xMin + tick * (xMax - xMin) / 4;
			context.fillText(options && options.xFormat ? options.xFormat(value) : fmt(value), clamp(x - 14, padding.left, width - 56), height - 12);
		}
		for (const item of series) {
			const valid = item.points.filter(point => Number.isFinite(point.x) && Number.isFinite(point.y));
			if (valid.some(point => Number.isFinite(point.low) && Number.isFinite(point.high))) {
				context.beginPath();
				valid.forEach((point, index) => {
					const x = X(point.x);
					const y = Y(Number.isFinite(point.high) ? point.high : point.y);
					if (!index) context.moveTo(x, y); else context.lineTo(x, y);
				});
				valid.slice().reverse().forEach(point => context.lineTo(X(point.x), Y(Number.isFinite(point.low) ? point.low : point.y)));
				context.closePath();
				context.fillStyle = rgba(item.colour, .14);
				context.fill();
			}
			context.beginPath();
			let previous = null;
			for (const point of item.points) {
				if (!Number.isFinite(point.x) || !Number.isFinite(point.y) || point.breakBefore) {
					previous = null;
					if (!Number.isFinite(point.x) || !Number.isFinite(point.y)) continue;
				}
				const x = X(point.x);
				const y = Y(point.y);
				if (!previous) context.moveTo(x, y); else context.lineTo(x, y);
				previous = point;
			}
			context.strokeStyle = item.colour;
			context.lineWidth = item.width || 2.3;
			context.lineJoin = 'round';
			context.lineCap = 'round';
			context.stroke();
		}
		let legendX = padding.left;
		for (const item of series) {
			context.fillStyle = item.colour;
			context.fillRect(legendX, 15, 18, 3);
			context.fillStyle = css('--mla-ink', '#282119');
			context.font = '12px Aptos, Segoe UI, sans-serif';
			context.fillText(item.name, legendX + 24, 20);
			legendX += Math.min(190, 45 + item.name.length * 7);
		}
		context.restore();
	}

	function drawEvolutionPlot(trackIndex, metricKey) {
		const drawing = setupChart('mlaLifeChart');
		if (!drawing) return null;
		const definition = METRICS[metricKey];
		const lineSeries = seriesValues(trackIndex, metricKey);
		const rainSeries = seriesValues(trackIndex, 'rain');
		const hours = lineSeries.hours;
		if (!hours.length) { emptyChart('mlaLifeChart'); return null; }
		const posterior = new Uint8Array(hours.length);
		for (const range of CORE.posterior_runs[trackIndex] || []) posterior.fill(1, Number(range[0]), Number(range[1]) + 1);
		const breakPrefix = new Uint16Array(hours.length + 1);
		const breakSet = new Set((CORE.breaks[trackIndex] || []).map(item => Number(item[0])));
		for (let index = 0; index < hours.length; index++) breakPrefix[index + 1] = breakPrefix[index] + (breakSet.has(index) ? 1 : 0);
		const linePoints = hours.map((hour, index) => ({hour, value: lineSeries.values[index], index})).filter(point => Number.isFinite(point.value));
		const rainPoints = hours.map((hour, index) => ({hour, value: rainSeries.values[index], index})).filter(point => Number.isFinite(point.value));
		if (!linePoints.length && !rainPoints.length) { emptyChart('mlaLifeChart'); return null; }

		const {canvas, context, width, height} = drawing;
		const padding = {left: 58, right: 56, top: 42, bottom: 58};
		const plotBottom = height - padding.bottom;
		const plotWidth = width - padding.left - padding.right;
		const plotHeight = plotBottom - padding.top;
		const xMin = Number(hours[0]);
		const xMax = Number(hours[hours.length - 1]);
		let yMin = Math.min(...linePoints.map(point => point.value));
		let yMax = Math.max(...linePoints.map(point => point.value));
		if (['deficit', 'wind'].includes(metricKey)) yMin = Math.min(0, yMin);
		if (yMin === yMax) { yMin -= 1; yMax += 1; }
		const yPad = (yMax - yMin) * .08;
		yMax += yPad;
		if (yMin !== 0) yMin -= yPad;
		const rainMax = Math.max(1, ...rainPoints.map(point => point.value)) * 1.08;
		const X = value => padding.left + (value - xMin) / ((xMax - xMin) || 1) * plotWidth;
		const Y = value => plotBottom - (value - yMin) / ((yMax - yMin) || 1) * plotHeight;
		const R = value => plotBottom - value / rainMax * plotHeight;
		const timeLabel = value => xMax >= 96 ? `${fmt(value / 24, 1)} d` : `${fmt(value)} h`;

		context.save();
		context.font = '11px ui-monospace, Consolas, monospace';
		context.fillStyle = css('--mla-muted', '#685c4d');
		context.strokeStyle = 'rgba(70, 60, 45, .16)';
		for (let tick = 0; tick <= 4; tick++) {
			const y = padding.top + tick * plotHeight / 4;
			const leftValue = yMax - tick * (yMax - yMin) / 4;
			const rightValue = rainMax * (4 - tick) / 4;
			context.beginPath(); context.moveTo(padding.left, y); context.lineTo(width - padding.right, y); context.stroke();
			context.textAlign = 'left'; context.fillText(fmt(leftValue, 1), 6, y + 4);
			context.textAlign = 'right'; context.fillText(fmt(rightValue, 0), width - 6, y + 4);
		}
		for (let tick = 0; tick <= 4; tick++) {
			const value = xMin + tick * (xMax - xMin) / 4;
			context.textAlign = tick === 0 ? 'left' : tick === 4 ? 'right' : 'center';
			context.fillText(timeLabel(value), X(value), height - 12);
		}
		context.textAlign = 'left';
		context.font = '12px Aptos, Segoe UI, sans-serif';
		context.fillStyle = definition.colour;
		context.fillRect(padding.left, 16, 18, 3);
		context.fillStyle = css('--mla-ink', '#282119');
		context.fillText(`${definition.title} (${definition.unit})`, padding.left + 24, 21);
		const rainLegendX = Math.min(width - 126, padding.left + 215);
		context.fillStyle = rgba(METRICS.rain.colour, .52);
		context.fillRect(rainLegendX, 13, 10, 10);
		context.fillStyle = css('--mla-ink', '#282119');
		context.fillText('24 h rain (mm)', rainLegendX + 16, 21);

		const estimatedStep = hours.length > 1 ? median(hours.slice(1).map((value, index) => value - hours[index]).filter(value => value > 0)) : 1;
		const barWidth = clamp(plotWidth * Math.max(.6, estimatedStep) / Math.max(1, xMax - xMin), .8, 7);
		for (const point of rainPoints) {
			const x = X(point.hour) - barWidth / 2;
			context.fillStyle = rgba(METRICS.rain.colour, .32);
			context.fillRect(x, R(point.value), barWidth, Math.max(0, plotBottom - R(point.value)));
		}

		context.beginPath();
		let previous = null;
		for (const point of linePoints) {
			const hasStructuralBreak = previous && breakPrefix[point.index + 1] > breakPrefix[previous.index + 1];
			const shortBridge = previous && point.hour - previous.hour <= 6 && !hasStructuralBreak;
			if (!shortBridge) context.moveTo(X(point.hour), Y(point.value));
			else context.lineTo(X(point.hour), Y(point.value));
			previous = point;
		}
		context.strokeStyle = definition.colour;
		context.lineWidth = 2.35;
		context.lineJoin = 'round';
		context.lineCap = 'round';
		context.stroke();
		if (linePoints.length <= 160) {
			context.fillStyle = definition.colour;
			for (const point of linePoints) { context.beginPath(); context.arc(X(point.hour), Y(point.value), 1.7, 0, Math.PI * 2); context.fill(); }
		}

		const coverageY = plotBottom + 10;
		const coverageHeight = 7;
		for (let index = 0; index < hours.length; index++) {
			const leftHour = index ? (hours[index - 1] + hours[index]) / 2 : hours[index];
			const rightHour = index + 1 < hours.length ? (hours[index] + hours[index + 1]) / 2 : hours[index];
			context.fillStyle = posterior[index] ? 'rgba(104, 92, 77, .28)' : rgba(css('--mla-good', '#2f7152'), .72);
			context.fillRect(X(leftHour), coverageY, Math.max(1, X(rightHour) - X(leftHour) + .4), coverageHeight);
		}
		context.font = '10px Aptos, Segoe UI, sans-serif';
		context.fillStyle = css('--mla-muted', '#685c4d');
		context.fillText('coverage', 6, coverageY + 7);
		context.restore();

		const observedCount = hours.length - posterior.reduce((sum, value) => sum + value, 0);
		const summary = `${fmt(observedCount)} of ${fmt(hours.length)} linked positions have observed detector support (${fmt(observedCount / hours.length * 100, 1)}%); grey intervals are interpolated positions. Published-centre physics is available throughout.`;
		const readout = $('#mlaLifeReadout');
		readout.textContent = summary;
		function showPoint(event) {
			const rectangle = canvas.getBoundingClientRect();
			const targetHour = xMin + clamp((event.clientX - rectangle.left - padding.left) / Math.max(1, rectangle.width - padding.left - padding.right), 0, 1) * (xMax - xMin);
			let low = 0;
			let high = hours.length - 1;
			while (low < high) {
				const middle = Math.floor((low + high) / 2);
				if (hours[middle] < targetHour) low = middle + 1; else high = middle;
			}
			const index = low > 0 && Math.abs(hours[low - 1] - targetHour) < Math.abs(hours[low] - targetHour) ? low - 1 : low;
			const source = posterior[index] ? 'interpolated position' : 'observed-support position';
			readout.textContent = `${timeLabel(hours[index])} from genesis · ${source} · ${definition.title} ${fmt(lineSeries.values[index], 1)} ${definition.unit} · 24 h rain ${fmt(rainSeries.values[index], 1)} mm.`;
		}
		canvas.onpointermove = showPoint;
		canvas.onpointerdown = showPoint;
		canvas.onpointerleave = () => { readout.textContent = summary; };
		canvas.setAttribute('aria-label', `${definition.title} line with 24-hour rainfall bars and position-source coverage for ${systemLabel(trackIndex)}`);
		return {hours, lineValues: lineSeries.values, rainValues: rainSeries.values, posterior, summary};
	}

	function drawBars(id, items, options) {
		const drawing = setupChart(id);
		if (!drawing) return;
		if (!items.length) { emptyChart(id); return; }
		const {context, width, height} = drawing;
		const padding = {left: 46, right: 16, top: 24, bottom: 46};
		const maximum = Math.max(1, ...items.map(item => item.value));
		const barWidth = (width - padding.left - padding.right) / items.length;
		context.font = '11px ui-monospace, Consolas, monospace';
		context.fillStyle = css('--mla-muted', '#685c4d');
		context.strokeStyle = 'rgba(70, 60, 45, .16)';
		for (let tick = 0; tick <= 4; tick++) {
			const y = padding.top + tick * (height - padding.top - padding.bottom) / 4;
			context.beginPath(); context.moveTo(padding.left, y); context.lineTo(width - padding.right, y); context.stroke();
			context.fillText(fmt(maximum * (4 - tick) / 4, options && options.decimals ? options.decimals : 0), 5, y + 4);
		}
		items.forEach((item, index) => {
			const x = padding.left + index * barWidth + barWidth * .12;
			const barHeight = item.value / maximum * (height - padding.top - padding.bottom);
			const y = height - padding.bottom - barHeight;
			context.fillStyle = item.colour || css('--mla-peacock', '#08736f');
			context.fillRect(x, y, Math.max(2, barWidth * .76), barHeight);
			context.save();
			context.translate(x + barWidth * .38, height - 12);
			if (items.length > 10) context.rotate(-Math.PI / 5);
			context.fillStyle = css('--mla-muted', '#685c4d');
			context.fillText(item.label, -8, 0);
			context.restore();
		});
	}

	function drawHeatmap(id, rows, columns, matrix, options) {
		const drawing = setupChart(id);
		if (!drawing) return;
		const {context, width, height} = drawing;
		const padding = {left: options && options.left || 72, right: 14, top: 20, bottom: 40};
		const cellWidth = (width - padding.left - padding.right) / columns.length;
		const cellHeight = (height - padding.top - padding.bottom) / rows.length;
		const maximum = Math.max(1, ...matrix.flat().filter(Number.isFinite));
		context.font = '11px ui-monospace, Consolas, monospace';
		context.fillStyle = css('--mla-muted', '#685c4d');
		columns.forEach((label, index) => context.fillText(label, padding.left + index * cellWidth + 3, height - 15));
		rows.forEach((label, row) => {
			context.fillText(label, 7, padding.top + row * cellHeight + cellHeight * .64);
			columns.forEach((unused, column) => {
				const value = matrix[row][column];
				context.fillStyle = Number.isFinite(value) && value > 0 ? ramp(value / maximum) : 'rgba(90, 75, 55, .08)';
				context.fillRect(padding.left + column * cellWidth, padding.top + row * cellHeight, Math.max(1, cellWidth - 1), Math.max(1, cellHeight - 1));
				if (cellWidth > 34 && cellHeight > 22 && Number.isFinite(value) && value > 0) {
					context.fillStyle = value / maximum > .58 ? '#fffaf0' : '#282119';
					context.fillText(fmt(value, options && options.decimals ? options.decimals : 0), padding.left + column * cellWidth + 4, padding.top + row * cellHeight + cellHeight * .64);
				}
			});
		});
	}

	function fixedProjection(width, height, bounds) {
		const padding = 22;
		const scale = Math.min((width - padding * 2) / (bounds.lonMax - bounds.lonMin), (height - padding * 2) / (bounds.latMax - bounds.latMin));
		const centreLon = (bounds.lonMin + bounds.lonMax) / 2;
		const centreLat = (bounds.latMin + bounds.latMax) / 2;
		return {
			project(latitude, longitude) { return [width / 2 + (longitude - centreLon) * scale, height / 2 - (latitude - centreLat) * scale]; },
			viewBounds: bounds
		};
	}

	function drawGenesisMap() {
		const drawing = setupChart('mlaGenesisChart');
		if (!drawing) return;
		const projection = fixedProjection(drawing.width, drawing.height, {lonMin: 48, lonMax: 118, latMin: -5, latMax: 47});
		drawMapGeography(drawing.context, projection, drawing.width, drawing.height, {});
		const cells = new Map();
		for (const index of state.active) {
			const row = track(index);
			const lat = row[T.gen_lat_x1000] / 1000;
			const lon = row[T.gen_lon_x1000] / 1000;
			const key = `${Math.floor(lon * 2)},${Math.floor(lat * 2)}`;
			cells.set(key, (cells.get(key) || 0) + 1);
		}
		const maximum = Math.max(1, ...cells.values());
		for (const [key, value] of cells) {
			const [lonCell, latCell] = key.split(',').map(Number);
			const lon = lonCell / 2;
			const lat = latCell / 2;
			const topLeft = projection.project(lat + .5, lon);
			const bottomRight = projection.project(lat, lon + .5);
			drawing.context.fillStyle = rgba(ramp(Math.sqrt(value / maximum)), .82);
			drawing.context.fillRect(topLeft[0], topLeft[1], Math.max(1, bottomRight[0] - topLeft[0]), Math.max(1, bottomRight[1] - topLeft[1]));
		}
	}

	function completeYear(year) {
		if (year > COMPLETE_END_YEAR) return false;
		const coverageStart = new Date(CORE.meta.coverage_start);
		const firstYear = coverageStart.getUTCFullYear();
		const firstMonth = coverageStart.getUTCMonth() + 1;
		if (year > firstYear) return true;
		if (year < firstYear) return false;
		return ![...state.months].some(month => month < firstMonth);
	}

	function renderClimatology() {
		if ($('#mlaPanelClimatology').hidden) return;
		const annual = new Map();
		for (const index of state.active) {
			const year = track(index)[T.start_year];
			annual.set(year, (annual.get(year) || 0) + 1);
		}
		const annualPoints = [];
		for (let year = periodYearMin(); year <= periodYearMax(); year++) {
			if (!completeYear(year)) continue;
			annualPoints.push({x: year, y: annual.get(year) || 0});
		}
		drawLinePlot('mlaAnnualChart', [{name: 'Systems', colour: css('--mla-indigo', '#233f78'), points: annualPoints}], {zero: true, xFormat: value => String(Math.round(value)), yFormat: value => fmt(value)});
		$('#mlaAnnualData').innerHTML = accessibleTable(['Year', 'Systems'], annualPoints.map(point => [point.x, point.y]), periodYearMax() > COMPLETE_END_YEAR ? '2026 is partial and excluded.' : '');

		const monthly = Array(12).fill(0);
		for (const index of state.active) {
			const row = track(index);
			if (state.monthMode === 'genesis') monthly[new Date(row[T.start_ms]).getUTCMonth()]++;
			else if (state.monthMode === 'peak') monthly[CORE.peak_months[index][metric().peakMonth] - 1]++;
			else {
				for (let month = 1; month <= 12; month++) if (row[T.month_mask] & (1 << (month - 1))) monthly[month - 1]++;
			}
		}
		drawBars('mlaMonthChart', MONTHS.map((label, index) => ({label, value: monthly[index], colour: index >= 5 && index <= 8 ? css('--mla-peacock', '#08736f') : css('--mla-saffron', '#c9631b')})));
		$('#mlaMonthData').innerHTML = accessibleTable(['Month', state.monthMode === 'active' ? 'Event-months' : 'Systems'], MONTHS.map((month, index) => [month, monthly[index]]));

		const decadeStart = Math.floor(periodYearMin() / 10) * 10;
		const decades = [];
		for (let value = decadeStart; value <= Math.min(periodYearMax(), COMPLETE_END_YEAR); value += 10) decades.push(value);
		const classMatrix = decades.map(() => Array(6).fill(0));
		const exposure = decades.map(decade => {
			let years = 0;
			for (let year = Math.max(decade, periodYearMin()); year <= Math.min(decade + 9, periodYearMax()); year++) if (completeYear(year)) years++;
			return Math.max(1, years);
		});
		for (const index of state.active) {
			const row = track(index);
			if (!completeYear(row[T.start_year])) continue;
			const decadeIndex = decades.indexOf(Math.floor(row[T.start_year] / 10) * 10);
			if (decadeIndex >= 0) classMatrix[decadeIndex][row[T.category] - 1]++;
		}
		classMatrix.forEach((row, index) => row.forEach((value, column) => { row[column] = value / exposure[index]; }));
		drawHeatmap('mlaClassChart', decades.map((value, index) => `${String(value).slice(2)}s (${exposure[index]}y)`), ['L', 'D', 'DD', 'CS', 'SCS', 'VS+'], classMatrix, {left: 78, decimals: 1});
		$('#mlaClassData').innerHTML = accessibleTable(['Decade', 'L/y', 'D/y', 'DD/y', 'CS/y', 'SCS/y', 'VS+/y'], decades.map((value, index) => [value, ...classMatrix[index].map(number => fmt(number, 2))]));
		drawGenesisMap();
	}

	function accessibleTable(headers, rows, note) {
		return `${note ? `<p>${esc(note)}</p>` : ''}<div class="mla-table-wrap"><table class="mla-table"><thead><tr>${headers.map(value => `<th>${esc(value)}</th>`).join('')}</tr></thead><tbody>${rows.map(row => `<tr>${row.map(value => `<td>${esc(value)}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
	}

	function seriesValues(index, key) {
		if (!DETAIL) return null;
		const definition = METRICS[key];
		const series = DETAIL.series[index];
		return {
			hours: series[S.hours_since_genesis],
			values: series[S[definition.series]].map(value => value == null ? NaN : Number(value) / definition.divisor)
		};
	}

	function renderLifeCharts() {
		if ($('#mlaPanelExplore').hidden) return;
		const profileButton = $('#mlaLoadProfile');
		if (state.selected == null) {
			emptyChart('mlaLifeChart', 'Select a system to view raw meteorology');
			$('#mlaLifeData').innerHTML = '';
			$('#mlaLifeReadout').textContent = 'Select a system to inspect hourly published-centre physics and position source.';
		} else if (!DETAIL) {
			emptyChart('mlaLifeChart', 'Loading selected-system detail…');
			$('#mlaLifeReadout').textContent = 'Loading hourly published-centre physics…';
			ensureDetail().then(renderLifeCharts).catch(showFatal);
		} else {
			const definition = METRICS[state.evolutionMetric];
			const evolution = drawEvolutionPlot(state.selected, state.evolutionMetric);
			const stride = Math.max(1, Math.ceil(evolution.hours.length / 160));
			const rows = evolution.hours.map((hour, index) => ({hour, index})).filter((item, index) => {
				const transition = index && evolution.posterior[index] !== evolution.posterior[index - 1];
				return index % stride === 0 || transition || index === evolution.hours.length - 1;
			}).map(item => [
				item.hour,
				evolution.posterior[item.index] ? 'Interpolated position' : 'Observed-support position',
				fmt(evolution.lineValues[item.index], 2),
				fmt(evolution.rainValues[item.index], 2)
			]);
			$('#mlaLifeData').innerHTML = accessibleTable(['Hours since genesis', 'Position source', `${definition.title} (${definition.unit})`, '24 h rain (mm)'], rows, 'Physics is resampled at each published v5.4 centre; lines break across structural track discontinuities.');
		}
		if (!DETAIL) {
			profileButton.hidden = false;
			emptyChart('mlaProfileChart', 'Load the detailed cohort profile when needed');
			$('#mlaProfileData').innerHTML = '';
			return;
		}
		profileButton.hidden = true;
		const profile = cohortProfile(state.active, state.metric, 'life');
		drawLinePlot('mlaProfileChart', [{name: `${metric().title} median`, colour: metric().colour, points: profile.points}], {zero: state.metric !== 'mslp' && state.metric !== 'vort', xMin: 0, xMax: 100, xFormat: value => `${fmt(value)}%`, yFormat: value => fmt(value, 1)});
		$('#mlaProfileData').innerHTML = accessibleTable(['Life fraction', 'Median', 'Q1', 'Q3', 'Systems'], profile.points.map(point => [`${fmt(point.x)}%`, fmt(point.y, 2), fmt(point.low, 2), fmt(point.high, 2), point.n]));
	}

	function cohortProfile(indexes, metricKey, alignment) {
		const cacheKey = `${indexes.join(',')}|${metricKey}|${alignment}`;
		if (profileCache.has(cacheKey)) return profileCache.get(cacheKey);
		const bins = alignment === 'peak' ? Array.from({length: 25}, (unused, index) => -72 + index * 6) : Array.from({length: 25}, (unused, index) => index * 100 / 24);
		const values = bins.map(() => []);
		for (const index of indexes) {
			const series = seriesValues(index, metricKey);
			if (!series || !series.hours.length) continue;
			const perBin = bins.map(() => []);
			let peakHour = 0;
			if (alignment === 'peak') {
				const definition = METRICS[metricKey];
				let best = definition.direction < 0 ? Infinity : -Infinity;
				series.values.forEach((value, pointIndex) => {
					if (!Number.isFinite(value)) return;
					if ((definition.direction < 0 && value < best) || (definition.direction >= 0 && value > best)) { best = value; peakHour = series.hours[pointIndex]; }
				});
			}
			series.hours.forEach((hour, pointIndex) => {
				const value = series.values[pointIndex];
				if (!Number.isFinite(value)) return;
				let bin;
				if (alignment === 'peak') bin = Math.round((hour - peakHour + 72) / 6);
				else bin = Math.round(hour / Math.max(1, series.hours[series.hours.length - 1]) * 24);
				if (bin >= 0 && bin < perBin.length) perBin[bin].push(value);
			});
			perBin.forEach((items, bin) => { if (items.length) values[bin].push(items.reduce((sum, value) => sum + value, 0) / items.length); });
		}
		const points = bins.map((x, index) => ({x, y: median(values[index]), low: quantile(values[index], .25), high: quantile(values[index], .75), n: values[index].length}));
		const result = {points, bins};
		profileCache.set(cacheKey, result);
		return result;
	}

	function cohortDescription() {
		const months = [...state.months].sort((a, b) => a - b).map(value => MONTHS[value - 1]).join('/');
		const period = state.timeMode === 'dates' ? `${state.dateMin} to ${state.dateMax}` : `${state.yearMin}–${state.yearMax}`;
		return `${period}; ${months}; ${state.monthMode}; ${state.metricMin ? `P${state.metricMin}+ ${metric().title}` : 'all intensities'}; ${fmt(state.active.length)} systems`;
	}

	function pinCurrentA() {
		pinnedA = {ids: state.active.slice(), description: cohortDescription(), signature: filterSignature()};
		toast(`Pinned ${fmt(pinnedA.ids.length)} systems as cohort A`);
		if (state.tab !== 'compare') activateTab('compare', true);
		else renderCompare();
	}

	function cohortStats(indexes, metricKey) {
		return {
			count: indexes.length,
			duration: median(indexes.map(index => track(index)[T.duration_hours])),
			metric: median(indexes.map(index => rawMetric(index, metricKey))),
			coverage: median(indexes.map(index => CORE.qc[index][2]))
		};
	}

	function renderCompareStats(first, second) {
		const definition = METRICS[state.compareMetric];
		const rows = [
			['Systems', fmt(first.count), fmt(second.count), second.count - first.count],
			['Median duration', durationText(first.duration), durationText(second.duration), second.duration - first.duration],
			[`Median ${definition.title}`, `${fmt(first.metric, 1)} ${definition.unit}`, `${fmt(second.metric, 1)} ${definition.unit}`, second.metric - first.metric],
			['Median observed support', `${fmt(first.coverage, 1)}%`, `${fmt(second.coverage, 1)}%`, second.coverage - first.coverage]
		];
		$('#mlaCompareStats').innerHTML = rows.map(row => `<section class="mla-card mla-stat"><span>${esc(row[0])}</span><strong>A ${esc(row[1])}</strong><small>B ${esc(row[2])}${Number.isFinite(row[3]) ? ` · Δ ${row[3] > 0 ? '+' : ''}${fmt(row[3], 1)}` : ''}</small></section>`).join('');
	}

	async function renderCompare() {
		if ($('#mlaPanelCompare').hidden) return;
		$('#mlaCohortA').innerHTML = `<h3>Cohort A</h3><p>${pinnedA ? esc(pinnedA.description) : 'Not pinned yet.'}</p>`;
		$('#mlaCohortB').innerHTML = `<h3>Cohort B</h3><p>${esc(cohortDescription())}</p>`;
		if (!pinnedA) {
			$('#mlaCompareStats').innerHTML = '';
			emptyChart('mlaCompareChart', 'Pin the current filters as cohort A, then change filters to create cohort B');
			$('#mlaCompareData').innerHTML = '';
			return;
		}
		await ensureDetail('Opening detailed series for cohort comparison…');
		const definition = METRICS[state.compareMetric];
		const first = cohortStats(pinnedA.ids, state.compareMetric);
		const second = cohortStats(state.active, state.compareMetric);
		renderCompareStats(first, second);
		const profileA = cohortProfile(pinnedA.ids, state.compareMetric, state.compareAlign);
		const profileB = cohortProfile(state.active, state.compareMetric, state.compareAlign);
		const peak = state.compareAlign === 'peak';
		drawLinePlot('mlaCompareChart', [
			{name: 'Cohort A median', colour: css('--mla-indigo', '#233f78'), points: profileA.points},
			{name: 'Cohort B median', colour: css('--mla-madder', '#aa3d2d'), points: profileB.points}
		], {zero: definition.direction > 0 && state.compareMetric !== 'vort', xMin: peak ? -72 : 0, xMax: peak ? 72 : 100, xFormat: value => peak ? `${value > 0 ? '+' : ''}${fmt(value)}h` : `${fmt(value)}%`, yFormat: value => fmt(value, 1)});
		$('#mlaCompareData').innerHTML = accessibleTable(['Alignment', 'A median', 'A Q1–Q3', 'A n', 'B median', 'B Q1–Q3', 'B n'], profileA.points.map((point, index) => [peak ? `${point.x > 0 ? '+' : ''}${point.x} h` : `${fmt(point.x)}%`, fmt(point.y, 2), `${fmt(point.low, 2)}–${fmt(point.high, 2)}`, point.n, fmt(profileB.points[index].y, 2), `${fmt(profileB.points[index].low, 2)}–${fmt(profileB.points[index].high, 2)}`, profileB.points[index].n]));
	}

	const EXTREMES = {
		duration: {label: 'Duration', unit: 'h', value: index => track(index)[T.duration_hours], descending: true, continuity: true},
		distance: {label: 'Linked path length', unit: 'km', value: index => track(index)[T.distance_km], descending: true, continuity: true},
		deficit: {label: 'Pressure deficit', unit: 'hPa', value: index => track(index)[T.peak_deficit_x10] / 10, descending: true},
		wind: {label: 'Maximum wind', unit: 'm s⁻¹', value: index => track(index)[T.peak_wind_x10] / 10, descending: true},
		rain: {label: '24 h precipitation', unit: 'mm', value: index => track(index)[T.peak_precip_x10] / 10, descending: true},
		vort: {label: 'Smoothed vorticity', unit: 'catalogue units', value: index => track(index)[T.peak_vort_x10] / 10, descending: true},
		mslp: {label: 'Minimum MSLP', unit: 'hPa', value: index => track(index)[T.min_mslp_x10] / 10, descending: false}
	};

	function eligibleExtreme(index, definition) {
		if (state.extremeEligibility === 'all') return true;
		if (state.extremeEligibility === 'good') return CORE.qc[index][4] === 0;
		return definition.continuity ? CORE.qc[index][4] === 0 : true;
	}

	function renderExtremes() {
		if ($('#mlaPanelExtremes').hidden) return;
		const definition = EXTREMES[state.extremeMetric];
		const indexes = state.active.filter(index => eligibleExtreme(index, definition)).sort((first, second) => definition.descending ? definition.value(second) - definition.value(first) : definition.value(first) - definition.value(second));
		$('#mlaExtremeCaveat').textContent = definition.continuity && state.extremeEligibility === 'recommended'
			? 'Recommended: strong observed support'
			: 'Intensity diagnostics retain continuity context; they are not externally validated records';
		$('#mlaRecordCards').innerHTML = indexes.slice(0, 3).map((index, rank) => {
			const value = definition.value(index);
			return `<article class="mla-card mla-record"><span class="mla-label">${rank + 1} · ${esc(definition.label)}</span><h3><button class="mla-row-button" type="button" data-select-track="${index}" data-open-explore="true">${esc(systemLabel(index))}</button></h3><p><strong>${fmt(value, 1)} ${esc(definition.unit)}</strong> · ${date(track(index)[T.start_ms])}</p><p>${esc(qcExplanation(index))}</p></article>`;
		}).join('') || '<p>No eligible systems in this cohort.</p>';
		const table = $('#mlaExtremeTable');
		table.querySelector('thead').innerHTML = `<tr><th>Rank</th><th>System</th><th>Genesis</th><th>${esc(definition.label)}</th><th>Peak class</th><th>Observed support</th><th>Stitches</th></tr>`;
		table.querySelector('tbody').innerHTML = indexes.slice(0, 50).map((index, rank) => {
			return `<tr><td>${rank + 1}</td><td><button class="mla-row-button" type="button" data-select-track="${index}" data-open-explore="true">${esc(systemLabel(index))}</button></td><td>${date(track(index)[T.start_ms])}</td><td class="mla-num">${fmt(definition.value(index), 1)} ${esc(definition.unit)}</td><td>${esc(CLASS_SHORT[track(index)[T.category]])}</td><td>${badge(QC_LABELS[CORE.qc[index][4]], QC_TONES[CORE.qc[index][4]])}<br><small>${fmt(CORE.qc[index][2])}% observed</small></td><td>${fmt(track(index)[T.stitch_count])}</td></tr>`;
		}).join('') || '<tr><td colspan="7">No eligible systems.</td></tr>';
	}

	function officialGradeCategory(grade) {
		const value = String(grade || '').toUpperCase();
		if (value === 'L' || value === 'LOW') return 1;
		if (value === 'D' || value === 'DEP') return 2;
		if (value === 'DD') return 3;
		if (value === 'CS') return 4;
		if (value === 'SCS') return 5;
		if (['VSCS', 'ESCS', 'SUCS'].includes(value)) return 6;
		return 0;
	}

	function renderVerification() {
		if ($('#mlaPanelVerification').hidden) return;
		const items = state.active.map(index => ({index, item: crosswalk(index)}));
		const ib = items.filter(value => value.item && value.item.ib);
		const high = ib.filter(value => value.item.ib.confidence === 'high').length;
		const medium = ib.filter(value => value.item.ib.confidence === 'medium').length;
		const named = ib.filter(value => ['high', 'medium'].includes(value.item.ib.confidence) && officialName(value.index)).length;
		const sidGroups = new Map();
		for (const value of ib) {
			const sid = value.item.ib.sid;
			if (!sidGroups.has(sid)) sidGroups.set(sid, []);
			sidGroups.get(sid).push(value.index);
		}
		const fragmented = [...sidGroups.values()].filter(indexes => indexes.length > 1).length;
		$('#mlaVerificationStats').innerHTML = [
			['High-confidence IBTrACS', high, `${fmt(high / Math.max(1, state.active.length) * 100, 1)}% of cohort`],
			['Medium-confidence', medium, 'Retained with match diagnostics'],
			['Named associations', named, 'High or medium confidence'],
			['Fragmented external events', fragmented, 'More than one atlas segment per SID']
		].map(value => `<section class="mla-card mla-stat"><span>${esc(value[0])}</span><strong>${fmt(value[1])}</strong><small>${esc(value[2])}</small></section>`).join('');

		const separationBins = [0, 50, 100, 150, 200, 300, 500, Infinity];
		const separationCounts = Array(separationBins.length - 1).fill(0);
		for (const value of ib) {
			const distance = value.item.ib.median_km;
			const bin = separationBins.findIndex((limit, index) => index < separationBins.length - 1 && distance >= limit && distance < separationBins[index + 1]);
			if (bin >= 0) separationCounts[bin]++;
		}
		drawBars('mlaSeparationChart', separationCounts.map((value, index) => ({label: `${separationBins[index]}–${Number.isFinite(separationBins[index + 1]) ? separationBins[index + 1] : '500+'}`, value, colour: ramp(1 - index / separationCounts.length)})));

		const matrix = Array.from({length: 6}, () => Array(6).fill(0));
		for (const value of items) {
			const official = officialGradeCategory(officialGrade(value.index));
			if (official) matrix[track(value.index)[T.category] - 1][official - 1]++;
		}
		drawHeatmap('mlaGradeChart', ['Atlas L', 'Atlas D', 'Atlas DD', 'Atlas CS', 'Atlas SCS', 'Atlas VS+'], ['Off L', 'D', 'DD', 'CS', 'SCS', 'VS+'], matrix, {left: 78});

		const groups = [...sidGroups.entries()].filter(([, indexes]) => indexes.length > 1).sort((first, second) => second[1].length - first[1].length || first[0].localeCompare(second[0]));
		const table = $('#mlaFragmentTable');
		table.querySelector('thead').innerHTML = '<tr><th>External event</th><th>Atlas segments</th><th>Best confidence</th><th>Median separation range</th></tr>';
		table.querySelector('tbody').innerHTML = groups.slice(0, 60).map(([sid, indexes]) => {
			const best = CORE.ibtracs_tracks[sid];
			const matches = indexes.map(index => crosswalk(index).ib);
			const order = {high: 3, medium: 2, low: 1};
			const confidence = matches.slice().sort((a, b) => order[b.confidence] - order[a.confidence])[0].confidence;
			const distances = matches.map(match => match.median_km);
			return `<tr><td>${esc(best && best.name ? `Cyclone ${best.name}` : sid)}<br><small>${esc(sid)}</small></td><td>${indexes.map(index => `<button class="mla-row-button" type="button" data-select-track="${index}" data-open-explore="true">${index}</button>`).join(', ')}</td><td>${esc(confidence)}</td><td>${fmt(Math.min(...distances))}–${fmt(Math.max(...distances))} km</td></tr>`;
		}).join('') || '<tr><td colspan="4">No fragmented external events in this cohort.</td></tr>';
	}

	function csvCell(value) {
		return `"${String(value == null ? '' : value).replace(/"/g, '""')}"`;
	}

	function downloadBlob(filename, contents, type) {
		const blob = new Blob([contents], {type: type || 'text/plain;charset=utf-8'});
		const anchor = document.createElement('a');
		anchor.href = URL.createObjectURL(blob);
		anchor.download = filename;
		document.body.appendChild(anchor);
		anchor.click();
		anchor.remove();
		setTimeout(() => URL.revokeObjectURL(anchor.href), 1000);
	}

	function summaryRows(indexes) {
		return indexes.map(index => {
			const row = track(index);
			return {
				atlas_track_id: atlasId(index),
				system_label: systemLabel(index),
				start_utc: new Date(row[T.start_ms]).toISOString(),
				end_utc: new Date(row[T.end_ms]).toISOString(),
				duration_hours: row[T.duration_hours],
				atlas_peak_class: CORE.cat_labels[String(row[T.category])],
				peak_vorticity_catalogue_units: row[T.peak_vort_x10] / 10,
				peak_precip_24h_mm: row[T.peak_precip_x10] / 10,
				peak_wind_ms: row[T.peak_wind_x10] / 10,
				peak_pressure_deficit_hpa: row[T.peak_deficit_x10] / 10,
				minimum_mslp_hpa: row[T.min_mslp_x10] / 10,
				distance_km: row[T.distance_km],
				qc_max_gap_hours: CORE.qc[index][0],
				qc_max_step_speed_ms: CORE.qc[index][1],
				qc_observed_hour_coverage_pct: CORE.qc[index][2],
				qc_screen: QC_LABELS[CORE.qc[index][4]],
				observed_positions: row[T.observed_positions],
				qualifying_positions: row[T.qualifying_positions],
				interpolated_fraction: row[T.posterior_fraction_x1000] / 1000,
				track_stitch_count: row[T.stitch_count],
				max_missing_run_hours: row[T.max_missing_run_hours],
				atlas_version: CORE.meta.atlas_version,
				catalogue_version: CORE.meta.catalogue_version
			};
		});
	}

	function downloadSummaries() {
		const rows = summaryRows(state.active);
		if (!rows.length) { toast('No systems to export'); return; }
		const headers = Object.keys(rows[0]);
		const csv = [headers.map(csvCell).join(','), ...rows.map(row => headers.map(header => csvCell(row[header])).join(','))].join('\n');
		downloadBlob('monsoon-low-atlas-filtered-systems.csv', csv, 'text/csv;charset=utf-8');
		toast(`Exported ${fmt(rows.length)} system summaries`);
	}

	function splitPath(index) {
		const points = paths.decoded[index];
		const breaks = new Set((CORE.breaks[index] || []).map(item => Number(item[0])));
		const lines = [];
		let line = [];
		points.forEach((point, pointIndex) => {
			if (breaks.has(pointIndex) && line.length) { lines.push(line); line = []; }
			line.push([point[1], point[0]]);
		});
		if (line.length) lines.push(line);
		return lines.filter(value => value.length >= 2);
	}

	function downloadGeojson() {
		if (!state.active.length) { toast('No systems to export'); return; }
		const features = state.active.map(index => {
			const summary = summaryRows([index])[0];
			return {
				type: 'Feature',
				id: atlasId(index),
				properties: summary,
				geometry: {type: 'MultiLineString', coordinates: splitPath(index)}
			};
		});
		downloadBlob('monsoon-low-atlas-filtered-tracks.geojson', JSON.stringify({type: 'FeatureCollection', features}, null, 2), 'application/geo+json');
		toast(`Exported ${fmt(features.length)} continuity-aware tracks`);
	}

	function reproducibilityState() {
		return {
			generated_utc: new Date().toISOString(),
			atlas_version: CORE.meta.atlas_version,
			catalogue: CORE.meta.title,
			catalogue_coverage: {start: CORE.meta.coverage_start, end: CORE.meta.coverage_end},
			source_sha256: CORE.meta.core_catalogue_sha256,
			filters: {
				genesis_time_mode: state.timeMode,
				year_min: state.timeMode === 'years' ? state.yearMin : null,
				year_max: state.timeMode === 'years' ? state.yearMax : null,
				date_min: state.timeMode === 'dates' ? state.dateMin : null,
				date_max: state.timeMode === 'dates' ? state.dateMax : null,
				months: [...state.months].sort((a, b) => a - b),
				month_definition: state.monthMode,
				atlas_peak_classes: [...state.classes].sort((a, b) => a - b),
				metric: state.metric,
				minimum_fixed_catalogue_percentile: state.metricMin,
				continuity_screen: state.qc,
				track_crosses_state: state.stateIndex < 0 ? null : CORE.state_slugs[state.stateIndex],
				search: state.search || null
			},
			view: {map_layer: state.mapLayer, map_colour: state.mapColour, state_fill: state.stateFill, map_track_period: state.mapPath, map_scope: state.mapScope, evolution_metric: state.evolutionMetric},
			selected_atlas_track_id: state.selected == null ? null : atlasId(state.selected),
			matching_atlas_track_ids: state.active.map(atlasId),
			url: window.location.href,
			caveats: [
				'Atlas-derived IMD-style class is not official IMD grade.',
				'Interpolated positions preserve track continuity; v5.4 physics is resampled at every published centre.',
				'Cyclone names use credible NOAA IBTrACS v04r01 associations; state means use IMD 0.25-degree daily rainfall over active track dates.',
				'Automated continuity flags are review aids rather than definitive quality judgements.'
			]
		};
	}

	function downloadQuery() {
		downloadBlob('monsoon-low-atlas-query.json', JSON.stringify(reproducibilityState(), null, 2), 'application/json');
		toast('Exported reproducibility recipe');
	}

	async function downloadSelectedFixes() {
		if (state.selected == null) { toast('Select a system first'); return; }
		await ensureDetail('Opening selected-track fixes…');
		const index = state.selected;
		const row = track(index);
		const series = DETAIL.series[index];
		const points = paths.decoded[index];
		const interpolated = new Uint8Array(points.length);
		for (const range of CORE.posterior_runs[index] || []) interpolated.fill(1, Number(range[0]), Number(range[1]) + 1);
		const valueAt = (field, pointIndex, divisor) => series[S[field]][pointIndex] == null ? '' : series[S[field]][pointIndex] / (divisor || 1);
		const headers = ['atlas_track_id', 'time_utc', 'hours_since_genesis', 'latitude', 'longitude', 'position_source', 'precip_24h_mm', 'vorticity_catalogue_units', 'max_wind_ms', 'mslp_hpa', 'pressure_deficit_hpa', 'q850_gkg', 'rh850_pct', 't850_k', 'atlas_class'];
		const rows = series[S.hours_since_genesis].map((hour, pointIndex) => [
			atlasId(index),
			new Date(row[T.start_ms] + Number(hour) * 3600000).toISOString(),
			hour,
			points[pointIndex][0],
			points[pointIndex][1],
			interpolated[pointIndex] ? 'interpolated' : 'observed_support',
			valueAt('precip24_x10', pointIndex, 10),
			valueAt('vort_smooth_x10', pointIndex, 10),
			valueAt('max_wind_x10', pointIndex, 10),
			valueAt('mslp_x10', pointIndex, 10),
			valueAt('pressure_deficit_x10', pointIndex, 10),
			valueAt('q850_x10', pointIndex, 10),
			valueAt('rh850_x10', pointIndex, 10),
			valueAt('t850_x10', pointIndex, 10),
			valueAt('category', pointIndex, 1)
		]);
		const csv = [headers.map(csvCell).join(','), ...rows.map(values => values.map(csvCell).join(','))].join('\n');
		downloadBlob(`monsoon-low-atlas-track-${atlasId(index)}-fixes.csv`, csv, 'text/csv;charset=utf-8');
		toast(`Exported ${fmt(rows.length)} linked positions`);
	}

	function renderData() {
		$('#mlaCoverageText').textContent = `${CORE.meta.coverage_start} to ${CORE.meta.coverage_end}; complete through ${COMPLETE_END_YEAR}.`;
		$('#mlaBuildText').textContent = `Atlas ${CORE.meta.atlas_version}, built ${CORE.meta.built_utc}; ${fmt(CORE.meta.tracks)} systems, ${fmt(CORE.meta.rows)} linked positions, ${fmt(CORE.meta.observed_rows)} observed-support and ${fmt(CORE.meta.posterior_rows)} interpolated.`;
		const release = $('#mlaReleaseSummary');
		if (release) release.href = CORE.meta.sources.release_summary;
	}

	function renderExplore() {
		if ($('#mlaPanelExplore').hidden) return;
		renderDossier();
		renderTopTable();
		mapScheduler.invalidate(MAP_DIRTY.ALL);
		renderLifeCharts();
	}

	function renderCurrentPanel() {
		if (!CORE) return;
		if (state.tab === 'explore') renderExplore();
		else if (state.tab === 'systems') renderSystems();
		else if (state.tab === 'climatology') renderClimatology();
		else if (state.tab === 'compare') renderCompare();
		else if (state.tab === 'extremes') renderExtremes();
		else if (state.tab === 'verification') renderVerification();
		else if (state.tab === 'data') renderData();
	}

	function showFatal(error) {
		console.error(error);
		const loading = $('#mlaLoading');
		loading.innerHTML = `<strong>Atlas could not be opened.</strong><span>${esc(error && error.message ? error.message : error)}</span>`;
		loading.style.borderColor = css('--mla-flag', '#a23d34');
		$('#mlaDataStatus').textContent = 'Load failed';
	}

	try {
		setLoading('Decompressing the fast map and summary catalogue…');
		CORE = await loadGzipJson('mla-core-gzip-b64');
		T = Object.fromEntries(CORE.track_fields.map((name, index) => [name, index]));
		S = Object.fromEntries(CORE.series_fields.map((name, index) => [name, index]));
		setLoading('Building a spatial index for responsive track selection…');
		await new Promise(resolve => setTimeout(resolve, 0));
		buildPathRuntime();
		buildFallbackLabels();
		buildSearchIndex();
		buildFilterControls();
		readUrl();
		if (state.stateFill !== 'none') await ensureDetail();
		bindTabs();
		bindControls();
		bindMap();
		syncControls();
		applyFilters({noUrl: true});
		activateTab(state.tab, false);
		renderData();
		$('#mlaLoading').hidden = true;
		$('#mlaDataStatus').textContent = `${fmt(CORE.meta.tracks)} systems ready`;
		$('#mlaDataStatus').dataset.status = 'ready';
		root.dataset.ready = 'true';
		writeUrl('replace');
		window.addEventListener('resize', debounce(renderCurrentPanel, 150));
	} catch (error) {
		showFatal(error);
	}
})();
