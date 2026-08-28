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
	const COMPLETE_END_YEAR = 2025;
	const MAP_DIRTY = Object.freeze({BASE: 1, WEATHER: 2, DATA: 4, OVERLAY: 8, ALL: 15});
	const HOUR_MS = 3600000;
	const CONCURRENT_CENTRE_SEPARATION_KM = 150;
	const FOCUSED_COMPANION_SEPARATION_KM = 750;
	const CANVAS_FONT = '"effra", Effra, Arial, sans-serif';
	const WEATHER_FIELDS = Object.freeze({
		vorticity: {label: '850-hPa vorticity', keyMin: '0', keyMax: '40 × 10⁻⁵ s⁻¹'},
		precipitation: {label: 'trailing 24 h precipitation', keyMin: '0', keyMax: '150 mm'},
		rh500: {label: '500-hPa relative humidity', keyMin: '0', keyMax: '100%'}
	});
	const COMPOSITE_PALETTES = Object.freeze({
		terrain_r: ['#ffffff', '#dfd6d4', '#bfada9', '#9f847e', '#805c54', '#a08566', '#c0ae77', '#e0d788', '#fdff99', '#bdf28c', '#7de57f', '#3dd872', '#00cb6a', '#00abcb', '#0a86ec', '#1f5bc1', '#333399'],
		vorticity: ['#053061', '#175290', '#2a71b2', '#3f8ec0', '#6bacd1', '#9bc9e0', '#c2ddec', '#e0ecf3', '#f7f6f6', '#fbe5d8', '#fbccb4', '#f5aa89', '#e48066', '#d05548', '#ba2832', '#930e26', '#67001f']
	});
	const COMPOSITE_SECTION_DEFINITIONS = Object.freeze({
		relative_vorticity: {label: 'Relative vorticity', unit: '10⁻⁵ s⁻¹', minimum: -20, maximum: 20, palette: 'vorticity'},
		theta_e: {label: 'Equivalent potential temperature (θₑ)', unit: 'K', minimum: 330, maximum: 370, palette: 'vorticity', topPressure: 125}
	});
	const COMPOSITE_TICK_FONT_SIZE = 12;
	const COMPOSITE_LABEL_FONT_SIZE = 13;

	let CORE;
	let CLIMATE;
	let DETAIL;
	let detailPromise;
	let T;
	let S;
	let Q;
	let paths;
	let analogueShapeFeatures;
	let segmentIndex;
	let densityMonthCache = new Map();
	let catalogueBounds;
	let fallbackLabels = [];
	let nearStateCache = new Map();
	let profileCache = new Map();
	let genesisRegions = [];
	let lysisRegions = [];
	let toastTimer;
	let pointerFrame = 0;
	let evolutionFocusFrame = 0;
	let evolutionChartDragging = false;
	let pendingPointer = null;
	let suppressUrl = false;
	let lastAutoFitSignature = '';
	let rainfallMapCache = null;
	let atlasConfig = {};
	let SOI_BOUNDARY = null;
	let weatherVideo = null;
	let weatherMonth = '';
	let weatherField = '';
	let weatherLoadSerial = 0;
	let weatherSyncSerial = 0;
	let weatherError = '';
	let weatherLoading = false;
	let weatherFrameCanvas = null;
	let weatherFrameContext = null;
	let weatherEncodedCanvas = null;
	let weatherEncodedContext = null;
	let compositeLoadSerial = 0;
	const compositeCache = new Map();
	const compositePromises = new Map();
	const compositeErrors = new Map();

	const METRICS = {
		deficit: {label: 'pressure-deficit', title: 'Pressure deficit', pct: 'pct_deficit', raw: 'peak_deficit_x10', series: 'pressure_deficit_x10', divisor: 10, unit: 'hPa', colour: '#aa3d2d', direction: 1, peakMonth: 4},
		vort: {label: 'vorticity', title: 'Smoothed vorticity', pct: 'pct_vort', raw: 'peak_vort_x10', series: 'vort_smooth_x10', divisor: 10, unit: '10⁻⁵ s⁻¹', colour: '#233f78', direction: 1, peakMonth: 1},
		wind: {label: 'circulation-wind', title: 'Circulation wind', pct: 'pct_wind', raw: 'peak_wind_x10', series: 'max_wind_x10', divisor: 10, unit: 'm s⁻¹', colour: '#08736f', direction: 1, peakMonth: 2},
		mslp: {label: 'MSLP-depth', title: 'Minimum MSLP', pct: 'pct_mslp_depth', raw: 'min_mslp_x10', series: 'mslp_x10', divisor: 10, unit: 'hPa', colour: '#64224f', direction: -1, peakMonth: 3},
		rain: {label: 'rainfall', title: '24 h precipitation', pct: 'pct_precip', raw: 'peak_precip_x10', series: 'precip24_x10', divisor: 10, unit: 'mm', colour: '#c3931d', direction: 1, peakMonth: 0},
		q: {label: 'q850', title: 'q850', raw: 'peak_q850_x10', series: 'q850_x10', divisor: 10, unit: 'g kg⁻¹', colour: '#4360a0', direction: 1},
		rh: {label: 'RH850', title: 'RH850', series: 'rh850_x10', divisor: 10, unit: '%', colour: '#477a4a', direction: 1}
	};
	const FILTER_METRIC_KEYS = ['deficit', 'vort', 'wind', 'mslp', 'rain'];
	const PROFILE_METRIC_KEYS = ['deficit', 'wind', 'vort', 'rain', 'mslp', 'q', 'rh'];
	const DEFAULT_PROFILE_METRICS = ['deficit', 'wind', 'rain'];
	const ENDPOINT_REGION_LABELS = {
		all: 'All locations',
		bob: 'Bay of Bengal',
		arabian: 'Arabian Sea',
		india: 'Indian land',
		land: 'All land',
		indian_ocean: 'Indian Ocean'
	};
	const ENDPOINT_REGION_BITS = Object.freeze({bob: 1, arabian: 2, india: 4, land: 8, indian_ocean: 16});

	const state = {
		tab: 'explore',
		timeMode: 'years',
		yearMin: 1940,
		yearMax: 2025,
		dateMin: '1940-05-17',
		dateMax: '2025-12-31',
		months: new Set([6, 7, 8, 9]),
		monthMode: 'active',
		classes: new Set([1, 2, 3, 4, 5, 6]),
		metric: 'deficit',
		percentileMins: {deficit: 0, vort: 0, wind: 0, mslp: 0, rain: 0},
		match: 'any',
		qc: 'any',
		genesisRegion: 'all',
		lysisRegion: 'all',
		bsiso: 'all',
		enso: 'all',
		stateIndex: -1,
		stateMin: 0,
		search: '',
		mapLayer: 'auto',
		mapColour: 'single',
		stateFill: 'none',
		stateOutlines: true,
		ibtracsOverlay: true,
		weatherLayer: 'none',
		weatherTracks: false,
		mapZoom: 1,
		mapCenterLon: 82,
		mapCenterLat: 20,
		selected: null,
		focusStartMs: null,
		focusEndMs: null,
		focusTimeMs: null,
		focusPointIndex: null,
		focusSource: '',
		hovered: null,
		active: [],
		activeBit: null,
		sort: 'metric-desc',
		extremeMetric: 'duration',
		extremeEligibility: 'all',
		evolutionMetric: 'deficit',
		compositePrecipSource: 'era5',
		compositeSectionVariable: 'relative_vorticity',
		profileMetrics: new Set(DEFAULT_PROFILE_METRICS)
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

	function dateTime(ms) {
		return new Date(ms).toISOString().slice(0, 16).replace('T', ' ') + ' UTC';
	}

	function pointTimeMs(trackIndex, pointIndex) {
		return Number(track(trackIndex)[T.start_ms]) + Number(pointIndex) * HOUR_MS;
	}

	function pointIndexAtTime(trackIndex, timeMs) {
		const pointIndex = Math.round((Number(timeMs) - Number(track(trackIndex)[T.start_ms])) / HOUR_MS);
		return pointIndex >= 0 && pointIndex < paths.decoded[trackIndex].length ? pointIndex : -1;
	}

	function pointIsObserved(trackIndex, pointIndex) {
		return pointIndex >= 0 && !paths.posterior[paths.offsets[trackIndex] + pointIndex];
	}

	function pointRangeAtTime(trackIndex, startMs, endMs) {
		const trackStart = Number(track(trackIndex)[T.start_ms]);
		const first = Math.max(0, Math.ceil((Number(startMs) - trackStart) / HOUR_MS));
		const last = Math.min(paths.decoded[trackIndex].length - 1, Math.floor((Number(endMs) - trackStart) / HOUR_MS));
		return first <= last ? [first, last] : null;
	}

	function centreSeparationKm(first, second) {
		const radians = Math.PI / 180;
		const firstLatitude = first[0] * radians;
		const secondLatitude = second[0] * radians;
		const latitudeDifference = (second[0] - first[0]) * radians;
		const longitudeDifference = (second[1] - first[1]) * radians;
		const haversine = Math.sin(latitudeDifference / 2) ** 2
			+ Math.cos(firstLatitude) * Math.cos(secondLatitude) * Math.sin(longitudeDifference / 2) ** 2;
		return 6371.0088 * 2 * Math.atan2(Math.sqrt(haversine), Math.sqrt(Math.max(0, 1 - haversine)));
	}

	function mapTrackIndexes() {
		if (!CORE || !paths || !Number.isFinite(state.focusTimeMs) || state.focusStartMs !== state.focusEndMs) return state.active;
		const selectedPointFocus = state.focusSource === 'point' && state.selected != null;
		const candidates = [];
		for (const trackIndex of state.active) {
			const pointIndex = pointIndexAtTime(trackIndex, state.focusTimeMs);
			if (pointIndex < 0) continue;
			if (selectedPointFocus && trackIndex !== state.selected && !pointIsObserved(trackIndex, pointIndex)) continue;
			candidates.push({trackIndex, pointIndex, point: paths.decoded[trackIndex][pointIndex]});
		}
		candidates.sort((first, second) => {
			const selectedOrder = Number(second.trackIndex === state.selected) - Number(first.trackIndex === state.selected);
			if (selectedOrder) return selectedOrder;
			const categoryOrder = Number(track(second.trackIndex)[T.category]) - Number(track(first.trackIndex)[T.category]);
			if (categoryOrder) return categoryOrder;
			const vorticityOrder = Number(track(second.trackIndex)[T.peak_vort_x10]) - Number(track(first.trackIndex)[T.peak_vort_x10]);
			if (vorticityOrder) return vorticityOrder;
			return Number(atlasId(first.trackIndex)) - Number(atlasId(second.trackIndex));
		});
		const separationKm = selectedPointFocus ? FOCUSED_COMPANION_SEPARATION_KM : CONCURRENT_CENTRE_SEPARATION_KM;
		const distinct = [];
		for (const candidate of candidates) {
			if (distinct.every(retained => centreSeparationKm(candidate.point, retained.point) >= separationKm)) distinct.push(candidate);
		}
		return distinct.map(candidate => candidate.trackIndex);
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

	function mean(values) {
		const valid = values.filter(Number.isFinite);
		return valid.length ? valid.reduce((sum, value) => sum + value, 0) / valid.length : NaN;
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

	function ensureAtlasConfig() {
		if (!Object.keys(atlasConfig).length) {
			const configNode = document.getElementById('mla-data-config');
			if (!configNode) throw new Error('Missing atlas data configuration');
			atlasConfig = JSON.parse(configNode.textContent);
		}
		return atlasConfig;
	}

	async function decodeJsonBytes(bytes) {
		if (bytes[0] !== 0x1f || bytes[1] !== 0x8b) return JSON.parse(new TextDecoder().decode(bytes));
		if (!('DecompressionStream' in window)) throw new Error('This browser needs DecompressionStream support. Please use a current Chrome, Edge, Firefox or Safari release.');
		const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
		return new Response(stream).json();
	}

	async function fetchJsonAsset(url, label) {
		const response = await fetch(url, {cache: 'force-cache'});
		if (!response.ok) throw new Error(`Could not fetch ${label} (${response.status})`);
		return decodeJsonBytes(new Uint8Array(await response.arrayBuffer()));
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
			ensureAtlasConfig();
			const key = id === 'mla-core-gzip-b64' ? 'core' : id === 'mla-detail-gzip-b64' ? 'detail' : id === 'mla-climate-gzip-b64' ? 'climate' : '';
			if (!key || !atlasConfig[key]) throw new Error(`Missing atlas data URL for ${id}`);
			const response = await fetch(atlasConfig[key], {cache: 'force-cache'});
			if (!response.ok) throw new Error(`Could not fetch ${key} data (${response.status})`);
			bytes = new Uint8Array(await response.arrayBuffer());
		}
		return decodeJsonBytes(bytes);
	}

	async function loadBoundaryView() {
		ensureAtlasConfig();
		if (!atlasConfig.geoCountryEndpoint || !atlasConfig.soiBoundary) return null;
		const override = new URLSearchParams(window.location.search).get('boundary');
		if (override === 'natural-earth') return null;
		let country = override === 'soi' ? 'IN' : '';
		if (!country) {
			const controller = new AbortController();
			const timeout = setTimeout(() => controller.abort(), 3500);
			try {
				const response = await fetch(atlasConfig.geoCountryEndpoint, {cache: 'no-store', referrerPolicy: 'no-referrer', signal: controller.signal});
				if (!response.ok) return null;
				country = String((await response.json()).country || '').toUpperCase();
			} catch (error) {
				console.warn('Country-only boundary lookup unavailable; using Natural Earth.', error);
				return null;
			} finally {
				clearTimeout(timeout);
			}
		}
		if (country !== 'IN') return null;
		try {
			const asset = await fetchJsonAsset(atlasConfig.soiBoundary, 'Survey of India boundary data');
			if (asset.schema !== 'monsoon-low-atlas-soi-boundary-v1') throw new Error('Unsupported Survey of India boundary asset');
			return asset;
		} catch (error) {
			console.warn('Survey of India boundary unavailable; using Natural Earth.', error);
			return null;
		}
	}

	function updateBoundarySourceText() {
		const node = $('#mlaBoundarySource');
		if (!node) return;
		node.textContent = SOI_BOUNDARY
			? 'Survey of India official India outline · Natural Earth elsewhere · IMD daily state means when selected'
			: 'Natural Earth land and borders · IMD daily state means when selected';
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

	async function ensureStormComposite(trackIndex, force) {
		ensureAtlasConfig();
		if (!atlasConfig.compositeBase) throw new Error('Storm-centred composite service is not configured');
		const trackId = atlasId(trackIndex);
		if (force) {
			compositeCache.delete(trackId);
			compositePromises.delete(trackId);
			compositeErrors.delete(trackId);
		}
		if (compositeCache.has(trackId)) return compositeCache.get(trackId);
		if (!compositePromises.has(trackId)) {
			const base = String(atlasConfig.compositeBase).replace(/\/$/, '');
			const promise = fetchJsonAsset(`${base}/tracks/track-${trackId}.json.gz`, `storm-centred composite for event ${trackId}`)
				.then(asset => {
					if (asset.schema !== 'monsoon-low-atlas-storm-composite-v1' || Number(asset.track_id) !== Number(trackId)) throw new Error('Composite asset does not match the selected physical event');
					compositeCache.set(trackId, asset);
					while (compositeCache.size > 30) compositeCache.delete(compositeCache.keys().next().value);
					compositeErrors.delete(trackId);
					return asset;
				})
				.catch(error => {
					compositeErrors.set(trackId, error);
					throw error;
				})
				.finally(() => compositePromises.delete(trackId));
			compositePromises.set(trackId, promise);
		}
		return compositePromises.get(trackId);
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
		const posterior = new Uint8Array(total);
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
			for (const run of (CORE.posterior_runs && CORE.posterior_runs[track]) || []) {
				posterior.fill(1, trackOffset + Number(run[0]), trackOffset + Number(run[1]) + 1);
			}
		});
		offsets[decoded.length] = cursor;
		paths = {decoded, offsets, latitude, longitude, breakBefore, month, posterior};
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
		densityMonthCache.clear();
	}

	function buildAnalogueShapeFeatures() {
		if (analogueShapeFeatures) return analogueShapeFeatures;
		analogueShapeFeatures = new Float32Array(CORE.tracks.length * 18);
		paths.decoded.forEach((points, trackIndex) => {
			const last = Math.max(0, points.length - 1);
			for (let sample = 0; sample < 9; sample += 1) {
				const position = last * sample / 8;
				const lower = Math.floor(position), upper = Math.min(last, Math.ceil(position)), fraction = position - lower;
				const latitude = points[lower][0] + fraction * (points[upper][0] - points[lower][0]);
				const longitude = points[lower][1] + fraction * (points[upper][1] - points[lower][1]);
				analogueShapeFeatures[trackIndex * 18 + sample * 2] = longitude;
				analogueShapeFeatures[trackIndex * 18 + sample * 2 + 1] = latitude;
			}
		});
		return analogueShapeFeatures;
	}

	function closestAnalogues(index, count) {
		const features = buildAnalogueShapeFeatures();
		const source = track(index);
		const distances = [];
		for (let candidate = 0; candidate < CORE.tracks.length; candidate += 1) {
			const comparison = track(candidate);
			if (candidate === index || comparison[T.start_year] === source[T.start_year]) continue;
			let shapeDistance = 0;
			for (let sample = 0; sample < 9; sample += 1) {
				const lonA = features[index * 18 + sample * 2], latA = features[index * 18 + sample * 2 + 1];
				const lonB = features[candidate * 18 + sample * 2], latB = features[candidate * 18 + sample * 2 + 1];
				const dx = (lonA - lonB) * Math.cos((latA + latB) * Math.PI / 360), dy = latA - latB;
				shapeDistance += (dx * dx + dy * dy) / 100;
			}
			const durationDifference = Math.log(Math.max(1, Number(source[T.duration_hours])) / Math.max(1, Number(comparison[T.duration_hours])));
			const pathDifference = Math.log(Math.max(10, Number(source[T.distance_km])) / Math.max(10, Number(comparison[T.distance_km])));
			const intensityDistance = ['pct_deficit', 'pct_vort', 'pct_wind', 'pct_mslp_depth'].reduce((sum, field) => sum + ((Number(source[T[field]]) - Number(comparison[T[field]])) / 30) ** 2, 0) / 4;
			const precipitationDifference = (Number(source[T.pct_precip]) - Number(comparison[T.pct_precip])) / 35;
			const rainDaysDifference = Math.log(Math.max(1, Number(source[T.rain_days])) / Math.max(1, Number(comparison[T.rain_days])));
			const stateRainDifference = Math.log(Math.max(1, Number(source[T.top_state_mean_x10])) / Math.max(1, Number(comparison[T.top_state_mean_x10])));
			const impactRegionPenalty = source[T.top_state_idx] === comparison[T.top_state_idx] ? 0 : 1;
			let distance = shapeDistance / 9
				+ .40 * intensityDistance
				+ .16 * durationDifference ** 2
				+ .16 * pathDifference ** 2
				+ .22 * precipitationDifference ** 2
				+ .10 * rainDaysDifference ** 2
				+ .08 * stateRainDifference ** 2
				+ .06 * impactRegionPenalty;
			distances.push([candidate, Math.sqrt(distance)]);
		}
		distances.sort((first, second) => first[1] - second[1] || Number(atlasId(first[0])) - Number(atlasId(second[0])));
		const analogues = [], years = new Set();
		for (const match of distances) {
			const year = track(match[0])[T.start_year];
			if (years.has(year)) continue;
			years.add(year); analogues.push(match);
			if (analogues.length === count) break;
		}
		return analogues;
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
						if (typeof options.active === 'function' ? !options.active(track) : !options.active[track]) continue;
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
		if (name) return `Cyclone ${name}${item && item.catalogue_part_count > 1 ? ` · catalogue part ${item.catalogue_part_index}/${item.catalogue_part_count}` : ''}`;
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

	function pointOnIndianLand(lon, lat) {
		for (const geometry of CORE.geo.states || []) {
			const bbox = geometry.bbox;
			if (!bbox || lon < bbox[0] || lon > bbox[2] || lat < bbox[1] || lat > bbox[3]) continue;
			if (pointInState(lon, lat, geometry)) return true;
		}
		return false;
	}

	function pointOnAtlasLand(lon, lat) {
		let inside = false;
		for (const ring of CORE.geo.land || []) if (pointInRing(lon, lat, ring)) inside = !inside;
		return inside;
	}

	function classifyEndpoint(lat, lon) {
		const indianLand = pointOnIndianLand(lon, lat);
		const land = indianLand || pointOnAtlasLand(lon, lat);
		const water = !land;
		let mask = 0;
		if (land) mask |= ENDPOINT_REGION_BITS.land;
		if (indianLand) mask |= ENDPOINT_REGION_BITS.india;
		if (water && lat >= -30 && lat <= 30 && lon >= 30 && lon <= 120) mask |= ENDPOINT_REGION_BITS.indian_ocean;
		if (water && lat >= 0 && lat <= 30 && lon >= 77.5 && lon <= 100) mask |= ENDPOINT_REGION_BITS.bob;
		if (water && lat >= 0 && lat <= 30 && lon >= 45 && lon < 77.5) mask |= ENDPOINT_REGION_BITS.arabian;
		return mask;
	}

	function buildEndpointRegions() {
		genesisRegions = CORE.tracks.map((unused, index) => {
			const row = track(index);
			return classifyEndpoint(Number(row[T.gen_lat_x1000]) / 1000, Number(row[T.gen_lon_x1000]) / 1000);
		});
		lysisRegions = CORE.tracks.map((unused, index) => {
			const row = track(index);
			return classifyEndpoint(Number(row[T.end_lat_x1000]) / 1000, Number(row[T.end_lon_x1000]) / 1000);
		});
	}

	function endpointRegionPass(index) {
		const genesisPass = state.genesisRegion === 'all' || Boolean(genesisRegions[index] & ENDPOINT_REGION_BITS[state.genesisRegion]);
		const lysisPass = state.lysisRegion === 'all' || Boolean(lysisRegions[index] & ENDPOINT_REGION_BITS[state.lysisRegion]);
		return genesisPass && lysisPass;
	}

	function endpointRegionLabel(mask) {
		if (mask & ENDPOINT_REGION_BITS.india) return ENDPOINT_REGION_LABELS.india;
		if (mask & ENDPOINT_REGION_BITS.bob) return ENDPOINT_REGION_LABELS.bob;
		if (mask & ENDPOINT_REGION_BITS.arabian) return ENDPOINT_REGION_LABELS.arabian;
		if (mask & ENDPOINT_REGION_BITS.land) return 'Other land';
		if (mask & ENDPOINT_REGION_BITS.indian_ocean) return ENDPOINT_REGION_LABELS.indian_ocean;
		return 'Other water';
	}

	function climatePass(index) {
		if (!CLIMATE) return true;
		if (state.bsiso !== 'all' && CLIMATE.bsiso.phase[index] !== Number(state.bsiso)) return false;
		if (state.enso !== 'all' && CLIMATE.enso.class[index] !== Number(state.enso)) return false;
		return true;
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
		const percentiles = FILTER_METRIC_KEYS.map(key => `${key}:${state.percentileMins[key]}`).join(',');
		return [state.timeMode, state.yearMin, state.yearMax, state.dateMin, state.dateMax, [...state.months].sort((a, b) => a - b).join('.'), state.monthMode, [...state.classes].sort().join('.'), state.metric, percentiles, state.match, state.qc, state.genesisRegion, state.lysisRegion, state.bsiso, state.enso, state.stateIndex, state.stateMin, state.search].join('|');
	}

	function parsedSearch() {
		const query = state.search.trim().toLowerCase();
		const number = Number(query);
		const exactYear = /^\d{4}$/.test(query) && number >= 1940 && number <= 2025 ? number : null;
		const dateMatch = query.match(/^(\d{4}-\d{2}-\d{2})(?:[ t](\d{2})(?::(\d{2}))?(?::\d{2})?z?)?$/);
		const datePart = dateMatch ? dateMatch[1] : '';
		const hourPart = dateMatch && dateMatch[2] != null ? Number(dateMatch[2]) : null;
		const minutePart = dateMatch && dateMatch[3] != null ? Number(dateMatch[3]) : 0;
		const parsedDate = dateMatch ? Date.parse(`${datePart}T${String(hourPart == null ? 0 : hourPart).padStart(2, '0')}:${String(minutePart).padStart(2, '0')}:00Z`) : NaN;
		const validHour = hourPart == null || (hourPart >= 0 && hourPart < 24);
		const validDate = Number.isFinite(parsedDate) && validHour && minutePart >= 0 && minutePart < 60 && new Date(parsedDate).toISOString().slice(0, 10) === datePart;
		const exactDate = validDate ? datePart : null;
		const exactTime = validDate && hourPart != null ? Math.round(parsedDate / HOUR_MS) * HOUR_MS : null;
		const explicitId = query.match(/^(?:id|parent(?:\s+event)?|event)\s*#?\s*(\d+)$/);
		const exactTrackId = explicitId ? Number(explicitId[1]) : (exactYear == null && /^\d+$/.test(query) ? number : null);
		return {
			query,
			exactYear,
			exactDate,
			exactTime,
			exactDateStart: exactTime != null ? exactTime : exactDate ? Date.parse(`${exactDate}T00:00:00Z`) : null,
			exactDateEnd: exactTime != null ? exactTime : exactDate ? Date.parse(`${exactDate}T23:59:59.999Z`) : null,
			exactTrackId
		};
	}

	function applyFilters(options) {
		if (!CORE) return;
		const active = [];
		const bits = new Uint8Array(CORE.tracks.length);
		const search = parsedSearch();
		const {query, exactYear, exactDateStart, exactDateEnd, exactTrackId} = search;
		const minimumActive = state.timeMode === 'dates' ? Date.parse(`${state.dateMin}T00:00:00Z`) : NaN;
		const maximumActive = state.timeMode === 'dates' ? Date.parse(`${state.dateMax}T23:59:59.999Z`) : NaN;
		for (let index = 0; index < CORE.tracks.length; index++) {
			const row = track(index);
			if (state.timeMode === 'dates') {
				if (row[T.end_ms] < minimumActive || row[T.start_ms] > maximumActive) continue;
			} else if (row[T.start_year] < state.yearMin || row[T.start_year] > state.yearMax) continue;
			if (exactDateStart == null && !monthPass(index)) continue;
			if (!state.classes.has(row[T.category])) continue;
			if (FILTER_METRIC_KEYS.some(key => percentileMetric(index, key) < state.percentileMins[key])) continue;
			if (!matchPass(index) || !qcPass(index) || !endpointRegionPass(index) || !climatePass(index) || !statePass(index)) continue;
			if (query) {
				if (exactDateStart != null && (row[T.end_ms] < exactDateStart || row[T.start_ms] > exactDateEnd)) continue;
				if (exactDateStart == null && exactYear != null && row[T.start_year] !== exactYear) continue;
				if (exactDateStart == null && exactYear == null && exactTrackId != null && atlasId(index) !== exactTrackId) continue;
				if (exactDateStart == null && exactYear == null && exactTrackId == null && !CORE.search[index].includes(query)) continue;
			}
			bits[index] = 1;
			active.push(index);
		}
		state.active = active;
		state.activeBit = bits;
		if (state.selected != null && !bits[state.selected]) state.selected = null;
		if (search.exactDateStart != null) {
			state.focusStartMs = search.exactDateStart;
			state.focusEndMs = search.exactDateEnd;
			state.focusTimeMs = search.exactTime;
			state.focusPointIndex = state.selected != null && search.exactTime != null ? pointIndexAtTime(state.selected, search.exactTime) : null;
			state.focusSource = 'search';
			if (search.exactTime != null && state.weatherLayer !== 'none') syncWeatherToFocus();
		} else if (state.focusSource === 'search') {
			clearTimeFocus({keepWeather: true});
		}
		rainfallMapCache = null;
		updateFilterReadout();
		updateTimeControls();
		mapScheduler.invalidate((state.stateFill === 'none' ? 0 : MAP_DIRTY.BASE) | MAP_DIRTY.WEATHER | MAP_DIRTY.DATA | MAP_DIRTY.OVERLAY);
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

	function updateFilterReadout() {
		$('#mlaResultCount').textContent = `${fmt(state.active.length)} of ${fmt(CORE.tracks.length)} systems`;
		$$('[data-percentile-filter]').forEach(control => {
			const key = control.dataset.percentileFilter;
			const output = $(`#${control.id}Value`);
			if (output) output.textContent = `${state.percentileMins[key]}%`;
		});
		$('#mlaStateMinValue').textContent = `${state.stateMin}%`;
	}

	function buildFilterControls() {
		$('#mlaMonthChips').innerHTML = MONTHS.map((name, index) => `<button class="mla-chip" type="button" data-month="${index + 1}" aria-pressed="${state.months.has(index + 1)}">${name}</button>`).join('');
		$('#mlaClassChips').innerHTML = [1, 2, 3, 4, 5, 6].map(value => `<button class="mla-chip" type="button" data-class="${value}" aria-pressed="true">${esc(CLASS_SHORT[value])}</button>`).join('');
		$('#mlaProfileMetrics').innerHTML = PROFILE_METRIC_KEYS.map(key => `<button class="mla-chip" type="button" data-profile-metric="${key}" aria-pressed="${state.profileMetrics.has(key)}">${esc(METRICS[key].title)}</button>`).join('');
		const stateSelect = $('#mlaState');
		CORE.states.forEach((name, index) => {
			const option = document.createElement('option');
			option.value = String(index);
			option.textContent = name;
			stateSelect.appendChild(option);
		});
		const matchField = $('#mlaMatch').closest('.mla-field');
		if (matchField) matchField.hidden = !CORE.crosswalk.some(Boolean);
	}

	function syncControls() {
		$('#mlaYearFields').hidden = state.timeMode !== 'years';
		$('#mlaDateFields').hidden = state.timeMode !== 'dates';
		$('#mlaPeriodLabel').textContent = state.timeMode === 'dates' ? 'Track active dates' : 'Genesis years';
		$('#mlaTimeModeYears').setAttribute('aria-pressed', String(state.timeMode === 'years'));
		$('#mlaTimeModeDates').setAttribute('aria-pressed', String(state.timeMode === 'dates'));
		$('#mlaYearMin').value = state.yearMin;
		$('#mlaYearMax').value = state.yearMax;
		$('#mlaDateMin').value = state.dateMin;
		$('#mlaDateMax').value = state.dateMax;
		$('#mlaMonthMode').value = state.monthMode;
		$('#mlaMetric').value = state.metric;
		$$('[data-percentile-filter]').forEach(control => {
			const key = control.dataset.percentileFilter;
			control.value = state.percentileMins[key];
			const output = $(`#${control.id}Value`);
			if (output) output.textContent = `${state.percentileMins[key]}%`;
		});
		$('#mlaMatch').value = state.match;
		$('#mlaQc').value = state.qc;
		$('#mlaGenesisRegion').value = state.genesisRegion;
		$('#mlaLysisRegion').value = state.lysisRegion;
		$('#mlaBsiso').value = state.bsiso;
		$('#mlaEnso').value = state.enso;
		$('#mlaState').value = state.stateIndex < 0 ? '' : String(state.stateIndex);
		$('#mlaStateMin').value = state.stateMin;
		$('#mlaSearch').value = state.search;
		$('#mlaMapLayer').value = state.mapLayer;
		$('#mlaMapColour').value = state.mapColour;
		$('#mlaStateFill').value = state.stateFill;
		$('#mlaWeatherLayer').value = state.weatherLayer;
		$('#mlaStateOutlines').checked = state.stateOutlines;
		$('#mlaIbtracsOverlay').checked = state.ibtracsOverlay;
		$('#mlaWeatherTracks').checked = state.weatherTracks;
		$('#mlaExtremeMetric').value = state.extremeMetric;
		$('#mlaExtremeEligibility').value = state.extremeEligibility;
		$('#mlaEvolutionMetric').value = state.evolutionMetric;
		$$('[data-month]').forEach(button => button.setAttribute('aria-pressed', String(state.months.has(Number(button.dataset.month)))));
		$$('[data-class]').forEach(button => button.setAttribute('aria-pressed', String(state.classes.has(Number(button.dataset.class)))));
		$$('[data-profile-metric]').forEach(button => button.setAttribute('aria-pressed', String(state.profileMetrics.has(button.dataset.profileMetric))));
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

	function toggleProfileMetric(key) {
		if (!PROFILE_METRIC_KEYS.includes(key)) return;
		if (state.profileMetrics.has(key) && state.profileMetrics.size > 1) state.profileMetrics.delete(key);
		else state.profileMetrics.add(key);
		syncControls();
		renderLifeCharts();
		writeUrl('replace');
	}

	function resetFilters() {
		state.timeMode = 'years';
		state.yearMin = 1940;
		state.yearMax = 2025;
		state.dateMin = '1940-05-17';
		state.dateMax = '2025-12-31';
		state.months = new Set([6, 7, 8, 9]);
		state.monthMode = 'active';
		state.classes = new Set([1, 2, 3, 4, 5, 6]);
		state.metric = 'deficit';
		state.percentileMins = {deficit: 0, vort: 0, wind: 0, mslp: 0, rain: 0};
		state.match = 'any';
		state.qc = 'any';
		state.genesisRegion = 'all';
		state.lysisRegion = 'all';
		state.bsiso = 'all';
		state.enso = 'all';
		state.stateIndex = -1;
		state.stateMin = 0;
		state.search = '';
		state.stateFill = 'none';
		state.selected = null;
		state.weatherLayer = 'none';
		clearTimeFocus();
		rainfallMapCache = null;
		profileCache.clear();
		syncControls();
		applyFilters();
	}

	const debouncedFilter = debounce(() => applyFilters(), 90);

	function setTimeMode(mode) {
		if (mode === state.timeMode) return;
		if (mode === 'dates') {
			state.dateMin = state.yearMin === 1940 ? '1940-05-17' : `${state.yearMin}-01-01`;
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
			state.dateMin = event.target.value || '1940-05-17';
			if (state.dateMin > state.dateMax) {
				state.dateMax = state.dateMin;
				$('#mlaDateMax').value = state.dateMax;
			}
			applyFilters();
		});
		$('#mlaDateMax').addEventListener('change', event => {
			state.dateMax = event.target.value || '2025-12-31';
			if (state.dateMax < state.dateMin) {
				state.dateMin = state.dateMax;
				$('#mlaDateMin').value = state.dateMin;
			}
			applyFilters();
		});
		$('#mlaMonthMode').addEventListener('change', event => { state.monthMode = event.target.value; applyFilters(); });
		$('#mlaMetric').addEventListener('change', event => {
			state.metric = event.target.value;
			state.sort = 'metric-desc';
			profileCache.clear();
			syncControls();
			applyFilters();
		});
		$('#mlaPercentileFilters').addEventListener('input', event => {
			const control = event.target.closest('[data-percentile-filter]');
			if (!control) return;
			const key = control.dataset.percentileFilter;
			state.percentileMins[key] = Number(control.value);
			const output = $(`#${control.id}Value`);
			if (output) output.textContent = `${state.percentileMins[key]}%`;
			debouncedFilter();
		});
		$('#mlaMatch').addEventListener('change', event => { state.match = event.target.value; applyFilters(); });
		$('#mlaQc').addEventListener('change', event => { state.qc = event.target.value; applyFilters(); });
		$('#mlaGenesisRegion').addEventListener('change', event => { state.genesisRegion = event.target.value; applyFilters(); });
		$('#mlaLysisRegion').addEventListener('change', event => { state.lysisRegion = event.target.value; applyFilters(); });
		$('#mlaBsiso').addEventListener('change', event => { state.bsiso = event.target.value; applyFilters(); });
		$('#mlaEnso').addEventListener('change', event => { state.enso = event.target.value; applyFilters(); });
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
		$('#mlaProfileMetrics').addEventListener('click', event => {
			const button = event.target.closest('[data-profile-metric]');
			if (button) toggleProfileMetric(button.dataset.profileMetric);
		});
		$('#mlaSeasonPresets').addEventListener('click', event => {
			const button = event.target.closest('[data-season]');
			if (!button) return;
			setMonths(SEASON_MONTHS[button.dataset.season]);
		});
		$('#mlaResetFilters').addEventListener('click', resetFilters);
		$('#mlaMapLayer').addEventListener('change', event => { state.mapLayer = event.target.value; mapScheduler.invalidate(MAP_DIRTY.DATA | MAP_DIRTY.OVERLAY); writeUrl('replace'); });
		$('#mlaMapColour').addEventListener('change', event => { state.mapColour = event.target.value; mapScheduler.invalidate(MAP_DIRTY.DATA); writeUrl('replace'); });
		$('#mlaStateFill').addEventListener('change', async event => {
			state.stateFill = event.target.value;
			rainfallMapCache = null;
			if (state.stateFill !== 'none') await ensureDetail('Opening IMD state rainfall context...');
			mapScheduler.invalidate(MAP_DIRTY.ALL);
			writeUrl('replace');
		});
		$('#mlaWeatherLayer').addEventListener('change', event => {
			state.weatherLayer = event.target.value;
			weatherError = '';
			if (state.weatherLayer !== 'none') state.weatherTracks = false;
			if (state.weatherLayer !== 'none') syncWeatherToFocus();
			else { weatherSyncSerial++; weatherLoadSerial++; weatherLoading = false; }
			updateTimeControls();
			mapScheduler.invalidate(MAP_DIRTY.WEATHER | MAP_DIRTY.DATA | MAP_DIRTY.OVERLAY);
			writeUrl('replace');
		});
		$('#mlaWeatherTracks').addEventListener('change', event => {
			state.weatherTracks = event.target.checked;
			mapScheduler.invalidate(MAP_DIRTY.DATA | MAP_DIRTY.OVERLAY);
			writeUrl('replace');
		});
		$('#mlaStateOutlines').addEventListener('change', event => {
			state.stateOutlines = event.target.checked;
			mapScheduler.invalidate(MAP_DIRTY.BASE);
			writeUrl('replace');
		});
		$('#mlaIbtracsOverlay').addEventListener('change', event => {
			state.ibtracsOverlay = event.target.checked;
			mapScheduler.invalidate(MAP_DIRTY.DATA | MAP_DIRTY.OVERLAY);
			writeUrl('replace');
		});
		$('#mlaRetryWeather').addEventListener('click', () => {
			weatherError = '';
			weatherMonth = '';
			weatherField = '';
			syncWeatherToFocus();
		});
		$('#mlaPreviousHour').addEventListener('click', () => stepTrackHour(-1));
		$('#mlaNextHour').addEventListener('click', () => stepTrackHour(1));
		$('#mlaTrackHour').addEventListener('input', moveTrackHourSlider);
		$('#mlaTrackHour').addEventListener('change', commitTrackHourSlider);
		$('#mlaFitCohort').addEventListener('click', () => fitCohort());
		$('#mlaExtremeMetric').addEventListener('change', event => { state.extremeMetric = event.target.value; renderExtremes(); });
		$('#mlaExtremeEligibility').addEventListener('change', event => { state.extremeEligibility = event.target.value; renderExtremes(); });
		$('#mlaEvolutionMetric').addEventListener('change', event => { state.evolutionMetric = event.target.value; renderLifeCharts(); writeUrl('replace'); });
		$('#mlaCompositePrecipSource').addEventListener('change', event => { state.compositePrecipSource = event.target.value; renderStormComposites(); writeUrl('replace'); });
		$('#mlaCompositeSectionVariable').addEventListener('change', event => { state.compositeSectionVariable = event.target.value; renderStormComposites(); writeUrl('replace'); });
		$('#mlaRetryComposite').addEventListener('click', () => {
			if (state.selected == null) return;
			const trackId = atlasId(state.selected);
			compositeErrors.delete(trackId);
			ensureStormComposite(state.selected, true).then(renderStormComposites).catch(renderStormComposites);
			renderStormComposites();
		});
		$('#mlaLoadProfile').addEventListener('click', () => ensureDetail('Opening detailed subset series…').then(renderLifeCharts).catch(showFatal));
		$('#mlaCopyLink').addEventListener('click', copyViewLink);
		$('#mlaQuickExport').addEventListener('click', downloadSummaries);
		$('#mlaDownloadSummaries').addEventListener('click', downloadSummaries);
		$('#mlaDownloadGeojson').addEventListener('click', downloadGeojson);
		$('#mlaDownloadQuery').addEventListener('click', downloadQuery);
		$('#mlaDownloadFixes').addEventListener('click', downloadSelectedFixes);
		root.addEventListener('click', event => {
			const selector = event.target.closest('[data-select-track]');
			if (selector) selectTrack(Number(selector.dataset.selectTrack), {openExplore: selector.dataset.openExplore === 'true', fit: selector.dataset.keepMap !== 'true'});
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
		FILTER_METRIC_KEYS.forEach(key => {
			if (state.percentileMins[key]) parameters.set(`p${key}`, String(state.percentileMins[key]));
		});
		if (state.match !== 'any') parameters.set('match', state.match);
		if (state.genesisRegion !== 'all') parameters.set('genesis', state.genesisRegion);
		if (state.lysisRegion !== 'all') parameters.set('lysis', state.lysisRegion);
		if (state.bsiso !== 'all') parameters.set('bsiso', state.bsiso);
		if (state.enso !== 'all') parameters.set('enso', state.enso);
		if (state.stateIndex >= 0) parameters.set('over', CORE.state_slugs[state.stateIndex]);
		if (state.search) parameters.set('q', state.search);
		if (state.mapLayer !== 'auto') parameters.set('layer', state.mapLayer);
		if (state.mapColour !== 'single') parameters.set('colour', state.mapColour);
		if (state.stateFill !== 'none') parameters.set('statefill', state.stateFill);
		if (!state.stateOutlines) parameters.set('states', '0');
		if (!state.ibtracsOverlay) parameters.set('ibtrack', '0');
		if (state.weatherLayer !== 'none') parameters.set('weather', state.weatherLayer);
		if (state.weatherTracks) parameters.set('weathertracks', '1');
		if (state.focusSource === 'point' && Number.isFinite(state.focusTimeMs)) parameters.set('time', new Date(state.focusTimeMs).toISOString().slice(0, 13));
		if (state.evolutionMetric !== 'deficit') parameters.set('evolve', state.evolutionMetric);
		if (state.compositePrecipSource !== 'era5') parameters.set('compositeprecip', state.compositePrecipSource);
		if (state.compositeSectionVariable !== 'relative_vorticity') parameters.set('compositesection', state.compositeSectionVariable);
		const profileMetrics = PROFILE_METRIC_KEYS.filter(key => state.profileMetrics.has(key));
		if (profileMetrics.join(',') !== DEFAULT_PROFILE_METRICS.join(',')) parameters.set('profiles', profileMetrics.join(','));
		if (Math.abs(state.mapZoom - 1) > .01) parameters.set('zoom', state.mapZoom.toFixed(2));
		if (Math.abs(state.mapZoom - 1) > .01) parameters.set('centre', `${state.mapCenterLon.toFixed(2)},${state.mapCenterLat.toFixed(2)}`);
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
		const validTabs = new Set(['explore', 'climatology', 'extremes', 'data']);
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
		FILTER_METRIC_KEYS.forEach(key => {
			state.percentileMins[key] = clamp(Number(parameters.get(`p${key}`)) || 0, 0, 100);
		});
		if (parameters.has('pmin') && !parameters.has(`p${state.metric}`)) state.percentileMins[state.metric] = clamp(Number(parameters.get('pmin')) || 0, 0, 100);
		if (['any', 'unmatched', 'high', 'credible', 'named'].includes(parameters.get('match'))) state.match = parameters.get('match');
		state.genesisRegion = Object.hasOwn(ENDPOINT_REGION_LABELS, parameters.get('genesis')) ? parameters.get('genesis') : 'all';
		state.lysisRegion = Object.hasOwn(ENDPOINT_REGION_LABELS, parameters.get('lysis')) ? parameters.get('lysis') : 'all';
		state.bsiso = ['-1', '0', '1', '2', '3', '4', '5', '6', '7', '8'].includes(parameters.get('bsiso')) ? parameters.get('bsiso') : 'all';
		state.enso = ['-1', '0', '1', '2'].includes(parameters.get('enso')) ? parameters.get('enso') : 'all';
		const overIndex = CORE.state_slugs.indexOf(parameters.get('over'));
		if (overIndex >= 0) state.stateIndex = overIndex;
		state.search = parameters.get('q') || '';
		if (['auto', 'none', 'density', 'tracks', 'genesis', 'lysis'].includes(parameters.get('layer'))) state.mapLayer = parameters.get('layer');
		if (['single', 'class', 'metric', 'year'].includes(parameters.get('colour'))) state.mapColour = parameters.get('colour');
		if (['none', 'selected', 'cohort', 'selected_anomaly', 'cohort_anomaly'].includes(parameters.get('statefill'))) state.stateFill = parameters.get('statefill');
		state.stateOutlines = parameters.get('states') !== '0';
		state.ibtracsOverlay = parameters.get('ibtrack') !== '0';
		if (['none', 'vorticity', 'precipitation', 'rh500'].includes(parameters.get('weather'))) state.weatherLayer = parameters.get('weather');
		state.weatherTracks = parameters.get('weathertracks') === '1';
		if (METRICS[parameters.get('evolve')] && parameters.get('evolve') !== 'rain') state.evolutionMetric = parameters.get('evolve');
		if (['era5', 'imerg'].includes(parameters.get('compositeprecip'))) state.compositePrecipSource = parameters.get('compositeprecip');
		if (Object.hasOwn(COMPOSITE_SECTION_DEFINITIONS, parameters.get('compositesection'))) state.compositeSectionVariable = parameters.get('compositesection');
		const profileMetrics = (parameters.get('profiles') || '').split(',').filter(key => PROFILE_METRIC_KEYS.includes(key));
		if (profileMetrics.length) state.profileMetrics = new Set(profileMetrics);
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
		const focusTime = parameters.get('time');
		if (focusTime && /^\d{4}-\d{2}-\d{2}T\d{2}$/.test(focusTime)) {
			const parsed = Date.parse(`${focusTime}:00:00Z`);
			if (Number.isFinite(parsed)) {
				state.focusStartMs = parsed;
				state.focusEndMs = parsed;
				state.focusTimeMs = parsed;
				state.focusPointIndex = state.selected == null ? null : pointIndexAtTime(state.selected, parsed);
				state.focusSource = 'point';
			}
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

	function weatherSettings(field) {
		const configuredBounds = atlasConfig.weatherBounds;
		const bounds = configuredBounds && !Array.isArray(configuredBounds)
			? configuredBounds[field]
			: configuredBounds;
		const configuredBases = atlasConfig.weatherBases || {};
		return {
			base: String(configuredBases[field] || atlasConfig.weatherBase || '').replace(/\/$/, ''),
			fps: Number(atlasConfig.weatherFps) || 6,
			bounds: Array.isArray(bounds) ? bounds.map(Number) : [49.875, -5.875, 109.875, 40.125]
		};
	}

	function weatherMonthForTime(timeMs) {
		const value = new Date(timeMs).toISOString();
		return value.slice(0, 4) + value.slice(5, 7);
	}

	function weatherMonthStart(month) {
		return Date.parse(`${month.slice(0, 4)}-${month.slice(4, 6)}-01T00:00:00Z`);
	}

	function ensureWeatherVideo() {
		if (weatherVideo) return weatherVideo;
		weatherVideo = document.createElement('video');
		weatherVideo.crossOrigin = 'anonymous';
		weatherVideo.muted = true;
		weatherVideo.playsInline = true;
		weatherVideo.preload = 'auto';
		weatherVideo.addEventListener('seeked', () => mapScheduler.invalidate(MAP_DIRTY.WEATHER));
		weatherVideo.addEventListener('error', () => {
			if (!weatherField) return;
			const definition = WEATHER_FIELDS[weatherField];
			weatherError = `${definition ? definition.label : 'Weather'} frame unavailable for this month`;
			updateTimeControls();
			mapScheduler.invalidate(MAP_DIRTY.WEATHER);
		});
		return weatherVideo;
	}

	function waitForVideoEvent(video, eventName, failureMessage, timeoutMs) {
		return new Promise((resolve, reject) => {
			let timer;
			const ready = () => { cleanup(); resolve(video); };
			const failed = () => { cleanup(); reject(new Error(failureMessage)); };
			const timedOut = () => { cleanup(); reject(new Error(`${failureMessage} (timed out)`)); };
			const cleanup = () => {
				clearTimeout(timer);
				video.removeEventListener(eventName, ready);
				video.removeEventListener('error', failed);
			};
			video.addEventListener(eventName, ready, {once: true});
			video.addEventListener('error', failed, {once: true});
			timer = setTimeout(timedOut, timeoutMs);
		});
	}

	function weatherUrl(month, field) {
		const settings = weatherSettings(field);
		const extension = atlasConfig.weatherFormat || 'webm';
		return settings.base ? `${settings.base}/${field}/${month.slice(0, 4)}/${month}.${extension}` : '';
	}

	async function loadWeatherMonth(timeMs) {
		const month = weatherMonthForTime(timeMs);
		const field = state.weatherLayer;
		const video = ensureWeatherVideo();
		if (weatherField === field && weatherMonth === month && video.readyState >= 1) return video;
		const url = weatherUrl(month, field);
		if (!url) throw new Error('The atlas weather-data URL is not configured');
		const serial = ++weatherLoadSerial;
		weatherError = '';
		weatherMonth = month;
		weatherField = field;
		const loading = waitForVideoEvent(video, 'loadedmetadata', `Could not load ${month} ${WEATHER_FIELDS[field].label}`, 20000);
		video.src = url;
		video.load();
		await loading;
		if (serial !== weatherLoadSerial) throw new Error('Superseded weather request');
		return video;
	}

	async function seekWeather(timeMs) {
		const video = await loadWeatherMonth(timeMs);
		const settings = weatherSettings(weatherField);
		const frame = Math.round((timeMs - weatherMonthStart(weatherMonth)) / HOUR_MS);
		const target = Math.max(0, frame / settings.fps + .001 / settings.fps);
		if (Math.abs(video.currentTime - target) < .25 / settings.fps) {
			mapScheduler.invalidate(MAP_DIRTY.WEATHER);
			return video;
		}
		const seeking = waitForVideoEvent(video, 'seeked', `Could not seek ${weatherMonth} ${WEATHER_FIELDS[weatherField].label}`, 15000);
		video.currentTime = target;
		await seeking;
		return video;
	}

	async function syncWeatherToFocus() {
		if (state.weatherLayer === 'none' || !Number.isFinite(state.focusTimeMs)) {
			weatherLoading = false;
			mapScheduler.invalidate(MAP_DIRTY.WEATHER);
			return;
		}
		const syncSerial = ++weatherSyncSerial;
		weatherError = '';
		weatherLoading = true;
		updateTimeControls();
		try {
			await seekWeather(state.focusTimeMs);
			mapScheduler.invalidate(MAP_DIRTY.WEATHER | MAP_DIRTY.OVERLAY);
		} catch (error) {
			if (String(error && error.message).includes('Superseded')) return;
			weatherError = error && error.message ? error.message : String(error);
		} finally {
			if (syncSerial === weatherSyncSerial) weatherLoading = false;
		}
		if (syncSerial === weatherSyncSerial) updateTimeControls();
	}

	const scheduleSliderWeather = debounce(syncWeatherToFocus, 80);

	function moveTrackHourSlider(event) {
		if (state.selected == null) return;
		const pointIndex = clamp(Number(event.target.value) || 0, 0, paths.decoded[state.selected].length - 1);
		setTrackPointFocus(state.selected, pointIndex, {activateWeather: false, noSeek: true, noUrl: true});
		scheduleSliderWeather();
	}

	function commitTrackHourSlider() {
		if (state.selected == null) return;
		renderDossier();
		writeUrl('replace');
	}

	function stepTrackHour(direction) {
		if (state.selected == null) return;
		const current = Number.isInteger(state.focusPointIndex) && state.focusPointIndex >= 0 ? state.focusPointIndex : 0;
		setTrackPointFocus(state.selected, clamp(current + direction, 0, paths.decoded[state.selected].length - 1), {activateWeather: false});
	}

	function setTrackPointFocus(trackIndex, pointIndex, options) {
		if (trackIndex == null || !Number.isInteger(pointIndex)) return;
		pointIndex = clamp(pointIndex, 0, paths.decoded[trackIndex].length - 1);
		const timeMs = pointTimeMs(trackIndex, pointIndex);
		state.focusStartMs = timeMs;
		state.focusEndMs = timeMs;
		state.focusTimeMs = timeMs;
		state.focusPointIndex = pointIndex;
		state.focusSource = 'point';
		if ((!options || options.activateWeather !== false) && state.weatherLayer === 'none') { state.weatherLayer = 'vorticity'; state.weatherTracks = false; }
		$('#mlaWeatherLayer').value = state.weatherLayer;
		updateTimeControls();
		scheduleEvolutionFocusDraw();
		if (!(options && options.noUrl)) renderDossier();
		mapScheduler.invalidate(MAP_DIRTY.WEATHER | MAP_DIRTY.DATA | MAP_DIRTY.OVERLAY);
		if (!(options && options.noSeek)) syncWeatherToFocus();
		if (!(options && options.noUrl)) writeUrl('push');
	}

	function clearTimeFocus(options) {
		state.focusStartMs = null;
		state.focusEndMs = null;
		state.focusTimeMs = null;
		state.focusPointIndex = null;
		state.focusSource = '';
		weatherError = '';
		weatherLoading = false;
		weatherSyncSerial++;
		weatherLoadSerial++;
		if (!(options && options.keepWeather)) state.weatherLayer = 'none';
		updateTimeControls();
		scheduleEvolutionFocusDraw();
		if (CORE) mapScheduler.invalidate(MAP_DIRTY.WEATHER | MAP_DIRTY.DATA | MAP_DIRTY.OVERLAY);
	}

	function updateTimeControls() {
		const controls = $('#mlaTimeControls');
		if (!controls) return;
		controls.hidden = state.selected == null && !Number.isFinite(state.focusStartMs) && state.weatherLayer === 'none';
		const weatherDefinition = WEATHER_FIELDS[state.weatherLayer];
		const weatherKey = $('#mlaWeatherKey');
		const weatherTracksControl = $('#mlaWeatherTracksControl');
		weatherKey.hidden = !weatherDefinition;
		weatherTracksControl.hidden = !weatherDefinition;
		$('#mlaWeatherTracks').checked = state.weatherTracks;
		$('#mlaRetryWeather').hidden = !weatherError;
		if (weatherDefinition) {
			$('#mlaWeatherKeyMin').textContent = weatherDefinition.keyMin;
			$('#mlaWeatherKeyMax').textContent = weatherDefinition.keyMax;
			$('#mlaWeatherRamp').dataset.field = state.weatherLayer;
		}
		$('#mlaPreviousHour').disabled = state.selected == null;
		$('#mlaNextHour').disabled = state.selected == null;
		const slider = $('#mlaTrackHour');
		slider.disabled = state.selected == null;
		if (state.selected != null) {
			const lastPoint = paths.decoded[state.selected].length - 1;
			const pointIndex = Number.isInteger(state.focusPointIndex) && state.focusPointIndex >= 0 ? state.focusPointIndex : 0;
			slider.max = String(lastPoint);
			slider.value = String(clamp(pointIndex, 0, lastPoint));
			slider.setAttribute('aria-valuetext', Number.isFinite(state.focusTimeMs) ? dateTime(state.focusTimeMs) : 'Move to choose a track hour');
		} else {
			slider.max = '0';
			slider.value = '0';
			slider.setAttribute('aria-valuetext', 'No selected track');
		}
		let message = '';
		if (weatherError) message = `${weatherError} · ${Number.isFinite(state.focusTimeMs) ? dateTime(state.focusTimeMs) : ''}`;
		else if (weatherLoading && weatherDefinition) message = `Loading ${weatherDefinition.label}${Number.isFinite(state.focusTimeMs) ? ` · ${dateTime(state.focusTimeMs)}` : ''}…`;
		else if (Number.isFinite(state.focusTimeMs) && state.focusSource === 'point' && state.selected != null) message = `${dateTime(state.focusTimeMs)} · ${mapTrackIndexes().length} centres shown · selected plus observation-supported companions`;
		else if (Number.isFinite(state.focusTimeMs)) message = `${dateTime(state.focusTimeMs)} · ${mapTrackIndexes().length} systems active`;
		else if (Number.isFinite(state.focusStartMs)) message = `${date(state.focusStartMs)} UTC · ${state.active.filter(index => pointRangeAtTime(index, state.focusStartMs, state.focusEndMs)).length} systems active · daily positions highlighted`;
		else if (state.selected != null) message = 'Move the track-hour slider or click the selected track again to choose an hour.';
		else if (weatherDefinition) message = 'Select a track, then choose an hour for the weather field.';
		$('#mlaFocusTime').textContent = message;
	}

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
		if (mask & MAP_DIRTY.WEATHER) drawMapWeather();
		if (mask & MAP_DIRTY.DATA) drawMapData();
		if (mask & MAP_DIRTY.OVERLAY) drawMapOverlay();
	});

	function mapBounds() {
		return catalogueBounds || {lonMin: 47, lonMax: 118, latMin: -6, latMax: 48};
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

	function rainfallAnomalyColour(value) {
		const stops = ['#b2182b', '#f7f7f7', '#2166ac'];
		const scaled = clamp(value, -1, 1) + 1;
		const lower = Math.floor(scaled);
		const upper = Math.min(2, Math.ceil(scaled));
		const mix = scaled - lower;
		const parse = colour => [1, 3, 5].map(index => parseInt(colour.slice(index, index + 2), 16));
		const first = parse(stops[lower]);
		const second = parse(stops[upper]);
		return `rgb(${first.map((channel, index) => Math.round(channel + (second[index] - channel) * mix)).join(',')})`;
	}

	function stateRainfallSummary() {
		if (state.stateFill === 'none' || !DETAIL || !DETAIL.state_mean_x10) return null;
		const selectedMode = state.stateFill.startsWith('selected');
		const anomaly = state.stateFill.endsWith('_anomaly');
		const indexes = selectedMode
			? (state.selected == null ? [] : [state.selected])
			: state.active;
		if (!indexes.length) return null;
		const key = selectedMode ? `${state.stateFill}:${state.selected}` : `${state.stateFill}:${filterSignature()}`;
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
		const means = Array.from(totals, (total, index) => weights[index] ? total / weights[index] / 10 : NaN);
		const climatology = DETAIL.state_rainfall && DETAIL.state_rainfall.jjas_climatology_x10;
		const values = anomaly
			? means.map((value, index) => Number.isFinite(value) && climatology && Number(climatology[index]) > 0 ? value / (Number(climatology[index]) / 10) - 1 : NaN)
			: means;
		const maximum = anomaly ? 1 : niceRainfallMaximum(Math.max(...values.filter(Number.isFinite)));
		rainfallMapCache = {key, values, maximum, tracks: indexes.length, systemDays, mode: state.stateFill, anomaly, climatologyPeriod: DETAIL.state_rainfall && DETAIL.state_rainfall.jjas_climatology_period};
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
			.map(item => [item.name, summary.anomaly ? `${item.value >= 0 ? '+' : ''}${fmt(item.value, 2)}` : fmt(item.value, 1)]);
		const selection = summary.mode.startsWith('selected') ? systemLabel(state.selected) : `${fmt(summary.tracks)} filtered systems`;
		const period = summary.climatologyPeriod ? `${summary.climatologyPeriod[0]}–${summary.climatologyPeriod[1]}` : 'all-record';
		const description = summary.anomaly
			? `IMD fractional anomaly relative to the ${period} JJAS daily state mean.`
			: 'IMD area-mean daily rainfall.';
		const heading = summary.anomaly ? 'Fractional anomaly' : 'Mean rainfall (mm day⁻¹)';
		$('#mlaStateRainfallData').innerHTML = `<p>${esc(selection)} · ${fmt(summary.systemDays)} system-days · ${esc(description)}</p>${accessibleTable(['State / UT', heading], rows)}`;
	}

	function drawMapGeography(context, projection, width, height, options) {
		context.fillStyle = css('--mla-sea', '#e7eee7');
		context.fillRect(0, 0, width, height);
		context.save();
		context.strokeStyle = 'rgba(67, 76, 64, .18)';
		context.fillStyle = 'rgba(67, 76, 64, .66)';
		context.lineWidth = 1;
		context.font = `11px ${CANVAS_FONT}`;
		const view = projection.viewBounds;
		const lonStart = Math.ceil(view.lonMin / 10) * 10;
		const latStart = Math.ceil(view.latMin / 5) * 5;
		const mapLabels = [];
		for (let longitude = lonStart; longitude <= view.lonMax; longitude += 10) {
			const first = projection.project(view.latMin, longitude);
			const second = projection.project(view.latMax, longitude);
			context.beginPath(); context.moveTo(first[0], first[1]); context.lineTo(second[0], second[1]); context.stroke();
			if (second[0] > 0 && second[0] < width - 24) mapLabels.push([`${longitude}°E`, second[0] + 3, 14]);
		}
		for (let latitude = latStart; latitude <= view.latMax; latitude += 5) {
			const first = projection.project(latitude, view.lonMin);
			const second = projection.project(latitude, view.lonMax);
			context.beginPath(); context.moveTo(first[0], first[1]); context.lineTo(second[0], second[1]); context.stroke();
			if (first[1] > 16 && first[1] < height - 8) mapLabels.push([`${latitude}°N`, 4, first[1] - 3]);
		}
		context.beginPath();
		drawRingPath(context, projection, CORE.geo.land);
		context.fillStyle = css('--mla-land', '#f3e6c8');
		context.fill('evenodd');
		context.strokeStyle = 'rgba(66, 54, 40, .30)';
		context.lineWidth = .8;
		context.stroke();
		if (SOI_BOUNDARY) {
			context.beginPath();
			drawRingPath(context, projection, SOI_BOUNDARY.rings);
			context.fillStyle = css('--mla-land', '#f3e6c8');
			context.fill('evenodd');
		}
		const borders = SOI_BOUNDARY ? SOI_BOUNDARY.borders_elsewhere : CORE.geo.borders;
		for (const border of borders || []) {
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
				context.fillStyle = rainfall.anomaly ? rainfallAnomalyColour(rainfall.values[index]) : rainfallColour(rainfall.values[index] / rainfall.maximum);
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
			if (state.stateOutlines) context.stroke();
		});
		if (SOI_BOUNDARY) {
			context.beginPath();
			drawRingPath(context, projection, SOI_BOUNDARY.rings);
			context.setLineDash([]);
			context.strokeStyle = 'rgba(66, 54, 40, .68)';
			context.lineWidth = 1.15;
			context.stroke();
		}
		if (state.stateOutlines && options && options.labels && state.mapZoom >= 1.6) {
			context.font = `11px ${CANVAS_FONT}`;
			context.fillStyle = 'rgba(23, 41, 79, .72)';
			for (const geometry of CORE.geo.states) {
				if (!geometry.anchor) continue;
				const point = projection.project(geometry.anchor[1], geometry.anchor[0]);
				if (point[0] > 12 && point[0] < width - 12 && point[1] > 12 && point[1] < height - 12) context.fillText(geometry.name.replace(' & ', '/'), point[0] + 2, point[1] - 2);
			}
		}
		context.font = `11px ${CANVAS_FONT}`;
		context.fillStyle = 'rgba(67, 76, 64, .76)';
		for (const [label, x, y] of mapLabels) context.fillText(label, x, y);
		context.restore();
	}

	function drawMapReferenceLines(context, projection) {
		context.save();
		context.beginPath();
		drawRingPath(context, projection, CORE.geo.land);
		context.strokeStyle = 'rgba(66, 54, 40, .44)';
		context.lineWidth = .9;
		context.stroke();
		const borders = SOI_BOUNDARY ? SOI_BOUNDARY.borders_elsewhere : CORE.geo.borders;
		for (const border of borders || []) {
			if (!border.p || border.p.length < 2) continue;
			context.beginPath();
			border.p.forEach((point, index) => {
				const projected = projection.project(point[1], point[0]);
				if (!index) context.moveTo(projected[0], projected[1]); else context.lineTo(projected[0], projected[1]);
			});
			context.setLineDash(border.c === 1 ? [4, 3] : []);
			context.strokeStyle = 'rgba(66, 54, 40, .38)';
			context.lineWidth = .7;
			context.stroke();
		}
		context.setLineDash([]);
		if (state.stateOutlines) {
			for (const geometry of CORE.geo.states) {
				context.beginPath();
				drawRingPath(context, projection, geometry.rings);
				context.strokeStyle = 'rgba(35, 63, 120, .42)';
				context.lineWidth = .6;
				context.stroke();
			}
		}
		if (SOI_BOUNDARY) {
			context.beginPath();
			drawRingPath(context, projection, SOI_BOUNDARY.rings);
			context.setLineDash([]);
			context.strokeStyle = 'rgba(66, 54, 40, .68)';
			context.lineWidth = 1.05;
			context.stroke();
		}
		context.restore();
	}

	function drawMapBase() {
		const drawing = setupCanvas('mlaMapBase');
		drawMapGeography(drawing.context, drawing.projection, drawing.width, drawing.height, {labels: true});
	}

	function maskedWeatherFrame() {
		const encodedWidth = weatherVideo.videoWidth;
		const height = weatherVideo.videoHeight;
		const width = Math.floor(encodedWidth / 2);
		if (!width || !height || encodedWidth !== width * 2) return null;
		if (!weatherFrameCanvas) {
			weatherFrameCanvas = document.createElement('canvas');
			weatherFrameContext = weatherFrameCanvas.getContext('2d', {willReadFrequently: true});
			weatherEncodedCanvas = document.createElement('canvas');
			weatherEncodedContext = weatherEncodedCanvas.getContext('2d', {willReadFrequently: true});
		}
		if (weatherFrameCanvas.width !== width || weatherFrameCanvas.height !== height) {
			weatherFrameCanvas.width = width;
			weatherFrameCanvas.height = height;
			weatherEncodedCanvas.width = encodedWidth;
			weatherEncodedCanvas.height = height;
		}
		weatherEncodedContext.clearRect(0, 0, encodedWidth, height);
		weatherEncodedContext.drawImage(weatherVideo, 0, 0, encodedWidth, height);
		const encoded = weatherEncodedContext.getImageData(0, 0, encodedWidth, height);
		const frame = weatherFrameContext.createImageData(width, height);
		for (let y = 0; y < height; y++) {
			for (let x = 0; x < width; x++) {
				const target = (y * width + x) * 4;
				const colour = (y * encodedWidth + x) * 4;
				const mask = (y * encodedWidth + x + width) * 4;
				frame.data[target] = encoded.data[colour];
				frame.data[target + 1] = encoded.data[colour + 1];
				frame.data[target + 2] = encoded.data[colour + 2];
				const opacity = encoded.data[mask];
				frame.data[target + 3] = opacity <= 8 ? 0 : opacity;
			}
		}
		weatherFrameContext.putImageData(frame, 0, 0);
		return weatherFrameCanvas;
	}

	function drawMapWeather() {
		const drawing = setupCanvas('mlaMapWeather');
		if (state.weatherLayer === 'none' || !Number.isFinite(state.focusTimeMs) || !weatherVideo || weatherVideo.readyState < 2 || weatherError) return;
		const settings = weatherSettings(state.weatherLayer);
		const [west, south, east, north] = settings.bounds;
		const topLeft = drawing.projection.project(north, west);
		const bottomRight = drawing.projection.project(south, east);
		drawing.context.save();
		drawing.context.globalAlpha = .88;
		drawing.context.imageSmoothingEnabled = false;
		try {
			const frame = maskedWeatherFrame();
			if (frame) drawing.context.drawImage(frame, topLeft[0], topLeft[1], bottomRight[0] - topLeft[0], bottomRight[1] - topLeft[1]);
		} catch (error) {
			weatherError = 'The browser could not draw this cross-origin weather frame';
			updateTimeControls();
		}
		drawing.context.restore();
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
		const item = crosswalk(index);
		if (!item || !item.ib) return '#8b7b63';
		return item.ib.confidence === 'high' ? '#08736f' : item.ib.confidence === 'medium' ? '#c3931d' : '#aa3d2d';
	}

	function pointVisible(trackIndex, pointIndex) {
		return state.months.has(paths.month[paths.offsets[trackIndex] + pointIndex]);
	}

	function visiblePointCount(indexes) {
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
		return mapTrackIndexes().length > 650 && state.mapZoom < 2.5 ? 'density' : 'tracks';
	}

	function drawDensity(context, projection, indexes) {
		const cellsData = currentDensityCells();
		const counts = new Uint16Array(cellsData.columns * cellsData.rows);
		let maximum = 0;
		for (const trackIndex of indexes) {
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

	function drawTrackLayer(context, projection, indexes) {
		const groups = new Map();
		for (const index of indexes) {
			if (!boundsIntersect(CORE.bounds[index], projection.viewBounds)) continue;
			const colour = trackColour(index);
			if (!groups.has(colour)) groups.set(colour, []);
			groups.get(colour).push(index);
		}
		for (const [colour, groupIndexes] of groups) {
			context.beginPath();
			for (const index of groupIndexes) appendTrackPath(context, projection, index, state.mapZoom < 1.5 ? 3 : state.mapZoom < 3 ? 2 : 1, false);
			context.strokeStyle = rgba(colour, indexes.length > 1000 ? .34 : .58);
			context.lineWidth = state.mapZoom > 3 ? 1.5 : 1;
			context.lineCap = 'round';
			context.lineJoin = 'round';
			context.stroke();
		}
	}

	function drawPointLayer(context, projection, mode, indexes) {
		const radius = indexes.length > 1000 ? 1.6 : 2.4;
		for (const index of indexes) {
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
		if (layer === 'none') {
			trackLegend = '';
		} else if (layer === 'density') {
			trackLegend = `<span class="mla-legend-item"><span class="mla-swatch" style="background:${ramp(.2)}"></span>fewer</span><span class="mla-legend-item"><span class="mla-swatch" style="background:${ramp(1)}"></span>up to ${fmt(maximum)} tracks/cell</span>`;
		} else if (state.mapColour === 'class') {
			trackLegend = [1, 2, 3, 4, 5, 6].map(value => `<span class="mla-legend-item"><span class="mla-swatch" style="background:${CLASS_COLOURS[value]}"></span>${CLASS_SHORT[value]}</span>`).join('');
		} else if (state.mapColour === 'single') {
			trackLegend = '';
		} else if (state.mapColour === 'metric') {
			trackLegend = `<span class="mla-legend-item"><span class="mla-swatch" style="background:${ramp(.1)}"></span>lower ${esc(metric().title)} percentile</span><span class="mla-legend-item"><span class="mla-swatch" style="background:${ramp(.55)}"></span>P50</span><span class="mla-legend-item"><span class="mla-swatch" style="background:${ramp(1)}"></span>higher percentile</span>`;
		} else {
			trackLegend = `<span class="mla-legend-item"><span class="mla-swatch" style="background:${ramp(.1)}"></span>earlier genesis year</span><span class="mla-legend-item"><span class="mla-swatch" style="background:${ramp(.55)}"></span>1982</span><span class="mla-legend-item"><span class="mla-swatch" style="background:${ramp(1)}"></span>later genesis year</span>`;
		}
		const rainfall = stateRainfallSummary();
		const rainfallLegend = rainfall && rainfall.anomaly
			? `<span class="mla-legend-item"><span class="mla-swatch" style="background:${rainfallAnomalyColour(-1)}"></span>−1 fractional anomaly</span><span class="mla-legend-item"><span class="mla-swatch" style="background:${rainfallAnomalyColour(0)}"></span>JJAS mean</span><span class="mla-legend-item"><span class="mla-swatch" style="background:${rainfallAnomalyColour(1)}"></span>+1 or wetter</span>`
			: rainfall
				? `<span class="mla-legend-item"><span class="mla-swatch" style="background:${rainfallColour(0)}"></span>state rain 0</span><span class="mla-legend-item"><span class="mla-swatch" style="background:${rainfallColour(1)}"></span>${fmt(rainfall.maximum)} mm/day</span>`
				: state.stateFill.startsWith('selected') ? '<span class="mla-legend-item">Select a system for state rainfall</span>' : '';
		const item = state.selected == null ? null : credibleIb(state.selected);
		const ibtracsLegend = state.ibtracsOverlay && item && CORE.ibtracs_tracks[item.sid] && CORE.ibtracs_tracks[item.sid].path
			? `<span class="mla-legend-item"><span class="mla-swatch" style="height:0;background:none;border-top:2px dashed ${css('--mla-peacock', '#08736f')}"></span>matched IBTrACS best track</span>`
			: '';
		node.innerHTML = rainfallLegend + trackLegend + ibtracsLegend;
	}

	function drawMapData() {
		const drawing = setupCanvas('mlaMapData');
		const layer = effectiveLayer();
		const indexes = mapTrackIndexes();
		const hideSubsetTracks = layer === 'tracks' && state.weatherLayer !== 'none' && !state.weatherTracks;
		let maximum = 0;
		if (layer === 'density') maximum = drawDensity(drawing.context, drawing.projection, indexes);
		else if (layer === 'tracks') { if (!hideSubsetTracks) drawTrackLayer(drawing.context, drawing.projection, indexes); }
		else if (layer !== 'none') drawPointLayer(drawing.context, drawing.projection, layer, indexes);
		const pathLabel = layer === 'none' ? '' : ` · ${fmt(visiblePointCount(indexes))} selected-month positions`;
		const rainfall = stateRainfallSummary();
		const rainfallLabel = rainfall ? ` · IMD state ${rainfall.anomaly ? 'fractional JJAS anomaly' : 'mean'} across ${fmt(rainfall.systemDays)} system-days` : '';
		const layerLabel = hideSubsetTracks ? 'subset tracks hidden while weather is on' : layer === 'density' ? 'unique-track density' : layer === 'none' ? (state.selected == null ? 'no LPS subset layer' : 'selected system only') : layer;
		$('#mlaMapStatus').textContent = `${fmt(indexes.length)} systems · ${layerLabel}${pathLabel}${rainfallLabel} · zoom ${fmt(state.mapZoom, 1)}×`;
		renderStateRainfallValues(rainfall);
		mapLegend(layer, maximum);
	}

	function strokeTrack(context, projection, index, colour, width) {
		context.save();
		context.lineCap = 'round';
		context.lineJoin = 'round';
		context.beginPath();
		appendTrackPath(context, projection, index, 1, false);
		context.setLineDash([]);
		context.strokeStyle = colour;
		context.lineWidth = width;
		context.stroke();
		context.restore();
	}

	function timeFocusTrackIndexes(exact) {
		if (effectiveLayer() === 'none') return state.selected == null ? [] : [state.selected];
		return exact ? mapTrackIndexes() : state.active;
	}

	function drawTimeFocus(context, projection) {
		if (!Number.isFinite(state.focusStartMs) || !Number.isFinite(state.focusEndMs)) return;
		const exact = state.focusStartMs === state.focusEndMs;
		context.save();
		context.lineCap = 'round';
		context.lineJoin = 'round';
		for (const trackIndex of timeFocusTrackIndexes(exact)) {
			const range = pointRangeAtTime(trackIndex, state.focusStartMs, state.focusEndMs);
			if (!range) continue;
			const points = paths.decoded[trackIndex];
			if (!exact && range[1] > range[0]) {
				context.beginPath();
				for (let pointIndex = range[0]; pointIndex <= range[1]; pointIndex++) {
					const point = projection.project(points[pointIndex][0], points[pointIndex][1]);
					if (pointIndex === range[0]) context.moveTo(point[0], point[1]); else context.lineTo(point[0], point[1]);
				}
				context.strokeStyle = css('--mla-card', '#fffaf0');
				context.lineWidth = 6;
				context.stroke();
				context.strokeStyle = css('--mla-saffron', '#c9631b');
				context.lineWidth = 3.2;
				context.stroke();
			}
			const markerIndex = exact ? range[0] : Math.round((range[0] + range[1]) / 2);
			const marker = projection.project(points[markerIndex][0], points[markerIndex][1]);
			const selected = trackIndex === state.selected;
			context.beginPath();
			context.arc(marker[0], marker[1], selected ? 7 : exact ? 4.5 : 3.5, 0, Math.PI * 2);
			context.fillStyle = selected ? css('--mla-lac', '#8f2938') : css('--mla-saffron', '#c9631b');
			context.fill();
			context.strokeStyle = css('--mla-card', '#fffaf0');
			context.lineWidth = 1.8;
			context.stroke();
		}
		context.restore();
	}

	function drawMapOverlay() {
		const drawing = setupCanvas('mlaMapOverlay');
		const selectedOnly = effectiveLayer() === 'none';
		if (!selectedOnly && state.hovered != null && state.hovered !== state.selected && state.activeBit[state.hovered]) strokeTrack(drawing.context, drawing.projection, state.hovered, css('--mla-madder', '#aa3d2d'), 2.5);
		if (state.selected != null) {
			strokeTrack(drawing.context, drawing.projection, state.selected, css('--mla-card', '#fffaf0'), 6.4);
			strokeTrack(drawing.context, drawing.projection, state.selected, css('--mla-indigo-deep', '#17294f'), 3.6);
		}
		const item = state.selected == null ? null : credibleIb(state.selected);
		if (state.ibtracsOverlay && item && CORE.ibtracs_tracks[item.sid] && CORE.ibtracs_tracks[item.sid].path) {
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
		drawTimeFocus(drawing.context, drawing.projection);
		if (state.selected == null) return;
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

	function timeFocusMarkerHitTest(clientX, clientY, touch) {
		if (!Number.isFinite(state.focusStartMs) || !Number.isFinite(state.focusEndMs)) return -1;
		const canvas = $('#mlaMapOverlay');
		const rectangle = canvas.getBoundingClientRect();
		const x = clientX - rectangle.left;
		const y = clientY - rectangle.top;
		const projection = mapProjection(rectangle.width, rectangle.height);
		const exact = state.focusStartMs === state.focusEndMs;
		let bestTrack = -1;
		let bestDistance = (touch ? 24 : 11) ** 2;
		for (const trackIndex of timeFocusTrackIndexes(exact)) {
			const range = pointRangeAtTime(trackIndex, state.focusStartMs, state.focusEndMs);
			if (!range) continue;
			const markerIndex = exact ? range[0] : Math.round((range[0] + range[1]) / 2);
			const marker = projection.project(paths.decoded[trackIndex][markerIndex][0], paths.decoded[trackIndex][markerIndex][1]);
			const distance = (marker[0] - x) ** 2 + (marker[1] - y) ** 2;
			if (distance < bestDistance) { bestDistance = distance; bestTrack = trackIndex; }
		}
		return bestTrack;
	}

	function mapHitTest(clientX, clientY, touch) {
		const focusMarker = timeFocusMarkerHitTest(clientX, clientY, touch);
		if (focusMarker >= 0) return focusMarker;
		const canvas = $('#mlaMapOverlay');
		const rectangle = canvas.getBoundingClientRect();
		const x = clientX - rectangle.left;
		const y = clientY - rectangle.top;
		const projection = mapProjection(rectangle.width, rectangle.height);
		const layer = effectiveLayer();
		const mapIndexes = layer === 'none' ? (state.selected == null ? [] : [state.selected]) : mapTrackIndexes();
		if (!mapIndexes.length) return -1;
		const mapBits = new Set(mapIndexes);
		if (layer === 'genesis' || layer === 'lysis') {
			let bestTrack = -1;
			let bestDistance = (touch ? 20 : 11) ** 2;
			for (const trackIndex of mapIndexes) {
				const row = track(trackIndex);
				const latitude = Number(row[layer === 'lysis' ? T.end_lat_x1000 : T.gen_lat_x1000]) / 1000;
				const longitude = Number(row[layer === 'lysis' ? T.end_lon_x1000 : T.gen_lon_x1000]) / 1000;
				const point = projection.project(latitude, longitude);
				const distance = (point[0] - x) ** 2 + (point[1] - y) ** 2;
				if (distance < bestDistance) { bestDistance = distance; bestTrack = trackIndex; }
			}
			return bestTrack;
		}
		const geographical = projection.invert(x, y);
		const radiusPx = touch ? 18 : 10;
		const subsetTracksHidden = layer === 'tracks' && state.weatherLayer !== 'none' && !state.weatherTracks;
		return segmentIndex.query({
			x, y,
			lat: geographical[0],
			lon: geographical[1],
			radiusPx,
			radiusLon: radiusPx / projection.scale,
			radiusLat: radiusPx / projection.scale,
			project: projection.project,
			active: trackIndex => mapBits.has(trackIndex) && (!subsetTracksHidden || trackIndex === state.selected),
			segmentVisible: (trackIndex, pointIndex) => pointVisible(trackIndex, pointIndex - 1) && pointVisible(trackIndex, pointIndex)
		});
	}

	function nearestTrackPoint(clientX, clientY, trackIndex, touch) {
		const canvas = $('#mlaMapOverlay');
		const rectangle = canvas.getBoundingClientRect();
		const x = clientX - rectangle.left;
		const y = clientY - rectangle.top;
		const projection = mapProjection(rectangle.width, rectangle.height);
		const maximumDistance = touch ? 24 : 14;
		let bestIndex = -1;
		let bestDistance = maximumDistance ** 2;
		paths.decoded[trackIndex].forEach((point, pointIndex) => {
			if (!pointVisible(trackIndex, pointIndex)) return;
			const projected = projection.project(point[0], point[1]);
			const distance = (projected[0] - x) ** 2 + (projected[1] - y) ** 2;
			if (distance < bestDistance) { bestDistance = distance; bestIndex = pointIndex; }
		});
		return bestIndex;
	}

	function updateMapHover(clientX, clientY, touch) {
		const index = mapHitTest(clientX, clientY, touch);
		state.hovered = index >= 0 ? index : null;
		mapScheduler.invalidate(MAP_DIRTY.OVERLAY);
		const tip = $('#mlaMapTip');
		if (touch || index < 0) { tip.dataset.visible = 'false'; return; }
		const rectangle = $('#mlaMapStack').getBoundingClientRect();
		const row = track(index);
		const item = credibleIb(index);
		tip.innerHTML = `<strong>${esc(systemLabel(index))}</strong><br>${esc(date(row[T.start_ms]))} · Peak ERA5 class: ${esc(CORE.cat_labels[String(row[T.category])])}<br>${esc(metric().title)} P${fmt(percentileMetric(index))} · ${fmt(rawMetric(index), 1)} ${esc(metric().unit)}${item ? `<br>IBTrACS ${esc(item.confidence)} · median ${fmt(item.median_km)} km` : ''}`;
		tip.style.left = `${clamp(clientX - rectangle.left, 150, rectangle.width - 150)}px`;
		tip.style.top = `${clamp(clientY - rectangle.top, 70, rectangle.height - 20)}px`;
		tip.dataset.visible = 'true';
	}

	function fitMapToBounds(bounds) {
		if (!bounds) return false;
		const canvas = $('#mlaMapOverlay');
		const rectangle = canvas.getBoundingClientRect();
		const scope = mapBounds();
		const baseScale = Math.min((rectangle.width - 48) / (scope.lonMax - scope.lonMin), (rectangle.height - 48) / (scope.latMax - scope.latMin));
		const neededScale = Math.min((rectangle.width - 90) / Math.max(1.5, bounds[2] - bounds[0]), (rectangle.height - 90) / Math.max(1.5, bounds[3] - bounds[1]));
		state.mapZoom = clamp(neededScale / baseScale, 1, 12);
		state.mapCenterLon = (bounds[0] + bounds[2]) / 2;
		state.mapCenterLat = (bounds[1] + bounds[3]) / 2;
		constrainMapView(rectangle.width, rectangle.height);
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
			const touch = event.pointerType === 'touch';
			const focusMarker = timeFocusMarkerHitTest(event.clientX, event.clientY, touch);
			if (focusMarker >= 0) {
				if (focusMarker !== state.selected) selectTrack(focusMarker, {keepTimeFocus: true});
				return;
			}
			const index = mapHitTest(event.clientX, event.clientY, touch);
			if (index >= 0 && index === state.selected) {
				const pointIndex = nearestTrackPoint(event.clientX, event.clientY, index, touch);
				if (pointIndex >= 0) setTrackPointFocus(index, pointIndex);
			} else if (index >= 0) selectTrack(index);
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

	function officialGrade(index) {
		const item = crosswalk(index);
		if (item && item.imd && item.imd.system.peak_grade) return item.imd.system.peak_grade;
		if (item && item.ib && CORE.ibtracs_tracks[item.ib.sid] && CORE.ibtracs_tracks[item.ib.sid].imd_peak_grade) return CORE.ibtracs_tracks[item.ib.sid].imd_peak_grade;
		return '';
	}

	function bsisoLabel(index) {
		const phase = CLIMATE.bsiso.phase[index];
		const amplitude = CLIMATE.bsiso.amplitude_x100[index];
		if (phase < 0) return 'Unavailable';
		if (phase === 0) return `Inactive (${fmt(amplitude / 100, 2)})`;
		return `Phase ${phase} (${fmt(amplitude / 100, 2)})`;
	}

	function ensoLabel(index) {
		const category = CLIMATE.enso.class[index];
		if (category < 0) return 'Unavailable';
		return `${['La Niña', 'Neutral', 'El Niño'][category]} (${fmt(CLIMATE.enso.oni_x100[index] / 100, 2)} °C)`;
	}

	function renderDossier() {
		const node = $('#mlaDossier');
		const hasSelection = state.selected != null;
		$('#mlaExploreEvolutionGrid').classList.toggle('has-selection', hasSelection);
		$('#mlaSelectedEvolutionCard').hidden = !hasSelection;
		$('#mlaCompositeCard').hidden = !hasSelection;
		if (state.selected == null) {
			if (!state.active.length) {
				node.innerHTML = '<div class="mla-dossier-head"><div><h3>No matching systems</h3><p class="mla-dossier-sub">Adjust or reset the active filters.</p></div></div>';
				return;
			}
			const durations = state.active.map(index => Number(track(index)[T.duration_hours]));
			const distances = state.active.map(index => Number(track(index)[T.distance_km]));
			const rainfall = state.active.map(index => Number(track(index)[T.peak_precip_x10]) / 10);
			const named = state.active.filter(index => Boolean(officialName(index))).length;
			const facts = [
				['Systems', fmt(state.active.length)],
				['Median duration', durationText(median(durations))],
				['Median path length', `${fmt(median(distances))} km`],
				['Median peak 24 h rain', `${fmt(median(rainfall), 1)} mm`],
				['Named cyclone matches', fmt(named)],
				['Displayed positions', fmt(visiblePointCount(state.active))]
			];
			node.innerHTML = `<div class="mla-dossier-head"><div><span class="mla-badge" data-tone="official">Current subset</span><h3>${fmt(state.active.length)} systems</h3><p class="mla-dossier-sub">Selected-month positions on the map</p></div></div><div class="mla-fact-grid">${facts.map(fact => `<div class="mla-fact"><span>${esc(fact[0])}</span><strong>${esc(fact[1])}</strong></div>`).join('')}</div><p class="mla-dossier-empty">Select a track for its weather evolution, rainfall context and downloads.</p>`;
			return;
		}
		const index = state.selected;
		const row = track(index);
		const facts = [
			['Peak ERA5 class', CORE.cat_labels[String(row[T.category])]],
			['Duration', durationText(row[T.duration_hours])],
			['Hourly positions', fmt(row[T.n_rows])],
			['Pressure deficit', `${fmt(row[T.peak_deficit_x10] / 10, 1)} hPa`],
			['Circulation wind', `${fmt(row[T.peak_wind_x10] / 10, 1)} m s⁻¹`],
			['Minimum MSLP', `${fmt(row[T.min_mslp_x10] / 10, 1)} hPa`],
			['Peak 24 h rain', `${fmt(row[T.peak_precip_x10] / 10, 1)} mm`],
			['Linked path', `${fmt(row[T.distance_km])} km`],
			['Peak q850', `${fmt(row[T.peak_q850_x10] / 10, 1)} g kg⁻¹`],
			['Genesis region', endpointRegionLabel(genesisRegions[index])],
			['Lysis region', endpointRegionLabel(lysisRegions[index])],
			['BSISO-1 at genesis', bsisoLabel(index)],
			['ENSO at genesis', ensoLabel(index)]
		];
		const analogues = closestAnalogues(index, 5);
		node.innerHTML = `
			<div class="mla-dossier-head"><div><h3>${esc(systemLabel(index))}</h3><p class="mla-dossier-sub">${date(row[T.start_ms])} to ${date(row[T.end_ms])} · physical event ID ${atlasId(index)}</p></div></div>
			<div class="mla-fact-grid">${facts.map(fact => `<div class="mla-fact"><span>${esc(fact[0])}</span><strong>${esc(fact[1])}</strong></div>`).join('')}</div>
			<p class="mla-dossier-empty">Peak class is ERA5-derived and uses IMD-equivalent wind thresholds. CS means Cyclonic Storm, not Saffir–Simpson Category 1 or an official agency classification.</p>
			<div class="mla-match-box"><h4>Closest catalogue analogues</h4><div class="mla-chip-row">${analogues.map(([analogue, distance]) => `<button class="mla-chip" type="button" data-select-track="${analogue}" data-keep-map="true" title="track, intensity and impact analogue distance ${distance.toFixed(2)}">${esc(systemLabel(analogue))}</button>`).join('')}</div></div>
			<div class="mla-dossier-actions"><button class="mla-btn mla-btn-small" id="mlaPreviousTrack" type="button">Previous</button><button class="mla-btn mla-btn-small" id="mlaNextTrack" type="button">Next</button><button class="mla-btn mla-btn-small" id="mlaFitTrack" type="button">Fit track</button><button class="mla-btn mla-btn-small" id="mlaSelectedFixes" type="button">Download track points</button></div>
			`;
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
		const next = Number.isInteger(index) ? index : null;
		if (state.focusSource === 'point' && state.selected !== next && !(options && options.keepTimeFocus)) clearTimeFocus();
		state.selected = next;
		if (state.selected != null && Number.isFinite(state.focusTimeMs)) state.focusPointIndex = pointIndexAtTime(state.selected, state.focusTimeMs);
		state.hovered = null;
		rainfallMapCache = null;
		$('#mlaMapTip').dataset.visible = 'false';
		if (options && options.openExplore) activateTab('explore', true);
		renderDossier();
		updateTimeControls();
		renderTopTable();
		mapScheduler.invalidate((state.stateFill.startsWith('selected') ? MAP_DIRTY.BASE : 0) | MAP_DIRTY.DATA | MAP_DIRTY.OVERLAY);
		renderLifeCharts();
		if (state.selected != null && options && options.fit) requestAnimationFrame(fitSelected);
		writeUrl('push');
	}

	function tableHead() {
		return `<tr><th>System</th><th>Genesis</th><th>Peak ERA5 class</th><th>${esc(metric().title)}</th><th>Duration</th></tr>`;
	}

	function tableRow(index, openExplore) {
		const row = track(index);
		return `<tr data-selected="${index === state.selected}"><td><button class="mla-row-button" type="button" data-select-track="${index}" data-open-explore="${openExplore}">${esc(systemLabel(index))}</button></td><td>${date(row[T.start_ms])}</td><td>${esc(CLASS_SHORT[row[T.category]])}</td><td class="mla-num">${fmt(rawMetric(index), 1)} ${esc(metric().unit)}<br><small>P${fmt(percentileMetric(index))}</small></td><td class="mla-num">${durationText(row[T.duration_hours])}</td></tr>`;
	}

	function renderTopTable() {
		const table = $('#mlaTopTable');
		table.querySelector('thead').innerHTML = tableHead();
		table.querySelector('tbody').innerHTML = sortedActive('metric-desc').slice(0, 12).map(index => tableRow(index, false)).join('') || '<tr><td colspan="5">No systems match the current filters.</td></tr>';
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
		drawing.context.font = `14px ${CANVAS_FONT}`;
		drawing.context.fillText(message || 'No data for this subset', 18, 34);
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
		context.font = `11px ${CANVAS_FONT}`;
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
			context.setLineDash(item.dash || []);
			context.stroke();
			context.setLineDash([]);
		}
		const legendWidths = series.map(item => Math.min(150, 45 + item.name.length * 6.5));
		const legendWidth = legendWidths.reduce((sum, value) => sum + value, 0);
		let legendX = Math.max(8, Math.min(padding.left, width - legendWidth - 8));
		for (const item of series) {
			context.beginPath();
			context.moveTo(legendX, 17);
			context.lineTo(legendX + 18, 17);
			context.strokeStyle = item.colour;
			context.lineWidth = item.width || 2.3;
			context.lineCap = 'round';
			context.setLineDash(item.dash || []);
			context.stroke();
			context.setLineDash([]);
			context.fillStyle = css('--mla-ink', '#282119');
			context.font = `12px ${CANVAS_FONT}`;
			context.fillText(item.name, legendX + 24, 20);
			legendX += legendWidths[series.indexOf(item)];
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
		const breakPrefix = new Uint16Array(hours.length + 1);
		const breakSet = new Set((CORE.breaks[trackIndex] || []).map(item => Number(item[0])));
		for (let index = 0; index < hours.length; index++) breakPrefix[index + 1] = breakPrefix[index] + (breakSet.has(index) ? 1 : 0);
		const linePoints = hours.map((hour, index) => ({hour, value: lineSeries.values[index], index})).filter(point => Number.isFinite(point.value));
		const rainPoints = hours.map((hour, index) => ({hour, value: rainSeries.values[index], index})).filter(point => Number.isFinite(point.value));
		if (!linePoints.length && !rainPoints.length) { emptyChart('mlaLifeChart'); return null; }

		const {canvas, context, width, height} = drawing;
		const padding = {left: 58, right: 56, top: 42, bottom: 44};
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
		context.font = `11px ${CANVAS_FONT}`;
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
		context.font = `12px ${CANVAS_FONT}`;
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

		const sliderIndex = trackIndex === state.selected && Number.isInteger(state.focusPointIndex) && state.focusPointIndex >= 0
			? clamp(state.focusPointIndex, 0, hours.length - 1)
			: trackIndex === state.selected ? 0 : -1;
		const sliderHour = sliderIndex >= 0 ? Number(hours[sliderIndex]) : NaN;
		if (Number.isFinite(sliderHour)) {
			const x = X(sliderHour);
			context.beginPath();
			context.moveTo(x, padding.top);
			context.lineTo(x, plotBottom);
			context.setLineDash([6, 5]);
			context.strokeStyle = css('--mla-lac', '#8f2938');
			context.lineWidth = 1.7;
			context.stroke();
			context.setLineDash([]);
		}

		context.restore();

		const summary = `${fmt(hours.length)} hourly positions · ${definition.title} ${fmt(Math.min(...linePoints.map(point => point.value)), 1)}–${fmt(Math.max(...linePoints.map(point => point.value)), 1)} ${definition.unit} · peak 24 h rain ${fmt(Math.max(...rainPoints.map(point => point.value)), 1)} mm.`;
		const readout = $('#mlaLifeReadout');
		readout.textContent = summary;
		function pointIndexFromEvent(event) {
			const rectangle = canvas.getBoundingClientRect();
			const targetHour = xMin + clamp((event.clientX - rectangle.left - padding.left) / Math.max(1, rectangle.width - padding.left - padding.right), 0, 1) * (xMax - xMin);
			let low = 0;
			let high = hours.length - 1;
			while (low < high) {
				const middle = Math.floor((low + high) / 2);
				if (hours[middle] < targetHour) low = middle + 1; else high = middle;
			}
			return low > 0 && Math.abs(hours[low - 1] - targetHour) < Math.abs(hours[low] - targetHour) ? low - 1 : low;
		}
		function showPoint(event) {
			const index = pointIndexFromEvent(event);
			readout.textContent = `${timeLabel(hours[index])} from genesis · ${definition.title} ${fmt(lineSeries.values[index], 1)} ${definition.unit} · 24 h rain ${fmt(rainSeries.values[index], 1)} mm.`;
		}
		function scrubPoint(event) {
			const index = pointIndexFromEvent(event);
			showPoint(event);
			setTrackPointFocus(trackIndex, index, {activateWeather: false, noSeek: true, noUrl: true});
			scheduleSliderWeather();
		}
		canvas.onpointermove = event => { if (evolutionChartDragging) scrubPoint(event); else showPoint(event); };
		canvas.onpointerdown = event => {
			evolutionChartDragging = true;
			canvas.setPointerCapture(event.pointerId);
			scrubPoint(event);
		};
		canvas.onpointerup = event => {
			if (!evolutionChartDragging) return;
			scrubPoint(event);
			evolutionChartDragging = false;
			if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
			commitTrackHourSlider();
		};
		canvas.onpointercancel = () => { evolutionChartDragging = false; };
		canvas.onpointerleave = () => { if (!evolutionChartDragging) readout.textContent = summary; };
		canvas.onkeydown = event => {
			if (!['ArrowLeft', 'ArrowRight', 'Home', 'End', 'PageUp', 'PageDown'].includes(event.key)) return;
			event.preventDefault();
			let index = Number.isInteger(state.focusPointIndex) && state.focusPointIndex >= 0 ? state.focusPointIndex : 0;
			if (event.key === 'Home') index = 0;
			else if (event.key === 'End') index = hours.length - 1;
			else index += (['ArrowRight', 'PageDown'].includes(event.key) ? 1 : -1) * (event.key.startsWith('Page') ? 8 : 1);
			setTrackPointFocus(trackIndex, clamp(index, 0, hours.length - 1), {activateWeather: false});
		};
		canvas.setAttribute('aria-label', `${definition.title} line with 24-hour rainfall bars for ${systemLabel(trackIndex)}${Number.isFinite(sliderHour) ? `; dashed slider-time marker at ${timeLabel(sliderHour)} from genesis` : ''}; drag the marker or use arrow keys to change time`);
		return {hours, lineValues: lineSeries.values, rainValues: rainSeries.values, summary};
	}

	function scheduleEvolutionFocusDraw() {
		if (evolutionFocusFrame || !DETAIL || state.selected == null || $('#mlaPanelExplore').hidden) return;
		evolutionFocusFrame = requestAnimationFrame(() => {
			evolutionFocusFrame = 0;
			if (DETAIL && state.selected != null && !$('#mlaPanelExplore').hidden) drawEvolutionPlot(state.selected, state.evolutionMetric);
		});
	}

	function drawBars(id, items, options) {
		const drawing = setupChart(id);
		if (!drawing) return;
		if (!items.length) { emptyChart(id); return; }
		const {context, width, height} = drawing;
		const padding = {left: 46, right: 16, top: 24, bottom: 46};
		const maximum = Math.max(1, ...items.map(item => item.value));
		const barWidth = (width - padding.left - padding.right) / items.length;
		context.font = `11px ${CANVAS_FONT}`;
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
		const labelColour = css('--mla-muted', '#685c4d');
		context.font = `11px ${CANVAS_FONT}`;
		context.fillStyle = labelColour;
		columns.forEach((label, index) => context.fillText(label, padding.left + index * cellWidth + 3, height - 15));
		rows.forEach((label, row) => {
			context.fillStyle = labelColour;
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
		const padding = 30;
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
		const projection = fixedProjection(drawing.width, drawing.height, {lonMin: 52, lonMax: 108, latMin: -4, latMax: 36});
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
		drawMapReferenceLines(drawing.context, projection);
		const legendWidth = Math.min(280, drawing.width * .34);
		const legendX = drawing.width - legendWidth - 18;
		const legendY = drawing.height - 25;
		const gradient = drawing.context.createLinearGradient(legendX, 0, legendX + legendWidth, 0);
		for (let step = 0; step <= 32; step++) gradient.addColorStop(step / 32, ramp(step / 32));
		drawing.context.fillStyle = gradient;
		drawing.context.fillRect(legendX, legendY, legendWidth, 9);
		drawing.context.font = `11px ${CANVAS_FONT}`;
		drawing.context.fillStyle = css('--mla-ink', '#282119');
		drawing.context.textAlign = 'left';
		drawing.context.fillText('Systems per 0.5° cell', legendX, legendY - 6);
		const tickValues = [...new Set([0, Math.max(1, Math.round(maximum / 4)), Math.max(1, Math.round(maximum / 2)), maximum])];
		for (const value of tickValues) {
			const x = legendX + Math.sqrt(value / maximum) * legendWidth;
			drawing.context.textAlign = value === 0 ? 'left' : value === maximum ? 'right' : 'center';
			drawing.context.fillText(fmt(value), x, legendY + 22);
		}
		drawing.context.textAlign = 'left';
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

	function compositePaletteColour(stops, fraction) {
		const position = clamp(fraction, 0, 1) * (stops.length - 1);
		const lower = Math.floor(position);
		const upper = Math.min(stops.length - 1, Math.ceil(position));
		const mix = position - lower;
		const parse = colour => [1, 3, 5].map(index => parseInt(colour.slice(index, index + 2), 16));
		const first = parse(stops[lower]);
		const second = parse(stops[upper]);
		return `rgb(${first.map((channel, index) => Math.round(channel + (second[index] - channel) * mix)).join(',')})`;
	}

	function unpackCompositeField(field) {
		if (!field || !Array.isArray(field.shape) || field.shape.length !== 2 || !Array.isArray(field.data)) return null;
		const values = new Float32Array(field.data.length);
		for (let index = 0; index < field.data.length; index++) values[index] = field.data[index] == null ? NaN : Number(field.data[index]) * Number(field.scale || 1);
		return {values, rows: Number(field.shape[0]), columns: Number(field.shape[1])};
	}

	function drawCompositeColourBar(context, palette, minimum, maximum, unit, x, y, width) {
		const height = 12;
		for (let step = 0; step < Math.ceil(width); step++) {
			const fraction = step / Math.max(1, width - 1);
			context.fillStyle = compositePaletteColour(palette, fraction);
			context.fillRect(x + step, y, 1.2, height);
		}
		context.strokeStyle = css('--mla-line-strong', '#b9aa97');
		context.lineWidth = 1;
		context.strokeRect(x, y, width, height);
		context.fillStyle = css('--mla-ink', '#282119');
		context.font = `${COMPOSITE_TICK_FONT_SIZE}px ${CANVAS_FONT}`;
		context.textBaseline = 'top';
		for (const value of [minimum, (minimum + maximum) / 2, maximum]) {
			const fraction = (value - minimum) / (maximum - minimum);
			const tickX = x + fraction * width;
			context.beginPath();
			context.moveTo(tickX, y + height);
			context.lineTo(tickX, y + height + 4);
			context.stroke();
			context.textAlign = value === minimum ? 'left' : value === maximum ? 'right' : 'center';
			context.fillText(fmt(value, Math.abs(value) < 10 && value % 1 ? 1 : 0), tickX, y + height + 6);
		}
		context.font = `${COMPOSITE_LABEL_FONT_SIZE}px ${CANVAS_FONT}`;
		context.textAlign = 'center';
		context.fillText(unit, x + width / 2, y + height + 27);
	}

	function drawCompositeXAxes(context, plot, yLabel) {
		const ink = css('--mla-ink', '#282119');
		const line = css('--mla-line-strong', '#b9aa97');
		context.strokeStyle = line;
		context.lineWidth = 1;
		context.strokeRect(plot.left, plot.top, plot.width, plot.height);
		context.fillStyle = ink;
		context.font = `${COMPOSITE_TICK_FONT_SIZE}px ${CANVAS_FONT}`;
		context.textBaseline = 'top';
		for (const value of [-10, -5, 0, 5, 10]) {
			const x = plot.left + (value + 10) / 20 * plot.width;
			context.beginPath();
			context.moveTo(x, plot.top + plot.height);
			context.lineTo(x, plot.top + plot.height + 4);
			context.stroke();
			context.textAlign = value === -10 ? 'left' : value === 10 ? 'right' : 'center';
			context.fillText(String(value).replace('-', '−'), x, plot.top + plot.height + 6);
		}
		context.font = `${COMPOSITE_LABEL_FONT_SIZE}px ${CANVAS_FONT}`;
		context.textAlign = 'center';
		context.fillText('Relative longitude (°)', plot.left + plot.width / 2, plot.top + plot.height + 27);
		context.save();
		context.translate(14, plot.top + plot.height / 2);
		context.rotate(-Math.PI / 2);
		context.textBaseline = 'top';
		context.fillText(yLabel, 0, 0);
		context.restore();
	}

	function drawPrecipitationComposite(field) {
		const unpacked = unpackCompositeField(field);
		const drawing = setupChart('mlaPrecipComposite');
		if (!drawing || !unpacked) return;
		const {context, width, height} = drawing;
		const availableWidth = Math.max(80, width - 66);
		const availableHeight = Math.max(80, height - 119);
		const plotSize = Math.min(availableWidth, availableHeight);
		const plot = {left: 52 + (availableWidth - plotSize) / 2, top: 14, width: plotSize, height: plotSize};
		const palette = COMPOSITE_PALETTES.terrain_r;
		const minimum = 0;
		const maximum = 60;
		const cellWidth = plot.width / unpacked.columns;
		const cellHeight = plot.height / unpacked.rows;
		context.save();
		context.beginPath();
		context.rect(plot.left, plot.top, plot.width, plot.height);
		context.clip();
		for (let row = 0; row < unpacked.rows; row++) {
			const y = plot.top + (unpacked.rows - row - 1) * cellHeight;
			for (let column = 0; column < unpacked.columns; column++) {
				const value = unpacked.values[row * unpacked.columns + column];
				if (!Number.isFinite(value)) continue;
				context.fillStyle = compositePaletteColour(palette, (value - minimum) / (maximum - minimum));
				context.fillRect(plot.left + column * cellWidth, y, cellWidth + .7, cellHeight + .7);
			}
		}
		context.restore();
		context.strokeStyle = 'rgba(40,33,25,.22)';
		context.lineWidth = 1;
		context.setLineDash([3, 4]);
		for (const value of [-5, 0, 5]) {
			const x = plot.left + (value + 10) / 20 * plot.width;
			const y = plot.top + (10 - value) / 20 * plot.height;
			context.beginPath(); context.moveTo(x, plot.top); context.lineTo(x, plot.top + plot.height); context.stroke();
			context.beginPath(); context.moveTo(plot.left, y); context.lineTo(plot.left + plot.width, y); context.stroke();
		}
		context.setLineDash([]);
		const centreX = plot.left + plot.width / 2;
		const centreY = plot.top + plot.height / 2;
		context.beginPath(); context.arc(centreX, centreY, 4.6, 0, Math.PI * 2); context.fillStyle = '#fff'; context.fill();
		context.beginPath(); context.arc(centreX, centreY, 2.8, 0, Math.PI * 2); context.fillStyle = '#19140f'; context.fill();
		drawCompositeXAxes(context, plot, 'Relative latitude (°)');
		context.fillStyle = css('--mla-ink', '#282119');
		context.font = `${COMPOSITE_TICK_FONT_SIZE}px ${CANVAS_FONT}`;
		context.textBaseline = 'middle';
		context.textAlign = 'right';
		for (const value of [-10, -5, 0, 5, 10]) {
			const y = plot.top + (10 - value) / 20 * plot.height;
			context.fillText(String(value).replace('-', '−'), plot.left - 6, y);
		}
		drawCompositeColourBar(context, palette, minimum, maximum, 'mm day⁻¹', plot.left, plot.top + plot.height + 51, plot.width);
	}

	function drawSectionComposite(field, pressureLevels, definition) {
		const unpacked = unpackCompositeField(field);
		const drawing = setupChart('mlaSectionComposite');
		if (!drawing || !unpacked || !Array.isArray(pressureLevels)) return;
		const {context, width, height} = drawing;
		const plot = {left: 58, top: 14, width: Math.max(80, width - 72), height: Math.max(80, height - 119)};
		const palette = COMPOSITE_PALETTES[definition.palette];
		const cellWidth = plot.width / unpacked.columns;
		const topPressure = Number(definition.topPressure || Math.min(...pressureLevels.map(Number)));
		const visibleRows = pressureLevels
			.map((pressure, index) => ({pressure: Number(pressure), index}))
			.filter(row => row.pressure >= topPressure);
		if (!visibleRows.length) return;
		const bottomPressure = Math.max(...visibleRows.map(row => row.pressure));
		const pressureSpan = Math.log(bottomPressure / topPressure);
		const pressureY = pressure => plot.top + Math.log(Number(pressure) / topPressure) / pressureSpan * plot.height;
		const centreY = visibleRows.map(row => pressureY(row.pressure));
		const edges = [plot.top + plot.height];
		for (let index = 0; index < centreY.length - 1; index++) edges.push((centreY[index] + centreY[index + 1]) / 2);
		edges.push(plot.top);
		context.save();
		context.beginPath();
		context.rect(plot.left, plot.top, plot.width, plot.height);
		context.clip();
		for (let row = 0; row < visibleRows.length; row++) {
			const y0 = Math.min(edges[row], edges[row + 1]);
			const cellHeight = Math.abs(edges[row + 1] - edges[row]);
			const sourceRow = visibleRows[row].index;
			for (let column = 0; column < unpacked.columns; column++) {
				const value = unpacked.values[sourceRow * unpacked.columns + column];
				if (!Number.isFinite(value)) continue;
				context.fillStyle = compositePaletteColour(palette, (value - definition.minimum) / (definition.maximum - definition.minimum));
				context.fillRect(plot.left + column * cellWidth, y0, cellWidth + .7, cellHeight + .7);
			}
		}
		context.restore();
		const centreX = plot.left + plot.width / 2;
		context.strokeStyle = 'rgba(25,20,15,.68)';
		context.lineWidth = 1.2;
		context.setLineDash([4, 4]);
		context.beginPath(); context.moveTo(centreX, plot.top); context.lineTo(centreX, plot.top + plot.height); context.stroke();
		context.setLineDash([]);
		drawCompositeXAxes(context, plot, 'Pressure (hPa)');
		context.fillStyle = css('--mla-ink', '#282119');
		context.font = `${COMPOSITE_TICK_FONT_SIZE}px ${CANVAS_FONT}`;
		context.textBaseline = 'middle';
		context.textAlign = 'right';
		const pressureTicks = topPressure > 100
			? [1000, 850, 700, 500, 300, 200, 125]
			: [1000, 850, 700, 500, 300, 200, 100];
		for (const pressure of pressureTicks.filter(value => value >= topPressure && value <= bottomPressure)) {
			const y = pressureY(pressure);
			context.beginPath(); context.moveTo(plot.left - 4, y); context.lineTo(plot.left, y); context.strokeStyle = css('--mla-line-strong', '#b9aa97'); context.stroke();
			context.fillText(String(pressure), plot.left - 6, y);
		}
		drawCompositeColourBar(context, palette, definition.minimum, definition.maximum, definition.unit, plot.left, plot.top + plot.height + 51, plot.width);
	}

	function compositeOptionLabel(key) {
		return key === 'imerg' ? 'IMERG' : key === 'era5' ? 'ERA5' : key;
	}

	function renderStormComposites() {
		if ($('#mlaPanelExplore').hidden) return;
		const precipControl = $('#mlaCompositePrecipSource');
		const sectionControl = $('#mlaCompositeSectionVariable');
		const retry = $('#mlaRetryComposite');
		if (state.selected == null) {
			compositeLoadSerial++;
			precipControl.disabled = true;
			sectionControl.disabled = true;
			retry.hidden = true;
			emptyChart('mlaPrecipComposite', 'Select a system to load its storm-centred footprint');
			emptyChart('mlaSectionComposite', 'Select a system to load its storm-centred section');
			$('#mlaPrecipCompositeStatus').textContent = 'Select a system to load its precipitation footprint.';
			$('#mlaSectionCompositeStatus').textContent = 'Select a system to load its vertical structure.';
			$('#mlaCompositeData').textContent = 'Select a system to inspect composite provenance.';
			return;
		}
		const selectedIndex = state.selected;
		const trackId = atlasId(selectedIndex);
		const asset = compositeCache.get(trackId);
		const loadError = compositeErrors.get(trackId);
		if (!asset && loadError) {
			precipControl.disabled = true;
			sectionControl.disabled = true;
			retry.hidden = false;
			emptyChart('mlaPrecipComposite', 'Composite unavailable; retry when ready');
			emptyChart('mlaSectionComposite', 'Composite unavailable; retry when ready');
			const message = loadError && loadError.message ? loadError.message : String(loadError);
			$('#mlaPrecipCompositeStatus').textContent = message;
			$('#mlaSectionCompositeStatus').textContent = message;
			$('#mlaCompositeData').innerHTML = `<p>${esc(message)}</p>`;
			return;
		}
		if (!asset) {
			precipControl.disabled = true;
			sectionControl.disabled = true;
			retry.hidden = true;
			emptyChart('mlaPrecipComposite', 'Loading storm-centred precipitation…');
			emptyChart('mlaSectionComposite', 'Loading storm-centred vertical structure…');
			$('#mlaPrecipCompositeStatus').textContent = 'Loading the selected physical event…';
			$('#mlaSectionCompositeStatus').textContent = 'Loading the selected physical event…';
			$('#mlaCompositeData').textContent = 'Loading composite provenance…';
			const serial = ++compositeLoadSerial;
			ensureStormComposite(selectedIndex).then(() => {
				if (serial === compositeLoadSerial && state.selected === selectedIndex) renderStormComposites();
			}).catch(() => {
				if (serial === compositeLoadSerial && state.selected === selectedIndex) renderStormComposites();
			});
			return;
		}
		compositeLoadSerial++;
		retry.hidden = true;
		const precipKeys = ['era5', 'imerg'].filter(key => asset.precipitation && asset.precipitation[key]);
		precipControl.innerHTML = precipKeys.map(key => `<option value="${key}">${esc(compositeOptionLabel(key))}</option>`).join('');
		const precipKey = precipKeys.includes(state.compositePrecipSource) ? state.compositePrecipSource : precipKeys[0];
		precipControl.disabled = precipKeys.length < 2;
		if (precipKey) {
			precipControl.value = precipKey;
			const field = asset.precipitation[precipKey];
			drawPrecipitationComposite(field);
			$('#mlaPrecipComposite').setAttribute('aria-label', `${compositeOptionLabel(precipKey)} storm-centred mean daily precipitation for ${systemLabel(selectedIndex)}; track centre is at zero relative longitude and latitude`);
			$('#mlaPrecipCompositeStatus').textContent = `${compositeOptionLabel(precipKey)} · ${fmt(field.samples)}/${fmt(field.requested_samples)} UTC days · ${fmt(100 * field.spatial_coverage_fraction)}% footprint coverage · fixed 0–60 mm day⁻¹ scale`;
		} else {
			emptyChart('mlaPrecipComposite', 'No daily precipitation source is available');
			$('#mlaPrecipCompositeStatus').textContent = 'No daily precipitation source is available for this system.';
		}
		const sectionKeys = Object.keys(COMPOSITE_SECTION_DEFINITIONS).filter(key => asset.section && asset.section[key]);
		sectionControl.innerHTML = sectionKeys.map(key => `<option value="${key}">${esc(COMPOSITE_SECTION_DEFINITIONS[key].label)}</option>`).join('');
		const sectionKey = sectionKeys.includes(state.compositeSectionVariable) ? state.compositeSectionVariable : sectionKeys[0];
		sectionControl.disabled = sectionKeys.length < 2;
		if (sectionKey) {
			sectionControl.value = sectionKey;
			const field = asset.section[sectionKey];
			const definition = COMPOSITE_SECTION_DEFINITIONS[sectionKey];
			drawSectionComposite(field, asset.grid.pressure_hpa, definition);
			const pressureNote = definition.topPressure ? ` · 1000–${definition.topPressure} hPa` : '';
			$('#mlaSectionComposite').setAttribute('aria-label', `${definition.label} storm-centred zonal vertical section through zero relative latitude for ${systemLabel(selectedIndex)}; dashed line is zero relative longitude${definition.topPressure ? '; 100 hPa is omitted from this display' : ''}`);
			$('#mlaSectionCompositeStatus').textContent = `${definition.label} · ${fmt(field.samples)}/${fmt(field.requested_samples)} lifecycle snapshots${pressureNote} · fixed ${definition.minimum}–${definition.maximum} ${definition.unit} · ${field.source}`;
		} else {
			emptyChart('mlaSectionComposite', 'No vertical section is available');
			$('#mlaSectionCompositeStatus').textContent = 'No vertical section is available for this system.';
		}
		const availabilityRows = [
			...precipKeys.map(key => {
				const field = asset.precipitation[key];
				return [compositeOptionLabel(key), `${field.samples}/${field.requested_samples} UTC days`, field.source];
			}),
			...sectionKeys.map(key => {
				const field = asset.section[key];
				return [COMPOSITE_SECTION_DEFINITIONS[key].label, `${field.samples}/${field.requested_samples} lifecycle snapshots`, field.source];
			})
		];
		$('#mlaCompositeData').innerHTML = accessibleTable(['Field', 'Coverage', 'Source'], availabilityRows, `${asset.method.precipitation} ${asset.method.vertical} The θₑ display omits 100 hPa and uses a fixed 330–370 K blue–white–red scale.`);
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
		renderStormComposites();
		const profileButton = $('#mlaLoadProfile');
		if (state.selected == null) {
			emptyChart('mlaLifeChart', 'Select a system to view raw meteorology');
			$('#mlaLifeData').innerHTML = '';
			$('#mlaLifeReadout').textContent = 'Select a system to inspect hourly published-centre physics.';
		} else if (!DETAIL) {
			emptyChart('mlaLifeChart', 'Loading selected-system detail…');
			$('#mlaLifeReadout').textContent = 'Loading hourly published-centre physics…';
			ensureDetail().then(renderLifeCharts).catch(showFatal);
		} else {
			const definition = METRICS[state.evolutionMetric];
			const evolution = drawEvolutionPlot(state.selected, state.evolutionMetric);
			const stride = Math.max(1, Math.ceil(evolution.hours.length / 160));
			const rows = evolution.hours.map((hour, index) => ({hour, index})).filter((item, index) => index % stride === 0 || index === evolution.hours.length - 1).map(item => [
				item.hour,
				fmt(evolution.lineValues[item.index], 2),
				fmt(evolution.rainValues[item.index], 2)
			]);
			$('#mlaLifeData').innerHTML = accessibleTable(['Hours since genesis', `${definition.title} (${definition.unit})`, '24 h rain (mm)'], rows, 'Physics is resampled at each published v5.5.1 centre.');
		}
		if (!DETAIL) {
			profileButton.hidden = false;
			$('#mlaProfileStack').innerHTML = '<canvas class="mla-chart mla-profile-chart" id="mlaProfilePlaceholder" role="img" aria-label="Subset profiles are ready to load"></canvas>';
			emptyChart('mlaProfilePlaceholder', 'Load the subset variables when needed');
			$('#mlaProfileData').innerHTML = '';
			return;
		}
		profileButton.hidden = true;
		const profileMetrics = PROFILE_METRIC_KEYS.filter(key => state.profileMetrics.has(key));
		const allIndexes = CORE.tracks.map((unused, index) => index);
		const stack = $('#mlaProfileStack');
		stack.innerHTML = profileMetrics.map(key => {
			const definition = METRICS[key];
			return `<div class="mla-profile-slab"><div class="mla-profile-slab-head"><strong>${esc(definition.title)}</strong><span id="mlaProfileMeta-${key}">${esc(definition.unit)}</span></div><canvas class="mla-chart mla-profile-chart" id="mlaProfileChart-${key}" role="img" tabindex="0" aria-label="${esc(`${definition.title}: filtered-subset median and interquartile range with all-LPS mean`)}"></canvas></div>`;
		}).join('');
		const accessibleProfiles = [];
		for (const key of profileMetrics) {
			const definition = METRICS[key];
			const profile = cohortProfile(state.active, key, 'life');
			const allProfile = cohortProfile(allIndexes, key, 'life');
			const maximumN = Math.max(0, ...profile.points.map(point => point.n));
			const allMaximumN = Math.max(0, ...allProfile.points.map(point => point.n));
			$(`#mlaProfileMeta-${key}`).textContent = `${definition.unit} · subset n ≤ ${fmt(maximumN)} · all LPS n ≤ ${fmt(allMaximumN)}`;
			drawLinePlot(`mlaProfileChart-${key}`, [
				{
					name: 'Subset median',
					colour: definition.colour,
					points: profile.points
				},
				{
					name: 'All-LPS mean',
					colour: css('--mla-muted', '#685c4d'),
					width: 1.9,
					dash: [1, 5],
					points: allProfile.points.map(point => ({x: point.x, y: point.mean}))
				}
			], {
				zero: !['mslp', 'vort', 'q', 'rh'].includes(key),
				xMin: 0,
				xMax: 100,
				xFormat: value => `${fmt(value)}%`,
				yFormat: value => fmt(value, key === 'mslp' ? 0 : 1)
			});
			accessibleProfiles.push(`<h4>${esc(`${definition.title} (${definition.unit})`)}</h4>${accessibleTable(
				['Life fraction', 'Subset median', 'Subset Q1', 'Subset Q3', 'Subset systems', 'All-LPS mean', 'All-LPS systems'],
				profile.points.map((point, index) => [`${fmt(point.x)}%`, fmt(point.y, 2), fmt(point.low, 2), fmt(point.high, 2), point.n, fmt(allProfile.points[index].mean, 2), allProfile.points[index].n])
			)}`);
		}
		$('#mlaProfileData').innerHTML = accessibleProfiles.join('');
	}

	function cohortProfile(indexes, metricKey, alignment) {
		const allSystems = indexes.length === CORE.tracks.length && indexes.every((value, index) => value === index);
		const cacheKey = `${allSystems ? 'all' : indexes.join(',')}|${metricKey}|${alignment}`;
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
		const points = bins.map((x, index) => ({x, y: median(values[index]), mean: mean(values[index]), low: quantile(values[index], .25), high: quantile(values[index], .75), n: values[index].length}));
		const result = {points, bins};
		profileCache.set(cacheKey, result);
		return result;
	}

	const EXTREMES = {
		duration: {label: 'Duration', unit: 'h', decimals: 0, value: index => track(index)[T.duration_hours], descending: true, note: 'Hourly event span · supported centres included'},
		distance: {label: 'Linked path length', unit: 'km', decimals: 0, value: index => track(index)[T.distance_km], descending: true, note: 'Great-circle distance summed along hourly centres'},
		meanSpeed: {label: 'Mean translation speed', unit: 'm s⁻¹', decimals: 1, value: index => track(index)[T.distance_km] * 1000 / Math.max(3600, track(index)[T.duration_hours] * 3600), descending: true, note: 'Path length divided by elapsed event duration'},
		deficit: {label: 'Pressure deficit', unit: 'hPa', decimals: 1, value: index => track(index)[T.peak_deficit_x10] / 10, descending: true},
		wind: {label: 'Circulation wind', unit: 'm s⁻¹', decimals: 1, value: index => track(index)[T.peak_wind_x10] / 10, descending: true},
		rain: {label: '24 h precipitation', unit: 'mm', decimals: 1, value: index => track(index)[T.peak_precip_x10] / 10, descending: true, note: 'Largest trailing 24-hour track-centred diagnostic'},
		vort: {label: 'Smoothed vorticity', unit: '10⁻⁵ s⁻¹', decimals: 1, value: index => track(index)[T.peak_vort_x10] / 10, descending: true},
		mslp: {label: 'Minimum MSLP', unit: 'hPa', decimals: 1, value: index => track(index)[T.min_mslp_x10] / 10, descending: false},
		q850: {label: 'q850', unit: 'g kg⁻¹', decimals: 1, value: index => track(index)[T.peak_q850_x10] / 10, descending: true},
		rh850: {label: 'RH850', unit: '%', decimals: 1, value: index => track(index)[T.peak_rh850_x10] / 10, descending: true},
		observedPositions: {label: 'Observed positions', unit: 'track points', decimals: 0, value: index => track(index)[T.observed_positions], descending: true, note: 'Detector-supported hourly positions only'},
		qualifyingPositions: {label: 'Mature detections', unit: 'track points', decimals: 0, value: index => track(index)[T.qualifying_positions], descending: true, note: 'Positions passing the mature-physics gate'},
		lowestCoverage: {label: 'Observed coverage', unit: '%', decimals: 0, value: index => CORE.qc[index][Q.coverage_pct], descending: false, note: 'Lowest detector-observed fraction of the hourly event span'},
		posteriorShare: {label: 'Posterior-position share', unit: '%', decimals: 1, value: index => track(index)[T.posterior_fraction_x1000] / 10, descending: true, note: 'Supported interpolated centres as a fraction of hourly positions'},
		missingRun: {label: 'Longest supported missing run', unit: 'h', decimals: 0, value: index => track(index)[T.max_missing_run_hours], descending: true, note: 'Longest consecutive run without an observed detector fix'},
		maxStepSpeed: {label: 'Maximum step speed', unit: 'm s⁻¹', decimals: 1, value: index => CORE.qc[index][Q.max_speed_ms], descending: true, note: 'QA diagnostic from consecutive hourly centres'},
		rainDays: {label: 'UTC rain days', unit: 'days', decimals: 0, value: index => track(index)[T.rain_days], descending: true, note: 'UTC calendar days touched by the event track'},
		stateRain: {label: 'Highest crossed-state mean rain', unit: 'mm day⁻¹', decimals: 1, value: index => track(index)[T.top_state_mean_x10] / 10, descending: true, note: 'Largest event-mean IMD rainfall among crossed states/UTs'},
		northGenesis: {label: 'Genesis latitude', unit: '°N', decimals: 2, value: index => track(index)[T.gen_lat_x1000] / 1000, descending: true, note: 'Northernmost first published centre'},
		southGenesis: {label: 'Genesis latitude', unit: '°N', decimals: 2, value: index => track(index)[T.gen_lat_x1000] / 1000, descending: false, note: 'Southernmost first published centre'},
		eastGenesis: {label: 'Genesis longitude', unit: '°E', decimals: 2, value: index => track(index)[T.gen_lon_x1000] / 1000, descending: true, note: 'Easternmost first published centre'},
		westGenesis: {label: 'Genesis longitude', unit: '°E', decimals: 2, value: index => track(index)[T.gen_lon_x1000] / 1000, descending: false, note: 'Westernmost first published centre'}
	};

	function renderExtremes() {
		if ($('#mlaPanelExtremes').hidden) return;
		const definition = EXTREMES[state.extremeMetric];
		const indexes = state.active.filter(index => Number.isFinite(definition.value(index))).sort((first, second) => {
			const difference = definition.descending ? definition.value(second) - definition.value(first) : definition.value(first) - definition.value(second);
			return difference || track(first)[T.id] - track(second)[T.id];
		});
		const valueText = index => `${fmt(definition.value(index), definition.decimals)} ${definition.unit}`.trim();
		$('#mlaExtremeCaveat').textContent = definition.note || 'Catalogue diagnostic · not an externally validated record';
		$('#mlaRecordCards').innerHTML = indexes.slice(0, 3).map((index, rank) => {
			return `<article class="mla-card mla-record"><span class="mla-label">${rank + 1} · ${esc(definition.label)}</span><h3><button class="mla-row-button" type="button" data-select-track="${index}" data-open-explore="true">${esc(systemLabel(index))}</button></h3><p><strong>${esc(valueText(index))}</strong> · ${date(track(index)[T.start_ms])} · ${esc(CLASS_SHORT[track(index)[T.category]])}</p></article>`;
		}).join('') || '<p>No eligible systems in this subset.</p>';
		const table = $('#mlaExtremeTable');
		table.querySelector('thead').innerHTML = `<tr><th>Rank</th><th>System</th><th>Genesis</th><th>${esc(definition.label)}</th><th>Peak ERA5 class</th></tr>`;
		table.querySelector('tbody').innerHTML = indexes.slice(0, 50).map((index, rank) => {
			return `<tr><td>${rank + 1}</td><td><button class="mla-row-button" type="button" data-select-track="${index}" data-open-explore="true">${esc(systemLabel(index))}</button></td><td>${date(track(index)[T.start_ms])}</td><td class="mla-num">${esc(valueText(index))}</td><td>${esc(CLASS_SHORT[track(index)[T.category]])}</td></tr>`;
		}).join('') || '<tr><td colspan="5">No eligible systems.</td></tr>';
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
			['High-confidence IBTrACS', high, `${fmt(high / Math.max(1, state.active.length) * 100, 1)}% of subset`],
			['Medium-confidence', medium, 'Retained with match diagnostics'],
			['Named associations', named, 'High or medium confidence'],
			['Multiple atlas events', fragmented, 'More than one physical event associated with an IBTrACS SID']
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
		table.querySelector('thead').innerHTML = '<tr><th>External event</th><th>Atlas events</th><th>Best confidence</th><th>Median separation range</th></tr>';
		table.querySelector('tbody').innerHTML = groups.slice(0, 60).map(([sid, indexes]) => {
			const best = CORE.ibtracs_tracks[sid];
			const matches = indexes.map(index => crosswalk(index).ib);
			const order = {high: 3, medium: 2, low: 1};
			const confidence = matches.slice().sort((a, b) => order[b.confidence] - order[a.confidence])[0].confidence;
			const distances = matches.map(match => match.median_km);
			return `<tr><td>${esc(best && best.name ? `Cyclone ${best.name}` : sid)}<br><small>${esc(sid)}</small></td><td>${indexes.map(index => `<button class="mla-row-button" type="button" data-select-track="${index}" data-open-explore="true">${index}</button>`).join(', ')}</td><td>${esc(confidence)}</td><td>${fmt(Math.min(...distances))}–${fmt(Math.max(...distances))} km</td></tr>`;
		}).join('') || '<tr><td colspan="4">No repeated IBTrACS associations in this subset.</td></tr>';
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
				physical_event_id: atlasId(index),
				system_label: systemLabel(index),
				start_utc: new Date(row[T.start_ms]).toISOString(),
				end_utc: new Date(row[T.end_ms]).toISOString(),
				duration_hours: row[T.duration_hours],
				genesis_latitude: row[T.gen_lat_x1000] / 1000,
				genesis_longitude: row[T.gen_lon_x1000] / 1000,
				genesis_region: endpointRegionLabel(genesisRegions[index]),
				lysis_latitude: row[T.end_lat_x1000] / 1000,
				lysis_longitude: row[T.end_lon_x1000] / 1000,
				lysis_region: endpointRegionLabel(lysisRegions[index]),
				bsiso1_phase_at_genesis: CLIMATE.bsiso.phase[index] < 0 ? null : CLIMATE.bsiso.phase[index],
				bsiso1_amplitude_at_genesis: CLIMATE.bsiso.amplitude_x100[index] < 0 ? null : CLIMATE.bsiso.amplitude_x100[index] / 100,
				enso_category_at_genesis: CLIMATE.enso.class[index] < 0 ? null : ['La Nina', 'Neutral', 'El Nino'][CLIMATE.enso.class[index]],
				oni_anomaly_c_at_genesis: CLIMATE.enso.oni_x100[index] === -32768 ? null : CLIMATE.enso.oni_x100[index] / 100,
				atlas_peak_class: CORE.cat_labels[String(row[T.category])],
				peak_vorticity_1e_5_s_1: row[T.peak_vort_x10] / 10,
				peak_precip_24h_mm: row[T.peak_precip_x10] / 10,
				peak_wind_ms: row[T.peak_wind_x10] / 10,
				peak_pressure_deficit_hpa: row[T.peak_deficit_x10] / 10,
				minimum_mslp_hpa: row[T.min_mslp_x10] / 10,
				distance_km: row[T.distance_km],
				qc_max_gap_hours: CORE.qc[index][0],
				qc_max_step_speed_ms: CORE.qc[index][1],
				qc_observed_hour_coverage_pct: CORE.qc[index][2],
				observed_positions: row[T.observed_positions],
				qualifying_positions: row[T.qualifying_positions],
				interpolated_fraction: row[T.posterior_fraction_x1000] / 1000,
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
		toast(`Exported ${fmt(features.length)} physical-event tracks`);
	}

	function reproducibilityState() {
		return {
			generated_utc: new Date().toISOString(),
			atlas_version: CORE.meta.atlas_version,
			catalogue: CORE.meta.title,
			catalogue_coverage: {start: CORE.meta.coverage_start, end: CORE.meta.coverage_end},
			source_sha256: CORE.meta.core_catalogue_sha256,
			filters: {
				time_mode: state.timeMode,
				time_filter_definition: state.timeMode === 'dates' ? 'track overlaps active-date interval' : 'genesis year within interval',
				year_min: state.timeMode === 'years' ? state.yearMin : null,
				year_max: state.timeMode === 'years' ? state.yearMax : null,
				date_min: state.timeMode === 'dates' ? state.dateMin : null,
				date_max: state.timeMode === 'dates' ? state.dateMax : null,
				months: [...state.months].sort((a, b) => a - b),
				month_definition: state.monthMode,
				atlas_peak_classes: [...state.classes].sort((a, b) => a - b),
				analysis_metric: state.metric,
				minimum_fixed_catalogue_percentiles: {...state.percentileMins},
				continuity_screen: state.qc,
				genesis_region: state.genesisRegion === 'all' ? null : state.genesisRegion,
				lysis_region: state.lysisRegion === 'all' ? null : state.lysisRegion,
				bsiso1_phase_at_genesis: state.bsiso === 'all' ? null : Number(state.bsiso),
				enso_category_at_genesis: state.enso === 'all' ? null : Number(state.enso),
				track_crosses_state: state.stateIndex < 0 ? null : CORE.state_slugs[state.stateIndex],
				search: state.search || null
			},
			view: {map_layer: state.mapLayer, map_colour: state.mapColour, state_fill: state.stateFill, state_outlines: state.stateOutlines, matched_ibtracs_overlay: state.ibtracsOverlay, weather_field: state.weatherLayer, show_subset_tracks_with_weather: state.weatherTracks, evolution_metric: state.evolutionMetric, subset_profile_metrics: PROFILE_METRIC_KEYS.filter(key => state.profileMetrics.has(key))},
			selected_physical_event_id: state.selected == null ? null : atlasId(state.selected),
			matching_physical_event_ids: state.active.map(atlasId),
			url: window.location.href,
			caveats: [
				'Atlas-derived IMD-style class is not official IMD grade.',
				'Every v5.5.1 physical event is continuous at hourly resolution with physics resampled at every published centre.',
				'Cyclone names use credible NOAA IBTrACS v04r01 associations; state means use IMD 0.25-degree daily rainfall over active track dates.',
				'Interpolated positions meet the published v5.5.1 gap-support contract.'
			]
		};
	}

	function downloadQuery() {
		downloadBlob('monsoon-low-atlas-query.json', JSON.stringify(reproducibilityState(), null, 2), 'application/json');
		toast('Exported reproducibility recipe');
	}

	async function downloadSelectedFixes() {
		if (state.selected == null) { toast('Select a system first'); return; }
		await ensureDetail('Opening selected track points…');
		const index = state.selected;
		const row = track(index);
		const series = DETAIL.series[index];
		const points = paths.decoded[index];
		const interpolated = new Uint8Array(points.length);
		for (const range of CORE.posterior_runs[index] || []) interpolated.fill(1, Number(range[0]), Number(range[1]) + 1);
		const valueAt = (field, pointIndex, divisor) => series[S[field]][pointIndex] == null ? '' : series[S[field]][pointIndex] / (divisor || 1);
		const headers = ['physical_event_id', 'time_utc', 'hours_since_genesis', 'latitude', 'longitude', 'position_source', 'precip_24h_mm', 'vorticity_1e-5_s-1', 'circulation_wind_p95_125km_ms', 'mslp_hpa', 'pressure_deficit_hpa', 'q850_gkg', 'rh850_pct', 't850_k', 'atlas_class'];
		const rows = series[S.hours_since_genesis].map((hour, pointIndex) => {
			return [
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
			];
		});
		const csv = [headers.map(csvCell).join(','), ...rows.map(values => values.map(csvCell).join(','))].join('\n');
		downloadBlob(`monsoon-low-atlas-track-${atlasId(index)}-track-points.csv`, csv, 'text/csv;charset=utf-8');
		toast(`Exported ${fmt(rows.length)} hourly positions`);
	}

	function renderData() {
		$('#mlaCoverageText').textContent = `${CORE.meta.coverage_start} to ${CORE.meta.coverage_end}; complete through ${COMPLETE_END_YEAR}.`;
		$('#mlaBuildText').textContent = `Atlas ${CORE.meta.atlas_version}, built ${CORE.meta.built_utc}; ${fmt(CORE.meta.tracks)} physical events and ${fmt(CORE.meta.rows)} hourly positions.`;
		const release = $('#mlaReleaseSummary');
		if (release) release.href = CORE.meta.sources.release_summary;
		const zenodo = $('#mlaZenodoRecord');
		const zenodoHeader = $('#mlaZenodoHeader');
		const separator = $('#mlaZenodoSeparator');
		if (zenodo && atlasConfig.zenodo) {
			zenodo.href = atlasConfig.zenodo;
			zenodo.hidden = false;
			zenodoHeader.href = atlasConfig.zenodo;
			zenodoHeader.hidden = false;
			separator.hidden = false;
		}
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
		else if (state.tab === 'climatology') renderClimatology();
		else if (state.tab === 'extremes') renderExtremes();
		else if (state.tab === 'verification') renderVerification();
		else if (state.tab === 'data') renderData();
	}

	function showFatal(error) {
		console.error(error);
		const loading = $('#mlaLoading');
		loading.innerHTML = `<strong>Atlas could not be opened.</strong><span>${esc(error && error.message ? error.message : error)}</span>`;
		loading.style.borderColor = css('--mla-flag', '#a23d34');
	}

	try {
		setLoading('Decompressing the fast map, summaries and climate filters…');
		const boundaryPromise = loadBoundaryView();
		[CORE, CLIMATE] = await Promise.all([loadGzipJson('mla-core-gzip-b64'), loadGzipJson('mla-climate-gzip-b64')]);
		if (CLIMATE.track_count !== CORE.tracks.length || CLIMATE.bsiso.phase.length !== CORE.tracks.length || CLIMATE.enso.class.length !== CORE.tracks.length) throw new Error('Climate-filter asset does not match the catalogue');
		T = Object.fromEntries(CORE.track_fields.map((name, index) => [name, index]));
		S = Object.fromEntries(CORE.series_fields.map((name, index) => [name, index]));
		Q = Object.fromEntries(CORE.qc_fields.map((name, index) => [name, index]));
		setLoading('Building a spatial index for responsive track selection…');
		await new Promise(resolve => setTimeout(resolve, 0));
		buildPathRuntime();
		buildFallbackLabels();
		buildSearchIndex();
		buildEndpointRegions();
		buildFilterControls();
		readUrl();
		if (state.stateFill !== 'none') await ensureDetail();
		bindTabs();
		bindControls();
		bindMap();
		updateBoundarySourceText();
		syncControls();
		applyFilters({noUrl: true});
		updateTimeControls();
		if (state.weatherLayer !== 'none' && Number.isFinite(state.focusTimeMs)) syncWeatherToFocus();
		activateTab(state.tab, false);
		renderData();
		$('#mlaLoading').hidden = true;
		root.dataset.ready = 'true';
		writeUrl('replace');
		boundaryPromise.then(boundary => {
			if (!boundary) return;
			SOI_BOUNDARY = boundary;
			updateBoundarySourceText();
			mapScheduler.invalidate(MAP_DIRTY.BASE | MAP_DIRTY.WEATHER);
		});
		if (document.fonts && document.fonts.ready) {
			document.fonts.ready.then(() => {
				if (root.dataset.ready === 'true') renderCurrentPanel();
			});
		}
		window.addEventListener('resize', debounce(renderCurrentPanel, 150));
	} catch (error) {
		showFatal(error);
	}
})();
