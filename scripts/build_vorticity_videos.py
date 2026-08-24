#!/usr/bin/env python3
"""Build compact monthly 1-degree 850-hPa vorticity videos for the atlas.

Each ERA5 hour is encoded as one video frame.  The atlas uses a fixed frame
rate to map UTC hours to video time, which makes point selection and playback
possible without publishing hundreds of thousands of individual images.
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


DEFAULT_SOURCE_DIR = Path("/home/users/kieran/ncas/data/era5-incompass/hourly_vorts_SA")
DEFAULT_BOUNDS = (50.0, -6.0, 110.0, 40.0)  # west, south, east, north
DEFAULT_FPS = 6
COLOUR_STOPS = (
	(-5.0, (45, 91, 128)),
	(0.0, (235, 241, 229)),
	(10.0, (229, 202, 66)),
	(20.0, (215, 105, 43)),
	(40.0, (126, 29, 67)),
)


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser()
	parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
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


def one_degree_field(source: Path) -> tuple[xr.DataArray, tuple[float, float, float, float]]:
	dataset = xr.open_dataset(source)
	field = select_850_vorticity(dataset)
	west, south, east, north = DEFAULT_BOUNDS
	latitudes = np.asarray(field.latitude.values)
	longitudes = np.asarray(field.longitude.values)
	lat_index = np.flatnonzero((latitudes >= south) & (latitudes <= north))
	lon_index = np.flatnonzero((longitudes >= west) & (longitudes <= east))
	if not len(lat_index) or not len(lon_index):
		dataset.close()
		raise ValueError("Requested atlas bounds do not intersect the ERA5 grid")
	field = field.isel(latitude=lat_index, longitude=lon_index)
	field = field.coarsen(latitude=4, longitude=4, boundary="trim").mean()
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
	return field, raster_bounds


def colourise(values: np.ndarray) -> np.ndarray:
	values = np.nan_to_num(np.asarray(values, dtype=np.float32), nan=COLOUR_STOPS[0][0])
	stop_values = np.asarray([item[0] for item in COLOUR_STOPS], dtype=np.float32)
	stop_colours = np.asarray([item[1] for item in COLOUR_STOPS], dtype=np.float32)
	channels = [np.interp(values, stop_values, stop_colours[:, channel]) for channel in range(3)]
	return np.clip(np.stack(channels, axis=-1), 0, 255).astype(np.uint8)


def render_month(args: argparse.Namespace, month: str) -> None:
	source = args.source_dir / f"{month}.nc"
	if not source.is_file():
		raise FileNotFoundError(source)
	destination = args.output_dir / "vorticity" / month[:4] / f"{month}.{args.container}"
	metadata_path = destination.with_suffix(".json")
	if destination.is_file() and metadata_path.is_file() and not args.overwrite:
		metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
		if metadata.get("sha256") == sha256(destination):
			print(f"{month}: already complete")
			return
	destination.parent.mkdir(parents=True, exist_ok=True)
	temporary = destination.with_name(f".{destination.name}.tmp-{os.getpid()}")
	field, raster_bounds = one_degree_field(source)
	times = pd.DatetimeIndex(field.time.values)
	height = int(field.sizes["latitude"])
	width = int(field.sizes["longitude"])
	if width % 2 or height % 2:
		field.close()
		raise ValueError(f"H.264 yuv420p requires even dimensions, got {width}x{height}")
	command = [
		args.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
		"-f", "rawvideo", "-pix_fmt", "rgb24", "-s:v", f"{width}x{height}",
		"-r", str(args.fps), "-i", "-", "-an",
	]
	if args.container == "webm":
		command.extend([
			"-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-deadline", "good", "-cpu-used", "2",
			"-pix_fmt", "yuv420p", "-g", str(args.fps * 2), "-f", "webm", str(temporary),
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
			frame = colourise(field.isel(time=index).values)
			process.stdin.write(frame.tobytes(order="C"))
		process.stdin.close()
		return_code = process.wait()
		if return_code:
			raise subprocess.CalledProcessError(return_code, command)
		os.replace(temporary, destination)
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
		"schema": "monsoon-low-atlas-vorticity-video-v1",
		"month": month,
		"source": f"ERA5 hourly_vorts_SA/{source.name}",
		"source_sha256": sha256(source),
		"field": "ERA5 850-hPa relative vorticity",
		"units": "1e-5 s-1",
		"grid_degrees": 1.0,
		"bounds_west_south_east_north": list(raster_bounds),
		"width": width,
		"height": height,
		"first_time_utc": times[0].isoformat().replace("+00:00", "") + "Z",
		"last_time_utc": times[-1].isoformat().replace("+00:00", "") + "Z",
		"frames": len(times),
		"frames_per_second": args.fps,
		"container": args.container,
		"colour_stops": [{"value": value, "rgb": list(colour)} for value, colour in COLOUR_STOPS],
		"sha256": sha256(destination),
		"bytes": destination.stat().st_size,
		"built_utc": datetime.now(timezone.utc).isoformat(),
	}
	metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
	print(json.dumps(metadata, indent=2, sort_keys=True))


def finalize_archive(args: argparse.Namespace) -> None:
	if args.month_manifest is None:
		raise ValueError("--month-manifest is required with --finalize")
	month_table = pd.read_csv(args.month_manifest, dtype={"yyyymm": str})
	months = [str(value) for value in month_table["yyyymm"]]
	if not months or len(set(months)) != len(months):
		raise ValueError("The active-month manifest is empty or contains duplicates")
	entries = []
	checksum_lines = []
	for month in months:
		year, month_number = int(month[:4]), int(month[4:])
		video = args.output_dir / "vorticity" / month[:4] / f"{month}.{args.container}"
		metadata_path = video.with_suffix(".json")
		if not video.is_file() or not metadata_path.is_file():
			raise FileNotFoundError(f"Incomplete weather month {month}: expected {video} and {metadata_path}")
		metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
		expected_frames = calendar.monthrange(year, month_number)[1] * 24
		expected_first = f"{year:04d}-{month_number:02d}-01T00:00:00.000000000Z"
		expected_last = (pd.Timestamp(year=year, month=month_number, day=1, tz="UTC") + pd.offsets.MonthEnd(1) + pd.Timedelta(hours=23)).isoformat().replace("+00:00", "Z")
		actual_sha = sha256(video)
		checks = {
			"schema": metadata.get("schema") == "monsoon-low-atlas-vorticity-video-v1",
			"month": metadata.get("month") == month,
			"frames": metadata.get("frames") == expected_frames,
			"fps": metadata.get("frames_per_second") == args.fps,
			"container": metadata.get("container") == args.container,
			"grid": metadata.get("grid_degrees") == 1.0,
			"dimensions": (metadata.get("width"), metadata.get("height")) == (60, 46),
			"last_time": metadata.get("last_time_utc") == expected_last,
			"bytes": metadata.get("bytes") == video.stat().st_size,
			"sha256": metadata.get("sha256") == actual_sha,
		}
		# NumPy datetime strings include nanoseconds; accept the equivalent ISO first hour.
		checks["first_time"] = pd.Timestamp(metadata.get("first_time_utc")) == pd.Timestamp(expected_first)
		failed = [name for name, passed in checks.items() if not passed]
		if failed:
			raise ValueError(f"Weather month {month} failed: {', '.join(failed)}")
		metadata["source"] = f"ERA5 hourly_vorts_SA/{month}.nc"
		metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
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
		"schema": "monsoon-low-atlas-weather-archive-v1",
		"field": "ERA5 850-hPa relative vorticity",
		"units": "1e-5 s-1",
		"grid_degrees": 1.0,
		"bounds_west_south_east_north": [49.875, -5.875, 109.875, 40.125],
		"frames_per_second": args.fps,
		"container": args.container,
		"active_months": len(entries),
		"total_frames": sum(item["frames"] for item in entries),
		"total_video_bytes": sum(item["bytes"] for item in entries),
		"built_utc": datetime.now(timezone.utc).isoformat(),
		"months": entries,
	}
	(args.output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
	(args.output_dir / "checksums.sha256").write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")
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
