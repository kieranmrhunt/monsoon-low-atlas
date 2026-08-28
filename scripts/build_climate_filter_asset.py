#!/usr/bin/env python3
"""Build the small, track-grain BSISO/MJO/ENSO filter payload for the atlas."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd


BSISO_URL = "https://download.apcc21.org/BSISO/BSISO.INDEX.NORM.LY.data"
RMM_URL = "https://www.bom.gov.au/clim_data/IDCKGEM000/rmm.74toRealtime.txt"
ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
ONI_SEASONS = ("DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ", "JJA", "JAS", "ASO", "SON", "OND", "NDJ")


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser()
	parser.add_argument("--core", type=Path, required=True, help="Hashed atlas core JSON or JSON.gz")
	parser.add_argument("--bsiso", type=Path, required=True, help="APCC BSISO.INDEX.NORM.LY.data")
	parser.add_argument("--rmm", type=Path, required=True, help="Bureau of Meteorology rmm.74toRealtime.txt")
	parser.add_argument("--oni", type=Path, required=True, help="NOAA CPC oni.ascii.txt")
	parser.add_argument("--output-dir", type=Path, required=True)
	return parser.parse_args()


def sha256(path: Path) -> str:
	digest = hashlib.sha256()
	with path.open("rb") as stream:
		for block in iter(lambda: stream.read(1024 * 1024), b""):
			digest.update(block)
	return digest.hexdigest()


def read_json(path: Path) -> dict:
	opener = gzip.open if path.suffix == ".gz" else open
	with opener(path, "rt", encoding="utf-8") as stream:
		return json.load(stream)


def bsiso_phase(pc1: float, pc2: float) -> int:
	"""Return the Lee et al. BSISO1 phase for a point outside the unit circle."""
	if pc1 < 0 and pc2 <= 0:
		return 1 if pc1 > pc2 else 2
	if pc1 <= 0 and pc2 > 0:
		return 3 if abs(pc1) > pc2 else 4
	if pc1 > 0 and pc2 >= 0:
		return 5 if pc1 < pc2 else 6
	return 7 if pc1 > abs(pc2) else 8


def read_rmm(path: Path) -> pd.DataFrame:
	"""Read and validate the Bureau's all-season Wheeler-Hendon RMM record."""
	columns = ["year", "month", "day", "rmm1", "rmm2", "phase", "amplitude", "method"]
	frame = pd.read_csv(path, sep=r"\s+", skiprows=2, header=None, names=columns)
	if frame.empty:
		raise RuntimeError("the Bureau RMM source is empty")
	frame["date"] = pd.to_datetime(frame[["year", "month", "day"]], errors="raise")
	if frame["date"].duplicated().any():
		raise RuntimeError("the Bureau RMM source contains duplicate dates")
	if frame["date"].iloc[0] != pd.Timestamp("1974-06-01"):
		raise RuntimeError("unexpected first date in the Bureau RMM source")
	if frame["date"].iloc[-1] < pd.Timestamp("2025-12-31"):
		raise RuntimeError("the Bureau RMM source does not cover the catalogue end")
	valid = (
		np.isfinite(frame["rmm1"])
		& np.isfinite(frame["rmm2"])
		& np.isfinite(frame["amplitude"])
		& frame["phase"].between(1, 8)
		& frame["rmm1"].abs().lt(100)
		& frame["rmm2"].abs().lt(100)
		& frame["amplitude"].between(0, 100)
	)
	calculated = np.hypot(frame.loc[valid, "rmm1"], frame.loc[valid, "rmm2"])
	if not np.allclose(calculated, frame.loc[valid, "amplitude"], atol=5e-4, rtol=5e-4):
		raise RuntimeError("Bureau RMM amplitudes are inconsistent with RMM1/RMM2")
	frame["valid"] = valid
	return frame


