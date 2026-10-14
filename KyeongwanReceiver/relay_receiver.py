"""Outbound-only workstation agent that pulls relay jobs into the TWAIN queue."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import ssl
import re
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote, urlsplit

from receiver import default_data_dir, JOB_ID_RE, MAX_PAGE_BYTES, MAX_PAGES_PER_JOB


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    station_id, token = config.get("station_id"), config.get("token")
    relay_url = config.get("relay_url", "")
    parsed = urlsplit(relay_url.rstrip("/")) if isinstance(relay_url, str) else None
    if not isinstance(station_id, str) or not JOB_ID_RE.fullmatch(station_id):
        raise ValueError("station_id must be 1-64 letters, digits, '_' or '-'")
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError("token must contain at least 32 characters")
    if (parsed is None or parsed.scheme != "https" or not parsed.netloc or parsed.path not in ("", "/")
            or parsed.query or parsed.fragment):
        raise ValueError("relay_url must be an HTTPS origin, for example https://relay.example.com")
    interval = config.get("poll_interval_seconds", 10)
    if not isinstance(interval, int) or isinstance(interval, bool) or not 2 <= interval <= 300:
        raise ValueError("poll_interval_seconds must be between 2 and 300")
    ca_cert = config.get("ca_cert")
    try:
        tls_context = ssl.create_default_context(cafile=ca_cert) if ca_cert else None
    except (OSError, ssl.SSLError) as error:
        raise ValueError(f"could not load relay CA certificate: {error}") from error
    return {"station_id": station_id, "token": token,
            "relay_url": f"https://{parsed.netloc}", "poll_interval_seconds": interval,
            "ssl_context": tls_context}


def request(url: str, token: str, method="GET", body=None, content_type=None, binary=False, ssl_context=None):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30, context=ssl_context) as response:
        data = response.read(MAX_PAGE_BYTES + 1 if binary else 64 * 1024)
        if binary:
            if len(data) > MAX_PAGE_BYTES:
                raise ValueError("relay page exceeds 40 MiB")
            return response.status, data
        try:
            parsed = json.loads(data.decode("utf-8")) if data else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("relay returned an invalid JSON response") from error
        return response.status, parsed


def atomic_write(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".page-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def receive_one(config: dict, data_dir: Path) -> bool:
    base = config["relay_url"]
    station = quote(config["station_id"], safe="")
    status, job = request(f"{base}/v1/stations/{station}/jobs/next", config["token"],
                          ssl_context=config.get("ssl_context"))
    if status == 204:
        return False
    if status != 200:
        raise ValueError(f"relay returned HTTP {status}: {job}")
    job_id, count, dpi = job.get("job_id"), job.get("page_count"), job.get("dpi")
    if not isinstance(job_id, str) or not JOB_ID_RE.fullmatch(job_id):
        raise ValueError("relay job ID is invalid")
    if not isinstance(count, int) or isinstance(count, bool) or not 1 <= count <= MAX_PAGES_PER_JOB:
        raise ValueError("relay page count is invalid")
    if not isinstance(dpi, int) or isinstance(dpi, bool) or not 72 <= dpi <= 600:
        raise ValueError("relay DPI is invalid")

    incoming = data_dir / "incoming" / job_id
    ready = data_dir / "ready" / job_id
    if ready.exists():
        manifest_path = ready / "manifest.json"
        try:
            saved = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise FileExistsError(f"existing ready job is unreadable; refusing overwrite: {ready}")
        if saved.get("station_id") != config["station_id"] or saved.get("page_count") != count:
            raise FileExistsError(f"existing ready job does not match relay job; refusing overwrite: {ready}")
        request(f"{base}/v1/stations/{station}/jobs/{quote(job_id, safe='')}/ack",
                config["token"], method="POST", body=b"", content_type="application/json",
                ssl_context=config.get("ssl_context"))
        return True
    incoming.mkdir(parents=True, exist_ok=True)
    pages = []
    for number in range(1, count + 1):
        page_url = f"{base}/v1/stations/{station}/jobs/{quote(job_id, safe='')}/pages/{number}"
        page_status, content = request(page_url, config["token"], binary=True,
                                       ssl_context=config.get("ssl_context"))
        if page_status != 200 or not content.startswith(b"\xff\xd8\xff") or not content.endswith(b"\xff\xd9"):
            raise ValueError(f"relay returned invalid JPEG page {number}")
        page_name = f"{number:03d}.jpg"
        atomic_write(incoming / page_name, content)
        pages.append({"number": number, "file": page_name,
                      "sha256": hashlib.sha256(content).hexdigest()})
    source_name = job.get("source_name", "relay-job")
    if not isinstance(source_name, str) or len(source_name) > 255:
        source_name = "relay-job"
    manifest = {"job_id": job_id, "station_id": config["station_id"], "source_name": source_name,
                "dpi": dpi, "page_count": count, "pages": pages}
    manifest_path = incoming / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(incoming, ready)
    request(f"{base}/v1/stations/{station}/jobs/{quote(job_id, safe='')}/ack",
            config["token"], method="POST", body=b"", content_type="application/json",
            ssl_context=config.get("ssl_context"))
    logging.info("Job %s received (%s pages); stored under %s", job_id, count, ready)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull scanner jobs from the central relay.")
    parser.add_argument("--config", type=Path, required=True, help="path to receiver.json")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir())
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = load_config(args.config)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        logging.error("Receiver config invalid: %s", error)
        return 2
    data_dir = args.data_dir.expanduser().resolve()
    (data_dir / "incoming").mkdir(parents=True, exist_ok=True)
    (data_dir / "ready").mkdir(parents=True, exist_ok=True)
    logging.info("Station %s polling the relay every %s seconds", config["station_id"], config["poll_interval_seconds"])
    logging.info("Completed jobs are stored under %s", data_dir / "ready")
    try:
        while True:
            try:
                if not receive_one(config, data_dir):
                    time.sleep(config["poll_interval_seconds"])
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError, ValueError) as error:
                logging.error("Relay receive failed; will retry: %s", error)
                time.sleep(config["poll_interval_seconds"])
    except KeyboardInterrupt:
        logging.info("Stopping receiver")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
