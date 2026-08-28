#!/usr/bin/env python3
"""Build compact monthly ERA5 weather videos for the atlas.

Each ERA5 hour is encoded as one video frame.  The atlas uses a fixed frame
rate to map UTC hours to video time, which lets the track-hour slider seek to
individual fields without publishing hundreds of thousands of images.

Vorticity and precipitation remain at 0.25 degrees. Locally derived RH500 is
coarsened to 1 degree. Precipitation is a trailing 24-hour accumulation using
the preceding month's final 23 hours at month boundaries. The 3-hourly
pressure-level inputs used for RH500 are linearly interpolated to hourly
values, with the final analysis held to 23 UTC.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import xarray as xr


DEFAULT_BOUNDS = (50.0, -6.0, 110.0, 40.0)  # west, south, east, north
DEFAULT_FPS = 6
RASTER_BOUNDS = (49.875, -5.875, 109.875, 40.125)
FIELD_SPECS = {
	"vorticity": {
		"source_dir": Path("/home/users/kieran/ncas/data/era5-incompass/hourly_vorts_SA"),
		"schema": "monsoon-low-atlas-vorticity-video-v3",
		"source_label": "ERA5 hourly_vorts_SA",
		"field_label": "ERA5 850-hPa relative vorticity",
		"units": "1e-5 s-1",
		"grid_degrees": 0.25,
		"coarsen_factor": 1,
		"dimensions": (240, 184),
		"positive_only": True,
		"colour_stops": (
			(0.0, (49, 54, 149)), (4.0, (69, 117, 180)),
			(8.0, (145, 191, 219)), (12.0, (247, 247, 247)),
			(18.0, (214, 96, 77)), (30.0, (103, 0, 31)),
		),
		"alpha_full": 6.0,
	},
	"precipitation": {
		"source_dir": Path("/home/users/kieran/ncas/data/era5-incompass/hourly_precip_SA"),
		"schema": "monsoon-low-atlas-precipitation-video-v3",
		"source_label": "ERA5 hourly_precip_SA",
		"field_label": "ERA5 trailing 24-hour accumulated precipitation",
		"units": "mm",
		"grid_degrees": 0.25,
		"coarsen_factor": 1,
		"dimensions": (240, 184),
		"positive_only": True,
		"colour_stops": (
			(0.0, (247, 252, 253)), (1.0, (204, 236, 230)),
			(5.0, (102, 194, 164)), (10.0, (35, 139, 69)),
			(25.0, (34, 94, 168)), (50.0, (84, 39, 143)),
			(100.0, (62, 0, 92)), (150.0, (46, 0, 72)),
		),
		"alpha_full": 10.0,
		"alpha_threshold": 0.1,
	},
	"rh500": {
		"source_dir": Path("/home/users/kieran/ncas/data/era5-incompass/3hourly_pl_SA"),
		"schema": "monsoon-low-atlas-rh500-video-v1",
		"source_label": "ERA5 3hourly_pl_SA",
		"field_label": "ERA5 500-hPa relative humidity derived from temperature and specific humidity",
		"units": "%",
		"grid_degrees": 1.0,
		"coarsen_factor": 4,
		"dimensions": (60, 46),
		"positive_only": False,
		"colour_stops": (
			(0.0, (246, 239, 247)), (20.0, (212, 185, 218)),
			(40.0, (158, 154, 200)), (60.0, (106, 81, 163)),
			(80.0, (84, 39, 143)), (100.0, (63, 0, 125)),
		),
	},
}


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser()
	parser.add_argument("--field", choices=tuple(FIELD_SPECS), default="vorticity")
	parser.add_argument("--source-dir", type=Path)
	parser.add_argument("--output-dir", type=Path, required=True)
	parser.add_argument("--month", help="Month to render as YYYYMM")
	parser.add_argument("--month-manifest", type=Path)
	parser.add_argument("--task-id", type=int)
	parser.add_argument("--catalogue", type=Path, help="Released Parquet catalogue used to build the active-month manifest")
	parser.add_argument("--write-month-manifest", type=Path)
	parser.add_argument("--fps", type=int, default=DEFAULT_FPS)
	parser.add_argument("--container", choices=("webm", "mp4"), default="webm")
	parser.add_argument("--ffmpeg", default="ffmpeg")
	parser.add_argument("--overwrite", action="store_true")
	parser.add_argument("--finalize", action="store_true", help="Validate every listed month and write the public archive manifest")
	return parser.parse_args()


def sha256(path: Path) -> str:
	digest = hashlib.sha256()
	with path.open("rb") as stream:
		for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
			digest.update(block)
	return digest.hexdigest()


def write_active_months(catalogue: Path, destination: Path) -> None:
	table = pq.read_table(catalogue, columns=["time"])
	times = table.column("time").to_pandas()
	months = sorted(pd.DatetimeIndex(times).strftime("%Y%m").unique())
	destination.parent.mkdir(parents=True, exist_ok=True)
	destination.write_text("yyyymm\n" + "\n".join(months) + "\n", encoding="utf-8")
	print(json.dumps({"manifest": str(destination), "active_months": len(months)}, indent=2))


def resolve_month(args: argparse.Namespace) -> str:
	if args.month:
		month = args.month
	elif args.month_manifest is not None and args.task_id is not None:
		manifest = pd.read_csv(args.month_manifest, dtype={"yyyymm": str})
		if args.task_id < 0 or args.task_id >= len(manifest):
			raise IndexError(f"task ID {args.task_id} outside month manifest with {len(manifest)} rows")
		month = str(manifest.iloc[args.task_id]["yyyymm"])
	else:
		raise ValueError("Provide --month or both --month-manifest and --task-id")
	if len(month) != 6 or not month.isdigit() or not 1 <= int(month[4:]) <= 12:
		raise ValueError(f"Invalid YYYYMM month: {month}")
	return month


def select_850_vorticity(dataset: xr.Dataset) -> xr.DataArray:
	for name in ("vo", "relative_vorticity", "vorticity", "atmosphere_relative_vorticity"):
		if name in dataset:
			field = dataset[name]
			break
	else:
		raise KeyError(f"No relative-vorticity variable in {list(dataset.data_vars)}")
	for coordinate in ("level", "pressure_level", "isobaricInhPa"):
		if coordinate in field.coords or coordinate in field.dims:
			field = field.sel({coordinate: 850}, method="nearest")
			break
	if "time" not in field.coords and "time" not in field.dims:
		for coordinate in ("valid_time", "forecast_time"):
			if coordinate in field.coords or coordinate in field.dims:
				field = field.rename({coordinate: "time"})
				break
	if "time" not in field.coords and "time" not in field.dims:
		raise KeyError(f"No hourly time coordinate in vorticity field: {list(field.coords)}")
	units = str(field.attrs.get("units", "")).lower()
	if "s" in units:
		field = field * np.float32(1.0e5)
	return field


def select_precipitation(dataset: xr.Dataset) -> xr.DataArray:
	for name in ("mtpr", "avg_tprate", "total_precipitation_rate", "tp"):
		if name in dataset:
			field = dataset[name]
			break
	else:
		raise KeyError(f"No precipitation variable in {list(dataset.data_vars)}")
	units = str(field.attrs.get("units", "")).lower()
	if "kg" in units and "s" in units:
		field = field * np.float32(3600.0)
	elif units.strip() in {"m", "metre", "meter"}:
		field = field * np.float32(1000.0)
	return field.clip(min=0)


def select_rh500(dataset: xr.Dataset) -> xr.DataArray:
	if "q" not in dataset or "t" not in dataset:
		raise KeyError(f"RH500 requires q and t; found {list(dataset.data_vars)}")
	coordinate = next((name for name in ("level", "pressure_level", "isobaricInhPa") if name in dataset.coords or name in dataset.dims), None)
	if coordinate is None:
		raise KeyError(f"No pressure coordinate in {list(dataset.coords)}")
	q = dataset["q"].sel({coordinate: 500}, method="nearest")
	temperature = dataset["t"].sel({coordinate: 500}, method="nearest")
	# Vapour pressure from specific humidity, then an IFS-style mixed-phase
	# saturation pressure (water above 0 C, ice below -23 C, quadratic blend).
	epsilon = np.float32(0.622)
	vapour_pressure = q * np.float32(50000.0) / (epsilon + (np.float32(1.0) - epsilon) * q)
	tc = temperature - np.float32(273.15)
	es_water = np.float32(611.21) * np.exp((np.float32(18.678) - tc / np.float32(234.5)) * (tc / (np.float32(257.14) + tc)))
	es_ice = np.float32(611.15) * np.exp((np.float32(23.036) - tc / np.float32(333.7)) * (tc / (np.float32(279.82) + tc)))
	weight = ((temperature - np.float32(250.16)) / np.float32(23.0)).clip(min=0, max=1) ** 2
	saturation_pressure = weight * es_water + (np.float32(1.0) - weight) * es_ice
	return (np.float32(100.0) * vapour_pressure / saturation_pressure).clip(min=0, max=100).rename("rh500")


def select_weather_field(dataset: xr.Dataset, field_name: str) -> xr.DataArray:
	if field_name == "vorticity":
		field = select_850_vorticity(dataset)
	elif field_name == "precipitation":
		field = select_precipitation(dataset)
	else:
		field = select_rh500(dataset)
	if "time" not in field.coords and "time" not in field.dims:
		for coordinate in ("valid_time", "forecast_time"):
			if coordinate in field.coords or coordinate in field.dims:
				field = field.rename({coordinate: "time"})
				break
	if "time" not in field.coords and "time" not in field.dims:
		raise KeyError(f"No time coordinate in {field_name} field: {list(field.coords)}")
	return field


def interpolate_hourly(field: xr.DataArray, month: str) -> xr.DataArray:
	start = pd.Timestamp(year=int(month[:4]), month=int(month[4:]), day=1)
	end = start + pd.offsets.MonthEnd(1) + pd.Timedelta(hours=23)
	target = pd.date_range(start, end, freq="h").to_numpy(dtype="datetime64[ns]")
	last_time = pd.Timestamp(field.time.values[-1])
	if last_time < end:
		tail = field.isel(time=-1).expand_dims(time=[end.to_datetime64()])
		field = xr.concat((field, tail), dim="time")
	return field.interp(time=target)


def trailing_24_hour_sum(field: xr.DataArray, current_times: np.ndarray) -> xr.DataArray:
	"""Return native-grid trailing sums without materialising a 24-step window.

	The input begins with the preceding month's final 23 hours. An in-place
	cumulative sum keeps native 0.25-degree processing comfortably within one
	Slurm task's memory while remaining numerically equivalent to rolling(24).
	"""
	field = field.transpose("time", "latitude", "longitude").astype(np.float32).load()
	values = np.asarray(field.values)
	if values.shape[0] != len(current_times) + 23:
		raise ValueError(
			f"Trailing precipitation expected {len(current_times) + 23} input hours, "
			f"found {values.shape[0]}"
		)
	np.cumsum(values, axis=0, dtype=np.float32, out=values)
	trailing = values[23:].copy()
	trailing[1:] -= values[:-24]
	return xr.DataArray(
		trailing,
		dims=("time", "latitude", "longitude"),
		coords={
			"time": current_times,
			"latitude": field.latitude.values,
			"longitude": field.longitude.values,
		},
		attrs=field.attrs,
		name=field.name,
	)


def atlas_field(source: Path, field_name: str, month: str) -> tuple[xr.DataArray, tuple[float, float, float, float], list[Path]]:
	datasets = [xr.open_dataset(source)]
	field = select_weather_field(datasets[0], field_name)
	sources = [source]
	if field_name == "precipitation":
		previous_month = (pd.Period(month, freq="M") - 1).strftime("%Y%m")
		previous_source = source.parent / f"{previous_month}.nc"
		if not previous_source.is_file():
			datasets[0].close()
			raise FileNotFoundError(f"Trailing 24-hour precipitation requires {previous_source}")
		datasets.append(xr.open_dataset(previous_source))
		previous = select_weather_field(datasets[-1], field_name).isel(time=slice(-23, None))
		current_times = np.asarray(field.time.values)
		field = xr.concat((previous, field), dim="time").sortby("time")
		_, unique_indexes = np.unique(np.asarray(field.time.values), return_index=True)
		field = field.isel(time=np.sort(unique_indexes))
		sources.append(previous_source)
	spec = FIELD_SPECS[field_name]
	west, south, east, north = DEFAULT_BOUNDS
	latitudes = np.asarray(field.latitude.values)
	longitudes = np.asarray(field.longitude.values)
	lat_index = np.flatnonzero((latitudes >= south) & (latitudes <= north))
	lon_index = np.flatnonzero((longitudes >= west) & (longitudes <= east))
	if not len(lat_index) or not len(lon_index):
		for dataset in datasets:
			dataset.close()
		raise ValueError("Requested atlas bounds do not intersect the ERA5 grid")
	field = field.isel(latitude=lat_index, longitude=lon_index)
	coarsen_factor = int(spec["coarsen_factor"])
	if coarsen_factor > 1:
		field = field.coarsen(latitude=coarsen_factor, longitude=coarsen_factor, boundary="trim").mean()
	else:
		# Drop the duplicated far edge of the inclusive source subset so video
		# dimensions remain even for broadly supported YUV 4:2:0 decoding.
		field = field.isel(
			latitude=slice(0, field.sizes["latitude"] // 2 * 2),
			longitude=slice(0, field.sizes["longitude"] // 2 * 2),
		)
	latitude = np.asarray(field.latitude.values, dtype=float)
	longitude = np.asarray(field.longitude.values, dtype=float)
	if latitude[0] < latitude[-1]:
		field = field.isel(latitude=slice(None, None, -1))
		latitude = latitude[::-1]
	if longitude[0] > longitude[-1]:
		field = field.isel(longitude=slice(None, None, -1))
		longitude = longitude[::-1]
	lat_step = float(abs(np.median(np.diff(latitude))))
	lon_step = float(abs(np.median(np.diff(longitude))))
	raster_bounds = (
		float(longitude[0] - lon_step / 2),
		float(latitude[-1] - lat_step / 2),
		float(longitude[-1] + lon_step / 2),
		float(latitude[0] + lat_step / 2),
	)
	if field_name == "rh500":
		field = interpolate_hourly(field, month).clip(min=0, max=100)
		field.load()
	elif field_name == "precipitation":
		field = trailing_24_hour_sum(field, current_times)
	else:
		field.load()
	for dataset in datasets:
		dataset.close()
	return field, raster_bounds, sources


def colourise(values: np.ndarray, spec: dict) -> np.ndarray:
	values = np.asarray(values, dtype=np.float32)
	finite = np.isfinite(values)
	threshold = float(spec.get("alpha_threshold", 0.0))
	visible = finite & (values > threshold) if spec["positive_only"] else finite
	values = np.where(finite, values, 0)
	colour_stops = spec["colour_stops"]
	stop_values = np.asarray([item[0] for item in colour_stops], dtype=np.float32)
	stop_colours = np.asarray([item[1] for item in colour_stops], dtype=np.float32)
	channels = [np.interp(values, stop_values, stop_colours[:, channel]) for channel in range(3)]
	rgb = np.clip(np.stack(channels, axis=-1), 0, 255).astype(np.uint8)
	if spec["positive_only"]:
		alpha = np.where(visible, np.clip(values / float(spec["alpha_full"]), 0, 1) * 255, 0).astype(np.uint8)
	else:
		alpha = np.where(visible, 90 + np.clip(values / 100.0, 0, 1) * 145, 0).astype(np.uint8)
	mask = np.repeat(alpha[..., None], 3, axis=-1)
	return np.concatenate((rgb, mask), axis=1)


def render_month(args: argparse.Namespace, month: str) -> None:
	spec = FIELD_SPECS[args.field]
	source_dir = args.source_dir or spec["source_dir"]
	source = source_dir / f"{month}.nc"
	if not source.is_file():
		raise FileNotFoundError(source)
	destination = args.output_dir / args.field / month[:4] / f"{month}.{args.container}"
	metadata_path = destination.with_suffix(".json")
	destination.parent.mkdir(parents=True, exist_ok=True)
	for directory in (args.output_dir, args.output_dir / args.field, destination.parent):
		directory.chmod(0o2755)
	if destination.is_file() and metadata_path.is_file() and not args.overwrite:
		metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
		if (
			metadata.get("sha256") == sha256(destination)
			and metadata.get("schema") == spec["schema"]
			and metadata.get("grid_degrees") == spec["grid_degrees"]
			and metadata.get("mask_layout") == "right-half-luma"
		):
			print(f"{month}: already complete")
			return
	temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
	field, raster_bounds, sources = atlas_field(source, args.field, month)
	times = pd.DatetimeIndex(field.time.values)
	height = int(field.sizes["latitude"])
	width = int(field.sizes["longitude"])
	encoded_width = width * 2
	if width % 2 or height % 2:
		field.close()
		raise ValueError(f"YUV 4:2:0 video requires even dimensions, got {width}x{height}")
	command = [
		args.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
		"-f", "rawvideo", "-pix_fmt", "rgb24", "-s:v", f"{encoded_width}x{height}",
		"-r", str(args.fps), "-i", "-", "-an",
	]
	if args.container == "webm":
		command.extend([
			"-c:v", "libvpx-vp9", "-crf", "26", "-b:v", "0", "-deadline", "good", "-cpu-used", "2",
			"-pix_fmt", "yuv420p", "-g", str(args.fps), "-f", "webm", str(temporary),
		])
	else:
		command.extend([
			"-c:v", "libx264", "-preset", "slow", "-crf", "21", "-pix_fmt", "yuv420p",
			"-g", str(args.fps * 2), "-keyint_min", str(args.fps * 2),
			"-sc_threshold", "0", "-movflags", "+faststart", "-f", "mp4", str(temporary),
		])
	process = subprocess.Popen(command, stdin=subprocess.PIPE)
	try:
		assert process.stdin is not None
		for index in range(len(times)):
			frame = colourise(field.isel(time=index).values, spec)
			process.stdin.write(frame.tobytes(order="C"))
		process.stdin.close()
		return_code = process.wait()
		if return_code:
			raise subprocess.CalledProcessError(return_code, command)
		os.replace(temporary, destination)
		destination.chmod(0o644)
	except Exception:
		if process.stdin and not process.stdin.closed:
			try:
				process.stdin.close()
			except BrokenPipeError:
				pass
		process.kill()
		process.wait()
		temporary.unlink(missing_ok=True)
		raise
	finally:
		field.close()
	metadata = {
		"schema": spec["schema"],
		"month": month,
		"source": f"{spec['source_label']}/{source.name}",
		"source_sha256": sha256(source),
		"field": spec["field_label"],
		"units": spec["units"],
		"grid_degrees": spec["grid_degrees"],
		"positive_only": spec["positive_only"],
		"mask_layout": "right-half-luma",
		"mask": "left half is the colour field; right-half luma is opacity",
		"bounds_west_south_east_north": list(raster_bounds),
		"width": width,
		"height": height,
		"encoded_width": encoded_width,
		"first_time_utc": times[0].isoformat().replace("+00:00", "") + "Z",
		"last_time_utc": times[-1].isoformat().replace("+00:00", "") + "Z",
		"frames": len(times),
		"frames_per_second": args.fps,
		"container": args.container,
		"colour_stops": [{"value": value, "rgb": list(colour)} for value, colour in spec["colour_stops"]],
		"sha256": sha256(destination),
		"bytes": destination.stat().st_size,
		"built_utc": datetime.now(timezone.utc).isoformat(),
	}
	if args.field == "rh500":
		metadata["temporal_processing"] = "3-hourly ERA5 analyses linearly interpolated to hourly values; the final 21 UTC analysis is held through 23 UTC"
		metadata["derivation"] = "relative humidity derived from q and t at 500 hPa using mixed-phase saturation vapour pressure"
	elif args.field == "precipitation":
		metadata["temporal_processing"] = "trailing sum of 24 hourly mean precipitation-rate amounts; each month includes the previous month's final 23 hours before accumulation"
		metadata["source_previous"] = f"{spec['source_label']}/{sources[1].name}"
		metadata["source_previous_sha256"] = sha256(sources[1])
	metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
	metadata_path.chmod(0o644)
	print(json.dumps(metadata, indent=2, sort_keys=True))


def finalize_archive(args: argparse.Namespace) -> None:
	if args.month_manifest is None:
		raise ValueError("--month-manifest is required with --finalize")
	spec = FIELD_SPECS[args.field]
	for directory in (args.output_dir, args.output_dir / args.field):
		directory.chmod(0o2755)
	month_table = pd.read_csv(args.month_manifest, dtype={"yyyymm": str})
	months = [str(value) for value in month_table["yyyymm"]]
	if not months or len(set(months)) != len(months):
		raise ValueError("The active-month manifest is empty or contains duplicates")
	entries = []
	checksum_lines = []
	for month in months:
		year, month_number = int(month[:4]), int(month[4:])
		video = args.output_dir / args.field / month[:4] / f"{month}.{args.container}"
		metadata_path = video.with_suffix(".json")
		if not video.is_file() or not metadata_path.is_file():
			raise FileNotFoundError(f"Incomplete weather month {month}: expected {video} and {metadata_path}")
		metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
		expected_frames = calendar.monthrange(year, month_number)[1] * 24
		expected_first = f"{year:04d}-{month_number:02d}-01T00:00:00.000000000Z"
		expected_last = (pd.Timestamp(year=year, month=month_number, day=1, tz="UTC") + pd.offsets.MonthEnd(1) + pd.Timedelta(hours=23)).isoformat().replace("+00:00", "Z")
		actual_sha = sha256(video)
		checks = {
			"schema": metadata.get("schema") == spec["schema"],
			"month": metadata.get("month") == month,
			"frames": metadata.get("frames") == expected_frames,
			"fps": metadata.get("frames_per_second") == args.fps,
			"container": metadata.get("container") == args.container,
			"grid": metadata.get("grid_degrees") == spec["grid_degrees"],
			"dimensions": (metadata.get("width"), metadata.get("height")) == spec["dimensions"],
			"encoded_dimensions": (metadata.get("encoded_width"), metadata.get("height")) == (spec["dimensions"][0] * 2, spec["dimensions"][1]),
			"positive_only": metadata.get("positive_only") is spec["positive_only"],
			"mask": metadata.get("mask_layout") == "right-half-luma",
			"bounds": metadata.get("bounds_west_south_east_north") == list(RASTER_BOUNDS),
			"last_time": metadata.get("last_time_utc") == expected_last,
			"bytes": metadata.get("bytes") == video.stat().st_size,
			"sha256": metadata.get("sha256") == actual_sha,
		}
		# NumPy datetime strings include nanoseconds; accept the equivalent ISO first hour.
		checks["first_time"] = pd.Timestamp(metadata.get("first_time_utc")) == pd.Timestamp(expected_first)
		failed = [name for name, passed in checks.items() if not passed]
		if failed:
			raise ValueError(f"Weather month {month} failed: {', '.join(failed)}")
		metadata["source"] = f"{spec['source_label']}/{month}.nc"
		metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
		video.parent.chmod(0o2755)
		video.chmod(0o644)
		metadata_path.chmod(0o644)
		relative_video = video.relative_to(args.output_dir).as_posix()
		relative_metadata = metadata_path.relative_to(args.output_dir).as_posix()
		metadata_sha = sha256(metadata_path)
		entries.append({
			"month": month,
			"url": relative_video,
			"metadata_url": relative_metadata,
			"first_time_utc": metadata["first_time_utc"],
			"last_time_utc": metadata["last_time_utc"],
			"frames": expected_frames,
			"bytes": video.stat().st_size,
			"sha256": actual_sha,
			"metadata_bytes": metadata_path.stat().st_size,
			"metadata_sha256": metadata_sha,
		})
		checksum_lines.append(f"{actual_sha}  {relative_video}")
		checksum_lines.append(f"{metadata_sha}  {relative_metadata}")
	manifest = {
		"schema": "monsoon-low-atlas-weather-archive-v4",
		"field_key": args.field,
		"field": spec["field_label"],
		"units": spec["units"],
		"grid_degrees": spec["grid_degrees"],
		"positive_only": spec["positive_only"],
		"mask_layout": "right-half-luma",
		"colour_stops": [{"value": value, "rgb": list(colour)} for value, colour in spec["colour_stops"]],
		"bounds_west_south_east_north": list(RASTER_BOUNDS),
		"frames_per_second": args.fps,
		"container": args.container,
		"active_months": len(entries),
		"total_frames": sum(item["frames"] for item in entries),
		"total_video_bytes": sum(item["bytes"] for item in entries),
		"built_utc": datetime.now(timezone.utc).isoformat(),
		"months": entries,
	}
	manifest_name = "manifest.json" if args.field == "vorticity" else f"{args.field}-manifest.json"
	checksums_name = "checksums.sha256" if args.field == "vorticity" else f"{args.field}-checksums.sha256"
	manifest_path = args.output_dir / manifest_name
	checksums_path = args.output_dir / checksums_name
	manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
	checksums_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
	manifest_path.chmod(0o644)
	checksums_path.chmod(0o644)
	print(json.dumps({key: value for key, value in manifest.items() if key != "months"}, indent=2, sort_keys=True))


def main() -> None:
	args = parse_args()
	if args.write_month_manifest is not None:
		if args.catalogue is None:
			raise ValueError("--catalogue is required with --write-month-manifest")
		write_active_months(args.catalogue, args.write_month_manifest)
		return
	if args.finalize:
		finalize_archive(args)
		return
	month = resolve_month(args)
	render_month(args, month)


if __name__ == "__main__":
	main()
