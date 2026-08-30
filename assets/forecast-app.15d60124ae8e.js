(function () {
	'use strict';

	const root = document.getElementById('monsoon-low-atlas');
	const panel = document.getElementById('mlaPanelForecast');
	if (!root || !panel) return;
	const $ = selector => root.querySelector(selector);
	const config = JSON.parse(document.getElementById('mla-data-config').textContent || '{}');
	const CLASS_LABELS = ['Unclassified', 'Low', 'Depression', 'Deep depression', 'Cyclonic storm', 'Severe cyclonic storm', 'VSCS+'];
	const SYSTEM_COLOURS = ['#233f78', '#aa3d2d', '#08736f', '#c9631b', '#64224f', '#3978a8', '#8f6d16'];
	const DOMAIN = {west: 45, east: 120, south: -15, north: 45};
	const state = {
		mode: 'latest', manifest: null, payload: null, geo: null, boundary: null,
		model: '', system: 'all', archiveModel: 'all', archiveEntry: null,
		leadIndex: 0, weather: 'none', showMembers: true, initialised: false,
		loading: false, weatherCache: new Map(), loadSerial: 0, archiveSearchTimer: 0
	};

	function esc(value) {
		return String(value == null ? '' : value).replace(/[&<>'"]/g, character => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'}[character]));
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

	function buildModelControls() {
		const models = state.manifest.models || [];
		const latest = state.manifest.latest || {};
		const select = $('#mlaForecastModel');
		select.innerHTML = models.map(model => {
			const entry = latest[model.id];
			const suffix = entry ? ` · ${String(entry.cycle).slice(4, 8)} ${String(entry.cycle).slice(8, 10)} UTC` : ' · unavailable';
			return `<option value="${esc(model.id)}" ${entry ? '' : 'disabled'}>${esc(model.label + suffix)}</option>`;
		}).join('');
		if (!state.model || !latest[state.model]) state.model = preferredModel();
		select.value = state.model;
		const archive = $('#mlaForecastArchiveModel');
		archive.innerHTML = '<option value="all">All models</option>' + models.map(model => `<option value="${esc(model.id)}">${esc(model.label)}</option>`).join('');
		archive.value = state.archiveModel;
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
		select.innerHTML = entries.slice(0, 800).map(entry => {
			const key = `${entry.model}:${entry.cycle}`;
			const names = (entry.verification_labels || []).join(', ');
			return `<option value="${esc(key)}">${esc(`${formatUtc(entry.cycle_utc)} · ${entry.model_label}${names ? ` · ${names}` : ''}`)}</option>`;
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

	async function loadPayload(entry, archive) {
		const serial = ++state.loadSerial;
		state.loading = true;
		notice(`Opening ${archive ? 'archived' : 'latest'} ${entry.model_label || state.model} forecast…`, '', false);
		try {
			const payload = await fetchGzipJson(joinUrl(config.forecastBase, entry.url));
			if (serial !== state.loadSerial) return;
			state.payload = payload;
			state.system = 'all';
			state.leadIndex = 0;
			state.weather = archive ? 'none' : $('#mlaForecastWeather').value;
			populateSystems();
			configureTimeline();
			const qa = payload.qa && payload.qa.status;
			const message = archive
				? `Archived ${payload.model.label} ${formatUtc(payload.cycle_utc)} · ${payload.verification.status.replaceAll('_', ' ')}.`
				: `${payload.model.label} ${formatUtc(payload.cycle_utc)} · ${freshness(payload.cycle_utc)} · ${payload.members.available}/${payload.members.expected} members.`;
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

	function loadLatest(model) {
		state.model = model || state.model || preferredModel();
		const entry = state.manifest.latest && state.manifest.latest[state.model];
		if (!entry) {
			notice(`${state.model || 'This model'} has no completed live cycle.`, 'flag', true);
			return;
		}
		loadPayload({...entry, model_label: modelDefinition(state.model).label}, false);
	}

	function loadArchive(entry) {
		if (!entry) return;
		state.archiveEntry = entry;
		loadPayload(entry, true);
	}

	function modelDefinition(id) {
		return (state.manifest.models || []).find(model => model.id === id) || {id, label: id, colour: '#233f78'};
	}

	function populateSystems() {
		const select = $('#mlaForecastSystem');
		const systems = state.payload ? state.payload.systems || [] : [];
		select.innerHTML = '<option value="all">All forecast systems</option>' + systems.map(system => `<option value="${esc(system.id)}">${esc(`${system.label} · ${system.member_count} member${system.member_count === 1 ? '' : 's'}`)}</option>`).join('');
		select.value = state.system;
	}

	function configureTimeline() {
		const slider = $('#mlaForecastLead');
		const steps = state.payload ? state.payload.steps || [] : [];
		slider.min = 0;
		slider.max = Math.max(0, steps.length - 1);
		slider.value = Math.min(state.leadIndex, Math.max(0, steps.length - 1));
		updateTimeLabel();
	}

	function updateTimeLabel() {
		if (!state.payload) { $('#mlaForecastTime').textContent = '—'; return; }
		const step = state.payload.steps[state.leadIndex] || 0;
		const valid = state.payload.valid_times[state.leadIndex];
		$('#mlaForecastTime').textContent = `+${String(step).padStart(3, '0')} h · ${formatUtc(valid)}`;
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
			} else loadLatest(state.model);
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
		state.system = 'all';
		render();
		if (mode === 'archive') {
			state.weather = 'none';
			if (state.archiveEntry) loadArchive(state.archiveEntry); else populateArchive(true);
		} else loadLatest(state.model);
	}

	function projection(width, height) {
		const padding = 14;
		const scale = Math.min((width - 2 * padding) / (DOMAIN.east - DOMAIN.west), (height - 2 * padding) / (DOMAIN.north - DOMAIN.south));
		const mapWidth = (DOMAIN.east - DOMAIN.west) * scale;
		const mapHeight = (DOMAIN.north - DOMAIN.south) * scale;
		const left = (width - mapWidth) / 2;
		const top = (height - mapHeight) / 2;
		return {
			project(lat, lon) { return [left + (lon - DOMAIN.west) * scale, top + (DOMAIN.north - lat) * scale]; },
			left, top, width: mapWidth, height: mapHeight, scale
		};
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
		context.strokeStyle = 'rgba(40,33,25,.12)';
		context.lineWidth = .7;
		context.font = '11px "effra", Effra, Arial, sans-serif';
		context.fillStyle = 'rgba(40,33,25,.55)';
		for (let lon = 50; lon <= 120; lon += 10) {
			const first = map.project(DOMAIN.south, lon), second = map.project(DOMAIN.north, lon);
			context.beginPath(); context.moveTo(...first); context.lineTo(...second); context.stroke();
			context.fillText(`${lon}°E`, second[0] + 3, second[1] + 13);
		}
		for (let lat = -10; lat <= 40; lat += 10) {
			const first = map.project(lat, DOMAIN.west), second = map.project(lat, DOMAIN.east);
			context.beginPath(); context.moveTo(...first); context.lineTo(...second); context.stroke();
			context.fillText(`${lat}°`, first[0] + 3, first[1] - 3);
		}
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

	async function decodeWeather(name) {
		if (!state.payload || !state.payload.weather || !state.payload.weather[name]) return null;
		const key = `${state.payload.model.id}:${state.payload.cycle}:${name}`;
		if (state.weatherCache.has(key)) return state.weatherCache.get(key);
		const field = state.payload.weather[name];
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
		if (state.mode !== 'latest' || state.weather === 'none' || !state.payload) return;
		const record = await decodeWeather(state.weather);
		if (!record || !state.payload) return;
		const [frames, ny, nx] = record.field.shape;
		const frame = Math.min(frames - 1, state.leadIndex);
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
		const grid = state.payload.grid;
		const northwest = target.projection.project(grid.north, grid.west);
		const southeast = target.projection.project(grid.south, grid.east);
		target.context.imageSmoothingEnabled = false;
		target.context.drawImage(image, northwest[0], northwest[1], southeast[0] - northwest[0], southeast[1] - northwest[1]);
		$('#mlaForecastWeatherKey').hidden = false;
		$('#mlaForecastWeatherMaximum').textContent = state.weather === 'vorticity' ? '20 × 10⁻⁵ s⁻¹' : '100 mm';
	}

	function tracksForSystem(system) {
		if (!state.payload) return [];
		const ids = new Set(system.track_ids || []);
		return state.payload.tracks.filter(track => ids.has(track.id));
	}

	function meanTrack(system) {
		const tracks = tracksForSystem(system);
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

	function visibleSystems() {
		if (!state.payload) return [];
		return (state.payload.systems || []).filter(system => state.system === 'all' || system.id === state.system);
	}

	function drawTracks() {
		const target = canvasContext('#mlaForecastTracks');
		if (!state.payload) return;
		const current = Number(state.payload.steps[state.leadIndex] || 0);
		const colour = state.payload.model.colour || '#233f78';
		const systems = visibleSystems();
		for (const [systemIndex, system] of systems.entries()) {
			const systemColour = state.system === 'all' ? SYSTEM_COLOURS[systemIndex % SYSTEM_COLOURS.length] : colour;
			const tracks = tracksForSystem(system);
			if (state.showMembers && state.payload.model.kind === 'ensemble') {
				for (const track of tracks) drawPath(target.context, target.projection, track.points, systemColour, 1, .32, current, true);
			}
			const mean = meanTrack(system);
			drawPath(target.context, target.projection, mean, systemColour, state.payload.model.kind === 'ensemble' ? 3.1 : 3.5, .96, current, true);
			const marker = pointAt(mean, current);
			if (marker) {
				const xy = target.projection.project(marker[2], marker[1]);
				target.context.beginPath(); target.context.arc(xy[0], xy[1], 5.3, 0, Math.PI * 2);
				target.context.fillStyle = systemColour; target.context.fill(); target.context.lineWidth = 2; target.context.strokeStyle = '#fffdf6'; target.context.stroke();
			}
		}
		if (state.mode === 'archive' && state.payload.verification) {
			for (const track of state.payload.verification.tracks || []) {
				target.context.beginPath();
				track.points.forEach((point, index) => { const xy = target.projection.project(point[2], point[1]); if (!index) target.context.moveTo(...xy); else target.context.lineTo(...xy); });
				target.context.setLineDash([7, 5]); target.context.strokeStyle = '#282119'; target.context.lineWidth = 2.2; target.context.globalAlpha = .9; target.context.stroke(); target.context.setLineDash([]); target.context.globalAlpha = 1;
				const marker = pointAt(track.points, current);
				if (marker) { const xy = target.projection.project(marker[2], marker[1]); target.context.beginPath(); target.context.arc(xy[0], xy[1], 4, 0, Math.PI * 2); target.context.fillStyle = '#fffdf6'; target.context.fill(); target.context.strokeStyle = '#282119'; target.context.lineWidth = 2; target.context.stroke(); }
			}
		}
	}

	function currentSystem() {
		if (!state.payload) return null;
		if (state.system !== 'all') return (state.payload.systems || []).find(system => system.id === state.system) || null;
		return (state.payload.systems || []).length === 1 ? state.payload.systems[0] : null;
	}

	function modelStatusHtml() {
		if (!state.manifest || state.mode !== 'latest') return '';
		return `<h4>Feed status</h4><div class="mla-forecast-model-list">${(state.manifest.models || []).map(model => {
			const attempt = (state.manifest.attempts || {})[model.id] || {};
			const latest = (state.manifest.latest || {})[model.id];
			const status = attempt.status || (latest ? 'success' : 'unavailable');
			const detail = latest ? freshness(latest.cycle_utc) : status === 'failed' ? 'last attempt failed' : 'not yet available';
			return `<div class="mla-forecast-model-status" data-status="${esc(status)}"><i></i><span>${esc(model.label)}</span><small>${esc(detail)}</small></div>`;
		}).join('')}</div>`;
	}

	function renderDossier() {
		const node = $('#mlaForecastDossier');
		if (!state.payload) {
			node.innerHTML = `<h3>Forecast status</h3><p>${state.loading ? 'Loading forecast guidance…' : 'No forecast cycle is open.'}</p>${modelStatusHtml()}`;
			return;
		}
		const payload = state.payload;
		const system = currentSystem();
		const current = Number(payload.steps[state.leadIndex] || 0);
		const mean = system ? meanTrack(system) : [];
		const marker = pointAt(mean, current);
		const systemTracks = system ? tracksForSystem(system) : [];
		const peakCategory = systemTracks.length
			? Math.max(...systemTracks.map(track => Number(track.maximum_provisional_category || 0)))
			: null;
		const gateKinds = [...new Set(systemTracks.map(track => track.publication_gate).filter(Boolean))];
		const verification = payload.verification || null;
		const title = system ? system.label : (payload.systems || []).length ? 'All forecast systems' : 'No forecast systems';
		const boundary = state.boundary ? 'Survey of India outline for an India IP' : 'Natural Earth borders';
		node.innerHTML = `<h3>${esc(title)}</h3>
			<p>${esc(payload.model.label)} initialized ${esc(formatUtc(payload.cycle_utc))}. ${payload.model.kind === 'ensemble' ? 'Thick lines are member-mean system paths.' : 'The deterministic track is shown.'}</p>
			<div class="mla-forecast-facts">
				<div class="mla-forecast-fact"><span>Lead</span><strong>+${esc(current)} h</strong></div>
				<div class="mla-forecast-fact"><span>Members</span><strong>${esc(`${payload.members.available}/${payload.members.expected}`)}</strong></div>
				<div class="mla-forecast-fact"><span>Systems</span><strong>${esc((payload.systems || []).length)}</strong></div>
				<div class="mla-forecast-fact"><span>Current mean centre</span><strong>${marker ? `${Number(marker[2]).toFixed(1)}°N, ${Number(marker[1]).toFixed(1)}°E` : 'not active'}</strong></div>
				<div class="mla-forecast-fact"><span>Peak guidance</span><strong>${peakCategory == null ? 'select a system' : esc(CLASS_LABELS[peakCategory] || `Class ${peakCategory}`)}</strong></div>
				<div class="mla-forecast-fact"><span>Publication gate</span><strong>${gateKinds.length ? esc(gateKinds.join(', ').replaceAll('-', ' ')) : 'select a system'}</strong></div>
			</div>
			${system ? `<p><strong>${esc(system.member_count)}</strong> member${system.member_count === 1 ? '' : 's'} · active +${esc(system.start_step)} to +${esc(system.end_step)} h.</p>` : (payload.systems || []).length ? '<p>Select a forecast system on the map or from the menu for its centre and intensity.</p>' : '<p>No track passed the provisional forecast support gate in this cycle.</p>'}
			${verification ? `<h4>ERA5 verification</h4><p>${esc(verification.status.replaceAll('_', ' '))}${verification.tracks && verification.tracks.length ? ` · ${esc(verification.tracks.map(track => track.label).join(', '))}` : ` · catalogue coverage ends ${esc(String(verification.coverage_end || '').slice(0, 10))}`}.</p>` : ''}
			<div class="mla-forecast-track-legend"><span><i></i>${payload.model.kind === 'ensemble' ? 'Member-mean forecast path' : 'Deterministic forecast path'}</span>${payload.model.kind === 'ensemble' ? '<span><i data-kind="member"></i>Individual member</span>' : ''}${verification && verification.tracks && verification.tracks.length ? '<span><i data-kind="era5"></i>Hourly ERA5 v5.6 track</span>' : ''}</div>
			<h4>Source and status</h4><p><a href="${esc(payload.source.url)}" target="_blank" rel="noopener">${esc(payload.source.service)}</a> · ${esc(payload.source.licence)} · ${esc(payload.qa.status)} QA.</p><p>${esc(boundary)}. Forecast classes are provisional.</p>${modelStatusHtml()}`;
	}

	async function render() {
		drawBase();
		$('#mlaForecastWeatherKey').hidden = true;
		await drawWeather();
		drawTracks();
		updateTimeLabel();
		renderDossier();
		const count = state.payload ? (state.payload.tracks || []).length : 0;
		const systems = state.payload ? (state.payload.systems || []).length : 0;
		$('#mlaForecastMapStatus').textContent = state.payload
			? `${systems} forecast system${systems === 1 ? '' : 's'} · ${count} member track${count === 1 ? '' : 's'} · ${state.mode === 'archive' ? 'ERA5 verification where available' : (state.payload.weather ? state.payload.weather.basis : 'weather unavailable')}`
			: 'Forecast data not loaded.';
	}

	function resizeAndRender() {
		if (!panel.hidden) render();
	}

	function selectSystemAt(event) {
		if (!state.payload) return;
		const canvas = $('#mlaForecastTracks');
		const rectangle = canvas.getBoundingClientRect();
		const map = projection(rectangle.width, rectangle.height);
		const current = Number(state.payload.steps[state.leadIndex] || 0);
		let best = null;
		for (const system of state.payload.systems || []) {
			const point = pointAt(meanTrack(system), current);
			if (!point) continue;
			const xy = map.project(point[2], point[1]);
			const distance = Math.hypot(event.clientX - rectangle.left - xy[0], event.clientY - rectangle.top - xy[1]);
			if (!best || distance < best.distance) best = {system, distance};
		}
		if (best && best.distance <= 18) {
			state.system = best.system.id;
			$('#mlaForecastSystem').value = state.system;
			render();
		}
	}

	$('#mlaForecastModeLatest').addEventListener('click', () => setMode('latest'));
	$('#mlaForecastModeArchive').addEventListener('click', () => setMode('archive'));
	$('#mlaForecastRetry').addEventListener('click', () => initialise(true));
	$('#mlaForecastModel').addEventListener('change', event => loadLatest(event.target.value));
	$('#mlaForecastSystem').addEventListener('change', event => { state.system = event.target.value; render(); });
	$('#mlaForecastWeather').addEventListener('change', async event => { state.weather = event.target.value; await render(); });
	$('#mlaForecastMembers').addEventListener('change', event => { state.showMembers = event.target.checked; drawTracks(); renderDossier(); });
	$('#mlaForecastLead').addEventListener('input', event => { state.leadIndex = Number(event.target.value); render(); });
	$('#mlaForecastPrevious').addEventListener('click', () => { state.leadIndex = Math.max(0, state.leadIndex - 1); $('#mlaForecastLead').value = state.leadIndex; render(); });
	$('#mlaForecastNext').addEventListener('click', () => { if (!state.payload) return; state.leadIndex = Math.min(state.payload.steps.length - 1, state.leadIndex + 1); $('#mlaForecastLead').value = state.leadIndex; render(); });
	$('#mlaForecastArchiveModel').addEventListener('change', event => { state.archiveModel = event.target.value; populateArchive(true); });
	$('#mlaForecastArchiveSearch').addEventListener('input', () => {
		clearTimeout(state.archiveSearchTimer);
		state.archiveSearchTimer = setTimeout(() => populateArchive(true), 250);
	});
	$('#mlaForecastArchiveCase').addEventListener('change', event => {
		const entry = (state.manifest.archive || []).find(item => `${item.model}:${item.cycle}` === event.target.value);
		if (entry) loadArchive(entry);
	});
	$('#mlaForecastTracks').addEventListener('click', selectSystemAt);
	window.addEventListener('mla:forecast-visible', () => initialise());
	window.addEventListener('resize', () => { clearTimeout(state.resizeTimer); state.resizeTimer = setTimeout(resizeAndRender, 120); });

	const parameters = new URLSearchParams(location.search);
	if (parameters.get('fmode') === 'archive') setMode('archive');
})();