def main() -> None:
	args = parse_args()
	core = read_json(args.core)
	fields = {name: index for index, name in enumerate(core["track_fields"])}
	genesis = pd.to_datetime([row[fields["start_ms"]] for row in core["tracks"]], unit="ms", utc=True)

	bsiso = pd.read_csv(args.bsiso, sep=r"\s+")
	bsiso_dates = pd.to_datetime(bsiso["YEAR"].astype(str), format="%Y") + pd.to_timedelta(bsiso["DAY"] - 1, unit="D")
	bsiso_lookup = {
		date.date(): (float(pc1), float(pc2), float(amplitude))
		for date, pc1, pc2, amplitude in zip(bsiso_dates, bsiso["BSISO1-1"], bsiso["BSISO1-2"], bsiso["BSISO1"])
	}
	bsiso_phases: list[int] = []
	bsiso_amplitudes: list[int] = []
	for value in genesis:
		item = bsiso_lookup.get(value.date()) if 5 <= value.month <= 10 else None
		if item is None or not all(np.isfinite(item)) or any(abs(component) > 20 for component in item) or item[2] < 0:
			bsiso_phases.append(-1)
			bsiso_amplitudes.append(-1)
			continue
		pc1, pc2, amplitude = item
		bsiso_amplitudes.append(int(round(amplitude * 100)))
		bsiso_phases.append(0 if amplitude < 1.0 else bsiso_phase(pc1, pc2))

	rmm = read_rmm(args.rmm)
	rmm_lookup = {
		date.date(): (float(rmm1), float(rmm2), int(phase), float(amplitude))
		for date, rmm1, rmm2, phase, amplitude in zip(
			rmm.loc[rmm["valid"], "date"],
			rmm.loc[rmm["valid"], "rmm1"],
			rmm.loc[rmm["valid"], "rmm2"],
			rmm.loc[rmm["valid"], "phase"],
			rmm.loc[rmm["valid"], "amplitude"],
		)
	}
	rmm_phases: list[int] = []
	rmm_amplitudes: list[int] = []
	rmm1_values: list[int] = []
	rmm2_values: list[int] = []
	for value in genesis:
		item = rmm_lookup.get(value.date())
		if item is None:
			rmm_phases.append(-1)
			rmm_amplitudes.append(-1)
			rmm1_values.append(-32768)
			rmm2_values.append(-32768)
			continue
		rmm1, rmm2, phase, amplitude = item
		rmm_phases.append(0 if amplitude < 1.0 else phase)
		rmm_amplitudes.append(int(round(amplitude * 100)))
		rmm1_values.append(int(round(rmm1 * 100)))
		rmm2_values.append(int(round(rmm2 * 100)))

	oni = pd.read_csv(args.oni, sep=r"\s+")
	oni_lookup = {(int(year), str(season)): float(anomaly) for season, year, anomaly in zip(oni["SEAS"], oni["YR"], oni["ANOM"])}
	enso_classes: list[int] = []
	oni_values: list[int] = []
	for value in genesis:
		anomaly = oni_lookup.get((value.year, ONI_SEASONS[value.month - 1]))
		if anomaly is None or not np.isfinite(anomaly):
			enso_classes.append(-1)
			oni_values.append(-32768)
			continue
		oni_values.append(int(round(anomaly * 100)))
		enso_classes.append(0 if anomaly <= -0.5 else 2 if anomaly >= 0.5 else 1)

	payload = {
		"schema": "monsoon-low-atlas-climate-filters-v2",
		"track_count": len(core["tracks"]),
		"grain": "one value per physical event, evaluated at its genesis UTC date",
		"bsiso": {
			"source": "APCC BSISO monitoring index",
			"url": BSISO_URL,
			"source_sha256": sha256(args.bsiso),
			"definition": "BSISO1 phase on the genesis day during May-October; phase 0 means amplitude below 1; -1 means unavailable or outside May-October",
			"phase": bsiso_phases,
			"amplitude_x100": bsiso_amplitudes,
		},
		"mjo": {
			"source": "Australian Bureau of Meteorology all-season Real-time Multivariate MJO index",
			"url": RMM_URL,
			"source_sha256": sha256(args.rmm),
			"source_first_date": rmm["date"].iloc[0].date().isoformat(),
			"source_last_date": rmm["date"].iloc[-1].date().isoformat(),
			"definition": "Wheeler-Hendon RMM phase on the genesis UTC day; phase 0 means amplitude below 1 and -1 means unavailable, including dates before 1974-06-01 and the documented 1978 satellite gap",
			"phase": rmm_phases,
			"amplitude_x100": rmm_amplitudes,
			"rmm1_x100": rmm1_values,
			"rmm2_x100": rmm2_values,
		},
		"enso": {
			"source": "NOAA CPC Oceanic Nino Index",
			"url": ONI_URL,
			"source_sha256": sha256(args.oni),
			"definition": "three-month ONI anomaly centred on the genesis month; class 0 is La Nina (<= -0.5 C), 1 neutral, 2 El Nino (>= 0.5 C), and -1 unavailable",
			"class": enso_classes,
			"oni_x100": oni_values,
		},
	}
	encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
	compressed = gzip.compress(encoded, compresslevel=9, mtime=0)
	digest = hashlib.sha256(compressed).hexdigest()
	args.output_dir.mkdir(parents=True, exist_ok=True)
	destination = args.output_dir / f"atlas-climate.{digest[:12]}.json.gz"
	destination.write_bytes(compressed)
	destination.chmod(0o644)
	print(json.dumps({"path": str(destination), "sha256": digest, "bytes": len(compressed), "tracks": len(core["tracks"])}, indent=2))


if __name__ == "__main__":
	main()
