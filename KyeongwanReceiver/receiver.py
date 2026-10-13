"""Small standard-library HTTP receiver for station-scoped scanner jobs."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit


MAX_PAGE_BYTES = 40 * 1024 * 1024
MAX_PAGES_PER_JOB = 100
JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PAGE_ROUTE_RE = re.compile(r"^/v1/jobs/([A-Za-z0-9_-]{1,64})/pages/([1-9][0-9]{0,2})$")
COMPLETE_ROUTE_RE = re.compile(r"^/v1/jobs/([A-Za-z0-9_-]{1,64})/complete$")
CANCEL_ROUTE_RE = re.compile(r"^/v1/jobs/([A-Za-z0-9_-]{1,64})/cancel$")


def default_data_dir() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "KyeongwanScanner" / "VirtualSource"
    return Path.home() / ".local" / "share" / "kyeongwan-scanner" / "receiver"


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as stream:
        config = json.load(stream)
    station_id = config.get("station_id")
    token = config.get("token")
    host = config.get("host", "0.0.0.0")
    port = config.get("port", 29742)
    if not isinstance(station_id, str) or not JOB_ID_RE.fullmatch(station_id):
        raise ValueError("config station_id must be 1-64 letters, digits, '_' or '-'")
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError("config token must contain at least 32 characters")
    if not isinstance(host, str) or not host:
        raise ValueError("config host must be a non-empty string")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("config port must be between 1 and 65535")
    return {"station_id": station_id, "token": token, "host": host, "port": port}


class ReceiverServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, *, station_id: str, token: str, data_dir: Path):
        super().__init__(address, handler)
        self.station_id = station_id
        self.token = token.encode("utf-8")
        self.data_dir = data_dir
        self.incoming_dir = data_dir / "incoming"
        self.ready_dir = data_dir / "ready"
        self.cancelled_dir = data_dir / "cancelled"
        self.incoming_dir.mkdir(parents=True, exist_ok=True)
        self.ready_dir.mkdir(parents=True, exist_ok=True)
        self.cancelled_dir.mkdir(parents=True, exist_ok=True)
        self.job_lock = threading.Lock()


class RequestHandler(BaseHTTPRequestHandler):
    server: ReceiverServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        logging.info("%s - %s", self.address_string(), fmt % args)

    def _reply(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        supplied = self.headers.get("X-Receiver-Token", "").encode("utf-8")
        if hmac.compare_digest(supplied, self.server.token):
            return True
        self.close_connection = True
        self._reply(401, {"error": "unauthorized"})
        return False

    def _content_length(self, maximum: int) -> int | None:
        raw = self.headers.get("Content-Length")
        try:
            length = int(raw) if raw is not None else -1
        except ValueError:
            length = -1
        if length < 0 or length > maximum:
            if length > maximum:
                self.close_connection = True
            self._reply(413 if length > maximum else 411, {"error": "invalid content length"})
            return None
        return length

    def do_GET(self):
        if urlsplit(self.path).path != "/health":
            self._reply(404, {"error": "not found"})
            return
        self._reply(200, {"status": "ready", "station_id": self.server.station_id})

    def do_PUT(self):
        if not self._authorized():
            return
        match = PAGE_ROUTE_RE.fullmatch(urlsplit(self.path).path)
        if not match:
            self._reply(404, {"error": "not found"})
            return
        job_id, page_text = match.groups()
        page_number = int(page_text)
        if page_number > MAX_PAGES_PER_JOB:
            self._reply(400, {"error": "page number exceeds limit"})
            return
        if self.headers.get_content_type() != "image/jpeg":
            self._reply(415, {"error": "content type must be image/jpeg"})
            return
        length = self._content_length(MAX_PAGE_BYTES)
        if length is None:
            return
        content = self.rfile.read(length)
        if len(content) != length or not content.startswith(b"\xff\xd8\xff") or not content.endswith(b"\xff\xd9"):
            self._reply(400, {"error": "payload is not a complete JPEG image"})
            return

        incoming = self.server.incoming_dir / job_id
        target = incoming / f"{page_number:03d}.jpg"
        digest = hashlib.sha256(content).hexdigest()
        with self.server.job_lock:
            if (self.server.cancelled_dir / job_id).exists():
                self._reply(409, {"error": "job was cancelled"})
                return
            if (self.server.ready_dir / job_id).exists():
                self._reply(409, {"error": "job is already complete"})
                return
            incoming.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if hashlib.sha256(target.read_bytes()).hexdigest() == digest:
                    self._reply(200, {"status": "duplicate", "page": page_number, "sha256": digest})
                else:
                    self._reply(409, {"error": "page already exists with different content"})
                return
            fd, temp_name = tempfile.mkstemp(prefix=".page-", suffix=".tmp", dir=str(incoming))
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_name, target)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        self._reply(201, {"status": "received", "page": page_number, "sha256": digest})

    def do_POST(self):
        if not self._authorized():
            return
        match = COMPLETE_ROUTE_RE.fullmatch(urlsplit(self.path).path)
        if not match:
            self._reply(404, {"error": "not found"})
            return
        job_id = match.group(1)
        length = self._content_length(4096)
        if length is None:
            return
        try:
            request = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._reply(400, {"error": "body must be JSON"})
            return
        page_count = request.get("page_count") if isinstance(request, dict) else None
        if not isinstance(page_count, int) or isinstance(page_count, bool) or not 1 <= page_count <= MAX_PAGES_PER_JOB:
            self._reply(400, {"error": "page_count must be between 1 and 100"})
            return
        source_name = request.get("source_name", "") if isinstance(request, dict) else ""
        if not isinstance(source_name, str) or len(source_name) > 255 or any(ord(char) < 32 for char in source_name):
            self._reply(400, {"error": "source_name must be a filename of at most 255 characters"})
            return
        source_name = source_name.replace("/", "_").replace("\\", "_")
        dpi = request.get("dpi", 175) if isinstance(request, dict) else 175
        if not isinstance(dpi, int) or isinstance(dpi, bool) or not 72 <= dpi <= 600:
            self._reply(400, {"error": "dpi must be between 72 and 600"})
            return

        with self.server.job_lock:
            source = self.server.incoming_dir / job_id
            destination = self.server.ready_dir / job_id
            if (self.server.cancelled_dir / job_id).exists():
                self._reply(409, {"error": "job was cancelled"})
                return
            if destination.exists():
                try:
                    existing = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    self._reply(500, {"error": "completed job manifest is unreadable"})
                    return
                if existing.get("page_count") != page_count:
                    self._reply(409, {"error": "job is already complete with a different page_count"})
                    return
                self._reply(200, {"status": "already-complete", "page_count": page_count})
                return
            expected = [source / f"{number:03d}.jpg" for number in range(1, page_count + 1)]
            if not source.is_dir() or any(not page.is_file() for page in expected):
                self._reply(409, {"error": "job is missing one or more pages"})
                return
            extra_pages = [path for path in source.glob("*.jpg") if path not in expected]
            if extra_pages:
                self._reply(409, {"error": "job contains pages beyond page_count"})
                return
            manifest = {
                "job_id": job_id,
                "station_id": self.server.station_id,
                "source_name": source_name,
                "dpi": dpi,
                "page_count": page_count,
                "pages": [
                    {"number": number, "file": f"{number:03d}.jpg", "sha256": hashlib.sha256(expected[number - 1].read_bytes()).hexdigest()}
                    for number in range(1, page_count + 1)
                ],
            }
            manifest_path = source / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(source, destination)
        self._reply(201, {"status": "ready", "job_id": job_id, "page_count": page_count})

    def do_DELETE(self):
        if not self._authorized():
            return
        match = CANCEL_ROUTE_RE.fullmatch(urlsplit(self.path).path)
        if not match:
            self._reply(404, {"error": "not found"})
            return
        job_id = match.group(1)
        incoming = self.server.incoming_dir / job_id
        ready = self.server.ready_dir / job_id
        cancelled = self.server.cancelled_dir / job_id
        with self.server.job_lock:
            if ready.exists():
                self._reply(409, {"error": "job is already ready and cannot be cancelled"})
                return
            if cancelled.exists():
                self._reply(200, {"status": "already-cancelled", "job_id": job_id})
                return
            preserved = incoming.exists()
            if incoming.exists():
                os.replace(incoming, cancelled)
            else:
                cancelled.mkdir(parents=True)
            cancellation = {
                "job_id": job_id,
                "station_id": self.server.station_id,
                "status": "cancelled",
            }
            (cancelled / "cancellation.json").write_text(
                json.dumps(cancellation, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        self._reply(200, {"status": "cancelled", "job_id": job_id, "preserved": preserved})


def main() -> int:
    parser = argparse.ArgumentParser(description="Receive scanner image jobs for one workstation.")
    parser.add_argument("--config", type=Path, required=True, help="path to receiver.json")
    parser.add_argument("--data-dir", type=Path, default=default_data_dir(), help="job storage directory")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        server = ReceiverServer(
            (config["host"], config["port"]),
            RequestHandler,
            station_id=config["station_id"],
            token=config["token"],
            data_dir=args.data_dir,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        logging.error("Receiver startup failed: %s", error)
        return 2

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.info("Receiver for station %s listening on %s:%s", config["station_id"], config["host"], config["port"])
    logging.info("Completed jobs are stored under %s", server.ready_dir)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Stopping receiver")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
