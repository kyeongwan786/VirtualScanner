"""Create a private test CA and HTTPS server certificate using macOS OpenSSL."""

from __future__ import annotations

import argparse
import ipaddress
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Create test-only TLS files; never use this CA for production.")
    parser.add_argument("--name", required=True, help="Mac LAN IP address or DNS name used by clients")
    parser.add_argument("--out-dir", type=Path, default=Path("."))
    args = parser.parse_args()
    openssl = shutil.which("openssl")
    if not openssl:
        parser.error("openssl was not found; install/use a macOS OpenSSL distribution")
    try:
        address = ipaddress.ip_address(args.name)
        san = f"IP:{address.compressed}"
        common_name = address.compressed
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", args.name):
            parser.error("--name must be an IP address or DNS name")
        san = f"DNS:{args.name}"
        common_name = args.name

    args.out_dir.mkdir(parents=True, exist_ok=True)
    outputs = {name: args.out_dir / name for name in (
        "relay-test-ca.key", "relay-test-ca.crt", "relay-server.key", "relay-server.crt")}
    existing = [str(path) for path in outputs.values() if path.exists()]
    if existing:
        parser.error("refusing to overwrite existing files: " + ", ".join(existing))

    with tempfile.TemporaryDirectory(prefix="relay-tls-", dir=args.out_dir) as temporary_dir:
        temp = Path(temporary_dir)
        ca_config = temp / "ca.cnf"
        ca_config.write_text(
            "[req]\ndistinguished_name=dn\nprompt=no\nx509_extensions=v3_ca\n"
            "[dn]\nCN=Kyeongwan Relay Test CA\n"
            "[v3_ca]\nbasicConstraints=critical,CA:TRUE,pathlen:0\n"
            "keyUsage=critical,keyCertSign,cRLSign\n", encoding="ascii")
        server_config = temp / "server.cnf"
        server_config.write_text(
            f"[req]\ndistinguished_name=dn\nprompt=no\nreq_extensions=req_ext\n"
            f"[dn]\nCN={common_name}\n"
            f"[req_ext]\nsubjectAltName={san}\n"
            f"[v3_server]\nbasicConstraints=critical,CA:FALSE\n"
            f"keyUsage=critical,digitalSignature,keyEncipherment\n"
            f"extendedKeyUsage=serverAuth\nsubjectAltName={san}\n", encoding="ascii")
        csr = temp / "relay-server.csr"
        serial = temp / "relay-ca.srl"
        commands = [
            [openssl, "req", "-x509", "-newkey", "rsa:3072", "-sha256", "-days", "3650", "-nodes",
             "-keyout", str(outputs["relay-test-ca.key"]), "-out", str(outputs["relay-test-ca.crt"]),
             "-config", str(ca_config)],
            [openssl, "req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout",
             str(outputs["relay-server.key"]), "-out", str(csr), "-config", str(server_config)],
            [openssl, "x509", "-req", "-in", str(csr), "-CA", str(outputs["relay-test-ca.crt"]),
             "-CAkey", str(outputs["relay-test-ca.key"]), "-CAserial", str(serial), "-CAcreateserial",
             "-out", str(outputs["relay-server.crt"]), "-days", "365", "-sha256",
             "-extfile", str(server_config), "-extensions", "v3_server"],
        ]
        for command in commands:
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL)
    for name in ("relay-test-ca.key", "relay-server.key"):
        try:
            outputs[name].chmod(0o600)
        except OSError:
            pass
    print(f"Created test TLS files in {args.out_dir.resolve()}")
    print("Keep relay-test-ca.key private. Copy only relay-test-ca.crt to the test clients.")
    print("These certificates are for a private test only; use a company-issued certificate for deployment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
