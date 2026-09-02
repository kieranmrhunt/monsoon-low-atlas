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
		vorticity: ['#053061', '#175290', '#2a71b2', '#3f8ec0', '#6bacd1', '#9bc9e0', '#c2ddec', '#e0ecf3', '#f7f6f6', '#fbe5d8', '#fbccb4', '#f5aa89', '#e48066', '#d05548', '#ba2832', '#930e26', '#67001f'],
		humidity: ['#ffffd9', '#edf8b1', '#c7e9b4', '#7fcdbb', '#41b6c4', '#1d91c0', '#225ea8', '#253494', '#081d58']
	});
	const COMPOSITE_SECTION_DEFINITIONS = Object.freeze({
		relative_vorticity: {label: 'Relative vorticity', unit: '10⁻⁵ s⁻¹', minimum: -20, maximum: 20, palette: 'vorticity'},
		theta_e: {label: 'Equivalent potential temperature (θₑ)', unit: 'K', minimum: 330, maximum: 370, palette: 'vorticity', topPressure: 125},
		relative_humidity: {label: 'Relative humidity', unit: '%', minimum: 0, maximum: 100, palette: 'humidity'}
	});
	const SUBSET_COMPOSITE_DEFINITIONS = Object.freeze({
		relative_vorticity: {...COMPOSITE_SECTION_DEFINITIONS.relative_vorticity, kind: 'vertical_section'},
		theta_e: {...COMPOSITE_SECTION_DEFINITIONS.theta_e, kind: 'vertical_section'},
		relative_humidity: {...COMPOSITE_SECTION_DEFINITIONS.relative_humidity, kind: 'vertical_section'},
		precipitation: {label: 'ERA5 daily precipitation', unit: 'mm day⁻¹', minimum: 0, palette: 'terrain_r', kind: 'horizontal_precipitation'}
	});
	const COMPOSITE_TICK_FONT_SIZE = 12;
	const COMPOSITE_LABEL_FONT_SIZE = 13;

	let CORE;
	let CLIMATE;
	let DETAIL;
	let SECTIONS;
	let catalogueStartDate = '1940-01-01';
	let catalogueEndDate = '2025-12-31';
	let detailPromise;
	let sectionPromise;
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
	let exactSearchIndex = null;
	let exactSearchConflicts = [];
	let extremeScatterPoints = [];
	let forecastArchiveIndexPromise = null;
	let forecastDossierSerial = 0;
	const compositeCache = new Map();
	const compositePromises = new Map();
	const compositeErrors = new Map();
	const sectionMeanCache = new Map();
	const forecastOpportunityCache = new Map();

	const METRICS = {
		deficit: {label: 'pressure-deficit', title: 'Pressure deficit', pct: 'pct_deficit', raw: 'peak_deficit_x10', series: 'pressure_deficit_x10', divisor: 10, unit: 'hPa', colour: '#aa3d2d', direction: 1, peakMonth: 4},
		vort: {label: 'vorticity', title: 'Smoothed maximum vorticity', shortTitle: 'Smoothed max', pct: 'pct_vort', raw: 'peak_vort_x10', series: 'vort_smooth_x10', divisor: 10, unit: '10⁻⁵ s⁻¹', colour: '#233f78', direction: 1, zero: false, peakMonth: 1},
		maxVort: {label: 'maximum-vorticity', title: 'Maximum core vorticity', shortTitle: 'Core maximum', series: 'max_vort_x10', divisor: 10, unit: '10⁻⁵ s⁻¹', colour: '#354c88', direction: 1, zero: false},
		meanVort: {label: 'mean-vorticity', title: 'Mean core vorticity', shortTitle: 'Core mean', series: 'mean_vort_x10', divisor: 10, unit: '10⁻⁵ s⁻¹', colour: '#5265a0', direction: 1, zero: false},
		vort850: {label: 'vorticity-850', title: 'Mean 850-hPa vorticity', shortTitle: '850 hPa', series: 'vort850_x10', divisor: 10, unit: '10⁻⁵ s⁻¹', colour: '#193a6a', direction: 1, zero: false},
		vort700: {label: 'vorticity-700', title: 'Mean 700-hPa vorticity', shortTitle: '700 hPa', series: 'vort700_x10', divisor: 10, unit: '10⁻⁵ s⁻¹', colour: '#415a92', direction: 1, zero: false},
		vort500: {label: 'vorticity-500', title: 'Mean 500-hPa vorticity', shortTitle: '500 hPa', series: 'vort500_x10', divisor: 10, unit: '10⁻⁵ s⁻¹', colour: '#6c79ae', direction: 1, zero: false},
		vortDeep: {label: 'deep-vorticity', title: 'Deep-layer mean vorticity', shortTitle: 'Deep mean', series: 'vort_deep_x10', divisor: 10, unit: '10⁻⁵ s⁻¹', colour: '#2f4f86', direction: 1, zero: false},
		wind: {label: 'circulation-wind', title: 'Circulation wind', shortTitle: 'Circulation', pct: 'pct_wind', raw: 'peak_wind_x10', series: 'circulation_wind_x10', divisor: 10, unit: 'm s⁻¹', colour: '#08736f', direction: 1, peakMonth: 2},
		maxWind: {label: 'maximum-wind', title: 'Maximum 10-m wind', shortTitle: '10-m maximum', series: 'max_wind_x10', divisor: 10, unit: 'm s⁻¹', colour: '#006b68', direction: 1},
		meanWind: {label: 'mean-wind', title: 'Mean 10-m wind', shortTitle: '10-m mean', series: 'mean_wind_x10', divisor: 10, unit: 'm s⁻¹', colour: '#3e8c85', direction: 1},
		bgU: {label: 'background-zonal-wind', title: 'Background zonal wind', shortTitle: 'Background u', series: 'background_u_x10', divisor: 10, unit: 'm s⁻¹', colour: '#557c7b', direction: 1, zero: false},
		bgV: {label: 'background-meridional-wind', title: 'Background meridional wind', shortTitle: 'Background v', series: 'background_v_x10', divisor: 10, unit: 'm s⁻¹', colour: '#799795', direction: 1, zero: false},
		mslp: {label: 'MSLP-depth', title: 'Minimum MSLP', shortTitle: 'Minimum MSLP', pct: 'pct_mslp_depth', raw: 'min_mslp_x10', series: 'mslp_x10', divisor: 10, unit: 'hPa', colour: '#64224f', direction: -1, zero: false, peakMonth: 3},
		ringMslp: {label: 'environmental-mslp', title: 'Environmental MSLP (P60)', shortTitle: 'Ring MSLP P60', series: 'ring_mslp_x10', divisor: 10, unit: 'hPa', colour: '#7e506b', direction: -1, zero: false},
		isobars: {label: 'closed-isobars', title: 'Closed 2-hPa isobars', shortTitle: 'Closed isobars', series: 'closed_isobars', divisor: 1, unit: 'count', colour: '#a85e4f', direction: 1},
		rain1: {label: 'hourly-rainfall', title: 'Hourly precipitation', shortTitle: 'Hourly', series: 'precip1_x100', divisor: 100, unit: 'mm h⁻¹', colour: '#d2a025', direction: 1},
		rain: {label: 'rainfall', title: '24 h precipitation', pct: 'pct_precip', raw: 'peak_precip_x10', series: 'precip24_x10', divisor: 10, unit: 'mm', colour: '#c3931d', direction: 1, peakMonth: 0},
		q: {label: 'q850', title: '850-hPa specific humidity', shortTitle: 'q850', raw: 'peak_q850_x10', series: 'q850_x10', divisor: 10, unit: 'g kg⁻¹', colour: '#4360a0', direction: 1, zero: false},
		q700: {label: 'q700', title: '700-hPa specific humidity', shortTitle: 'q700', series: 'q700_x10', divisor: 10, unit: 'g kg⁻¹', colour: '#5d73ae', direction: 1, zero: false},
		q500: {label: 'q500', title: '500-hPa specific humidity', shortTitle: 'q500', series: 'q500_x10', divisor: 10, unit: 'g kg⁻¹', colour: '#7888bb', direction: 1, zero: false},
		qDeep: {label: 'deep-q', title: 'Deep-layer specific humidity', shortTitle: 'q deep', series: 'q_deep_x10', divisor: 10, unit: 'g kg⁻¹', colour: '#2f518f', direction: 1, zero: false},
		rh: {label: 'RH850', title: '850-hPa relative humidity', shortTitle: 'RH850', raw: 'peak_rh850_x10', series: 'rh850_x10', divisor: 10, unit: '%', colour: '#477a4a', direction: 1, zero: false},
		rh700: {label: 'RH700', title: '700-hPa relative humidity', shortTitle: 'RH700', series: 'rh700_x10', divisor: 10, unit: '%', colour: '#5f8b60', direction: 1, zero: false},
		rh500: {label: 'RH500', title: '500-hPa relative humidity', shortTitle: 'RH500', series: 'rh500_x10', divisor: 10, unit: '%', colour: '#789d77', direction: 1, zero: false},
		rhDeep: {label: 'deep-RH', title: 'Deep-layer relative humidity', shortTitle: 'RH deep', series: 'rh_deep_x10', divisor: 10, unit: '%', colour: '#356b3e', direction: 1, zero: false},
		t: {label: 'T850', title: '850-hPa temperature', shortTitle: 'T850', series: 't850_x10', divisor: 10, unit: 'K', colour: '#c9631b', direction: 1, zero: false},
		t700: {label: 'T700', title: '700-hPa temperature', shortTitle: 'T700', series: 't700_x10', divisor: 10, unit: 'K', colour: '#d17d31', direction: 1, zero: false},
		t500: {label: 'T500', title: '500-hPa temperature', shortTitle: 'T500', series: 't500_x10', divisor: 10, unit: 'K', colour: '#d99a55', direction: 1, zero: false},
		tAnom850: {label: 'T850-inner-anomaly', title: '850-hPa inner-minus-environment temperature', shortTitle: 'T850 inner anomaly', series: 't850_inner_anomaly_x100', divisor: 100, unit: 'K', colour: '#aa3d2d', direction: 1, zero: false},
		tAnom700: {label: 'T700-inner-anomaly', title: '700-hPa inner-minus-environment temperature', shortTitle: 'T700 inner anomaly', series: 't700_inner_anomaly_x100', divisor: 100, unit: 'K', colour: '#b85a42', direction: 1, zero: false},
		tAnom500: {label: 'T500-inner-anomaly', title: '500-hPa inner-minus-environment temperature', shortTitle: 'T500 inner anomaly', series: 't500_inner_anomaly_x100', divisor: 100, unit: 'K', colour: '#c47761', direction: 1, zero: false},
		tAnomDeep: {label: 'deep-inner-temperature-anomaly', title: 'Deep inner-minus-environment temperature', shortTitle: 'Deep inner anomaly', series: 't_inner_anomaly_deep_x100', divisor: 100, unit: 'K', colour: '#8f2938', direction: 1, zero: false},
		t850t500: {label: 'T850-minus-T500', title: '850–500-hPa temperature difference', shortTitle: 'T850−T500', series: 't850_minus_t500_x10', divisor: 10, unit: 'K', colour: '#9d5237', direction: 1, zero: false},
		t700t500: {label: 'T700-minus-T500', title: '700–500-hPa temperature difference', shortTitle: 'T700−T500', series: 't700_minus_t500_x10', divisor: 10, unit: 'K', colour: '#b26b4d', direction: 1, zero: false},
		orography: {label: 'orography', title: 'Centre orography', shortTitle: 'Orography', series: 'orography_m', divisor: 1, unit: 'm', colour: '#8b6b43', direction: 1},
		land: {label: 'land-fraction', title: 'Centre land fraction', shortTitle: 'Land fraction', series: 'land_fraction_pct_x10', divisor: 10, unit: '%', colour: '#74733f', direction: 1}
	};
	const FILTER_METRIC_KEYS = ['deficit', 'vort', 'wind', 'mslp', 'rain'];
	const EVOLUTION_METRIC_GROUPS = Object.freeze([
		{label: 'Vorticity', keys: ['vort', 'maxVort', 'meanVort', 'vort850', 'vort700', 'vort500', 'vortDeep']},
		{label: 'Wind and pressure', keys: ['wind', 'maxWind', 'meanWind', 'bgU', 'bgV', 'deficit', 'mslp', 'ringMslp', 'isobars']},
		{label: 'Precipitation', keys: ['rain1', 'rain']},
		{label: 'Moisture', keys: ['q', 'q700', 'q500', 'qDeep', 'rh', 'rh700', 'rh500', 'rhDeep']},
		{label: 'Temperature and structure', keys: ['t', 't700', 't500', 'tAnom850', 'tAnom700', 'tAnom500', 'tAnomDeep', 't850t500', 't700t500']},
		{label: 'Surface context', keys: ['orography', 'land']}
	]);
	const PROFILE_METRIC_KEYS = EVOLUTION_METRIC_GROUPS.flatMap(group => group.keys);
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
		dateMin: '1940-01-01',
		dateMax: '2025-12-31',
		months: new Set(SEASON_MONTHS.all),
		monthMode: 'active',
		classes: new Set([1, 2, 3, 4, 5, 6]),
		metric: 'deficit',
		percentileMins: {deficit: 0, vort: 0, wind: 0, mslp: 0, rain: 0},
		match: 'any',
		qc: 'any',
		genesisRegion: 'all',
		lysisRegion: 'all',
		bsiso: 'all',
		mjo: 'all',
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
		extremeX: 'deficit',
		extremeY: 'wind',
		climatologyMeasure: 'systems',
		seasonalMode: 'count',
		climateIndex: 'mjo',
		subsetSectionVariable: 'relative_vorticity',
		sectionReference: null,
		sectionReferenceLabel: '',
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
		const orphanedPointFocus = state.focusSource === 'point' && state.selected == null;
		if (!CORE || !paths || orphanedPointFocus || !Number.isFinite(state.focusTimeMs) || state.focusStartMs !== state.focusEndMs) return state.active;
		const selectedPointFocus = state.focusSource === 'point' && state.selected != null;
		const candidateIndexes = selectedPointFocus && !state.activeBit[state.selected]
			? [state.selected, ...state.active]
			: state.active;
		const candidates = [];
		for (const trackIndex of candidateIndexes) {
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

	async function loadVisitCounter() {
		ensureAtlasConfig();
		const counter = atlasConfig.visitCounter;
		const container = $('#mlaVisitCounter');
		const valueNode = $('#mlaVisitCount');
		if (!counter || !counter.endpoint || !container || !valueNode) return;
		try {
			const endpoint = new URL(counter.endpoint);
			if (counter.productionHost && window.location.hostname !== counter.productionHost) endpoint.searchParams.set('readOnly', 'true');
			const response = await fetch(endpoint, {cache: 'no-store', credentials: 'omit', referrerPolicy: 'no-referrer'});
			if (!response.ok) return;
			const count = Number((await response.json()).value);
			if (!Number.isSafeInteger(count) || count < 0) return;
			valueNode.textContent = fmt(count);
			container.hidden = false;
		} catch (_) {
			// A third-party counter must never delay or impair the atlas.
		}
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

	async function ensureForecastArchiveIndex() {
		ensureAtlasConfig();
		if (!atlasConfig.forecastBase) throw new Error('Forecast archive is not configured');
		if (!forecastArchiveIndexPromise) {
			const base = String(atlasConfig.forecastBase).replace(/\/$/, '');
			forecastArchiveIndexPromise = fetchJsonAsset(`${base}/archive-manifest.json.gz`, 'forecast archive index').then(value => {
				if (value.schema !== 'mla-forecast-manifest-v1') throw new Error('Unsupported forecast archive index');
				return value;
			});
		}
		return forecastArchiveIndexPromise;
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

	function decodeInt16Base64(encoded) {
		const binary = atob(encoded);
		const bytes = new Uint8Array(binary.length);
		for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
		if (new Uint8Array(new Uint16Array([1]).buffer)[0] === 1) return new Int16Array(bytes.buffer);
		const view = new DataView(bytes.buffer);
		const values = new Int16Array(bytes.byteLength / 2);
		for (let index = 0; index < values.length; index++) values[index] = view.getInt16(index * 2, true);
		return values;
	}

	async function ensureSections(reason) {
		if (SECTIONS) return SECTIONS;
		if (!sectionPromise) {
			sectionPromise = (async () => {
				ensureAtlasConfig();
				if (!atlasConfig.sections) throw new Error('Filtered-subset composites are not configured');
				if (reason) toast(reason);
				const asset = await fetchJsonAsset(atlasConfig.sections, 'filtered-subset composites');
				if (asset.schema !== 'monsoon-low-atlas-subset-composites-v2' || asset.track_count !== CORE.tracks.length) throw new Error('Subset-composite asset does not match the catalogue');
				for (let index = 0; index < CORE.tracks.length; index++) if (Number(asset.track_ids[index]) !== atlasId(index)) throw new Error('Subset-composite event order does not match the catalogue');
				for (const field of Object.values(asset.fields)) {
					field.values = decodeInt16Base64(field.data_b64);
					delete field.data_b64;
				}
				SECTIONS = asset;
				sectionMeanCache.clear();
				return asset;
			})();
		}
		return sectionPromise;
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
		if (state.mjo !== 'all' && CLIMATE.mjo.phase[index] !== Number(state.mjo)) return false;
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
		return [state.timeMode, state.yearMin, state.yearMax, state.dateMin, state.dateMax, [...state.months].sort((a, b) => a - b).join('.'), state.monthMode, [...state.classes].sort().join('.'), state.metric, percentiles, state.match, state.qc, state.genesisRegion, state.lysisRegion, state.bsiso, state.mjo, state.enso, state.stateIndex, state.stateMin, state.search].join('|');
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

	function resolveExactSystemSearch(search) {
		if (!search.query || search.exactDateStart != null || search.exactYear != null) return null;
		if (search.exactTrackId != null) {
			const index = CORE.tracks.findIndex((unused, candidate) => atlasId(candidate) === search.exactTrackId);
			return index >= 0 ? index : null;
		}
		const query = search.query.replace(/^cyclone\s+/, '').trim();
		const matches = [];
		for (let index = 0; index < CORE.tracks.length; index++) {
			const name = officialName(index).toLowerCase();
			if ((name && name === query) || systemLabel(index).toLowerCase() === search.query) matches.push(index);
		}
		return matches.length === 1 ? matches[0] : null;
	}

	function selectedMonthLabel() {
		const values = [...state.months].sort((a, b) => a - b);
		const preset = Object.entries(SEASON_MONTHS).find(([, months]) => months.length === values.length && months.every(month => state.months.has(month)));
		return preset ? preset[0].toUpperCase() : values.map(month => MONTHS[month - 1]).join(', ');
	}

	function filterFailures(index, search, options) {
		const failures = [];
		const ignoredClimate = options && options.ignoreClimate;
		const row = track(index);
		const minimumActive = state.timeMode === 'dates' ? Date.parse(`${state.dateMin}T00:00:00Z`) : NaN;
		const maximumActive = state.timeMode === 'dates' ? Date.parse(`${state.dateMax}T23:59:59.999Z`) : NaN;
		if (state.timeMode === 'dates') {
			if (row[T.end_ms] < minimumActive || row[T.start_ms] > maximumActive) failures.push({key: 'period', label: `Active dates ${state.dateMin}–${state.dateMax}`});
		} else if (row[T.start_year] < state.yearMin || row[T.start_year] > state.yearMax) failures.push({key: 'period', label: `Genesis years ${state.yearMin}–${state.yearMax}`});
		if (search.exactDateStart == null && !monthPass(index)) failures.push({key: 'months', label: `Months: ${selectedMonthLabel()}`});
		if (!state.classes.has(row[T.category])) failures.push({key: 'classes', label: `Peak classes: ${[...state.classes].sort().map(value => CLASS_SHORT[value]).join(', ')}`});
		for (const key of FILTER_METRIC_KEYS) if (percentileMetric(index, key) < state.percentileMins[key]) failures.push({key: `pct-${key}`, label: `${METRICS[key].title} ≥ P${state.percentileMins[key]}`});
		if (!matchPass(index)) failures.push({key: 'match', label: `IBTrACS: ${$('#mlaMatch').selectedOptions[0].textContent}`});
		if (!qcPass(index)) failures.push({key: 'qc', label: 'Continuity screen'});
		if (state.genesisRegion !== 'all' && !(genesisRegions[index] & ENDPOINT_REGION_BITS[state.genesisRegion])) failures.push({key: 'genesis', label: `Genesis: ${ENDPOINT_REGION_LABELS[state.genesisRegion]}`});
		if (state.lysisRegion !== 'all' && !(lysisRegions[index] & ENDPOINT_REGION_BITS[state.lysisRegion])) failures.push({key: 'lysis', label: `Lysis: ${ENDPOINT_REGION_LABELS[state.lysisRegion]}`});
		if (ignoredClimate !== 'bsiso' && state.bsiso !== 'all' && CLIMATE.bsiso.phase[index] !== Number(state.bsiso)) failures.push({key: 'bsiso', label: `BSISO: ${$('#mlaBsiso').selectedOptions[0].textContent}`});
		if (ignoredClimate !== 'mjo' && state.mjo !== 'all' && CLIMATE.mjo.phase[index] !== Number(state.mjo)) failures.push({key: 'mjo', label: `MJO: ${$('#mlaMjo').selectedOptions[0].textContent}`});
		if (ignoredClimate !== 'enso' && state.enso !== 'all' && CLIMATE.enso.class[index] !== Number(state.enso)) failures.push({key: 'enso', label: `ENSO: ${$('#mlaEnso').selectedOptions[0].textContent}`});
		if (!statePass(index)) failures.push({key: 'state', label: `Crosses ${CORE.states[state.stateIndex]}`});
		if (!(options && options.ignoreSearch) && search.query) {
			if (search.exactDateStart != null && (row[T.end_ms] < search.exactDateStart || row[T.start_ms] > search.exactDateEnd)) failures.push({key: 'search', label: `Active on ${search.exactDate}`});
			else if (search.exactDateStart == null && search.exactYear != null && row[T.start_year] !== search.exactYear) failures.push({key: 'search', label: `Search: ${search.query}`});
			else if (search.exactDateStart == null && search.exactYear == null && search.exactTrackId != null && atlasId(index) !== search.exactTrackId) failures.push({key: 'search', label: `Search: ${search.query}`});
			else if (search.exactDateStart == null && search.exactYear == null && search.exactTrackId == null && !CORE.search[index].includes(search.query)) failures.push({key: 'search', label: `Search: ${search.query}`});
		}
		return failures;
	}

	function clearFilter(key) {
		if (key === 'period') {
			state.yearMin = Number(catalogueStartDate.slice(0, 4)); state.yearMax = Number(catalogueEndDate.slice(0, 4));
			state.dateMin = catalogueStartDate; state.dateMax = catalogueEndDate;
		} else if (key === 'months') state.months = new Set(SEASON_MONTHS.all);
		else if (key === 'month-mode') state.monthMode = 'active';
		else if (key === 'classes') state.classes = new Set([1, 2, 3, 4, 5, 6]);
		else if (key.startsWith('pct-')) state.percentileMins[key.slice(4)] = 0;
		else if (key === 'match') state.match = 'any';
		else if (key === 'qc') state.qc = 'any';
		else if (key === 'genesis') state.genesisRegion = 'all';
		else if (key === 'lysis') state.lysisRegion = 'all';
		else if (key === 'bsiso') state.bsiso = 'all';
		else if (key === 'mjo') state.mjo = 'all';
		else if (key === 'enso') state.enso = 'all';
		else if (key === 'state') { state.stateIndex = -1; state.stateMin = 0; }
		else if (key === 'search') state.search = '';
	}

	function activeFilterDescriptors() {
		const descriptors = [];
		if (state.timeMode === 'dates' ? state.dateMin !== catalogueStartDate || state.dateMax !== catalogueEndDate : state.yearMin !== Number(catalogueStartDate.slice(0, 4)) || state.yearMax !== Number(catalogueEndDate.slice(0, 4))) descriptors.push({key: 'period', label: state.timeMode === 'dates' ? `${state.dateMin}–${state.dateMax}` : `${state.yearMin}–${state.yearMax}`, group: 'context'});
		if (state.months.size !== 12) descriptors.push({key: 'months', label: `Months: ${selectedMonthLabel()}`, group: 'context'});
		if (state.monthMode !== 'active') descriptors.push({key: 'month-mode', label: `Month: ${state.monthMode}`, group: 'context'});
		if (state.classes.size !== 6) descriptors.push({key: 'classes', label: `Class: ${[...state.classes].sort().map(value => CLASS_SHORT[value]).join(',')}`, group: 'context'});
		for (const key of FILTER_METRIC_KEYS) if (state.percentileMins[key] > 0) descriptors.push({key: `pct-${key}`, label: `${METRICS[key].shortTitle || METRICS[key].title} ≥ P${state.percentileMins[key]}`, group: 'percentile'});
		if (state.match !== 'any') descriptors.push({key: 'match', label: `IBTrACS: ${$('#mlaMatch').selectedOptions[0].textContent}`, group: 'context'});
		if (state.genesisRegion !== 'all') descriptors.push({key: 'genesis', label: `Genesis: ${ENDPOINT_REGION_LABELS[state.genesisRegion]}`, group: 'context'});
		if (state.lysisRegion !== 'all') descriptors.push({key: 'lysis', label: `Lysis: ${ENDPOINT_REGION_LABELS[state.lysisRegion]}`, group: 'context'});
		if (state.bsiso !== 'all') descriptors.push({key: 'bsiso', label: `BSISO: ${$('#mlaBsiso').selectedOptions[0].textContent}`, group: 'context'});
		if (state.mjo !== 'all') descriptors.push({key: 'mjo', label: `MJO: ${$('#mlaMjo').selectedOptions[0].textContent}`, group: 'context'});
		if (state.enso !== 'all') descriptors.push({key: 'enso', label: `ENSO: ${$('#mlaEnso').selectedOptions[0].textContent}`, group: 'context'});
		if (state.stateIndex >= 0) descriptors.push({key: 'state', label: `Crosses: ${CORE.states[state.stateIndex]}`, group: 'context'});
		if (state.search) descriptors.push({key: 'search', label: `${exactSearchIndex == null ? 'Search' : 'Opened'}: ${state.search}`, group: 'context'});
		return descriptors;
	}

	function applyFilters(options) {
		if (!CORE) return;
		const active = [];
		const bits = new Uint8Array(CORE.tracks.length);
		const search = parsedSearch();
		exactSearchIndex = resolveExactSystemSearch(search);
		for (let index = 0; index < CORE.tracks.length; index++) {
			if (filterFailures(index, search, {ignoreSearch: exactSearchIndex != null}).length) continue;
			bits[index] = 1;
			active.push(index);
		}
		state.active = active;
		state.activeBit = bits;
		if (root.dataset.ready !== 'true') setLoading('Rendering filter state and time controls…');
		if (exactSearchIndex != null) state.selected = exactSearchIndex;
		exactSearchConflicts = state.selected != null && !bits[state.selected] ? filterFailures(state.selected, search, {ignoreSearch: exactSearchIndex != null}) : [];
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
		if (root.dataset.ready !== 'true') setLoading('Rendering the requested atlas panel…');
		renderCurrentPanel();
		if (root.dataset.ready !== 'true') setLoading('Preparing the initial map view…');
		const autoFitKey = filterSignature();
		const narrowTime = state.timeMode === 'dates'
			? Date.parse(state.dateMax) - Date.parse(state.dateMin) <= 3 * 366 * 86400000
			: state.yearMax - state.yearMin <= 3;
		if (!(options && options.noAutoFit) && state.selected == null && active.length > 0 && active.length <= 80 && narrowTime && lastAutoFitSignature !== autoFitKey) {
			lastAutoFitSignature = autoFitKey;
			requestAnimationFrame(() => fitCohort({quiet: true}));
		}
		if (!(options && options.noUrl)) writeUrl('replace');
	}

	function updateFilterReadout() {
		$('#mlaResultCount').textContent = `${fmt(state.active.length)} of ${fmt(CORE.tracks.length)} systems`;
		const descriptors = activeFilterDescriptors();
		$('#mlaActiveFilters').innerHTML = descriptors.map(item => `<button class="mla-chip mla-filter-chip" type="button" data-clear-filter="${esc(item.key)}" aria-label="Remove ${esc(item.label)} filter">${esc(item.label)}<span aria-hidden="true">×</span></button>`).join('');
		const contextCount = descriptors.filter(item => item.group === 'context' && item.key !== 'search').length;
		const percentileCount = descriptors.filter(item => item.group === 'percentile').length;
		$('#mlaContextFilterCount').textContent = contextCount ? String(contextCount) : '';
		$('#mlaPercentileFilterCount').textContent = percentileCount ? String(percentileCount) : '';
		const notice = $('#mlaFilterNotice');
		const noticeText = $('#mlaFilterNoticeText');
		const clear = $('#mlaClearConflicts');
		if (state.selected != null && !state.activeBit[state.selected]) {
			const labels = exactSearchConflicts.slice(0, 3).map(item => item.label);
			const extra = exactSearchConflicts.length > 3 ? ` and ${exactSearchConflicts.length - 3} more` : '';
			noticeText.textContent = `${systemLabel(state.selected)} remains pinned outside the current subset because of ${labels.join('; ')}${extra}.`;
			clear.hidden = !exactSearchConflicts.length;
			notice.hidden = false;
		} else if (exactSearchIndex != null) {
			noticeText.textContent = `${systemLabel(exactSearchIndex)} opened globally; the surrounding subset filters are unchanged.`;
			clear.hidden = true;
			notice.hidden = false;
		} else notice.hidden = true;
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
		$('#mlaEvolutionMetric').innerHTML = EVOLUTION_METRIC_GROUPS.map(group => `<optgroup label="${esc(group.label)}">${group.keys.map(key => {
			const definition = METRICS[key];
			return `<option value="${key}">${esc(`${definition.title} (${definition.unit})`)}</option>`;
		}).join('')}</optgroup>`).join('');
		$('#mlaProfileMetrics').innerHTML = EVOLUTION_METRIC_GROUPS.map(group => `<div class="mla-profile-metric-group" role="group" aria-label="${esc(group.label)}"><span class="mla-label">${esc(group.label)}</span><div class="mla-chip-row">${group.keys.map(key => {
			const definition = METRICS[key];
			return `<button class="mla-chip" type="button" data-profile-metric="${key}" aria-pressed="${state.profileMetrics.has(key)}" title="${esc(definition.title)}">${esc(definition.shortTitle || definition.title)}</button>`;
		}).join('')}</div></div>`).join('');
		const stateSelect = $('#mlaState');
		CORE.states.forEach((name, index) => {
			const option = document.createElement('option');
			option.value = String(index);
			option.textContent = name;
			stateSelect.appendChild(option);
		});
		const matchField = $('#mlaMatch').closest('.mla-field');
		if (matchField) matchField.hidden = !CORE.crosswalk.some(Boolean);
		const namedSuggestions = new Map();
		for (let index = 0; index < CORE.tracks.length; index++) {
			const name = officialName(index);
			if (name && !namedSuggestions.has(name.toLowerCase())) namedSuggestions.set(name.toLowerCase(), {name, id: atlasId(index)});
		}
		$('#mlaSearchSuggestions').innerHTML = [...namedSuggestions.values()].sort((first, second) => first.name.localeCompare(second.name)).map(item => `<option value="Cyclone ${esc(item.name)}">physical event ${item.id}</option>`).join('');
		const relationshipOptions = EXTREME_RELATIONSHIP_KEYS.map(key => `<option value="${key}">${esc(`${EXTREMES[key].label} (${EXTREMES[key].unit})`)}</option>`).join('');
		$('#mlaExtremeX').innerHTML = relationshipOptions;
		$('#mlaExtremeY').innerHTML = relationshipOptions;
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
		$('#mlaMjo').value = state.mjo;
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
		$('#mlaExtremeX').value = state.extremeX;
		$('#mlaExtremeY').value = state.extremeY;
		$('#mlaClimatologyMeasure').value = state.climatologyMeasure;
		$('#mlaSeasonalMode').value = state.seasonalMode;
		$('#mlaClimateIndex').value = state.climateIndex;
		$('#mlaSubsetSectionVariable').value = state.subsetSectionVariable;
		$('#mlaEvolutionMetric').value = state.evolutionMetric;
		$('#mlaProfileMetricCount').textContent = `${fmt(state.profileMetrics.size)} shown`;
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
		state.dateMin = catalogueStartDate;
		state.dateMax = catalogueEndDate;
		state.months = new Set(SEASON_MONTHS.all);
		state.monthMode = 'active';
		state.classes = new Set([1, 2, 3, 4, 5, 6]);
		state.metric = 'deficit';
		state.percentileMins = {deficit: 0, vort: 0, wind: 0, mslp: 0, rain: 0};
		state.match = 'any';
		state.qc = 'any';
		state.genesisRegion = 'all';
		state.lysisRegion = 'all';
		state.bsiso = 'all';
		state.mjo = 'all';
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
			state.dateMin = state.yearMin === Number(catalogueStartDate.slice(0, 4)) ? catalogueStartDate : `${state.yearMin}-01-01`;
			state.dateMax = state.yearMax === Number(catalogueEndDate.slice(0, 4)) ? catalogueEndDate : `${state.yearMax}-12-31`;
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
			state.dateMin = event.target.value || catalogueStartDate;
			if (state.dateMin > state.dateMax) {
				state.dateMax = state.dateMin;
				$('#mlaDateMax').value = state.dateMax;
			}
			applyFilters();
		});
		$('#mlaDateMax').addEventListener('change', event => {
			state.dateMax = event.target.value || catalogueEndDate;
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
		$('#mlaMjo').addEventListener('change', event => { state.mjo = event.target.value; applyFilters(); });
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
		$('#mlaActiveFilters').addEventListener('click', event => {
			const button = event.target.closest('[data-clear-filter]');
			if (!button) return;
			clearFilter(button.dataset.clearFilter);
			syncControls();
			applyFilters();
		});
		$('#mlaClearConflicts').addEventListener('click', () => {
			for (const key of new Set(exactSearchConflicts.map(item => item.key))) clearFilter(key);
			syncControls();
			applyFilters();
		});
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
		$('#mlaExtremeMetric').addEventListener('change', event => { state.extremeMetric = event.target.value; renderExtremes(); writeUrl('replace'); });
		$('#mlaExtremeEligibility').addEventListener('change', event => { state.extremeEligibility = event.target.value; renderExtremes(); });
		$('#mlaExtremeX').addEventListener('change', event => { state.extremeX = event.target.value; renderExtremes(); writeUrl('replace'); });
		$('#mlaExtremeY').addEventListener('change', event => { state.extremeY = event.target.value; renderExtremes(); writeUrl('replace'); });
		$('#mlaClimatologyMeasure').addEventListener('change', event => { state.climatologyMeasure = event.target.value; renderClimatology(); writeUrl('replace'); });
		$('#mlaSeasonalMode').addEventListener('change', event => { state.seasonalMode = event.target.value; renderClimatology(); writeUrl('replace'); });
		$('#mlaClimateIndex').addEventListener('change', event => { state.climateIndex = event.target.value; renderClimatology(); writeUrl('replace'); });
		$('#mlaSubsetSectionVariable').addEventListener('change', event => { state.subsetSectionVariable = event.target.value; renderSubsetSections(); writeUrl('replace'); });
		$('#mlaPinSectionReference').addEventListener('click', () => {
			state.sectionReference = state.active.slice();
			const descriptors = activeFilterDescriptors().filter(item => item.key !== 'search');
			state.sectionReferenceLabel = descriptors.length ? descriptors.slice(0, 3).map(item => item.label).join(' · ') + (descriptors.length > 3 ? ` · +${descriptors.length - 3}` : '') : 'All current filters';
			renderSubsetSections();
		});
		$('#mlaClearSectionReference').addEventListener('click', () => {
			state.sectionReference = null;
			state.sectionReferenceLabel = '';
			renderSubsetSections();
		});
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
		$('#mlaFilterDock').hidden = name === 'data' || name === 'forecast';
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
		if (months.join(',') !== SEASON_MONTHS.all.join(',')) parameters.set('months', months.join(','));
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
		if (state.mjo !== 'all') parameters.set('mjo', state.mjo);
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
		if (state.climatologyMeasure !== 'systems') parameters.set('climmeasure', state.climatologyMeasure);
		if (state.seasonalMode !== 'count') parameters.set('seasonview', state.seasonalMode);
		if (state.climateIndex !== 'mjo') parameters.set('climateplot', state.climateIndex);
		if (state.subsetSectionVariable !== 'relative_vorticity') parameters.set('subsetsection', state.subsetSectionVariable);
		if (state.extremeMetric !== 'duration') parameters.set('extreme', state.extremeMetric);
		if (state.extremeX !== 'deficit') parameters.set('extx', state.extremeX);
		if (state.extremeY !== 'wind') parameters.set('exty', state.extremeY);
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
		const validTabs = new Set(['explore', 'forecast', 'climatology', 'extremes', 'data']);
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
		if (FILTER_METRIC_KEYS.includes(parameters.get('metric'))) state.metric = parameters.get('metric');
		FILTER_METRIC_KEYS.forEach(key => {
			state.percentileMins[key] = clamp(Number(parameters.get(`p${key}`)) || 0, 0, 100);
		});
		if (parameters.has('pmin') && !parameters.has(`p${state.metric}`)) state.percentileMins[state.metric] = clamp(Number(parameters.get('pmin')) || 0, 0, 100);
		if (['any', 'unmatched', 'high', 'credible', 'named'].includes(parameters.get('match'))) state.match = parameters.get('match');
		state.genesisRegion = Object.hasOwn(ENDPOINT_REGION_LABELS, parameters.get('genesis')) ? parameters.get('genesis') : 'all';
		state.lysisRegion = Object.hasOwn(ENDPOINT_REGION_LABELS, parameters.get('lysis')) ? parameters.get('lysis') : 'all';
		state.bsiso = ['-1', '0', '1', '2', '3', '4', '5', '6', '7', '8'].includes(parameters.get('bsiso')) ? parameters.get('bsiso') : 'all';
		state.mjo = ['-1', '0', '1', '2', '3', '4', '5', '6', '7', '8'].includes(parameters.get('mjo')) ? parameters.get('mjo') : 'all';
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
		if (PROFILE_METRIC_KEYS.includes(parameters.get('evolve'))) state.evolutionMetric = parameters.get('evolve');
		if (['era5', 'imerg'].includes(parameters.get('compositeprecip'))) state.compositePrecipSource = parameters.get('compositeprecip');
		if (Object.hasOwn(COMPOSITE_SECTION_DEFINITIONS, parameters.get('compositesection'))) state.compositeSectionVariable = parameters.get('compositesection');
		if (['systems', 'rate', 'system_days'].includes(parameters.get('climmeasure'))) state.climatologyMeasure = parameters.get('climmeasure');
		if (['count', 'share'].includes(parameters.get('seasonview'))) state.seasonalMode = parameters.get('seasonview');
		if (['mjo', 'bsiso', 'enso'].includes(parameters.get('climateplot'))) state.climateIndex = parameters.get('climateplot');
		if (Object.hasOwn(SUBSET_COMPOSITE_DEFINITIONS, parameters.get('subsetsection'))) state.subsetSectionVariable = parameters.get('subsetsection');
		if (Object.hasOwn(EXTREMES, parameters.get('extreme'))) state.extremeMetric = parameters.get('extreme');
		if (EXTREME_RELATIONSHIP_KEYS.includes(parameters.get('extx'))) state.extremeX = parameters.get('extx');
		if (EXTREME_RELATIONSHIP_KEYS.includes(parameters.get('exty'))) state.extremeY = parameters.get('exty');
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
		if (!(options && options.keepWeather)) {
			state.weatherLayer = 'none';
			const weatherSelect = $('#mlaWeatherLayer');
			if (weatherSelect) weatherSelect.value = 'none';
		}
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
		const climatologyTotals = new Float64Array(CORE.states.length);
		const climatologyWeights = new Float64Array(CORE.states.length);
		let systemDays = 0;
		for (const index of indexes) {
			const days = Math.max(1, Number(track(index)[T.rain_days]) || 1);
			systemDays += days;
			const values = DETAIL.state_mean_x10[index] || [];
			const climatologyValues = DETAIL.state_climatology_mean_x10 && DETAIL.state_climatology_mean_x10[index] || [];
			for (let stateIndex = 0; stateIndex < CORE.states.length; stateIndex++) {
				const value = Number(values[stateIndex]);
				if (value >= 0 && Number.isFinite(value)) {
					totals[stateIndex] += value * days;
					weights[stateIndex] += days;
				}
				const climatologyValue = Number(climatologyValues[stateIndex]);
				if (climatologyValue >= 0 && Number.isFinite(climatologyValue)) {
					climatologyTotals[stateIndex] += climatologyValue * days;
					climatologyWeights[stateIndex] += days;
				}
			}
		}
		const means = Array.from(totals, (total, index) => weights[index] ? total / weights[index] / 10 : NaN);
		const climatology = Array.from(climatologyTotals, (total, index) => climatologyWeights[index] ? total / climatologyWeights[index] / 10 : NaN);
		const values = anomaly
			? means.map((value, index) => Number.isFinite(value) && Number(climatology[index]) > 0 ? value / Number(climatology[index]) - 1 : NaN)
			: means;
		const maximum = anomaly ? 1 : niceRainfallMaximum(Math.max(...values.filter(Number.isFinite)));
		rainfallMapCache = {key, values, maximum, tracks: indexes.length, systemDays, mode: state.stateFill, anomaly, climatologyPeriod: DETAIL.state_rainfall && DETAIL.state_rainfall.climatology_period};
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
			? `IMD fractional anomaly relative to the ${period} calendar-month-matched daily state mean.`
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
			? `<span class="mla-legend-item"><span class="mla-swatch" style="background:${rainfallAnomalyColour(-1)}"></span>−1 fractional anomaly</span><span class="mla-legend-item"><span class="mla-swatch" style="background:${rainfallAnomalyColour(0)}"></span>month-matched mean</span><span class="mla-legend-item"><span class="mla-swatch" style="background:${rainfallAnomalyColour(1)}"></span>+1 or wetter</span>`
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
		const rainfallLabel = rainfall ? ` · IMD state ${rainfall.anomaly ? 'fractional climatology anomaly' : 'mean'} across ${fmt(rainfall.systemDays)} system-days` : '';
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

	function mjoLabel(index) {
		const phase = CLIMATE.mjo.phase[index];
		const amplitude = CLIMATE.mjo.amplitude_x100[index];
		if (phase < 0) return 'Unavailable';
		if (phase === 0) return `Inactive (${fmt(amplitude / 100, 2)})`;
		return `Phase ${phase} (${fmt(amplitude / 100, 2)})`;
	}

	function ensoLabel(index) {
		const category = CLIMATE.enso.class[index];
		if (category < 0) return 'Unavailable';
		return `${['La Niña', 'Neutral', 'El Niño'][category]} (${fmt(CLIMATE.enso.oni_x100[index] / 100, 2)} °C)`;
	}

	function forecastArchiveOpportunity(manifest, trackIndex) {
		const row = track(trackIndex);
		const start = Number(row[T.start_ms]);
		const end = Number(row[T.end_ms]);
		const token = `ERA5 v5.6 track ${atlasId(trackIndex)}`;
		const entries = [...(manifest.archive || []), ...(manifest.tigge_archive || [])].filter(entry => {
			const first = new Date(entry.valid_start_utc || entry.cycle_utc).getTime();
			const last = new Date(entry.valid_end_utc).getTime();
			return Number.isFinite(first) && Number.isFinite(last) && first <= end && last >= start;
		});
		if (!entries.length) return null;
		const firstTime = Math.ceil(start / (6 * HOUR_MS)) * 6 * HOUR_MS;
		const candidates = [];
		for (let time = firstTime; time <= end; time += 6 * HOUR_MS) candidates.push(time);
		if (!candidates.length) candidates.push(Math.round((start + end) / (12 * HOUR_MS)) * 6 * HOUR_MS);
		let best = null;
		for (const time of candidates) {
			const valid = entries.filter(entry => new Date(entry.valid_start_utc || entry.cycle_utc).getTime() <= time && new Date(entry.valid_end_utc).getTime() >= time);
			if (!valid.length) continue;
			const matched = valid.filter(entry => (entry.verification_labels || []).some(label => String(label).includes(token)));
			const pool = matched.length ? matched : valid;
			const modelIds = new Set(pool.map(entry => entry.model));
			const score = Number(Boolean(matched.length)) * 100000 + modelIds.size * 1000 + pool.length - Math.abs(time - (start + end) / 2) / HOUR_MS / 1000;
			if (!best || score > best.score) best = {time, pool, matched: Boolean(matched.length), score};
		}
		if (!best) return null;
		const definitions = new Map((manifest.models || []).map(model => [model.id, model.label]));
		const labels = [...new Set(best.pool.map(entry => entry.model_label || definitions.get(entry.model) || entry.model))].sort();
		const target = new Date(best.time);
		return {
			date: target.toISOString().slice(0, 10),
			hour: String(target.getUTCHours()).padStart(2, '0'),
			query: best.matched ? token : '',
			labels
		};
	}

	function renderForecastArchiveAction(trackIndex) {
		const button = $('#mlaOpenForecastArchive');
		if (!button) return;
		const serial = ++forecastDossierSerial;
		button.hidden = true;
		ensureForecastArchiveIndex().then(manifest => {
			if (serial !== forecastDossierSerial || state.selected !== trackIndex) return;
			if (!forecastOpportunityCache.has(trackIndex)) forecastOpportunityCache.set(trackIndex, forecastArchiveOpportunity(manifest, trackIndex));
			const opportunity = forecastOpportunityCache.get(trackIndex);
			if (!opportunity) return;
			button.dataset.forecastDate = opportunity.date;
			button.dataset.forecastHour = opportunity.hour;
			button.dataset.forecastQuery = opportunity.query;
			const visible = opportunity.labels.slice(0, 3).join(', ');
			const remainder = opportunity.labels.length > 3 ? ` +${opportunity.labels.length - 3}` : '';
			button.textContent = `Forecast archive · ${visible}${remainder}`;
			button.title = `Open ${opportunity.date} ${opportunity.hour} UTC with ${opportunity.labels.join(', ')}`;
			button.hidden = false;
		}).catch(() => {
			// Forecast availability is optional context and must not disturb the explorer.
		});
	}

	function renderDossier() {
		const node = $('#mlaDossier');
		const content = node;
		const hasSelection = state.selected != null;
		node.classList.toggle('has-selection', hasSelection);
		$('#mlaExploreEvolutionGrid').classList.toggle('has-selection', hasSelection);
		$('#mlaSelectedEvolutionCard').hidden = !hasSelection;
		$('#mlaCompositeCard').hidden = !hasSelection;
		if (state.selected == null) {
			forecastDossierSerial++;
			if (!state.active.length) {
				content.innerHTML = '<div class="mla-dossier-head"><div><h3>No matching systems</h3><p class="mla-dossier-sub">Adjust or reset the active filters.</p></div></div>';
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
			content.innerHTML = `<div class="mla-dossier-head"><div><span class="mla-badge" data-tone="official">Current subset</span><h3>${fmt(state.active.length)} systems</h3><p class="mla-dossier-sub">Selected-month positions on the map</p></div></div><div class="mla-fact-grid">${facts.map(fact => `<div class="mla-fact"><span>${esc(fact[0])}</span><strong>${esc(fact[1])}</strong></div>`).join('')}</div><p class="mla-dossier-empty">Select a track for its weather evolution, rainfall context and downloads.</p>`;
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
			['MJO RMM at genesis', mjoLabel(index)],
			['ENSO at genesis', ensoLabel(index)]
		];
		const analogues = closestAnalogues(index, 5);
		content.innerHTML = `
			<div class="mla-dossier-head"><div>${state.activeBit[index] ? '' : '<span class="mla-badge" data-tone="review">Pinned · outside current subset</span>'}<h3>${esc(systemLabel(index))}</h3><p class="mla-dossier-sub">${date(row[T.start_ms])} to ${date(row[T.end_ms])} · physical event ID ${atlasId(index)}</p></div></div>
			<div class="mla-fact-grid">${facts.map(fact => `<div class="mla-fact"><span>${esc(fact[0])}</span><strong>${esc(fact[1])}</strong></div>`).join('')}</div>
			<p class="mla-dossier-empty">Peak class is ERA5-derived and uses IMD-equivalent wind thresholds. CS means Cyclonic Storm, not Saffir–Simpson Category 1 or an official agency classification.</p>
			<div class="mla-match-box"><h4>Closest catalogue analogues</h4><div class="mla-chip-row">${analogues.map(([analogue, distance]) => `<button class="mla-chip" type="button" data-select-track="${analogue}" data-keep-map="true" title="track, intensity and impact analogue distance ${distance.toFixed(2)}">${esc(systemLabel(analogue))}</button>`).join('')}</div></div>
			<div class="mla-dossier-actions"><button class="mla-btn mla-btn-small" id="mlaPreviousTrack" type="button">Previous</button><button class="mla-btn mla-btn-small" id="mlaNextTrack" type="button">Next</button><button class="mla-btn mla-btn-small" id="mlaFitTrack" type="button">Fit track</button><button class="mla-btn mla-btn-small" id="mlaSelectedFixes" type="button">Download track points</button><button class="mla-btn mla-btn-small" id="mlaOpenForecastArchive" type="button" hidden>Forecast archive</button></div>
			`;
		$('#mlaPreviousTrack').addEventListener('click', () => stepSelected(-1));
		$('#mlaNextTrack').addEventListener('click', () => stepSelected(1));
		$('#mlaFitTrack').addEventListener('click', fitSelected);
		$('#mlaSelectedFixes').addEventListener('click', downloadSelectedFixes);
		$('#mlaOpenForecastArchive').addEventListener('click', event => {
			const button = event.currentTarget;
			window.dispatchEvent(new CustomEvent('mla:open-forecast-archive', {detail: {
				date: button.dataset.forecastDate,
				hour: button.dataset.forecastHour,
				query: button.dataset.forecastQuery
			}}));
			activateTab('forecast', true);
		});
		renderForecastArchiveAction(index);
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
		if (current < 0) selectTrack(indexes[direction < 0 ? indexes.length - 1 : 0]);
		else selectTrack(indexes[(current + direction + indexes.length) % indexes.length]);
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
		const showRainBars = metricKey !== 'rain';
		const hours = lineSeries.hours;
		if (!hours.length) { emptyChart('mlaLifeChart'); return null; }
		const breakPrefix = new Uint16Array(hours.length + 1);
		const breakSet = new Set((CORE.breaks[trackIndex] || []).map(item => Number(item[0])));
		for (let index = 0; index < hours.length; index++) breakPrefix[index + 1] = breakPrefix[index] + (breakSet.has(index) ? 1 : 0);
		const linePoints = hours.map((hour, index) => ({hour, value: lineSeries.values[index], index})).filter(point => Number.isFinite(point.value));
		const rainPoints = hours.map((hour, index) => ({hour, value: rainSeries.values[index], index})).filter(point => Number.isFinite(point.value));
		if (!linePoints.length) { emptyChart('mlaLifeChart'); return null; }

		const {canvas, context, width, height} = drawing;
		const padding = {left: 58, right: showRainBars ? 56 : 18, top: 42, bottom: 44};
		const plotBottom = height - padding.bottom;
		const plotWidth = width - padding.left - padding.right;
		const plotHeight = plotBottom - padding.top;
		const xMin = Number(hours[0]);
		const xMax = Number(hours[hours.length - 1]);
		let yMin = Math.min(...linePoints.map(point => point.value));
		let yMax = Math.max(...linePoints.map(point => point.value));
		if (definition.zero !== false) yMin = Math.min(0, yMin);
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
			if (showRainBars) { context.textAlign = 'right'; context.fillText(fmt(rightValue, 0), width - 6, y + 4); }
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
		context.fillText(`${definition.shortTitle || definition.title} (${definition.unit})`, padding.left + 24, 21);
		if (showRainBars) {
			const rainLegendX = Math.min(width - 126, padding.left + 215);
			context.fillStyle = rgba(METRICS.rain.colour, .52);
			context.fillRect(rainLegendX, 13, 10, 10);
			context.fillStyle = css('--mla-ink', '#282119');
			context.fillText('24 h rain (mm)', rainLegendX + 16, 21);
		}

		if (showRainBars) {
			const estimatedStep = hours.length > 1 ? median(hours.slice(1).map((value, index) => value - hours[index]).filter(value => value > 0)) : 1;
			const barWidth = clamp(plotWidth * Math.max(.6, estimatedStep) / Math.max(1, xMax - xMin), .8, 7);
			for (const point of rainPoints) {
				const x = X(point.hour) - barWidth / 2;
				context.fillStyle = rgba(METRICS.rain.colour, .32);
				context.fillRect(x, R(point.value), barWidth, Math.max(0, plotBottom - R(point.value)));
			}
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

		const rainSummary = showRainBars && rainPoints.length ? ` · peak 24 h rain ${fmt(Math.max(...rainPoints.map(point => point.value)), 1)} mm` : '';
		const summary = `${fmt(hours.length)} hourly positions · ${definition.title} ${fmt(Math.min(...linePoints.map(point => point.value)), 1)}–${fmt(Math.max(...linePoints.map(point => point.value)), 1)} ${definition.unit}${rainSummary}.`;
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
			const rainReadout = showRainBars ? ` · 24 h rain ${fmt(rainSeries.values[index], 1)} mm` : '';
			readout.textContent = `${timeLabel(hours[index])} from genesis · ${definition.title} ${fmt(lineSeries.values[index], 1)} ${definition.unit}${rainReadout}.`;
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
		canvas.setAttribute('aria-label', `${definition.title} line${showRainBars ? ' with 24-hour rainfall bars' : ''} for ${systemLabel(trackIndex)}${Number.isFinite(sliderHour) ? `; dashed slider-time marker at ${timeLabel(sliderHour)} from genesis` : ''}; drag the marker or use arrow keys to change time`);
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

	function drawStackedBars(id, labels, matrix, options) {
		const drawing = setupChart(id);
		if (!drawing) return;
		if (!labels.length || !matrix.length) { emptyChart(id); return; }
		const {context, width, height} = drawing;
		const colours = options.colours;
		const seriesLabels = options.seriesLabels;
		const padding = {left: 48, right: 14, top: 48, bottom: 48};
		const totals = matrix.map(row => row.reduce((sum, value) => sum + (Number(value) || 0), 0));
		const maximum = options.share ? 100 : Math.max(1, ...totals);
		const plotHeight = height - padding.top - padding.bottom;
		const barWidth = (width - padding.left - padding.right) / labels.length;
		context.font = `11px ${CANVAS_FONT}`;
		context.fillStyle = css('--mla-muted', '#685c4d');
		context.strokeStyle = 'rgba(70, 60, 45, .16)';
		for (let tick = 0; tick <= 4; tick++) {
			const y = padding.top + tick * plotHeight / 4;
			context.beginPath(); context.moveTo(padding.left, y); context.lineTo(width - padding.right, y); context.stroke();
			context.fillText(`${fmt(maximum * (4 - tick) / 4)}${options.share ? '%' : ''}`, 4, y + 4);
		}
		matrix.forEach((row, rowIndex) => {
			const total = totals[rowIndex];
			let cumulative = 0;
			row.forEach((raw, column) => {
				const value = options.share ? (total ? Number(raw) / total * 100 : 0) : Number(raw);
				const y0 = height - padding.bottom - cumulative / maximum * plotHeight;
				cumulative += value;
				const y1 = height - padding.bottom - cumulative / maximum * plotHeight;
				context.fillStyle = colours[column];
				context.fillRect(padding.left + rowIndex * barWidth + barWidth * .12, y1, Math.max(2, barWidth * .76), Math.max(0, y0 - y1));
			});
			context.fillStyle = css('--mla-muted', '#685c4d');
			context.textAlign = 'center';
			context.fillText(labels[rowIndex], padding.left + rowIndex * barWidth + barWidth / 2, height - 13);
		});
		let legendX = padding.left;
		seriesLabels.forEach((label, index) => {
			context.fillStyle = colours[index]; context.fillRect(legendX, 15, 12, 9);
			context.fillStyle = css('--mla-ink', '#282119'); context.textAlign = 'left'; context.fillText(label, legendX + 16, 24);
			legendX += 29 + label.length * 7;
		});
		context.textAlign = 'left';
	}

	function drawGroupedBars(id, labels, series, options) {
		const drawing = setupChart(id);
		if (!drawing) return;
		if (!labels.length || !series.length) { emptyChart(id); return; }
		const {context, width, height} = drawing;
		context.font = `11px ${CANVAS_FONT}`;
		const provisionalGroupWidth = (width - 48 - 14) / labels.length;
		const rotateLabels = labels.some(label => context.measureText(label).width > provisionalGroupWidth - 6);
		const padding = {left: 48, right: 14, top: 48, bottom: rotateLabels ? 78 : 52};
		const maximumRaw = Math.max(1, ...series.flatMap(item => item.values).filter(Number.isFinite));
		const maximum = options && options.percent ? Math.max(10, Math.ceil(maximumRaw / 10) * 10) : maximumRaw;
		const plotHeight = height - padding.top - padding.bottom;
		const groupWidth = (width - padding.left - padding.right) / labels.length;
		const barWidth = Math.min(22, groupWidth * .72 / series.length);
		context.strokeStyle = 'rgba(70, 60, 45, .16)';
		context.fillStyle = css('--mla-muted', '#685c4d');
		for (let tick = 0; tick <= 4; tick++) {
			const y = padding.top + tick * plotHeight / 4;
			context.beginPath(); context.moveTo(padding.left, y); context.lineTo(width - padding.right, y); context.stroke();
			context.fillText(`${fmt(maximum * (4 - tick) / 4, options && options.decimals || 0)}${options && options.percent ? '%' : ''}`, 4, y + 4);
		}
		labels.forEach((label, labelIndex) => {
			const centre = padding.left + (labelIndex + .5) * groupWidth;
			series.forEach((item, seriesIndex) => {
				const value = Number(item.values[labelIndex]) || 0;
				const barHeight = value / maximum * plotHeight;
				context.fillStyle = item.colour;
				context.fillRect(centre + (seriesIndex - (series.length - 1) / 2) * barWidth - barWidth / 2, height - padding.bottom - barHeight, Math.max(2, barWidth - 1), barHeight);
			});
			context.fillStyle = css('--mla-muted', '#685c4d');
			if (rotateLabels) {
				context.save();
				context.translate(centre, height - padding.bottom + 13);
				context.rotate(-Math.PI / 4);
				context.textAlign = 'right';
				context.textBaseline = 'middle';
				context.fillText(label, 0, 0);
				context.restore();
			} else {
				context.textAlign = 'center';
				context.textBaseline = 'alphabetic';
				context.fillText(label, centre, height - 14);
			}
		});
		let legendX = padding.left;
		series.forEach(item => {
			context.fillStyle = item.colour; context.fillRect(legendX, 15, 12, 9);
			context.fillStyle = css('--mla-ink', '#282119'); context.textAlign = 'left'; context.fillText(item.name, legendX + 16, 24);
			legendX += 34 + item.name.length * 7;
		});
		context.textAlign = 'left';
	}

	function drawHeatmap(id, rows, columns, matrix, options) {
		const drawing = setupChart(id);
		if (!drawing) return;
		const {context, width, height} = drawing;
		context.font = `11px ${CANVAS_FONT}`;
		const rowLabelWidth = Math.max(0, ...rows.map(label => context.measureText(label).width));
		const left = Math.max(options && options.left || 72, Math.ceil(rowLabelWidth) + 14);
		const provisionalCellWidth = (width - left - 14) / columns.length;
		const rotateColumns = columns.some(label => context.measureText(label).width > provisionalCellWidth - 7);
		const padding = {left, right: 14, top: 20, bottom: rotateColumns ? 88 : 40};
		const cellWidth = (width - padding.left - padding.right) / columns.length;
		const cellHeight = (height - padding.top - padding.bottom) / rows.length;
		const maximum = Math.max(1, ...matrix.flat().filter(Number.isFinite));
		const labelColour = css('--mla-muted', '#685c4d');
		rows.forEach((label, row) => {
			columns.forEach((unused, column) => {
				const value = matrix[row][column];
				context.fillStyle = Number.isFinite(value) && value > 0 ? ramp(value / maximum) : 'rgba(90, 75, 55, .08)';
				context.fillRect(padding.left + column * cellWidth, padding.top + row * cellHeight, Math.max(1, cellWidth - 1), Math.max(1, cellHeight - 1));
				if (cellWidth > 34 && cellHeight > 22 && Number.isFinite(value) && value > 0) {
					context.fillStyle = value / maximum > .58 ? '#fffaf0' : '#282119';
					context.textAlign = 'left';
					context.textBaseline = 'alphabetic';
					context.fillText(fmt(value, options && options.decimals ? options.decimals : 0), padding.left + column * cellWidth + 4, padding.top + row * cellHeight + cellHeight * .64);
				}
			});
			context.fillStyle = labelColour;
			context.textAlign = 'right';
			context.textBaseline = 'middle';
			context.fillText(label, padding.left - 7, padding.top + row * cellHeight + cellHeight / 2);
		});
		context.fillStyle = labelColour;
		columns.forEach((label, index) => {
			const centre = padding.left + (index + .5) * cellWidth;
			if (rotateColumns) {
				context.save();
				context.translate(centre, height - padding.bottom + 10);
				context.rotate(-Math.PI / 4);
				context.textAlign = 'right';
				context.textBaseline = 'middle';
				context.fillText(label, 0, 0);
				context.restore();
			} else {
				context.textAlign = 'center';
				context.textBaseline = 'alphabetic';
				context.fillText(label, centre, height - 15);
			}
		});
		context.textAlign = 'left';
		context.textBaseline = 'alphabetic';
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

	function drawTrackDensityMap() {
		const drawing = setupChart('mlaGenesisChart');
		if (!drawing) return;
		const projection = fixedProjection(drawing.width, drawing.height, {lonMin: 52, lonMax: 108, latMin: -4, latMax: 36});
		drawMapGeography(drawing.context, projection, drawing.width, drawing.height, {});
		const maximum = Math.max(1, drawDensity(drawing.context, projection, state.active));
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
		drawing.context.fillText('Unique tracks per 0.5° cell', legendX, legendY - 6);
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

	function selectedExposureDays(year) {
		let days = 0;
		for (let month = 1; month <= 12; month++) {
			if (!state.months.has(month)) continue;
			const count = new Date(Date.UTC(year, month, 0)).getUTCDate();
			for (let day = 1; day <= count; day++) {
				const value = `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
				if (state.timeMode === 'dates' && (value < state.dateMin || value > state.dateMax)) continue;
				days++;
			}
		}
		return days;
	}

	function systemDaysByYear(indexes) {
		const result = new Map();
		for (const index of indexes) {
			const row = track(index);
			let day = Math.floor(Number(row[T.start_ms]) / 86400000) * 86400000;
			const end = Math.floor(Number(row[T.end_ms]) / 86400000) * 86400000;
			for (; day <= end; day += 86400000) {
				const instant = new Date(day);
				const year = instant.getUTCFullYear();
				const month = instant.getUTCMonth() + 1;
				const value = instant.toISOString().slice(0, 10);
				if (!state.months.has(month)) continue;
				if (state.timeMode === 'dates' ? value < state.dateMin || value > state.dateMax : year < state.yearMin || year > state.yearMax) continue;
				result.set(year, (result.get(year) || 0) + 1);
			}
		}
		return result;
	}

	function centredMean(points, radius) {
		return points.map((point, index) => {
			const values = points.slice(Math.max(0, index - radius), index + radius + 1).map(item => item.y).filter(Number.isFinite);
			return {x: point.x, y: values.length >= Math.min(5, radius * 2 + 1) ? mean(values) : NaN};
		});
	}

	function theilSenSlope(points) {
		const valid = points.filter(point => Number.isFinite(point.x) && Number.isFinite(point.y));
		const slopes = [];
		for (let first = 0; first < valid.length; first++) for (let second = first + 1; second < valid.length; second++) slopes.push((valid[second].y - valid[first].y) / (valid[second].x - valid[first].x));
		return slopes.length ? median(slopes) : NaN;
	}

	const ENDPOINT_FLOW_LABELS = ['Bay of Bengal', 'Arabian Sea', 'Indian land', 'Other land', 'Other Indian Ocean', 'Other water'];
	function endpointFlowIndex(mask) {
		if (mask & ENDPOINT_REGION_BITS.india) return 2;
		if (mask & ENDPOINT_REGION_BITS.bob) return 0;
		if (mask & ENDPOINT_REGION_BITS.arabian) return 1;
		if (mask & ENDPOINT_REGION_BITS.land) return 3;
		if (mask & ENDPOINT_REGION_BITS.indian_ocean) return 4;
		return 5;
	}

	function renderClimateComposition() {
		const key = state.climateIndex;
		const field = key === 'enso' ? CLIMATE.enso.class : CLIMATE[key].phase;
		const values = key === 'enso' ? [-1, 0, 1, 2] : [0, 1, 2, 3, 4, 5, 6, 7, 8];
		const labels = key === 'enso' ? ['Unavailable', 'La Niña', 'Neutral', 'El Niño'] : ['Inactive', '1', '2', '3', '4', '5', '6', '7', '8'];
		const search = {...parsedSearch(), query: '', exactDate: null, exactTime: null, exactDateStart: null, exactDateEnd: null, exactTrackId: null, exactYear: null};
		const currentIndexes = [];
		const referenceIndexes = [];
		for (let index = 0; index < CORE.tracks.length; index++) {
			const failures = filterFailures(index, search, {ignoreSearch: true, ignoreClimate: key});
			if (!failures.length) currentIndexes.push(index);
			if (!failures.some(item => item.key === 'period' || item.key === 'months')) referenceIndexes.push(index);
		}
		const counts = indexes => values.map(value => indexes.reduce((sum, index) => sum + Number(field[index] === value), 0));
		const currentCounts = counts(currentIndexes);
		const referenceCounts = counts(referenceIndexes);
		const currentTotal = currentCounts.reduce((sum, value) => sum + value, 0);
		const referenceTotal = referenceCounts.reduce((sum, value) => sum + value, 0);
		const currentOmitted = currentIndexes.length - currentTotal;
		const referenceOmitted = referenceIndexes.length - referenceTotal;
		const currentShares = currentCounts.map(value => currentTotal ? value / currentTotal * 100 : 0);
		const referenceShares = referenceCounts.map(value => referenceTotal ? value / referenceTotal * 100 : 0);
		drawGroupedBars('mlaClimateChart', labels, [
			{name: 'Filtered context', colour: css('--mla-indigo', '#233f78'), values: currentShares},
			{name: 'All LPSs', colour: css('--mla-turmeric', '#c3931d'), values: referenceShares}
		], {percent: true, decimals: 0});
		const missingNote = key === 'enso' ? '' : ` Records without ${key.toUpperCase()} phase data are omitted (${fmt(currentOmitted)} filtered; ${fmt(referenceOmitted)} reference).`;
		$('#mlaClimateStatus').textContent = `${fmt(currentTotal)} eligible systems in the filtered context; ${fmt(referenceTotal)} in the time/month reference. Shares describe LPS composition, not formation rate per index day.${missingNote}`;
		$('#mlaClimateData').innerHTML = accessibleTable(['Category', 'Filtered N', 'Filtered share', 'Reference N', 'Reference share'], labels.map((label, index) => [label, currentCounts[index], `${fmt(currentShares[index], 1)}%`, referenceCounts[index], `${fmt(referenceShares[index], 1)}%`]));
	}

	function renderClimatology() {
		if ($('#mlaPanelClimatology').hidden) return;
		const annual = new Map();
		for (const index of state.active) {
			const year = track(index)[T.start_year];
			annual.set(year, (annual.get(year) || 0) + 1);
		}
		const systemDays = systemDaysByYear(state.active);
		const annualPoints = [];
		const annualLabel = state.climatologyMeasure === 'rate' ? 'Systems / 100 selected days' : state.climatologyMeasure === 'system_days' ? 'LPS system-days' : 'Selected systems';
		for (let year = periodYearMin(); year <= periodYearMax(); year++) {
			if (!completeYear(year)) continue;
			const count = annual.get(year) || 0;
			const exposure = selectedExposureDays(year);
			const value = state.climatologyMeasure === 'rate' ? (exposure ? count / exposure * 100 : NaN) : state.climatologyMeasure === 'system_days' ? (systemDays.get(year) || 0) : count;
			annualPoints.push({x: year, y: value, count, exposure, systemDays: systemDays.get(year) || 0});
		}
		const smooth = centredMean(annualPoints, 5);
		drawLinePlot('mlaAnnualChart', [
			{name: annualLabel, colour: rgba(css('--mla-indigo', '#233f78'), .34), width: 1.4, points: annualPoints},
			{name: 'Centred 11-year mean', colour: css('--mla-indigo-deep', '#17294f'), width: 3, points: smooth}
		], {zero: true, xFormat: value => String(Math.round(value)), yFormat: value => fmt(value, state.climatologyMeasure === 'rate' ? 1 : 0)});
		const slope = theilSenSlope(annualPoints) * 10;
		const unit = state.climatologyMeasure === 'rate' ? 'systems per 100 days' : state.climatologyMeasure === 'system_days' ? 'system-days' : 'systems';
		$('#mlaAnnualStatus').textContent = `${Number.isFinite(slope) ? `Theil–Sen descriptive slope ${slope >= 0 ? '+' : '−'}${fmt(Math.abs(slope), 2)} ${unit} decade⁻¹` : 'Trend unavailable'} · ${fmt(annualPoints.length)} exposed years · no attribution implied.`;
		$('#mlaAnnualData').innerHTML = accessibleTable(['Year', annualLabel, 'Systems', 'Selected days', 'System-days', '11-year mean'], annualPoints.map((point, index) => [point.x, fmt(point.y, state.climatologyMeasure === 'rate' ? 2 : 0), point.count, point.exposure, point.systemDays, fmt(smooth[index].y, 2)]));

		const exposedYears = annualPoints.length;
		const totalSystemDays = [...systemDays.values()].reduce((sum, value) => sum + value, 0);
		const durations = state.active.map(index => Number(track(index)[T.duration_hours]));
		const named = state.active.filter(index => officialName(index)).length;
		$('#mlaClimatologyStats').innerHTML = [
			['Filtered systems', fmt(state.active.length), exposedYears ? `${fmt(state.active.length / exposedYears, 2)} per exposed year` : 'No exposed years'],
			['LPS system-days', fmt(totalSystemDays), 'Each system-date counted once'],
			['Median duration', durationText(median(durations)), `${fmt(quantile(durations, .25))}–${fmt(quantile(durations, .75))} h IQR`],
			['Named cyclone matches', `${fmt(named / Math.max(1, state.active.length) * 100, 1)}%`, `${fmt(named)} credible named associations`]
		].map(item => `<section class="mla-card mla-stat"><span>${esc(item[0])}</span><strong>${esc(item[1])}</strong><small>${esc(item[2])}</small></section>`).join('');

		const monthly = Array.from({length: 12}, () => Array(6).fill(0));
		for (const index of state.active) {
			const row = track(index);
			if (state.monthMode === 'genesis') monthly[new Date(row[T.start_ms]).getUTCMonth()][row[T.category] - 1]++;
			else if (state.monthMode === 'peak') monthly[CORE.peak_months[index][metric().peakMonth] - 1][row[T.category] - 1]++;
			else {
				for (let month = 1; month <= 12; month++) if (row[T.month_mask] & (1 << (month - 1))) monthly[month - 1][row[T.category] - 1]++;
			}
		}
		drawStackedBars('mlaMonthChart', MONTHS, monthly, {share: state.seasonalMode === 'share', colours: CLASS_COLOURS.slice(1), seriesLabels: ['L', 'D', 'DD', 'CS', 'SCS', 'VS+']});
		$('#mlaMonthData').innerHTML = accessibleTable(['Month', 'L', 'D', 'DD', 'CS', 'SCS', 'VS+', 'Total'], MONTHS.map((month, index) => [month, ...monthly[index], monthly[index].reduce((sum, value) => sum + value, 0)]));

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

		const flowCounts = Array.from({length: ENDPOINT_FLOW_LABELS.length}, () => Array(ENDPOINT_FLOW_LABELS.length).fill(0));
		for (const index of state.active) flowCounts[endpointFlowIndex(genesisRegions[index])][endpointFlowIndex(lysisRegions[index])]++;
		const flowShares = flowCounts.map(row => {
			const total = row.reduce((sum, value) => sum + value, 0);
			return row.map(value => total ? value / total * 100 : 0);
		});
		drawHeatmap('mlaEndpointChart', ENDPOINT_FLOW_LABELS.map(label => label.replace('Indian Ocean', 'IO')), ENDPOINT_FLOW_LABELS.map(label => label.replace('Indian Ocean', 'IO')), flowShares, {left: 104, decimals: 0});
		$('#mlaEndpointData').innerHTML = accessibleTable(['Genesis', ...ENDPOINT_FLOW_LABELS], ENDPOINT_FLOW_LABELS.map((label, row) => [label, ...flowShares[row].map((value, column) => `${fmt(value, 1)}% (n=${flowCounts[row][column]})`)]));
		drawTrackDensityMap();
		renderClimateComposition();
		renderSubsetSections();
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
		if (!field || !Array.isArray(field.shape) || field.shape.length !== 2 || !(Array.isArray(field.data) || ArrayBuffer.isView(field.data))) return null;
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

	function drawPrecipitationComposite(field, canvasId, scaleMaximum) {
		const unpacked = unpackCompositeField(field);
		const drawing = setupChart(canvasId || 'mlaPrecipComposite');
		if (!drawing || !unpacked) return;
		const {context, width, height} = drawing;
		const availableWidth = Math.max(80, width - 66);
		const availableHeight = Math.max(80, height - 119);
		const plotSize = Math.min(availableWidth, availableHeight);
		const plot = {left: 52 + (availableWidth - plotSize) / 2, top: 14, width: plotSize, height: plotSize};
		const palette = COMPOSITE_PALETTES.terrain_r;
		const minimum = 0;
		const maximum = Number.isFinite(Number(scaleMaximum)) && Number(scaleMaximum) > 0 ? Number(scaleMaximum) : 60;
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

	function drawSectionComposite(field, pressureLevels, definition, canvasId) {
		const unpacked = unpackCompositeField(field);
		const drawing = setupChart(canvasId || 'mlaSectionComposite');
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

	function meanSubsetSection(indexes, fieldKey, cacheKey) {
		if (!SECTIONS || !indexes.length) return null;
		const key = `${fieldKey}|${cacheKey}`;
		if (sectionMeanCache.has(key)) return sectionMeanCache.get(key);
		const source = SECTIONS.fields[fieldKey];
		const [rows, columns] = source.shape_per_track.map(Number);
		const size = rows * columns;
		const sums = new Float64Array(size);
		const counts = new Uint32Array(size);
		const nullValue = Number(source.null_value);
		for (const trackIndex of indexes) {
			const offset = trackIndex * size;
			for (let cell = 0; cell < size; cell++) {
				const raw = source.values[offset + cell];
				if (raw === nullValue) continue;
				sums[cell] += raw * Number(source.scale);
				counts[cell]++;
			}
		}
		const data = new Float32Array(size);
		let validCells = 0;
		for (let cell = 0; cell < size; cell++) {
			data[cell] = counts[cell] ? sums[cell] / counts[cell] : NaN;
			if (counts[cell]) validCells++;
		}
		const result = {shape: [rows, columns], scale: 1, data, systems: indexes.length, validCells, totalCells: size};
		sectionMeanCache.set(key, result);
		while (sectionMeanCache.size > 18) sectionMeanCache.delete(sectionMeanCache.keys().next().value);
		return result;
	}

	function sharedCompositeMaximum(fields) {
		let maximum = 0;
		for (const field of fields) {
			const unpacked = unpackCompositeField(field);
			if (!unpacked) continue;
			for (const value of unpacked.values) if (Number.isFinite(value) && value > maximum) maximum = value;
		}
		return niceRainfallMaximum(maximum);
	}

	function renderSubsetSections() {
		if ($('#mlaPanelClimatology').hidden) return;
		const variable = state.subsetSectionVariable;
		const definition = SUBSET_COMPOSITE_DEFINITIONS[variable];
		const precipitation = definition.kind === 'horizontal_precipitation';
		$('#mlaSubsetSectionVariable').value = variable;
		$('#mlaSubsetCompositeHeading').textContent = precipitation ? 'Storm-centred precipitation footprint' : 'Storm-centred vertical structure';
		$('#mlaSubsetCompositeSubtitle').textContent = precipitation
			? 'Lifecycle-mean ERA5 daily precipitation; both panels use the same data-driven scale.'
			: 'Lifecycle-mean ERA5 zonal section through 0° relative latitude; both panels use the same fixed scale.';
		for (const id of ['mlaSubsetSectionChart', 'mlaReferenceSectionChart']) $( `#${id}`).classList.toggle('is-footprint', precipitation);
		$('#mlaClearSectionReference').hidden = !state.sectionReference;
		$('#mlaPinSectionReference').textContent = state.sectionReference ? 'Update pinned reference' : 'Pin subset as reference';
		$('#mlaReferenceSectionHeading').textContent = state.sectionReference ? `Pinned reference · ${state.sectionReferenceLabel}` : 'Complete catalogue reference';
		if (!SECTIONS) {
			emptyChart('mlaSubsetSectionChart', 'Loading filtered-subset ERA5 composites…');
			emptyChart('mlaReferenceSectionChart', 'Loading reference ERA5 composites…');
			$('#mlaSubsetSectionStatus').textContent = 'Loading one compact archive containing all 2,115 lifecycle-mean composites…';
			$('#mlaReferenceSectionStatus').textContent = 'Reference composite pending.';
			ensureSections('Loading filterable ERA5 composites…').then(renderSubsetSections).catch(error => {
				emptyChart('mlaSubsetSectionChart', 'Subset composites unavailable');
				emptyChart('mlaReferenceSectionChart', 'Subset composites unavailable');
				$('#mlaSubsetSectionStatus').textContent = error.message || String(error);
				$('#mlaReferenceSectionStatus').textContent = 'Could not load the composite archive.';
			});
			return;
		}
		if (!SECTIONS.fields[variable]) {
			emptyChart('mlaSubsetSectionChart', `${definition.label} is not available in this composite archive`);
			emptyChart('mlaReferenceSectionChart', `${definition.label} is not available in this composite archive`);
			$('#mlaSubsetSectionStatus').textContent = 'This field is not present in the loaded composite archive.';
			$('#mlaReferenceSectionStatus').textContent = 'This field is not present in the loaded composite archive.';
			return;
		}
		const reference = state.sectionReference || CORE.tracks.map((unused, index) => index);
		const current = meanSubsetSection(state.active, variable, `current:${filterSignature()}`);
		const referenceKey = state.sectionReference ? `pinned:${state.sectionReference.join('.')}` : 'all';
		const baseline = meanSubsetSection(reference, variable, referenceKey);
		const sharedMaximum = precipitation ? sharedCompositeMaximum([current, baseline]) : null;
		if (current) {
			if (precipitation) drawPrecipitationComposite(current, 'mlaSubsetSectionChart', sharedMaximum);
			else drawSectionComposite(current, SECTIONS.grid.pressure_hpa, definition, 'mlaSubsetSectionChart');
			$('#mlaSubsetSectionStatus').textContent = precipitation
				? `${fmt(current.systems)} systems · ${fmt(current.validCells / current.totalCells * 100, 1)}% of footprint cells represented · shared 0–${fmt(sharedMaximum)} ${definition.unit}.`
				: `${fmt(current.systems)} systems · ${fmt(current.validCells / current.totalCells * 100, 1)}% of section cells represented · fixed ${definition.minimum}–${definition.maximum} ${definition.unit}.`;
			$('#mlaSubsetSectionChart').setAttribute('aria-label', precipitation
				? `${definition.label} lifecycle-mean storm-centred horizontal footprint for ${current.systems} systems in the current filtered subset`
				: `${definition.label} lifecycle-mean storm-centred vertical section for ${current.systems} systems in the current filtered subset`);
		} else {
			emptyChart('mlaSubsetSectionChart', 'No systems in the filtered subset');
			$('#mlaSubsetSectionStatus').textContent = 'No systems are available for this filtered composite.';
		}
		if (baseline) {
			if (precipitation) drawPrecipitationComposite(baseline, 'mlaReferenceSectionChart', sharedMaximum);
			else drawSectionComposite(baseline, SECTIONS.grid.pressure_hpa, definition, 'mlaReferenceSectionChart');
			$('#mlaReferenceSectionStatus').textContent = `${fmt(baseline.systems)} systems · same ${precipitation ? `0–${fmt(sharedMaximum)} ${definition.unit}` : 'fixed'} scale as the current subset.`;
			$('#mlaReferenceSectionChart').setAttribute('aria-label', precipitation
				? `${definition.label} lifecycle-mean storm-centred horizontal footprint for ${baseline.systems} systems in the reference subset`
				: `${definition.label} lifecycle-mean storm-centred vertical section for ${baseline.systems} systems in the reference subset`);
		}
		const method = precipitation ? SECTIONS.method.precipitation : SECTIONS.method.vertical;
		const verticalDisplayNote = variable === 'theta_e'
			? 'The θₑ view omits 100 hPa and uses a fixed 330–370 K scale.'
			: variable === 'relative_humidity'
				? 'Relative humidity is derived from ERA5 T and q using mixed-phase saturation vapour pressure, bounded to 0–100%, and shown on that fixed scale.'
				: 'Relative vorticity uses a fixed −20 to 20 × 10⁻⁵ s⁻¹ scale.';
		$('#mlaSubsetSectionData').innerHTML = precipitation
			? `<p>${esc(method)} Each system contributes one lifecycle-mean footprint to the subset mean. The interactive display uses a 0.5° storm-relative latitude–longitude grid. Source archives: ${esc(SECTIONS.fields[variable].sources.join('; '))}.</p>`
			: `<p>${esc(method)} The display uses a 0.5° relative-longitude grid and preserves all ${fmt(SECTIONS.grid.pressure_hpa.length)} pressure levels. ${esc(verticalDisplayNote)} Source archives: ${esc(SECTIONS.fields[variable].sources.join('; '))}.</p>`;
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
		$('#mlaCompositeData').innerHTML = accessibleTable(['Field', 'Coverage', 'Source'], availabilityRows, `${asset.method.precipitation} ${asset.method.vertical} The θₑ display omits 100 hPa and uses a fixed 330–370 K blue–white–red scale. Relative humidity is derived from ERA5 T and q using mixed-phase saturation vapour pressure, bounded to 0–100%, and uses a fixed sequential scale.`);
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
			const includeRain = state.evolutionMetric !== 'rain';
			const rows = evolution.hours.map((hour, index) => ({hour, index})).filter((item, index) => index % stride === 0 || index === evolution.hours.length - 1).map(item => includeRain
				? [item.hour, fmt(evolution.lineValues[item.index], 2), fmt(evolution.rainValues[item.index], 2)]
				: [item.hour, fmt(evolution.lineValues[item.index], 2)]
			);
			const headers = ['Hours since genesis', `${definition.title} (${definition.unit})`];
			if (includeRain) headers.push('24 h rain (mm)');
			$('#mlaLifeData').innerHTML = accessibleTable(headers, rows, `Physics is resampled at each published ${CORE.meta.catalogue_version} centre.`);
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
			return `<div class="mla-profile-slab"><div class="mla-profile-slab-head"><strong>${esc(definition.title)}</strong><span id="mlaProfileMeta-${key}">${esc(definition.unit)}</span></div><canvas class="mla-chart mla-profile-chart" id="mlaProfileChart-${key}" role="img" tabindex="0" aria-label="${esc(`${definition.title}: filtered-subset mean and interquartile range with all-LPS mean`)}"></canvas></div>`;
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
					name: 'Subset mean',
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
				zero: false,
				xMin: 0,
				xMax: 100,
				xFormat: value => `${fmt(value)}%`,
				yFormat: value => fmt(value, ['mslp', 'ringMslp', 'orography'].includes(key) ? 0 : 1)
			});
			accessibleProfiles.push(`<h4>${esc(`${definition.title} (${definition.unit})`)}</h4>${accessibleTable(
				['Life fraction', 'Subset mean', 'Subset Q1', 'Subset Q3', 'Subset systems', 'All-LPS mean', 'All-LPS systems'],
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
		const points = bins.map((x, index) => {
			const average = mean(values[index]);
			return {x, y: average, mean: average, low: quantile(values[index], .25), high: quantile(values[index], .75), n: values[index].length};
		});
		const result = {points, bins};
		profileCache.set(cacheKey, result);
		return result;
	}

	function maximumSeriesValue(index, field, divisor) {
		if (!DETAIL) return NaN;
		const source = DETAIL.series[index][S[field]];
		if (!source) return NaN;
		const values = source.filter(value => value != null).map(Number);
		return values.length ? Math.max(...values) / (divisor || 1) : NaN;
	}

	function minimumSeriesValue(index, field, divisor) {
		if (!DETAIL) return NaN;
		const source = DETAIL.series[index][S[field]];
		if (!source) return NaN;
		const values = source.filter(value => value != null).map(Number);
		return values.length ? Math.min(...values) / (divisor || 1) : NaN;
	}

	function maximumVectorSeriesValue(index, firstField, secondField, divisor) {
		if (!DETAIL) return NaN;
		const first = DETAIL.series[index][S[firstField]];
		const second = DETAIL.series[index][S[secondField]];
		if (!first || !second) return NaN;
		let maximum = -Infinity;
		for (let point = 0; point < Math.min(first.length, second.length); point++) {
			if (first[point] == null || second[point] == null) continue;
			maximum = Math.max(maximum, Math.hypot(Number(first[point]), Number(second[point])) / (divisor || 1));
		}
		return Number.isFinite(maximum) ? maximum : NaN;
	}

	function maximumLagGrowth(index, field, divisor, lag) {
		if (!DETAIL) return NaN;
		const values = DETAIL.series[index][S[field]];
		let maximum = -Infinity;
		for (let point = lag || 24; point < values.length; point++) {
			if (values[point] == null || values[point - (lag || 24)] == null) continue;
			maximum = Math.max(maximum, (Number(values[point]) - Number(values[point - (lag || 24)])) / (divisor || 1));
		}
		return Number.isFinite(maximum) ? maximum : NaN;
	}

	function seriesCount(index, field, predicate) {
		if (!DETAIL) return NaN;
		return DETAIL.series[index][S[field]].reduce((count, value) => count + Number(value != null && predicate(Number(value))), 0);
	}

	const EXTREMES = {
		compoundIntensity: {label: 'Compound intensity percentile', unit: 'P', decimals: 1, value: index => mean(['deficit', 'wind', 'vort', 'mslp'].map(key => percentileMetric(index, key))), descending: true, note: 'Equal-weight mean of fixed full-catalogue pressure-deficit, circulation-wind, smoothed-vorticity and MSLP-depth percentiles'},
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
		coreVortMax: {label: 'Maximum core vorticity', unit: '10⁻⁵ s⁻¹', decimals: 1, value: index => maximumSeriesValue(index, 'max_vort_x10', 10), descending: true, requiresDetail: true},
		coreVortMean: {label: 'Maximum mean core vorticity', unit: '10⁻⁵ s⁻¹', decimals: 1, value: index => maximumSeriesValue(index, 'mean_vort_x10', 10), descending: true, requiresDetail: true},
		vort850: {label: 'Maximum mean 850-hPa vorticity', unit: '10⁻⁵ s⁻¹', decimals: 1, value: index => maximumSeriesValue(index, 'vort850_x10', 10), descending: true, requiresDetail: true},
		vort700: {label: 'Maximum mean 700-hPa vorticity', unit: '10⁻⁵ s⁻¹', decimals: 1, value: index => maximumSeriesValue(index, 'vort700_x10', 10), descending: true, requiresDetail: true},
		vort500: {label: 'Maximum mean 500-hPa vorticity', unit: '10⁻⁵ s⁻¹', decimals: 1, value: index => maximumSeriesValue(index, 'vort500_x10', 10), descending: true, requiresDetail: true},
		vortDeep: {label: 'Maximum deep-layer mean vorticity', unit: '10⁻⁵ s⁻¹', decimals: 1, value: index => maximumSeriesValue(index, 'vort_deep_x10', 10), descending: true, requiresDetail: true},
		maxWind: {label: 'Maximum 10-m wind', unit: 'm s⁻¹', decimals: 1, value: index => maximumSeriesValue(index, 'max_wind_x10', 10), descending: true, requiresDetail: true},
		meanWind: {label: 'Maximum mean 10-m wind', unit: 'm s⁻¹', decimals: 1, value: index => maximumSeriesValue(index, 'mean_wind_x10', 10), descending: true, requiresDetail: true},
		backgroundWind: {label: 'Strongest background wind', unit: 'm s⁻¹', decimals: 1, value: index => maximumVectorSeriesValue(index, 'background_u_x10', 'background_v_x10', 10), descending: true, requiresDetail: true, note: 'Largest 300–500 km environmental vector speed sampled along the track'},
		westerlyBackground: {label: 'Strongest westerly background flow', unit: 'm s⁻¹', decimals: 1, value: index => maximumSeriesValue(index, 'background_u_x10', 10), descending: true, requiresDetail: true},
		easterlyBackground: {label: 'Strongest easterly background flow', unit: 'm s⁻¹', decimals: 1, value: index => minimumSeriesValue(index, 'background_u_x10', 10), descending: false, requiresDetail: true},
		ringMslp: {label: 'Lowest environmental MSLP', unit: 'hPa', decimals: 1, value: index => minimumSeriesValue(index, 'ring_mslp_x10', 10), descending: false, requiresDetail: true},
		hourlyRain: {label: 'Maximum hourly precipitation', unit: 'mm h⁻¹', decimals: 1, value: index => maximumSeriesValue(index, 'precip1_x100', 100), descending: true, requiresDetail: true},
		q700: {label: 'Maximum q700', unit: 'g kg⁻¹', decimals: 1, value: index => maximumSeriesValue(index, 'q700_x10', 10), descending: true, requiresDetail: true},
		q500: {label: 'Maximum q500', unit: 'g kg⁻¹', decimals: 1, value: index => maximumSeriesValue(index, 'q500_x10', 10), descending: true, requiresDetail: true},
		qDeep: {label: 'Maximum deep-layer specific humidity', unit: 'g kg⁻¹', decimals: 1, value: index => maximumSeriesValue(index, 'q_deep_x10', 10), descending: true, requiresDetail: true},
		rh700: {label: 'Maximum RH700', unit: '%', decimals: 1, value: index => maximumSeriesValue(index, 'rh700_x10', 10), descending: true, requiresDetail: true},
		rh500: {label: 'Maximum RH500', unit: '%', decimals: 1, value: index => maximumSeriesValue(index, 'rh500_x10', 10), descending: true, requiresDetail: true},
		rhDeep: {label: 'Maximum deep-layer relative humidity', unit: '%', decimals: 1, value: index => maximumSeriesValue(index, 'rh_deep_x10', 10), descending: true, requiresDetail: true},
		dryRh500: {label: 'Minimum RH500', unit: '%', decimals: 1, value: index => minimumSeriesValue(index, 'rh500_x10', 10), descending: false, requiresDetail: true},
		t850Warm: {label: 'Maximum T850', unit: 'K', decimals: 1, value: index => maximumSeriesValue(index, 't850_x10', 10), descending: true, requiresDetail: true},
		t850Cold: {label: 'Minimum T850', unit: 'K', decimals: 1, value: index => minimumSeriesValue(index, 't850_x10', 10), descending: false, requiresDetail: true},
		t700Warm: {label: 'Maximum T700', unit: 'K', decimals: 1, value: index => maximumSeriesValue(index, 't700_x10', 10), descending: true, requiresDetail: true},
		t500Cold: {label: 'Minimum T500', unit: 'K', decimals: 1, value: index => minimumSeriesValue(index, 't500_x10', 10), descending: false, requiresDetail: true},
		warmCore850: {label: 'Largest 850-hPa inner temperature anomaly', unit: 'K', decimals: 2, value: index => maximumSeriesValue(index, 't850_inner_anomaly_x100', 100), descending: true, requiresDetail: true},
		warmCore700: {label: 'Largest 700-hPa inner temperature anomaly', unit: 'K', decimals: 2, value: index => maximumSeriesValue(index, 't700_inner_anomaly_x100', 100), descending: true, requiresDetail: true},
		warmCore500: {label: 'Largest 500-hPa inner temperature anomaly', unit: 'K', decimals: 2, value: index => maximumSeriesValue(index, 't500_inner_anomaly_x100', 100), descending: true, requiresDetail: true},
		warmCoreDeep: {label: 'Largest deep inner temperature anomaly', unit: 'K', decimals: 2, value: index => maximumSeriesValue(index, 't_inner_anomaly_deep_x100', 100), descending: true, requiresDetail: true},
		coldCoreDeep: {label: 'Most negative deep inner temperature anomaly', unit: 'K', decimals: 2, value: index => minimumSeriesValue(index, 't_inner_anomaly_deep_x100', 100), descending: false, requiresDetail: true},
		stability850500: {label: 'Largest T850−T500 difference', unit: 'K', decimals: 1, value: index => maximumSeriesValue(index, 't850_minus_t500_x10', 10), descending: true, requiresDetail: true},
		stability700500: {label: 'Largest T700−T500 difference', unit: 'K', decimals: 1, value: index => maximumSeriesValue(index, 't700_minus_t500_x10', 10), descending: true, requiresDetail: true},
		orography: {label: 'Highest centre orography', unit: 'm', decimals: 0, value: index => maximumSeriesValue(index, 'orography_m', 1), descending: true, requiresDetail: true},
		deepening24: {label: 'Maximum 24 h pressure-deficit growth', unit: 'hPa day⁻¹', decimals: 1, value: index => maximumLagGrowth(index, 'pressure_deficit_x10', 10, 24), descending: true, requiresDetail: true, note: 'Largest end-minus-start change across any 24-hour interval; this is pressure-deficit growth, not central-pressure fall'},
		windGrowth24: {label: 'Maximum 24 h circulation-wind growth', unit: 'm s⁻¹ day⁻¹', decimals: 1, value: index => maximumLagGrowth(index, 'circulation_wind_x10', 10, 24), descending: true, requiresDetail: true, note: 'Largest end-minus-start change across any 24-hour interval'},
		vortGrowth24: {label: 'Maximum 24 h smoothed-vorticity growth', unit: '10⁻⁵ s⁻¹ day⁻¹', decimals: 1, value: index => maximumLagGrowth(index, 'vort_smooth_x10', 10, 24), descending: true, requiresDetail: true, note: 'Largest end-minus-start change across any 24-hour interval'},
		closedIsobars: {label: 'Maximum closed 2-hPa isobars', unit: 'count', decimals: 0, value: index => maximumSeriesValue(index, 'closed_isobars', 1), descending: true, requiresDetail: true},
		depressionHours: {label: 'Hours at depression strength or above', unit: 'h', decimals: 0, value: index => seriesCount(index, 'category', value => value >= 2), descending: true, requiresDetail: true, note: 'Hourly positions with persistent atlas-derived class D or stronger'},
		landHours: {label: 'Hours over majority land', unit: 'h', decimals: 0, value: index => seriesCount(index, 'land_fraction_pct_x10', value => value >= 500), descending: true, requiresDetail: true, note: 'Hourly centres whose local ERA5 land fraction is at least 50%'},
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
	const EXTREME_RELATIONSHIP_KEYS = ['compoundIntensity', 'duration', 'distance', 'meanSpeed', 'deficit', 'wind', 'rain', 'hourlyRain', 'vort', 'coreVortMax', 'coreVortMean', 'vort850', 'vort700', 'vort500', 'vortDeep', 'mslp', 'ringMslp', 'maxWind', 'meanWind', 'backgroundWind', 'q850', 'q700', 'q500', 'qDeep', 'rh850', 'rh700', 'rh500', 'rhDeep', 'dryRh500', 't850Warm', 't850Cold', 't700Warm', 't500Cold', 'warmCore850', 'warmCore700', 'warmCore500', 'warmCoreDeep', 'coldCoreDeep', 'stability850500', 'stability700500', 'orography', 'stateRain', 'deepening24', 'windGrowth24', 'vortGrowth24', 'depressionHours', 'landHours'];

	function extremeValueMap(indexes, definition) {
		const values = new Map();
		for (const index of indexes) {
			const value = Number(definition.value(index));
			if (Number.isFinite(value)) values.set(index, value);
		}
		return values;
	}

	function extremeAxisDomain(values) {
		let minimum = Math.min(...values);
		let maximum = Math.max(...values);
		if (minimum === maximum) {
			const margin = Math.max(1, Math.abs(minimum) * .08);
			minimum -= margin;
			maximum += margin;
		} else {
			const margin = (maximum - minimum) * .055;
			minimum -= margin;
			maximum += margin;
		}
		return [minimum, maximum];
	}

	function drawExtremeHistogram(indexes, definition, valueByIndex) {
		const drawing = setupChart('mlaExtremeHistogram');
		if (!drawing) return;
		const values = indexes.map(index => valueByIndex.get(index)).filter(Number.isFinite);
		if (!values.length) {
			emptyChart('mlaExtremeHistogram');
			$('#mlaExtremeDistributionData').innerHTML = '<p>No eligible values.</p>';
			return;
		}
		const {context, width, height} = drawing;
		const padding = {left: 52, right: 18, top: 22, bottom: 47};
		const rawMinimum = Math.min(...values);
		const rawMaximum = Math.max(...values);
		const span = rawMaximum - rawMinimum || Math.max(1, Math.abs(rawMinimum) * .1);
		const minimum = rawMaximum === rawMinimum ? rawMinimum - span / 2 : rawMinimum;
		const maximum = rawMaximum === rawMinimum ? rawMaximum + span / 2 : rawMaximum;
		const binCount = clamp(Math.round(Math.sqrt(values.length)), 8, 24);
		const bins = Array(binCount).fill(0);
		for (const value of values) bins[Math.min(binCount - 1, Math.floor((value - minimum) / (maximum - minimum) * binCount))]++;
		const maximumCount = Math.max(1, ...bins);
		const plotWidth = width - padding.left - padding.right;
		const plotHeight = height - padding.top - padding.bottom;
		context.font = `11px ${CANVAS_FONT}`;
		context.fillStyle = css('--mla-muted', '#685c4d');
		context.strokeStyle = 'rgba(70, 60, 45, .16)';
		for (let tick = 0; tick <= 4; tick++) {
			const y = padding.top + tick * plotHeight / 4;
			context.beginPath(); context.moveTo(padding.left, y); context.lineTo(width - padding.right, y); context.stroke();
			context.textAlign = 'right';
			context.fillText(fmt(maximumCount * (4 - tick) / 4), padding.left - 7, y + 4);
		}
		const barWidth = plotWidth / binCount;
		bins.forEach((count, bin) => {
			const barHeight = count / maximumCount * plotHeight;
			context.fillStyle = rgba(css('--mla-indigo', '#233f78'), .78);
			context.fillRect(padding.left + bin * barWidth + .5, height - padding.bottom - barHeight, Math.max(1, barWidth - 1), barHeight);
		});
		for (let tick = 0; tick <= 4; tick++) {
			const x = padding.left + tick * plotWidth / 4;
			const value = minimum + tick * (maximum - minimum) / 4;
			context.fillStyle = css('--mla-muted', '#685c4d');
			context.textAlign = tick === 0 ? 'left' : tick === 4 ? 'right' : 'center';
			context.fillText(fmt(value, definition.decimals), x, height - 23);
		}
		if (state.selected != null && valueByIndex.has(state.selected)) {
			const selectedValue = valueByIndex.get(state.selected);
			const x = padding.left + clamp((selectedValue - minimum) / (maximum - minimum), 0, 1) * plotWidth;
			context.strokeStyle = css('--mla-madder', '#ad4328');
			context.lineWidth = 2;
			context.setLineDash([5, 4]);
			context.beginPath(); context.moveTo(x, padding.top); context.lineTo(x, height - padding.bottom); context.stroke();
			context.setLineDash([]);
		}
		context.fillStyle = css('--mla-ink', '#282119');
		context.textAlign = 'center';
		context.fillText(`${definition.label} (${definition.unit})`, padding.left + plotWidth / 2, height - 6);
		const statistics = [
			['Eligible systems', fmt(values.length)],
			['Minimum', fmt(rawMinimum, definition.decimals)],
			['25th percentile', fmt(quantile(values, .25), definition.decimals)],
			['Median', fmt(median(values), definition.decimals)],
			['75th percentile', fmt(quantile(values, .75), definition.decimals)],
			['Maximum', fmt(rawMaximum, definition.decimals)]
		];
		$('#mlaExtremeDistributionSubtitle').textContent = `${fmt(values.length)} eligible systems · median ${fmt(median(values), definition.decimals)} ${definition.unit} · dashed marker is the pinned system when eligible.`;
		$('#mlaExtremeDistributionData').innerHTML = accessibleTable(['Statistic', definition.unit || 'Value'], statistics);
	}

	function drawExtremeBoxPlot(indexes, definition, valueByIndex) {
		const drawing = setupChart('mlaExtremeBoxPlot');
		if (!drawing) return;
		const groups = Array.from({length: 6}, () => []);
		for (const index of indexes) if (valueByIndex.has(index)) groups[track(index)[T.category] - 1].push(valueByIndex.get(index));
		const summaries = groups.map(values => ({
			n: values.length,
			p05: quantile(values, .05),
			p25: quantile(values, .25),
			p50: quantile(values, .5),
			p75: quantile(values, .75),
			p95: quantile(values, .95)
		}));
		const domainValues = summaries.flatMap(item => [item.p05, item.p95]).filter(Number.isFinite);
		if (!domainValues.length) {
			emptyChart('mlaExtremeBoxPlot');
			$('#mlaExtremeClassData').innerHTML = '<p>No eligible values.</p>';
			return;
		}
		const {context, width, height} = drawing;
		const padding = {left: 56, right: 18, top: 22, bottom: 47};
		const [minimum, maximum] = extremeAxisDomain(domainValues);
		const plotWidth = width - padding.left - padding.right;
		const plotHeight = height - padding.top - padding.bottom;
		const Y = value => height - padding.bottom - (value - minimum) / (maximum - minimum) * plotHeight;
		context.font = `11px ${CANVAS_FONT}`;
		context.strokeStyle = 'rgba(70, 60, 45, .16)';
		context.fillStyle = css('--mla-muted', '#685c4d');
		for (let tick = 0; tick <= 4; tick++) {
			const y = padding.top + tick * plotHeight / 4;
			const value = maximum - tick * (maximum - minimum) / 4;
			context.beginPath(); context.moveTo(padding.left, y); context.lineTo(width - padding.right, y); context.stroke();
			context.textAlign = 'right'; context.fillText(fmt(value, definition.decimals), padding.left - 7, y + 4);
		}
		const groupWidth = plotWidth / groups.length;
		summaries.forEach((summary, category) => {
			const x = padding.left + (category + .5) * groupWidth;
			const boxWidth = Math.min(44, groupWidth * .56);
			context.fillStyle = css('--mla-muted', '#685c4d');
			context.textAlign = 'center';
			context.fillText(CLASS_SHORT[category + 1], x, height - 22);
			context.fillText(`n=${summary.n}`, x, height - 7);
			if (!summary.n) return;
			context.strokeStyle = CLASS_COLOURS[category + 1];
			context.lineWidth = 1.5;
			context.beginPath(); context.moveTo(x, Y(summary.p05)); context.lineTo(x, Y(summary.p95)); context.stroke();
			for (const value of [summary.p05, summary.p95]) {
				context.beginPath(); context.moveTo(x - boxWidth * .28, Y(value)); context.lineTo(x + boxWidth * .28, Y(value)); context.stroke();
			}
			context.fillStyle = rgba(CLASS_COLOURS[category + 1], .42);
			context.fillRect(x - boxWidth / 2, Y(summary.p75), boxWidth, Math.max(1, Y(summary.p25) - Y(summary.p75)));
			context.strokeRect(x - boxWidth / 2, Y(summary.p75), boxWidth, Math.max(1, Y(summary.p25) - Y(summary.p75)));
			context.lineWidth = 2.4;
			context.beginPath(); context.moveTo(x - boxWidth / 2, Y(summary.p50)); context.lineTo(x + boxWidth / 2, Y(summary.p50)); context.stroke();
		});
		$('#mlaExtremeClassData').innerHTML = accessibleTable(['Peak class', 'n', 'P05', 'P25', 'Median', 'P75', 'P95'], summaries.map((summary, category) => [CLASS_SHORT[category + 1], summary.n, fmt(summary.p05, definition.decimals), fmt(summary.p25, definition.decimals), fmt(summary.p50, definition.decimals), fmt(summary.p75, definition.decimals), fmt(summary.p95, definition.decimals)]), `Values are ${definition.unit}. Classes are each system's peak atlas-derived class.`);
	}

	function drawExtremeScatter(indexes, xDefinition, yDefinition, xValues, yValues) {
		const drawing = setupChart('mlaExtremeScatter');
		if (!drawing) return;
		const points = indexes.filter(index => xValues.has(index) && yValues.has(index)).map(index => ({index, xValue: xValues.get(index), yValue: yValues.get(index)}));
		if (!points.length) {
			extremeScatterPoints = [];
			emptyChart('mlaExtremeScatter');
			$('#mlaExtremeScatterStatus').textContent = 'No systems have both diagnostics in this subset.';
			return;
		}
		const {canvas, context, width, height} = drawing;
		const padding = {left: 72, right: 24, top: 24, bottom: 58};
		const [xMinimum, xMaximum] = extremeAxisDomain(points.map(point => point.xValue));
		const [yMinimum, yMaximum] = extremeAxisDomain(points.map(point => point.yValue));
		const plotWidth = width - padding.left - padding.right;
		const plotHeight = height - padding.top - padding.bottom;
		const X = value => padding.left + (value - xMinimum) / (xMaximum - xMinimum) * plotWidth;
		const Y = value => height - padding.bottom - (value - yMinimum) / (yMaximum - yMinimum) * plotHeight;
		context.font = `11px ${CANVAS_FONT}`;
		context.strokeStyle = 'rgba(70, 60, 45, .16)';
		context.fillStyle = css('--mla-muted', '#685c4d');
		for (let tick = 0; tick <= 4; tick++) {
			const x = padding.left + tick * plotWidth / 4;
			const y = padding.top + tick * plotHeight / 4;
			context.beginPath(); context.moveTo(x, padding.top); context.lineTo(x, height - padding.bottom); context.stroke();
			context.beginPath(); context.moveTo(padding.left, y); context.lineTo(width - padding.right, y); context.stroke();
			context.textAlign = tick === 0 ? 'left' : tick === 4 ? 'right' : 'center';
			context.fillText(fmt(xMinimum + tick * (xMaximum - xMinimum) / 4, xDefinition.decimals), x, height - 37);
			context.textAlign = 'right';
			context.fillText(fmt(yMaximum - tick * (yMaximum - yMinimum) / 4, yDefinition.decimals), padding.left - 8, y + 4);
		}
		extremeScatterPoints = points.map(point => ({...point, x: X(point.xValue), y: Y(point.yValue)}));
		for (const point of extremeScatterPoints) {
			context.beginPath(); context.arc(point.x, point.y, point.index === state.selected ? 5.7 : 3.15, 0, Math.PI * 2);
			context.fillStyle = rgba(CLASS_COLOURS[track(point.index)[T.category]], point.index === state.selected ? .98 : .63);
			context.fill();
			if (point.index === state.selected) {
				context.strokeStyle = css('--mla-ink', '#282119'); context.lineWidth = 1.6; context.stroke();
			}
		}
		context.fillStyle = css('--mla-ink', '#282119');
		context.textAlign = 'center';
		context.fillText(`${xDefinition.label} (${xDefinition.unit})`, padding.left + plotWidth / 2, height - 8);
		context.save();
		context.translate(16, padding.top + plotHeight / 2);
		context.rotate(-Math.PI / 2);
		context.fillText(`${yDefinition.label} (${yDefinition.unit})`, 0, 0);
		context.restore();
		const status = $('#mlaExtremeScatterStatus');
		const summary = `${fmt(points.length)} systems · coloured by peak atlas class · click a point to open it.`;
		status.textContent = summary;
		const nearest = event => {
			const rectangle = canvas.getBoundingClientRect();
			const x = event.clientX - rectangle.left;
			const y = event.clientY - rectangle.top;
			let best = null;
			let distance = 13 * 13;
			for (const point of extremeScatterPoints) {
				const candidate = (point.x - x) ** 2 + (point.y - y) ** 2;
				if (candidate <= distance) { distance = candidate; best = point; }
			}
			return best;
		};
		canvas.onpointermove = event => {
			const point = nearest(event);
			canvas.style.cursor = point ? 'pointer' : 'crosshair';
			status.textContent = point ? `${systemLabel(point.index)} · ${xDefinition.label} ${fmt(point.xValue, xDefinition.decimals)} ${xDefinition.unit} · ${yDefinition.label} ${fmt(point.yValue, yDefinition.decimals)} ${yDefinition.unit}` : summary;
		};
		canvas.onpointerleave = () => { canvas.style.cursor = 'crosshair'; status.textContent = summary; };
		canvas.onclick = event => {
			const point = nearest(event);
			if (point) selectTrack(point.index, {openExplore: true, fit: true});
		};
		canvas.setAttribute('aria-label', `${xDefinition.label} against ${yDefinition.label} for ${points.length} filtered systems, coloured by peak class. A ranked accessible table follows the diagnostic charts.`);
	}

	function drawExtremeTiming(indexes, definition) {
		const count = indexes.length ? Math.max(1, Math.ceil(indexes.length * .1)) : 0;
		const extremeIndexes = indexes.slice(0, count);
		const matrix = Array.from({length: 12}, () => Array(6).fill(0));
		for (const index of extremeIndexes) matrix[new Date(track(index)[T.start_ms]).getUTCMonth()][track(index)[T.category] - 1]++;
		drawHeatmap('mlaExtremeTiming', MONTHS, ['L', 'D', 'DD', 'CS', 'SCS', 'VS+'], matrix, {left: 43});
		$('#mlaExtremeTimingData').innerHTML = accessibleTable(['Genesis month', 'L', 'D', 'DD', 'CS', 'SCS', 'VS+', 'Total'], MONTHS.map((month, row) => [month, ...matrix[row], matrix[row].reduce((sum, value) => sum + value, 0)]), `${fmt(extremeIndexes.length)} systems in the most extreme decile by ${definition.label.toLowerCase()}; ties are ordered by physical-event ID.`);
	}

	function renderExtremes() {
		if ($('#mlaPanelExtremes').hidden) return;
		const definition = EXTREMES[state.extremeMetric];
		const xDefinition = EXTREMES[state.extremeX];
		const yDefinition = EXTREMES[state.extremeY];
		if (!DETAIL && [definition, xDefinition, yDefinition].some(item => item.requiresDetail)) {
			$('#mlaExtremeCaveat').textContent = 'Opening complete hourly diagnostics for the selected analysis…';
			$('#mlaRecordCards').innerHTML = '<p>Loading hourly catalogue diagnostics…</p>';
			for (const id of ['mlaExtremeHistogram', 'mlaExtremeBoxPlot', 'mlaExtremeScatter', 'mlaExtremeTiming']) emptyChart(id, 'Loading hourly catalogue diagnostics…');
			$('#mlaExtremeScatterStatus').textContent = 'Loading the full hourly diagnostic series once for this session.';
			ensureDetail('Opening complete hourly diagnostics…').then(renderExtremes).catch(error => {
				$('#mlaExtremeCaveat').textContent = `Hourly diagnostics could not be opened: ${error.message || error}`;
			});
			return;
		}
		const valueByIndex = extremeValueMap(state.active, definition);
		const indexes = [...valueByIndex.keys()].sort((first, second) => {
			const difference = definition.descending ? valueByIndex.get(second) - valueByIndex.get(first) : valueByIndex.get(first) - valueByIndex.get(second);
			return difference || track(first)[T.id] - track(second)[T.id];
		});
		const valueText = index => `${fmt(valueByIndex.get(index), definition.decimals)} ${definition.unit}`.trim();
		$('#mlaExtremeCaveat').textContent = definition.note || 'Catalogue diagnostic · not an externally validated record';
		$('#mlaRecordCards').innerHTML = indexes.slice(0, 3).map((index, rank) => {
			return `<article class="mla-card mla-record"><span class="mla-label">${rank + 1} · ${esc(definition.label)}</span><h3><button class="mla-row-button" type="button" data-select-track="${index}" data-open-explore="true">${esc(systemLabel(index))}</button></h3><p><strong>${esc(valueText(index))}</strong> · ${date(track(index)[T.start_ms])} · ${esc(CLASS_SHORT[track(index)[T.category]])}</p></article>`;
		}).join('') || '<p>No eligible systems in this subset.</p>';
		drawExtremeHistogram(indexes, definition, valueByIndex);
		drawExtremeBoxPlot(indexes, definition, valueByIndex);
		const xValues = state.extremeX === state.extremeMetric ? valueByIndex : extremeValueMap(state.active, xDefinition);
		const yValues = state.extremeY === state.extremeMetric ? valueByIndex : state.extremeY === state.extremeX ? xValues : extremeValueMap(state.active, yDefinition);
		drawExtremeScatter(state.active, xDefinition, yDefinition, xValues, yValues);
		drawExtremeTiming(indexes, definition);
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
				mjo_rmm_phase_at_genesis: CLIMATE.mjo.phase[index] < 0 ? null : CLIMATE.mjo.phase[index],
				mjo_rmm_amplitude_at_genesis: CLIMATE.mjo.amplitude_x100[index] < 0 ? null : CLIMATE.mjo.amplitude_x100[index] / 100,
				mjo_rmm1_at_genesis: CLIMATE.mjo.rmm1_x100[index] === -32768 ? null : CLIMATE.mjo.rmm1_x100[index] / 100,
				mjo_rmm2_at_genesis: CLIMATE.mjo.rmm2_x100[index] === -32768 ? null : CLIMATE.mjo.rmm2_x100[index] / 100,
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
				mjo_rmm_phase_at_genesis: state.mjo === 'all' ? null : Number(state.mjo),
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
				`Every ${CORE.meta.catalogue_version} physical event is continuous at hourly resolution with physics resampled at every published centre.`,
				'Cyclone names use credible NOAA IBTrACS v04r01 associations; state means use IMD 0.25-degree daily rainfall over active track dates.',
				`Interpolated positions meet the published ${CORE.meta.catalogue_version} gap-support contract.`
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
		const analysisCoverage = `${String(CORE.meta.coverage_start).slice(0, 10)} to ${String(CORE.meta.coverage_end).slice(0, 10)}`;
		const retainedCoverage = CORE.meta.first_position && CORE.meta.last_position
			? ` Retained event positions run from ${String(CORE.meta.first_position).slice(0, 10)} to ${String(CORE.meta.last_position).slice(0, 10)}.`
			: '';
		$('#mlaCoverageText').textContent = `Analysis source: ${analysisCoverage}; complete through ${COMPLETE_END_YEAR}.${retainedCoverage}`;
		$('#mlaBuildText').textContent = `Atlas ${CORE.meta.atlas_version}, built ${CORE.meta.built_utc}; ${fmt(CORE.meta.tracks)} physical events and ${fmt(CORE.meta.rows)} hourly positions.`;
		const version = $('#mlaCatalogueVersion');
		if (version) version.textContent = `LPS ${CORE.meta.catalogue_version}`;
		const rows = $('#mlaCatalogueRows');
		if (rows) rows.textContent = fmt(CORE.meta.rows);
		const tracks = $('#mlaCatalogueTracks');
		if (tracks) tracks.textContent = fmt(CORE.meta.tracks);
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
		else if (state.tab === 'forecast') window.dispatchEvent(new CustomEvent('mla:forecast-visible'));
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
		if (CLIMATE.track_count !== CORE.tracks.length || CLIMATE.bsiso.phase.length !== CORE.tracks.length || CLIMATE.mjo.phase.length !== CORE.tracks.length || CLIMATE.enso.class.length !== CORE.tracks.length) throw new Error('Climate-filter asset does not match the catalogue');
		catalogueStartDate = String(CORE.meta.coverage_start).slice(0, 10);
		catalogueEndDate = String(CORE.meta.coverage_end).slice(0, 10);
		state.yearMin = Number(catalogueStartDate.slice(0, 4));
		state.yearMax = Number(catalogueEndDate.slice(0, 4));
		state.dateMin = catalogueStartDate;
		state.dateMax = catalogueEndDate;
		for (const selector of ['#mlaDateMin', '#mlaDateMax']) {
			$(selector).min = catalogueStartDate;
			$(selector).max = catalogueEndDate;
		}
		T = Object.fromEntries(CORE.track_fields.map((name, index) => [name, index]));
		S = Object.fromEntries(CORE.series_fields.map((name, index) => [name, index]));
		Q = Object.fromEntries(CORE.qc_fields.map((name, index) => [name, index]));
		setLoading('Building a spatial index for responsive track selection…');
		await new Promise(resolve => setTimeout(resolve, 0));
		buildPathRuntime();
		setLoading('Indexing event labels, search and endpoint regions…');
		buildFallbackLabels();
		buildSearchIndex();
		buildEndpointRegions();
		setLoading('Preparing atlas controls and the requested view…');
		buildFilterControls();
		readUrl();
		if (state.stateFill !== 'none') await ensureDetail();
		bindTabs();
		bindControls();
		bindMap();
		updateBoundarySourceText();
		syncControls();
		setLoading('Applying filters and drawing the initial view…');
		applyFilters({noUrl: true});
		setLoading('Synchronising initial time controls…');
		updateTimeControls();
		if (state.weatherLayer !== 'none' && Number.isFinite(state.focusTimeMs)) syncWeatherToFocus();
		setLoading('Activating the requested atlas panel…');
		activateTab(state.tab, false);
		setLoading('Preparing data and provenance notes…');
		renderData();
		setLoading('Finishing the atlas view…');
		$('#mlaLoading').hidden = true;
		root.dataset.ready = 'true';
		void loadVisitCounter();
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
