(function (scope) {
	'use strict';
	const valid = x => x !== null && x !== undefined && x !== '' && Number.isFinite(Number(x));
	const clean = values => values.filter(valid).map(Number);
	const mean = values => { const v = clean(values); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; };
	const quantile = (values, p) => { const v = clean(values).sort((a, b) => a - b); if (!v.length) return null; const x = (v.length - 1) * p, k = Math.floor(x); return v[k] + (v[Math.ceil(x)] - v[k]) * (x - k); };
	const months = {all: [1,2,3,4,5,6,7,8,9,10,11,12], jjas: [6,7,8,9], mam: [3,4,5], ond: [10,11,12], djf: [12,1,2]};
	const regions = {
		northwest: ['jammu_and_kashmir','himachal_pradesh','punjab','chandigarh','haryana','nct_of_delhi','rajasthan','gujarat'],
		north_central: ['uttar_pradesh','uttarakhand','madhya_pradesh','chhattisgarh'],
		east: ['bihar','jharkhand','odisha','west_bengal'],
		northeast: ['arunachal_pradesh','assam','meghalaya','nagaland','manipur','mizoram','tripura','sikkim'],
		west_coast: ['maharashtra','goa','karnataka','kerala','dadra_and_nagar_haveli','daman_and_diu'],
		south_peninsula: ['telangana','andhra_pradesh','tamil_nadu','puducherry']
	};
	function decode(payload) {
		return payload.events.map(e => ({...e, ...Object.fromEntries(payload.columns.map((k, j) => [k, e.v[j]]))}));
	}
	function pointInRing(x, y, ring) {
		let inside = false;
		for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
			const a = ring[i], b = ring[j];
			if ((a[1] > y) !== (b[1] > y) && x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]) inside = !inside;
		}
		return inside;
	}
	function inState(point, state) { return (state.rings || []).reduce((inside, ring) => inside !== pointInRing(point[0], point[1], ring), false); }
	function filter(events, options, geography) {
		const wanted = options.month ? [Number(options.month)] : months[options.season] || months.all;
		const ids = regions[options.region] || (options.region && options.region !== 'all' && options.region !== 'box' ? [options.region] : []);
		const states = (geography?.states || []).filter(s => ids.includes(s.id));
		const inBox = p => p[0] >= options.box[0] && p[1] >= options.box[1] && p[0] <= options.box[2] && p[1] <= options.box[3];
		return events.filter(e => {
			if (!wanted.includes(e.genesis_month) || e.peak_category < Number(options.category || 1)) return false;
			if (!options.region || options.region === 'all') return true;
			const point = options.location === 'lysis' ? [e.lysis_lon, e.lysis_lat] : [e.genesis_lon, e.genesis_lat];
			if (options.region === 'box') return options.location === 'passage' ? e.path.some(inBox) : inBox(point);
			return options.location === 'passage' ? e.states.some(id => ids.includes(id)) : states.some(s => inState(point, s));
		});
	}
	function annual(events, coverage, definitions) {
		const groups = new Map();
		for (const e of events) { if (!groups.has(e.genesis_year)) groups.set(e.genesis_year, []); groups.get(e.genesis_year).push(e); }
		return Array.from({length: coverage.years}, (_, n) => {
			const year = coverage.start_year + n, group = groups.get(year) || [];
			const row = {year, systems: group.length, system_days: group.reduce((sum, e) => sum + e.duration_hours / 24, 0),
				depressions_or_stronger: group.filter(e => e.peak_category >= 2).length,
				deep_depressions_or_stronger: group.filter(e => e.peak_category >= 3).length,
				cyclonic_storms_or_stronger: group.filter(e => e.peak_category >= 4).length};
			for (const [key, d] of Object.entries(definitions)) if (d.event) {
				const values = group.map(e => e[d.event]);
				row[key] = d.reducer === 'median' ? quantile(values, .5) : d.reducer === 'p90' ? quantile(values, .9) : mean(values);
			}
			return row;
		});
	}
	function density(events, kind, template) {
		const latitude_edges = template.latitude_edges, longitude_edges = template.longitude_edges;
		const unique_track_counts = Array.from({length: latitude_edges.length - 1}, () => Array(longitude_edges.length - 1).fill(0));
		for (const e of events) {
			const cells = kind === 'track_density' ? e.cells : [[Math.floor(e[kind === 'genesis_density' ? 'genesis_lat' : 'lysis_lat']), Math.floor(e[kind === 'genesis_density' ? 'genesis_lon' : 'lysis_lon'])]];
			for (const [lat, lon] of cells) { const r = lat - latitude_edges[0], c = lon - longitude_edges[0]; if (unique_track_counts[r] && c >= 0 && c < unique_track_counts[r].length) unique_track_counts[r][c]++; }
		}
		return {latitude_edges, longitude_edges, unique_track_counts};
	}
	function subsetRun(run, payload, options, geo) {
		const events = filter(payload.decoded || (payload.decoded = decode(payload)), options, geo);
		const base = run.seasonal[options.season];
		const season = {...base, annual: annual(events, payload.coverage, payload.metric_definitions), counts: {events: events.length, positions: events.reduce((s, e) => s + e.duration_hours, 0)},
			class_counts: Object.fromEntries([1,2,3,4,5,6].map(c => [c, events.filter(e => e.peak_category === c).length]))};
		for (const kind of ['track_density','genesis_density','lysis_density']) season[kind] = density(events, kind, base.track_density);
		return {...run, seasonal: {...run.seasonal, [options.season]: season},
			monthly: months.all.map(month => ({month, systems_per_year: events.filter(e => e.genesis_month === month).length / run.coverage.years})), selectedEvents: events};
	}
	// Deterministic, independent year-resampling. Missing annual means are not zeros.
	function changes(historical, future, seed = 731, samples = 600) {
		const left = clean(historical), right = clean(future);
		const h = mean(left), f = mean(right);
		const result = {historical:h, future:f, absolute_change: valid(h) && valid(f) ? f-h : null, percent_change: valid(h) && h !== 0 && valid(f) ? 100*(f/h-1) : null,
			ci05:null, ci95:null, percent_ci05:null, percent_ci95:null};
		if (!left.length || !right.length) return result;
		let randomState = seed >>> 0;
		const random = () => { randomState = (Math.imul(randomState,1664525)+1013904223) >>> 0; return randomState/4294967296; };
		const diff = [], pct = [];
		for (let j=0; j<samples; j++) {
			let a=0,b=0; for(let n=0;n<left.length;n++) a+=left[Math.floor(random()*left.length)]/left.length;
			for(let n=0;n<right.length;n++) b+=right[Math.floor(random()*right.length)]/right.length;
			diff.push(b-a); if(a!==0) pct.push(100*(b/a-1));
		}
		return {...result,ci05:quantile(diff,.05),ci95:quantile(diff,.95),percent_ci05:quantile(pct,.05),percent_ci95:quantile(pct,.95)};
	}
	function changeRun(historical, future, season, definitions) {
		return Object.fromEntries(Object.keys(definitions).map((key, i) => [key, changes(historical.seasonal[season].annual.map(r=>r[key]),future.seasonal[season].annual.map(r=>r[key]),731+i)]));
	}
	function aggregateRecord(records) {
		const models = records.filter(r=>valid(r.historical)&&valid(r.future));
		const h=mean(models.map(r=>r.historical)),f=mean(models.map(r=>r.future));
		// Do not average confidence limits. The UI shows the individual intervals and model spread.
		return {historical:h,future:f,absolute_change:valid(h)&&valid(f)?f-h:null,percent_change:valid(h)&&h!==0&&valid(f)?100*(f/h-1):null,
			ci05:null,ci95:null,percent_ci05:null,percent_ci95:null,models,model_count:models.length};
	}
	function aggregateRun(runs, season, definitions) {
		const first=runs[0], years=first.coverage.years, sections=runs.map(r=>r.seasonal[season]);
		const section={...sections[0],annual:Array.from({length:years},(_,i)=>({year:i+1,...Object.fromEntries(Object.keys(definitions).map(k=>[k,mean(sections.map(s=>s.annual[i]?.[k]))]))})),
			counts:{events:mean(sections.map(s=>s.counts.events)),positions:mean(sections.map(s=>s.counts.positions))},
			class_counts:Object.fromEntries([1,2,3,4,5,6].map(c=>[c,mean(sections.map(s=>{const total=Object.values(s.class_counts).reduce((a,b)=>a+Number(b),0);return total?Number(s.class_counts[c]||0)/total:null;}))||0]))};
		for(const kind of ['track_density','genesis_density','lysis_density']) {
			const d=sections[0][kind]||sections[0].track_density;
			section[kind]={...d,unique_track_counts:d.unique_track_counts.map((row,r)=>row.map((_,c)=>mean(runs.map(run=>(run.seasonal[season][kind]||run.seasonal[season].track_density).unique_track_counts[r][c]/run.coverage.years))*years))};
		}
		return {...first,seasonal:{...first.seasonal,[season]:section},monthly:months.all.map((m,i)=>({month:m,systems_per_year:mean(runs.map(r=>r.monthly[i].systems_per_year))}))};
	}
	function aggregateImpact(entries) {
		const available=entries.filter(e=>e.impact); if(!available.length)return null;
		const result=structuredClone(available[0].impact);
		const recordsAt=path=>available.map(e=>({id:e.pair.id,source_label:e.pair.source_label,...path.reduce((a,k)=>a?.[k],e.impact)}));
		for(const key of Object.keys(result.india_jjas_changes))result.india_jjas_changes[key]=aggregateRecord(recordsAt(['india_jjas_changes',key]));
		for(const [id,region] of Object.entries(result.regional_india_jjas_changes||{}))for(const key of Object.keys(region.changes))region.changes[key]=aggregateRecord(recordsAt(['regional_india_jjas_changes',id,'changes',key]));
		for(const [season,item] of Object.entries(result.storm_centred_precipitation?.seasons||{})) {
			for(const key of ['historical_mean_mm','future_mean_mm','change_mm']) if(Array.isArray(item[key]))item[key]=item[key].map((row,r)=>row.map((_,c)=>mean(available.map(e=>e.impact.storm_centred_precipitation?.seasons[season]?.[key]?.[r]?.[c]))));
			item.model_count=available.length;
		}
		return result;
	}
	function combine(entries, base, season, definitions) {
		if(entries.length===1)return entries[0];
		const seasonal=Object.fromEntries(Object.keys(definitions).map(key=>[key,aggregateRecord(entries.map(e=>({id:e.pair.id,...e.change.seasonal_changes[season][key]})))]));
		const pair={...base.pair,kind:'multi-model',model_ids:entries.map(e=>e.pair.id)};
		if(pair.warming){const models=entries.filter(e=>valid(e.pair.warming?.change_k)).map(e=>({id:e.pair.id,change_k:e.pair.warming.change_k}));pair.warming={...pair.warming,models,model_count:models.length,mean_change_k:mean(models.map(m=>m.change_k)),minimum_change_k:Math.min(...models.map(m=>m.change_k)),maximum_change_k:Math.max(...models.map(m=>m.change_k))};}
		const template=entries[0].historical.seasonal[season].track_density;
		const agreement={...template,model_count:entries.length,summary:{cells_with_any_change:0,cells_at_least_80_percent_agreement:0,cells_unanimous:0,robust_model_threshold:Math.ceil(.8*entries.length)}};
		agreement.signed_agreement_fraction=template.unique_track_counts.map((row,r)=>row.map((_,c)=>{
			const delta=entries.map(e=>e.future.seasonal[season].track_density.unique_track_counts[r][c]/e.future.coverage.years-e.historical.seasonal[season].track_density.unique_track_counts[r][c]/e.historical.coverage.years);
			const positive=delta.filter(v=>v>1e-12).length,negative=delta.filter(v=>v< -1e-12).length,max=Math.max(positive,negative);
			if(max){agreement.summary.cells_with_any_change++;if(max>=agreement.summary.robust_model_threshold)agreement.summary.cells_at_least_80_percent_agreement++;if(max===entries.length)agreement.summary.cells_unanimous++;}
			return Math.sign(positive-negative)*max/entries.length;
		}));
		return {pair,
			historical:aggregateRun(entries.map(e=>e.historical),season,definitions),future:aggregateRun(entries.map(e=>e.future),season,definitions),
			change:{...base.change,seasonal_changes:{...base.change.seasonal_changes,[season]:seasonal},track_density_agreement:{[season]:agreement}},impact:aggregateImpact(entries)};
	}
	const api={valid,mean,quantile,regions,months,decode,filter,annual,density,subsetRun,changes,changeRun,aggregateRecord,aggregateRun,aggregateImpact,combine,inState};
	if(typeof module==='object'&&module.exports)module.exports=api;else scope.LPSClimateSubsets=api;
})(globalThis);
