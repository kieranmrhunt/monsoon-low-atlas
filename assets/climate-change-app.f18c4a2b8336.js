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
	const MODEL_COLOURS = ['#00629b', '#d55e00', '#009e73', '#8f3b76', '#6f4c9b', '#e69f00', '#0072b2', '#cc79a7'];
	const GWL_COLOURS = {'1.5': '#e69f00', '2': '#d55e00', '3': '#c33149', '4': '#6f4c9b'};
	const METRICS = {
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
	const PUBLISHED_STATUSES = new Set(['validated-production-window', 'multi-model-awaiting-review']);
	const state = {pair: '', season: 'jjas', metric: 'systems', rainMetric: 'exposed_mean_mm_day'};
	const cache = new Map();
	let index = null;
	let current = null;
	let resolutionControls = [];
	let geography = null;
	let loadingPromise = null;
	let pairSerial = 0;

	function readState() {
		try {
			const stored = JSON.parse(localStorage.getItem('mla-climate-state-v1') || '{}');
			if (typeof stored.pair === 'string') state.pair = stored.pair;
			if (VALID_SEASONS.has(stored.season)) state.season = stored.season;
			if (Object.hasOwn(METRICS, stored.metric)) state.metric = stored.metric;
			if (Object.hasOwn(RAIN_METRICS, stored.rainMetric)) state.rainMetric = stored.rainMetric;
		} catch (_) {
			// Private browsing can disable storage without disabling the atlas.
		}
		const parameters = new URLSearchParams(window.location.search);
		if (parameters.has('cmpair')) state.pair = parameters.get('cmpair');
		if (VALID_SEASONS.has(parameters.get('cmseason'))) state.season = parameters.get('cmseason');
		if (Object.hasOwn(METRICS, parameters.get('cmmetric'))) state.metric = parameters.get('cmmetric');
		if (Object.hasOwn(RAIN_METRICS, parameters.get('cmrain'))) state.rainMetric = parameters.get('cmrain');
	}

	function writeState() {
		try {
			localStorage.setItem('mla-climate-state-v1', JSON.stringify(state));
		} catch (_) {
			// URL state remains available when local storage is unavailable.
		}
		const url = new URL(window.location.href);
		url.searchParams.set('cmpair', state.pair);
		url.searchParams.set('cmseason', state.season);
		url.searchParams.set('cmmetric', state.metric);
		url.searchParams.set('cmrain', state.rainMetric);
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

	function populateControls() {
		const pairControl = $('#mlaClimatePair');
		pairControl.replaceChildren(...index.pairs.map(pair => {
			const option = document.createElement('option');
			option.value = pair.id;
			option.textContent = pairLabel(pair);
			return option;
		}));
		if (!index.pairs.some(pair => pair.id === state.pair)) {
			state.pair = index.defaults && index.defaults.pair || index.pairs[0].id;
		}
		pairControl.value = state.pair;
		$('#mlaClimateSeason').value = state.season;
		const metricControl = $('#mlaClimateMetric');
		metricControl.replaceChildren(...Object.entries(METRICS).map(([key, metric]) => {
			const option = document.createElement('option');
			option.value = key;
			option.textContent = metric.label;
			return option;
		}));
		metricControl.value = state.metric;
		$('#mlaClimateRainMetric').value = state.rainMetric;
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
		const maximumLength = Math.max(historical.length, future.length);
		const x = index => plot.left + index / Math.max(1, maximumLength - 1) * (plot.right - plot.left);
		const y = value => plot.bottom - (value - yExtent[0]) / (yExtent[1] - yExtent[0]) * (plot.bottom - plot.top);
		for (const [values, colour] of [[historical, HISTORICAL_COLOUR], [future, FUTURE_COLOUR]]) {
			context.strokeStyle = colour;
			context.lineWidth = 2.2;
			context.beginPath();
			let open = false;
			values.forEach((value, index) => {
				if (!Number.isFinite(value)) { open = false; return; }
				if (!open) context.moveTo(x(index), y(value));
				else context.lineTo(x(index), y(value));
				open = true;
			});
			context.stroke();
		}
		context.fillStyle = css('--mla-muted', '#5f6574');
		context.textAlign = 'left';
		context.textBaseline = 'top';
		context.fillText('1', plot.left, plot.bottom + 8);
		context.textAlign = 'right';
		context.fillText(String(maximumLength), plot.right, plot.bottom + 8);
		context.textAlign = 'center';
		context.fillText('year within window', (plot.left + plot.right) / 2, plot.bottom + 8);
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
		const pairs = index.pairs.filter(pair => pair.kind !== 'multi-model');
		const position = pairs.findIndex(pair => pair.id === modelId);
		return MODEL_COLOURS[(position >= 0 ? position : ordinal) % MODEL_COLOURS.length];
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

	function drawModelChanges() {
		const canvas = $('#mlaClimateModelChange');
		const {context, width, height} = setupCanvas(canvas);
		const records = selectedModelChanges().filter(record => numberAvailable(record.percent_change));
		const status = $('#mlaClimateModelChangeStatus');
		const data = $('#mlaClimateModelChangeData');
		if (!records.length) {
			context.fillStyle = css('--mla-muted', '#5f6574');
			context.textAlign = 'center';
			context.fillText('Percentage change is unavailable because the historical mean is zero.', width / 2, height / 2);
			status.textContent = 'Use an absolute continuous measure for a stable comparison.';
			data.textContent = '';
			return;
		}
		const intervalValues = finite(records.flatMap(record => [record.percent_ci05, record.percent_change, record.percent_ci95]));
		const scale = Math.max(5, Math.max(...intervalValues.map(Math.abs)) * 1.12);
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
			const low = Number(record.percent_ci05), high = Number(record.percent_ci95);
			if (numberAvailable(record.percent_ci05) && numberAvailable(record.percent_ci95)) {
				context.strokeStyle = record.colour;
				context.lineWidth = 2;
				context.beginPath(); context.moveTo(x(low), y); context.lineTo(x(high), y); context.stroke();
				for (const value of [low, high]) { context.beginPath(); context.moveTo(x(value), y - 4); context.lineTo(x(value), y + 4); context.stroke(); }
			}
			context.fillStyle = record.colour;
			context.beginPath(); context.arc(x(record.percent_change), y, 5, 0, Math.PI * 2); context.fill();
		});
		context.fillStyle = muted;
		context.textAlign = 'center';
		context.textBaseline = 'bottom';
		context.fillText('future − historical (%)', (left + right) / 2, height - 2);
		const positive = records.filter(record => Number(record.percent_change) > 0).length;
		const negative = records.filter(record => Number(record.percent_change) < 0).length;
		const robustPositive = records.filter(record => Number(record.percent_ci05) > 0).length;
		const robustNegative = records.filter(record => Number(record.percent_ci95) < 0).length;
		status.textContent = `${positive}/${records.length} increase · ${negative}/${records.length} decrease · ${robustPositive + robustNegative} intervals exclude zero`;
		const metric = METRICS[state.metric];
		data.innerHTML = `<table><thead><tr><th>Model</th><th>Historical</th><th>Future</th><th>Change</th><th>90% interval</th></tr></thead><tbody>${records.map(record => `<tr><td>${esc(record.label)}</td><td>${esc(valueText(record.historical, metric))}</td><td>${esc(valueText(record.future, metric))}</td><td>${esc(signedPercent(record.percent_change))}</td><td>${esc(numberAvailable(record.percent_ci05) ? `${signedPercent(record.percent_ci05)} to ${signedPercent(record.percent_ci95)}` : '—')}</td></tr>`).join('')}</tbody></table>`;
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

	function drawWarmingNormalisedChanges() {
		const canvas = $('#mlaClimateWarmingChange');
		const {context, width, height} = setupCanvas(canvas);
		const records = selectedModelChanges().map(record => {
			const warming = warmingForModel(record.id);
			return {
				...record,
				warming,
				normalised: warming && numberAvailable(record.percent_change) ? Number(record.percent_change) / warming : null,
				low: warming && numberAvailable(record.percent_ci05) ? Number(record.percent_ci05) / warming : null,
				high: warming && numberAvailable(record.percent_ci95) ? Number(record.percent_ci95) / warming : null
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
		context.fillText('paired change per degree of global warming (% °C⁻¹)', (left + right) / 2, height - 2);
		const mean = records.reduce((sum, record) => sum + Number(record.normalised), 0) / records.length;
		const warmings = records.map(record => Number(record.warming));
		status.textContent = `${current.pair.kind === 'multi-model' ? 'Equal-model mean' : 'Response'} ${signedPerDegree(mean)} · global warming ${Math.min(...warmings).toFixed(2)}${records.length > 1 ? `–${Math.max(...warmings).toFixed(2)}` : ''} °C`;
		data.innerHTML = `<table><thead><tr><th>Model</th><th>Global warming</th><th>Response</th><th>Within-model 90% interval</th></tr></thead><tbody>${records.map(record => `<tr><td>${esc(record.label)}</td><td>+${Number(record.warming).toFixed(2)} °C</td><td>${esc(signedPerDegree(record.normalised))}</td><td>${esc(numberAvailable(record.low) ? `${signedPerDegree(record.low)} to ${signedPerDegree(record.high)}` : '—')}</td></tr>`).join('')}</tbody></table>`;
	}

	function publishedGwlRecords() {
		const pairs = current.pair.kind === 'multi-model'
			? current.pair.model_ids.map(modelPair).filter(Boolean)
			: [current.pair];
		return pairs.map(pair => ({
			id: pair.id,
			label: pair.source_label,
			crossings: pair.warming && pair.warming.published_gwl ? pair.warming.published_gwl.crossings : []
		})).filter(record => record.crossings.length);
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
		return current.pair.kind === 'multi-model' ? record.jjas : (record.seasonal || {}).jjas;
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
			context.fillText('Historical comparison is available for all months and JJAS.', width / 2, height / 2);
			status.textContent = 'Choose All months or JJAS.';
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
		card.hidden = !current.impact;
		if (!current.impact) return;
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
		const records = regionalRainfallRecords();
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
		context.fillText(mode === 'agreement' ? 'all fewer' : mode === 'change' ? `−${scale.toFixed(1)}` : '0', legendX, legendY + 9);
		if (mode === 'agreement') {
			context.textAlign = 'center';
			context.fillText('mixed', legendX + legendWidth / 2, legendY + 9);
		}
		context.textAlign = 'right';
		context.fillText(mode === 'agreement' ? 'all more' : mode === 'change' ? `+${scale.toFixed(1)}` : scale.toFixed(1), legendX + legendWidth, legendY + 9);
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
		const historical = current.historical.seasonal[state.season].track_density;
		const future = current.future.seasonal[state.season].track_density;
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
		const agreement = current.pair.kind === 'multi-model'
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
		$('#mlaClimateDensityAgreementStatus').textContent = `${threshold}/${agreement.model_count} or more models agree on the sign in ${robust.toLocaleString()} of ${changed.toLocaleString()} cells with any projected change.`;
		$('#mlaClimateDensityAgreementData').innerHTML = `<table><thead><tr><th>Diagnostic</th><th>Cells</th></tr></thead><tbody><tr><td>Any model changes</td><td>${changed.toLocaleString()}</td></tr><tr><td>At least ${threshold}/${agreement.model_count} agree</td><td>${robust.toLocaleString()}</td></tr><tr><td>All ${agreement.model_count} agree</td><td>${unanimous.toLocaleString()}</td></tr></tbody></table>`;
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
		let cards = [
			['Historical mean', valueText(change.historical, metric), current.historical.run.period_label],
			['Future mean', valueText(change.future, metric), current.future.run.period_label],
			['Paired change', Number.isFinite(change.percent_change) ? `${change.percent_change > 0 ? '+' : ''}${change.percent_change.toFixed(1)}%` : '—', valueText(change.absolute_change, metric, true)],
			['90% bootstrap interval', `${valueText(change.ci05, metric, true)} to ${valueText(change.ci95, metric, true)}`, 'annual resampling']
		];
		if (current.pair.kind === 'multi-model') {
			const models = Array.isArray(change.models) ? change.models : [];
			const positive = models.filter(model => Number(model.absolute_change) > 0).length;
			const negative = models.filter(model => Number(model.absolute_change) < 0).length;
			cards = [
				['Historical mean', valueText(change.historical, metric), 'one model, one vote'],
				['Future mean', valueText(change.future, metric), 'one model, one vote'],
				['Mean paired change', Number.isFinite(change.percent_change) ? `${change.percent_change > 0 ? '+' : ''}${change.percent_change.toFixed(1)}%` : '—', valueText(change.absolute_change, metric, true)],
				['Across-model 90% range', `${valueText(change.model_spread05, metric, true)} to ${valueText(change.model_spread95, metric, true)}`, `${change.model_count} models`],
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
		scope.textContent = (current.pair.kind === 'multi-model'
			? `${current.change.model_count} models · equal weight`
			: `${current.pair.source_label} · single model`) + (preview ? ' · research preview' : '');
		$('#mlaClimateHistoricalMapHeading').textContent = current.historical.run.period_label;
		$('#mlaClimateFutureMapHeading').textContent = current.future.run.period_label;
		renderStats();
		requestAnimationFrame(() => {
			drawModelChanges();
			drawHistoricalAgreement();
			drawWarmingNormalisedChanges();
			drawPublishedGwl();
			drawRainfallChanges();
			drawRegionalRainfall();
			drawFootprints();
			drawAnnual();
			drawMonthly();
			drawClasses();
			drawMaps();
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
		if (!current.historical.seasonal[state.season] || !current.future.seasonal[state.season]) state.season = 'all';
		$('#mlaClimateSeason').value = state.season;
		$('#mlaClimateLoading').hidden = true;
		$('#mlaClimateContent').hidden = false;
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
					populateControls();
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

	readState();
	$('#mlaClimatePair').addEventListener('change', event => {
		state.pair = event.target.value;
		writeState();
		void selectPair().catch(showLoadError);
	});
	$('#mlaClimateSeason').addEventListener('change', event => {
		state.season = event.target.value;
		writeState();
		render();
	});
	$('#mlaClimateMetric').addEventListener('change', event => {
		state.metric = event.target.value;
		writeState();
		render();
	});
	$('#mlaClimateRainMetric').addEventListener('change', event => {
		state.rainMetric = event.target.value;
		writeState();
		render();
	});
	window.addEventListener('mla:climate-visible', event => {
		if (event.detail && event.detail.geo) geography = event.detail.geo;
		void activate();
	});
})();
