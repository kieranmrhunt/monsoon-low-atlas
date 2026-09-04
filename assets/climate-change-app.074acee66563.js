(function () {
	'use strict';

	const root = document.getElementById('monsoon-low-atlas');
	const tab = document.getElementById('mlaTabClimateChange');
	const panel = document.getElementById('mlaPanelClimateChange');
	const configNode = document.getElementById('mla-data-config');
	if (!root || !tab || !panel || !configNode) return;

	let config;
	try {
		config = JSON.parse(configNode.textContent);
	} catch (_) {
		return;
	}
	if (!config.climateChangeBase) return;
	tab.hidden = false;

	const $ = selector => panel.querySelector(selector);
	const esc = value => String(value == null ? '' : value).replace(/[&<>"']/g, character => ({
		'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
	}[character]));
	const FONT = '"effra", Effra, Arial, sans-serif';
	const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
	const CLASSES = ['L', 'D', 'DD', 'CS', 'SCS', 'VSCS+'];
	const HISTORICAL_COLOUR = '#243665';
	const FUTURE_COLOUR = '#c6473b';
	const MODEL_COLOURS = ['#0072b2', '#d55e00', '#009e73', '#8f3b76', '#e69f00', '#cc79a7', '#8c6d1f', '#00a6a6', '#b2182b', '#6f4c9b', '#56b4e9', '#4d4d4d'];
	const MODEL_SOURCE_COLOURS = {
		'HadGEM3-GC31-LL': '#0072b2',
		'HadGEM3-GC31-MM': '#d55e00',
		'MIROC6': '#009e73',
		'MPI-ESM1-2-HR': '#8f3b76',
		'MPI-ESM1-2-LR': '#e69f00',
		'MRI-ESM2-0': '#cc79a7',
		'CNRM-CM6-1': '#8c6d1f',
		'EC-Earth3P': '#00a6a6',
		'EC-Earth3P-HR': '#b2182b'
	};
	const GWL_COLOURS = {'1.5': '#e69f00', '2': '#d55e00', '3': '#c33149', '4': '#6f4c9b'};
	let METRICS = {
		systems: {label: 'Systems', unit: 'yr⁻¹', digits: 1, zero: true},
		depressions_or_stronger: {label: 'Depressions or stronger', unit: 'yr⁻¹', digits: 1, zero: true},
		deep_depressions_or_stronger: {label: 'Deep depressions or stronger', unit: 'yr⁻¹', digits: 1, zero: true},
		cyclonic_storms_or_stronger: {label: 'Cyclonic storms or stronger', unit: 'yr⁻¹', digits: 1, zero: true},
		system_days: {label: 'System-days', unit: 'days yr⁻¹', digits: 1, zero: true},
		mean_duration_hours: {label: 'Mean duration', unit: 'h', digits: 0},
		mean_peak_wind_ms: {label: 'Mean peak circulation wind', unit: 'm s⁻¹', digits: 1},
		mean_peak_pressure_deficit_hpa: {label: 'Mean peak pressure deficit', unit: 'hPa', digits: 1},
		mean_peak_24h_precipitation_mm: {label: 'Mean peak 24 h precipitation', unit: 'mm', digits: 1}
	};
	const RAIN_METRICS = {
		exposed_mean_mm_day: {label: 'Rain within 800 km of LPS', unit: 'mm day⁻¹', digits: 1, changeMode: 'percent'},
		all_india_mean_mm_day: {label: 'All-India mean rain', unit: 'mm day⁻¹', digits: 1, changeMode: 'percent', regionalKey: 'regional_mean_mm_day'},
		rainfall_share: {label: 'Rainfall on LPS-exposed cell-days', unit: '%', digits: 1, fraction: true, changeMode: 'points'},
		climatological_excess_share: {label: 'LPS-day excess above climatology', unit: '%', digits: 1, fraction: true, changeMode: 'points'},
		month_control_excess_share: {label: 'LPS-day excess above same-month control', unit: '%', digits: 1, fraction: true, changeMode: 'points'},
		exposed_area_day_fraction: {label: 'LPS-exposed area-days', unit: '%', digits: 1, fraction: true, changeMode: 'points'},
		exposed_to_all_rain_ratio: {label: 'Exposed / all-day rain ratio', unit: '×', digits: 2, changeMode: 'percent'},
		heavy_20mm_exposed_cell_day_share: {label: '20 mm heavy-rain cell-days exposed', unit: '%', digits: 1, fraction: true, changeMode: 'points'},
		heavy_50mm_exposed_cell_day_share: {label: '50 mm heavy-rain cell-days exposed', unit: '%', digits: 1, fraction: true, changeMode: 'points'}
	};
	const VALID_SEASONS = new Set(['all', 'jjas', 'mam', 'ond', 'djf']);
	const VALID_VIEWS = new Set(['overview', 'tracks', 'rainfall', 'structure', 'evaluation']);
	const VALID_MAP_METRICS = new Set(['track_density', 'genesis_density', 'lysis_density']);
	const VALID_PROFILES = new Set(['vorticity', 'rh', 'q', 'temperature', 'core_temperature']);
	const PUBLISHED_STATUSES = new Set(['validated-production-window', 'multi-model-awaiting-review']);
	const state = {pair: '', basis: 'gwl', comparison: '', season: 'jjas', metric: 'systems', metricGroup: 'Frequency and class', rainMetric: 'exposed_mean_mm_day', mapMetric: 'track_density', profileMetric: 'vorticity', view: 'overview'};
	const cache = new Map();
	let index = null;
	let current = null;
	let resolutionControls = [];
	let geography = null;
	let loadingPromise = null;
	let pairSerial = 0;
	let layoutReady = false;
	let chartHits = [];
	let tooltip = null;

	function readState() {
		try {
			const stored = JSON.parse(localStorage.getItem('mla-climate-state-v2') || localStorage.getItem('mla-climate-state-v1') || '{}');
			if (typeof stored.pair === 'string') state.pair = stored.pair;
			if (['gwl', 'time-slice'].includes(stored.basis)) state.basis = stored.basis;
			if (typeof stored.comparison === 'string') state.comparison = stored.comparison;
			if (VALID_SEASONS.has(stored.season)) state.season = stored.season;
			if (typeof stored.metric === 'string') state.metric = stored.metric;
			if (typeof stored.metricGroup === 'string') state.metricGroup = stored.metricGroup;
			if (Object.hasOwn(RAIN_METRICS, stored.rainMetric)) state.rainMetric = stored.rainMetric;
			if (VALID_MAP_METRICS.has(stored.mapMetric)) state.mapMetric = stored.mapMetric;
			if (VALID_PROFILES.has(stored.profileMetric)) state.profileMetric = stored.profileMetric;
			if (VALID_VIEWS.has(stored.view)) state.view = stored.view;
		} catch (_) {
			// Private browsing can disable storage without disabling the atlas.
		}
		const parameters = new URLSearchParams(window.location.search);
		if (parameters.has('cmpair')) state.pair = parameters.get('cmpair');
		if (['gwl', 'time-slice'].includes(parameters.get('cmbasis'))) state.basis = parameters.get('cmbasis');
		if (parameters.has('cmcomparison')) state.comparison = parameters.get('cmcomparison');
		if (VALID_SEASONS.has(parameters.get('cmseason'))) state.season = parameters.get('cmseason');
		if (parameters.has('cmmetric')) state.metric = parameters.get('cmmetric');
		if (Object.hasOwn(RAIN_METRICS, parameters.get('cmrain'))) state.rainMetric = parameters.get('cmrain');
		if (VALID_MAP_METRICS.has(parameters.get('cmmap'))) state.mapMetric = parameters.get('cmmap');
		if (VALID_PROFILES.has(parameters.get('cmprofile'))) state.profileMetric = parameters.get('cmprofile');
		if (VALID_VIEWS.has(parameters.get('cmview'))) state.view = parameters.get('cmview');
	}

	function writeState() {
		try {
			localStorage.setItem('mla-climate-state-v2', JSON.stringify(state));
		} catch (_) {
			// URL state remains available when local storage is unavailable.
		}
		const url = new URL(window.location.href);
		url.searchParams.set('cmpair', state.pair);
		url.searchParams.set('cmbasis', state.basis);
		url.searchParams.set('cmcomparison', state.comparison);
		url.searchParams.set('cmseason', state.season);
		url.searchParams.set('cmmetric', state.metric);
		url.searchParams.set('cmrain', state.rainMetric);
		url.searchParams.set('cmmap', state.mapMetric);
		url.searchParams.set('cmprofile', state.profileMetric);
		url.searchParams.set('cmview', state.view);
		history.replaceState(null, '', url);
	}

	function baseUrl() {
		const value = String(config.climateChangeBase).replace(/\/?$/, '/');
		return new URL(value, window.location.href);
	}

	function assetUrl(path) {
		return new URL(path, baseUrl()).toString();
	}

	async function decodeJson(response) {
		if (!response.ok) throw new Error(`Climate asset returned ${response.status}`);
		const bytes = new Uint8Array(await response.arrayBuffer());
		if (bytes[0] !== 0x1f || bytes[1] !== 0x8b) return JSON.parse(new TextDecoder().decode(bytes));
		if (!('DecompressionStream' in window)) throw new Error('A current browser is required to open compressed climate assets.');
		const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
		return new Response(stream).json();
	}

	async function fetchJson(url, cacheMode = 'force-cache') {
		if (!cache.has(url)) cache.set(url, fetch(url, {cache: cacheMode}).then(decodeJson));
		return cache.get(url);
	}

	function pairLabel(pair) {
		if (pair.label) return pair.label;
		const future = pair.future.run;
		const screening = pair.historical && pair.historical.qa && pair.historical.qa.historical_screen
			? pair.historical.qa.historical_screen.screening_status
			: '';
		const review = screening === 'passes-basic-historical-screen' ? ' · passes historical screen' : screening === 'review-model-bias' ? ' · model bias' : '';
		return `${pair.source_label} · ${future.experiment_id.toUpperCase()} · ${pair.member_id}${review}`;
	}

	async function loadIndex() {
		const manifestUrl = assetUrl('manifest.json');
		const manifest = await fetchJson(manifestUrl, 'no-store');
		if (manifest.schema !== 'lps-atlas-cmip6-climate-index-v1' || !manifest.index || !manifest.index.path) {
			throw new Error('Climate manifest does not match this atlas.');
		}
		const loaded = await fetchJson(assetUrl(manifest.index.path));
		if (loaded.schema !== manifest.schema || loaded.status !== manifest.status || !PUBLISHED_STATUSES.has(loaded.status) || !Array.isArray(loaded.pairs) || !loaded.pairs.length) {
			throw new Error('No compatible climate experiment pairs are available.');
		}
		return loaded;
	}

	async function loadPair(pair) {
		const [historical, future, change, impact] = await Promise.all([
			fetchJson(assetUrl(pair.historical.url)),
			fetchJson(assetUrl(pair.future.url)),
			fetchJson(assetUrl(pair.change.url)),
			pair.impact ? fetchJson(assetUrl(pair.impact.url)) : Promise.resolve(null)
		]);
		if (historical.schema !== 'lps-atlas-cmip6-climate-summary-v2' || future.schema !== historical.schema) {
			throw new Error('Climate run assets use an unsupported schema.');
		}
		const expectedChangeSchema = pair.kind === 'multi-model'
			? 'lps-atlas-cmip6-multimodel-change-v2'
			: 'lps-atlas-cmip6-paired-change-v2';
		if (change.schema !== expectedChangeSchema) {
			throw new Error('Climate change asset uses an unsupported schema.');
		}
		const expectedImpactSchema = pair.kind === 'multi-model'
			? 'lps-atlas-cmip6-precipitation-impact-ensemble-v1'
			: 'lps-atlas-cmip6-precipitation-impact-pair-v1';
		if (impact && impact.schema !== expectedImpactSchema) {
			throw new Error('Climate precipitation asset uses an unsupported schema.');
		}
		return {pair, historical, future, change, impact};
	}

	async function loadResolutionControls() {
		return Promise.all((index.resolution_controls || []).map(async control => {
			const summary = await fetchJson(assetUrl(control.summary.url));
			if (summary.schema !== 'lps-atlas-cmip6-climate-summary-v2' || !summary.qa || !summary.qa.historical_screen) {
				throw new Error('ERA5 common-grid control uses an unsupported schema.');
			}
			return {...control, summary};
		}));
	}

	function comparisonBasis(pair) {
		return pair.comparison_basis || (pair.comparison && pair.comparison.basis) || 'time-slice';
	}

	function comparisonKey(pair) {
		const basis = comparisonBasis(pair);
		if (basis === 'gwl') return `gwl:${Number(pair.comparison.level_c)}`;
		return `time-slice:${String(pair.future.run.experiment_id).toLowerCase()}`;
	}

	function comparisonLabel(pair) {
		if (comparisonBasis(pair) === 'gwl') {
			return `+${Number(pair.comparison.level_c).toFixed(Number(pair.comparison.level_c) % 1 ? 1 : 0)} °C · ${String(pair.comparison.scenario).toUpperCase()}`;
		}
		const experiment = String(pair.future.run.experiment_id).toLowerCase();
		const scenario = experiment === 'highres-future' ? 'HighResMIP future' : experiment.toUpperCase();
		const windows = new Set(index.pairs.filter(item => comparisonKey(item) === comparisonKey(pair)).map(item => item.future.run.period_label));
		return `${scenario} · ${windows.size === 1 ? [...windows][0] : 'late-century windows'}`;
	}

	function comparisonGroups(basis = state.basis) {
		const groups = new Map();
		for (const pair of index.pairs.filter(item => comparisonBasis(item) === basis)) {
			const key = comparisonKey(pair);
			if (!groups.has(key)) groups.set(key, []);
			groups.get(key).push(pair);
		}
		return groups;
	}

	function preferredPair(pairs) {
		return pairs.find(pair => pair.kind === 'multi-model') || pairs[0];
	}

	function mergeMetricDefinitions(payload) {
		const supplied = payload && payload.metric_definitions;
		if (!supplied || typeof supplied !== 'object') return;
		METRICS = Object.fromEntries(Object.entries(supplied).map(([key, value]) => [key, {
			label: value.label || key,
			group: value.group || 'Other diagnostics',
			unit: value.unit || '',
			digits: Number.isFinite(Number(value.digits)) ? Number(value.digits) : 1,
			zero: Boolean(value.zero),
			changeMode: value.change_mode || 'percent',
			resolutionSensitive: Boolean(value.resolution_sensitive),
			description: value.description || ''
		}]));
	}

	function availableMetricSet() {
		const seasonal = current && current.change && current.change.seasonal_changes && current.change.seasonal_changes[state.season];
		if (seasonal) {
			const values = Object.entries(seasonal)
				.filter(([key, record]) => {
					const metric = METRICS[key];
					const fields = metric && changeFields(metric);
					return fields && record && numberAvailable(record.historical) && numberAvailable(record.future) && numberAvailable(record[fields.value]);
				})
				.map(([key]) => key);
			if (values.length) return new Set(values);
		}
		const values = current && current.pair && current.pair.capabilities && Array.isArray(current.pair.capabilities.available_metrics)
			? current.pair.capabilities.available_metrics
			: current && current.historical && current.historical.capabilities && current.historical.capabilities.available_metrics;
		return new Set(Array.isArray(values) && values.length ? values : Object.keys(METRICS));
	}

	function populateMetricControls() {
		const available = availableMetricSet();
		const requestedMetric = state.metric;
		if (!Object.hasOwn(METRICS, state.metric) || !available.has(state.metric)) state.metric = available.has('systems') ? 'systems' : [...available][0];
		const groups = new Map();
		for (const [key, metric] of Object.entries(METRICS)) {
			if (!groups.has(metric.group)) groups.set(metric.group, []);
			groups.get(metric.group).push([key, metric]);
		}
		const metricControl = $('#mlaClimateMetric');
		metricControl.replaceChildren(...[...groups].map(([group, values]) => {
			const node = document.createElement('optgroup');
			node.label = group;
			for (const [key, metric] of values) {
				const option = document.createElement('option');
				option.value = key;
				option.textContent = `${metric.label}${metric.resolutionSensitive ? ' · resolution-sensitive' : ''}`;
				option.disabled = !available.has(key);
				node.append(option);
			}
			return node;
		}));
		metricControl.value = state.metric;
		const selectedGroup = METRICS[state.metric] && METRICS[state.metric].group;
		if (state.metric !== requestedMetric || !groups.has(state.metricGroup)) state.metricGroup = selectedGroup || [...groups.keys()][0];
		const groupControl = $('#mlaClimateMetricGroup');
		groupControl.replaceChildren(...[...groups.keys()].map(group => {
			const option = document.createElement('option');
			option.value = group;
			option.textContent = group;
			return option;
		}));
		groupControl.value = state.metricGroup;
	}

	function populatePairControls() {
		if (!index.pairs.some(pair => pair.id === state.pair)) {
			const groups = comparisonGroups(state.basis);
			const requested = groups.get(state.comparison) || [...groups.values()][0];
			const fallback = requested && preferredPair(requested) || index.pairs.find(pair => pair.id === index.defaults.pair) || index.pairs[0];
			state.pair = fallback.id;
		}
		let selected = index.pairs.find(pair => pair.id === state.pair);
		state.basis = comparisonBasis(selected);
		state.comparison = comparisonKey(selected);
		const bases = [...new Set(index.pairs.map(comparisonBasis))];
		const basisControl = $('#mlaClimateBasis');
		for (const option of basisControl.options) option.disabled = !bases.includes(option.value);
		basisControl.value = state.basis;
		const groups = comparisonGroups();
		const comparisonControl = $('#mlaClimateComparison');
		comparisonControl.replaceChildren(...[...groups].map(([key, pairs]) => {
			const option = document.createElement('option');
			option.value = key;
			option.textContent = comparisonLabel(preferredPair(pairs));
			return option;
		}));
		if (!groups.has(state.comparison)) {
			state.comparison = groups.keys().next().value;
			selected = preferredPair(groups.get(state.comparison));
			state.pair = selected.id;
		}
		comparisonControl.value = state.comparison;
		const datasets = groups.get(state.comparison) || [];
		const datasetControl = $('#mlaClimateDataset');
		datasetControl.replaceChildren(...datasets.sort((left, right) => Number(right.kind === 'multi-model') - Number(left.kind === 'multi-model') || left.source_label.localeCompare(right.source_label)).map(pair => {
			const option = document.createElement('option');
			option.value = pair.id;
			const count = pair.kind === 'multi-model' ? ` · ${pair.model_ids.length} models` : ` · ${pair.member_id}`;
			option.textContent = `${pair.source_label}${count}`;
			return option;
		}));
		if (!datasets.some(pair => pair.id === state.pair)) state.pair = preferredPair(datasets).id;
		datasetControl.value = state.pair;
		$('#mlaClimateSeason').value = state.season;
		$('#mlaClimateRainMetric').value = state.rainMetric;
		$('#mlaClimateMapMetric').value = state.mapMetric;
		$('#mlaClimateProfileMetric').value = state.profileMetric;
	}

	function prepareLayout() {
		if (layoutReady) return;
		layoutReady = true;
		const content = $('#mlaClimateContent');
		const originalGrid = content.querySelector(':scope > .mla-chart-grid');
		const stats = $('#mlaClimateStats');
		const definitions = {
			overview: ['#mlaClimateModelChange', '#mlaClimateAnnualChart', '#mlaClimateMetricFamilyCard'],
			tracks: ['#mlaClimateDensityGrid', '#mlaClimateMonthlyChart', '#mlaClimateClassChart'],
			rainfall: ['#mlaClimateRainfallCard', '#mlaClimateRainDriversCard', '#mlaClimateRegionalRainfallCard', '#mlaClimateFootprintCard'],
			structure: ['#mlaClimateVerticalProfileCard', '#mlaClimateCompositeExpansionCard'],
			evaluation: ['#mlaClimateAvailabilityCard', '#mlaClimateHistoricalSkill', '#mlaClimateWarmingChange', '#mlaClimateGwl', '#mlaClimateInterpretationCard']
		};
		for (const [view, selectors] of Object.entries(definitions)) {
			const section = document.createElement('section');
			section.className = 'mla-climate-view';
			section.dataset.climateView = view;
			section.hidden = view !== state.view;
			const grid = document.createElement('div');
			grid.className = 'mla-chart-grid';
			if (view === 'overview') section.append(stats);
			if (view === 'rainfall') {
				const notice = document.createElement('p');
				notice.className = 'mla-climate-view-notice';
				notice.id = 'mlaClimateRainfallNotice';
				section.append(notice);
			}
			for (const selector of selectors) {
				const child = $(selector);
				const card = child && (child.classList.contains('mla-card') ? child : child.closest('.mla-card'));
				if (card) grid.append(card);
			}
			section.append(grid);
			content.append(section);
		}
		originalGrid.remove();
		tooltip = document.createElement('div');
		tooltip.className = 'mla-climate-tooltip';
		tooltip.hidden = true;
		document.body.append(tooltip);
	}

	function setView(view, shouldRender = true) {
		state.view = VALID_VIEWS.has(view) ? view : 'overview';
		panel.querySelectorAll('[data-climate-view]').forEach(node => { node.hidden = node.dataset.climateView !== state.view; });
		panel.querySelectorAll('[data-climate-view-button]').forEach(button => button.setAttribute('aria-selected', String(button.dataset.climateViewButton === state.view)));
		writeState();
		if (shouldRender) render();
	}

	function css(name, fallback) {
		return getComputedStyle(root).getPropertyValue(name).trim() || fallback;
	}

	function setupCanvas(canvas) {
		const bounds = canvas.getBoundingClientRect();
		const width = Math.max(260, bounds.width || canvas.clientWidth || 600);
		const height = Math.max(220, bounds.height || canvas.clientHeight || 300);
		const ratio = Math.min(window.devicePixelRatio || 1, 2);
		canvas.width = Math.round(width * ratio);
		canvas.height = Math.round(height * ratio);
		const context = canvas.getContext('2d');
		context.setTransform(ratio, 0, 0, ratio, 0, 0);
		context.clearRect(0, 0, width, height);
		context.font = `12px ${FONT}`;
		context.lineJoin = 'round';
		context.lineCap = 'round';
		return {context, width, height};
	}

	function finite(values) {
		return values.filter(numberAvailable).map(Number);
	}

	function numberAvailable(value) {
		return value !== null && value !== '' && value !== undefined && Number.isFinite(Number(value));
	}

	function extent(values, includeZero) {
		const clean = finite(values);
		if (!clean.length) return [0, 1];
		let minimum = Math.min(...clean);
		let maximum = Math.max(...clean);
		if (includeZero) minimum = Math.min(0, minimum);
		if (maximum === minimum) maximum = minimum + Math.max(1, Math.abs(minimum) * .1);
		const padding = (maximum - minimum) * .08;
		return [includeZero ? Math.max(0, minimum - padding) : minimum - padding, maximum + padding];
	}

	function drawAxes(context, plot, yExtent, yLabel) {
		const ink = css('--mla-muted', '#5f6574');
		const line = css('--mla-line', '#d8d9df');
		context.strokeStyle = line;
		context.fillStyle = ink;
		context.lineWidth = 1;
		context.textAlign = 'right';
		context.textBaseline = 'middle';
		for (let index = 0; index <= 4; index += 1) {
			const fraction = index / 4;
			const y = plot.bottom - fraction * (plot.bottom - plot.top);
			const value = yExtent[0] + fraction * (yExtent[1] - yExtent[0]);
			context.beginPath();
			context.moveTo(plot.left, y);
			context.lineTo(plot.right, y);
			context.stroke();
			context.fillText(value.toFixed(Math.abs(value) < 10 ? 1 : 0), plot.left - 7, y);
		}
		context.save();
		context.translate(13, (plot.top + plot.bottom) / 2);
		context.rotate(-Math.PI / 2);
		context.textAlign = 'center';
		context.fillText(yLabel, 0, 0);
		context.restore();
	}

	function drawLegend(context, items, x, y) {
		context.textAlign = 'left';
		context.textBaseline = 'middle';
		let cursor = x;
		for (const item of items) {
			context.strokeStyle = item.colour;
			context.lineWidth = 3;
			context.beginPath();
			context.moveTo(cursor, y);
			context.lineTo(cursor + 20, y);
			context.stroke();
			context.fillStyle = css('--mla-ink', '#202334');
			context.fillText(item.label, cursor + 26, y);
			cursor += 32 + context.measureText(item.label).width + 18;
		}
	}

	function drawAnnual() {
		const canvas = $('#mlaClimateAnnualChart');
		const {context, width, height} = setupCanvas(canvas);
		const metric = METRICS[state.metric];
		const historical = current.historical.seasonal[state.season].annual.map(row => row[state.metric] == null ? NaN : Number(row[state.metric]));
		const future = current.future.seasonal[state.season].annual.map(row => row[state.metric] == null ? NaN : Number(row[state.metric]));
		const yExtent = extent([...historical, ...future], metric.zero);
		const plot = {left: 58, right: width - 18, top: 31, bottom: height - 34};
		drawAxes(context, plot, yExtent, metric.unit);
		const x = (index, length) => plot.left + index / Math.max(1, length - 1) * (plot.right - plot.left);
		const y = value => plot.bottom - (value - yExtent[0]) / (yExtent[1] - yExtent[0]) * (plot.bottom - plot.top);
		for (const [values, colour] of [[historical, HISTORICAL_COLOUR], [future, FUTURE_COLOUR]]) {
			context.strokeStyle = colour;
			context.lineWidth = 2.2;
			context.beginPath();
			let open = false;
			values.forEach((value, index) => {
				if (!Number.isFinite(value)) { open = false; return; }
				if (!open) context.moveTo(x(index, values.length), y(value));
				else context.lineTo(x(index, values.length), y(value));
				open = true;
			});
			context.stroke();
		}
		context.fillStyle = css('--mla-muted', '#5f6574');
		context.textAlign = 'left';
		context.textBaseline = 'top';
		context.fillText('1', plot.left, plot.bottom + 8);
		context.textAlign = 'right';
		context.fillText(`${historical.length} / ${future.length}`, plot.right, plot.bottom + 8);
		context.textAlign = 'center';
		context.fillText('year within window (historical / future)', (plot.left + plot.right) / 2, plot.bottom + 8);
		drawLegend(context, [
			{label: current.historical.run.period_label, colour: HISTORICAL_COLOUR},
			{label: current.future.run.period_label, colour: FUTURE_COLOUR}
		], plot.left, 16);
	}

	function drawMonthly() {
		const canvas = $('#mlaClimateMonthlyChart');
		const {context, width, height} = setupCanvas(canvas);
		const left = 48, right = width - 12, top = 31, bottom = height - 34;
		const historical = current.historical.monthly.map(row => Number(row.systems_per_year));
		const future = current.future.monthly.map(row => Number(row.systems_per_year));
		const yExtent = extent([...historical, ...future], true);
		drawAxes(context, {left, right, top, bottom}, yExtent, 'systems yr⁻¹');
		const groupWidth = (right - left) / 12;
		const barWidth = Math.max(3, groupWidth * .31);
		const y = value => bottom - value / yExtent[1] * (bottom - top);
		for (let index = 0; index < 12; index += 1) {
			const centre = left + (index + .5) * groupWidth;
			for (const [value, colour, offset] of [[historical[index], HISTORICAL_COLOUR, -barWidth], [future[index], FUTURE_COLOUR, 0]]) {
				context.fillStyle = colour;
				context.fillRect(centre + offset, y(value), barWidth - 1, bottom - y(value));
			}
			context.fillStyle = css('--mla-muted', '#5f6574');
			context.textAlign = 'center';
			context.textBaseline = 'top';
			context.fillText(MONTHS[index], centre - .5, bottom + 8);
		}
		drawLegend(context, [{label: 'Historical', colour: HISTORICAL_COLOUR}, {label: 'Future', colour: FUTURE_COLOUR}], left, 16);
	}

	function drawClasses() {
		const canvas = $('#mlaClimateClassChart');
		const {context, width, height} = setupCanvas(canvas);
		const left = 48, right = width - 12, top = 31, bottom = height - 34;
		const historicalCounts = current.historical.seasonal[state.season].class_counts;
		const futureCounts = current.future.seasonal[state.season].class_counts;
		const historicalTotal = Object.values(historicalCounts).reduce((sum, value) => sum + Number(value), 0) || 1;
		const futureTotal = Object.values(futureCounts).reduce((sum, value) => sum + Number(value), 0) || 1;
		const historical = CLASSES.map((_, index) => 100 * Number(historicalCounts[String(index + 1)] || 0) / historicalTotal);
		const future = CLASSES.map((_, index) => 100 * Number(futureCounts[String(index + 1)] || 0) / futureTotal);
		const yExtent = extent([...historical, ...future], true);
		drawAxes(context, {left, right, top, bottom}, yExtent, 'share (%)');
		const groupWidth = (right - left) / CLASSES.length;
		const barWidth = Math.max(5, groupWidth * .3);
		const y = value => bottom - value / yExtent[1] * (bottom - top);
		CLASSES.forEach((label, index) => {
			const centre = left + (index + .5) * groupWidth;
			for (const [value, colour, offset] of [[historical[index], HISTORICAL_COLOUR, -barWidth], [future[index], FUTURE_COLOUR, 0]]) {
				context.fillStyle = colour;
				context.fillRect(centre + offset, y(value), barWidth - 1, bottom - y(value));
			}
			context.fillStyle = css('--mla-muted', '#5f6574');
			context.textAlign = 'center';
			context.textBaseline = 'top';
			context.fillText(label, centre - .5, bottom + 8);
		});
		drawLegend(context, [{label: 'Historical', colour: HISTORICAL_COLOUR}, {label: 'Future', colour: FUTURE_COLOUR}], left, 16);
	}

	function modelPair(modelId) {
		return index.pairs.find(pair => pair.kind !== 'multi-model' && pair.id === modelId) || null;
	}

	function modelLabel(modelId) {
		const pair = modelPair(modelId);
		return pair ? pair.source_label : String(modelId);
	}

	function modelColour(modelId, ordinal) {
		const source = modelPair(modelId) && modelPair(modelId).source_label;
		if (source && MODEL_SOURCE_COLOURS[source]) return MODEL_SOURCE_COLOURS[source];
		let sourceHash = 0;
		for (const character of String(source || modelId)) sourceHash = ((sourceHash << 5) - sourceHash + character.charCodeAt(0)) | 0;
		return MODEL_COLOURS[(Math.abs(sourceHash) || ordinal) % MODEL_COLOURS.length];
	}

	function selectedModelChanges() {
		const change = current.change.seasonal_changes[state.season][state.metric] || {};
		if (current.pair.kind === 'multi-model' && Array.isArray(change.models)) {
			return change.models.map((record, ordinal) => ({
				...record,
				label: modelLabel(record.id),
				colour: modelColour(record.id, ordinal)
			}));
		}
		return [{
			id: current.pair.id,
			label: current.pair.source_label,
			colour: modelColour(current.pair.id, 0),
			historical: change.historical,
			future: change.future,
			absolute_change: change.absolute_change,
			percent_change: change.percent_change,
			ci05: change.ci05,
			ci95: change.ci95,
			percent_ci05: change.percent_ci05,
			percent_ci95: change.percent_ci95
		}];
	}

	function signedPercent(value) {
		if (!Number.isFinite(Number(value))) return '—';
		const number = Number(value);
		return `${number > 0 ? '+' : ''}${number.toFixed(1)}%`;
	}

	function changeFields(metric = METRICS[state.metric]) {
		return metric.changeMode === 'absolute'
			? {value: 'absolute_change', low: 'ci05', high: 'ci95'}
			: {value: 'percent_change', low: 'percent_ci05', high: 'percent_ci95'};
	}

	function changeText(value, metric = METRICS[state.metric]) {
		if (!numberAvailable(value)) return '—';
		if (metric.changeMode !== 'absolute') return signedPercent(value);
		return valueText(value, metric, true);
	}

	function drawModelChanges() {
		const canvas = $('#mlaClimateModelChange');
		const {context, width, height} = setupCanvas(canvas);
		const metric = METRICS[state.metric];
		const fields = changeFields(metric);
		const records = selectedModelChanges().map(record => ({...record, plotValue: record[fields.value], plotLow: record[fields.low], plotHigh: record[fields.high]})).filter(record => numberAvailable(record.plotValue));
		const status = $('#mlaClimateModelChangeStatus');
		const data = $('#mlaClimateModelChangeData');
		if (!records.length) {
			context.fillStyle = css('--mla-muted', '#5f6574');
			context.textAlign = 'center';
			context.fillText('This measure is unavailable for the selected comparison.', width / 2, height / 2);
			status.textContent = '';
			data.textContent = '';
			return;
		}
		const intervalValues = finite(records.flatMap(record => [record.plotLow, record.plotValue, record.plotHigh]));
		const scale = Math.max(metric.changeMode === 'absolute' ? .1 : 5, Math.max(...intervalValues.map(Math.abs)) * 1.12);
		const left = Math.min(132, Math.max(90, width * .29)), right = width - 31, top = 28, bottom = height - 40;
		const rowGap = (bottom - top) / Math.max(1, records.length);
		const x = value => left + (Number(value) + scale) / (2 * scale) * (right - left);
		const ink = css('--mla-ink', '#202334'), muted = css('--mla-muted', '#5f6574'), line = css('--mla-line', '#d8d9df');
		context.strokeStyle = line;
		context.fillStyle = muted;
		context.lineWidth = 1;
		context.textAlign = 'center';
		context.textBaseline = 'top';
		for (const fraction of [-1, -.5, 0, .5, 1]) {
			const value = fraction * scale;
			const px = x(value);
			context.beginPath(); context.moveTo(px, top - 8); context.lineTo(px, bottom + 4); context.stroke();
			context.fillText(metric.changeMode === 'absolute' ? value.toFixed(Math.abs(scale) < 5 ? 1 : 0) : `${Math.round(value)}%`, px, bottom + 8);
		}
		context.strokeStyle = ink;
		context.lineWidth = 1.5;
		context.beginPath(); context.moveTo(x(0), top - 8); context.lineTo(x(0), bottom + 4); context.stroke();
		records.forEach((record, ordinal) => {
			const y = top + (ordinal + .5) * rowGap;
			context.fillStyle = ink;
			context.textAlign = 'right';
			context.textBaseline = 'middle';
			context.fillText(record.label, left - 9, y);
			const low = Number(record.plotLow), high = Number(record.plotHigh);
			if (numberAvailable(record.plotLow) && numberAvailable(record.plotHigh)) {
				context.strokeStyle = record.colour;
				context.lineWidth = 2;
				context.beginPath(); context.moveTo(x(low), y); context.lineTo(x(high), y); context.stroke();
				for (const value of [low, high]) { context.beginPath(); context.moveTo(x(value), y - 4); context.lineTo(x(value), y + 4); context.stroke(); }
			}
			context.fillStyle = record.colour;
			context.beginPath(); context.arc(x(record.plotValue), y, 5, 0, Math.PI * 2); context.fill();
			chartHits.push({canvas, x: x(record.plotValue), y, radius: 10, pairId: record.id, text: `${record.label}: ${changeText(record.plotValue, metric)} · click to inspect model`});
		});
		context.fillStyle = muted;
		context.textAlign = 'center';
		context.textBaseline = 'bottom';
		context.fillText(metric.changeMode === 'absolute' ? `future − historical (${metric.unit})` : 'future − historical (%)', (left + right) / 2, height - 2);
		const positive = records.filter(record => Number(record.plotValue) > 0).length;
		const negative = records.filter(record => Number(record.plotValue) < 0).length;
		const robustPositive = records.filter(record => Number(record.plotLow) > 0).length;
		const robustNegative = records.filter(record => Number(record.plotHigh) < 0).length;
		status.textContent = `${positive}/${records.length} increase · ${negative}/${records.length} decrease · ${robustPositive + robustNegative} intervals exclude zero · N=${records.length}${metric.resolutionSensitive ? ' · resolution-sensitive' : ''}`;
		data.innerHTML = `<table><thead><tr><th>Model</th><th>Historical</th><th>Future</th><th>Change</th><th>90% interval</th></tr></thead><tbody>${records.map(record => `<tr><td>${esc(record.label)}</td><td>${esc(valueText(record.historical, metric))}</td><td>${esc(valueText(record.future, metric))}</td><td>${esc(changeText(record.plotValue, metric))}</td><td>${esc(numberAvailable(record.plotLow) ? `${changeText(record.plotLow, metric)} to ${changeText(record.plotHigh, metric)}` : '—')}</td></tr>`).join('')}</tbody></table>`;
	}

	function warmingForModel(modelId) {
		const pair = modelPair(modelId);
		const value = pair && pair.warming ? Number(pair.warming.change_k) : NaN;
		return Number.isFinite(value) && value > 0 ? value : null;
	}

	function signedPerDegree(value) {
		if (!numberAvailable(value)) return '—';
		const number = Number(value);
		return `${number > 0 ? '+' : ''}${number.toFixed(1)}% °C⁻¹`;
	}

	function perDegreeText(value, metric = METRICS[state.metric]) {
		if (!numberAvailable(value)) return '—';
		const number = Number(value);
		return `${number > 0 ? '+' : ''}${number.toFixed(metric.digits)}${metric.changeMode === 'absolute' ? ` ${metric.unit} °C⁻¹` : '% °C⁻¹'}`;
	}

	function drawWarmingNormalisedChanges() {
		const canvas = $('#mlaClimateWarmingChange');
		const {context, width, height} = setupCanvas(canvas);
		const metric = METRICS[state.metric];
		const fields = changeFields(metric);
		const records = selectedModelChanges().map(record => {
			const warming = warmingForModel(record.id);
			return {
				...record,
				warming,
				normalised: warming && numberAvailable(record[fields.value]) ? Number(record[fields.value]) / warming : null,
				low: warming && numberAvailable(record[fields.low]) ? Number(record[fields.low]) / warming : null,
				high: warming && numberAvailable(record[fields.high]) ? Number(record[fields.high]) / warming : null
			};
		}).filter(record => numberAvailable(record.normalised));
		const status = $('#mlaClimateWarmingStatus');
		const data = $('#mlaClimateWarmingData');
		if (!records.length) {
			context.fillStyle = css('--mla-muted', '#5f6574');
			context.textAlign = 'center';
			context.fillText('Warming-normalised change is unavailable for this measure.', width / 2, height / 2);
			status.textContent = '';
			data.textContent = '';
			return;
		}
		const intervalValues = finite(records.flatMap(record => [record.low, record.normalised, record.high]));
		const scale = Math.max(2, Math.max(...intervalValues.map(Math.abs)) * 1.12);
		const left = Math.min(150, Math.max(96, width * .24)), right = width - 38, top = 28, bottom = height - 40;
		const rowGap = (bottom - top) / Math.max(1, records.length);
		const x = value => left + (Number(value) + scale) / (2 * scale) * (right - left);
		const ink = css('--mla-ink', '#202334'), muted = css('--mla-muted', '#5f6574'), line = css('--mla-line', '#d8d9df');
		context.strokeStyle = line;
		context.fillStyle = muted;
		context.lineWidth = 1;
		context.textAlign = 'center';
		context.textBaseline = 'top';
		for (const fraction of [-1, -.5, 0, .5, 1]) {
			const value = fraction * scale;
			const px = x(value);
			context.beginPath(); context.moveTo(px, top - 8); context.lineTo(px, bottom + 4); context.stroke();
			context.fillText(`${Math.round(value)}`, px, bottom + 8);
		}
		context.strokeStyle = ink;
		context.lineWidth = 1.5;
		context.beginPath(); context.moveTo(x(0), top - 8); context.lineTo(x(0), bottom + 4); context.stroke();
		records.forEach((record, ordinal) => {
			const y = top + (ordinal + .5) * rowGap;
			context.fillStyle = ink;
			context.textAlign = 'right';
			context.textBaseline = 'middle';
			context.fillText(record.label, left - 9, y);
			if (numberAvailable(record.low) && numberAvailable(record.high)) {
				context.strokeStyle = record.colour;
				context.lineWidth = 2;
				context.beginPath(); context.moveTo(x(record.low), y); context.lineTo(x(record.high), y); context.stroke();
				for (const value of [record.low, record.high]) { context.beginPath(); context.moveTo(x(value), y - 4); context.lineTo(x(value), y + 4); context.stroke(); }
			}
			context.fillStyle = record.colour;
			context.beginPath(); context.arc(x(record.normalised), y, 5, 0, Math.PI * 2); context.fill();
		});
		context.fillStyle = muted;
		context.textAlign = 'center';
		context.textBaseline = 'bottom';
		context.fillText(`paired change per degree of global warming (${metric.changeMode === 'absolute' ? `${metric.unit} °C⁻¹` : '% °C⁻¹'})`, (left + right) / 2, height - 2);
		const mean = records.reduce((sum, record) => sum + Number(record.normalised), 0) / records.length;
		const warmings = records.map(record => Number(record.warming));
		status.textContent = `${current.pair.kind === 'multi-model' ? 'Equal-model mean' : 'Response'} ${perDegreeText(mean, metric)} · global warming ${Math.min(...warmings).toFixed(2)}${records.length > 1 ? `–${Math.max(...warmings).toFixed(2)}` : ''} °C · N=${records.length}`;
		data.innerHTML = `<table><thead><tr><th>Model</th><th>Global warming</th><th>Response</th><th>Within-model 90% interval</th></tr></thead><tbody>${records.map(record => `<tr><td>${esc(record.label)}</td><td>+${Number(record.warming).toFixed(2)} °C</td><td>${esc(perDegreeText(record.normalised, metric))}</td><td>${esc(numberAvailable(record.low) ? `${perDegreeText(record.low, metric)} to ${perDegreeText(record.high, metric)}` : '—')}</td></tr>`).join('')}</tbody></table>`;
	}

	function publishedGwlRecords() {
		const pairs = current.pair.kind === 'multi-model'
			? current.pair.model_ids.map(modelPair).filter(Boolean)
			: [current.pair];
		return pairs.map(pair => {
			const reference = pair.warming ? pair : index.pairs.find(candidate => candidate.source_label === pair.source_label && candidate.member_id === pair.member_id && candidate.warming);
			return {
				id: pair.id,
				label: pair.source_label,
				crossings: reference && reference.warming && reference.warming.published_gwl ? reference.warming.published_gwl.crossings : []
			};
		}).filter(record => record.crossings.length);
	}

	function gwlWindowText(crossing) {
		return numberAvailable(crossing.central_year)
			? `${crossing.window_start_year}–${crossing.window_end_year} (${crossing.central_year})`
			: 'Not reached';
	}

	function drawPublishedGwl() {
		const canvas = $('#mlaClimateGwl');
		const {context, width, height} = setupCanvas(canvas);
		const records = publishedGwlRecords();
		const status = $('#mlaClimateGwlStatus');
		const data = $('#mlaClimateGwlData');
		if (!records.length) {
			context.fillStyle = css('--mla-muted', '#5f6574');
			context.textAlign = 'center';
			context.fillText('No published GWL crossings match this exact model run.', width / 2, height / 2);
			status.textContent = '';
			data.textContent = '';
			return;
		}
		const left = Math.min(150, Math.max(96, width * .24)), right = width - 34, top = 42, bottom = height - 40;
		const minimumYear = 2010, maximumYear = 2100;
		const x = year => left + (Number(year) - minimumYear) / (maximumYear - minimumYear) * (right - left);
		const rowGap = (bottom - top) / Math.max(1, records.length);
		const ink = css('--mla-ink', '#202334'), muted = css('--mla-muted', '#5f6574'), line = css('--mla-line', '#d8d9df');
		context.strokeStyle = line;
		context.fillStyle = muted;
		context.lineWidth = 1;
		context.textAlign = 'center';
		context.textBaseline = 'top';
		for (let year = 2020; year <= 2100; year += 20) {
			const px = x(year);
			context.beginPath(); context.moveTo(px, top - 8); context.lineTo(px, bottom + 4); context.stroke();
			context.fillText(String(year), px, bottom + 8);
		}
		records.forEach((record, ordinal) => {
			const y = top + (ordinal + .5) * rowGap;
			context.fillStyle = ink;
			context.textAlign = 'right';
			context.textBaseline = 'middle';
			context.fillText(record.label, left - 9, y);
			for (const crossing of record.crossings) {
				if (!numberAvailable(crossing.central_year)) continue;
				const level = String(Number(crossing.level_c));
				const px = x(crossing.central_year);
				context.strokeStyle = GWL_COLOURS[level] || '#555';
				context.globalAlpha = .34;
				context.lineWidth = 5;
				context.beginPath(); context.moveTo(x(crossing.window_start_year), y); context.lineTo(x(crossing.window_end_year), y); context.stroke();
				context.globalAlpha = 1;
				context.fillStyle = GWL_COLOURS[level] || '#555';
				context.beginPath(); context.arc(px, y, 6, 0, Math.PI * 2); context.fill();
			}
		});
		drawLegend(context, [1.5, 2, 3, 4].map(level => ({label: `${level} °C`, colour: GWL_COLOURS[String(level)]})), left, 17);
		context.fillStyle = muted;
		context.textAlign = 'center';
		context.textBaseline = 'bottom';
		context.fillText('central year of first 20-year GWL window', (left + right) / 2, height - 2);
		const levels = [1.5, 2, 3, 4];
		const counts = levels.map(level => records.filter(record => record.crossings.some(crossing => Number(crossing.level_c) === level && numberAvailable(crossing.central_year))).length);
		status.textContent = levels.map((level, position) => `${level} °C: ${counts[position]}/${records.length}`).join(' · ');
		data.innerHTML = `<table><thead><tr><th>Model</th>${levels.map(level => `<th>${level} °C window (central year)</th>`).join('')}</tr></thead><tbody>${records.map(record => `<tr><td>${esc(record.label)}</td>${levels.map(level => { const crossing = record.crossings.find(item => Number(item.level_c) === level); return `<td>${esc(crossing ? gwlWindowText(crossing) : 'Unavailable')}</td>`; }).join('')}</tr>`).join('')}</tbody></table>`;
	}

	function historicalScreenRecords() {
		let records;
		if (current.pair.kind === 'multi-model') {
			records = [...((current.historical.qa && current.historical.qa.historical_screening) || [])];
			if (!records.length) {
				records = current.pair.model_ids.map(modelPair).filter(Boolean).map(pair => ({
					id: pair.id,
					source_label: pair.source_label,
					...((pair.historical.qa && pair.historical.qa.historical_screen) || {})
				}));
			}
		} else {
			const screen = current.historical.qa && current.historical.qa.historical_screen;
			records = screen ? [{id: current.pair.id, source_label: current.pair.source_label, ...screen}] : [];
		}
		for (const control of resolutionControls) {
			const screen = control.summary.qa.historical_screen;
			records.push({
				id: control.id,
				source_label: control.label,
				is_resolution_control: true,
				...screen,
				jjas: (screen.seasonal || {}).jjas
			});
		}
		return records;
	}

	function screenForSeason(record) {
		if (state.season === 'all') return record;
		if (state.season !== 'jjas') return null;
		return current.pair.kind === 'multi-model' ? (record.jjas || (record.seasonal || {}).jjas) : (record.seasonal || {}).jjas;
	}

	function summaryMedianRatio(screen, key) {
		const model = screen && screen.model && screen.model[key];
		const reference = screen && screen.reference_metrics && screen.reference_metrics[key];
		const numerator = model && Number(model.median), denominator = reference && Number(reference.median);
		return Number.isFinite(numerator) && Number.isFinite(denominator) && denominator !== 0 ? numerator / denominator : null;
	}

	function historicalRatio(screen) {
		if (!screen) return null;
		const comparisons = screen.comparisons || {};
		const keys = {
			systems: 'event_frequency_ratio',
			depressions_or_stronger: 'depression_or_stronger_frequency_ratio',
			deep_depressions_or_stronger: 'deep_depression_or_stronger_frequency_ratio',
			cyclonic_storms_or_stronger: 'cyclonic_storm_or_stronger_frequency_ratio',
			system_days: 'system_days_ratio',
			mean_duration_hours: 'median_duration_ratio',
			mean_peak_wind_ms: 'median_peak_wind_ratio',
			mean_peak_pressure_deficit_hpa: 'median_peak_pressure_deficit_ratio'
		};
		if (state.metric === 'mean_peak_24h_precipitation_mm') return summaryMedianRatio(screen, 'peak_24h_precipitation_mm');
		const value = Number(comparisons[keys[state.metric]]);
		return Number.isFinite(value) ? value : null;
	}

	function drawHistoricalAgreement() {
		const canvas = $('#mlaClimateHistoricalSkill');
		const {context, width, height} = setupCanvas(canvas);
		const status = $('#mlaClimateHistoricalSkillStatus');
		const data = $('#mlaClimateHistoricalSkillData');
		const records = historicalScreenRecords().map((record, ordinal) => {
			const screen = screenForSeason(record);
			return {id: record.id, label: record.source_label || modelLabel(record.id), value: historicalRatio(screen), colour: record.is_resolution_control ? '#111111' : modelColour(record.id, ordinal), isControl: record.is_resolution_control};
		}).filter(record => Number.isFinite(record.value));
		if (!records.length) {
			context.fillStyle = css('--mla-muted', '#5f6574');
			context.textAlign = 'center';
			context.fillText('This measure is not in the current ERA5 screening summary.', width / 2, height / 2);
			status.textContent = state.season === 'all' || state.season === 'jjas' ? 'The reanalysis envelope is being expanded to the full variable set.' : 'Historical screening is currently available for All months and JJAS.';
			data.textContent = '';
			return;
		}
		const maximum = Math.max(2, Math.max(...records.map(record => record.value)) * 1.1);
		const left = Math.min(132, Math.max(90, width * .29)), right = width - 30, top = 28, bottom = height - 40;
		const rowGap = (bottom - top) / Math.max(1, records.length);
		const x = value => left + Number(value) / maximum * (right - left);
		const ink = css('--mla-ink', '#202334'), muted = css('--mla-muted', '#5f6574'), line = css('--mla-line', '#d8d9df');
		context.fillStyle = 'rgba(0, 158, 115, .10)';
		context.fillRect(x(.5), top - 8, Math.max(0, x(Math.min(2, maximum)) - x(.5)), bottom - top + 12);
		context.strokeStyle = line; context.lineWidth = 1; context.fillStyle = muted; context.textAlign = 'center'; context.textBaseline = 'top';
		for (let value = 0; value <= maximum + .001; value += maximum / 4) {
			const px = x(value); context.beginPath(); context.moveTo(px, top - 8); context.lineTo(px, bottom + 4); context.stroke(); context.fillText(value.toFixed(1), px, bottom + 8);
		}
		context.strokeStyle = ink; context.lineWidth = 1.5; context.beginPath(); context.moveTo(x(1), top - 8); context.lineTo(x(1), bottom + 4); context.stroke();
		records.forEach((record, ordinal) => {
			const y = top + (ordinal + .5) * rowGap;
			context.fillStyle = ink; context.textAlign = 'right'; context.textBaseline = 'middle'; context.fillText(record.label, left - 9, y);
			context.strokeStyle = record.colour; context.globalAlpha = .35; context.lineWidth = 2; context.beginPath(); context.moveTo(x(0), y); context.lineTo(x(record.value), y); context.stroke(); context.globalAlpha = 1;
			context.fillStyle = record.colour; context.beginPath(); context.arc(x(record.value), y, 5, 0, Math.PI * 2); context.fill();
		});
		context.fillStyle = muted; context.textAlign = 'center'; context.textBaseline = 'bottom'; context.fillText('model / ERA5', (left + right) / 2, height - 2);
		const modelRecords = records.filter(record => !record.isControl);
		const within = modelRecords.filter(record => record.value >= .5 && record.value <= 2).length;
		const control = records.find(record => record.isControl);
		const continuous = state.metric.startsWith('mean_');
		status.textContent = `${within}/${modelRecords.length} models within a factor of two${control ? ` · ERA5 at 1°: ${control.value.toFixed(2)}` : ''}${continuous ? ' · event medians' : ''}`;
		data.innerHTML = `<table><thead><tr><th>Dataset</th><th>Dataset / ERA5</th><th>Within factor two</th></tr></thead><tbody>${records.map(record => `<tr><td>${esc(record.label)}</td><td>${record.value.toFixed(2)}</td><td>${record.value >= .5 && record.value <= 2 ? 'Yes' : 'No'}</td></tr>`).join('')}</tbody></table>`;
	}

	function rainfallRecords() {
		if (!current.impact) return [];
		const change = current.impact.india_jjas_changes[state.rainMetric];
		if (!change) return [];
		if (current.pair.kind === 'multi-model' && Array.isArray(change.models)) {
			return change.models.map((record, ordinal) => ({
				...record,
				label: record.source_label || modelLabel(record.id),
				colour: modelColour(record.id, ordinal)
			}));
		}
		return [{...change, id: current.pair.id, label: current.pair.source_label, colour: modelColour(current.pair.id, 0)}];
	}

	function rainValueText(value, metric) {
		if (!numberAvailable(value)) return '—';
		const number = Number(value) * (metric.fraction ? 100 : 1);
		return metric.unit === '×' ? `${number.toFixed(metric.digits)}×` : `${number.toFixed(metric.digits)} ${metric.unit}`;
	}

	function rainChangeNumber(record, metric, position = 'change') {
		const fields = metric.changeMode === 'points'
			? {change: 'absolute_change', low: 'ci05', high: 'ci95'}
			: {change: 'percent_change', low: 'percent_ci05', high: 'percent_ci95'};
		const raw = record && record[fields[position]];
		if (raw === null || raw === '' || raw === undefined) return NaN;
		const value = Number(raw);
		return Number.isFinite(value) ? value * (metric.changeMode === 'points' ? 100 : 1) : NaN;
	}

	function rainChangeText(value, metric) {
		if (!Number.isFinite(Number(value))) return '—';
		const number = Number(value);
		return `${number > 0 ? '+' : ''}${number.toFixed(1)}${metric.changeMode === 'points' ? ' pp' : '%'}`;
	}

	function drawRainfallChanges() {
		const card = $('#mlaClimateRainfallCard');
		const available = Boolean(current.impact) && state.season === 'jjas';
		card.hidden = !available;
		if (!available) return;
		const canvas = $('#mlaClimateRainfallChange');
		const {context, width, height} = setupCanvas(canvas);
		const metric = RAIN_METRICS[state.rainMetric];
		const records = rainfallRecords().map(record => ({
			...record,
			plotChange: rainChangeNumber(record, metric),
			plotLow: rainChangeNumber(record, metric, 'low'),
			plotHigh: rainChangeNumber(record, metric, 'high')
		})).filter(record => numberAvailable(record.plotChange));
		const status = $('#mlaClimateRainfallStatus');
		const data = $('#mlaClimateRainfallData');
		const change = current.impact.india_jjas_changes[state.rainMetric];
		if (!records.length || !change) {
			context.fillStyle = css('--mla-muted', '#5f6574');
			context.textAlign = 'center';
			context.fillText('This rainfall measure is unavailable.', width / 2, height / 2);
			status.textContent = '';
			data.textContent = '';
			return;
		}
		const intervalValues = finite(records.flatMap(record => [record.plotLow, record.plotChange, record.plotHigh]));
		const scale = Math.max(metric.changeMode === 'points' ? 1 : 5, Math.max(...intervalValues.map(Math.abs)) * 1.12);
		const left = Math.min(158, Math.max(108, width * .25)), right = width - 31, top = 28, bottom = height - 40;
		const rowGap = (bottom - top) / Math.max(1, records.length);
		const x = value => left + (Number(value) + scale) / (2 * scale) * (right - left);
		const ink = css('--mla-ink', '#202334'), muted = css('--mla-muted', '#5f6574'), line = css('--mla-line', '#d8d9df');
		context.strokeStyle = line;
		context.fillStyle = muted;
		context.lineWidth = 1;
		context.textAlign = 'center';
		context.textBaseline = 'top';
		for (const fraction of [-1, -.5, 0, .5, 1]) {
			const value = fraction * scale;
			const px = x(value);
			context.beginPath(); context.moveTo(px, top - 8); context.lineTo(px, bottom + 4); context.stroke();
			context.fillText(`${Math.round(value)}%`, px, bottom + 8);
		}
		context.strokeStyle = ink;
		context.lineWidth = 1.5;
		context.beginPath(); context.moveTo(x(0), top - 8); context.lineTo(x(0), bottom + 4); context.stroke();
		records.forEach((record, ordinal) => {
			const y = top + (ordinal + .5) * rowGap;
			context.fillStyle = ink;
			context.textAlign = 'right';
			context.textBaseline = 'middle';
			context.fillText(record.label, left - 9, y);
			if (numberAvailable(record.plotLow) && numberAvailable(record.plotHigh)) {
				const low = Number(record.plotLow), high = Number(record.plotHigh);
				context.strokeStyle = record.colour;
				context.lineWidth = 2;
				context.beginPath(); context.moveTo(x(low), y); context.lineTo(x(high), y); context.stroke();
				for (const value of [low, high]) { context.beginPath(); context.moveTo(x(value), y - 4); context.lineTo(x(value), y + 4); context.stroke(); }
			}
			context.fillStyle = record.colour;
			context.beginPath(); context.arc(x(record.plotChange), y, 5, 0, Math.PI * 2); context.fill();
		});
		context.fillStyle = muted;
		context.textAlign = 'center';
		context.textBaseline = 'bottom';
		context.fillText(metric.changeMode === 'points' ? 'future − historical (percentage points)' : 'future − historical (%)', (left + right) / 2, height - 2);
		const positive = records.filter(record => record.plotChange > 0).length;
		const robust = records.filter(record => record.plotLow > 0 || record.plotHigh < 0).length;
		const plotChange = rainChangeNumber(change, metric);
		const plotLow = rainChangeNumber(change, metric, 'low');
		const plotHigh = rainChangeNumber(change, metric, 'high');
		status.textContent = current.pair.kind === 'multi-model'
			? `${rainChangeText(plotChange, metric)} equal-model mean (${rainChangeText(plotLow, metric)} to ${rainChangeText(plotHigh, metric)}) · ${positive}/${records.length} models increase · ${robust}/${records.length} individual intervals exclude zero`
			: `${rainChangeText(plotChange, metric)} (${rainChangeText(plotLow, metric)} to ${rainChangeText(plotHigh, metric)})`;
		data.innerHTML = `<table><thead><tr><th>Model</th><th>Historical</th><th>Future</th><th>Change</th><th>90% interval</th></tr></thead><tbody>${records.map(record => `<tr><td>${esc(record.label)}</td><td>${esc(rainValueText(record.historical, metric))}</td><td>${esc(rainValueText(record.future, metric))}</td><td>${esc(rainChangeText(record.plotChange, metric))}</td><td>${esc(numberAvailable(record.plotLow) ? `${rainChangeText(record.plotLow, metric)} to ${rainChangeText(record.plotHigh, metric)}` : '—')}</td></tr>`).join('')}</tbody></table>`;
	}

	function regionalRainfallRecords() {
		if (!current.impact || !current.impact.regional_india_jjas_changes) return [];
		const metric = RAIN_METRICS[state.rainMetric];
		const metricKey = metric.regionalKey || state.rainMetric;
		return Object.values(current.impact.regional_india_jjas_changes).map(region => {
			const change = region.changes && region.changes[metricKey];
			if (!change) return null;
			const modelValues = Array.isArray(change.models)
				? change.models.map(model => rainChangeNumber(model, metric)).filter(Number.isFinite)
				: [];
			const positiveModels = modelValues.filter(value => value > 0).length;
			const negativeModels = modelValues.filter(value => value < 0).length;
			return {
				...region,
				...change,
				plotChange: rainChangeNumber(change, metric),
				plotLow: rainChangeNumber(change, metric, 'low'),
				plotHigh: rainChangeNumber(change, metric, 'high'),
				positiveModels,
				negativeModels,
				modelValues
			};
		}).filter(record => record && numberAvailable(record.plotChange));
	}

	function drawRegionalRainfall() {
		const card = $('#mlaClimateRegionalRainfallCard');
		const records = state.season === 'jjas' ? regionalRainfallRecords() : [];
		card.hidden = !records.length;
		if (!records.length) return;
		const canvas = $('#mlaClimateRegionalRainfall');
		const {context, width, height} = setupCanvas(canvas);
		const metric = RAIN_METRICS[state.rainMetric];
		const values = finite(records.flatMap(record => [record.plotLow, record.plotChange, record.plotHigh]));
		const scale = Math.max(metric.changeMode === 'points' ? 1 : 5, Math.max(...values.map(Math.abs)) * 1.12);
		const left = Math.min(150, Math.max(112, width * .22)), right = width - 30, top = 20, bottom = height - 40;
		const rowGap = (bottom - top) / records.length;
		const x = value => left + (Number(value) + scale) / (2 * scale) * (right - left);
		const ink = css('--mla-ink', '#202334'), muted = css('--mla-muted', '#5f6574'), line = css('--mla-line', '#d8d9df');
		context.strokeStyle = line; context.fillStyle = muted; context.lineWidth = 1; context.textAlign = 'center'; context.textBaseline = 'top';
		for (const fraction of [-1, -.5, 0, .5, 1]) {
			const value = fraction * scale, px = x(value);
			context.beginPath(); context.moveTo(px, top - 6); context.lineTo(px, bottom + 4); context.stroke();
			context.fillText(metric.changeMode === 'points' ? value.toFixed(1) : `${Math.round(value)}%`, px, bottom + 8);
		}
		context.strokeStyle = ink; context.lineWidth = 1.5; context.beginPath(); context.moveTo(x(0), top - 6); context.lineTo(x(0), bottom + 4); context.stroke();
		records.forEach((record, ordinal) => {
			const y = top + (ordinal + .5) * rowGap;
			context.fillStyle = ink; context.textAlign = 'right'; context.textBaseline = 'middle'; context.fillText(record.label, left - 9, y);
			const colour = record.plotChange >= 0 ? '#2166ac' : '#b2182b';
			if (numberAvailable(record.plotLow) && numberAvailable(record.plotHigh)) {
				context.strokeStyle = colour; context.lineWidth = 2;
				context.beginPath(); context.moveTo(x(record.plotLow), y); context.lineTo(x(record.plotHigh), y); context.stroke();
				for (const value of [record.plotLow, record.plotHigh]) { context.beginPath(); context.moveTo(x(value), y - 4); context.lineTo(x(value), y + 4); context.stroke(); }
			}
			context.fillStyle = colour; context.beginPath(); context.arc(x(record.plotChange), y, 5, 0, Math.PI * 2); context.fill();
		});
		context.fillStyle = muted; context.textAlign = 'center'; context.textBaseline = 'bottom';
		context.fillText(metric.changeMode === 'points' ? 'future − historical (percentage points)' : 'future − historical (%)', (left + right) / 2, height - 2);
		const increasing = records.filter(record => record.plotChange > 0).length;
		const robustAgreement = records.filter(record => {
			if (!record.modelValues.length) return false;
			return Math.max(record.positiveModels, record.negativeModels) >= Math.ceil(.8 * record.modelValues.length);
		}).length;
		$('#mlaClimateRegionalRainfallStatus').textContent = current.pair.kind === 'multi-model'
			? `${increasing}/${records.length} regions increase · at least 80% of models agree on the sign in ${robustAgreement}/${records.length}`
			: `${increasing}/${records.length} regions increase in this model`;
		$('#mlaClimateRegionalRainfallData').innerHTML = `<table><thead><tr><th>Region</th><th>Historical</th><th>Future</th><th>Change</th><th>90% interval</th><th>Model sign</th></tr></thead><tbody>${records.map(record => `<tr><td>${esc(record.label)}</td><td>${esc(rainValueText(record.historical, metric))}</td><td>${esc(rainValueText(record.future, metric))}</td><td>${esc(rainChangeText(record.plotChange, metric))}</td><td>${esc(`${rainChangeText(record.plotLow, metric)} to ${rainChangeText(record.plotHigh, metric)}`)}</td><td>${record.modelValues.length ? `${record.positiveModels} ↑ / ${record.negativeModels} ↓` : '—'}</td></tr>`).join('')}</tbody></table>`;
	}

	function matrixMean(matrix) {
		const values = finite((matrix || []).flat());
		return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : NaN;
	}

	function drawFootprintMap(canvas, matrix, mode, scale, longitudes, latitudes) {
		const {context, width, height} = setupCanvas(canvas);
		if (!Array.isArray(matrix) || !matrix.length) {
			context.fillStyle = css('--mla-muted', '#5f6574');
			context.textAlign = 'center';
			context.fillText('No footprint available', width / 2, height / 2);
			return;
		}
		const left = 43, top = 10;
		const size = Math.max(110, Math.min(width - left - 10, height - top - 72));
		const plot = {left, right: left + size, top, bottom: top + size};
		const west = Number(longitudes[0]) - .5, east = Number(longitudes.at(-1)) + .5;
		const south = Number(latitudes[0]) - .5, north = Number(latitudes.at(-1)) + .5;
		const project = (lon, lat) => [
			plot.left + (lon - west) / (east - west) * size,
			plot.bottom - (lat - south) / (north - south) * size
		];
		const sequential = [[255, 255, 245], [205, 239, 98], [254, 211, 76], [245, 121, 60], [181, 45, 109], [75, 20, 112]];
		const diverging = [[178, 24, 43], [239, 138, 98], [255, 255, 255], [103, 169, 207], [33, 102, 172]];
		context.fillStyle = '#fff';
		context.fillRect(plot.left, plot.top, size, size);
		for (let row = 0; row < matrix.length; row += 1) {
			for (let column = 0; column < matrix[row].length; column += 1) {
				if (!numberAvailable(matrix[row][column])) continue;
				const value = Number(matrix[row][column]);
				const fraction = mode === 'change' ? .5 + .5 * value / scale : Math.sqrt(Math.max(0, value) / scale);
				context.fillStyle = interpolateColour(mode === 'change' ? diverging : sequential, fraction);
				const [x0, y0] = project(Number(longitudes[column]) - .5, Number(latitudes[row]) + .5);
				const [x1, y1] = project(Number(longitudes[column]) + .5, Number(latitudes[row]) - .5);
				context.fillRect(x0, y0, Math.max(1, x1 - x0 + .4), Math.max(1, y1 - y0 + .4));
			}
		}
		context.strokeStyle = 'rgba(32,35,52,.65)';
		context.lineWidth = 1;
		context.strokeRect(plot.left, plot.top, size, size);
		const [centreX, centreY] = project(0, 0);
		context.strokeStyle = '#111';
		context.lineWidth = 1.5;
		context.beginPath(); context.arc(centreX, centreY, 4.5, 0, Math.PI * 2); context.stroke();
		context.fillStyle = css('--mla-muted', '#5f6574');
		context.font = `11px ${FONT}`;
		context.textAlign = 'center';
		context.textBaseline = 'top';
		for (const value of [-10, -5, 0, 5, 10]) {
			const [x] = project(value, south);
			context.fillText(String(value), x, plot.bottom + 5);
		}
		context.fillText('relative longitude (°)', (plot.left + plot.right) / 2, plot.bottom + 20);
		context.textAlign = 'right';
		context.textBaseline = 'middle';
		for (const value of [-10, -5, 0, 5, 10]) {
			const [, y] = project(west, value);
			context.fillText(String(value), plot.left - 5, y);
		}
		context.save();
		context.translate(11, (plot.top + plot.bottom) / 2);
		context.rotate(-Math.PI / 2);
		context.textAlign = 'center';
		context.fillText('relative latitude (°)', 0, 0);
		context.restore();
		const legendWidth = Math.min(150, size * .62), legendX = plot.left + (size - legendWidth) / 2, legendY = plot.bottom + 42;
		for (let index = 0; index < legendWidth; index += 1) {
			context.fillStyle = interpolateColour(mode === 'change' ? diverging : sequential, index / Math.max(1, legendWidth - 1));
			context.fillRect(legendX + index, legendY, 1.2, 7);
		}
		context.fillStyle = css('--mla-ink', '#202334');
		context.textBaseline = 'top';
		context.textAlign = 'left';
		context.fillText(mode === 'change' ? `−${scale.toFixed(1)}` : '0', legendX, legendY + 9);
		context.textAlign = 'center';
		context.fillText('mm / 24 h', legendX + legendWidth / 2, legendY + 9);
		context.textAlign = 'right';
		context.fillText(mode === 'change' ? `+${scale.toFixed(1)}` : scale.toFixed(1), legendX + legendWidth, legendY + 9);
	}

	function drawFootprints() {
		const card = $('#mlaClimateFootprintCard');
		card.hidden = !current.impact;
		if (!current.impact) return;
		const footprint = current.impact.storm_centred_precipitation;
		const season = footprint.seasons[state.season];
		const status = $('#mlaClimateFootprintStatus');
		if (!season || !Array.isArray(season.historical_mean_mm)) {
			for (const selector of ['#mlaClimateFootprintHistorical', '#mlaClimateFootprintFuture', '#mlaClimateFootprintChange']) {
				drawFootprintMap($(selector), null, 'sequential', 1, [], []);
			}
			status.textContent = 'No storm-centred precipitation samples are available for this season.';
			return;
		}
		const historicalValues = finite(season.historical_mean_mm.flat());
		const futureValues = finite(season.future_mean_mm.flat());
		const changeValues = finite(season.change_mm.flat());
		const sequentialScale = Math.max(.1, quantile([...historicalValues, ...futureValues], .98));
		const changeScale = Math.max(.1, quantile(changeValues.map(Math.abs), .98));
		const longitudes = footprint.relative_longitude_deg, latitudes = footprint.relative_latitude_deg;
		drawFootprintMap($('#mlaClimateFootprintHistorical'), season.historical_mean_mm, 'sequential', sequentialScale, longitudes, latitudes);
		drawFootprintMap($('#mlaClimateFootprintFuture'), season.future_mean_mm, 'sequential', sequentialScale, longitudes, latitudes);
		drawFootprintMap($('#mlaClimateFootprintChange'), season.change_mm, 'change', changeScale, longitudes, latitudes);
		const row = latitudes.reduce((best, value, index) => Math.abs(value) < Math.abs(latitudes[best]) ? index : best, 0);
		const column = longitudes.reduce((best, value, index) => Math.abs(value) < Math.abs(longitudes[best]) ? index : best, 0);
		const domain = (matrixMean(season.future_mean_mm) / matrixMean(season.historical_mean_mm) - 1) * 100;
		const centre = (Number(season.future_mean_mm[row][column]) / Number(season.historical_mean_mm[row][column]) - 1) * 100;
		const seasonLabel = state.season === 'all' ? 'All months' : state.season.toUpperCase();
		if (current.pair.kind === 'multi-model') {
			status.textContent = `${season.model_count} models · ${seasonLabel} domain mean ${signedPercent(domain)} · centre ${signedPercent(centre)}`;
		} else {
			status.textContent = `${season.historical_samples} historical and ${season.future_samples} future systems · ${seasonLabel} domain mean ${signedPercent(domain)} · centre ${signedPercent(centre)}`;
		}
	}

	function interpolateColour(stops, fraction) {
		const value = Math.max(0, Math.min(1, fraction)) * (stops.length - 1);
		const lower = Math.floor(value);
		const upper = Math.min(stops.length - 1, lower + 1);
		const weight = value - lower;
		const rgb = stops[lower].map((channel, index) => Math.round(channel * (1 - weight) + stops[upper][index] * weight));
		return `rgb(${rgb.join(',')})`;
	}

	function pathRing(context, points, project) {
		let open = false;
		for (const point of points) {
			if (!Array.isArray(point) || point.length < 2) continue;
			const [x, y] = project(Number(point[0]), Number(point[1]));
			if (!open) context.moveTo(x, y);
			else context.lineTo(x, y);
			open = true;
		}
	}

	function drawGeography(context, project, fillLand) {
		if (!geography) return;
		if (fillLand && Array.isArray(geography.land)) {
			context.fillStyle = css('--mla-land', '#eee9da');
			for (const ring of geography.land) {
				context.beginPath();
				pathRing(context, ring, project);
				context.fill();
			}
		}
		if (!fillLand && Array.isArray(geography.land)) {
			context.strokeStyle = 'rgba(39,42,54,.62)';
			context.lineWidth = .75;
			for (const ring of geography.land) {
				context.beginPath();
				pathRing(context, ring, project);
				context.stroke();
			}
		}
		context.strokeStyle = 'rgba(39,42,54,.48)';
		context.lineWidth = .65;
		for (const item of geography.borders || []) {
			context.beginPath();
			pathRing(context, item.p || [], project);
			context.stroke();
		}
	}

	function drawDensityMap(canvas, density, years, mode, scale) {
		const {context, width, height} = setupCanvas(canvas);
		const bounds = {west: 50, east: 110, south: -5, north: 40};
		const plot = {left: 38, right: width - 10, top: 10, bottom: height - 34};
		const project = (lon, lat) => [
			plot.left + (lon - bounds.west) / (bounds.east - bounds.west) * (plot.right - plot.left),
			plot.bottom - (lat - bounds.south) / (bounds.north - bounds.south) * (plot.bottom - plot.top)
		];
		context.fillStyle = css('--mla-sea', '#e9f2f3');
		context.fillRect(plot.left, plot.top, plot.right - plot.left, plot.bottom - plot.top);
		drawGeography(context, project, true);
		const latitudes = density.latitude_edges;
		const longitudes = density.longitude_edges;
		const matrix = density.unique_track_counts;
		const sequential = [[255, 255, 245], [255, 220, 121], [247, 139, 61], [192, 54, 92], [76, 26, 112]];
		const diverging = [[178, 24, 43], [239, 138, 98], [255, 255, 255], [103, 169, 207], [33, 102, 172]];
		const divergingMode = mode !== 'sequential';
		for (let row = 0; row < matrix.length; row += 1) {
			const south = latitudes[row], north = latitudes[row + 1];
			if (north < bounds.south || south > bounds.north) continue;
			for (let column = 0; column < matrix[row].length; column += 1) {
				const west = longitudes[column], east = longitudes[column + 1];
				if (east < bounds.west || west > bounds.east) continue;
				const value = Number(matrix[row][column]) / years;
				if (!Number.isFinite(value) || (mode === 'sequential' && value <= 0)) continue;
				const fraction = divergingMode ? .5 + .5 * value / scale : Math.sqrt(Math.max(0, value) / scale);
				context.fillStyle = interpolateColour(divergingMode ? diverging : sequential, fraction);
				const [x0, y0] = project(west, north);
				const [x1, y1] = project(east, south);
				context.fillRect(x0, y0, Math.max(1, x1 - x0 + .35), Math.max(1, y1 - y0 + .35));
			}
		}
		drawGeography(context, project, false);
		context.fillStyle = css('--mla-muted', '#5f6574');
		context.textBaseline = 'top';
		context.textAlign = 'center';
		for (const lon of [50, 70, 90, 110]) {
			const [x] = project(lon, bounds.south);
			context.fillText(`${lon}°E`, x, plot.bottom + 7);
		}
		context.textAlign = 'right';
		context.textBaseline = 'middle';
		for (const lat of [0, 10, 20, 30, 40]) {
			const [, y] = project(bounds.west, lat);
			context.fillText(lat === 0 ? '0°' : `${lat}°N`, plot.left - 5, y);
		}
		const legendWidth = Math.min(118, (plot.right - plot.left) * .34);
		const legendX = plot.right - legendWidth;
		const legendY = plot.top + 7;
		for (let index = 0; index < legendWidth; index += 1) {
			context.fillStyle = interpolateColour(divergingMode ? diverging : sequential, index / Math.max(1, legendWidth - 1));
			context.fillRect(legendX + index, legendY, 1.2, 7);
		}
		context.fillStyle = css('--mla-ink', '#202334');
		context.textBaseline = 'top';
		context.textAlign = 'left';
		context.fillText(mode === 'agreement' ? 'fewer' : mode === 'change' ? `−${scale.toFixed(1)}` : '0', legendX, legendY + 9);
		if (mode === 'agreement') {
			context.textAlign = 'center';
			context.fillText('mixed', legendX + legendWidth / 2, legendY + 9);
		}
		context.textAlign = 'right';
		context.fillText(mode === 'agreement' ? 'more' : mode === 'change' ? `+${scale.toFixed(1)}` : scale.toFixed(1), legendX + legendWidth, legendY + 9);
	}

	function differenceDensity(historical, future, historicalYears, futureYears) {
		return {
			latitude_edges: historical.latitude_edges,
			longitude_edges: historical.longitude_edges,
			unique_track_counts: historical.unique_track_counts.map((row, rowIndex) => row.map((value, columnIndex) =>
				Number(future.unique_track_counts[rowIndex][columnIndex]) / futureYears - Number(value) / historicalYears
			))
		};
	}

	function quantile(values, probability) {
		const sorted = finite(values).sort((left, right) => left - right);
		if (!sorted.length) return 0;
		const position = (sorted.length - 1) * probability;
		const lower = Math.floor(position), upper = Math.ceil(position);
		return lower === upper ? sorted[lower] : sorted[lower] * (upper - position) + sorted[upper] * (position - lower);
	}

	function drawMaps() {
		const spatialKey = state.mapMetric;
		const historical = current.historical.seasonal[state.season][spatialKey] || current.historical.seasonal[state.season].track_density;
		const future = current.future.seasonal[state.season][spatialKey] || current.future.seasonal[state.season].track_density;
		const labels = {track_density: ['Track-density change', 'Unique tracks per 1° cell per year; systems are grouped by genesis season.'], genesis_density: ['Genesis-density change', 'First published centres per 1° cell per year.'], lysis_density: ['Lysis-density change', 'Last published centres per 1° cell per year.']};
		$('#mlaClimateDensityHeading').textContent = labels[spatialKey][0];
		$('#mlaClimateDensitySubtitle').textContent = labels[spatialKey][1];
		const historicalYears = Number(current.historical.coverage.years);
		const futureYears = Number(current.future.coverage.years);
		const historicalValues = historical.unique_track_counts.flat().map(value => Number(value) / historicalYears);
		const futureValues = future.unique_track_counts.flat().map(value => Number(value) / futureYears);
		const sequentialScale = Math.max(.1, quantile([...historicalValues, ...futureValues], .98));
		const difference = differenceDensity(historical, future, historicalYears, futureYears);
		const differenceValues = difference.unique_track_counts.flat().map(Number);
		const differenceScale = Math.max(.05, quantile(differenceValues.map(Math.abs), .98));
		drawDensityMap($('#mlaClimateHistoricalMap'), historical, historicalYears, 'sequential', sequentialScale);
		drawDensityMap($('#mlaClimateFutureMap'), future, futureYears, 'sequential', sequentialScale);
		drawDensityMap($('#mlaClimateChangeMap'), difference, 1, 'change', differenceScale);
		const agreement = spatialKey === 'track_density' && current.pair.kind === 'multi-model'
			&& current.change.track_density_agreement
			&& current.change.track_density_agreement[state.season];
		const agreementPanel = $('#mlaClimateAgreementPanel');
		const agreementDetails = $('#mlaClimateDensityAgreementDetails');
		const densityGrid = $('#mlaClimateDensityGrid');
		agreementPanel.hidden = !agreement;
		agreementDetails.hidden = !agreement;
		densityGrid.classList.toggle('mla-climate-density-grid', Boolean(agreement));
		if (!agreement) {
			$('#mlaClimateDensityAgreementStatus').textContent = '';
			return;
		}
		drawDensityMap($('#mlaClimateAgreementMap'), {
			latitude_edges: agreement.latitude_edges,
			longitude_edges: agreement.longitude_edges,
			unique_track_counts: agreement.signed_agreement_fraction
		}, 1, 'agreement', 1);
		const summary = agreement.summary || {};
		const threshold = Number(summary.robust_model_threshold || Math.ceil(.8 * agreement.model_count));
		const changed = Number(summary.cells_with_any_change || 0);
		const robust = Number(summary.cells_at_least_80_percent_agreement || 0);
		const unanimous = Number(summary.cells_unanimous || 0);
		const agreementLead = threshold === Number(agreement.model_count)
			? `All ${agreement.model_count} models agree on the sign`
			: `At least ${threshold}/${agreement.model_count} models agree on the sign`;
		$('#mlaClimateDensityAgreementStatus').textContent = `${agreementLead} in ${robust.toLocaleString()} of ${changed.toLocaleString()} cells with any projected change.`;
		$('#mlaClimateDensityAgreementData').innerHTML = `<table><thead><tr><th>Diagnostic</th><th>Cells</th></tr></thead><tbody><tr><td>Any model changes</td><td>${changed.toLocaleString()}</td></tr><tr><td>At least ${threshold}/${agreement.model_count} agree</td><td>${robust.toLocaleString()}</td></tr><tr><td>All ${agreement.model_count} agree</td><td>${unanimous.toLocaleString()}</td></tr></tbody></table>`;
	}

	function metricChangeRecords(metricKey) {
		const metric = METRICS[metricKey];
		const change = current.change.seasonal_changes[state.season][metricKey];
		if (!metric || !change) return [];
		const fields = changeFields(metric);
		const source = current.pair.kind === 'multi-model' && Array.isArray(change.models)
			? change.models
			: [{id: current.pair.id, ...change}];
		return source.map((record, ordinal) => ({
			id: record.id,
			label: modelLabel(record.id),
			colour: modelColour(record.id, ordinal),
			value: record[fields.value],
			low: record[fields.low],
			high: record[fields.high]
		})).filter(record => numberAvailable(record.value));
	}

	function drawMetricFamily() {
		const canvas = $('#mlaClimateMetricFamilyChart');
		const metrics = Object.entries(METRICS).filter(([, metric]) => metric.group === state.metricGroup && current.change.seasonal_changes[state.season]);
		const rows = metrics.map(([key, metric]) => {
			const records = metricChangeRecords(key);
			const fields = changeFields(metric);
			const ensemble = current.change.seasonal_changes[state.season][key] || {};
			return {key, metric, records, ensembleValue: ensemble[fields.value], modelCount: records.length};
		}).filter(row => row.records.length && numberAvailable(row.ensembleValue));
		canvas.style.height = `${Math.max(270, 76 + rows.length * 31)}px`;
		const {context, width, height} = setupCanvas(canvas);
		const status = $('#mlaClimateMetricFamilyStatus');
		const data = $('#mlaClimateMetricFamilyData');
		if (!rows.length) {
			context.fillStyle = css('--mla-muted', '#5f6574');
			context.textAlign = 'center';
			context.fillText('No measures in this family are available for the selected comparison.', width / 2, height / 2);
			status.textContent = '';
			data.textContent = '';
			return;
		}
		const modelIds = current.pair.kind === 'multi-model' ? current.pair.model_ids : [current.pair.id];
		const left = Math.min(220, Math.max(145, width * .27));
		const summaryWidth = Math.min(132, width * .25);
		const right = width - summaryWidth - 12;
		const top = 60, bottom = height - 22;
		const rowGap = (bottom - top) / rows.length;
		const cellGap = 3;
		const cellWidth = Math.max(12, (right - left) / Math.max(1, modelIds.length));
		const negative = [[178, 24, 43], [255, 247, 247]], positive = [[247, 251, 255], [33, 102, 172]];
		context.fillStyle = css('--mla-muted', '#5f6574');
		context.textAlign = 'center';
		context.textBaseline = 'bottom';
		modelIds.forEach((id, ordinal) => {
			const x = left + (ordinal + .5) * cellWidth;
			const label = modelLabel(id).replace('MPI-ESM1-2-', 'MPI-');
			context.save(); context.translate(x, top - 7); context.rotate(-.55); context.fillText(label, 0, 0); context.restore();
		});
		context.textAlign = 'left';
		context.fillText('Ensemble change', right + 10, top - 7);
		rows.forEach((row, rowIndex) => {
			const y = top + rowIndex * rowGap;
			context.fillStyle = row.key === state.metric ? css('--mla-indigo-deep', '#243665') : css('--mla-ink', '#202334');
			context.font = `${row.key === state.metric ? '650' : '400'} 11px ${FONT}`;
			context.textAlign = 'right';
			context.textBaseline = 'middle';
			context.fillText(row.metric.label, left - 8, y + rowGap / 2);
			const maximum = Math.max(...row.records.map(record => Math.abs(Number(record.value))), .000001);
			modelIds.forEach((id, ordinal) => {
				const record = row.records.find(item => item.id === id);
				const x = left + ordinal * cellWidth;
				context.fillStyle = css('--mla-card', '#fff');
				context.fillRect(x + cellGap / 2, y + 2, cellWidth - cellGap, Math.max(8, rowGap - 4));
				if (!record) return;
				const value = Number(record.value), magnitude = Math.min(1, Math.abs(value) / maximum);
				context.fillStyle = interpolateColour(value < 0 ? negative : positive, .25 + .75 * magnitude);
				context.fillRect(x + cellGap / 2, y + 2, cellWidth - cellGap, Math.max(8, rowGap - 4));
				chartHits.push({canvas, x: x + cellWidth / 2, y: y + rowGap / 2, radius: Math.max(9, cellWidth / 2), pairId: record.id, text: `${modelLabel(record.id)} · ${row.metric.label}: ${changeText(value, row.metric)}`});
			});
			context.font = `600 11px ${FONT}`;
			context.textAlign = 'left';
			context.fillStyle = css('--mla-ink', '#202334');
			context.fillText(`${changeText(row.ensembleValue, row.metric)} · N=${row.modelCount}`, right + 10, y + rowGap / 2);
		});
		context.font = `11px ${FONT}`;
		status.textContent = 'Red = decrease; blue = increase. Saturation is scaled within each row, so compare sign and model agreement rather than colour magnitude between variables.';
		const headings = modelIds.map(id => `<th>${esc(modelLabel(id))}</th>`).join('');
		data.innerHTML = `<table><thead><tr><th>Measure</th>${headings}<th>Ensemble</th></tr></thead><tbody>${rows.map(row => `<tr><td>${esc(row.metric.label)}</td>${modelIds.map(id => { const record = row.records.find(item => item.id === id); return `<td>${esc(record ? changeText(record.value, row.metric) : '—')}</td>`; }).join('')}<td>${esc(changeText(row.ensembleValue, row.metric))} · N=${row.modelCount}</td></tr>`).join('')}</tbody></table>`;
	}

	const PROFILE_METRICS = {
		vorticity: {label: 'Relative vorticity', unit: '10⁻⁵ s⁻¹', keys: ['mean_lifetime_vorticity_850_x1e5_s1', 'mean_lifetime_vorticity_700_x1e5_s1', 'mean_lifetime_vorticity_500_x1e5_s1']},
		rh: {label: 'Relative humidity', unit: '%', keys: ['mean_rh850_pct', 'mean_rh700_pct', 'mean_rh500_pct']},
		q: {label: 'Specific humidity', unit: 'g kg⁻¹', keys: ['mean_q850_gkg', 'mean_q700_gkg', 'mean_q500_gkg']},
		temperature: {label: 'Temperature', unit: 'K', keys: ['mean_t850_k', 'mean_t700_k', 'mean_t500_k']},
		core_temperature: {label: 'Core temperature anomaly', unit: 'K', keys: ['mean_t850_core_anomaly_k', 'mean_t700_core_anomaly_k', 'mean_t500_core_anomaly_k']}
	};

	function drawVerticalProfile() {
		const canvas = $('#mlaClimateVerticalProfile');
		const {context, width, height} = setupCanvas(canvas);
		const profile = PROFILE_METRICS[state.profileMetric];
		const levels = [850, 700, 500];
		const records = profile.keys.map((key, index) => {
			const change = current.change.seasonal_changes[state.season][key];
			return change && numberAvailable(change.historical) && numberAvailable(change.future)
				? {level: levels[index], historical: Number(change.historical), future: Number(change.future), modelCount: Number(change.model_count || 1)}
				: null;
		}).filter(Boolean);
		const status = $('#mlaClimateVerticalProfileStatus');
		const data = $('#mlaClimateVerticalProfileData');
		if (records.length < 2) {
			context.fillStyle = css('--mla-muted', '#5f6574'); context.textAlign = 'center';
			context.fillText('This vertical profile is unavailable for the selected dataset.', width / 2, height / 2);
			status.textContent = ''; data.textContent = ''; return;
		}
		const values = records.flatMap(record => [record.historical, record.future]);
		const xExtent = extent(values, false);
		const plot = {left: 62, right: width - 24, top: 25, bottom: height - 45};
		const x = value => plot.left + (value - xExtent[0]) / (xExtent[1] - xExtent[0]) * (plot.right - plot.left);
		const y = level => plot.bottom - (850 - level) / 350 * (plot.bottom - plot.top);
		const muted = css('--mla-muted', '#5f6574'), line = css('--mla-line', '#d8d9df');
		context.strokeStyle = line; context.fillStyle = muted; context.lineWidth = 1; context.textAlign = 'right'; context.textBaseline = 'middle';
		for (const level of levels) { const py = y(level); context.beginPath(); context.moveTo(plot.left, py); context.lineTo(plot.right, py); context.stroke(); context.fillText(`${level}`, plot.left - 8, py); }
		context.save(); context.translate(13, (plot.top + plot.bottom) / 2); context.rotate(-Math.PI / 2); context.textAlign = 'center'; context.fillText('pressure (hPa)', 0, 0); context.restore();
		context.textAlign = 'center'; context.textBaseline = 'top';
		for (let ordinal = 0; ordinal <= 4; ordinal += 1) { const value = xExtent[0] + ordinal / 4 * (xExtent[1] - xExtent[0]), px = x(value); context.fillText(value.toFixed(Math.abs(value) < 10 ? 1 : 0), px, plot.bottom + 9); }
		for (const [key, colour, label] of [['historical', HISTORICAL_COLOUR, 'Historical'], ['future', FUTURE_COLOUR, 'Future']]) {
			context.strokeStyle = colour; context.lineWidth = 2.5; context.beginPath();
			records.forEach((record, index) => { const px = x(record[key]), py = y(record.level); if (!index) context.moveTo(px, py); else context.lineTo(px, py); }); context.stroke();
			for (const record of records) { const px = x(record[key]), py = y(record.level); context.fillStyle = colour; context.beginPath(); context.arc(px, py, 5, 0, Math.PI * 2); context.fill(); chartHits.push({canvas, x: px, y: py, radius: 10, text: `${label} · ${record.level} hPa: ${record[key].toFixed(2)} ${profile.unit}`}); }
		}
		context.fillStyle = muted; context.textAlign = 'center'; context.textBaseline = 'bottom'; context.fillText(profile.unit, (plot.left + plot.right) / 2, height - 2);
		drawLegend(context, [{label: current.historical.run.period_label, colour: HISTORICAL_COLOUR}, {label: current.future.run.period_label, colour: FUTURE_COLOUR}], plot.left, 13);
		const counts = records.map(record => record.modelCount);
		status.textContent = `${profile.label} along track centres over system lifecycles · N=${Math.min(...counts)}${Math.max(...counts) !== Math.min(...counts) ? `–${Math.max(...counts)}` : ''} model${Math.max(...counts) === 1 ? '' : 's'}.`;
		data.innerHTML = `<table><thead><tr><th>Level</th><th>Historical</th><th>Future</th><th>N</th></tr></thead><tbody>${records.map(record => `<tr><td>${record.level} hPa</td><td>${record.historical.toFixed(2)} ${esc(profile.unit)}</td><td>${record.future.toFixed(2)} ${esc(profile.unit)}</td><td>${record.modelCount}</td></tr>`).join('')}</tbody></table>`;
	}

	function drawRainDrivers() {
		const card = $('#mlaClimateRainDriversCard');
		const available = Boolean(current.impact) && state.season === 'jjas';
		card.hidden = !available;
		if (!available) return;
		const changes = current.impact.india_jjas_changes || {};
		const value = (key, role) => changes[key] && Number(changes[key][role]);
		const ratioContribution = (future, historical) => future > 0 && historical > 0 ? 100 * Math.log(future / historical) : NaN;
		const activeHistorical = value('active_lps', 'historical'), activeFuture = value('active_lps', 'future');
		const areaHistorical = value('exposed_area_day_fraction', 'historical'), areaFuture = value('exposed_area_day_fraction', 'future');
		const intensityHistorical = value('exposed_mean_mm_day', 'historical'), intensityFuture = value('exposed_mean_mm_day', 'future');
		const backgroundHistorical = value('all_india_mean_mm_day', 'historical'), backgroundFuture = value('all_india_mean_mm_day', 'future');
		const shareHistorical = value('rainfall_share', 'historical'), shareFuture = value('rainfall_share', 'future');
		const records = [
			{label: 'LPS occurrence', value: ratioContribution(activeFuture, activeHistorical)},
			{label: 'Footprint per active LPS', value: ratioContribution(areaFuture / activeFuture, areaHistorical / activeHistorical)},
			{label: 'Exposed-day rain intensity', value: ratioContribution(intensityFuture, intensityHistorical)},
			{label: 'All-India rain background', value: -ratioContribution(backgroundFuture, backgroundHistorical)}
		].filter(record => Number.isFinite(record.value));
		const canvas = $('#mlaClimateRainDrivers');
		const {context, width, height} = setupCanvas(canvas);
		const status = $('#mlaClimateRainDriversStatus'), data = $('#mlaClimateRainDriversData');
		if (records.length !== 4) { context.fillStyle = css('--mla-muted', '#5f6574'); context.textAlign = 'center'; context.fillText('Rainfall-share decomposition is unavailable.', width / 2, height / 2); status.textContent = ''; data.textContent = ''; return; }
		const observed = ratioContribution(shareFuture, shareHistorical), explained = records.reduce((sum, record) => sum + record.value, 0), residual = observed - explained;
		if (Math.abs(residual) > .01) records.push({label: 'Aggregation residual', value: residual});
		const scale = Math.max(2, Math.max(...records.map(record => Math.abs(record.value))) * 1.18);
		const left = Math.min(175, width * .38), right = width - 28, top = 20, bottom = height - 42, rowGap = (bottom - top) / records.length;
		const x = value => left + (value + scale) / (2 * scale) * (right - left);
		context.strokeStyle = css('--mla-line', '#d8d9df'); context.beginPath(); context.moveTo(x(0), top); context.lineTo(x(0), bottom); context.stroke();
		records.forEach((record, ordinal) => { const y = top + (ordinal + .5) * rowGap; context.fillStyle = css('--mla-ink', '#202334'); context.textAlign = 'right'; context.textBaseline = 'middle'; context.fillText(record.label, left - 8, y); context.fillStyle = record.value >= 0 ? '#2166ac' : '#b2182b'; const start = x(Math.min(0, record.value)), end = x(Math.max(0, record.value)); context.fillRect(start, y - 7, Math.max(1, end - start), 14); context.textAlign = record.value >= 0 ? 'left' : 'right'; context.fillText(`${record.value > 0 ? '+' : ''}${record.value.toFixed(1)}`, x(record.value) + (record.value >= 0 ? 5 : -5), y); });
		context.fillStyle = css('--mla-muted', '#5f6574'); context.textAlign = 'center'; context.textBaseline = 'bottom'; context.fillText('contribution to log change (%)', (left + right) / 2, height - 2);
		status.textContent = `Observed rainfall-share log change ${observed > 0 ? '+' : ''}${observed.toFixed(1)}% · components sum to ${explained > 0 ? '+' : ''}${explained.toFixed(1)}%.`;
		data.innerHTML = `<table><thead><tr><th>Component</th><th>Log-change contribution</th></tr></thead><tbody>${records.map(record => `<tr><td>${esc(record.label)}</td><td>${record.value > 0 ? '+' : ''}${record.value.toFixed(2)}%</td></tr>`).join('')}</tbody></table>`;
	}

	function renderAvailability() {
		const container = $('#mlaClimateAvailability');
		const groups = new Map();
		for (const pair of index.pairs) {
			const key = comparisonKey(pair);
			if (!groups.has(key)) groups.set(key, []);
			groups.get(key).push(pair);
		}
		const rows = [...groups.values()].map(pairs => {
			const pair = preferredPair(pairs);
			const models = pair.kind === 'multi-model' ? pair.model_ids.length : pairs.filter(item => item.kind !== 'multi-model').length;
			const capability = pair.capabilities || {};
			const sourcePairs = pair.kind === 'multi-model' ? pairs.filter(item => item.kind !== 'multi-model') : [pair];
			const completeFractions = sourcePairs.flatMap(item => ['historical', 'future'].map(role => Number(item[role] && item[role].qa && item[role].qa.checks && item[role].qa.checks.physics_complete_fraction))).filter(Number.isFinite);
			const completeness = completeFractions.length ? {minimum: Math.min(...completeFractions), maximum: Math.max(...completeFractions)} : null;
			return {pair, models, metricCount: Number(capability.metric_count || (capability.available_metrics || []).length), rain: Boolean(pair.impact || capability.precipitation_impacts), completeness};
		}).sort((left, right) => comparisonBasis(left.pair).localeCompare(comparisonBasis(right.pair)) || comparisonLabel(left.pair).localeCompare(comparisonLabel(right.pair)));
		const cells = ['<strong role="columnheader">Comparison</strong>', '<strong role="columnheader">Models</strong>', '<strong role="columnheader">Track measures</strong>', '<strong role="columnheader">All-physics rows</strong>', '<strong role="columnheader">India rain</strong>'];
		for (const row of rows) {
			const selected = comparisonKey(row.pair) === state.comparison;
			const completeText = row.completeness
				? `${Math.round(100 * row.completeness.minimum)}${Math.round(100 * row.completeness.maximum) !== Math.round(100 * row.completeness.minimum) ? `–${Math.round(100 * row.completeness.maximum)}` : ''}%`
				: 'Unavailable';
			const completeClass = !row.completeness ? 'mla-climate-capability-no' : row.completeness.minimum >= .99 ? 'mla-climate-capability-yes' : 'mla-climate-capability-partial';
			cells.push(`<span${selected ? ' aria-current="true"' : ''}>${esc(comparisonLabel(row.pair))}</span>`, `<span>${row.models}</span>`, `<span class="${row.metricCount ? 'mla-climate-capability-yes' : 'mla-climate-capability-no'}">${row.metricCount}/${Object.keys(METRICS).length}</span>`, `<span class="${completeClass}">${completeText}</span>`, `<span class="${row.rain ? 'mla-climate-capability-yes' : 'mla-climate-capability-no'}">${row.rain ? 'Available' : 'Processing'}</span>`);
		}
		container.innerHTML = cells.join('');
		$('#mlaClimateAvailabilityStatus').textContent = `${rows.length} source-backed comparison${rows.length === 1 ? '' : 's'} currently published. Missing diagnostics remain disabled rather than estimated.`;
	}

	function valueText(value, metric, signed = false) {
		if (!Number.isFinite(Number(value))) return '—';
		const number = Number(value);
		const prefix = signed && number > 0 ? '+' : '';
		return `${prefix}${number.toFixed(metric.digits)} ${metric.unit}`;
	}

	function renderStats() {
		const metric = METRICS[state.metric];
		const change = current.change.seasonal_changes[state.season][state.metric];
		const fields = changeFields(metric);
		let cards = [
			['Historical mean', valueText(change.historical, metric), current.historical.run.period_label],
			['Future mean', valueText(change.future, metric), current.future.run.period_label],
			['Paired change', changeText(change[fields.value], metric), metric.changeMode === 'absolute' ? 'absolute difference' : valueText(change.absolute_change, metric, true)],
			['90% bootstrap interval', `${changeText(change[fields.low], metric)} to ${changeText(change[fields.high], metric)}`, 'annual resampling']
		];
		if (current.pair.kind === 'multi-model') {
			const models = Array.isArray(change.models) ? change.models : [];
			const positive = models.filter(model => Number(model.absolute_change) > 0).length;
			const negative = models.filter(model => Number(model.absolute_change) < 0).length;
			const spreadValues = finite(models.map(model => model[fields.value]));
			const spread = spreadValues.length
				? `${changeText(quantile(spreadValues, .05), metric)} to ${changeText(quantile(spreadValues, .95), metric)}`
				: '—';
			cards = [
				['Historical mean', valueText(change.historical, metric), 'one model, one vote'],
				['Future mean', valueText(change.future, metric), 'one model, one vote'],
				['Mean paired change', changeText(change[fields.value], metric), metric.changeMode === 'absolute' ? 'absolute difference' : valueText(change.absolute_change, metric, true)],
				['Across-model 90% range', spread, `${spreadValues.length} models with defined change`],
				['Model agreement', `${positive}/${models.length} increase`, `${negative}/${models.length} decrease`]
			];
		}
		const warming = current.pair.warming;
		if (warming) {
			if (current.pair.kind === 'multi-model') {
				cards.push([
					'Global warming',
					`+${Number(warming.mean_change_k).toFixed(2)} °C`,
					`${Number(warming.minimum_change_k).toFixed(2)}–${Number(warming.maximum_change_k).toFixed(2)} °C across models`
				]);
			} else {
				cards.push([
					'Global warming',
					`+${Number(warming.change_k).toFixed(2)} °C`,
					`${current.historical.run.period_label} to ${current.future.run.period_label}`
				]);
			}
		} else if (comparisonBasis(current.pair) === 'gwl') {
			cards.push(['Global warming level', `+${Number(current.pair.comparison.level_c).toFixed(1)} °C`, `${String(current.pair.comparison.scenario).toUpperCase()} first-crossing window`]);
		}
		const container = $('#mlaClimateStats');
		container.replaceChildren(...cards.map(([label, value, note]) => {
			const card = document.createElement('section');
			card.className = 'mla-card mla-stat';
			const labelNode = document.createElement('span');
			labelNode.textContent = label;
			const valueNode = document.createElement('strong');
			valueNode.textContent = value;
			const noteNode = document.createElement('small');
			noteNode.textContent = note;
			card.append(labelNode, valueNode, noteNode);
			return card;
		}));
	}

	function render() {
		if (!current || panel.hidden) return;
		const metric = METRICS[state.metric];
		$('#mlaClimateAnnualHeading').textContent = `Annual ${metric.label.toLowerCase()}`;
		const scope = $('#mlaClimateScope');
		const preview = index.status === 'multi-model-awaiting-review';
		scope.dataset.tone = preview ? 'review' : '';
		const selectedChange = current.change.seasonal_changes[state.season][state.metric] || {};
		const selectedFields = changeFields(metric);
		const definedModelCount = current.pair.kind === 'multi-model' && Array.isArray(selectedChange.models)
			? selectedChange.models.filter(model => numberAvailable(model[selectedFields.value])).length
			: 1;
		const modelCount = Number(definedModelCount || selectedChange.model_count || (current.pair.kind === 'multi-model' ? current.pair.model_ids.length : 1));
		scope.textContent = (current.pair.kind === 'multi-model'
			? `${modelCount} models for this measure · equal weight`
			: `${current.pair.source_label} · single model`) + (metric.resolutionSensitive ? ' · resolution-sensitive' : '') + (preview ? ' · research preview' : '');
		$('#mlaClimateMetricNote').textContent = metric.description || `${metric.label}; ${metric.changeMode === 'absolute' ? 'absolute' : 'relative'} paired change in ${metric.unit}.`;
		$('#mlaClimateHistoricalMapHeading').textContent = current.historical.run.period_label;
		$('#mlaClimateFutureMapHeading').textContent = current.future.run.period_label;
		const warmingCard = $('#mlaClimateWarmingChange').closest('.mla-card');
		warmingCard.hidden = comparisonBasis(current.pair) === 'gwl';
		const rainNotice = $('#mlaClimateRainfallNotice');
		rainNotice.hidden = Boolean(current.impact) && state.season === 'jjas';
		rainNotice.textContent = current.impact
			? 'India-wide and regional attribution is currently JJAS-only; the storm-centred footprint below follows the selected genesis season.'
			: `India-wide and storm-footprint rainfall diagnostics are still processing for ${comparisonLabel(current.pair)}. Track-centred precipitation remains available from the main measure selector.`;
		chartHits = [];
		requestAnimationFrame(() => {
			if (state.view === 'overview') {
				renderStats();
				drawModelChanges();
				drawAnnual();
				drawMetricFamily();
			} else if (state.view === 'tracks') {
				drawMaps();
				drawMonthly();
				drawClasses();
			} else if (state.view === 'rainfall') {
				drawRainfallChanges();
				drawRainDrivers();
				drawRegionalRainfall();
				drawFootprints();
			} else if (state.view === 'structure') {
				drawVerticalProfile();
			} else if (state.view === 'evaluation') {
				renderAvailability();
				drawHistoricalAgreement();
				if (comparisonBasis(current.pair) !== 'gwl') drawWarmingNormalisedChanges();
				drawPublishedGwl();
			}
		});
	}

	function showLoadError(error) {
		console.error(error);
		$('#mlaClimateContent').hidden = true;
		$('#mlaClimateLoading').hidden = false;
		$('#mlaClimateLoading').textContent = 'Climate experiments could not be loaded.';
	}

	async function selectPair() {
		const serial = ++pairSerial;
		const pair = index.pairs.find(candidate => candidate.id === state.pair) || index.pairs[0];
		state.pair = pair.id;
		$('#mlaClimateLoading').hidden = false;
		$('#mlaClimateLoading').textContent = 'Loading paired climate diagnostics…';
		$('#mlaClimateContent').hidden = true;
		const loaded = await loadPair(pair);
		if (serial !== pairSerial) return;
		current = loaded;
		mergeMetricDefinitions(current.historical);
		if (!current.historical.seasonal[state.season] || !current.future.seasonal[state.season]) state.season = 'all';
		populatePairControls();
		populateMetricControls();
		$('#mlaClimateLoading').hidden = true;
		$('#mlaClimateContent').hidden = false;
		setView(state.view, false);
		writeState();
		render();
	}

	async function activate() {
		if (loadingPromise) return loadingPromise;
		loadingPromise = (async () => {
			try {
				if (!index) {
					$('#mlaClimateLoading').textContent = 'Opening CMIP6 comparisons…';
					index = await loadIndex();
					resolutionControls = await loadResolutionControls();
					populatePairControls();
				}
				if (!current || current.pair.id !== state.pair) await selectPair();
				else render();
			} catch (error) {
				showLoadError(error);
			} finally {
				loadingPromise = null;
			}
		})();
		return loadingPromise;
	}

	function choosePair(pair) {
		state.pair = pair.id;
		state.basis = comparisonBasis(pair);
		state.comparison = comparisonKey(pair);
		populatePairControls();
		writeState();
		void selectPair().catch(showLoadError);
	}

	function csvCell(value) {
		const text = String(value == null ? '' : value);
		return /[",\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
	}

	function downloadBlob(blob, filename) {
		const link = document.createElement('a');
		link.href = URL.createObjectURL(blob);
		link.download = filename;
		link.click();
		setTimeout(() => URL.revokeObjectURL(link.href), 1000);
	}

	function downloadCurrentCsv() {
		const metric = METRICS[state.metric], fields = changeFields(metric);
		const rows = selectedModelChanges().map(record => [comparisonBasis(current.pair), comparisonLabel(current.pair), state.season, state.metric, metric.label, record.label, record.historical, record.future, record[fields.value], record[fields.low], record[fields.high], metric.changeMode, metric.unit]);
		const header = ['comparison_basis', 'comparison', 'genesis_season', 'metric', 'metric_label', 'model', 'historical', 'future', 'change', 'ci05', 'ci95', 'change_mode', 'unit'];
		const csv = [header, ...rows].map(row => row.map(csvCell).join(',')).join('\n') + '\n';
		downloadBlob(new Blob([csv], {type: 'text/csv;charset=utf-8'}), `lps-climate-${state.metric}-${state.season}.csv`);
	}

	function saveCurrentFigure() {
		const primary = {overview: '#mlaClimateModelChange', tracks: '#mlaClimateChangeMap', rainfall: current.impact ? (state.season === 'jjas' ? '#mlaClimateRainfallChange' : '#mlaClimateFootprintChange') : null, structure: '#mlaClimateVerticalProfile', evaluation: '#mlaClimateHistoricalSkill'}[state.view];
		const canvas = primary && $(primary);
		if (!canvas) return;
		canvas.toBlob(blob => { if (blob) downloadBlob(blob, `lps-climate-${state.view}-${state.metric}.png`); }, 'image/png');
	}

	function hitAt(event) {
		if (!(event.target instanceof HTMLCanvasElement)) return null;
		const bounds = event.target.getBoundingClientRect();
		const x = event.clientX - bounds.left, y = event.clientY - bounds.top;
		return chartHits.filter(hit => hit.canvas === event.target).find(hit => Math.hypot(hit.x - x, hit.y - y) <= hit.radius) || null;
	}

	readState();
	prepareLayout();
	setView(state.view, false);
	$('#mlaClimateBasis').addEventListener('change', event => {
		state.basis = event.target.value;
		const groups = comparisonGroups();
		const pairs = groups.values().next().value;
		if (pairs) choosePair(preferredPair(pairs));
	});
	$('#mlaClimateComparison').addEventListener('change', event => {
		state.comparison = event.target.value;
		const pairs = comparisonGroups().get(state.comparison);
		if (pairs) choosePair(preferredPair(pairs));
	});
	$('#mlaClimateDataset').addEventListener('change', event => {
		state.pair = event.target.value;
		writeState();
		void selectPair().catch(showLoadError);
	});
	$('#mlaClimateSeason').addEventListener('change', event => {
		state.season = event.target.value;
		populateMetricControls();
		writeState();
		render();
	});
	$('#mlaClimateMetric').addEventListener('change', event => {
		state.metric = event.target.value;
		state.metricGroup = METRICS[state.metric].group;
		$('#mlaClimateMetricGroup').value = state.metricGroup;
		writeState();
		render();
	});
	$('#mlaClimateMetricGroup').addEventListener('change', event => {
		state.metricGroup = event.target.value;
		writeState();
		render();
	});
	$('#mlaClimateRainMetric').addEventListener('change', event => {
		state.rainMetric = event.target.value;
		writeState();
		render();
	});
	$('#mlaClimateMapMetric').addEventListener('change', event => {
		state.mapMetric = event.target.value;
		writeState();
		render();
	});
	$('#mlaClimateProfileMetric').addEventListener('change', event => {
		state.profileMetric = event.target.value;
		writeState();
		render();
	});
	panel.querySelectorAll('[data-climate-view-button]').forEach(button => button.addEventListener('click', () => setView(button.dataset.climateViewButton)));
	$('#mlaClimateCopyLink').addEventListener('click', async event => {
		writeState();
		try {
			await navigator.clipboard.writeText(window.location.href);
			event.currentTarget.textContent = 'Link copied';
			setTimeout(() => { event.currentTarget.textContent = 'Copy link'; }, 1400);
		} catch (_) {
			event.currentTarget.textContent = 'Use address bar';
		}
	});
	$('#mlaClimateDownloadCsv').addEventListener('click', downloadCurrentCsv);
	$('#mlaClimateSavePng').addEventListener('click', saveCurrentFigure);
	panel.addEventListener('pointermove', event => {
		const hit = hitAt(event);
		if (!hit) { tooltip.hidden = true; if (event.target instanceof HTMLCanvasElement) event.target.style.cursor = ''; return; }
		tooltip.textContent = hit.text;
		tooltip.hidden = false;
		tooltip.style.left = `${Math.max(8, Math.min(window.innerWidth - 290, event.clientX + 12))}px`;
		tooltip.style.top = `${Math.min(window.innerHeight - 70, event.clientY + 12)}px`;
		event.target.style.cursor = hit.pairId && current && current.pair.kind === 'multi-model' ? 'pointer' : 'crosshair';
	});
	panel.addEventListener('pointerleave', () => { if (tooltip) tooltip.hidden = true; });
	panel.addEventListener('click', event => {
		const hit = hitAt(event);
		if (!hit || !hit.pairId || !current || current.pair.kind !== 'multi-model') return;
		const pair = index.pairs.find(item => item.id === hit.pairId);
		if (pair) choosePair(pair);
	});
	let resizeTimer = null;
	window.addEventListener('resize', () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(render, 120); });
	window.addEventListener('mla:climate-visible', event => {
		if (event.detail && event.detail.geo) geography = event.detail.geo;
		void activate();
	});
})();
