(function () {
	'use strict';

	const root = document.getElementById('monsoon-low-atlas');
	const panel = document.getElementById('mlaPanelForecast');
	if (!root || !panel) return;
	const $ = selector => root.querySelector(selector);
	const config = JSON.parse(document.getElementById('mla-data-config').textContent || '{}');
	const DOMAIN = {west: 45, east: 120, south: -15, north: 45};
	const DEFAULT_MAP = {zoom: 1.25, longitude: 82.5, latitude: 17};
	const MODEL_TRACK_COLOURS = {
		gfs: '#dc0000', gefs: '#00963c', ifs: '#0046dc', 'ifs-ens': '#ff8c00',
		aifs: '#be00b4', 'aifs-ens': '#00bec8', 'ukmo-global': '#c2185b', 'mogreps-g': '#00a7a5',
		'gefs-control': '#6a5acd', 'tigge-ecmwf': '#73539b',
		'tigge-bom': '#d55e00', 'tigge-cma': '#e69f00', 'tigge-cptec': '#a65628',
		'tigge-dwd': '#009e73', 'tigge-eccc': '#56b4e9', 'tigge-imd': '#cc79a7',
		'tigge-jma': '#0072b2', 'tigge-kma': '#00a087', 'tigge-mf': '#8c6d00',
		'tigge-ncep': '#332288', 'tigge-ncmrwf': '#882255', 'tigge-ukmo': '#44aa99'
	};
	const RUN_COLOUR_VARIANTS = [
		[0, 0, 0], [3, 18, -2], [-3, -14, 2], [6, 29, -5],
		[-6, -24, 4], [9, 10, -1], [-9, -8, 3], [12, 24, -4]
	];
	const state = {
		mode: 'latest', manifest: null, payload: null, geo: null, boundary: null,
		selectedModels: new Set(), latestPayloads: new Map(), modelLoads: new Map(),
		selectedSystem: null, initialization: 'latest', initializationCount: 1, archiveDate: '', archiveHour: '00', archiveMonth: '', archiveEntry: null,
		archiveSelected: new Set(), archivePayloads: new Map(), archiveLoads: new Map(),
		leadIndex: 0, timelineTimes: [], weather: 'none', weatherModel: '', showMembers: false, showEra5: true,
		mapZoom: DEFAULT_MAP.zoom, mapCenterLon: DEFAULT_MAP.longitude,
		mapCenterLat: DEFAULT_MAP.latitude,
		initialised: false, loading: false, weatherCache: new Map(), loadSerial: 0,
		renderSerial: 0, archiveSearchTimer: 0, archiveAvailability: null
	};

	function esc(value) {
		return String(value == null ? '' : value).replace(/[&<>'"]/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'}[character]));
	}

	function clamp(value, minimum, maximum) {
		return Math.max(minimum, Math.min(maximum, value));
	}

	function joinUrl(base, relative) {
		return `${String(base || '').replace(/\/$/, '')}/${String(relative || '').replace(/^\//, '')}`;
	}

	async function gunzipJson(response) {
		if (!response.ok) throw new Error(`HTTP ${response.status} for ${response.url}`);
		const bytes = await response.arrayBuffer();
		if (typeof DecompressionStream === 'undefined') throw new Error('This browser cannot decompress forecast assets');
		const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
		return JSON.parse(await new Response(stream).text());
	}

	async function fetchGzipJson(url) {
		return gunzipJson(await fetch(url, {cache: 'force-cache'}));
	}

	async function fetchManifest() {
		let lastError = new Error('Forecast manifest is unavailable');
		for (let attempt = 0; attempt < 3; attempt += 1) {
			try {
				const url = `${joinUrl(config.forecastBase, 'manifest.json')}?v=${Date.now()}-${attempt}`;
				const response = await fetch(url, {cache: 'no-store'});
				if (!response.ok) throw new Error(`Forecast manifest returned HTTP ${response.status}`);
				const value = await response.json();
				if (value.schema !== 'mla-forecast-manifest-v1') throw new Error('Unsupported forecast manifest');
				return value;
			} catch (error) {
				lastError = error;
				if (attempt < 2) await new Promise(resolve => setTimeout(resolve, 300 * (attempt + 1)));
			}
		}
		throw lastError;
	}

	function notice(message, tone, retry) {
		const node = $('#mlaForecastNotice');
		node.hidden = !message;
		node.dataset.tone = tone || '';
		node.querySelector('span').textContent = message;
		$('#mlaForecastRetry').hidden = !retry;
	}

	function formatUtc(value, includeTime) {
		const date = new Date(value);
		if (!Number.isFinite(date.getTime())) return '—';
		return new Intl.DateTimeFormat('en-GB', {
			timeZone: 'UTC', day: '2-digit', month: 'short', year: 'numeric',
			hour: includeTime === false ? undefined : '2-digit', minute: includeTime === false ? undefined : '2-digit',
			hourCycle: 'h23', timeZoneName: includeTime === false ? undefined : 'short'
		}).format(date);
	}

	function versionLabel(value) {
		return value && value.label && value.label !== 'Version not yet crosswalked' ? value.label : '';
	}

	function modelTrackColour(id, fallback) {
		return MODEL_TRACK_COLOURS[id] || fallback || '#0057b8';
	}

	function modelRunColour(id, fallback, index) {
		const base = modelTrackColour(id, fallback);
		const match = String(base).match(/^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
		if (!match || !index) return base;
		const [red, green, blue] = match.slice(1).map(value => parseInt(value, 16) / 255);
		const maximum = Math.max(red, green, blue), minimum = Math.min(red, green, blue);
		const delta = maximum - minimum;
		let hue = 0;
		if (delta) {
			if (maximum === red) hue = 60 * (((green - blue) / delta) % 6);
			else if (maximum === green) hue = 60 * ((blue - red) / delta + 2);
			else hue = 60 * ((red - green) / delta + 4);
		}
		if (hue < 0) hue += 360;
		const lightness = 100 * (maximum + minimum) / 2;
		const saturation = delta ? 100 * delta / (1 - Math.abs(2 * lightness / 100 - 1)) : 0;
		const variant = RUN_COLOUR_VARIANTS[index % RUN_COLOUR_VARIANTS.length];
		const cycle = Math.floor(index / RUN_COLOUR_VARIANTS.length);
		return `hsl(${Math.round((hue + variant[0] + cycle * 4) % 360)} ${Math.round(clamp(saturation + variant[2], 45, 96))}% ${Math.round(clamp(lightness + variant[1] - cycle * 3, 18, 76))}%)`;
	}

	async function loadBoundary() {
		if (!config.geoCountryEndpoint || !config.soiBoundary) return null;
		const override = new URLSearchParams(location.search).get('boundary');
		if (override === 'natural-earth') return null;
		let country = override === 'soi' ? 'IN' : '';
		if (!country) {
			const controller = new AbortController();
			const timeout = setTimeout(() => controller.abort(), 3500);
			try {
				const response = await fetch(config.geoCountryEndpoint, {cache: 'no-store', referrerPolicy: 'no-referrer', signal: controller.signal});
				if (response.ok) country = String((await response.json()).country || '').toUpperCase();
			} catch (error) {
				console.warn('Forecast boundary lookup unavailable', error);
			} finally { clearTimeout(timeout); }
		}
		if (country !== 'IN') return null;
		try {
			const value = await fetchGzipJson(config.soiBoundary);
			return value.schema === 'monsoon-low-atlas-soi-boundary-v1' ? value : null;
		} catch (error) {
			console.warn('Forecast Survey of India boundary unavailable', error);
			return null;
		}
	}

	async function loadGeography() {
		const core = await fetchGzipJson(config.core);
		return core.geo;
	}

	function preferredModel() {
		const available = Object.keys(state.manifest.latest || {});
		for (const id of ['ifs', 'aifs', 'gfs', 'ifs-ens', 'aifs-ens', 'gefs']) if (available.includes(id)) return id;
		return available[0] || '';
	}

	function activeEntry(modelId) {
		if (!state.manifest) return null;
		if (state.initialization === 'latest') return (state.manifest.latest || {})[modelId] || null;
		return ((state.manifest.recent || {})[modelId] || []).find(entry => String(entry.cycle) === String(state.initialization)) || null;
	}

	function payloadKey(modelId, entry) {
		return `${modelId}:${entry ? entry.cycle : ''}`;
	}

	function requestedLatestRuns() {
		if (!state.manifest) return [];
		const definitions = new Map((state.manifest.models || []).map(model => [model.id, model]));
		const modelIds = [...state.selectedModels];
		if (state.initializationCount !== 1 && modelIds.length === 1) {
			const modelId = modelIds[0];
			const anchor = activeEntry(modelId);
			if (!anchor) return [];
			const candidates = new Map();
			for (const entry of [...((state.manifest.recent || {})[modelId] || []), (state.manifest.latest || {})[modelId]].filter(Boolean)) candidates.set(String(entry.cycle), entry);
			const anchorTime = new Date(anchor.cycle_utc).getTime();
			const limit = state.initializationCount === 0 ? candidates.size : state.initializationCount;
			return [...candidates.values()]
				.sort((a, b) => Math.abs(new Date(a.cycle_utc).getTime() - anchorTime) - Math.abs(new Date(b.cycle_utc).getTime() - anchorTime) || String(b.cycle).localeCompare(String(a.cycle)))
				.slice(0, limit)
				.sort((a, b) => String(b.cycle).localeCompare(String(a.cycle)))
				.map(entry => ({model: definitions.get(modelId) || modelDefinition(modelId), entry}));
		}
		return modelIds.map(modelId => ({model: definitions.get(modelId) || modelDefinition(modelId), entry: activeEntry(modelId)}));
	}

	function populateInitializationControls() {
		const select = $('#mlaForecastInitialization');
		if (!select || !state.manifest) return;
		const selected = [...state.selectedModels];
		const cycles = new Map();
		for (const modelId of selected) for (const entry of (state.manifest.recent || {})[modelId] || []) {
			const key = String(entry.cycle || '');
			if (!key) continue;
			if (!cycles.has(key)) cycles.set(key, {utc: entry.cycle_utc, models: new Set()});
			cycles.get(key).models.add(modelId);
		}
		const options = [...cycles.entries()].sort((a, b) => String(b[0]).localeCompare(String(a[0])));
		if (state.initialization !== 'latest' && !cycles.has(String(state.initialization))) state.initialization = 'latest';
		select.innerHTML = '<option value="latest">Latest available for each model</option>' + options.map(([cycle, item]) => `<option value="${esc(cycle)}">${esc(formatUtc(item.utc))} · ${item.models.size}/${selected.length} models</option>`).join('');
		select.value = state.initialization;
		const count = $('#mlaForecastRunCount');
		if (selected.length !== 1) state.initializationCount = 1;
		const runCount = selected.length === 1
			? new Set([...((state.manifest.recent || {})[selected[0]] || []), (state.manifest.latest || {})[selected[0]]].filter(Boolean).map(entry => String(entry.cycle))).size
			: 1;
		if (runCount <= 1) state.initializationCount = 1;
		else if (state.initializationCount >= runCount) state.initializationCount = 0;
		count.innerHTML = '<option value="1">Current only</option>'
			+ Array.from({length: Math.max(0, runCount - 2)}, (unused, index) => `<option value="${index + 2}">${index + 2} initializations</option>`).join('')
			+ (runCount > 1 ? `<option value="0">All ${runCount} available</option>` : '');
		count.value = String(state.initializationCount);
		count.disabled = selected.length !== 1;
		count.title = selected.length === 1 ? 'Compare this model across neighbouring initialization cycles' : 'Select exactly one model to compare neighbouring initializations';
	}

	function buildModelControls() {
		const latest = state.manifest.latest || {};
		const definitions = new Map((state.manifest.models || []).map(model => [model.id, model]));
		const operational = ['gfs', 'gefs', 'mogreps-g', 'ifs', 'ifs-ens', 'aifs', 'aifs-ens'];
		const models = operational.map(id => definitions.get(id) || (
			id === 'mogreps-g'
				? {id, label: 'MOGREPS-G', centre: 'Met Office', kind: 'ensemble', colour: MODEL_TRACK_COLOURS[id]}
				: {id, label: id.toUpperCase(), kind: 'ensemble', colour: MODEL_TRACK_COLOURS[id]}
		));
		if (!state.selectedModels.size) {
			for (const model of models) if (model.kind === 'deterministic' && latest[model.id]) state.selectedModels.add(model.id);
			if (!state.selectedModels.size && preferredModel()) state.selectedModels.add(preferredModel());
		}
		state.selectedModels = new Set([...state.selectedModels].filter(id => latest[id]));
		$('#mlaForecastModelChecks').innerHTML = models.map(model => {
			const entry = latest[model.id];
			const checked = state.selectedModels.has(model.id);
			const title = entry ? `${model.label} initialized ${formatUtc(entry.cycle_utc)}` : `${model.label} unavailable`;
			return `<label class="mla-forecast-model-choice" style="--model-colour:${esc(modelTrackColour(model.id, model.colour))}" title="${esc(title)}"><input type="checkbox" value="${esc(model.id)}" ${checked ? 'checked' : ''} ${entry ? '' : 'disabled'}><i aria-hidden="true"></i><span>${esc(model.label)}</span></label>`;
		}).join('');
		populateArchiveTimeControls();
		populateInitializationControls();
		populateWeatherModels();
	}

	function archiveEntries() {
		if (!state.manifest) return [];
		const entries = [...(state.manifest.archive || []), ...(state.manifest.tigge_archive || [])];
		const unique = new Map();
		for (const entry of entries) {
			const key = `${entry.model}:${entry.cycle}`;
			if (!unique.has(key)) unique.set(key, entry);
		}
		return [...unique.values()];
	}

	function archiveAvailability() {
		if (state.archiveAvailability) return state.archiveAvailability;
		const dates = new Map();
		for (const entry of archiveEntries()) {
			const start = new Date(entry.valid_start_utc || entry.cycle_utc).getTime();
			const end = new Date(entry.valid_end_utc).getTime();
			if (!Number.isFinite(start) || !Number.isFinite(end)) continue;
			for (let time = start; time <= end; time += 6 * 3600000) {
				const value = new Date(time);
				const date = value.toISOString().slice(0, 10);
				const hour = String(value.getUTCHours()).padStart(2, '0');
				if (!dates.has(date)) dates.set(date, new Map());
				const hours = dates.get(date);
				if (!hours.has(hour)) hours.set(hour, {runs: new Set(), models: new Set()});
				const slot = hours.get(hour);
				slot.runs.add(`${entry.model}:${entry.cycle}`);
				slot.models.add(entry.model);
			}
		}
		state.archiveAvailability = dates;
		return dates;
	}

	function archiveMonths() {
		return [...new Set([...archiveAvailability().keys()].map(date => date.slice(0, 7)))].sort();
	}

	function archiveMonthLabel(value) {
		const date = new Date(`${value}-01T00:00:00Z`);
		return Number.isFinite(date.getTime())
			? new Intl.DateTimeFormat('en-GB', {timeZone: 'UTC', month: 'long', year: 'numeric'}).format(date)
			: value;
	}

	function chooseArchiveHour(date) {
		const slots = archiveAvailability().get(date);
		if (!slots || !slots.size) return state.archiveHour;
		if (slots.has(state.archiveHour)) return state.archiveHour;
		return [...slots.entries()].sort((a, b) => b[1].runs.size - a[1].runs.size || a[0].localeCompare(b[0]))[0][0];
	}

	function renderArchiveCalendar() {
		const node = $('#mlaForecastCalendarDays');
		const select = $('#mlaForecastCalendarMonth');
		if (!node || !select || !state.manifest) return;
		const months = archiveMonths();
		if (!months.length) {
			select.innerHTML = '<option>No processed dates</option>';
			select.disabled = true;
			node.innerHTML = '';
			return;
		}
		select.disabled = false;
		const requestedMonth = state.archiveMonth || (state.archiveDate ? state.archiveDate.slice(0, 7) : '');
		state.archiveMonth = months.includes(requestedMonth) ? requestedMonth : months[months.length - 1];
		select.innerHTML = months.map(month => `<option value="${esc(month)}">${esc(archiveMonthLabel(month))}</option>`).join('');
		select.value = state.archiveMonth;
		const monthIndex = months.indexOf(state.archiveMonth);
		$('#mlaForecastCalendarPrevious').disabled = monthIndex <= 0;
		$('#mlaForecastCalendarNext').disabled = monthIndex >= months.length - 1;

		const [year, month] = state.archiveMonth.split('-').map(Number);
		const first = new Date(Date.UTC(year, month - 1, 1));
		const leading = (first.getUTCDay() + 6) % 7;
		const days = new Date(Date.UTC(year, month, 0)).getUTCDate();
		const availability = archiveAvailability();
		let maximum = 1;
		for (let day = 1; day <= days; day += 1) {
			const date = `${state.archiveMonth}-${String(day).padStart(2, '0')}`;
			for (const slot of (availability.get(date) || new Map()).values()) maximum = Math.max(maximum, slot.runs.size);
		}
		const cells = Array.from({length: leading}, () => '<span class="mla-forecast-calendar-empty"></span>');
		for (let day = 1; day <= days; day += 1) {
			const date = `${state.archiveMonth}-${String(day).padStart(2, '0')}`;
			const slots = availability.get(date) || new Map();
			const available = slots.size > 0;
			const details = ['00', '06', '12', '18'].map(hour => {
				const slot = slots.get(hour);
				return `${hour}Z ${slot ? `${slot.runs.size} run${slot.runs.size === 1 ? '' : 's'} across ${slot.models.size} model${slot.models.size === 1 ? '' : 's'}` : 'unavailable'}`;
			}).join('; ');
			const bars = ['00', '06', '12', '18'].map(hour => {
				const count = slots.get(hour) ? slots.get(hour).runs.size : 0;
				const opacity = count ? (.22 + .78 * Math.sqrt(count / maximum)).toFixed(2) : '.06';
				return `<i style="--availability:${opacity}" aria-hidden="true"></i>`;
			}).join('');
			cells.push(`<button class="mla-forecast-calendar-day" type="button" data-forecast-calendar-date="${esc(date)}" aria-pressed="${state.archiveDate === date}" title="${esc(details)}" ${available ? '' : 'disabled'}><strong>${day}</strong><span class="mla-forecast-calendar-hours">${bars}</span></button>`);
		}
		node.innerHTML = cells.join('');
	}

	function archiveModeLabel() {
		return 'archived';
	}

	function parseArchiveTarget(value) {
		const match = String(value || '').match(/(\d{4})[-/]?(\d{2})[-/]?(\d{2})(?:[ T]?(\d{2}))?/);
		if (!match) return null;
		const year = Number(match[1]), month = Number(match[2]), day = Number(match[3]);
		const requestedHour = match[4] == null ? Number(state.archiveHour || 0) : Number(match[4]);
		const hour = Math.max(0, Math.min(18, Math.floor(requestedHour / 6) * 6));
		const time = Date.UTC(year, month - 1, day, hour);
		const date = new Date(time);
		if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return null;
		return {time, date: `${match[1]}-${match[2]}-${match[3]}`, hour: String(hour).padStart(2, '0')};
	}

	function setArchiveTarget(time) {
		const date = new Date(time);
		if (!Number.isFinite(date.getTime())) return;
		state.archiveDate = date.toISOString().slice(0, 10);
		state.archiveMonth = state.archiveDate.slice(0, 7);
		state.archiveHour = String(date.getUTCHours()).padStart(2, '0');
		const calendar = $('#mlaForecastArchiveDate');
		const hour = $('#mlaForecastArchiveHour');
		if (calendar) calendar.value = state.archiveDate;
		if (hour) hour.value = state.archiveHour;
	}

	function archiveTargetTime() {
		if (!state.archiveDate) return null;
		return Date.parse(`${state.archiveDate}T${state.archiveHour || '00'}:00:00Z`);
	}

	function entryLeadAt(entry, target) {
		const cycle = new Date(entry.cycle_utc).getTime();
		const start = new Date(entry.valid_start_utc || entry.cycle_utc).getTime();
		const end = new Date(entry.valid_end_utc).getTime();
		if (!Number.isFinite(cycle) || !Number.isFinite(start) || !Number.isFinite(end) || !Number.isFinite(target) || target < start || target > end) return null;
		const lead = (target - cycle) / 3600000;
		const rounded = Math.round(lead / 6) * 6;
		return Math.abs(lead - rounded) < .01 ? rounded : null;
	}

	function archiveNameEntries() {
		const raw = $('#mlaForecastArchiveSearch').value.trim();
		if (!raw || parseArchiveTarget(raw)) return archiveEntries();
		const query = raw.toLowerCase();
		const compact = query.replace(/[^a-z0-9]/g, '');
		return archiveEntries().filter(entry => {
			const searchable = `${entry.search_text || ''} ${entry.cycle || ''}`.toLowerCase();
			return searchable.includes(query) || (compact && searchable.replace(/[^a-z0-9]/g, '').includes(compact));
		});
	}

	function bestArchiveTarget(entries) {
		const candidates = new Map();
		for (const entry of entries) {
			const start = new Date(entry.valid_start_utc || entry.cycle_utc).getTime();
			const end = new Date(entry.valid_end_utc).getTime();
			if (!Number.isFinite(start) || !Number.isFinite(end)) continue;
			for (let time = start; time <= end; time += 6 * 3600000) {
				if (!candidates.has(time)) candidates.set(time, {runs: 0, models: new Set()});
				const item = candidates.get(time);
				item.runs += 1;
				item.models.add(entry.model);
			}
		}
		const ordered = [...candidates.entries()].sort((a, b) => b[1].models.size - a[1].models.size || b[1].runs - a[1].runs || b[0] - a[0]);
		return ordered.length ? ordered[0][0] : null;
	}

	function ensureArchiveTarget() {
		const raw = $('#mlaForecastArchiveSearch').value.trim();
		const parsed = parseArchiveTarget(raw);
		if (parsed) {
			state.archiveDate = parsed.date;
			state.archiveHour = parsed.hour;
			state.archiveMonth = parsed.date.slice(0, 7);
		} else if (!state.archiveDate) {
			const matches = archiveNameEntries();
			const target = raw ? bestArchiveTarget(matches) : Math.max(...archiveEntries().map(entry => new Date(entry.cycle_utc).getTime()).filter(Number.isFinite));
			if (Number.isFinite(target)) setArchiveTarget(target);
		}
	}

	function populateArchiveTimeControls() {
		if (!state.manifest) return;
		const entries = archiveEntries();
		const starts = entries.map(entry => String(entry.valid_start_utc || entry.cycle_utc || '').slice(0, 10)).filter(Boolean).sort();
		const ends = entries.map(entry => String(entry.valid_end_utc || '').slice(0, 10)).filter(Boolean).sort();
		const calendar = $('#mlaForecastArchiveDate');
		calendar.min = starts[0] || '';
		calendar.max = ends[ends.length - 1] || starts[starts.length - 1] || '';
		calendar.value = state.archiveDate;
		$('#mlaForecastArchiveHour').value = state.archiveHour;
		renderArchiveCalendar();
	}

	function renderAvailability() {
		const node = $('#mlaForecastAvailability');
		if (!node || !state.manifest || state.mode === 'latest') { if (node) node.hidden = true; return; }
		node.hidden = false;
		const entries = archiveEntries();
		if (!entries.length) {
			const metadata = state.manifest.tigge_backfill || {};
			const status = metadata.status ? ` · ${metadata.status.replaceAll('_', ' ')}` : '';
			node.innerHTML = `<summary>Archive coverage</summary><p>No processed cycles published yet${esc(status)}. Availability appears as each cycle passes atlas QA.</p>`;
			return;
		}
		const ordered = [...entries].sort((a, b) => String(a.cycle).localeCompare(String(b.cycle)));
		const groups = new Map();
		for (const entry of ordered) {
			if (!groups.has(entry.model)) groups.set(entry.model, []);
			groups.get(entry.model).push(entry);
		}
		const modelHtml = [...groups.entries()].map(([id, values]) => {
			const label = values[0].model_label || id;
			return `<span class="mla-forecast-coverage-model"><strong>${esc(label)}</strong>${esc(formatUtc(values[0].cycle_utc, false))}–${esc(formatUtc(values[values.length - 1].cycle_utc, false))}</span>`;
		}).join('');
		node.innerHTML = `<summary>Archive coverage</summary><div class="mla-forecast-coverage-models">${modelHtml}</div>`;
	}

	function latestEntries() {
		return requestedLatestRuns().map(({model, entry}) => {
			const runKey = payloadKey(model.id, entry);
			return {model, payload: state.latestPayloads.get(runKey) || null, entry, runKey};
		});
	}

	function displayEntries() {
		if (state.mode !== 'latest') return [...state.archiveSelected].map(runKey => {
			const payload = state.archivePayloads.get(runKey);
			if (!payload) return null;
			const entry = archiveEntries().find(item => payloadKey(item.model, item) === runKey) || null;
			return {model: modelDefinition(payload.model.id), payload, entry, runKey};
		}).filter(Boolean);
		return latestEntries().filter(item => item.payload);
	}

	function populateWeatherModels() {
		const entries = displayEntries().filter(item => {
			const weather = item.payload && item.payload.weather;
			return weather && (weather.vorticity || weather.precipitation) && (state.weather === 'none' || weather[state.weather]);
		});
		if (!entries.some(item => item.runKey === state.weatherModel)) {
			const preferred = entries.find(item => item.model.id === preferredModel()) || entries[0];
			state.weatherModel = preferred ? preferred.runKey : '';
		}
		const options = entries.map(item => `<option value="${esc(item.runKey)}">${esc(`${item.model.label} · ${formatUtc(item.payload.cycle_utc)}`)}</option>`).join('') || '<option value="">No selected run with weather</option>';
		for (const selector of ['#mlaForecastWeatherModel', '#mlaForecastArchiveWeatherModel']) {
			const select = $(selector);
			if (!select) continue;
			select.innerHTML = options;
			select.value = state.weatherModel;
			select.disabled = state.weather === 'none' || !entries.length;
		}
		$('#mlaForecastWeather').value = state.weather;
		$('#mlaForecastWeather').disabled = state.mode !== 'latest' || !entries.length;
		$('#mlaForecastArchiveWeather').value = state.weather;
		$('#mlaForecastArchiveWeather').disabled = state.mode !== 'archive' || !entries.length;
	}

	function filteredArchive() {
		const target = archiveTargetTime();
		return archiveNameEntries().filter(entry => entryLeadAt(entry, target) != null);
	}

	function noArchiveMatchMessage() {
		const query = $('#mlaForecastArchiveSearch').value.trim();
		const values = archiveNameEntries();
		if (!values.length) return `No processed archived forecast matches “${query}”. Try an official cyclone name or another valid date.`;
		const target = archiveTargetTime();
		if (!Number.isFinite(target)) return `No processed ${archiveModeLabel()} forecast matches this search.`;
		const intervalDistance = entry => {
			const start = new Date(entry.valid_start_utc || entry.cycle_utc).getTime(), end = new Date(entry.valid_end_utc).getTime();
			return target < start ? start - target : target > end ? target - end : 0;
		};
		const nearest = [...values].sort((a, b) => intervalDistance(a) - intervalDistance(b)).slice(0, 2);
		const labels = nearest.map(entry => `${entry.model_label || entry.model} ${formatUtc(entry.cycle_utc)}`).join(' / ');
		return `No processed ${archiveModeLabel()} forecast is valid at ${formatUtc(target)}${query ? ` for “${query}”` : ''}.${labels ? ` Nearest processed initializations: ${labels}.` : ''}`;
	}

	function archiveModelOrder(ids) {
		const definitions = (state.manifest.models || []).map(model => model.id);
		return [...ids].sort((a, b) => {
			const first = definitions.indexOf(a), second = definitions.indexOf(b);
			return (first < 0 ? 999 : first) - (second < 0 ? 999 : second) || a.localeCompare(b);
		});
	}

	function shortArchiveInitialization(value) {
		const date = new Date(value);
		if (!Number.isFinite(date.getTime())) return ['—', '—'];
		const day = new Intl.DateTimeFormat('en-GB', {timeZone: 'UTC', day: '2-digit', month: 'short'}).format(date);
		return [day, `${String(date.getUTCHours()).padStart(2, '0')}Z`];
	}

	function archiveRunMatrix(entries) {
		const target = archiveTargetTime();
		const grouped = new Map();
		for (const entry of entries) {
			if (!grouped.has(entry.model)) grouped.set(entry.model, []);
			grouped.get(entry.model).push(entry);
		}
		const modelIds = archiveModelOrder(grouped.keys());
		const selected = state.archiveSelected.size;
		const groups = modelIds.map(modelId => {
			const model = modelDefinition(modelId);
			const modelColour = modelTrackColour(model.id, model.colour);
			const values = grouped.get(modelId).sort((a, b) => entryLeadAt(a, target) - entryLeadAt(b, target));
			const colourOrder = [...values].sort((a, b) => String(b.cycle).localeCompare(String(a.cycle)));
			const versions = [...new Set(values.map(entry => versionLabel(entry.model_version)).filter(Boolean))];
			const version = versions.length === 1 ? versions[0] : versions.length ? `${versions.length} versions at this valid time` : 'Version unavailable';
			const cells = values.map(entry => {
				const lead = entryLeadAt(entry, target);
				const key = `${entry.model}:${entry.cycle}`;
				const active = state.archiveSelected.has(key);
				const loading = state.archiveLoads.has(key);
				const cellColour = modelRunColour(model.id, model.colour, Math.max(0, colourOrder.findIndex(item => item.cycle === entry.cycle)));
				const initialization = shortArchiveInitialization(entry.cycle_utc);
				const names = (entry.verification_labels || []).join(', ');
				const title = [entry.model_label || entry.model, versionLabel(entry.model_version), `initialized ${formatUtc(entry.cycle_utc)}`, `valid ${formatUtc(target)}`, `lead +${lead} h`, names].filter(Boolean).join(' · ');
				return `<button class="mla-forecast-matrix-cell${loading ? ' is-loading' : ''}" type="button" style="--model-colour:${esc(cellColour)}" data-forecast-archive-run="${esc(key)}" aria-pressed="${active}" aria-label="${esc(title)}" title="${esc(title)}"><strong>+${esc(String(lead).padStart(3, '0'))} h</strong><small>${esc(initialization[0])}<br>${esc(initialization[1])} init</small></button>`;
			}).join('');
			return `<section class="mla-forecast-matrix-group" style="--model-colour:${esc(modelColour)}"><div class="mla-forecast-matrix-model" title="${esc(version)}"><i aria-hidden="true"></i><span><strong>${esc(model.label)}</strong><small>${esc(version)}</small></span></div><div class="mla-forecast-matrix-cells">${cells}</div></section>`;
		}).join('');
		const available = entries.length;
		const summary = available
			? `${available} model–lead pair${available === 1 ? '' : 's'} available · ${selected} selected`
			: 'No model–lead pairs available';
		return `<div class="mla-forecast-matrix-layout">
			<div class="mla-forecast-matrix-toolbar"><span><strong>${esc(formatUtc(target))}</strong><small>${esc(summary)}</small></span><button class="mla-btn mla-btn-small mla-btn-quiet" type="button" data-forecast-archive-clear ${selected ? '' : 'hidden'}>Clear</button></div>
			<div class="mla-forecast-matrix-intro"><p>Choose any model–lead squares; click a selected square again to remove it.</p><aside class="mla-forecast-analysis-choice"><span class="mla-label">Analysis</span><button class="mla-forecast-era5-tile" id="mlaForecastEra5Tile" type="button" aria-pressed="${state.showEra5}" title="Show or hide matched ERA5 catalogue tracks"><strong>ERA5</strong><small>v5.6 track</small></button></aside></div>
			<div class="mla-forecast-matrix-groups">${groups || `<p class="mla-forecast-matrix-no-match">${esc(noArchiveMatchMessage())}</p>`}</div>
		</div>`;
	}

	function defaultArchiveEntry(entries) {
		const preferred = ['ifs', 'aifs', 'gfs', 'ifs-ens', 'aifs-ens', 'gefs', 'mogreps-g', 'ukmo-global', 'gefs-control', 'tigge-ecmwf'];
		const target = archiveTargetTime();
		return [...entries].sort((a, b) => {
			const first = preferred.indexOf(a.model), second = preferred.indexOf(b.model);
			return (first < 0 ? 999 : first) - (second < 0 ? 999 : second) || entryLeadAt(a, target) - entryLeadAt(b, target);
		})[0] || null;
	}

	function populateArchive(loadFirst) {
		ensureArchiveTarget();
		populateArchiveTimeControls();
		const entries = filteredArchive();
		const permitted = new Set(entries.map(entry => `${entry.model}:${entry.cycle}`));
		let selectionChanged = false;
		for (const key of [...state.archiveSelected]) if (!permitted.has(key)) {
			state.archiveSelected.delete(key);
			if (state.selectedSystem && state.selectedSystem.runKey === key) state.selectedSystem = null;
			selectionChanged = true;
		}
		if (selectionChanged) {
			const remaining = displayEntries();
			state.payload = remaining.length ? remaining[remaining.length - 1].payload : null;
			configureTimeline(false, archiveTargetTime());
			populateWeatherModels();
			render();
		}
		const results = $('#mlaForecastArchiveResults');
		results.innerHTML = archiveRunMatrix(entries);
		const selected = loadFirst && !state.archiveSelected.size ? defaultArchiveEntry(entries) : null;
		if (selected) loadArchive(selected);
		if (!entries.length) {
			notice(noArchiveMatchMessage(), 'flag', false);
			render();
		} else if (!state.archiveSelected.size && !selected) notice('', '', false);
	}

	async function loadArchivePayload(entry) {
		const key = payloadKey(entry.model, entry);
		if (state.archivePayloads.has(key)) return state.archivePayloads.get(key);
		if (state.archiveLoads.has(key)) return state.archiveLoads.get(key);
		state.loading = true;
		notice(`Opening archived ${entry.model_label || entry.model} forecast…`, '', false);
		const promise = (async () => {
			const payload = await fetchGzipJson(joinUrl(config.forecastBase, entry.url));
			state.archivePayloads.set(key, payload);
			return payload;
		})();
		state.archiveLoads.set(key, promise);
		try { return await promise; }
		finally { state.archiveLoads.delete(key); state.loading = state.archiveLoads.size > 0; }
	}

	async function ensureLatest(modelId, entry) {
		if (!entry) throw new Error(`${modelDefinition(modelId).label} is unavailable for ${state.initialization === 'latest' ? 'its latest cycle' : state.initialization}`);
		const key = payloadKey(modelId, entry);
		const cached = state.latestPayloads.get(key);
		if (cached && String(cached.cycle) === String(entry.cycle)) return cached;
		if (state.modelLoads.has(key)) return state.modelLoads.get(key);
		const promise = fetchGzipJson(joinUrl(config.forecastBase, entry.url)).then(payload => {
			state.latestPayloads.set(key, payload);
			return payload;
		}).finally(() => state.modelLoads.delete(key));
		state.modelLoads.set(key, promise);
		return promise;
	}

	async function loadSelectedModels() {
		const selected = requestedLatestRuns();
		if (!selected.length) {
			notice('Select at least one forecast model.', 'flag', false);
			configureTimeline(true);
			return render();
		}
		state.loading = true;
		notice(`Opening ${selected.length} forecast run${selected.length === 1 ? '' : 's'}…`, '', false);
		const results = await Promise.allSettled(selected.map(item => ensureLatest(item.model.id, item.entry)));
		state.loading = false;
		const failures = results.filter(result => result.status === 'rejected');
		const loaded = latestEntries().filter(item => item.payload);
		if (state.selectedSystem && !loaded.some(item => item.runKey === state.selectedSystem.runKey && (item.payload.systems || []).some(system => system.id === state.selectedSystem.systemId))) state.selectedSystem = null;
		populateWeatherModels();
		configureTimeline(true);
		if (!loaded.length) notice(`Selected forecasts could not be loaded${failures[0] ? `: ${failures[0].reason.message || failures[0].reason}` : '.'}`, 'flag', true);
		else if (failures.length) notice(`${failures.length} selected forecast run${failures.length === 1 ? '' : 's'} could not be loaded.`, 'flag', false);
		else notice('', '', false);
		await render();
	}

	async function loadArchive(entry) {
		if (!entry) return;
		const key = payloadKey(entry.model, entry);
		if (state.archiveSelected.has(key)) return;
		const hadRuns = state.archiveSelected.size > 0;
		state.archiveSelected.add(key);
		state.archiveEntry = entry;
		state.selectedSystem = null;
		populateArchive(false);
		try {
			const payload = await loadArchivePayload(entry);
			if (!state.archiveSelected.has(key)) return;
			state.payload = payload;
			configureTimeline(hadRuns, archiveTargetTime());
			notice('', '', false);
			populateArchive(false);
			populateWeatherModels();
			await render();
		} catch (error) {
			state.archiveSelected.delete(key);
			populateArchive(false);
			populateWeatherModels();
			notice(`Forecast could not be loaded: ${error.message || error}`, 'flag', true);
			render();
		}
	}

	function removeArchiveRun(key) {
		if (!key || !state.archiveSelected.has(key)) return;
		state.archiveSelected.delete(key);
		state.archiveEntry = null;
		if (state.selectedSystem && state.selectedSystem.runKey === key) state.selectedSystem = null;
		const remaining = displayEntries();
		state.payload = remaining.length ? remaining[remaining.length - 1].payload : null;
		configureTimeline(Boolean(remaining.length), archiveTargetTime());
		populateArchive(false);
		populateWeatherModels();
		notice('', '', false);
		render();
	}

	function clearArchiveRuns() {
		state.archiveSelected.clear();
		state.archiveEntry = null;
		state.payload = null;
		state.selectedSystem = null;
		configureTimeline(false);
		populateArchive(false);
		populateWeatherModels();
		notice('', '', false);
		render();
	}

	function modelDefinition(id) {
		const model = (state.manifest.models || []).find(item => item.id === id);
		if (model) return {...model, colour: modelTrackColour(id, model.colour)};
		const archived = [...(state.manifest.archive || []), ...(state.manifest.tigge_archive || [])].find(item => item.model === id);
		return {id, label: archived && archived.model_label || id, colour: modelTrackColour(id)};
	}

	function currentValidTime() {
		return state.timelineTimes[state.leadIndex] || null;
	}

	function configureTimeline(preserve, preferredTime) {
		const slider = $('#mlaForecastLead');
		const previous = Number.isFinite(preferredTime) ? preferredTime : preserve ? currentValidTime() : null;
		const times = new Set();
		for (const {payload} of displayEntries()) for (const value of payload.valid_times || []) {
			const stamp = new Date(value).getTime();
			if (Number.isFinite(stamp)) times.add(stamp);
		}
		state.timelineTimes = [...times].sort((a, b) => a - b);
		if (previous != null && state.timelineTimes.length && previous >= state.timelineTimes[0] && previous <= state.timelineTimes[state.timelineTimes.length - 1]) {
			let nearest = 0;
			for (let index = 1; index < state.timelineTimes.length; index++) if (Math.abs(state.timelineTimes[index] - previous) < Math.abs(state.timelineTimes[nearest] - previous)) nearest = index;
			state.leadIndex = nearest;
		} else if (state.timelineTimes.length) {
			const sharedStart = Math.max(...displayEntries().map(item => new Date(item.payload.cycle_utc).getTime()).filter(Number.isFinite));
			const firstShared = state.timelineTimes.findIndex(value => value >= sharedStart);
			state.leadIndex = firstShared >= 0 ? firstShared : 0;
		} else state.leadIndex = 0;
		slider.min = 0;
		slider.max = Math.max(0, state.timelineTimes.length - 1);
		slider.value = state.leadIndex;
		updateTimeLabel();
	}

	function stepForPayload(payload) {
		const valid = currentValidTime();
		if (valid == null || !payload) return 0;
		return Math.round((valid - new Date(payload.cycle_utc).getTime()) / 3600000);
	}

	function frameForPayload(payload) {
		const valid = currentValidTime();
		if (valid == null || !payload || !(payload.valid_times || []).length) return -1;
		let nearest = 0;
		for (let index = 1; index < payload.valid_times.length; index++) if (Math.abs(new Date(payload.valid_times[index]).getTime() - valid) < Math.abs(new Date(payload.valid_times[nearest]).getTime() - valid)) nearest = index;
		return Math.abs(new Date(payload.valid_times[nearest]).getTime() - valid) <= 3.1 * 3600000 ? nearest : -1;
	}

	function updateTimeLabel() {
		const valid = currentValidTime();
		if (valid == null) { $('#mlaForecastTime').textContent = '—'; return; }
		const leads = displayEntries().map(item => ({value: stepForPayload(item.payload), horizon: itemHorizon(item.payload)})).filter(item => item.value >= 0 && item.value <= item.horizon).map(item => item.value);
		const range = leads.length && Math.min(...leads) === Math.max(...leads)
			? `+${String(leads[0]).padStart(3, '0')} h`
			: leads.length ? `leads +${Math.min(...leads)}–+${Math.max(...leads)} h` : 'outside model range';
		$('#mlaForecastTime').textContent = `${range} · ${formatUtc(valid)}`;
	}

	function itemHorizon(payload) {
		return payload ? Number(payload.horizon_hours || Math.max(...(payload.steps || [0]))) : 0;
	}

	async function initialise(force) {
		if (state.loading) return;
		if (state.initialised && !force) { resizeAndRender(); return; }
		state.loading = true;
		notice('Opening forecast manifest and map geography…', '', false);
		try {
			[state.manifest, state.geo, state.boundary] = await Promise.all([
				fetchManifest(), loadGeography(), loadBoundary()
			]);
			state.initialised = true;
			buildModelControls();
			populateArchive(false);
			renderAvailability();
			state.loading = false;
			if (state.mode !== 'latest') {
				populateArchive(true);
			} else loadSelectedModels();
		} catch (error) {
			state.loading = false;
			notice(`Forecast service is unavailable: ${error.message || error}`, 'flag', true);
			render();
		}
	}

	function setMode(mode) {
		if (mode === 'tigge') mode = 'archive';
		state.mode = mode;
		$('#mlaForecastModeLatest').setAttribute('aria-pressed', String(mode === 'latest'));
		$('#mlaForecastModeArchive').setAttribute('aria-pressed', String(mode === 'archive'));
		$('#mlaForecastLayout').dataset.mode = mode;
		$('#mlaForecastLiveControls').hidden = mode !== 'latest';
		$('#mlaForecastArchiveControls').hidden = mode === 'latest';
		$('#mlaForecastArchiveSidebar').hidden = mode === 'latest';
		$('#mlaForecastArchiveSearchLabel').textContent = 'Storm or valid time';
		$('#mlaForecastArchiveWeatherField').hidden = mode !== 'archive';
		$('#mlaForecastArchiveWeatherSourceField').hidden = mode !== 'archive';
		$('#mlaForecastArchiveMembersLabel').hidden = mode !== 'archive';
		state.showMembers = false;
		$('#mlaForecastArchiveMembers').checked = false;
		if (!state.initialised) { initialise(); return; }
		state.payload = null;
		state.selectedSystem = null;
		state.archiveEntry = null;
		state.archiveSelected.clear();
		state.archiveDate = '';
		state.archiveHour = '00';
		$('#mlaForecastArchiveSearch').value = '';
		$('#mlaForecastArchiveDate').value = '';
		$('#mlaForecastArchiveHour').value = state.archiveHour;
		populateArchiveTimeControls();
		renderAvailability();
		render();
		if (mode !== 'latest') {
			state.weather = 'none';
			populateWeatherModels();
			populateArchive(true);
		} else {
			state.weather = $('#mlaForecastWeather').value;
			populateWeatherModels();
			loadSelectedModels();
		}
	}

	function projection(width, height) {
		const padding = 24;
		const baseScale = Math.min((width - 2 * padding) / (DOMAIN.east - DOMAIN.west), (height - 2 * padding) / (DOMAIN.north - DOMAIN.south));
		const scale = baseScale * state.mapZoom;
		return {
			project(lat, lon) { return [width / 2 + (lon - state.mapCenterLon) * scale, height / 2 - (lat - state.mapCenterLat) * scale]; },
			invert(x, y) { return [state.mapCenterLat - (y - height / 2) / scale, state.mapCenterLon + (x - width / 2) / scale]; },
			viewBounds: {
				west: state.mapCenterLon - width / (2 * scale), east: state.mapCenterLon + width / (2 * scale),
				south: state.mapCenterLat - height / (2 * scale), north: state.mapCenterLat + height / (2 * scale)
			},
			scale
		};
	}

	function constrainMapView(width, height) {
		const padding = 24;
		const scale = Math.min((width - 2 * padding) / (DOMAIN.east - DOMAIN.west), (height - 2 * padding) / (DOMAIN.north - DOMAIN.south)) * state.mapZoom;
		const halfLongitude = width / (2 * scale);
		const halfLatitude = height / (2 * scale);
		const middleLongitude = (DOMAIN.west + DOMAIN.east) / 2;
		const middleLatitude = (DOMAIN.south + DOMAIN.north) / 2;
		state.mapCenterLon = halfLongitude * 2 >= DOMAIN.east - DOMAIN.west ? middleLongitude : clamp(state.mapCenterLon, DOMAIN.west + halfLongitude, DOMAIN.east - halfLongitude);
		state.mapCenterLat = halfLatitude * 2 >= DOMAIN.north - DOMAIN.south ? middleLatitude : clamp(state.mapCenterLat, DOMAIN.south + halfLatitude, DOMAIN.north - halfLatitude);
	}

	function setMapZoom(value, x, y) {
		const canvas = $('#mlaForecastTracks');
		const rectangle = canvas.getBoundingClientRect();
		const before = projection(rectangle.width, rectangle.height);
		const pointX = x == null ? rectangle.width / 2 : x;
		const pointY = y == null ? rectangle.height / 2 : y;
		const geographical = before.invert(pointX, pointY);
		state.mapZoom = clamp(value, 1, 14);
		const after = projection(rectangle.width, rectangle.height);
		const current = after.invert(pointX, pointY);
		state.mapCenterLat += geographical[0] - current[0];
		state.mapCenterLon += geographical[1] - current[1];
		constrainMapView(rectangle.width, rectangle.height);
		$('#mlaForecastMapStack').dataset.zoom = state.mapZoom.toFixed(3);
		scheduleRender();
	}

	function resetMapView() {
		state.mapZoom = DEFAULT_MAP.zoom;
		state.mapCenterLon = DEFAULT_MAP.longitude;
		state.mapCenterLat = DEFAULT_MAP.latitude;
		$('#mlaForecastMapStack').dataset.zoom = '1.000';
		scheduleRender();
	}

	function canvasContext(id) {
		const canvas = $(id);
		const rectangle = canvas.getBoundingClientRect();
		const width = Math.max(1, rectangle.width);
		const height = Math.max(1, rectangle.height);
		const ratio = Math.min(2, window.devicePixelRatio || 1);
		if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
			canvas.width = Math.round(width * ratio);
			canvas.height = Math.round(height * ratio);
		}
		const context = canvas.getContext('2d');
		context.setTransform(ratio, 0, 0, ratio, 0, 0);
		context.clearRect(0, 0, width, height);
		return {canvas, context, width, height, projection: projection(width, height)};
	}

	function ringPath(context, project, rings) {
		for (const ring of rings || []) {
			if (!ring || ring.length < 2) continue;
			ring.forEach((point, index) => {
				const [x, y] = project(point[1], point[0]);
				if (!index) context.moveTo(x, y); else context.lineTo(x, y);
			});
			context.closePath();
		}
	}

	function drawBase() {
		const {context, width, height, projection: map} = canvasContext('#mlaForecastBase');
		context.fillStyle = getComputedStyle(root).getPropertyValue('--mla-sea').trim() || '#e7eee7';
		context.fillRect(0, 0, width, height);
		if (!state.geo) return;
		context.beginPath(); ringPath(context, map.project, state.geo.land);
		context.fillStyle = getComputedStyle(root).getPropertyValue('--mla-land').trim() || '#f3e6c8';
		context.fill('evenodd');
		context.strokeStyle = 'rgba(40,33,25,.28)'; context.lineWidth = .8; context.stroke();
		if (state.boundary) {
			context.beginPath(); ringPath(context, map.project, state.boundary.rings);
			context.fillStyle = getComputedStyle(root).getPropertyValue('--mla-land').trim() || '#f3e6c8';
			context.fill('evenodd');
		}
		const borders = state.boundary ? state.boundary.borders_elsewhere : state.geo.borders;
		for (const border of borders || []) {
			if (!border.p || border.p.length < 2) continue;
			context.beginPath();
			border.p.forEach((point, index) => { const xy = map.project(point[1], point[0]); if (!index) context.moveTo(...xy); else context.lineTo(...xy); });
			context.setLineDash(border.c === 1 ? [4, 3] : []);
			context.strokeStyle = 'rgba(40,33,25,.34)'; context.lineWidth = .7; context.stroke();
		}
		context.setLineDash([]);
		for (const geometry of state.geo.states || []) {
			context.beginPath(); ringPath(context, map.project, geometry.rings);
			context.strokeStyle = 'rgba(40,33,25,.17)'; context.lineWidth = .55; context.stroke();
		}
	}

	function drawAnnotations(target) {
		const {context, width, height, projection: map} = target;
		const northwest = map.project(DOMAIN.north, DOMAIN.west);
		const southeast = map.project(DOMAIN.south, DOMAIN.east);
		const left = clamp(Math.min(northwest[0], southeast[0]), 0, width);
		const right = clamp(Math.max(northwest[0], southeast[0]), 0, width);
		const top = clamp(Math.min(northwest[1], southeast[1]), 0, height);
		const bottom = clamp(Math.max(northwest[1], southeast[1]), 0, height);
		context.save();
		context.beginPath(); context.rect(left, top, Math.max(0, right - left), Math.max(0, bottom - top)); context.clip();
		context.strokeStyle = 'rgba(40,33,25,.16)'; context.lineWidth = .7;
		for (let lon = 50; lon <= 120; lon += 10) {
			const first = map.project(DOMAIN.south, lon), second = map.project(DOMAIN.north, lon);
			context.beginPath(); context.moveTo(...first); context.lineTo(...second); context.stroke();
		}
		for (let lat = -10; lat <= 40; lat += 10) {
			const first = map.project(lat, DOMAIN.west), second = map.project(lat, DOMAIN.east);
			context.beginPath(); context.moveTo(...first); context.lineTo(...second); context.stroke();
		}
		context.restore();
		context.save();
		context.font = '11px "effra", Effra, Arial, sans-serif';
		context.textBaseline = 'top'; context.lineJoin = 'round'; context.lineWidth = 3;
		context.strokeStyle = 'rgba(255,253,246,.94)'; context.fillStyle = 'rgba(40,33,25,.68)';
		const longitudeY = clamp(top + 4, 4, height - 16);
		for (let lon = 50; lon <= 120; lon += 10) {
			const x = map.project(DOMAIN.north, lon)[0];
			if (x < 4 || x > width - 34) continue;
			const label = `${lon}°E`;
			context.strokeText(label, x + 3, longitudeY); context.fillText(label, x + 3, longitudeY);
		}
		context.textBaseline = 'bottom';
		const latitudeX = clamp(left + 4, 4, width - 34);
		for (let lat = -10; lat <= 40; lat += 10) {
			const y = map.project(lat, DOMAIN.west)[1];
			if (y < 14 || y > height - 3) continue;
			const label = `${lat}°`;
			context.strokeText(label, latitudeX, y - 2); context.fillText(label, latitudeX, y - 2);
		}
		context.restore();
	}

	function hexRgb(value) {
		const text = value.replace('#', '');
		return [parseInt(text.slice(0, 2), 16), parseInt(text.slice(2, 4), 16), parseInt(text.slice(4, 6), 16)];
	}

	function paletteColour(value, palette) {
		const position = Math.max(0, Math.min(1, value)) * (palette.length - 1);
		const index = Math.min(palette.length - 2, Math.floor(position));
		const weight = position - index;
		const first = hexRgb(palette[index]), second = hexRgb(palette[index + 1]);
		return first.map((channel, i) => Math.round(channel * (1 - weight) + second[i] * weight));
	}

	async function decodeWeather(payload, name) {
		if (!payload || !payload.weather || !payload.weather[name]) return null;
		const key = `${payload.model.id}:${payload.cycle}:${name}`;
		if (state.weatherCache.has(key)) return state.weatherCache.get(key);
		const field = payload.weather[name];
		const binary = atob(field.data);
		const bytes = new Uint8Array(binary.length);
		for (let index = 0; index < binary.length; index++) bytes[index] = binary.charCodeAt(index);
		const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'));
		const decoded = new Uint8Array(await new Response(stream).arrayBuffer());
		const value = {field, decoded};
		state.weatherCache.set(key, value);
		return value;
	}

	async function drawWeather() {
		const target = canvasContext('#mlaForecastWeatherMap');
		if (state.weather === 'none') return;
		const weatherEntry = displayEntries().find(item => item.runKey === state.weatherModel);
		const payload = weatherEntry && weatherEntry.payload;
		if (!payload) return;
		const record = await decodeWeather(payload, state.weather);
		if (!record) return;
		const [frames, ny, nx] = record.field.shape;
		const frame = frameForPayload(payload);
		if (frame < 0 || frame >= frames) return;
		const image = document.createElement('canvas');
		image.width = nx; image.height = ny;
		const imageContext = image.getContext('2d');
		const pixels = imageContext.createImageData(nx, ny);
		const palettes = state.weather === 'vorticity'
			? ['#d8eff7', '#83c7dc', '#3b91bd', '#5967a9', '#b3446c', '#7b183c']
			: ['#e5f7d0', '#72d68c', '#18b4a8', '#2b83ba', '#8f57b5', '#ed5a72', '#ffb23f', '#a92a32'];
		for (let y = 0; y < ny; y++) {
			for (let x = 0; x < nx; x++) {
				const sourceY = ny - 1 - y;
				const encoded = record.decoded[frame * ny * nx + sourceY * nx + x];
				const value = encoded * Number(record.field.scale) + Number(record.field.offset || 0);
				const maximum = state.weather === 'vorticity' ? 20 : 100;
				const normal = Math.max(0, Math.min(1, value / maximum));
				const colour = paletteColour(Math.pow(normal, .62), palettes);
				const offset = (y * nx + x) * 4;
				pixels.data[offset] = colour[0]; pixels.data[offset + 1] = colour[1]; pixels.data[offset + 2] = colour[2];
				pixels.data[offset + 3] = value <= 0 ? 0 : Math.round(35 + 190 * Math.pow(normal, .42));
			}
		}
		imageContext.putImageData(pixels, 0, 0);
		const grid = payload.grid;
		const northwest = target.projection.project(grid.north, grid.west);
		const southeast = target.projection.project(grid.south, grid.east);
		target.context.imageSmoothingEnabled = false;
		target.context.save();
		target.context.beginPath();
		target.context.rect(
			Math.min(northwest[0], southeast[0]), Math.min(northwest[1], southeast[1]),
			Math.abs(southeast[0] - northwest[0]), Math.abs(southeast[1] - northwest[1])
		);
		target.context.clip();
		target.context.drawImage(image, northwest[0], northwest[1], southeast[0] - northwest[0], southeast[1] - northwest[1]);
		target.context.restore();
		$('#mlaForecastWeatherKey').hidden = false;
		$('#mlaForecastWeatherMaximum').textContent = state.weather === 'vorticity' ? '20 × 10⁻⁵ s⁻¹' : '100 mm';
	}

	function tracksForSystem(payload, system) {
		if (!payload) return [];
		const ids = new Set(system.track_ids || []);
		return (payload.tracks || []).filter(track => ids.has(track.id));
	}

	function meanTrack(payload, system) {
		const tracks = tracksForSystem(payload, system);
		const byStep = new Map();
		for (const track of tracks) for (const point of track.points) {
			if (!byStep.has(point[0])) byStep.set(point[0], []);
			byStep.get(point[0]).push(point);
		}
		const minimum = Math.max(1, Math.ceil((system.member_count || tracks.length) * .2));
		return [...byStep.entries()].filter(([, points]) => points.length >= minimum).sort((a, b) => a[0] - b[0]).map(([step, points]) => [
			step,
			points.reduce((sum, point) => sum + Number(point[1]), 0) / points.length,
			points.reduce((sum, point) => sum + Number(point[2]), 0) / points.length,
			points.length
		]);
	}

	function haversineKm(longitudeA, latitudeA, longitudeB, latitudeB) {
		const radians = value => Number(value) * Math.PI / 180;
		const latitudeDelta = radians(latitudeB - latitudeA);
		const longitudeDelta = radians(longitudeB - longitudeA);
		const value = Math.sin(latitudeDelta / 2) ** 2 + Math.cos(radians(latitudeA)) * Math.cos(radians(latitudeB)) * Math.sin(longitudeDelta / 2) ** 2;
		return 6371.0088 * 2 * Math.atan2(Math.sqrt(value), Math.sqrt(Math.max(0, 1 - value)));
	}

	function historyEntries(modelId, payload) {
		if (!state.manifest || !payload) return [];
		const current = new Date(payload.cycle_utc).getTime();
		const currentVersion = String(payload.model_version && payload.model_version.label || '');
		const source = state.mode !== 'latest'
			? archiveEntries().filter(entry => entry.model === modelId).map(entry => ({...entry, centres: entry.analysis_centres || []}))
			: ((state.manifest.analysis_history || {})[modelId] || []);
		return source.filter(entry => {
			const cycle = new Date(entry.cycle_utc).getTime();
			const age = (current - cycle) / 3600000;
			const entryVersion = String(entry.model_version && entry.model_version.label || '');
			return Number.isFinite(cycle) && age > 0 && age <= 14 * 24 && (!currentVersion || !entryVersion || currentVersion === entryVersion);
		}).sort((a, b) => new Date(b.cycle_utc).getTime() - new Date(a.cycle_utc).getTime());
	}

	function signaturePoint(centre, step) {
		let best = null;
		for (const point of centre.match_points || []) if (!best || Math.abs(Number(point[0]) - step) < Math.abs(Number(best[0]) - step)) best = point;
		return best && Math.abs(Number(best[0]) - step) <= 3.1 ? best : null;
	}

	function payloadAnalysisCentres(payload) {
		const output = [];
		for (const item of payload.systems || []) {
			const point = meanTrack(payload, item).find(value => Number(value[0]) === 0);
			if (point) output.push({system_id: String(item.id), longitude: Number(point[1]), latitude: Number(point[2])});
		}
		return output;
	}

	function analysisHistory(payload, system, modelId) {
		const forecast = meanTrack(payload, system);
		const initial = forecast.find(point => Number(point[0]) === 0);
		if (!initial) return [];
		const currentCycle = new Date(payload.cycle_utc).getTime();
		let anchor = {time: currentCycle, longitude: Number(initial[1]), latitude: Number(initial[2]), systemId: String(system.id), cohort: payloadAnalysisCentres(payload)};
		let later = null;
		const matched = [];
		const maximumGap = state.mode !== 'latest' ? 60 : 30;
		for (const entry of historyEntries(modelId, payload)) {
			const entryTime = new Date(entry.cycle_utc).getTime();
			const gap = (anchor.time - entryTime) / 3600000;
			if (gap <= 0) continue;
			if (gap > maximumGap) break;
			const candidates = [];
			for (const centre of entry.centres || []) {
				const signature = signaturePoint(centre, gap);
				if (!signature) continue;
				const longitude = Number(centre.longitude);
				const latitude = Number(centre.latitude);
				const overlap = haversineKm(signature[1], signature[2], anchor.longitude, anchor.latitude);
				const origin = haversineKm(longitude, latitude, anchor.longitude, anchor.latitude);
				if (overlap > 500 || origin > Math.max(450, 45 * gap)) continue;
				const rival = (anchor.cohort || []).filter(item => String(item.system_id) !== anchor.systemId).some(item => haversineKm(signature[1], signature[2], item.longitude, item.latitude) <= overlap + 75);
				if (rival) continue;
				let prediction = 0;
				if (later) {
					const laterGap = (later.time - anchor.time) / 3600000;
					const ratio = laterGap > 0 ? gap / laterGap : 0;
					const predictedLongitude = anchor.longitude + (anchor.longitude - later.longitude) * ratio;
					const predictedLatitude = anchor.latitude + (anchor.latitude - later.latitude) * ratio;
					prediction = haversineKm(longitude, latitude, predictedLongitude, predictedLatitude);
					if (prediction > Math.max(500, 28 * gap)) continue;
				}
				candidates.push({centre, longitude, latitude, score: overlap + .15 * origin + .25 * prediction});
			}
			candidates.sort((a, b) => a.score - b.score);
			if (!candidates.length) continue;
			if (candidates.length > 1 && candidates[1].score - candidates[0].score < 75 && haversineKm(candidates[0].longitude, candidates[0].latitude, candidates[1].longitude, candidates[1].latitude) > 125) break;
			const best = candidates[0];
			const relativeStep = Math.round((entryTime - currentCycle) / 3600000);
			matched.push([relativeStep, best.longitude, best.latitude, Number(best.centre.member_count || 1), 'analysis']);
			later = anchor;
			anchor = {time: entryTime, longitude: best.longitude, latitude: best.latitude, systemId: String(best.centre.system_id), cohort: entry.centres || []};
		}
		return matched.reverse();
	}

	function stitchedTrack(payload, system, modelId) {
		const forecast = meanTrack(payload, system);
		const history = analysisHistory(payload, system, modelId);
		return {history, points: [...history, ...forecast]};
	}

	function drawPath(context, map, points, colour, width, alpha, current, distinguishFuture) {
		if (!points || points.length < 2) return;
		for (const phase of distinguishFuture ? ['past', 'future'] : ['all']) {
			context.beginPath(); let started = false;
			for (const point of points) {
				const belongs = phase === 'all' || (phase === 'past' ? Number(point[0]) <= current : Number(point[0]) >= current);
				if (!belongs) { started = false; continue; }
				const xy = map.project(Number(point[2]), Number(point[1]));
				if (!started) { context.moveTo(...xy); started = true; } else context.lineTo(...xy);
			}
			context.globalAlpha = alpha;
			context.strokeStyle = colour; context.lineWidth = phase === 'future' ? Math.max(.65, width * .5) : width; context.lineJoin = 'round'; context.lineCap = 'round';
			context.setLineDash([]); context.stroke();
		}
		context.setLineDash([]); context.globalAlpha = 1;
	}

	function pointAt(points, step) {
		if (!points || !points.length) return null;
		let best = points[0];
		for (const point of points) if (Math.abs(Number(point[0]) - step) < Math.abs(Number(best[0]) - step)) best = point;
		return Math.abs(Number(best[0]) - step) <= 1 ? best : null;
	}

	function runColour(entry) {
		const siblings = state.mode === 'latest'
			? requestedLatestRuns().filter(item => item.entry && item.model.id === entry.model.id).map(item => ({runKey: payloadKey(item.model.id, item.entry), cycle: item.entry.cycle}))
			: filteredArchive().filter(item => item.model === entry.model.id).map(item => ({runKey: payloadKey(item.model, item), cycle: item.cycle}));
		siblings.sort((a, b) => String(b.cycle).localeCompare(String(a.cycle)));
		const index = Math.max(0, siblings.findIndex(item => item.runKey === entry.runKey));
		return modelRunColour(entry.model.id, entry.model.colour || entry.payload.model.colour, index);
	}

	function drawTracks() {
		const target = canvasContext('#mlaForecastTracks');
		for (const entry of displayEntries()) {
			const {model, payload, runKey} = entry;
			const current = stepForPayload(payload);
			const colour = runColour(entry);
			for (const system of payload.systems || []) {
				const tracks = tracksForSystem(payload, system);
				const selected = state.selectedSystem && state.selectedSystem.runKey === runKey && state.selectedSystem.systemId === system.id;
				if (state.showMembers && payload.model.kind === 'ensemble') {
					for (const track of tracks) drawPath(target.context, target.projection, track.points, colour, 1, selected ? .48 : .24, current, true);
				}
				const mean = meanTrack(payload, system);
				const stitched = stitchedTrack(payload, system, model.id);
				if (selected) drawPath(target.context, target.projection, stitched.points, '#fffdf6', payload.model.kind === 'ensemble' ? 6.2 : 6.6, .96, current, true);
				drawPath(target.context, target.projection, stitched.points, colour, payload.model.kind === 'ensemble' ? 3.1 : 3.5, selected ? 1 : .9, current, true);
				if (selected) for (const point of stitched.history) {
					const xy = target.projection.project(point[2], point[1]);
					target.context.beginPath(); target.context.arc(xy[0], xy[1], 2.8, 0, Math.PI * 2);
					target.context.fillStyle = colour; target.context.fill(); target.context.lineWidth = 1.1; target.context.strokeStyle = '#fffdf6'; target.context.stroke();
				}
				const marker = pointAt(mean, current);
				if (marker) {
					const xy = target.projection.project(marker[2], marker[1]);
					target.context.beginPath(); target.context.arc(xy[0], xy[1], selected ? 7 : 5.3, 0, Math.PI * 2);
					target.context.fillStyle = colour; target.context.fill(); target.context.lineWidth = selected ? 3 : 2; target.context.strokeStyle = '#fffdf6'; target.context.stroke();
				}
			}
		}
		if (state.mode !== 'latest' && state.showEra5) {
			const verificationTracks = new Map();
			for (const entry of displayEntries()) for (const track of (entry.payload.verification || {}).tracks || []) if (!verificationTracks.has(String(track.id))) verificationTracks.set(String(track.id), {entry, track});
			for (const {entry, track} of verificationTracks.values()) {
				const current = stepForPayload(entry.payload);
				target.context.beginPath();
				track.points.forEach((point, index) => { const xy = target.projection.project(point[2], point[1]); if (!index) target.context.moveTo(...xy); else target.context.lineTo(...xy); });
				target.context.setLineDash([]); target.context.strokeStyle = '#fffdf6'; target.context.lineWidth = 5; target.context.globalAlpha = .92; target.context.stroke();
				target.context.strokeStyle = '#000000'; target.context.lineWidth = 2.5; target.context.globalAlpha = 1; target.context.stroke();
				const marker = pointAt(track.points, current);
				if (marker) { const xy = target.projection.project(marker[2], marker[1]); target.context.beginPath(); target.context.arc(xy[0], xy[1], 4, 0, Math.PI * 2); target.context.fillStyle = '#000000'; target.context.fill(); target.context.strokeStyle = '#fffdf6'; target.context.lineWidth = 1.5; target.context.stroke(); }
			}
		}
		drawAnnotations(target);
	}

	async function render() {
		const serial = ++state.renderSerial;
		drawBase();
		$('#mlaForecastWeatherKey').hidden = true;
		await drawWeather();
		if (serial !== state.renderSerial) return;
		drawTracks();
		updateTimeLabel();
		const entries = displayEntries();
		const count = entries.reduce((sum, item) => sum + (item.payload.tracks || []).length, 0);
		const systems = entries.reduce((sum, item) => sum + (item.payload.systems || []).length, 0);
		const models = new Set(entries.map(item => item.model.id)).size;
		const weatherEntry = entries.find(item => item.runKey === state.weatherModel);
		const era5TrackIds = new Set();
		if (state.mode !== 'latest' && state.showEra5) for (const item of entries) for (const track of (item.payload.verification || {}).tracks || []) era5TrackIds.add(String(track.id));
		const era5Tracks = era5TrackIds.size;
		const mapStack = $('#mlaForecastMapStack');
		mapStack.dataset.zoom = state.mapZoom.toFixed(3);
		mapStack.dataset.centerLon = state.mapCenterLon.toFixed(3);
		mapStack.dataset.centerLat = state.mapCenterLat.toFixed(3);
		const status = entries.length
			? [`${entries.length} run${entries.length === 1 ? '' : 's'}`, `${models} model${models === 1 ? '' : 's'}`, `${systems} disturbance${systems === 1 ? '' : 's'}`, `${count} member track${count === 1 ? '' : 's'}`]
			: [];
		if (state.mode !== 'latest') status.push(era5Tracks ? `${era5Tracks} ERA5 verification track${era5Tracks === 1 ? '' : 's'}` : 'no matched ERA5 track');
		if (state.weather !== 'none') status.push(`${weatherEntry ? weatherEntry.model.label : 'selected run'} weather`);
		else if (state.mode === 'latest') status.push('weather off');
		$('#mlaForecastMapStatus').textContent = status.length ? status.join(' · ') : 'Forecast data not loaded.';
		const runKey = $('#mlaForecastRunKey');
		runKey.hidden = entries.length < 2;
		runKey.innerHTML = entries.map(item => `<span><i style="--run-colour:${esc(runColour(item))}" aria-hidden="true"></i>${esc(`${item.model.label} · ${formatUtc(item.payload.cycle_utc)}`)}</span>`).join('');
		$('#mlaForecastEra5Key').hidden = !era5Tracks;
	}

	let renderFrame = 0;
	function scheduleRender() {
		if (renderFrame) return;
		renderFrame = requestAnimationFrame(() => { renderFrame = 0; render(); });
	}

	function resizeAndRender() {
		if (!panel.hidden) render();
	}

	function selectSystemAt(event) {
		if (!displayEntries().length) return;
		const canvas = $('#mlaForecastTracks');
		const rectangle = canvas.getBoundingClientRect();
		const map = projection(rectangle.width, rectangle.height);
		let best = null;
		for (const entry of displayEntries()) {
			const {model, payload, runKey} = entry;
			const current = stepForPayload(payload);
			for (const system of payload.systems || []) {
				const point = pointAt(meanTrack(payload, system), current);
				if (!point) continue;
				const xy = map.project(point[2], point[1]);
				const distance = Math.hypot(event.clientX - rectangle.left - xy[0], event.clientY - rectangle.top - xy[1]);
				if (!best || distance < best.distance) best = {model, runKey, system, distance};
			}
		}
		if (best && best.distance <= (event.pointerType === 'touch' ? 25 : 18)) {
			state.selectedSystem = {runKey: best.runKey, systemId: best.system.id};
			render();
		}
	}

	function bindForecastMap() {
		const canvas = $('#mlaForecastTracks');
		const pointers = new Map();
		let drag = null;
		let pinch = null;
		let suppressTap = false;
		function pinchMetrics() {
			const points = [...pointers.values()].slice(0, 2);
			if (points.length < 2) return null;
			return {distance: Math.hypot(points[1].x - points[0].x, points[1].y - points[0].y), x: (points[0].x + points[1].x) / 2, y: (points[0].y + points[1].y) / 2};
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
			if (!drag) return;
			const dx = event.clientX - drag.x;
			const dy = event.clientY - drag.y;
			if (Math.abs(event.clientX - drag.startX) + Math.abs(event.clientY - drag.startY) > 5) drag.moved = true;
			if (!drag.moved) return;
			event.preventDefault();
			const rectangle = canvas.getBoundingClientRect();
			const map = projection(rectangle.width, rectangle.height);
			state.mapCenterLon -= dx / map.scale;
			state.mapCenterLat += dy / map.scale;
			constrainMapView(rectangle.width, rectangle.height);
			drag.x = event.clientX; drag.y = event.clientY;
			scheduleRender();
		});
		canvas.addEventListener('pointerup', event => {
			const wasPinching = Boolean(pinch);
			const moved = Boolean(drag && drag.moved);
			pointers.delete(event.pointerId);
			if (wasPinching) {
				suppressTap = true; pinch = null;
				const remaining = [...pointers.values()][0];
				drag = remaining ? {x: remaining.x, y: remaining.y, startX: remaining.x, startY: remaining.y, moved: false} : null;
				if (!pointers.size) canvas.classList.remove('is-dragging');
				return;
			}
			drag = null;
			canvas.classList.remove('is-dragging');
			if (suppressTap) { suppressTap = false; return; }
			if (!moved) selectSystemAt(event);
		});
		canvas.addEventListener('pointercancel', event => { pointers.delete(event.pointerId); drag = null; pinch = null; canvas.classList.remove('is-dragging'); });
		canvas.addEventListener('wheel', event => {
			event.preventDefault();
			const rectangle = canvas.getBoundingClientRect();
			setMapZoom(state.mapZoom * (event.deltaY < 0 ? 1.22 : 1 / 1.22), event.clientX - rectangle.left, event.clientY - rectangle.top);
		}, {passive: false});
		canvas.addEventListener('dblclick', event => {
			event.preventDefault();
			const rectangle = canvas.getBoundingClientRect();
			setMapZoom(state.mapZoom * 1.65, event.clientX - rectangle.left, event.clientY - rectangle.top);
		});
		$('#mlaForecastZoomIn').addEventListener('click', () => setMapZoom(state.mapZoom * 1.35));
		$('#mlaForecastZoomOut').addEventListener('click', () => setMapZoom(state.mapZoom / 1.35));
		$('#mlaForecastZoomReset').addEventListener('click', resetMapView);
	}

	$('#mlaForecastModeLatest').addEventListener('click', () => setMode('latest'));
	$('#mlaForecastModeArchive').addEventListener('click', () => setMode('archive'));
	$('#mlaForecastRetry').addEventListener('click', () => initialise(true));
	$('#mlaForecastModelChecks').addEventListener('change', event => {
		const input = event.target.closest('input[type="checkbox"]');
		if (!input) return;
		if (input.checked) state.selectedModels.add(input.value); else state.selectedModels.delete(input.value);
		if (!state.selectedModels.size) { state.selectedModels.add(input.value); input.checked = true; notice('Keep at least one forecast model selected.', 'flag', false); return; }
		if (state.selectedModels.size > 1) state.initializationCount = 1;
		state.selectedSystem = null;
		populateInitializationControls();
		loadSelectedModels();
	});
	$('#mlaForecastInitialization').addEventListener('change', event => {
		state.initialization = event.target.value;
		state.selectedSystem = null;
		state.leadIndex = 0;
		loadSelectedModels();
	});
	$('#mlaForecastRunCount').addEventListener('change', event => {
		const count = Number(event.target.value);
		if (count > 1 && state.selectedModels.size !== 1) {
			event.target.value = '1';
			notice('Select exactly one model before comparing neighbouring initializations.', 'flag', false);
			return;
		}
		state.initializationCount = count === 0 ? 0 : Math.max(1, count);
		state.selectedSystem = null;
		state.leadIndex = 0;
		loadSelectedModels();
	});
	$('#mlaForecastWeather').addEventListener('change', async event => { state.weather = event.target.value; populateWeatherModels(); await render(); });
	$('#mlaForecastWeatherModel').addEventListener('change', async event => { state.weatherModel = event.target.value; await render(); });
	$('#mlaForecastArchiveWeather').addEventListener('change', async event => { state.weather = event.target.value; populateWeatherModels(); await render(); });
	$('#mlaForecastArchiveWeatherModel').addEventListener('change', async event => { state.weatherModel = event.target.value; await render(); });
	$('#mlaForecastMembers').addEventListener('change', event => { state.showMembers = event.target.checked; render(); });
	$('#mlaForecastArchiveMembers').addEventListener('change', event => { state.showMembers = event.target.checked; render(); });
	$('#mlaForecastLead').addEventListener('input', event => { state.leadIndex = Number(event.target.value); render(); });
	$('#mlaForecastPrevious').addEventListener('click', () => { state.leadIndex = Math.max(0, state.leadIndex - 1); $('#mlaForecastLead').value = state.leadIndex; render(); });
	$('#mlaForecastNext').addEventListener('click', () => { if (!state.timelineTimes.length) return; state.leadIndex = Math.min(state.timelineTimes.length - 1, state.leadIndex + 1); $('#mlaForecastLead').value = state.leadIndex; render(); });
	$('#mlaForecastArchiveDate').addEventListener('change', event => {
		if (parseArchiveTarget($('#mlaForecastArchiveSearch').value)) $('#mlaForecastArchiveSearch').value = '';
		state.archiveDate = event.target.value;
		state.archiveMonth = state.archiveDate ? state.archiveDate.slice(0, 7) : state.archiveMonth;
		state.archiveEntry = null;
		populateArchive(false);
	});
	$('#mlaForecastArchiveHour').addEventListener('change', event => {
		if (parseArchiveTarget($('#mlaForecastArchiveSearch').value)) $('#mlaForecastArchiveSearch').value = '';
		state.archiveHour = event.target.value;
		state.archiveEntry = null;
		populateArchive(false);
	});
	$('#mlaForecastCalendarMonth').addEventListener('change', event => {
		state.archiveMonth = event.target.value;
		renderArchiveCalendar();
	});
	$('#mlaForecastCalendarPrevious').addEventListener('click', () => {
		const months = archiveMonths();
		const index = months.indexOf(state.archiveMonth);
		if (index > 0) state.archiveMonth = months[index - 1];
		renderArchiveCalendar();
	});
	$('#mlaForecastCalendarNext').addEventListener('click', () => {
		const months = archiveMonths();
		const index = months.indexOf(state.archiveMonth);
		if (index >= 0 && index + 1 < months.length) state.archiveMonth = months[index + 1];
		renderArchiveCalendar();
	});
	$('#mlaForecastCalendarDays').addEventListener('click', event => {
		const button = event.target.closest('[data-forecast-calendar-date]');
		if (!button || button.disabled) return;
		if (parseArchiveTarget($('#mlaForecastArchiveSearch').value)) $('#mlaForecastArchiveSearch').value = '';
		state.archiveDate = button.dataset.forecastCalendarDate;
		state.archiveMonth = state.archiveDate.slice(0, 7);
		state.archiveHour = chooseArchiveHour(state.archiveDate);
		state.archiveEntry = null;
		$('#mlaForecastArchiveDate').value = state.archiveDate;
		$('#mlaForecastArchiveHour').value = state.archiveHour;
		populateArchive(false);
	});
	$('#mlaForecastArchiveSearch').addEventListener('input', () => {
		state.archiveDate = '';
		state.archiveEntry = null;
		clearTimeout(state.archiveSearchTimer);
		state.archiveSearchTimer = setTimeout(() => populateArchive(false), 250);
	});
	$('#mlaForecastArchiveResults').addEventListener('click', event => {
		if (event.target.closest('[data-forecast-archive-clear]')) { clearArchiveRuns(); return; }
		if (event.target.closest('#mlaForecastEra5Tile')) {
			state.showEra5 = !state.showEra5;
			populateArchive(false);
			render();
			return;
		}
		const button = event.target.closest('[data-forecast-archive-run]');
		if (!button) return;
		const key = button.dataset.forecastArchiveRun;
		if (state.archiveSelected.has(key)) { removeArchiveRun(key); return; }
		const entry = archiveEntries().find(item => `${item.model}:${item.cycle}` === key);
		if (entry) loadArchive(entry);
	});
	bindForecastMap();
	window.addEventListener('mla:forecast-visible', () => initialise());
	window.addEventListener('resize', () => { clearTimeout(state.resizeTimer); state.resizeTimer = setTimeout(resizeAndRender, 120); });

	const parameters = new URLSearchParams(location.search);
	if (['archive', 'tigge'].includes(parameters.get('fmode'))) setMode('archive');
})();
