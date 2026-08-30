(function () {
	'use strict';

	const root = document.getElementById('monsoon-low-atlas');
	const panel = document.getElementById('mlaPanelForecast');
	if (!root || !panel) return;
	const $ = selector => root.querySelector(selector);
	const config = JSON.parse(document.getElementById('mla-data-config').textContent || '{}');
	const CLASS_LABELS = ['Unclassified', 'Low', 'Depression', 'Deep depression', 'Cyclonic storm', 'Severe cyclonic storm', 'VSCS+'];
	const DOMAIN = {west: 45, east: 120, south: -15, north: 45};
	const state = {
		mode: 'latest', manifest: null, payload: null, geo: null, boundary: null,
		selectedModels: new Set(), latestPayloads: new Map(), modelLoads: new Map(),
		selectedSystem: null, initialization: 'latest', archiveModel: 'all', archiveEntry: null,
		leadIndex: 0, timelineTimes: [], weather: 'none', weatherModel: '', showMembers: false,
		mapZoom: 1, mapCenterLon: (DOMAIN.west + DOMAIN.east) / 2,
		mapCenterLat: (DOMAIN.south + DOMAIN.north) / 2,
		initialised: false, loading: false, weatherCache: new Map(), loadSerial: 0,
		renderSerial: 0, archiveSearchTimer: 0
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
		const url = `${joinUrl(config.forecastBase, 'manifest.json')}?v=${Date.now()}`;
		const response = await fetch(url, {cache: 'no-store'});
		if (!response.ok) throw new Error(`Forecast manifest returned HTTP ${response.status}`);
		const value = await response.json();
		if (value.schema !== 'mla-forecast-manifest-v1') throw new Error('Unsupported forecast manifest');
		return value;
	}

	function notice(message, tone, retry) {
		const node = $('#mlaForecastNotice');
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

	function freshness(value) {
		const hours = (Date.now() - new Date(value).getTime()) / 3600000;
		if (!Number.isFinite(hours)) return 'unknown age';
		if (hours < 1) return `${Math.max(0, Math.round(hours * 60))} min old`;
		if (hours < 48) return `${Math.round(hours)} h old`;
		return `${Math.round(hours / 24)} days old`;
	}

	function versionLabel(value) {
		return value && value.label && value.label !== 'Version not yet crosswalked' ? value.label : '';
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
	}

	function buildModelControls() {
		const models = state.manifest.models || [];
		const latest = state.manifest.latest || {};
		if (!state.selectedModels.size) {
			for (const model of models) if (model.kind === 'deterministic' && latest[model.id]) state.selectedModels.add(model.id);
			if (!state.selectedModels.size && preferredModel()) state.selectedModels.add(preferredModel());
		}
		state.selectedModels = new Set([...state.selectedModels].filter(id => latest[id]));
		$('#mlaForecastModelChecks').innerHTML = models.map(model => {
			const entry = latest[model.id];
			const checked = state.selectedModels.has(model.id);
			const title = entry ? `${model.label} initialized ${formatUtc(entry.cycle_utc)}` : `${model.label} unavailable`;
			return `<label class="mla-forecast-model-choice" style="--model-colour:${esc(model.colour || '#233f78')}" title="${esc(title)}"><input type="checkbox" value="${esc(model.id)}" ${checked ? 'checked' : ''} ${entry ? '' : 'disabled'}><i aria-hidden="true"></i><span>${esc(model.label)}</span></label>`;
		}).join('');
		const archive = $('#mlaForecastArchiveModel');
		const archiveModels = new Map(models.map(model => [model.id, model.label]));
		for (const entry of state.manifest.archive || []) if (!archiveModels.has(entry.model)) archiveModels.set(entry.model, entry.model_label || entry.model);
		archive.innerHTML = '<option value="all">All models</option>' + [...archiveModels].map(([id, label]) => `<option value="${esc(id)}">${esc(label)}</option>`).join('');
		archive.value = state.archiveModel;
		populateInitializationControls();
		populateWeatherModels();
	}

	function latestEntries() {
		if (!state.manifest) return [];
		return (state.manifest.models || []).filter(model => state.selectedModels.has(model.id)).map(model => {
			const entry = activeEntry(model.id);
			return {model, payload: state.latestPayloads.get(payloadKey(model.id, entry)) || null, entry};
		});
	}

	function displayEntries() {
		if (state.mode === 'archive') return state.payload ? [{model: modelDefinition(state.payload.model.id), payload: state.payload}] : [];
		return latestEntries().filter(item => item.payload);
	}

	function populateWeatherModels() {
		const select = $('#mlaForecastWeatherModel');
		if (!select) return;
		const entries = state.mode === 'latest' ? latestEntries().filter(item => item.payload) : [];
		if (!entries.some(item => item.model.id === state.weatherModel)) {
			state.weatherModel = entries.find(item => item.model.id === preferredModel())?.model.id || entries[0]?.model.id || '';
		}
		select.innerHTML = entries.map(item => `<option value="${esc(item.model.id)}">${esc(item.model.label)}</option>`).join('') || '<option value="">No selected model loaded</option>';
		select.value = state.weatherModel;
		select.disabled = state.weather === 'none' || !entries.length;
	}

	function filteredArchive() {
		const query = $('#mlaForecastArchiveSearch').value.trim().toLowerCase();
		const compact = query.replace(/[^a-z0-9]/g, '');
		return (state.manifest.archive || []).filter(entry => {
			if (state.archiveModel !== 'all' && entry.model !== state.archiveModel) return false;
			if (!query) return true;
			const searchable = `${entry.search_text || ''} ${entry.cycle || ''}`.toLowerCase();
			return searchable.includes(query) || (compact && searchable.replace(/[^a-z0-9]/g, '').includes(compact));
		});
	}

	function populateArchive(loadFirst) {
		const entries = filteredArchive();
		const select = $('#mlaForecastArchiveCase');
		const previousKey = state.archiveEntry ? `${state.archiveEntry.model}:${state.archiveEntry.cycle}` : '';
		select.innerHTML = entries.slice(0, 5000).map(entry => {
			const key = `${entry.model}:${entry.cycle}`;
			const names = (entry.verification_labels || []).join(', ');
			const version = versionLabel(entry.model_version);
			return `<option value="${esc(key)}">${esc(`${formatUtc(entry.cycle_utc)} · ${entry.model_label}${version ? ` · ${version}` : ''}${names ? ` · ${names}` : ''}`)}</option>`;
		}).join('') || '<option value="">No matching archived forecasts</option>';
		let selected = entries.find(entry => `${entry.model}:${entry.cycle}` === previousKey);
		if (!selected) selected = entries[0] || null;
		state.archiveEntry = selected;
		if (selected) select.value = `${selected.model}:${selected.cycle}`;
		if (loadFirst && selected) loadArchive(selected);
		if (!selected) {
			state.payload = null;
			notice('No archived forecasts match this search.', 'flag', false);
			render();
		}
	}

	async function loadArchivePayload(entry) {
		const serial = ++state.loadSerial;
		state.loading = true;
		notice(`Opening archived ${entry.model_label || entry.model} forecast…`, '', false);
		try {
			const payload = await fetchGzipJson(joinUrl(config.forecastBase, entry.url));
			if (serial !== state.loadSerial) return;
			state.payload = payload;
			state.selectedSystem = null;
			state.leadIndex = 0;
			state.weather = 'none';
			configureTimeline(false);
			const qa = payload.qa && payload.qa.status;
			const message = `Archived ${payload.model.label} ${formatUtc(payload.cycle_utc)} · ${payload.verification.status.replaceAll('_', ' ')}.`;
			notice(message, qa === 'failed' ? 'flag' : 'good', false);
			await render();
		} catch (error) {
			if (serial !== state.loadSerial) return;
			state.payload = null;
			notice(`Forecast could not be loaded: ${error.message || error}`, 'flag', true);
			render();
		} finally {
			if (serial === state.loadSerial) state.loading = false;
		}
	}

	async function ensureLatest(modelId) {
		const entry = activeEntry(modelId);
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
		const selected = [...state.selectedModels];
		if (!selected.length) {
			notice('Select at least one forecast model.', 'flag', false);
			configureTimeline(true);
			return render();
		}
		state.loading = true;
		notice(`Opening ${selected.length} selected forecast model${selected.length === 1 ? '' : 's'}…`, '', false);
		const results = await Promise.allSettled(selected.map(ensureLatest));
		state.loading = false;
		const failures = results.filter(result => result.status === 'rejected');
		const loaded = latestEntries().filter(item => item.payload);
		if (state.selectedSystem && !loaded.some(item => item.model.id === state.selectedSystem.modelId && (item.payload.systems || []).some(system => system.id === state.selectedSystem.systemId))) state.selectedSystem = null;
		populateWeatherModels();
		configureTimeline(true);
		const cycles = [...new Set(loaded.map(item => formatUtc(item.payload.cycle_utc)))];
		const message = loaded.length
			? `${loaded.length} model${loaded.length === 1 ? '' : 's'} compared · initialized ${cycles.join(' / ')}${failures.length ? ` · ${failures.length} unavailable` : ''}.`
			: `Selected forecasts could not be loaded${failures[0] ? `: ${failures[0].reason.message || failures[0].reason}` : '.'}`;
		notice(message, loaded.length ? (failures.length ? 'flag' : 'good') : 'flag', !loaded.length);
		await render();
	}

	function loadArchive(entry) {
		if (!entry) return;
		state.archiveEntry = entry;
		loadArchivePayload(entry);
	}

	function modelDefinition(id) {
		const model = (state.manifest.models || []).find(item => item.id === id);
		if (model) return model;
		const archived = (state.manifest.archive || []).find(item => item.model === id);
		return {id, label: archived && archived.model_label || id, colour: id === 'gefs-control' ? '#b46722' : '#233f78'};
	}

	function currentValidTime() {
		return state.timelineTimes[state.leadIndex] || null;
	}

	function configureTimeline(preserve) {
		const slider = $('#mlaForecastLead');
		const previous = preserve ? currentValidTime() : null;
		const times = new Set();
		for (const {payload} of displayEntries()) for (const value of payload.valid_times || []) {
			const stamp = new Date(value).getTime();
			if (Number.isFinite(stamp)) times.add(stamp);
		}
		state.timelineTimes = [...times].sort((a, b) => a - b);
		if (previous != null && state.timelineTimes.length) {
			let nearest = 0;
			for (let index = 1; index < state.timelineTimes.length; index++) if (Math.abs(state.timelineTimes[index] - previous) < Math.abs(state.timelineTimes[nearest] - previous)) nearest = index;
			state.leadIndex = nearest;
		} else state.leadIndex = Math.min(state.leadIndex, Math.max(0, state.timelineTimes.length - 1));
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
			state.loading = false;
			if (state.mode === 'archive') {
				if (state.archiveEntry) loadArchive(state.archiveEntry); else render();
			} else loadSelectedModels();
		} catch (error) {
			state.loading = false;
			notice(`Forecast service is unavailable: ${error.message || error}`, 'flag', true);
			render();
		}
	}

	function setMode(mode) {
		state.mode = mode;
		$('#mlaForecastModeLatest').setAttribute('aria-pressed', String(mode === 'latest'));
		$('#mlaForecastModeArchive').setAttribute('aria-pressed', String(mode === 'archive'));
		$('#mlaForecastLiveControls').hidden = mode !== 'latest';
		$('#mlaForecastArchiveControls').hidden = mode !== 'archive';
		$('#mlaForecastWeather').disabled = mode === 'archive';
		if (!state.initialised) { initialise(); return; }
		state.payload = null;
		state.selectedSystem = null;
		render();
		if (mode === 'archive') {
			state.weather = 'none';
			if (state.archiveEntry) loadArchive(state.archiveEntry); else populateArchive(true);
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
		state.mapZoom = 1;
		state.mapCenterLon = (DOMAIN.west + DOMAIN.east) / 2;
		state.mapCenterLat = (DOMAIN.south + DOMAIN.north) / 2;
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
		if (state.mode !== 'latest' || state.weather === 'none') return;
		const payload = state.latestPayloads.get(payloadKey(state.weatherModel, activeEntry(state.weatherModel)));
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
		const source = state.mode === 'archive'
			? (state.manifest.archive || []).filter(entry => entry.model === modelId).map(entry => ({...entry, centres: entry.analysis_centres || []}))
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
		const maximumGap = state.mode === 'archive' ? 60 : 30;
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

	function drawPath(context, map, points, colour, width, alpha, current, dashedFuture) {
		if (!points || points.length < 2) return;
		for (const phase of dashedFuture ? ['past', 'future'] : ['all']) {
			context.beginPath(); let started = false;
			for (const point of points) {
				const belongs = phase === 'all' || (phase === 'past' ? Number(point[0]) <= current : Number(point[0]) >= current);
				if (!belongs) { started = false; continue; }
				const xy = map.project(Number(point[2]), Number(point[1]));
				if (!started) { context.moveTo(...xy); started = true; } else context.lineTo(...xy);
			}
			context.globalAlpha = alpha;
			context.strokeStyle = colour; context.lineWidth = width; context.lineJoin = 'round'; context.lineCap = 'round';
			context.setLineDash(phase === 'future' ? [5, 5] : []); context.stroke();
		}
		context.setLineDash([]); context.globalAlpha = 1;
	}

	function pointAt(points, step) {
		if (!points || !points.length) return null;
		let best = points[0];
		for (const point of points) if (Math.abs(Number(point[0]) - step) < Math.abs(Number(best[0]) - step)) best = point;
		return Math.abs(Number(best[0]) - step) <= 1 ? best : null;
	}

	function disturbanceLabel(system) {
		return String(system && system.label || 'Disturbance').replace(/^Forecast system/i, 'Disturbance');
	}

	function drawTracks() {
		const target = canvasContext('#mlaForecastTracks');
		for (const {model, payload} of displayEntries()) {
			const current = stepForPayload(payload);
			const colour = model.colour || payload.model.colour || '#233f78';
			for (const system of payload.systems || []) {
				const tracks = tracksForSystem(payload, system);
				const selected = state.selectedSystem && state.selectedSystem.modelId === model.id && state.selectedSystem.systemId === system.id;
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
		if (state.mode === 'archive' && state.payload && state.payload.verification) {
			const current = stepForPayload(state.payload);
			for (const track of state.payload.verification.tracks || []) {
				target.context.beginPath();
				track.points.forEach((point, index) => { const xy = target.projection.project(point[2], point[1]); if (!index) target.context.moveTo(...xy); else target.context.lineTo(...xy); });
				target.context.setLineDash([7, 5]); target.context.strokeStyle = '#282119'; target.context.lineWidth = 2.2; target.context.globalAlpha = .9; target.context.stroke(); target.context.setLineDash([]); target.context.globalAlpha = 1;
				const marker = pointAt(track.points, current);
				if (marker) { const xy = target.projection.project(marker[2], marker[1]); target.context.beginPath(); target.context.arc(xy[0], xy[1], 4, 0, Math.PI * 2); target.context.fillStyle = '#fffdf6'; target.context.fill(); target.context.strokeStyle = '#282119'; target.context.lineWidth = 2; target.context.stroke(); }
			}
		}
		drawAnnotations(target);
	}

	function selectedSystemRecord() {
		if (state.selectedSystem) {
			const entry = displayEntries().find(item => item.model.id === state.selectedSystem.modelId);
			const system = entry && (entry.payload.systems || []).find(item => item.id === state.selectedSystem.systemId);
			if (entry && system) return {...entry, system};
		}
		if (state.mode === 'archive' && state.payload && (state.payload.systems || []).length === 1) {
			return {model: modelDefinition(state.payload.model.id), payload: state.payload, system: state.payload.systems[0]};
		}
		return null;
	}

	function modelStatusHtml() {
		if (!state.manifest || state.mode !== 'latest') return '';
		return `<h4>Feed status</h4><div class="mla-forecast-model-list">${(state.manifest.models || []).map(model => {
			const attempt = (state.manifest.attempts || {})[model.id] || {};
			const latest = (state.manifest.latest || {})[model.id];
			const status = attempt.status || (latest ? 'success' : 'unavailable');
			const version = latest ? versionLabel(latest.model_version) : '';
			const detail = latest ? `${version ? `${version} · ` : ''}${freshness(latest.cycle_utc)}` : status === 'failed' ? 'last attempt failed' : 'not yet available';
			return `<div class="mla-forecast-model-status" data-status="${esc(status)}"><i></i><span>${esc(model.label)}</span><small>${esc(detail)}</small></div>`;
		}).join('')}</div>`;
	}

	function renderDossier() {
		const node = $('#mlaForecastDossier');
		const entries = displayEntries();
		if (!entries.length) {
			node.innerHTML = `<h3>Forecast status</h3><p>${state.loading ? 'Loading forecast guidance…' : 'No forecast cycle is open.'}</p>${modelStatusHtml()}`;
			return;
		}
		const boundary = state.boundary ? 'Survey of India outline for an India IP' : 'Natural Earth borders';
		const selected = selectedSystemRecord();
		if (selected) {
			const {model, payload, system} = selected;
			const current = stepForPayload(payload);
			const stitched = stitchedTrack(payload, system, model.id);
			const marker = pointAt(meanTrack(payload, system), current);
			const historyHours = stitched.history.length ? Math.abs(Number(stitched.history[0][0])) : 0;
			const systemTracks = tracksForSystem(payload, system);
			const peakCategory = systemTracks.length ? Math.max(...systemTracks.map(track => Number(track.maximum_provisional_category || 0))) : null;
			const gateKinds = [...new Set(systemTracks.map(track => track.publication_gate).filter(Boolean))];
			const verification = payload.verification || null;
			const version = versionLabel(payload.model_version);
			const versionHtml = version ? `${payload.model_version.source_url ? `<a href="${esc(payload.model_version.source_url)}" target="_blank" rel="noopener">${esc(version)}</a>` : esc(version)} · ` : '';
			const archiveCoverage = payload.archive_coverage || null;
			node.innerHTML = `<div class="mla-forecast-dossier-head"><h3>${esc(`${model.label} · ${disturbanceLabel(system)}`)}</h3>${state.selectedSystem ? '<button class="mla-btn mla-btn-small mla-btn-quiet" type="button" data-forecast-clear-system>Compare models</button>' : ''}</div>
				<p>${versionHtml}initialized ${esc(formatUtc(payload.cycle_utc))}. ${payload.model.kind === 'ensemble' ? 'The thick line is the member-mean path.' : 'The deterministic/control path is shown.'}${stitched.history.length ? ` The pre-initialization segment joins continuity-matched ${state.mode === 'archive' ? 'available' : 'six-hourly'} t+0 centres.` : ''}</p>
				<div class="mla-forecast-facts">
					<div class="mla-forecast-fact"><span>Lead</span><strong>+${esc(current)} h</strong></div>
					<div class="mla-forecast-fact"><span>Members</span><strong>${esc(`${system.member_count}/${payload.members.expected}`)}</strong></div>
					<div class="mla-forecast-fact"><span>Current mean centre</span><strong>${marker ? `${Number(marker[2]).toFixed(1)}°N, ${Number(marker[1]).toFixed(1)}°E` : 'not active'}</strong></div>
					<div class="mla-forecast-fact"><span>Prior t+0 history</span><strong>${stitched.history.length ? `${esc(stitched.history.length)} centres · ${esc(historyHours)} h` : 'no confident match'}</strong></div>
					<div class="mla-forecast-fact"><span>Peak guidance</span><strong>${peakCategory == null ? '—' : esc(CLASS_LABELS[peakCategory] || `Class ${peakCategory}`)}</strong></div>
					<div class="mla-forecast-fact"><span>Active leads</span><strong>+${esc(system.start_step)} to +${esc(system.end_step)} h</strong></div>
					<div class="mla-forecast-fact"><span>Publication gate</span><strong>${gateKinds.length ? esc(gateKinds.join(', ').replaceAll('-', ' ')) : '—'}</strong></div>
				</div>
				${verification ? `<h4>ERA5 verification</h4><p>${esc(verification.status.replaceAll('_', ' '))}${verification.tracks && verification.tracks.length ? ` · ${esc(verification.tracks.map(track => track.label).join(', '))}` : ` · catalogue coverage ends ${esc(String(verification.coverage_end || '').slice(0, 10))}`}.</p>` : ''}
				${archiveCoverage ? `<h4>Archive completeness</h4><p>${esc(archiveCoverage.valid_time_count)} valid times · ${esc(archiveCoverage.published_track_count)} tracks · ${esc(archiveCoverage.published_track_point_count)} track points. Cycles with no detected disturbance are retained.</p>` : ''}
				<div class="mla-forecast-track-legend" style="--legend-colour:${esc(model.colour || '#233f78')}"><span><i></i>${payload.model.kind === 'ensemble' ? 'Member-mean path' : 'Deterministic/control path'}</span>${stitched.history.length ? '<span><i data-kind="analysis"></i>Matched prior t+0 centres</span>' : ''}<span><i data-kind="future"></i>Forecast after selected time</span>${payload.model.kind === 'ensemble' ? '<span><i data-kind="member"></i>Individual member</span>' : ''}${verification && verification.tracks && verification.tracks.length ? '<span><i data-kind="era5"></i>Hourly ERA5 v5.6 track</span>' : ''}</div>
				<h4>Source and status</h4><p><a href="${esc(payload.source.url)}" target="_blank" rel="noopener">${esc(payload.source.service)}</a> · ${esc(payload.source.licence)} · ${esc(payload.qa.status)} QA.</p><p>${esc(boundary)}. Forecast classes are provisional.</p>${modelStatusHtml()}`;
			return;
		}
		const rows = [];
		let trackCount = 0;
		for (const {model, payload} of entries) {
			trackCount += (payload.tracks || []).length;
			const current = stepForPayload(payload);
			const version = versionLabel(payload.model_version);
			if (!(payload.systems || []).length) rows.push(`<div class="mla-forecast-comparison-row is-empty" style="--model-colour:${esc(model.colour || '#233f78')}"><i></i><span><strong>${esc(`${model.label}${version ? ` · ${version}` : ''}`)}</strong><small>No disturbance passed the forecast gate</small></span></div>`);
			for (const system of payload.systems || []) {
				const marker = pointAt(meanTrack(payload, system), current);
				const tracks = tracksForSystem(payload, system);
				const peak = tracks.length ? Math.max(...tracks.map(track => Number(track.maximum_provisional_category || 0))) : 0;
				rows.push(`<button class="mla-forecast-comparison-row" type="button" data-forecast-model="${esc(model.id)}" data-forecast-system="${esc(system.id)}" style="--model-colour:${esc(model.colour || '#233f78')}"><i aria-hidden="true"></i><span><strong>${esc(`${model.label} · ${disturbanceLabel(system)}`)}</strong><small>${version ? `${esc(version)} · ` : ''}${marker ? `${Number(marker[2]).toFixed(1)}°N, ${Number(marker[1]).toFixed(1)}°E now` : 'not active at this valid time'} · ${esc(system.member_count)} member${system.member_count === 1 ? '' : 's'} · peak ${esc(CLASS_LABELS[peak] || `class ${peak}`)}</small></span></button>`);
			}
		}
		const disturbanceCount = entries.reduce((sum, item) => sum + (item.payload.systems || []).length, 0);
		const archiveMode = state.mode === 'archive' && entries.length === 1;
		const archivePayload = archiveMode ? entries[0].payload : null;
		const archiveVersion = archivePayload ? versionLabel(archivePayload.model_version) : '';
		const archiveCoverage = archivePayload && archivePayload.archive_coverage;
		node.innerHTML = `<h3>${archiveMode ? 'Archived forecast' : 'Model comparison'}</h3><p>${archiveMode ? `${archiveVersion ? `${esc(archiveVersion)} · ` : ''}initialized ${esc(formatUtc(archivePayload.cycle_utc))}` : esc(formatUtc(currentValidTime()))}. Each disturbance is a coherent cluster of tracks within one model; click a marker or row for its details.</p>
			<div class="mla-forecast-facts"><div class="mla-forecast-fact"><span>Models</span><strong>${esc(entries.length)}</strong></div><div class="mla-forecast-fact"><span>Disturbances</span><strong>${esc(disturbanceCount)}</strong></div><div class="mla-forecast-fact"><span>Published member tracks</span><strong>${esc(trackCount)}</strong></div><div class="mla-forecast-fact"><span>Weather source</span><strong>${state.weather === 'none' ? 'off' : esc(modelDefinition(state.weatherModel).label)}</strong></div></div>
			${archiveCoverage ? `<h4>Archive completeness</h4><p>${esc(archiveCoverage.valid_time_count)} valid times · ${esc(archiveCoverage.published_track_count)} tracks · ${esc(archiveCoverage.published_track_point_count)} track points. Cycles with no detected disturbance are retained.</p>` : ''}
			<h4>Detected disturbances</h4><div class="mla-forecast-comparison-list">${rows.join('') || '<p>No disturbance passed the frozen forecast gate in this cycle.</p>'}</div><p>${esc(boundary)}. Forecast classes are provisional.</p>${modelStatusHtml()}`;
	}

	async function render() {
		const serial = ++state.renderSerial;
		drawBase();
		$('#mlaForecastWeatherKey').hidden = true;
		await drawWeather();
		if (serial !== state.renderSerial) return;
		drawTracks();
		updateTimeLabel();
		renderDossier();
		const entries = displayEntries();
		const count = entries.reduce((sum, item) => sum + (item.payload.tracks || []).length, 0);
		const systems = entries.reduce((sum, item) => sum + (item.payload.systems || []).length, 0);
		const mapStack = $('#mlaForecastMapStack');
		mapStack.dataset.zoom = state.mapZoom.toFixed(3);
		mapStack.dataset.centerLon = state.mapCenterLon.toFixed(3);
		mapStack.dataset.centerLat = state.mapCenterLat.toFixed(3);
		$('#mlaForecastMapStatus').textContent = entries.length
			? `${entries.length} model${entries.length === 1 ? '' : 's'} · ${systems} disturbance${systems === 1 ? '' : 's'} · ${count} member track${count === 1 ? '' : 's'} · ${state.mode === 'archive' ? 'ERA5 verification where available' : state.weather === 'none' ? 'weather off' : `${modelDefinition(state.weatherModel).label} weather`}`
			: 'Forecast data not loaded.';
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
		for (const {model, payload} of displayEntries()) {
			const current = stepForPayload(payload);
			for (const system of payload.systems || []) {
				const point = pointAt(meanTrack(payload, system), current);
				if (!point) continue;
				const xy = map.project(point[2], point[1]);
				const distance = Math.hypot(event.clientX - rectangle.left - xy[0], event.clientY - rectangle.top - xy[1]);
				if (!best || distance < best.distance) best = {model, system, distance};
			}
		}
		if (best && best.distance <= (event.pointerType === 'touch' ? 25 : 18)) {
			state.selectedSystem = {modelId: best.model.id, systemId: best.system.id};
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
	$('#mlaForecastWeather').addEventListener('change', async event => { state.weather = event.target.value; populateWeatherModels(); await render(); });
	$('#mlaForecastWeatherModel').addEventListener('change', async event => { state.weatherModel = event.target.value; await render(); });
	$('#mlaForecastMembers').addEventListener('change', event => { state.showMembers = event.target.checked; render(); });
	$('#mlaForecastLead').addEventListener('input', event => { state.leadIndex = Number(event.target.value); render(); });
	$('#mlaForecastPrevious').addEventListener('click', () => { state.leadIndex = Math.max(0, state.leadIndex - 1); $('#mlaForecastLead').value = state.leadIndex; render(); });
	$('#mlaForecastNext').addEventListener('click', () => { if (!state.timelineTimes.length) return; state.leadIndex = Math.min(state.timelineTimes.length - 1, state.leadIndex + 1); $('#mlaForecastLead').value = state.leadIndex; render(); });
	$('#mlaForecastArchiveModel').addEventListener('change', event => { state.archiveModel = event.target.value; populateArchive(true); });
	$('#mlaForecastArchiveSearch').addEventListener('input', () => {
		clearTimeout(state.archiveSearchTimer);
		state.archiveSearchTimer = setTimeout(() => populateArchive(true), 250);
	});
	$('#mlaForecastArchiveCase').addEventListener('change', event => {
		const entry = (state.manifest.archive || []).find(item => `${item.model}:${item.cycle}` === event.target.value);
		if (entry) loadArchive(entry);
	});
	$('#mlaForecastDossier').addEventListener('click', event => {
		const clear = event.target.closest('[data-forecast-clear-system]');
		if (clear) { state.selectedSystem = null; render(); return; }
		const row = event.target.closest('[data-forecast-model][data-forecast-system]');
		if (row) { state.selectedSystem = {modelId: row.dataset.forecastModel, systemId: row.dataset.forecastSystem}; render(); }
	});
	bindForecastMap();
	window.addEventListener('mla:forecast-visible', () => initialise());
	window.addEventListener('resize', () => { clearTimeout(state.resizeTimer); state.resizeTimer = setTimeout(resizeAndRender, 120); });

	const parameters = new URLSearchParams(location.search);
	if (parameters.get('fmode') === 'archive') setMode('archive');
})();
