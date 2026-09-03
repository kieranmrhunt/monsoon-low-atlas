(function () {
	'use strict';

	const root = document.getElementById('monsoon-low-atlas');
	const panel = document.getElementById('mlaPanelForecast');
	if (!root || !panel) return;
	const $ = selector => root.querySelector(selector);
	const config = JSON.parse(document.getElementById('mla-data-config').textContent || '{}');
	const DOMAIN = {west: 45, east: 120, south: -15, north: 45};
	const HOUR_MS = 3600000;
	const DEFAULT_MAP = {zoom: 1.45, longitude: 82, latitude: 20};
	const DEFAULT_ARCHIVE_DATE = '2016-07-01';
	const PREFERENCES_KEY = 'monsoon-low-atlas.forecast.v2';
	const MODEL_TRACK_COLOURS = {
		gfs: '#d7191c', gefs: '#f07c00', ifs: '#2166ac', 'ifs-ens': '#00a6ca',
		aigfs: '#7b2cbf', aigefs: '#d45087',
		'graphcast-noaa': '#1b9e77', 'graphcast-ifs-noaa': '#00796b',
		aifs: '#5e3c99', 'aifs-ens': '#b358c8', 'ukmo-global': '#8c510a', 'mogreps-g': '#4d4d4d',
		'gefs-control': '#e66101', 'tigge-ecmwf': '#4575b4',
		'tigge-bom': '#b8860b', 'tigge-cma': '#00a8a8', 'tigge-cptec': '#a65628',
		'tigge-dwd': '#4daf4a', 'tigge-eccc': '#6a3d9a', 'tigge-imd': '#e7298a',
		'tigge-jma': '#984ea3', 'tigge-kma': '#238b45', 'tigge-mf': '#9a8700',
		'tigge-ncep': '#e41a1c', 'tigge-ncmrwf': '#ff7f00', 'tigge-ukmo': '#795548'
	};
	const ANALYSIS_TRACKS = Object.freeze({
		era5: {label: 'ERA5', colour: '#000000', detail: 'v5.6 track'},
		merra2: {label: 'MERRA-2', colour: '#c51b7d', detail: 'all active systems'},
		imdaa: {label: 'IMDAA', colour: '#008c95', detail: 'all active systems'},
		jra55: {label: 'JRA-55', colour: '#e66101', detail: 'all active systems'},
		erainterim: {label: 'ERA-Interim', colour: '#5e3c99', detail: 'all active systems'}
	});
	const ALTERNATIVE_ANALYSIS_KEYS = Object.freeze(Object.keys(ANALYSIS_TRACKS).filter(source => source !== 'era5'));
	const OPERATIONAL_MODEL_ORDER = [
		'gfs', 'gefs', 'ifs', 'ifs-ens', 'aifs', 'aifs-ens', 'aigfs', 'aigefs',
		'graphcast-noaa', 'graphcast-ifs-noaa', 'mogreps-g'
	];
	let storedPreferences = {};
	try {
		storedPreferences = JSON.parse(localStorage.getItem(PREFERENCES_KEY) || '{}');
		if (!storedPreferences || typeof storedPreferences !== 'object') storedPreferences = {};
	} catch (_) {
		storedPreferences = {};
	}
	const storedModels = Array.isArray(storedPreferences.selectedModels)
		? storedPreferences.selectedModels.filter(value => typeof value === 'string')
		: null;
	const storedAnalyses = Array.isArray(storedPreferences.analysisSources)
		? storedPreferences.analysisSources.filter(value => Object.hasOwn(ANALYSIS_TRACKS, value))
		: storedPreferences.showEra5 === false ? [] : ['era5'];
	const state = {
		mode: storedPreferences.mode === 'archive' ? 'archive' : 'latest', manifest: null, payload: null, geo: null, boundary: null,
		selectedModels: new Set(storedModels || []), hasModelPreference: storedModels !== null, latestPayloads: new Map(), modelLoads: new Map(),
		fullPayloads: new Map(), fullLoads: new Map(), fullFailures: new Set(), archiveEntriesCache: null,
		archiveColourIndexes: new Map(), archiveManifestLoaded: false, archiveManifestLoad: null,
		systemGroupsCache: null, systemGroupsCacheKey: '',
		selectedSystem: null, initialization: typeof storedPreferences.initialization === 'string' ? storedPreferences.initialization : 'latest',
		archiveDate: /^\d{4}-\d{2}-\d{2}$/.test(storedPreferences.archiveDate || '') ? storedPreferences.archiveDate : DEFAULT_ARCHIVE_DATE,
		archiveHour: ['00', '06', '12', '18'].includes(storedPreferences.archiveHour) ? storedPreferences.archiveHour : '00', archiveMonth: '', archiveEntry: null,
		archiveSelected: new Set(), archivePayloads: new Map(), archiveLoads: new Map(),
		leadIndex: 0, timelineTimes: [], weather: 'none', weatherModel: '', showMembers: Boolean(storedPreferences.showMembers), analysisSources: new Set(storedAnalyses),
		mapZoom: DEFAULT_MAP.zoom, mapCenterLon: DEFAULT_MAP.longitude,
		mapCenterLat: DEFAULT_MAP.latitude,
		initialised: false, loading: false, weatherCache: new Map(), loadSerial: 0, atlasContextTrack: null,
		renderSerial: 0, archiveSearchTimer: 0, archiveAvailability: null
	};
	const meanTrackCaches = new WeakMap();
	const systemTimelineCaches = new WeakMap();
	const evolutionSeriesCaches = new WeakMap();
	const analysisCentreCaches = new WeakMap();
	const analysisHistoryCaches = new WeakMap();
	let reanalysisManifestPromise = null;
	let reanalysisManifest = null;
	const nativeReanalysisAssets = new Map();
	const nativeReanalysisLoads = new Map();

	function persistPreferences() {
		try {
			localStorage.setItem(PREFERENCES_KEY, JSON.stringify({
				mode: state.mode,
				selectedModels: [...state.selectedModels],
				initialization: state.initialization,
				archiveDate: state.archiveDate,
				archiveHour: state.archiveHour,
				showMembers: state.showMembers,
				analysisSources: [...state.analysisSources]
			}));
		} catch (_) {
			// Browsers with blocked storage still retain the same choices for this page view.
		}
	}

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

	async function fetchGzipJson(url, cache = 'force-cache') {
		return gunzipJson(await fetch(url, {cache}));
	}

	function reanalysisBase() {
		return String(config.reanalysisBase || '').replace(/\/$/, '');
	}

	async function ensureReanalysisManifest() {
		if (reanalysisManifestPromise) return reanalysisManifestPromise;
		const base = reanalysisBase();
		if (!base) throw new Error('Matched reanalysis tracks are not configured');
		reanalysisManifestPromise = (async () => {
			const response = await fetch(`${base}/manifest.json`, {cache: 'no-store'});
			if (!response.ok) throw new Error(`HTTP ${response.status} for the reanalysis inventory`);
			const value = await response.json();
			if (value.schema !== 'lps-atlas-reanalysis-manifest-v1' || !value.sources) throw new Error('Unsupported reanalysis inventory');
			reanalysisManifest = value;
			return value;
		})().catch(error => { reanalysisManifestPromise = null; throw error; });
		return reanalysisManifestPromise;
	}

	function reanalysisMonth(timeMs) {
		return Number.isFinite(timeMs) ? new Date(timeMs).toISOString().slice(0, 7).replace('-', '') : '';
	}

	function nativeReanalysisAvailable(source, timeMs) {
		const definition = reanalysisManifest && reanalysisManifest.sources[source];
		const native = definition && definition.native_tracks;
		const month = reanalysisMonth(timeMs);
		return Boolean(definition && definition.status === 'ready' && native && month >= native.start_month && month <= native.end_month);
	}

	function indexNativeReanalysisAsset(source, month, value) {
		if (
			value.schema !== 'lps-atlas-reanalysis-native-month-v1'
			|| String(value.source).toLowerCase() !== source
			|| value.month !== month
			|| !value.tracks
		) throw new Error(`Incompatible ${ANALYSIS_TRACKS[source].label} source-native track asset`);
		value.tracksByDay = new Map();
		for (const [trackId, points] of Object.entries(value.tracks)) {
			const days = new Set((points || []).map(point => new Date(Number(point[0]) * HOUR_MS).toISOString().slice(0, 10)));
			for (const day of days) {
				if (!value.tracksByDay.has(day)) value.tracksByDay.set(day, []);
				value.tracksByDay.get(day).push({id: trackId, points});
			}
		}
		return value;
	}

	async function ensureNativeReanalysisMonth(source, timeMs) {
		const month = reanalysisMonth(timeMs);
		const key = `${source}:${month}`;
		if (nativeReanalysisAssets.has(key)) return nativeReanalysisAssets.get(key);
		if (nativeReanalysisLoads.has(key)) return nativeReanalysisLoads.get(key);
		const promise = (async () => {
			const manifest = await ensureReanalysisManifest();
			const definition = manifest.sources[source];
			const native = definition && definition.native_tracks;
			if (!definition || definition.status !== 'ready' || !native || month < native.start_month || month > native.end_month) return null;
			const relative = String(native.url_template || '').replace('{month}', month);
			if (!relative || relative.includes('{month}')) throw new Error(`${ANALYSIS_TRACKS[source].label} source-native track URL is invalid`);
			const url = /^https?:\/\//.test(relative) ? relative : `${reanalysisBase()}/${relative.replace(/^\//, '')}`;
			const asset = indexNativeReanalysisAsset(source, month, await fetchGzipJson(url, 'no-store'));
			nativeReanalysisAssets.set(key, asset);
			return asset;
		})();
		nativeReanalysisLoads.set(key, promise);
		try { return await promise; }
		finally { nativeReanalysisLoads.delete(key); }
	}

	function nativeReanalysisTracksOnDay(source, timeMs) {
		const asset = nativeReanalysisAssets.get(`${source}:${reanalysisMonth(timeMs)}`);
		if (!asset || !asset.tracksByDay) return [];
		return asset.tracksByDay.get(new Date(timeMs).toISOString().slice(0, 10)) || [];
	}

	async function ensureArchiveNativeReanalyses() {
		if (state.mode === 'latest') return;
		const target = currentValidTime() == null ? archiveTargetTime() : currentValidTime();
		if (!Number.isFinite(target)) return;
		await Promise.allSettled(ALTERNATIVE_ANALYSIS_KEYS
			.filter(source => state.analysisSources.has(source) && nativeReanalysisAvailable(source, target))
			.map(source => ensureNativeReanalysisMonth(source, target)));
	}

	async function initialiseReanalysisTracks() {
		try {
			await ensureReanalysisManifest();
			if (state.mode === 'archive') configureTimeline(Boolean(displayEntries().length), archiveTargetTime());
			await ensureArchiveNativeReanalyses();
			if (state.mode === 'archive') {
				populateArchiveTimeControls();
				populateArchive(false);
			}
			render();
		} catch (error) {
			console.warn('Matched reanalysis tracks unavailable', error);
		}
	}

	function firstSustainedTrackCoalescence(first, second) {
		const secondByStep = new Map((second.points || []).map(point => [Number(point[0]), point]));
		let runStart = null;
		let previousStep = null;
		for (const point of [...(first.points || [])].sort((a, b) => Number(a[0]) - Number(b[0]))) {
			const step = Number(point[0]);
			const other = secondByStep.get(step);
			if (!other) continue;
			const close = haversineKm(point[1], point[2], other[1], other[2]) <= 75;
			if (!close) runStart = null;
			else {
				if (runStart == null || previousStep == null || step !== previousStep + 1) runStart = step;
				if (step - runStart + 1 >= 6) return runStart;
			}
			previousStep = step;
		}
		return null;
	}

	function forecastTrackRank(track) {
		const points = track.points || [];
		const start = points.length ? Math.min(...points.map(point => Number(point[0]))) : Infinity;
		const observed = points.filter(point => String(point[point.length - 1]).toLowerCase() === 'o').length;
		return [start, -observed, String(track.id)];
	}

	function rankBefore(first, second) {
		for (let index = 0; index < first.length; index++) {
			if (first[index] === second[index]) continue;
			return first[index] < second[index];
		}
		return true;
	}

	function preferredCoalescedPoint(candidate, incumbent, keeperId) {
		if (!incumbent) return true;
		const candidateObserved = String(candidate.point[candidate.point.length - 1]).toLowerCase() === 'o';
		const incumbentObserved = String(incumbent.point[incumbent.point.length - 1]).toLowerCase() === 'o';
		if (candidateObserved !== incumbentObserved) return candidateObserved;
		const candidateScore = Number(candidate.point[3]);
		const incumbentScore = Number(incumbent.point[3]);
		if (Number.isFinite(candidateScore) && Number.isFinite(incumbentScore) && candidateScore !== incumbentScore) return candidateScore > incumbentScore;
		return candidate.trackId === keeperId && incumbent.trackId !== keeperId;
	}

	function refreshForecastTrack(track) {
		track.points = [...(track.points || [])].sort((a, b) => Number(a[0]) - Number(b[0]));
		if (!track.points.length) return track;
		track.start_step = Number(track.points[0][0]);
		track.end_step = Number(track.points[track.points.length - 1][0]);
		const vorticity = track.points.map(point => Number(point[3])).filter(Number.isFinite);
		const pressureDeficit = track.points.map(point => Number(point[4])).filter(Number.isFinite);
		const mslp = track.points.map(point => Number(point[5])).filter(Number.isFinite);
		const category = track.points.map(point => Number(point[6])).filter(Number.isFinite);
		if (vorticity.length) track.max_vorticity = Math.max(...vorticity);
		if (pressureDeficit.length) track.max_pressure_deficit = Math.max(...pressureDeficit);
		if (mslp.length) track.minimum_mslp = Math.min(...mslp);
		if (category.length) track.maximum_provisional_category = Math.max(...category);
		track.observed_support_hours = track.points.filter(point => String(point[point.length - 1]).toLowerCase() === 'o').length;
		return track;
	}

	function coalesceLegacyForecastPayload(payload) {
		if (!payload || !Array.isArray(payload.tracks) || payload._coalescenceChecked) return payload;
		Object.defineProperty(payload, '_coalescenceChecked', {value: true});
		let tracks = payload.tracks.map(track => ({...track, points: [...(track.points || [])]}));
		const audit = [];
		while (true) {
			const byMember = new Map();
			for (const track of tracks) {
				const member = String(track.member || 'det');
				if (!byMember.has(member)) byMember.set(member, []);
				byMember.get(member).push(track);
			}
			let match = null;
			for (const [member, values] of byMember) {
				for (let firstIndex = 0; firstIndex < values.length && !match; firstIndex++) {
					for (let secondIndex = firstIndex + 1; secondIndex < values.length; secondIndex++) {
						const mergeStep = firstSustainedTrackCoalescence(values[firstIndex], values[secondIndex]);
						if (mergeStep != null) { match = {member, first: values[firstIndex], second: values[secondIndex], mergeStep}; break; }
					}
				}
				if (match) break;
			}
			if (!match) break;
			const firstRank = forecastTrackRank(match.first);
			const secondRank = forecastTrackRank(match.second);
			const keeper = rankBefore(firstRank, secondRank) ? match.first : match.second;
			const terminated = keeper === match.first ? match.second : match.first;
			const tailByStep = new Map();
			for (const track of [keeper, terminated]) for (const point of track.points || []) {
				if (Number(point[0]) < match.mergeStep) continue;
				const candidate = {point, trackId: track.id};
				const step = Number(point[0]);
				if (preferredCoalescedPoint(candidate, tailByStep.get(step), keeper.id)) tailByStep.set(step, candidate);
			}
			keeper.points = [
				...(keeper.points || []).filter(point => Number(point[0]) < match.mergeStep),
				...[...tailByStep.entries()].sort((a, b) => a[0] - b[0]).map(([, value]) => value.point)
			];
			terminated.points = (terminated.points || []).filter(point => Number(point[0]) < match.mergeStep);
			refreshForecastTrack(keeper);
			refreshForecastTrack(terminated);
			if (!terminated.points.length) tracks = tracks.filter(track => track !== terminated);
			audit.push({member: match.member, kept: String(keeper.id), terminated: String(terminated.id), mergeStep: match.mergeStep});
		}
		payload.tracks = tracks;
		if (audit.length && Array.isArray(payload.systems)) {
			const tracksById = new Map(tracks.map(track => [String(track.id), track]));
			payload.systems = payload.systems.map(system => {
				const systemTracks = (system.track_ids || []).map(id => tracksById.get(String(id))).filter(Boolean);
				if (!systemTracks.length) return null;
				const members = [...new Set(systemTracks.map(track => String(track.member || 'det')))];
				return {
					...system,
					track_ids: systemTracks.map(track => track.id),
					members,
					member_count: members.length,
					start_step: Math.min(...systemTracks.map(track => Number(track.start_step))),
					end_step: Math.max(...systemTracks.map(track => Number(track.end_step)))
				};
			}).filter(Boolean);
		}
		return payload;
	}

	async function fetchEntryPayload(entry, tracksOnly) {
		const preferred = tracksOnly && entry.tracks_url ? entry.tracks_url : entry.url;
		try {
			return coalesceLegacyForecastPayload(await fetchGzipJson(joinUrl(config.forecastBase, preferred)));
		} catch (error) {
			if (preferred === entry.url) throw error;
			console.warn(`Track sidecar unavailable for ${preferred}; using full payload`, error);
			return coalesceLegacyForecastPayload(await fetchGzipJson(joinUrl(config.forecastBase, entry.url)));
		}
	}

	async function fetchManifest() {
		let lastError = new Error('Forecast manifest is unavailable');
		for (let attempt = 0; attempt < 3; attempt += 1) {
			try {
				const suffix = `?v=${Date.now()}-${attempt}`;
				let response = await fetch(`${joinUrl(config.forecastBase, 'latest-manifest.json.gz')}${suffix}`, {cache: 'no-store'});
				let value;
				if (response.ok) value = await gunzipJson(response);
				else {
					response = await fetch(`${joinUrl(config.forecastBase, 'manifest.json')}${suffix}`, {cache: 'no-store'});
					if (!response.ok) throw new Error(`Forecast manifest returned HTTP ${response.status}`);
					value = await response.json();
				}
				if (value.schema !== 'mla-forecast-manifest-v1') throw new Error('Unsupported forecast manifest');
				return value;
			} catch (error) {
				lastError = error;
				if (attempt < 2) await new Promise(resolve => setTimeout(resolve, 300 * (attempt + 1)));
			}
		}
		throw lastError;
	}

	async function ensureArchiveManifest() {
		if (state.archiveManifestLoaded) return state.manifest;
		if (state.archiveManifestLoad) return state.archiveManifestLoad;
		state.archiveManifestLoad = (async () => {
			let response = await fetch(`${joinUrl(config.forecastBase, 'archive-manifest.json.gz')}?v=${Date.now()}`, {cache: 'no-store'});
			let value;
			if (response.ok) value = await gunzipJson(response);
			else {
				response = await fetch(`${joinUrl(config.forecastBase, 'manifest.json')}?v=${Date.now()}`, {cache: 'no-store'});
				if (!response.ok) throw new Error(`Archive manifest returned HTTP ${response.status}`);
				value = await response.json();
			}
			if (value.schema !== 'mla-forecast-manifest-v1') throw new Error('Unsupported archive manifest');
			state.manifest = {...state.manifest, ...value};
			state.archiveManifestLoaded = true;
			state.archiveEntriesCache = null;
			state.archiveAvailability = null;
			return state.manifest;
		})().finally(() => { state.archiveManifestLoad = null; });
		return state.archiveManifestLoad;
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

	function colourChannels(value) {
		const match = String(value).match(/^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i);
		return match ? match.slice(1).map(channel => parseInt(channel, 16)) : [0, 87, 184];
	}

	function mixColour(first, second, amount) {
		const a = colourChannels(first), b = colourChannels(second);
		const weight = clamp(Number(amount) || 0, 0, 1);
		return `#${a.map((channel, index) => Math.round(channel * (1 - weight) + b[index] * weight).toString(16).padStart(2, '0')).join('')}`;
	}

	function modelLeadColour(id, fallback, lead, maximumLead) {
		const base = modelTrackColour(id, fallback);
		const fraction = maximumLead > 0 ? clamp(Number(lead) / Number(maximumLead), 0, 1) : 0;
		// Model is encoded by hue; increasing lead is encoded by a restrained lightness ramp.
		return mixColour(base, '#ffffff', .06 + .38 * fraction);
	}

	function setShowMembers(value) {
		state.showMembers = Boolean(value);
		$('#mlaForecastMembers').checked = state.showMembers;
		$('#mlaForecastArchiveMembers').checked = state.showMembers;
		persistPreferences();
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
		for (const id of ['ifs', 'aifs', 'aigfs', 'gfs', 'ifs-ens', 'aifs-ens', 'aigefs', 'gefs']) if (available.includes(id)) return id;
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
		return [...state.selectedModels]
			.map(modelId => ({model: definitions.get(modelId) || modelDefinition(modelId), entry: activeEntry(modelId)}))
			.filter(item => item.entry);
	}

	function populateInitializationControls() {
		const select = $('#mlaForecastInitialization');
		if (!select || !state.manifest) return;
		const selected = [...state.selectedModels];
		const cycles = new Map();
		for (const modelId of selected) for (const entry of [
			...(((state.manifest.recent || {})[modelId]) || []),
			(state.manifest.latest || {})[modelId]
		].filter(Boolean)) {
			const key = String(entry.cycle || '');
			if (!key) continue;
			if (!cycles.has(key)) cycles.set(key, {utc: entry.cycle_utc, models: new Set()});
			cycles.get(key).models.add(modelId);
		}
		const options = [...cycles.entries()].sort((a, b) => String(b[0]).localeCompare(String(a[0])));
		if (state.initialization !== 'latest' && !cycles.has(String(state.initialization))) state.initialization = 'latest';
		select.innerHTML = selected.length
			? '<option value="latest">Latest available for each model</option>' + options.map(([cycle, item]) => `<option value="${esc(cycle)}">${esc(formatUtc(item.utc))} · ${item.models.size}/${selected.length} models</option>`).join('')
			: '<option value="latest">Select a model to see cycles</option>';
		select.value = state.initialization;
		select.disabled = !selected.length;
		persistPreferences();
	}

	function buildModelControls() {
		const latest = state.manifest.latest || {};
		const definitions = new Map((state.manifest.models || []).map(model => [model.id, model]));
		const models = OPERATIONAL_MODEL_ORDER.map(id => definitions.get(id) || (
			id === 'mogreps-g'
				? {id, label: 'MOGREPS-G', centre: 'Met Office', kind: 'ensemble', colour: MODEL_TRACK_COLOURS[id]}
				: {id, label: id.toUpperCase(), kind: ['gfs', 'aigfs', 'graphcast-noaa', 'graphcast-ifs-noaa', 'ifs', 'aifs'].includes(id) ? 'deterministic' : 'ensemble', colour: MODEL_TRACK_COLOURS[id]}
		));
		if (!state.hasModelPreference) {
			for (const model of models) if (model.kind === 'deterministic' && latest[model.id]) state.selectedModels.add(model.id);
			if (!state.selectedModels.size && preferredModel()) state.selectedModels.add(preferredModel());
			state.hasModelPreference = true;
		}
		const modelChoice = model => {
			const entry = latest[model.id];
			const checked = state.selectedModels.has(model.id);
			const attempt = (state.manifest.attempts || {})[model.id];
			const unavailable = attempt && attempt.message ? ` · Last attempt: ${attempt.message}` : '';
			const title = entry ? `${model.label} initialized ${formatUtc(entry.cycle_utc)}` : `${model.label} unavailable${unavailable}`;
			return `<label class="mla-forecast-model-choice" style="--model-colour:${esc(modelTrackColour(model.id, model.colour))}" title="${esc(title)}"><input type="checkbox" value="${esc(model.id)}" ${checked ? 'checked' : ''} ${entry ? '' : 'disabled'}><i aria-hidden="true"></i><span>${esc(model.label)}</span>${entry ? '' : '<em>Unavailable</em>'}</label>`;
		};
		const grouped = [
			['Deterministic', models.filter(model => model.kind !== 'ensemble')],
			['Ensemble', models.filter(model => model.kind === 'ensemble')]
		];
		$('#mlaForecastModelChecks').innerHTML = grouped.map(([label, values]) => `<section class="mla-forecast-model-group" aria-label="${esc(label)} models"><strong>${esc(label)}</strong><div>${values.map(modelChoice).join('')}</div></section>`).join('');
		setShowMembers(state.showMembers);
		if (state.archiveManifestLoaded) populateArchiveTimeControls();
		populateInitializationControls();
		populateWeatherModels();
		persistPreferences();
	}

	function archiveEntries() {
		if (!state.manifest) return [];
		if (state.archiveEntriesCache) return state.archiveEntriesCache;
		const entries = [...(state.manifest.archive || []), ...(state.manifest.tigge_archive || [])];
		const unique = new Map();
		for (const entry of entries) {
			const key = `${entry.model}:${entry.cycle}`;
			if (!unique.has(key)) unique.set(key, entry);
		}
		state.archiveEntriesCache = [...unique.values()];
		return state.archiveEntriesCache;
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
		const monthModels = new Set();
		for (let day = 1; day <= days; day += 1) {
			const date = `${state.archiveMonth}-${String(day).padStart(2, '0')}`;
			for (const slot of (availability.get(date) || new Map()).values()) for (const model of slot.models) monthModels.add(model);
		}
		const legend = $('#mlaForecastCalendarLegend');
		if (legend) legend.innerHTML = archiveModelOrder(monthModels).map(modelId => {
			const model = modelDefinition(modelId);
			return `<span><i style="--calendar-model-colour:${esc(modelTrackColour(modelId, model.colour))}" aria-hidden="true"></i>${esc(model.label)}</span>`;
		}).join('');
		const cells = Array.from({length: leading}, () => '<span class="mla-forecast-calendar-empty"></span>');
		for (let day = 1; day <= days; day += 1) {
			const date = `${state.archiveMonth}-${String(day).padStart(2, '0')}`;
			const slots = availability.get(date) || new Map();
			const available = slots.size > 0;
			const dayModels = new Set();
			for (const slot of slots.values()) for (const model of slot.models) dayModels.add(model);
			const orderedModels = archiveModelOrder(dayModels);
			const details = orderedModels.length
				? orderedModels.map(modelId => modelDefinition(modelId).label).join(', ')
				: 'No processed models';
			const bars = orderedModels.map(modelId => {
				const model = modelDefinition(modelId);
				return `<i style="--calendar-model-colour:${esc(modelTrackColour(modelId, model.colour))}" title="${esc(model.label)}" aria-hidden="true"></i>`;
			}).join('');
			cells.push(`<button class="mla-forecast-calendar-day" type="button" data-forecast-calendar-date="${esc(date)}" aria-pressed="${state.archiveDate === date}" title="${esc(details)}" ${available ? '' : 'disabled'}><strong>${day}</strong><span class="mla-forecast-calendar-models">${bars}</span></button>`);
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
		persistPreferences();
	}

	function applyAtlasArchiveContext(detail) {
		const time = Number(detail && detail.time);
		if (!Number.isFinite(time)) return false;
		const date = new Date(time);
		const target = Date.UTC(
			date.getUTCFullYear(),
			date.getUTCMonth(),
			date.getUTCDate(),
			Math.floor(date.getUTCHours() / 6) * 6
		);
		setArchiveTarget(target);
		if (detail.analysis === 'compare') state.analysisSources = new Set(Object.keys(ANALYSIS_TRACKS));
		else if (Object.hasOwn(ANALYSIS_TRACKS, detail.analysis)) state.analysisSources = new Set(['era5', detail.analysis]);
		else state.analysisSources.add('era5');
		state.atlasContextTrack = Array.isArray(detail.era5Track) && detail.era5Track.length
			? {id: String(detail.system || ''), points: detail.era5Track}
			: null;
		if (typeof detail.query === 'string') $('#mlaForecastArchiveSearch').value = detail.query;
		state.mode = 'archive';
		state.archiveEntry = null;
		state.archiveSelected.clear();
		state.selectedSystem = null;
		persistPreferences();
		syncModeControls();
		return true;
	}

	function archiveTargetTime() {
		if (!state.archiveDate) return null;
		return Date.parse(`${state.archiveDate}T${state.archiveHour || '00'}:00:00Z`);
	}

	function atlasContextTrackActive(timeMs) {
		const points = state.atlasContextTrack && state.atlasContextTrack.points;
		if (!points || !points.length || !Number.isFinite(timeMs)) return false;
		return timeMs >= Number(points[0][0]) * HOUR_MS && timeMs <= Number(points[points.length - 1][0]) * HOUR_MS;
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
			persistPreferences();
		} else if (!state.archiveDate) {
			const matches = archiveNameEntries();
			const target = raw ? bestArchiveTarget(matches) : Math.max(...archiveEntries().map(entry => new Date(entry.cycle_utc).getTime()).filter(Number.isFinite));
			if (Number.isFinite(target)) setArchiveTarget(target);
		}
	}

	function populateArchiveTimeControls() {
		if (!state.manifest) return;
		const entries = archiveEntries();
		const starts = entries.map(entry => String(entry.valid_start_utc || entry.cycle_utc || '').slice(0, 10)).filter(Boolean);
		const ends = entries.map(entry => String(entry.valid_end_utc || '').slice(0, 10)).filter(Boolean);
		if (reanalysisManifest && reanalysisManifest.sources) for (const definition of Object.values(reanalysisManifest.sources)) {
			if (definition && definition.status === 'ready' && definition.native_tracks) {
				if (definition.coverage_start_utc) starts.push(String(definition.coverage_start_utc).slice(0, 10));
				if (definition.coverage_end_utc) ends.push(String(definition.coverage_end_utc).slice(0, 10));
			}
		}
		starts.sort(); ends.sort();
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
			const payload = state.fullPayloads.get(runKey) || state.archivePayloads.get(runKey);
			if (!payload) return null;
			const entry = archiveEntries().find(item => payloadKey(item.model, item) === runKey) || null;
			return {model: modelDefinition(payload.model.id), payload, entry, runKey};
		}).filter(Boolean);
		return latestEntries().filter(item => item.payload).map(item => ({...item, payload: state.fullPayloads.get(item.runKey) || item.payload}));
	}

	function weatherFields(item) {
		const embedded = Object.entries((item.payload && item.payload.weather) || {})
			.filter(([, field]) => field && typeof field === 'object' && field.shape)
			.map(([name]) => name);
		return new Set([
			...embedded,
			...((item.payload && item.payload.weather_available) || []),
			...((item.entry && item.entry.weather_fields) || [])
		]);
	}

	function populateWeatherModels() {
		const entries = displayEntries().filter(item => {
			const fields = weatherFields(item);
			return fields.size && (state.weather === 'none' || fields.has(state.weather));
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
		const definitions = [...OPERATIONAL_MODEL_ORDER, ...(state.manifest.models || []).map(model => model.id).filter(id => !OPERATIONAL_MODEL_ORDER.includes(id))];
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
		const maximumLead = Math.max(1, ...entries.map(entry => entryLeadAt(entry, target)).filter(Number.isFinite));
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
			const versions = [...new Set(values.map(entry => versionLabel(entry.model_version)).filter(Boolean))];
			const version = versions.length === 1 ? versions[0] : versions.length ? `${versions.length} versions at this valid time` : 'Version unavailable';
			const cells = values.map(entry => {
				const lead = entryLeadAt(entry, target);
				const key = `${entry.model}:${entry.cycle}`;
				const active = state.archiveSelected.has(key);
				const loading = state.archiveLoads.has(key);
				const cellColour = modelLeadColour(model.id, model.colour, lead, maximumLead);
				const initialization = shortArchiveInitialization(entry.cycle_utc);
				const names = (entry.verification_labels || []).join(', ');
				const title = [entry.model_label || entry.model, versionLabel(entry.model_version), `initialized ${formatUtc(entry.cycle_utc)}`, `valid ${formatUtc(target)}`, `lead +${lead} h`, names].filter(Boolean).join(' · ');
				return `<button class="mla-forecast-matrix-cell${loading ? ' is-loading' : ''}" type="button" style="--model-colour:${esc(cellColour)}" data-forecast-archive-run="${esc(key)}" aria-pressed="${active}" aria-label="${esc(title)}" title="${esc(title)}"><strong>+${esc(String(lead).padStart(3, '0'))} h</strong><small>${esc(initialization[0])}<br>${esc(initialization[1])} init</small></button>`;
			}).join('');
			return `<section class="mla-forecast-matrix-group" style="--model-colour:${esc(modelColour)}"><div class="mla-forecast-matrix-model" title="${esc(version)}"><i aria-hidden="true"></i><span><strong>${esc(model.label)}</strong><small>${esc(version)}</small></span></div><div class="mla-forecast-matrix-cells">${cells}</div></section>`;
		}).join('');
		const available = entries.length;
		const era5Available = atlasContextTrackActive(target) || entries.some(entry => entry.verification_status === 'matched' || (entry.verification_labels || []).length);
		const verificationIds = new Set();
		for (const entry of entries) {
			for (const value of entry.verification_track_ids || []) verificationIds.add(String(value));
			const payload = state.archivePayloads.get(payloadKey(entry.model, entry));
			for (const track of (payload && payload.verification ? payload.verification.tracks : []) || []) verificationIds.add(String(track.id));
		}
		const analysisTiles = Object.entries(ANALYSIS_TRACKS).map(([source, definition]) => {
			const sourceAvailable = source === 'era5' ? era5Available : nativeReanalysisAvailable(source, target);
			const availability = source === 'era5' ? 'No matched track' : reanalysisCoverageLabel(source);
			const title = sourceAvailable
				? source === 'era5' ? 'Show or hide matched ERA5 verification tracks' : `Show or hide all ${definition.label} tracks active on this UTC date`
				: `No ${definition.label} analysis is available on this date · ${availability}`;
			return `<button class="mla-forecast-era5-tile" type="button" style="--analysis-colour:${definition.colour}" data-forecast-analysis-source="${source}" aria-pressed="${state.analysisSources.has(source) && sourceAvailable}" title="${esc(title)}" ${sourceAvailable ? '' : 'disabled'}><strong>${esc(definition.label)}</strong><small>${sourceAvailable ? esc(definition.detail) : esc(availability)}</small></button>`;
		}).join('');
		const summary = available
			? `${available} model–lead pair${available === 1 ? '' : 's'} available · ${selected} selected`
			: 'No model–lead pairs available';
		return `<div class="mla-forecast-matrix-layout">
			<div class="mla-forecast-matrix-toolbar"><span><strong>${esc(formatUtc(target))}</strong><small>${esc(summary)}</small></span><button class="mla-btn mla-btn-small mla-btn-quiet" type="button" data-forecast-archive-clear ${selected ? '' : 'hidden'}>Clear</button></div>
			<div class="mla-forecast-matrix-intro"><p>Choose any model–lead squares; click a selected square again to remove it.</p><aside class="mla-forecast-analysis-choice"><span class="mla-label">Analysis</span><div class="mla-forecast-analysis-tiles">${analysisTiles}</div></aside></div>
			<div class="mla-forecast-matrix-groups">${groups || `<p class="mla-forecast-matrix-no-match">${esc(noArchiveMatchMessage())}</p>`}</div>
		</div>`;
	}

	function defaultArchiveEntry(entries) {
		const preferred = ['ifs', 'aifs', 'aigfs', 'graphcast-ifs-noaa', 'graphcast-noaa', 'gfs', 'ifs-ens', 'aifs-ens', 'aigefs', 'gefs', 'mogreps-g', 'ukmo-global', 'gefs-control', 'tigge-ecmwf'];
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
		const target = archiveTargetTime();
		const maximumLead = Math.max(1, ...entries.map(entry => entryLeadAt(entry, target)).filter(Number.isFinite));
		state.archiveColourIndexes = new Map();
		const colourGroups = new Map();
		for (const entry of entries) {
			if (!colourGroups.has(entry.model)) colourGroups.set(entry.model, []);
			colourGroups.get(entry.model).push(entry);
		}
		for (const [modelId, values] of colourGroups) {
			const model = modelDefinition(modelId);
			for (const entry of values) state.archiveColourIndexes.set(
				payloadKey(entry.model, entry),
				modelLeadColour(model.id, model.colour, entryLeadAt(entry, target), maximumLead)
			);
		}
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
		else if (!state.archiveSelected.size) configureTimeline(false, target);
		if (!entries.length) {
			notice(noArchiveMatchMessage(), 'flag', false);
			render();
		} else if (!state.archiveSelected.size && !selected) notice('', '', false);
		scheduleRender();
	}

	async function loadArchivePayload(entry) {
		const key = payloadKey(entry.model, entry);
		if (state.archivePayloads.has(key)) return state.archivePayloads.get(key);
		if (state.archiveLoads.has(key)) return state.archiveLoads.get(key);
		state.loading = true;
		notice(`Opening archived ${entry.model_label || entry.model} forecast…`, '', false);
		const promise = (async () => {
			const payload = await fetchEntryPayload(entry, true);
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
		const promise = fetchEntryPayload(entry, true).then(payload => {
			state.latestPayloads.set(key, payload);
			return payload;
		}).finally(() => state.modelLoads.delete(key));
		state.modelLoads.set(key, promise);
		return promise;
	}

	function entryForRunKey(runKey) {
		if (state.mode === 'latest') {
			const item = requestedLatestRuns().find(value => payloadKey(value.model.id, value.entry) === runKey);
			return item && item.entry;
		}
		return archiveEntries().find(entry => payloadKey(entry.model, entry) === runKey) || null;
	}

	async function ensureFullPayload(runKey) {
		if (!runKey) return null;
		if (state.fullPayloads.has(runKey)) return state.fullPayloads.get(runKey);
		if (state.fullLoads.has(runKey)) return state.fullLoads.get(runKey);
		const entry = entryForRunKey(runKey);
		if (!entry || !entry.url) return null;
		const current = state.latestPayloads.get(runKey) || state.archivePayloads.get(runKey);
		if (current && current.weather) {
			state.fullPayloads.set(runKey, current);
			return current;
		}
		const promise = fetchEntryPayload(entry, false).then(payload => {
			state.fullPayloads.set(runKey, payload);
			return payload;
		}).finally(() => state.fullLoads.delete(runKey));
		state.fullLoads.set(runKey, promise);
		return promise;
	}

	async function loadWeatherForSelection() {
		if (state.weather === 'none' || !state.weatherModel) return;
		try {
			await ensureFullPayload(state.weatherModel);
		} catch (error) {
			notice(`Weather fields could not be loaded: ${error.message || error}`, 'flag', true);
		}
		populateWeatherModels();
	}

	async function loadSelectedModels() {
		const selected = requestedLatestRuns();
		if (!selected.length) {
			state.selectedSystem = null;
			notice('', '', false);
			populateWeatherModels();
			configureTimeline(false);
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
		if (modelDefinition(entry.model).kind === 'ensemble') setShowMembers(true);
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

	function reanalysisCoverageLabel(source) {
		const definition = reanalysisManifest && reanalysisManifest.sources[source];
		if (!definition || definition.status !== 'ready') return 'Processing';
		const native = definition.native_tracks;
		if (!native || !native.start_month || !native.end_month) return 'No source-native tracks';
		const label = month => {
			const value = String(month);
			const time = Date.UTC(Number(value.slice(0, 4)), Number(value.slice(4, 6)) - 1, 1);
			return Number.isFinite(time) ? new Intl.DateTimeFormat('en-GB', {timeZone: 'UTC', month: 'short', year: 'numeric'}).format(time) : value;
		};
		return native.start_month === native.end_month ? `${label(native.start_month)} only` : `${label(native.start_month)}–${label(native.end_month)}`;
	}

	function analysisOnlyTimeline(target) {
		if (state.mode === 'latest' || !Number.isFinite(target)) return [];
		const available = (
			state.analysisSources.has('era5') && atlasContextTrackActive(target)
		) || ALTERNATIVE_ANALYSIS_KEYS.some(source => state.analysisSources.has(source) && nativeReanalysisAvailable(source, target));
		if (!available) return [];
		const day = new Date(target);
		const midnight = Date.UTC(day.getUTCFullYear(), day.getUTCMonth(), day.getUTCDate());
		return [0, 6, 12, 18].map(hour => midnight + hour * 3600000);
	}

	function configureTimeline(preserve, preferredTime) {
		const slider = $('#mlaForecastLead');
		const previous = Number.isFinite(preferredTime) ? preferredTime : preserve ? currentValidTime() : null;
		const times = new Set();
		for (const {payload} of displayEntries()) for (const value of payload.valid_times || []) {
			const stamp = new Date(value).getTime();
			if (Number.isFinite(stamp)) times.add(stamp);
		}
		if (!times.size) for (const stamp of analysisOnlyTimeline(archiveTargetTime())) times.add(stamp);
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
		if (!displayEntries().length) {
			$('#mlaForecastTime').textContent = `Analysis · ${formatUtc(valid)}`;
			return;
		}
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
		if (force) state.fullFailures.clear();
		state.loading = true;
		notice('Opening forecast manifest and map geography…', '', false);
		try {
			[state.manifest, state.geo, state.boundary] = await Promise.all([
				fetchManifest(), loadGeography(), loadBoundary()
			]);
			state.archiveManifestLoaded = Array.isArray(state.manifest.archive) || Array.isArray(state.manifest.tigge_archive);
			state.archiveEntriesCache = null;
			state.initialised = true;
			buildModelControls();
			void initialiseReanalysisTracks();
			if (state.mode !== 'latest') {
				await ensureArchiveManifest();
				populateArchiveTimeControls();
				renderAvailability();
				state.loading = false;
				populateArchive(true);
			} else {
				state.loading = false;
				loadSelectedModels();
			}
		} catch (error) {
			state.loading = false;
			notice(`Forecast service is unavailable: ${error.message || error}`, 'flag', true);
			render();
		}
	}

	function syncModeControls() {
		const mode = state.mode;
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
	}

	function setMode(mode) {
		if (mode === 'tigge') mode = 'archive';
		const changed = state.mode !== mode;
		state.mode = mode;
		syncModeControls();
		persistPreferences();
		if (!state.initialised) { if (!panel.hidden) initialise(); return; }
		if (!changed) { resizeAndRender(); return; }
		state.payload = null;
		state.selectedSystem = null;
		state.archiveEntry = null;
		state.archiveSelected.clear();
		$('#mlaForecastArchiveDate').value = state.archiveDate;
		$('#mlaForecastArchiveHour').value = state.archiveHour;
		render();
		if (mode !== 'latest') {
			state.weather = 'none';
			populateWeatherModels();
			state.loading = true;
			notice('Opening the processed archive index…', '', false);
			ensureArchiveManifest().then(() => {
				state.loading = false;
				if (state.mode === 'latest') return;
				populateArchiveTimeControls();
				renderAvailability();
				populateArchive(true);
			}).catch(error => {
				state.loading = false;
				if (state.mode === 'latest') return;
				notice(`Forecast archive is unavailable: ${error.message || error}`, 'flag', true);
			});
		} else {
			renderAvailability();
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
		if (!payload || !system) return [];
		let cache = meanTrackCaches.get(payload);
		if (!cache) { cache = new Map(); meanTrackCaches.set(payload, cache); }
		if (cache.has(system.id)) return cache.get(system.id);
		const tracks = tracksForSystem(payload, system);
		const byStep = new Map();
		for (const track of tracks) for (const point of track.points) {
			if (!byStep.has(point[0])) byStep.set(point[0], []);
			byStep.get(point[0]).push(point);
		}
		const minimum = Math.max(1, Math.ceil((system.member_count || tracks.length) * .2));
		const result = [...byStep.entries()].filter(([, points]) => points.length >= minimum).sort((a, b) => a[0] - b[0]).map(([step, points]) => [
			step,
			points.reduce((sum, point) => sum + Number(point[1]), 0) / points.length,
			points.reduce((sum, point) => sum + Number(point[2]), 0) / points.length,
			points.length
		]);
		cache.set(system.id, result);
		return result;
	}

	function haversineKm(longitudeA, latitudeA, longitudeB, latitudeB) {
		const radians = value => Number(value) * Math.PI / 180;
		const latitudeDelta = radians(latitudeB - latitudeA);
		const longitudeDelta = radians(longitudeB - longitudeA);
		const value = Math.sin(latitudeDelta / 2) ** 2 + Math.cos(radians(latitudeA)) * Math.cos(radians(latitudeB)) * Math.sin(longitudeDelta / 2) ** 2;
		return 6371.0088 * 2 * Math.atan2(Math.sqrt(value), Math.sqrt(Math.max(0, 1 - value)));
	}

	function systemItemKey(item) {
		return `${item.runKey}:${item.system.id}`;
	}

	function systemTimeline(item) {
		let cache = systemTimelineCaches.get(item.payload);
		if (!cache) { cache = new Map(); systemTimelineCaches.set(item.payload, cache); }
		if (cache.has(item.system.id)) return cache.get(item.system.id);
		const cycle = new Date(item.payload.cycle_utc).getTime();
		const points = meanTrack(item.payload, item.system).map(point => ({
			time: cycle + Number(point[0]) * 3600000,
			step: Number(point[0]), longitude: Number(point[1]), latitude: Number(point[2])
		}));
		const output = {points, byTime: new Map(points.map(point => [point.time, point]))};
		cache.set(item.system.id, output);
		return output;
	}

	function systemMatchScore(first, second) {
		const firstTimeline = systemTimeline(first), secondTimeline = systemTimeline(second);
		const shorter = firstTimeline.points.length <= secondTimeline.points.length ? firstTimeline : secondTimeline;
		const longer = shorter === firstTimeline ? secondTimeline : firstTimeline;
		const distances = [];
		for (const point of shorter.points) {
			if (point.time % (6 * 3600000) !== 0) continue;
			const counterpart = longer.byTime.get(point.time);
			if (counterpart) distances.push(haversineKm(point.longitude, point.latitude, counterpart.longitude, counterpart.latitude));
		}
		if (distances.length < 2) return Infinity;
		distances.sort((a, b) => a - b);
		const median = distances[Math.floor(distances.length / 2)];
		const mean = distances.reduce((sum, value) => sum + value, 0) / distances.length;
		const available = Math.max(2, Math.ceil(shorter.points.length / 6));
		const coveragePenalty = 140 * (1 - Math.min(1, distances.length / available));
		return .72 * median + .28 * mean + coveragePenalty;
	}

	function forecastSystemGroups() {
		const current = currentValidTime();
		const items = displayEntries().flatMap(entry => (entry.payload.systems || []).map(system => ({...entry, system})));
		const cacheKey = items.map(item => `${systemItemKey(item)}:${item.payload.payload_variant || 'full'}`).sort().join('|');
		let groups = state.systemGroupsCacheKey === cacheKey ? state.systemGroupsCache : null;
		if (!groups) {
			items.sort((a, b) => Number(b.system.member_count || 1) - Number(a.system.member_count || 1) || systemItemKey(a).localeCompare(systemItemKey(b)));
			groups = [];
			for (const item of items) {
				let best = null;
				for (const group of groups) {
					if (group.items.some(member => member.runKey === item.runKey)) continue;
					const scores = group.items.map(member => systemMatchScore(item, member)).filter(Number.isFinite).sort((a, b) => a - b);
					if (!scores.length) continue;
					const score = scores[Math.floor(scores.length / 2)];
					if (score <= 550 && (!best || score < best.score)) best = {group, score};
				}
				if (best) best.group.items.push(item);
				else groups.push({items: [item]});
			}
			state.systemGroupsCacheKey = cacheKey;
			state.systemGroupsCache = groups;
		}
		for (const group of groups) {
			group.items.sort((a, b) => a.model.label.localeCompare(b.model.label) || String(b.payload.cycle).localeCompare(String(a.payload.cycle)));
			group.key = group.items.map(systemItemKey).sort().join('|');
			group.active = group.items.filter(item => {
				if (!Number.isFinite(current)) return false;
				const timeline = systemTimeline(item);
				return timeline.points.some(point => Math.abs(point.time - current) <= 1.1 * 3600000);
			}).length;
			group.models = new Set(group.items.map(item => item.model.id)).size;
			group.members = group.items.reduce((sum, item) => sum + Number(item.system.member_count || 1), 0);
		}
		return groups.sort((a, b) => b.active - a.active || b.models - a.models || b.items.length - a.items.length || b.members - a.members || a.key.localeCompare(b.key));
	}

	function selectedForecastGroup(groups) {
		const values = groups || forecastSystemGroups();
		let group = state.selectedSystem
			? values.find(candidate => candidate.items.some(item => item.runKey === state.selectedSystem.runKey && item.system.id === state.selectedSystem.systemId))
			: null;
		if (!group) {
			group = values[0] || null;
			const anchor = group && (group.items.find(item => systemTimeline(item).points.some(point => Math.abs(point.time - currentValidTime()) <= 1.1 * 3600000)) || group.items[0]);
			state.selectedSystem = anchor ? {runKey: anchor.runKey, systemId: anchor.system.id} : null;
		}
		return group;
	}

	function groupReference(group) {
		const current = currentValidTime();
		const nearby = [];
		for (const item of group.items) {
			const points = systemTimeline(item).points;
			let point = points[0];
			for (const candidate of points) if (!point || Math.abs(candidate.time - current) < Math.abs(point.time - current)) point = candidate;
			if (point && (!Number.isFinite(current) || Math.abs(point.time - current) <= 12 * 3600000)) nearby.push(point);
		}
		const points = nearby.length ? nearby : group.items.map(item => systemTimeline(item).points[0]).filter(Boolean);
		return points.length ? {
			longitude: points.reduce((sum, point) => sum + point.longitude, 0) / points.length,
			latitude: points.reduce((sum, point) => sum + point.latitude, 0) / points.length,
			time: Math.min(...points.map(point => point.time))
		} : {longitude: NaN, latitude: NaN, time: NaN};
	}

	function groupLabel(group) {
		const reference = groupReference(group);
		const meaningful = group.items.map(item => String(item.system.label || '')).find(label => label && !/^Forecast system \d+$/i.test(label) && !/^Disturbance \d+$/i.test(label));
		const position = Number.isFinite(reference.longitude) && Number.isFinite(reference.latitude)
			? `${Math.abs(reference.latitude).toFixed(1)}°${reference.latitude < 0 ? 'S' : 'N'}, ${Math.abs(reference.longitude).toFixed(1)}°${reference.longitude < 0 ? 'W' : 'E'}`
			: 'Forecast system';
		return `${meaningful || position} · ${group.items.length} run${group.items.length === 1 ? '' : 's'}`;
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
		if (analysisCentreCaches.has(payload)) return analysisCentreCaches.get(payload);
		const output = [];
		for (const item of payload.systems || []) {
			const point = meanTrack(payload, item).find(value => Number(value[0]) === 0);
			if (point) output.push({system_id: String(item.id), longitude: Number(point[1]), latitude: Number(point[2])});
		}
		analysisCentreCaches.set(payload, output);
		return output;
	}

	function analysisHistory(payload, system, modelId) {
		let cache = analysisHistoryCaches.get(payload);
		if (!cache) { cache = new Map(); analysisHistoryCaches.set(payload, cache); }
		const cacheKey = `${state.mode}:${modelId}:${system.id}`;
		if (cache.has(cacheKey)) return cache.get(cacheKey);
		const forecast = meanTrack(payload, system);
		const initial = forecast.find(point => Number(point[0]) === 0);
		if (!initial) { cache.set(cacheKey, []); return []; }
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
		const result = matched.reverse();
		cache.set(cacheKey, result);
		return result;
	}

	function stitchedTrack(payload, system, modelId) {
		const forecast = meanTrack(payload, system);
		const history = analysisHistory(payload, system, modelId);
		return {history, points: [...history, ...forecast]};
	}

	function drawPath(context, map, points, colour, width, alpha) {
		if (!points || points.length < 2) return;
		context.beginPath();
		points.forEach((point, index) => {
			const xy = map.project(Number(point[2]), Number(point[1]));
			if (!index) context.moveTo(...xy); else context.lineTo(...xy);
		});
		context.globalAlpha = alpha;
		context.strokeStyle = colour; context.lineWidth = width; context.lineJoin = 'round'; context.lineCap = 'round';
		context.setLineDash([]); context.stroke();
		context.setLineDash([]); context.globalAlpha = 1;
	}

	function pointAt(points, step) {
		if (!points || !points.length) return null;
		let best = points[0];
		for (const point of points) if (Math.abs(Number(point[0]) - step) < Math.abs(Number(best[0]) - step)) best = point;
		return Math.abs(Number(best[0]) - step) <= 1 ? best : null;
	}

	function pointAtEpoch(points, timeMs) {
		if (!points || !points.length || !Number.isFinite(timeMs)) return null;
		const hour = timeMs / 3600000;
		let best = points[0];
		for (const point of points) if (Math.abs(Number(point[0]) - hour) < Math.abs(Number(best[0]) - hour)) best = point;
		return Math.abs(Number(best[0]) - hour) <= 1.01 ? best : null;
	}

	function drawAnalysisPath(target, points, colour, marker) {
		if (!points || points.length < 2) return;
		target.context.beginPath();
		points.forEach((point, index) => {
			const xy = target.projection.project(Number(point[2]), Number(point[1]));
			if (!index) target.context.moveTo(...xy); else target.context.lineTo(...xy);
		});
		target.context.setLineDash([]); target.context.strokeStyle = '#fffdf6'; target.context.lineWidth = 5; target.context.globalAlpha = .92; target.context.stroke();
		target.context.strokeStyle = colour; target.context.lineWidth = 2.5; target.context.globalAlpha = 1; target.context.stroke();
		if (marker) {
			const xy = target.projection.project(Number(marker[2]), Number(marker[1]));
			target.context.beginPath(); target.context.arc(xy[0], xy[1], 4.2, 0, Math.PI * 2); target.context.fillStyle = colour; target.context.fill(); target.context.strokeStyle = '#fffdf6'; target.context.lineWidth = 1.5; target.context.stroke();
		}
	}

	function runColour(entry) {
		if (state.mode !== 'latest') return state.archiveColourIndexes.get(entry.runKey)
			|| modelTrackColour(entry.model.id, entry.model.colour || entry.payload.model.colour);
		return modelTrackColour(entry.model.id, entry.model.colour || entry.payload.model.colour);
	}

	function drawTracks(systemGroups) {
		const target = canvasContext('#mlaForecastTracks');
		const selectedGroup = selectedForecastGroup(systemGroups);
		const selectedKeys = new Set(selectedGroup ? selectedGroup.items.map(systemItemKey) : []);
		for (const entry of displayEntries()) {
			const {model, payload, runKey} = entry;
			const current = stepForPayload(payload);
			const colour = runColour(entry);
			for (const system of payload.systems || []) {
				const tracks = tracksForSystem(payload, system);
				const selected = selectedKeys.has(`${runKey}:${system.id}`);
				if (state.showMembers && payload.model.kind === 'ensemble') {
					for (const track of tracks) drawPath(target.context, target.projection, track.points, colour, 1, selected ? .48 : .24);
				}
				const mean = meanTrack(payload, system);
				const stitched = stitchedTrack(payload, system, model.id);
				// Operational-style distinction: previous analyses are a thin solid
				// history, while every forecast lead retains one thicker solid path.
				// The valid-time slider moves the marker; it does not restyle a path
				// as an artificial "past" and "future" forecast segment.
				if (selected) {
					drawPath(target.context, target.projection, stitched.history, '#fffdf6', 3.6, .92);
					drawPath(target.context, target.projection, mean, '#fffdf6', payload.model.kind === 'ensemble' ? 6.2 : 6.6, .96);
				}
				drawPath(target.context, target.projection, stitched.history, colour, selected ? 1.8 : 1.25, selected ? .9 : .62);
				drawPath(target.context, target.projection, mean, colour, payload.model.kind === 'ensemble' ? 3.1 : 3.5, selected ? 1 : .9);
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
		if (state.mode !== 'latest' && state.analysisSources.size) {
			const validTime = currentValidTime() == null ? archiveTargetTime() : currentValidTime();
			for (const source of ALTERNATIVE_ANALYSIS_KEYS) {
				if (!state.analysisSources.has(source)) continue;
				for (const track of nativeReanalysisTracksOnDay(source, validTime)) {
					drawAnalysisPath(target, track.points, ANALYSIS_TRACKS[source].colour, pointAtEpoch(track.points, validTime));
				}
			}
			const verificationTracks = new Map();
			for (const entry of displayEntries()) for (const track of (entry.payload.verification || {}).tracks || []) if (!verificationTracks.has(String(track.id))) verificationTracks.set(String(track.id), {entry, track});
			if (state.analysisSources.has('era5') && atlasContextTrackActive(validTime)) {
				drawAnalysisPath(target, state.atlasContextTrack.points, ANALYSIS_TRACKS.era5.colour, pointAtEpoch(state.atlasContextTrack.points, validTime));
			}
			for (const {entry, track} of verificationTracks.values()) {
				if (state.atlasContextTrack && String(track.id) === state.atlasContextTrack.id) continue;
				const currentStep = stepForPayload(entry.payload);
				if (state.analysisSources.has('era5')) drawAnalysisPath(target, track.points, ANALYSIS_TRACKS.era5.colour, pointAt(track.points, currentStep));
			}
		}
		drawAnnotations(target);
	}

	function evolutionCanvasContext() {
		const canvas = $('#mlaForecastEvolution');
		const rectangle = canvas.getBoundingClientRect();
		const width = Math.max(1, rectangle.width), height = Math.max(1, rectangle.height);
		const ratio = Math.min(2, window.devicePixelRatio || 1);
		if (canvas.width !== Math.round(width * ratio) || canvas.height !== Math.round(height * ratio)) {
			canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
		}
		const context = canvas.getContext('2d');
		context.setTransform(ratio, 0, 0, ratio, 0, 0);
		context.clearRect(0, 0, width, height);
		return {canvas, context, width, height};
	}

	function sampledWeatherValue(record, payload, frame, longitude, latitude) {
		if (!record || !payload || !payload.grid) return null;
		const shape = record.field.shape || [];
		if (shape.length !== 3 || frame < 0 || frame >= shape[0]) return null;
		const ny = Number(shape[1]), nx = Number(shape[2]), grid = payload.grid;
		const x = clamp(Math.round((Number(longitude) - Number(grid.west)) / (Number(grid.east) - Number(grid.west)) * (nx - 1)), 0, nx - 1);
		const y = clamp(Math.round((Number(latitude) - Number(grid.south)) / (Number(grid.north) - Number(grid.south)) * (ny - 1)), 0, ny - 1);
		const encoded = record.decoded[frame * ny * nx + y * nx + x];
		return Number(encoded) * Number(record.field.scale) + Number(record.field.offset || 0);
	}

	async function buildForecastEvolutionSeries(item) {
		const cycle = new Date(item.payload.cycle_utc).getTime();
		const tracks = tracksForSystem(item.payload, item.system);
		const byStep = new Map();
		const byMember = new Map();
		for (const track of tracks) {
			const member = String(track.member || track.id || 'member');
			if (!byMember.has(member)) byMember.set(member, new Map());
			for (const point of track.points || []) {
				if (point[7] === 'i' || !Number.isFinite(Number(point[3]))) continue;
				const step = Number(point[0]);
				if (!byStep.has(step)) byStep.set(step, []);
				byStep.get(step).push(Number(point[3]));
				const memberSteps = byMember.get(member);
				if (!memberSteps.has(step)) memberSteps.set(step, []);
				memberSteps.get(step).push(Number(point[3]));
			}
		}
		const stepSeries = values => [...values.entries()].sort((a, b) => a[0] - b[0]).map(([step, samples]) => ({
			step,
			time: cycle + step * 3600000,
			value: samples.reduce((sum, value) => sum + value, 0) / samples.length
		}));
		const vorticity = stepSeries(byStep);
		const members = [...byMember.entries()].map(([member, values]) => ({member, vorticity: stepSeries(values)})).filter(record => record.vorticity.length);
		let precipitation = [];
		const precipitationRecord = item.payload.weather && item.payload.weather.precipitation
			? await decodeWeather(item.payload, 'precipitation')
			: null;
		if (precipitationRecord) {
			const mean = new Map(meanTrack(item.payload, item.system).map(point => [Number(point[0]), point]));
			const native = (item.payload.steps || []).map((step, frame) => {
				const point = mean.get(Number(step));
				if (!point) return null;
				return {step: Number(step), frame, longitude: point[1], latitude: point[2]};
			}).filter(Boolean);
			precipitation = native.map(point => ({
				step: point.step,
				time: cycle + point.step * 3600000,
				value: sampledWeatherValue(precipitationRecord, item.payload, point.frame, point.longitude, point.latitude)
			})).filter(point => Number.isFinite(point.value));
		}
		return {vorticity, precipitation, members};
	}

	async function forecastEvolutionSeries(item) {
		let cache = evolutionSeriesCaches.get(item.payload);
		if (!cache) { cache = new Map(); evolutionSeriesCaches.set(item.payload, cache); }
		if (!cache.has(item.system.id)) cache.set(item.system.id, buildForecastEvolutionSeries(item));
		try { return await cache.get(item.system.id); }
		catch (error) { cache.delete(item.system.id); throw error; }
	}

	function chartNumber(value, digits) {
		return Number(value).toLocaleString('en-GB', {maximumFractionDigits: digits, minimumFractionDigits: digits});
	}

	function compactCycleLabel(value) {
		const date = new Date(value);
		if (!Number.isFinite(date.getTime())) return '';
		const day = new Intl.DateTimeFormat('en-GB', {timeZone: 'UTC', day: '2-digit', month: 'short'}).format(date);
		return `${day} ${String(date.getUTCHours()).padStart(2, '0')}Z`;
	}

	function evolutionRunLabel(item) {
		return `${item.model.label} · ${compactCycleLabel(item.payload.cycle_utc)}`;
	}

	function chartRunLabel(item, group) {
		return group.items.filter(member => member.model.id === item.model.id).length > 1
			? evolutionRunLabel(item)
			: item.model.label;
	}

	function updateEvolutionControls(groups, group) {
		const select = $('#mlaForecastEvolutionSystem');
		select.innerHTML = groups.map(candidate => `<option value="${esc(candidate.key)}">${esc(groupLabel(candidate))}</option>`).join('');
		select.value = group.key;
		select.disabled = groups.length < 2;
		$('#mlaForecastEvolutionLegend').innerHTML = group.items.map(item => `<span><i style="--run-colour:${esc(runColour(item))}" aria-hidden="true"></i>${esc(chartRunLabel(item, group))}</span>`).join('');
	}

	function drawEvolutionLine(context, points, x, y, colour, dash, markers, width, alpha) {
		if (!points.length) return;
		context.save();
		context.globalAlpha = alpha == null ? 1 : alpha;
		context.beginPath();
		points.forEach((point, index) => { if (!index) context.moveTo(x(point.time), y(point.value)); else context.lineTo(x(point.time), y(point.value)); });
		context.strokeStyle = colour; context.lineWidth = width == null ? 2 : width; context.lineJoin = 'round'; context.lineCap = 'round'; context.setLineDash(dash); context.stroke();
		if (markers && points.length > 1) {
			const stride = Math.max(1, Math.ceil(points.length / 14));
			context.setLineDash([]); context.fillStyle = '#fffdf6'; context.strokeStyle = colour; context.lineWidth = 1.3;
			for (let index = 0; index < points.length; index += stride) {
				context.beginPath(); context.arc(x(points[index].time), y(points[index].value), 2.2, 0, Math.PI * 2); context.fill(); context.stroke();
			}
		}
		context.restore();
	}

	function requestEvolutionWeather(group) {
		for (const item of group.items) {
			if (!weatherFields(item).has('precipitation') || item.payload.weather || state.fullLoads.has(item.runKey) || state.fullFailures.has(item.runKey)) continue;
			ensureFullPayload(item.runKey).then(() => scheduleRender()).catch(error => {
				state.fullFailures.add(item.runKey);
				console.warn(`Forecast evolution weather unavailable for ${item.runKey}`, error);
				scheduleRender();
			});
		}
	}

	async function drawForecastEvolution(systemGroups, renderSerial) {
		const section = $('#mlaForecastEvolutionPanel');
		const group = selectedForecastGroup(systemGroups);
		section.hidden = !group;
		if (!group) return;
		updateEvolutionControls(systemGroups, group);
		const {canvas, context, width, height} = evolutionCanvasContext();
		const series = (await Promise.all(group.items.map(async item => ({item, values: await forecastEvolutionSeries(item)}))))
			.filter(item => item.values.vorticity.length || item.values.precipitation.length);
		if (renderSerial !== state.renderSerial) return;
		if (!series.length) return;
		const dark = getComputedStyle(root).getPropertyValue('--mla-ink').trim() || '#28211a';
		const muted = getComputedStyle(root).getPropertyValue('--mla-muted').trim() || '#716b63';
		const line = getComputedStyle(root).getPropertyValue('--mla-line').trim() || '#d8d0c4';
		const compact = width < 560;
		const left = compact ? 57 : 64, right = 18, top = 13, bottom = 44, gap = 39;
		const plotWidth = Math.max(1, width - left - right);
		const panelHeight = Math.max(58, (height - top - bottom - gap) / 2);
		const vortTop = top, rainTop = top + panelHeight + gap;
		const memberVorticity = state.showMembers
			? series.flatMap(item => item.item.payload.model.kind === 'ensemble' ? item.values.members.flatMap(member => member.vorticity) : [])
			: [];
		const times = [...series.flatMap(item => [...item.values.vorticity, ...item.values.precipitation]), ...memberVorticity].map(point => point.time).filter(Number.isFinite);
		const first = Math.min(...times), last = Math.max(...times), span = Math.max(3600000, last - first);
		const x = time => left + (Number(time) - first) / span * plotWidth;
		const vortMaximum = Math.max(1, ...series.flatMap(item => item.values.vorticity.map(point => point.value)), ...memberVorticity.map(point => point.value)) * 1.08;
		const rainMaximum = Math.max(1, ...series.flatMap(item => item.values.precipitation.map(point => point.value))) * 1.08;
		const yVort = value => vortTop + panelHeight * (1 - Number(value) / vortMaximum);
		const yRain = value => rainTop + panelHeight * (1 - Number(value) / rainMaximum);
		context.font = '12px "effra", Effra, Arial, sans-serif';
		context.strokeStyle = line; context.fillStyle = muted; context.lineWidth = 1;
		context.textBaseline = 'middle';
		for (const [panelTop, maximum, digits] of [[vortTop, vortMaximum, 1], [rainTop, rainMaximum, 0]]) {
			for (let index = 0; index <= 3; index++) {
				const fraction = index / 3, yy = panelTop + panelHeight * (1 - fraction);
				context.beginPath(); context.moveTo(left, yy); context.lineTo(width - right, yy); context.stroke();
				context.textAlign = 'right'; context.fillText(chartNumber(maximum * fraction, digits), left - 8, yy);
			}
		}
		const ticks = compact ? 3 : 5;
		context.textAlign = 'center';
		for (let index = 0; index <= ticks; index++) {
			const time = first + span * index / ticks, xx = x(time), date = new Date(time);
			context.strokeStyle = line; context.beginPath(); context.moveTo(xx, vortTop); context.lineTo(xx, rainTop + panelHeight); context.stroke();
			context.fillStyle = muted; context.textBaseline = 'top';
			context.fillText(new Intl.DateTimeFormat('en-GB', {timeZone: 'UTC', day: '2-digit', month: 'short'}).format(date), xx, rainTop + panelHeight + 8);
			context.fillText(`${String(date.getUTCHours()).padStart(2, '0')}Z`, xx, rainTop + panelHeight + 23);
		}
		const dashPatterns = [[], [8, 3], [2, 3], [11, 3, 2, 3], [5, 3]];
		if (state.showMembers) series.forEach(record => {
			if (record.item.payload.model.kind !== 'ensemble') return;
			const colour = runColour(record.item);
			const alpha = clamp(.62 / Math.sqrt(Math.max(1, record.values.members.length)), .075, .22);
			for (const member of record.values.members) drawEvolutionLine(context, member.vorticity, x, yVort, colour, [], false, .85, alpha);
		});
		series.forEach((record, index) => {
			const colour = runColour(record.item), dash = dashPatterns[index % dashPatterns.length];
			drawEvolutionLine(context, record.values.vorticity, x, yVort, colour, dash, false, 2.7, 1);
			drawEvolutionLine(context, record.values.precipitation, x, yRain, colour, dash, true, 2.2, .96);
		});
		if (!series.some(record => record.values.precipitation.length)) {
			context.fillStyle = muted; context.textAlign = 'center'; context.textBaseline = 'middle';
			context.fillText('Precipitation unavailable for these track-only runs', left + plotWidth / 2, rainTop + panelHeight / 2);
		}
		const current = currentValidTime();
		if (current >= first && current <= last) {
			context.save(); context.setLineDash([5, 4]); context.strokeStyle = dark; context.lineWidth = 1.25;
			context.beginPath(); context.moveTo(x(current), vortTop); context.lineTo(x(current), rainTop + panelHeight); context.stroke(); context.restore();
		}
		context.save(); context.translate(15, vortTop + panelHeight / 2); context.rotate(-Math.PI / 2); context.textAlign = 'center'; context.textBaseline = 'middle'; context.fillStyle = dark; context.fillText('Tracked 850-hPa vorticity (10⁻⁵ s⁻¹)', 0, 0); context.restore();
		context.save(); context.translate(15, rainTop + panelHeight / 2); context.rotate(-Math.PI / 2); context.textAlign = 'center'; context.textBaseline = 'middle'; context.fillStyle = dark; context.fillText('Trailing 24 h rain (mm)', 0, 0); context.restore();
		canvas._forecastTimeline = {left, right: width - right, first, last};
		const status = $('#mlaForecastEvolutionStatus');
		const rainAvailable = group.items.filter(item => weatherFields(item).has('precipitation')).length;
		const rainLoaded = series.filter(record => record.values.precipitation.length).length;
		const rainLoading = group.items.filter(item => state.fullLoads.has(item.runKey)).length;
		const ensembleMembers = state.showMembers ? series.reduce((sum, record) => sum + (record.item.payload.model.kind === 'ensemble' ? record.values.members.length : 0), 0) : 0;
		status.textContent = `Bold: ${group.items.length} model/run mean${group.items.length === 1 ? '' : 's'}`
			+ (ensembleMembers ? ` · thin: ${ensembleMembers} ensemble members` : '')
			+ (rainLoading ? ` · loading mean precipitation for ${rainLoading}` : rainAvailable ? ` · mean precipitation ${rainLoaded}/${rainAvailable}` : ' · precipitation unavailable');
		requestEvolutionWeather(group);
	}

	async function render() {
		const serial = ++state.renderSerial;
		drawBase();
		$('#mlaForecastWeatherKey').hidden = true;
		await drawWeather();
		if (serial !== state.renderSerial) return;
		await ensureArchiveNativeReanalyses();
		if (serial !== state.renderSerial) return;
		const systemGroups = forecastSystemGroups();
		drawTracks(systemGroups);
		await drawForecastEvolution(systemGroups, serial);
		if (serial !== state.renderSerial) return;
		updateTimeLabel();
		const entries = displayEntries();
		const era5TrackIds = new Set();
		if (state.mode !== 'latest') for (const item of entries) for (const track of (item.payload.verification || {}).tracks || []) era5TrackIds.add(String(track.id));
		if (state.mode !== 'latest' && atlasContextTrackActive(currentValidTime() == null ? archiveTargetTime() : currentValidTime())) era5TrackIds.add(state.atlasContextTrack.id);
		const analysisCounts = {};
		for (const source of state.analysisSources) {
			analysisCounts[source] = source === 'era5' ? era5TrackIds.size : nativeReanalysisTracksOnDay(source, currentValidTime() == null ? archiveTargetTime() : currentValidTime()).length;
		}
		const mapStack = $('#mlaForecastMapStack');
		mapStack.dataset.zoom = state.mapZoom.toFixed(3);
		mapStack.dataset.centerLon = state.mapCenterLon.toFixed(3);
		mapStack.dataset.centerLat = state.mapCenterLat.toFixed(3);
		const status = entries.length
			? [`${entries.length} run${entries.length === 1 ? '' : 's'} loaded`, `${systemGroups.length} storm group${systemGroups.length === 1 ? '' : 's'}`]
			: [];
		if (state.mode !== 'latest') for (const source of state.analysisSources) {
			if (analysisCounts[source]) status.push(source === 'era5'
				? `${analysisCounts[source]} ERA5 match${analysisCounts[source] === 1 ? '' : 'es'}`
				: `${analysisCounts[source]} ${ANALYSIS_TRACKS[source].label} track${analysisCounts[source] === 1 ? '' : 's'}`);
			else if (source !== 'era5' && nativeReanalysisAvailable(source, currentValidTime() == null ? archiveTargetTime() : currentValidTime())) {
				status.push(`No ${ANALYSIS_TRACKS[source].label} physical track active at this time`);
			}
		}
		$('#mlaForecastMapStatus').textContent = status.length ? status.join(' · ') : 'Forecast data not loaded.';
		const runKey = $('#mlaForecastRunKey');
		runKey.hidden = entries.length < 2;
		runKey.innerHTML = entries.map(item => `<span><i style="--run-colour:${esc(runColour(item))}" aria-hidden="true"></i>${esc(evolutionRunLabel(item))}</span>`).join('');
		const analysisKey = $('#mlaForecastAnalysisKey');
		const visibleAnalyses = [...state.analysisSources].filter(source => analysisCounts[source]);
		analysisKey.hidden = !visibleAnalyses.length;
		analysisKey.innerHTML = visibleAnalyses.map(source => `<span><i style="--analysis-colour:${ANALYSIS_TRACKS[source].colour}" aria-hidden="true"></i>${esc(ANALYSIS_TRACKS[source].label)} analysis</span>`).join('');
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

	function bindForecastEvolution() {
		const canvas = $('#mlaForecastEvolution');
		let dragging = false;
		function move(event) {
			const timeline = canvas._forecastTimeline;
			if (!timeline || !state.timelineTimes.length) return;
			const rectangle = canvas.getBoundingClientRect();
			const local = clamp(event.clientX - rectangle.left, timeline.left, timeline.right);
			const target = timeline.first + (local - timeline.left) / Math.max(1, timeline.right - timeline.left) * (timeline.last - timeline.first);
			let nearest = 0;
			for (let index = 1; index < state.timelineTimes.length; index++) if (Math.abs(state.timelineTimes[index] - target) < Math.abs(state.timelineTimes[nearest] - target)) nearest = index;
			state.leadIndex = nearest;
			$('#mlaForecastLead').value = nearest;
			scheduleRender();
		}
		canvas.addEventListener('pointerdown', event => {
			dragging = true;
			if (canvas.setPointerCapture) canvas.setPointerCapture(event.pointerId);
			move(event);
		});
		canvas.addEventListener('pointermove', event => { if (dragging) move(event); });
		canvas.addEventListener('pointerup', () => { dragging = false; });
		canvas.addEventListener('pointercancel', () => { dragging = false; });
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
		if (input.checked) {
			state.selectedModels.add(input.value);
			if (modelDefinition(input.value).kind === 'ensemble') setShowMembers(true);
		} else state.selectedModels.delete(input.value);
		state.hasModelPreference = true;
		state.selectedSystem = null;
		populateInitializationControls();
		persistPreferences();
		loadSelectedModels();
	});
	$('#mlaForecastInitialization').addEventListener('change', event => {
		state.initialization = event.target.value;
		state.selectedSystem = null;
		state.leadIndex = 0;
		persistPreferences();
		loadSelectedModels();
	});
	$('#mlaForecastWeather').addEventListener('change', async event => { state.weather = event.target.value; populateWeatherModels(); await loadWeatherForSelection(); await render(); });
	$('#mlaForecastWeatherModel').addEventListener('change', async event => { state.weatherModel = event.target.value; await loadWeatherForSelection(); await render(); });
	$('#mlaForecastArchiveWeather').addEventListener('change', async event => { state.weather = event.target.value; populateWeatherModels(); await loadWeatherForSelection(); await render(); });
	$('#mlaForecastArchiveWeatherModel').addEventListener('change', async event => { state.weatherModel = event.target.value; await loadWeatherForSelection(); await render(); });
	$('#mlaForecastMembers').addEventListener('change', event => { setShowMembers(event.target.checked); render(); });
	$('#mlaForecastArchiveMembers').addEventListener('change', event => { setShowMembers(event.target.checked); render(); });
	$('#mlaForecastEvolutionSystem').addEventListener('change', event => {
		const group = forecastSystemGroups().find(candidate => candidate.key === event.target.value);
		const anchor = group && (group.items.find(item => systemTimeline(item).points.some(point => Math.abs(point.time - currentValidTime()) <= 1.1 * 3600000)) || group.items[0]);
		if (anchor) state.selectedSystem = {runKey: anchor.runKey, systemId: anchor.system.id};
		render();
	});
	$('#mlaForecastLead').addEventListener('input', event => { state.leadIndex = Number(event.target.value); scheduleRender(); });
	$('#mlaForecastPrevious').addEventListener('click', () => { state.leadIndex = Math.max(0, state.leadIndex - 1); $('#mlaForecastLead').value = state.leadIndex; render(); });
	$('#mlaForecastNext').addEventListener('click', () => { if (!state.timelineTimes.length) return; state.leadIndex = Math.min(state.timelineTimes.length - 1, state.leadIndex + 1); $('#mlaForecastLead').value = state.leadIndex; render(); });
	$('#mlaForecastArchiveDate').addEventListener('change', event => {
		if (parseArchiveTarget($('#mlaForecastArchiveSearch').value)) $('#mlaForecastArchiveSearch').value = '';
		state.archiveDate = event.target.value;
		state.archiveMonth = state.archiveDate ? state.archiveDate.slice(0, 7) : state.archiveMonth;
		state.archiveEntry = null;
		persistPreferences();
		populateArchive(false);
	});
	$('#mlaForecastArchiveHour').addEventListener('change', event => {
		if (parseArchiveTarget($('#mlaForecastArchiveSearch').value)) $('#mlaForecastArchiveSearch').value = '';
		state.archiveHour = event.target.value;
		state.archiveEntry = null;
		persistPreferences();
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
		persistPreferences();
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
		const analysisButton = event.target.closest('[data-forecast-analysis-source]');
		if (analysisButton) {
			if (analysisButton.disabled) return;
			const source = analysisButton.dataset.forecastAnalysisSource;
			if (state.analysisSources.has(source)) state.analysisSources.delete(source); else state.analysisSources.add(source);
			persistPreferences();
			configureTimeline(Boolean(displayEntries().length), archiveTargetTime());
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
	bindForecastEvolution();
	let atlasContextApplied = false;
	window.addEventListener('mla:forecast-visible', event => {
		const detail = event.detail || {};
		const parameters = new URLSearchParams(location.search);
		if (!atlasContextApplied && parameters.has('system') && String(detail.system || '') === parameters.get('system')) {
			atlasContextApplied = applyAtlasArchiveContext(detail);
		}
		initialise();
	});
	window.addEventListener('mla:open-forecast-archive', event => {
		const detail = event.detail || {};
		if (/^\d{4}-\d{2}-\d{2}$/.test(detail.date || '')) {
			state.archiveDate = detail.date;
			state.archiveMonth = detail.date.slice(0, 7);
		}
		if (['00', '06', '12', '18'].includes(detail.hour)) state.archiveHour = detail.hour;
		if (typeof detail.query === 'string') $('#mlaForecastArchiveSearch').value = detail.query;
		state.archiveEntry = null;
		state.archiveSelected.clear();
		state.selectedSystem = null;
		persistPreferences();
		const alreadyArchive = state.mode === 'archive';
		setMode('archive');
		if (state.initialised && alreadyArchive) ensureArchiveManifest().then(() => populateArchive(true)).catch(error => {
			notice(`Forecast archive is unavailable: ${error.message || error}`, 'flag', true);
		});
	});
	window.addEventListener('resize', () => { clearTimeout(state.resizeTimer); state.resizeTimer = setTimeout(resizeAndRender, 120); });

	const parameters = new URLSearchParams(location.search);
	if (['archive', 'tigge'].includes(parameters.get('fmode'))) state.mode = 'archive';
	else if (parameters.get('fmode') === 'latest') state.mode = 'latest';
	const requestedArchiveDate = parameters.get('fdate');
	if (/^\d{4}-\d{2}-\d{2}$/.test(requestedArchiveDate || '')) {
		state.archiveDate = requestedArchiveDate;
		state.archiveMonth = requestedArchiveDate.slice(0, 7);
	}
	if (['00', '06', '12', '18'].includes(parameters.get('fhour'))) state.archiveHour = parameters.get('fhour');
	syncModeControls();
	persistPreferences();
})();
