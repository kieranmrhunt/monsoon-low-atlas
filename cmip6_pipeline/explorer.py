"""Publish event-level inputs for interactive climate subsets, without retuning tracks.

The browser uses the same event reductions and native genesis years as summarise.py.
State membership and density cells use original centres; box-selection coordinates
are rounded to 0.001 degree solely to reduce transfer size.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from shapely import Polygon, intersects_xy, make_valid

from .summarise import METRIC_DEFINITIONS, atomic_gzip_json, atomic_json
from reanalysis_pipeline.common import sha256


def read_json(path):
    with (gzip.open(path, 'rt') if str(path).endswith('.gz') else open(path)) as stream:
        return json.load(stream)


def build(root: Path, runs: Path, core: Path):
    manifest = read_json(root / 'manifest.json')
    index = read_json(root / manifest['index']['path'])
    geo = read_json(core)['geo']
    sources = {}
    for path in sorted(runs.glob('**/summary/manifest.json')):
        metadata = read_json(path)
        ref = metadata.get('asset', {})
        if not ref.get('path') or not (path.parent / 'events.parquet').is_file():
            continue
        summary = read_json(path.parent / ref['path'])
        key = (summary['provenance']['catalogue_sha256'], summary['coverage']['start_year'], summary['coverage']['end_year'])
        sources[key] = path.parent
    refs = {p[role]['url'] for p in index['pairs'] if p.get('kind') != 'multi-model' for role in ('historical', 'future')}
    assets, built = {}, {}
    columns = list(dict.fromkeys(['track_id', 'genesis_year', 'genesis_month', 'peak_category', 'duration_hours',
                                'genesis_lon', 'genesis_lat', 'lysis_lon', 'lysis_lat'] +
                               [d['event'] for d in METRIC_DEFINITIONS.values() if 'event' in d]))
    for url in sorted(refs):
        summary = read_json(root / url)
        key = (summary['provenance']['catalogue_sha256'], summary['coverage']['start_year'], summary['coverage']['end_year'])
        if key not in sources:
            raise ValueError(f'No exact event source for {url}: {key}')
        if key in built:
            assets[url] = built[key]
            continue
        directory = sources[key]
        events = pd.read_parquet(directory / 'events.parquet')
        catalogue = directory.parent / 'cmip6-physical-events.parquet'
        if sha256(catalogue) != key[0]:
            raise ValueError(f'Catalogue changed since summary: {catalogue}')
        points = pd.read_parquet(catalogue, columns=['track_id', 'lon', 'lat'])
        points = points.loc[points.track_id.isin(events.track_id)].copy()
        membership = {int(t): [] for t in events.track_id}
        lon, lat = points.lon.to_numpy(), points.lat.to_numpy()
        for state in geo.get('states', []):
            inside = np.zeros(len(points), dtype=bool)
            for ring in state.get('rings', []):
                if len(ring) >= 3:
                    inside ^= intersects_xy(make_valid(Polygon(ring)), lon, lat)
            for tid in points.loc[inside, 'track_id'].unique():
                membership[int(tid)].append(state['id'])
        groups = dict(tuple(points.groupby('track_id', sort=False)))
        records = []
        for record in events.to_dict('records'):
            tid = int(record['track_id'])
            track = groups[tid]
            cells = sorted(set((int(np.floor(y)), int(np.floor(x))) for x, y in zip(track.lon, track.lat)))
            records.append({
                'v': [None if pd.isna(record.get(c)) else float(record[c]) for c in columns],
                'states': membership[tid],
                'cells': cells,
                'path': np.round(track[['lon', 'lat']].to_numpy(), 3).tolist(),
            })
        payload = {'schema': 'lps-climate-explorer-events-v1', 'coverage': summary['coverage'],
                   'columns': columns, 'events': records, 'metric_definitions': METRIC_DEFINITIONS,
                   'provenance': summary['provenance'], 'counts': summary['counts']}
        raw = json.dumps(payload, allow_nan=False, separators=(',', ':')).encode()
        name = f'assets/climate-events.{hashlib.sha256(raw).hexdigest()[:12]}.json.gz'
        atomic_gzip_json(root / name, payload)
        built[key] = assets[url] = {'url': name, 'sha256': sha256(root / name), 'bytes': (root / name).stat().st_size}
        print(summary['run']['source_label'], summary['run']['period_label'], len(records), assets[url]['bytes'], flush=True)
    output = {'schema': 'lps-climate-explorer-index-v1', 'climate_index_sha256': manifest['index']['sha256'],
              'runs': assets, 'coordinate_precision_degrees': .001}
    atomic_json(root / 'explorer.json', output)
    print(f'Published {len(built)} unique event assets / {len(assets)} summary references', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('climate-change'))
    parser.add_argument('--runs', type=Path, default=Path('.cmip6-runs'))
    parser.add_argument('--core', type=Path, required=True)
    args = parser.parse_args()
    build(args.root, args.runs, args.core)
