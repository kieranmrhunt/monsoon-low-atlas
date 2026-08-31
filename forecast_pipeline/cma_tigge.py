#!/usr/bin/env python3
"""Authenticated client for the CMA synchronized TIGGE portal.

CMA historical retrieval is asynchronous: a request is first expanded into a
human-readable application, then submitted, staged, listed by folder and
downloaded.  This module preserves that workflow and a local state file so a
restarted job never silently resubmits the same recovery request.
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import shutil
import tarfile
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Iterable

from .forecast_core import atomic_write_json, iso_z, utc_now


DEFAULT_ROOT = "http://tigge.cma.cn"
DEFAULT_CONFIG = Path.home() / ".config/monsoon-low-atlas/cma-tigge.json"


class CmaTiggeError(RuntimeError):
    pass


@dataclass(frozen=True)
class CmaCredentials:
    username: str
    password: str

    @classmethod
    def load(cls, path: Path | None = None) -> "CmaCredentials":
        username = os.environ.get("LPS_CMA_TIGGE_USERNAME", "").strip()
        password = os.environ.get("LPS_CMA_TIGGE_PASSWORD", "")
        config_path = path or Path(os.environ.get("LPS_CMA_TIGGE_CONFIG", DEFAULT_CONFIG))
        if not username or not password:
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                config = {}
            except (OSError, json.JSONDecodeError) as error:
                raise CmaTiggeError(f"Could not read CMA credentials from {config_path}: {error}") from error
            username = username or str(config.get("username", "")).strip()
            password = password or str(config.get("password", ""))
        if not username or not password:
            raise CmaTiggeError(
                "CMA TIGGE credentials are required in LPS_CMA_TIGGE_USERNAME/"
                f"LPS_CMA_TIGGE_PASSWORD or {config_path}"
            )
        return cls(username=username, password=password)


class CmaTiggeClient:
    def __init__(
        self,
        credentials: CmaCredentials | None = None,
        *,
        root: str | None = None,
        timeout: int = 180,
        opener: Any | None = None,
    ):
        self.credentials = credentials
        self.root = (root or os.environ.get("LPS_CMA_TIGGE_ROOT", DEFAULT_ROOT)).rstrip("/")
        self.timeout = timeout
        self.cookies = CookieJar()
        self.opener = opener or urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies)
        )
        self.session_id: str | None = None

    def _url(self, path: str) -> str:
        return f"{self.root}/{path.lstrip('/')}"

    @staticmethod
    def _assert_success(payload: Any, path: str) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise CmaTiggeError(f"CMA endpoint {path} returned a non-object response")
        if int(payload.get("code", 0)) != 1 or payload.get("flag") is False:
            raise CmaTiggeError(f"CMA endpoint {path} failed: {payload.get('message', payload)}")
        return payload

    def _json(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "User-Agent": "monsoon-low-atlas-forecast/1.0",
        }
        data = None
        method = "GET"
        if payload is not None:
            method = "POST"
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.session_id:
            headers["Cookie"] = f"JSESSIONID={self.session_id}"
        request = urllib.request.Request(
            self._url(path), data=data, headers=headers, method=method
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                result = json.load(response)
        except Exception as error:
            raise CmaTiggeError(f"CMA request {path} failed: {error}") from error
        return self._assert_success(result, path)

    def login(self) -> dict[str, Any]:
        if self.credentials is None:
            raise CmaTiggeError("login requires CMA credentials")
        if not self.root.lower().startswith("https://") and os.environ.get("LPS_CMA_TIGGE_ALLOW_INSECURE") != "1":
            raise CmaTiggeError(
                "The current CMA portal exposes login over HTTP. Refusing to send credentials "
                "without explicit LPS_CMA_TIGGE_ALLOW_INSECURE=1 acknowledgement."
            )
        response = self._json(
            "/tigge/login",
            {"uname": self.credentials.username, "pwd": self.credentials.password},
        )
        nested = response.get("data", {})
        if isinstance(nested, dict) and int(nested.get("code", 0)) != 1:
            raise CmaTiggeError(f"CMA login failed: {nested.get('message', response.get('message'))}")
        session = nested.get("data", {}) if isinstance(nested, dict) else {}
        if isinstance(session, dict):
            self.session_id = str(session.get("JSESSIONID", "")).strip() or None
        return response

    def centres(self) -> list[dict[str, Any]]:
        return list(self._json("/tigge/findCenter").get("data", []))

    def parameters(self, level_type: int) -> list[dict[str, Any]]:
        return list(self._json("/tigge/findBylid", {"lid": int(level_type)}).get("data", []))

    def prepare_history(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._json("/tigge/getApplyHistoryRecordData", request)

    def submit_query(self, query_string: str) -> dict[str, Any]:
        return self._json("/tigge/applyHistoryRecord", {"queryString": query_string})

    def applications(self, page: int = 1, page_size: int = 100) -> dict[str, Any]:
        return self._json(
            "/tigge/getApplyHistoryRecordList",
            {"pageNum": int(page), "pageSize": int(page_size)},
        )

    def application_detail(self, application_id: str | int) -> dict[str, Any]:
        return self._json("/tigge/getApplyHistoryRecordDetail", {"id": application_id})

    def download_groups(self, application_id: str | int) -> dict[str, Any]:
        return self._json("/tigge/historyDownloadGroupList", {"id": application_id})

    def download_files(
        self,
        application_id: str | int,
        folder_id: str | int,
        *,
        page: int = 1,
        page_size: int = 1000,
    ) -> dict[str, Any]:
        return self._json(
            "/tigge/historyDownloadList",
            {
                "id": application_id,
                "pageNum": int(page),
                "pageSize": int(page_size),
                "folderId": folder_id,
                "centre": "",
                "time": "",
                "variableName": "",
            },
        )

    def download_file(self, file_id: str | int, target: Path) -> Path:
        query = urllib.parse.urlencode({"ids": file_id})
        headers = {"User-Agent": "monsoon-low-atlas-forecast/1.0"}
        if self.session_id:
            headers["Cookie"] = f"JSESSIONID={self.session_id}"
        request = urllib.request.Request(
            self._url(f"/tigge/download/downHistoryFile?{query}"), headers=headers
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f".{target.name}.partial")
        try:
            with self.opener.open(request, timeout=max(self.timeout, 900)) as response:
                with partial.open("wb") as stream:
                    while chunk := response.read(1024 * 1024):
                        stream.write(chunk)
            partial.replace(target)
        except Exception as error:
            partial.unlink(missing_ok=True)
            raise CmaTiggeError(f"CMA download {file_id} failed: {error}") from error
        return target


def _query_string(prepared: dict[str, Any]) -> str:
    data = prepared.get("data", {})
    value = data.get("queryString") if isinstance(data, dict) else None
    if not value:
        raise CmaTiggeError("CMA prepared request did not contain queryString")
    return str(value)


def _first_identifier(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in ("applyHistoryRecordId", "applicationId", "id"):
            if value.get(key) not in {None, ""}:
                return str(value[key])
        for child in value.values():
            found = _first_identifier(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _first_identifier(child)
            if found:
                return found
    return None


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema": "mla-cma-tigge-recovery-state-v1",
            "created_utc": iso_z(utc_now()),
            "submissions": {},
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != "mla-cma-tigge-recovery-state-v1":
        raise CmaTiggeError(f"Unsupported CMA state schema in {path}")
    return value


def submit_plan(plan_path: Path, state_path: Path, limit: int | None = None) -> dict[str, Any]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    requests = list(plan.get("requests", []))
    state = _load_state(state_path)
    client = CmaTiggeClient(CmaCredentials.load())
    client.login()
    submitted = 0
    for item in requests:
        key = str(item["key"])
        if key in state["submissions"]:
            continue
        if limit is not None and submitted >= limit:
            break
        prepared = client.prepare_history(dict(item["request"]))
        response = client.submit_query(_query_string(prepared))
        state["submissions"][key] = {
            "submitted_utc": iso_z(utc_now()),
            "application_id": _first_identifier(response),
            "model": item["model"],
            "cycle": item["cycle"],
            "component": item["component"],
            "response_message": response.get("message"),
        }
        state["updated_utc"] = iso_z(utc_now())
        atomic_write_json(state_path, state)
        submitted += 1
    state["last_submit_count"] = submitted
    state["updated_utc"] = iso_z(utc_now())
    atomic_write_json(state_path, state)
    return state


def _safe_filename(value: str, fallback: str) -> str:
    name = Path(value).name
    name = re.sub(r"[^A-Za-z0-9._+-]", "_", name)
    return name or fallback


def _safe_member(root: Path, name: str) -> Path:
    target = (root / name).resolve()
    if root.resolve() not in target.parents and target != root.resolve():
        raise CmaTiggeError(f"CMA archive contains unsafe path {name!r}")
    return target


def extract_download(path: Path) -> list[Path]:
    """Safely expose GRIB files from CMA's file or package response."""
    root = path.parent
    output: list[Path] = []
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                target = _safe_member(root, member.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                output.append(target)
        return output
    if tarfile.is_tarfile(path):
        with tarfile.open(path) as archive:
            for member in archive.getmembers():
                if not member.isfile():
                    continue
                target = _safe_member(root, member.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    continue
                with source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                output.append(target)
        return output
    with path.open("rb") as stream:
        magic = stream.read(4)
    if magic[:2] == b"\x1f\x8b":
        target = path.with_suffix("")
        with gzip.open(path, "rb") as source, target.open("wb") as destination:
            shutil.copyfileobj(source, destination)
        return [target]
    if magic == b"GRIB" and path.suffix.lower() not in {".grib", ".grb", ".grib2", ".grb2"}:
        target = path.with_name(f"{path.name}.grib")
        path.replace(target)
        return [target]
    return [path]


def download_ready(state_path: Path, output_root: Path) -> dict[str, Any]:
    state = _load_state(state_path)
    client = CmaTiggeClient(CmaCredentials.load())
    client.login()
    downloaded = 0
    for key, item in state.get("submissions", {}).items():
        application_id = item.get("application_id")
        if not application_id or item.get("downloaded_utc"):
            continue
        groups_payload = client.download_groups(application_id)
        data = groups_payload.get("data", {})
        groups = data.get("dataList", []) if isinstance(data, dict) else []
        if not groups:
            continue
        for group in groups:
            folder_id = group.get("folderId", group.get("folder_id"))
            if folder_id is None:
                continue
            files_payload = client.download_files(application_id, folder_id)
            file_data = files_payload.get("data", {})
            files = file_data.get("dataList", []) if isinstance(file_data, dict) else []
            for index, record in enumerate(files, 1):
                file_id = record.get("file_id", record.get("fileId", record.get("id")))
                raw_size = record.get("file_size", 1)
                try:
                    empty = float(raw_size or 0) <= 0
                except (TypeError, ValueError):
                    empty = False
                if file_id is None or empty:
                    continue
                raw_name = str(
                    record.get("file_name", record.get("fileName", record.get("name", "")))
                )
                name = _safe_filename(raw_name, f"cma-{file_id}-{index}.grib")
                target = output_root / str(item["model"]) / str(item["cycle"]) / str(item["component"]) / name
                if not target.exists():
                    client.download_file(file_id, target)
                    extract_download(target)
                    downloaded += 1
        item["downloaded_utc"] = iso_z(utc_now())
        state["updated_utc"] = iso_z(utc_now())
        atomic_write_json(state_path, state)
    state["last_download_count"] = downloaded
    state["updated_utc"] = iso_z(utc_now())
    atomic_write_json(state_path, state)
    return state


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    metadata = subparsers.add_parser("metadata", help="verify the public CMA centre/parameter catalogue")
    metadata.add_argument("--output", type=Path)
    submit = subparsers.add_parser("submit-plan", help="submit unrecorded requests from a recovery plan")
    submit.add_argument("--plan", type=Path, required=True)
    submit.add_argument("--state", type=Path, required=True)
    submit.add_argument("--limit", type=int)
    download = subparsers.add_parser("download-ready", help="download staged files for recorded application IDs")
    download.add_argument("--state", type=Path, required=True)
    download.add_argument("--output-root", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "metadata":
        client = CmaTiggeClient()
        payload = {
            "schema": "mla-cma-tigge-metadata-v1",
            "generated_utc": iso_z(utc_now()),
            "centres": client.centres(),
            "pressure_parameters": client.parameters(3),
            "surface_parameters": client.parameters(4),
        }
        if args.output:
            atomic_write_json(args.output, payload)
        print(json.dumps(payload, indent=2))
    elif args.command == "submit-plan":
        state = submit_plan(args.plan, args.state, args.limit)
        print(json.dumps({"submissions": len(state["submissions"]), "last_submit_count": state["last_submit_count"]}))
    elif args.command == "download-ready":
        state = download_ready(args.state, args.output_root)
        print(json.dumps({"last_download_count": state["last_download_count"]}))


if __name__ == "__main__":
    main()
