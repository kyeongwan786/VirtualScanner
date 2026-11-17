"""TLS relay for moving scanner jobs to outbound-only workstation Receivers.

The service stores encrypted job payloads in private directories.  It is intended
to run behind an approved, TLS-enabled host.  A non-loopback listener is refused
unless TLS files and explicit client IP allow-lists are configured.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import ssl
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    Fernet = None

    class InvalidToken(Exception):
        pass

JOB_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
PAGE_RE = re.compile(r"^/v1/jobs/([A-Za-z0-9_-]{1,64})/pages/([1-9][0-9]{0,2})$")
MAX_PAGE_BYTES = 40 * 1024 * 1024
MAX_PAGES = 100
MAX_JOB_BYTES = 512 * 1024 * 1024


def _ip_allowed(peer: str, entries: list[str]) -> bool:
    try:
        address = ipaddress.ip_address(peer.split("%", 1)[0])
        return any(address in ipaddress.ip_network(item, strict=False) for item in entries)
    except ValueError:
        return False


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class NeonStateStore:
    """Small durable state store for emergency locks and station credentials."""

    def __init__(self, dsn: str):
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("install psycopg to use Neon DATABASE_URL") from exc
        self._psycopg = psycopg
        self._dsn = dsn
        self._execute(
            "CREATE TABLE IF NOT EXISTS kyeongwan_relay_state ("
            "state_key TEXT PRIMARY KEY, state_value JSONB NOT NULL, "
            "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )

    def _execute(self, query: str, params: tuple = (), *, fetch: bool = False):
        try:
            with self._psycopg.connect(self._dsn, connect_timeout=8) as conn:
                with conn.cursor() as cur:
                    cur.execute(query, params)
                    return cur.fetchone() if fetch else None
        except Exception:
            # Do not leak connection strings or credentials into application logs.
            raise OSError("Neon state database is unavailable") from None

    def get(self, key: str) -> dict | None:
        row = self._execute(
            "SELECT state_value FROM kyeongwan_relay_state WHERE state_key = %s",
            (key,), fetch=True,
        )
        return row[0] if row else None

    def set(self, key: str, value: dict) -> None:
        self._execute(
            "INSERT INTO kyeongwan_relay_state (state_key, state_value, updated_at) "
            "VALUES (%s, %s::jsonb, now()) "
            "ON CONFLICT (state_key) DO UPDATE SET "
            "state_value = EXCLUDED.state_value, updated_at = now()",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def ready(self) -> bool:
        self._execute("SELECT 1", fetch=True)
        return True


def load_config(path: Path) -> dict:
    config_dir = path.expanduser().resolve().parent
    config = json.loads(path.read_text(encoding="utf-8"))
    host, port = config.get("host", "127.0.0.1"), config.get("port", 8443)
    if not isinstance(host, str) or not host:
        raise ValueError("host must be a non-empty string")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    if not isinstance(config.get("central_token_sha256"), str) or len(config["central_token_sha256"]) != 64:
        raise ValueError("central_token_sha256 must be a SHA-256 hex digest")
    stations = config.get("stations")
    if not isinstance(stations, dict) or not stations:
        raise ValueError("stations must contain at least one station")
    for station_id, station in stations.items():
        if not JOB_RE.fullmatch(station_id) or not isinstance(station, dict):
            raise ValueError(f"invalid station entry: {station_id!r}")
        digest = station.get("token_sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"station {station_id} needs token_sha256")
        station["allowed_ips"] = station.get("allowed_ips", [])
        if not isinstance(station["allowed_ips"], list):
            raise ValueError(f"station {station_id} allowed_ips must be a list")
        for item in station["allowed_ips"]:
            ipaddress.ip_network(item, strict=False)
    config["central_allowed_ips"] = config.get("central_allowed_ips", [])
    if not isinstance(config["central_allowed_ips"], list):
        raise ValueError("central_allowed_ips must be a list")
    for item in config["central_allowed_ips"]:
        ipaddress.ip_network(item, strict=False)
    enforce_ip_allowlist = config.get("enforce_ip_allowlist", True)
    if not isinstance(enforce_ip_allowlist, bool):
        raise ValueError("enforce_ip_allowlist must be a boolean")
    config["enforce_ip_allowlist"] = enforce_ip_allowlist
    for key, default in (("data_dir", "./relay-data"), ("data_key_file", "./relay-data.key"),
                         ("tls_cert", ""), ("tls_key", "")):
        value = config.get(key, default)
        if value:
            resolved = Path(value).expanduser()
            if not resolved.is_absolute():
                resolved = config_dir / resolved
            config[key] = str(resolved.resolve())
        else:
            config[key] = ""
    return config


def load_render_config() -> tuple[dict, bytes]:
    """Build the public Render configuration from dashboard-managed secrets."""
    if (os.environ.get("KYEONGWAN_RELAY_RENDER_MODE") != "true"
            or not os.environ.get("RENDER_EXTERNAL_HOSTNAME")):
        raise ValueError("Render mode must be explicitly enabled with KYEONGWAN_RELAY_RENDER_MODE=true")
    if Fernet is None:
        raise ValueError("install the relay requirements first; cryptography is required")

    port_text = os.environ.get("PORT", "10000")
    try:
        port = int(port_text)
    except ValueError as error:
        raise ValueError("PORT must be an integer") from error
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be between 1 and 65535")

    central_token = os.environ.get("KYEONGWAN_RELAY_CENTRAL_TOKEN", "")
    if len(central_token) < 32:
        raise ValueError("KYEONGWAN_RELAY_CENTRAL_TOKEN must contain at least 32 characters")
    station_text = os.environ.get("KYEONGWAN_RELAY_STATIONS", "")
    try:
        station_values = json.loads(station_text)
    except json.JSONDecodeError as error:
        raise ValueError("KYEONGWAN_RELAY_STATIONS must be a JSON array") from error
    if not isinstance(station_values, list) or not station_values:
        raise ValueError("KYEONGWAN_RELAY_STATIONS must contain at least one station")
    stations = {}
    for value in station_values:
        if not isinstance(value, dict):
            raise ValueError("each Render station entry must be an object")
        station_id, token = value.get("station_id"), value.get("token")
        if not isinstance(station_id, str) or not JOB_RE.fullmatch(station_id):
            raise ValueError("each Render station needs a valid station_id")
        if station_id in stations:
            raise ValueError(f"duplicate Render station_id: {station_id}")
        if not isinstance(token, str) or len(token) < 32:
            raise ValueError(f"station {station_id} token must contain at least 32 characters")
        stations[station_id] = {
            "token_sha256": hashlib.sha256(token.encode("utf-8")).hexdigest(),
            "allowed_ips": [],
        }

    key_text = os.environ.get("KYEONGWAN_RELAY_DATA_KEY", "")
    try:
        key = key_text.encode("ascii")
        Fernet(key)
    except (UnicodeEncodeError, ValueError) as error:
        raise ValueError("KYEONGWAN_RELAY_DATA_KEY must be a valid Fernet key") from error

    # Render's free filesystem is ephemeral. Job payloads still use this local
    # directory, while safety state is persisted separately in Neon Postgres.
    data_dir = Path(os.environ.get("KYEONGWAN_RELAY_DATA_DIR", "/tmp/kyeongwan-relay"))
    config = {
        "host": "0.0.0.0",
        "port": port,
        "tls_cert": "",
        "tls_key": "",
        "data_dir": str(data_dir),
        "data_key_file": "",
        "central_token_sha256": hashlib.sha256(central_token.encode("utf-8")).hexdigest(),
        "central_allowed_ips": [],
        "stations": stations,
        "enforce_ip_allowlist": False,
        "render_mode": True,
        "database_url": os.environ.get("DATABASE_URL", "").strip(),
    }
    return config, key


def init_config(path: Path, station_id: str) -> Path:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite existing config: {path}")
    if not JOB_RE.fullmatch(station_id):
        raise ValueError("station ID must be 1-64 letters, digits, '_' or '-'")
    central_token = secrets.token_urlsafe(48)
    station_token = secrets.token_urlsafe(48)
    data = {
        "host": "127.0.0.1",
        "port": 8443,
        "tls_cert": "",
        "tls_key": "",
        "data_dir": "./relay-data",
        "data_key_file": "relay-data.key",
        "central_token_sha256": hashlib.sha256(central_token.encode()).hexdigest(),
        "central_allowed_ips": ["127.0.0.1/32", "::1/128"],
        "stations": {station_id: {
            "token_sha256": hashlib.sha256(station_token.encode()).hexdigest(),
            "allowed_ips": ["127.0.0.1/32", "::1/128"],
        }},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    provision = path.with_name("relay-provisioning.json")
    if provision.exists():
        path.unlink()
        raise FileExistsError(f"refusing to overwrite existing provisioning file: {provision}")
    key_path = path.with_name("relay-data.key")
    if key_path.exists():
        path.unlink()
        raise FileExistsError(f"refusing to overwrite existing data key: {key_path}")
    key_path.write_bytes(base64.urlsafe_b64encode(secrets.token_bytes(32)))
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    provision.write_text(json.dumps({
        "relay_url": f"https://REPLACE_WITH_SERVER_NAME:{data['port']}",
        "central_token": central_token,
        "stations": [{"station_id": station_id, "token": station_token}],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        provision.chmod(0o600)
    except OSError:
        pass
    return provision


def add_station(path: Path, station_id: str) -> None:
    if not JOB_RE.fullmatch(station_id):
        raise ValueError("station ID must be 1-64 letters, digits, '_' or '-'")
    provision = path.with_name("relay-provisioning.json")
    config = json.loads(path.read_text(encoding="utf-8"))
    credentials = json.loads(provision.read_text(encoding="utf-8"))
    if station_id in config.get("stations", {}):
        raise ValueError(f"station already exists: {station_id}")
    token = secrets.token_urlsafe(48)
    config.setdefault("stations", {})[station_id] = {
        "token_sha256": hashlib.sha256(token.encode()).hexdigest(),
        "allowed_ips": ["127.0.0.1/32", "::1/128"],
    }
    credentials.setdefault("stations", []).append({"station_id": station_id, "token": token})
    path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    provision.write_text(json.dumps(credentials, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        path.chmod(0o600)
        provision.chmod(0o600)
    except OSError:
        pass


class RelayHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, address, handler, *, config: dict, key: bytes):
        super().__init__(address, handler)
        self.config = config
        self.cipher = Fernet(key)
        self.data_dir = Path(config["data_dir"]).expanduser().resolve()
        self.jobs_dir = self.data_dir / "jobs"
        self.emergency_path = self.data_dir / "emergency-stop.json"
        self.station_provisioning_path = self.data_dir / "station-provisioning.json"
        database_url = config.get("database_url", "")
        self.state_store = NeonStateStore(database_url) if database_url else None
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.data_dir.chmod(0o700)
            self.jobs_dir.chmod(0o700)
        except OSError:
            pass
        self.lock = threading.RLock()
        # Volatile heartbeat timestamps; Receivers refresh these through their
        # existing two-second emergency-command poll.
        self.station_last_seen: dict[str, datetime] = {}
        self._load_station_provisioning()
        self._migrate_emergency_state()

    def _read_state(self, key: str, path: Path) -> dict | None:
        if self.state_store:
            value = self.state_store.get(key)
            if value is not None:
                if not isinstance(value, dict):
                    raise ValueError(f"invalid persisted {key} state")
                return value
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid persisted {key} state: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"invalid persisted {key} state")
        return value

    def _write_state(self, key: str, path: Path, value: dict) -> None:
        if self.state_store:
            self.state_store.set(key, value)
        else:
            _atomic_write(path, _json_bytes(value))

    def _migrate_emergency_state(self) -> None:
        """Preserve legacy locks and add fields required for station receipts."""
        event = self._read_state("emergency", self.emergency_path)
        if event is None:
            return
        if not isinstance(event, dict) or event.get("active") is not True:
            return
        changed = False
        if not isinstance(event.get("event_id"), str) or not event["event_id"]:
            event["event_id"] = uuid4().hex
            changed = True
        if not isinstance(event.get("activated_at"), str):
            event["activated_at"] = datetime.now(timezone.utc).isoformat()
            changed = True
        if not isinstance(event.get("results"), dict):
            event["results"] = {}
            changed = True
        if changed:
            self._write_state("emergency", self.emergency_path, event)

    def _load_station_provisioning(self) -> None:
        """Restore centrally provisioned station hashes from the service data dir."""
        payload = self._read_state("stations", self.station_provisioning_path)
        if payload is None:
            return
        stations = payload.get("stations") if isinstance(payload, dict) else None
        if not isinstance(stations, dict):
            raise ValueError("persisted station provisioning must contain stations")
        for station_id, station in stations.items():
            if not JOB_RE.fullmatch(station_id) or not isinstance(station, dict):
                raise ValueError("persisted station provisioning contains an invalid station")
            digest = station.get("token_sha256")
            if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
                raise ValueError(f"persisted station {station_id} has an invalid token hash")
            allowed_ips = station.get("allowed_ips", [])
            if not isinstance(allowed_ips, list):
                raise ValueError(f"persisted station {station_id} has invalid allowed_ips")
            self.config["stations"][station_id] = {
                "token_sha256": digest,
                "allowed_ips": allowed_ips,
            }

    def provision_stations(self, entries: list[dict]) -> int:
        """Replace provisioned station credentials while retaining configured IP rules."""
        stations: dict[str, dict[str, object]] = {}
        for entry in entries:
            station_id = entry["station_id"]
            previous = self.config["stations"].get(station_id, {})
            stations[station_id] = {
                "token_sha256": hashlib.sha256(entry["token"].encode("utf-8")).hexdigest(),
                "allowed_ips": previous.get("allowed_ips", []),
            }
        with self.lock:
            self._write_state("stations", self.station_provisioning_path, {"stations": stations})
            if not self.state_store:
                try:
                    self.station_provisioning_path.chmod(0o600)
                except OSError:
                    pass
            # Keep any Render environment entries that were not part of the app profile.
            self.config["stations"].update(stations)
        return len(stations)

    def emergency_active(self) -> bool:
        try:
            value = self._read_state("emergency", self.emergency_path)
            return isinstance(value, dict) and value.get("active") is True
        except (OSError, ValueError):
            # Fail closed if the durable lock store cannot be read.
            logging.exception("Could not read emergency state")
            return True

    def emergency_storage_persistent(self) -> bool:
        """Report whether emergency state has a currently reachable durable store."""
        if self.state_store:
            try:
                return self.state_store.ready()
            except OSError:
                return False
        if not self.config.get("render_mode"):
            return True
        try:
            data_dir = self.data_dir.resolve()
            mount_points = []
            for line in Path("/proc/mounts").read_text(encoding="utf-8").splitlines():
                fields = line.split()
                if len(fields) >= 2:
                    mount = Path(fields[1].replace("\\040", " ")).resolve()
                    if mount == Path("/var/data"):
                        mount_points.append(mount)
            return any(data_dir == mount or mount in data_dir.parents for mount in mount_points)
        except OSError:
            return False

    def emergency_report(self) -> dict:
        try:
            event = self._read_state("emergency", self.emergency_path)
        except (OSError, ValueError):
            logging.exception("Could not read emergency report state")
            event = {"active": True, "event_id": None, "activated_at": None, "results": {}}
        if event is None:
            event = {"active": False, "event_id": None, "activated_at": None, "results": {}}
        if not isinstance(event, dict):
            event = {"active": False, "event_id": None, "activated_at": None, "results": {}}
        results = event.get("results") if isinstance(event.get("results"), dict) else {}
        sender_result = event.get("sender_result")
        if not isinstance(sender_result, dict):
            sender_result = {}
        stations = []
        for station_id in sorted(self.config["stations"]):
            result = results.get(station_id)
            stations.append({
                "station_id": station_id,
                "status": result.get("status", "pending") if isinstance(result, dict) else "pending",
                "cleanup_at": result.get("cleanup_at") if isinstance(result, dict) else None,
                "shutdown": result.get("shutdown", "not_confirmed") if isinstance(result, dict) else "not_confirmed",
                "detail": result.get("detail", "") if isinstance(result, dict) else "",
            })
        return {
            "active": event.get("active") is True,
            "event_id": event.get("event_id"),
            "activated_at": event.get("activated_at"),
            "storage_persistent": self.emergency_storage_persistent(),
            "sender": {
                "status": sender_result.get("status", "pending"),
                "cleanup_at": sender_result.get("cleanup_at"),
                "shutdown": sender_result.get("shutdown", "not_confirmed"),
                "detail": sender_result.get("detail", ""),
            },
            "stations": stations,
        }

    def record_emergency_result(self, payload: dict, *, station_id: str | None = None) -> dict:
        with self.lock:
            event = self._read_state("emergency", self.emergency_path)
            if (not isinstance(event, dict) or event.get("active") is not True
                    or payload.get("event_id") != event.get("event_id")):
                raise ValueError("emergency event does not match")
            status = payload.get("status")
            if not isinstance(status, str) or status not in {"cleanup_complete", "cleanup_failed"}:
                raise ValueError("invalid emergency result status")
            result = {
                "status": status,
                "cleanup_at": datetime.now(timezone.utc).isoformat(),
                "shutdown": "requested",
                "detail": payload.get("detail", "")[:500],
            }
            if station_id is None:
                event["sender_result"] = result
            else:
                results = event.get("results")
                if not isinstance(results, dict):
                    results = {}
                    event["results"] = results
                results[station_id] = result
            self._write_state("emergency", self.emergency_path, event)
            return result

    def activate_emergency(self) -> int:
        with self.lock:
            if not self.emergency_storage_persistent():
                raise OSError("durable emergency state storage is unavailable")
            if self.emergency_active():
                return 0
            event_id = uuid4().hex
            self._write_state("emergency", self.emergency_path, {
                "active": True,
                "event_id": event_id,
                "activated_at": datetime.now(timezone.utc).isoformat(),
                "results": {},
                "sender_result": {},
            })
            removed = 0
            for item in list(self.jobs_dir.iterdir()):
                try:
                    if item.is_symlink() or not item.is_dir():
                        item.unlink(missing_ok=True)
                    else:
                        shutil.rmtree(item)
                    removed += 1
                except OSError:
                    logging.exception("Could not purge relay job during emergency stop: %s", item.name)
            return removed


class RelayHandler(BaseHTTPRequestHandler):
    server: RelayHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        logging.info("%s %s", self.client_address[0], fmt % args)

    def _reply(self, status: int, payload: dict, content_type="application/json; charset=utf-8"):
        body = b"" if status == 204 else (_json_bytes(payload) if content_type.startswith("application/json") else payload)
        self.send_response(status)
        if status != 204:
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _body(self, limit: int) -> bytes | None:
        raw = self.headers.get("Content-Length", "")
        try:
            length = int(raw)
        except ValueError:
            self._reply(411, {"error": "content length required"})
            return None
        if length < 0 or length > limit:
            self.close_connection = True
            self._reply(413, {"error": "request too large"})
            return None
        body = self.rfile.read(length)
        if len(body) != length:
            self._reply(400, {"error": "incomplete request body"})
            return None
        return body

    def _authorize_central(self) -> bool:
        if (self.server.config["enforce_ip_allowlist"]
                and not _ip_allowed(self.client_address[0], self.server.config["central_allowed_ips"])):
            self._reply(403, {"error": "source IP is not allowed"})
            return False
        supplied = self.headers.get("Authorization", "")
        supplied = supplied.removeprefix("Bearer ")
        digest = hashlib.sha256(supplied.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(digest, self.server.config["central_token_sha256"]):
            self._reply(401, {"error": "unauthorized"})
            return False
        return True

    def _authorize_station(self, station_id: str) -> bool:
        station = self.server.config["stations"].get(station_id)
        if station is None:
            self._reply(404, {"error": "station not found"})
            return False
        if (self.server.config["enforce_ip_allowlist"]
                and not _ip_allowed(self.client_address[0], station["allowed_ips"])):
            self._reply(403, {"error": "source IP is not allowed"})
            return False
        supplied = self.headers.get("Authorization", "").removeprefix("Bearer ")
        digest = hashlib.sha256(supplied.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(digest, station["token_sha256"]):
            self._reply(401, {"error": "unauthorized"})
            return False
        return True

    def _job_dir(self, job_id: str) -> Path:
        return self.server.jobs_dir / job_id

    def _metadata(self, job: Path) -> dict | None:
        try:
            return json.loads((job / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/health":
            address = self.client_address[0]
            allowed = not self.server.config["enforce_ip_allowlist"]
            allowed = allowed or _ip_allowed(address, self.server.config["central_allowed_ips"])
            allowed = allowed or any(_ip_allowed(address, station["allowed_ips"])
                                     for station in self.server.config["stations"].values())
            if not allowed:
                self._reply(403, {"error": "source IP is not allowed"})
                return
            self._reply(200, {"status": "ready", "features": [
                "scanner-pages-v1", "new-work-v1", "new-work-zip-v1", "job-status-v1",
                "emergency-lock-v1", "emergency-receipts-v1", "station-provisioning-v1",
                "station-presence-v1",
            ], "emergency_active": self.server.emergency_active(),
                "emergency_storage_persistent": self.server.emergency_storage_persistent()})
            return
        if path == "/v1/emergency":
            if not self._authorize_central():
                return
            self._reply(200, self.server.emergency_report())
            return
        if path == "/v1/stations/status":
            if not self._authorize_central():
                return
            now = datetime.now(timezone.utc)
            with self.server.lock:
                stations = []
                for station_id in sorted(self.server.config["stations"]):
                    last_seen = self.server.station_last_seen.get(station_id)
                    age = (now - last_seen).total_seconds() if last_seen else None
                    stations.append({
                        "station_id": station_id,
                        "online": age is not None and age <= 15,
                        "last_seen": last_seen.isoformat() if last_seen else None,
                    })
            self._reply(200, {"stations": stations})
            return
        match = re.fullmatch(r"/v1/stations/([A-Za-z0-9_-]{1,64})/emergency", path)
        if match:
            station_id = match.group(1)
            if not self._authorize_station(station_id):
                return
            with self.server.lock:
                self.server.station_last_seen[station_id] = datetime.now(timezone.utc)
            report = self.server.emergency_report()
            self._reply(200, {
                "active": report["active"], "event_id": report["event_id"],
                "activated_at": report["activated_at"],
            })
            return
        if self.server.emergency_active():
            self._reply(410, {"error": "relay permanently disabled by emergency command"})
            return
        match = re.fullmatch(r"/v1/jobs/([A-Za-z0-9_-]{1,64})/status", path)
        if match:
            if not self._authorize_central():
                return
            with self.server.lock:
                job_id = match.group(1)
                metadata = self._metadata(self._job_dir(job_id))
                if not metadata:
                    self._reply(404, {"error": "job not found"})
                    return
                self._reply(200, {"job_id": job_id,
                                  "station_id": metadata.get("station_id"),
                                  "status": metadata.get("status")})
            return
        match = re.fullmatch(r"/v1/stations/([A-Za-z0-9_-]{1,64})/jobs/next", path)
        if match:
            station_id = match.group(1)
            if not self._authorize_station(station_id):
                return
            requested_types = parse_qs(urlsplit(self.path).query).get("payload_type", [])
            if requested_types and requested_types[0] not in {
                "scanner-pages-v1", "new-work-v1", "new-work-zip-v1"
            }:
                self._reply(400, {"error": "unsupported payload_type filter"})
                return
            # Legacy clients that do not send a filter continue to receive
            # scanner-page jobs and never consume NEW_WORK packages.
            requested_type = requested_types[0] if requested_types else "scanner-pages-v1"
            with self.server.lock:
                queued = []
                for job in self.server.jobs_dir.iterdir():
                    metadata = self._metadata(job)
                    payload_type = (metadata or {}).get("payload_type", "scanner-pages-v1")
                    if (metadata and metadata.get("station_id") == station_id
                            and metadata.get("status") == "queued"
                            and payload_type == requested_type):
                        queued.append((metadata.get("created_at", ""), job, metadata))
                if queued:
                    _, job, metadata = min(queued, key=lambda item: item[0])
                    try:
                        details = json.loads(self.server.cipher.decrypt(
                            (job / "details.fernet").read_bytes()).decode("utf-8"))
                        source_name = details.get("source_name", "relay-job")
                    except (OSError, InvalidToken, UnicodeDecodeError, json.JSONDecodeError):
                        details = {}
                        source_name = "relay-job"
                    response = {key: metadata[key] for key in ("job_id", "page_count", "dpi", "created_at")}
                    if metadata.get("payload_type") == "new-work-zip-v1":
                        response["archive_size"] = metadata["archive_size"]
                        response["archive_sha256"] = details.get("archive_sha256")
                    response.update({"source_name": source_name,
                                     "payload_type": metadata.get("payload_type", "scanner-pages-v1")})
                    if metadata.get("payload_type") in {"new-work-v1", "new-work-zip-v1"}:
                        response["work_package"] = details.get("work_package")
                    self._reply(200, response)
                    return
            self._reply(204, {})
            return
        match = re.fullmatch(r"/v1/stations/([A-Za-z0-9_-]{1,64})/jobs/([A-Za-z0-9_-]{1,64})/pages/([1-9][0-9]{0,2})", path)
        if match:
            station_id, job_id, page_text = match.groups()
            if not self._authorize_station(station_id):
                return
            with self.server.lock:
                if self.server.emergency_active():
                    self._reply(410, {"error": "relay permanently disabled by emergency command"})
                    return
                job = self._job_dir(job_id)
                metadata = self._metadata(job)
                number = int(page_text)
                if not metadata or metadata.get("station_id") != station_id or metadata.get("status") != "queued" or number > metadata.get("page_count", 0):
                    self._reply(404, {"error": "job page not found"})
                    return
                try:
                    encrypted = (job / f"{number:03d}.fernet").read_bytes()
                    content = self.server.cipher.decrypt(encrypted)
                except (OSError, InvalidToken):
                    self._reply(500, {"error": "stored page is unavailable"})
                    return
            content_type = (
                "application/octet-stream"
                if metadata.get("payload_type") == "new-work-zip-v1"
                else "image/jpeg"
            )
            self._reply(200, content, content_type)
            return
        self._reply(404, {"error": "not found"})

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/v1/stations/provision":
            if not self._authorize_central():
                return
            body = self._body(64 * 1024)
            if body is None:
                return
            try:
                payload = json.loads(body.decode("utf-8"))
                entries = payload.get("stations") if isinstance(payload, dict) else None
                if not isinstance(entries, list) or not entries or len(entries) > 100:
                    raise ValueError("stations must contain 1 to 100 entries")
                normalized = []
                seen_ids, seen_tokens = set(), set()
                for entry in entries:
                    if not isinstance(entry, dict):
                        raise ValueError("invalid station entry")
                    station_id = entry.get("station_id")
                    token = entry.get("token")
                    if not isinstance(station_id, str) or not JOB_RE.fullmatch(station_id):
                        raise ValueError("invalid station ID")
                    if not isinstance(token, str) or len(token) < 32:
                        raise ValueError("station tokens must be at least 32 characters")
                    if station_id in seen_ids or token in seen_tokens:
                        raise ValueError("station IDs and tokens must be unique")
                    seen_ids.add(station_id)
                    seen_tokens.add(token)
                    normalized.append({"station_id": station_id, "token": token})
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                self._reply(400, {"error": str(exc)})
                return
            try:
                count = self.server.provision_stations(normalized)
            except OSError as exc:
                self._reply(500, {"error": f"could not persist station provisioning: {exc}"})
                return
            self._reply(200, {"configured_stations": count})
            return
        if path == "/v1/emergency-result":
            if not self._authorize_central():
                return
            body = self._body(16 * 1024)
            if body is None:
                return
            try:
                payload = json.loads(body.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("body must be an object")
                detail = payload.get("detail", "")
                if not isinstance(detail, str):
                    raise ValueError("detail must be text")
                payload["detail"] = detail
                result = self.server.record_emergency_result(payload)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                self._reply(400, {"error": str(exc)})
                return
            except OSError as exc:
                self._reply(500, {"error": f"could not persist emergency result: {exc}"})
                return
            self._reply(200, {"recorded": True, **result})
            return
        match = re.fullmatch(r"/v1/stations/([A-Za-z0-9_-]{1,64})/emergency-result", path)
        if match:
            station_id = match.group(1)
            if not self._authorize_station(station_id):
                return
            body = self._body(16 * 1024)
            if body is None:
                return
            try:
                payload = json.loads(body.decode("utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("body must be an object")
                detail = payload.get("detail", "")
                if not isinstance(detail, str):
                    raise ValueError("detail must be text")
                payload["detail"] = detail
                result = self.server.record_emergency_result(payload, station_id=station_id)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                self._reply(400, {"error": str(exc)})
                return
            except OSError as exc:
                self._reply(500, {"error": f"could not persist emergency result: {exc}"})
                return
            self._reply(200, {"recorded": True, **result})
            return
        if path == "/v1/emergency":
            if not self._authorize_central():
                return
            try:
                removed = self.server.activate_emergency()
            except OSError as exc:
                status = 503 if "durable emergency state storage" in str(exc) else 500
                self._reply(status, {"error": f"could not persist emergency lock: {exc}"})
                return
            self._reply(200, {**self.server.emergency_report(), "removed_jobs": removed})
            return
        if self.server.emergency_active():
            self._reply(410, {"error": "relay permanently disabled by emergency command"})
            return
        if path == "/v1/jobs":
            if not self._authorize_central():
                return
            # new-work adds reviewed values and an ordered document/page map.
            body = self._body(1024 * 1024)
            if body is None:
                return
            try:
                request = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._reply(400, {"error": "body must be JSON"})
                return
            if not isinstance(request, dict):
                self._reply(400, {"error": "body must be an object"})
                return
            job_id, station_id = request.get("job_id"), request.get("station_id")
            page_count, dpi = request.get("page_count"), request.get("dpi")
            source_name = request.get("source_name", "")
            payload_type = request.get("payload_type", "scanner-pages-v1")
            work_package = request.get("work_package")
            archive_size = request.get("archive_size")
            archive_sha256 = request.get("archive_sha256")
            if (not isinstance(job_id, str) or not JOB_RE.fullmatch(job_id)
                    or not isinstance(station_id, str)
                    or station_id not in self.server.config["stations"]):
                self._reply(400, {"error": "invalid job_id or station_id"})
                return
            if not isinstance(page_count, int) or isinstance(page_count, bool) or not 1 <= page_count <= MAX_PAGES:
                self._reply(400, {"error": "page_count must be between 1 and 100"})
                return
            if not isinstance(dpi, int) or isinstance(dpi, bool) or not 72 <= dpi <= 600:
                self._reply(400, {"error": "dpi must be between 72 and 600"})
                return
            if not isinstance(source_name, str) or len(source_name) > 255 or any(ord(c) < 32 for c in source_name):
                self._reply(400, {"error": "invalid source_name"})
                return
            if not isinstance(payload_type, str) or payload_type not in {
                "scanner-pages-v1", "new-work-v1", "new-work-zip-v1"
            }:
                self._reply(400, {"error": "unsupported payload_type"})
                return
            if payload_type in {"new-work-v1", "new-work-zip-v1"} and not isinstance(work_package, dict):
                self._reply(400, {"error": "new-work payload requires work_package metadata"})
                return
            if payload_type == "new-work-zip-v1":
                if (not isinstance(archive_size, int) or isinstance(archive_size, bool)
                        or not 1 <= archive_size <= MAX_JOB_BYTES
                        or not isinstance(archive_sha256, str)
                        or not re.fullmatch(r"[0-9a-f]{64}", archive_sha256)):
                    self._reply(400, {"error": "invalid ZIP size or SHA-256"})
                    return
            metadata = {"job_id": job_id, "station_id": station_id, "page_count": page_count, "dpi": dpi,
                        "total_bytes": 0, "status": "uploading", "payload_type": payload_type,
                        "created_at": datetime.now(timezone.utc).isoformat()}
            if payload_type == "new-work-zip-v1":
                if page_count != (archive_size + MAX_PAGE_BYTES - 1) // MAX_PAGE_BYTES:
                    self._reply(400, {"error": "ZIP chunk count does not match archive size"})
                    return
                metadata.update({"archive_size": archive_size})
            with self.server.lock:
                if self.server.emergency_active():
                    self._reply(410, {"error": "relay permanently disabled by emergency command"})
                    return
                job = self._job_dir(job_id)
                try:
                    job.mkdir(mode=0o700)
                except FileExistsError:
                    self._reply(409, {"error": "job ID already exists"})
                    return
                _atomic_write(job / "metadata.json", _json_bytes(metadata))
                details = {"source_name": source_name.replace("/", "_").replace("\\", "_")}
                if payload_type in {"new-work-v1", "new-work-zip-v1"}:
                    details["work_package"] = work_package
                if payload_type == "new-work-zip-v1":
                    details["archive_sha256"] = archive_sha256
                _atomic_write(job / "details.fernet", self.server.cipher.encrypt(_json_bytes(details)))
            self._reply(201, {"status": "created", "job_id": job_id})
            return
        match = re.fullmatch(r"/v1/jobs/([A-Za-z0-9_-]{1,64})/complete", path)
        if match:
            if not self._authorize_central():
                return
            job_id = match.group(1)
            with self.server.lock:
                if self.server.emergency_active():
                    self._reply(410, {"error": "relay permanently disabled by emergency command"})
                    return
                job = self._job_dir(job_id)
                metadata = self._metadata(job)
                if not metadata:
                    self._reply(404, {"error": "job not found"})
                    return
                if metadata.get("status") == "queued":
                    self._reply(200, {"status": "queued", "job_id": job_id})
                    return
                expected = [job / f"{number:03d}.fernet" for number in range(1, metadata["page_count"] + 1)]
                if any(not page.is_file() for page in expected):
                    self._reply(409, {"error": "job is missing pages"})
                    return
                if (metadata.get("payload_type") == "new-work-zip-v1"
                        and metadata.get("total_bytes") != metadata.get("archive_size")):
                    self._reply(409, {"error": "ZIP archive is incomplete"})
                    return
                metadata["status"] = "queued"
                _atomic_write(job / "metadata.json", _json_bytes(metadata))
            self._reply(201, {"status": "queued", "job_id": job_id})
            return
        match = re.fullmatch(r"/v1/stations/([A-Za-z0-9_-]{1,64})/jobs/([A-Za-z0-9_-]{1,64})/ack", path)
        if match:
            station_id, job_id = match.groups()
            if not self._authorize_station(station_id):
                return
            with self.server.lock:
                job = self._job_dir(job_id)
                metadata = self._metadata(job)
                if not metadata or metadata.get("station_id") != station_id:
                    self._reply(404, {"error": "job not found"})
                    return
                if metadata.get("status") == "delivered":
                    pass
                elif metadata.get("status") != "queued":
                    self._reply(409, {"error": "job is not queued"})
                    return
                else:
                    metadata["status"] = "delivered"
                    metadata["delivered_at"] = datetime.now(timezone.utc).isoformat()
                    _atomic_write(job / "metadata.json", _json_bytes(metadata))
                if metadata.get("payload_type") in {"new-work-v1", "new-work-zip-v1"}:
                    # Purge the personal payload but retain a status-only
                    # record so the central UI can confirm local application.
                    for child in job.iterdir():
                        if child.name != "metadata.json":
                            if child.is_dir():
                                shutil.rmtree(child)
                            else:
                                child.unlink(missing_ok=True)
                else:
                    try:
                        shutil.rmtree(job)
                    except OSError:
                        logging.exception("Could not remove delivered relay job %s", job_id)
                        self._reply(200, {"status": "delivered", "job_id": job_id,
                                          "cleanup": "pending-render-restart"})
                        return
            self._reply(200, {"status": "delivered-and-removed", "job_id": job_id})
            return
        self._reply(404, {"error": "not found"})

    def do_PUT(self):
        if not self._authorize_central():
            return
        if self.server.emergency_active():
            self._reply(410, {"error": "relay permanently disabled by emergency command"})
            return
        match = PAGE_RE.fullmatch(urlsplit(self.path).path)
        if not match:
            self._reply(404, {"error": "not found"})
            return
        job_id, page_text = match.groups()
        number = int(page_text)
        content_type = self.headers.get_content_type()
        if content_type not in {"image/jpeg", "application/octet-stream"}:
            self._reply(415, {"error": "content type must be image/jpeg or application/octet-stream"})
            return
        body = self._body(MAX_PAGE_BYTES)
        if body is None:
            return
        if number > MAX_PAGES or not body:
            self._reply(400, {"error": "invalid page or chunk"})
            return
        with self.server.lock:
            if self.server.emergency_active():
                self._reply(410, {"error": "relay permanently disabled by emergency command"})
                return
            job = self._job_dir(job_id)
            metadata = self._metadata(job)
            if not metadata or metadata.get("status") != "uploading" or number > metadata.get("page_count", 0):
                self._reply(409, {"error": "job does not accept this page"})
                return
            if metadata.get("payload_type") == "new-work-zip-v1":
                expected_length = min(
                    MAX_PAGE_BYTES,
                    metadata["archive_size"] - (number - 1) * MAX_PAGE_BYTES,
                )
                if content_type != "application/octet-stream" or len(body) != expected_length:
                    self._reply(400, {"error": "invalid ZIP chunk"})
                    return
            elif (content_type != "image/jpeg" or not body.startswith(b"\xff\xd8\xff")
                    or not body.endswith(b"\xff\xd9")):
                self._reply(400, {"error": "invalid JPEG page"})
                return
            target = job / f"{number:03d}.fernet"
            digest = hashlib.sha256(body).hexdigest()
            if target.exists():
                try:
                    old = self.server.cipher.decrypt(target.read_bytes())
                except (OSError, InvalidToken):
                    self._reply(500, {"error": "stored page is unavailable"})
                    return
                if hashlib.sha256(old).hexdigest() != digest:
                    self._reply(409, {"error": "page already exists with different content"})
                    return
                self._reply(200, {"status": "duplicate", "page": number})
                return
            if metadata.get("total_bytes", 0) + len(body) > MAX_JOB_BYTES:
                self._reply(413, {"error": "job exceeds the 512 MiB total limit"})
                return
            _atomic_write(target, self.server.cipher.encrypt(body))
            metadata["total_bytes"] = metadata.get("total_bytes", 0) + len(body)
            _atomic_write(job / "metadata.json", _json_bytes(metadata))
        self._reply(201, {"status": "received", "page": number})

    def do_DELETE(self):
        if not self._authorize_central():
            return
        if self.server.emergency_active():
            self._reply(410, {"error": "relay permanently disabled by emergency command"})
            return
        match = re.fullmatch(r"/v1/jobs/([A-Za-z0-9_-]{1,64})/cancel", urlsplit(self.path).path)
        if not match:
            self._reply(404, {"error": "not found"})
            return
        with self.server.lock:
            job = self._job_dir(match.group(1))
            metadata = self._metadata(job)
            if not metadata:
                self._reply(404, {"error": "job not found"})
                return
            if metadata.get("status") in ("delivered", "cancelled"):
                self._reply(409, {"error": "job can no longer be cancelled"})
                return
            metadata["status"] = "cancelled"
            metadata["cancelled_at"] = datetime.now(timezone.utc).isoformat()
            _atomic_write(job / "metadata.json", _json_bytes(metadata))
        self._reply(200, {"status": "cancelled", "job_id": match.group(1)})


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Kyeongwan secure job relay.")
    parser.add_argument("--config", type=Path, default=Path("relay.json"))
    parser.add_argument("--render", action="store_true", help="run as a Render web service using dashboard secrets")
    parser.add_argument("--init-config", action="store_true", help="create local-only config and one-time provisioning file")
    parser.add_argument("--station-id", default="desk-01")
    parser.add_argument("--add-station", metavar="ID", help="add a station and rotate the private provisioning file")
    args = parser.parse_args()
    if args.init_config:
        try:
            provision = init_config(args.config, args.station_id)
        except (OSError, ValueError, FileExistsError) as error:
            logging.error("Config initialization failed: %s", error)
            return 2
        print(f"Created local-only relay config: {args.config}")
        print(f"One-time client credentials: {provision} (transfer securely; do not email or commit)")
        return 0
    if args.add_station:
        try:
            add_station(args.config, args.add_station)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            logging.error("Station provisioning failed: %s", error)
            return 2
        print(f"Added station {args.add_station}; updated {args.config.with_name('relay-provisioning.json')}")
        return 0
    try:
        if args.render:
            config, key = load_render_config()
        else:
            config = load_config(args.config)
            key_text = os.environ.get("KYEONGWAN_RELAY_DATA_KEY", "")
            if key_text:
                key = key_text.encode("ascii")
            else:
                key_file = Path(config.get("data_key_file", "relay-data.key")).expanduser()
                key = key_file.read_bytes().strip()
        if Fernet is None:
            raise ValueError("install the project requirements first; cryptography is required to start the relay")
        Fernet(key)
        bind_ip = ipaddress.ip_address(config["host"])
        external = not bind_ip.is_loopback
        if external and not config.get("render_mode"):
            if not config["tls_cert"] or not config["tls_key"]:
                raise ValueError("external listener requires tls_cert and tls_key")
            if not config["central_allowed_ips"] or any(not s["allowed_ips"] for s in config["stations"].values()):
                raise ValueError("external listener requires explicit central and per-station allowed_ips")
        server = RelayHTTPServer((config["host"], config["port"]), RelayHandler, config=config, key=key)
        if external and not config.get("render_mode"):
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(config["tls_cert"], config["tls_key"])
            server.socket = context.wrap_socket(server.socket, server_side=True)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        logging.error("Relay startup refused: %s", error)
        return 2
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if config.get("render_mode"):
        logging.info("Render relay listening on 0.0.0.0:%s; public HTTPS terminates at Render", config["port"])
    else:
        logging.info("Relay listening on %s:%s", config["host"], config["port"])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Stopping relay")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
