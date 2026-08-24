#!/usr/bin/env python3
"""Reference unattended Signer Provider for Origen.

Not part of Origen core. This is a deployment-owned example of a Provider that can
honestly declare `interaction: none`: Ed25519 keys live in mode-0600 files outside the
repository and are used through OpenSSH SSHSIG, so no agent, key store, or approval
dialog sits between a scheduler and a signature.

The trade-off is explicit and is the price of unattended operation: any process running
as this user can read the private keys. Treat suspected exposure as a rotation event.

Protocol mode reads one `origen-signer/1` / `origen-root-authorization/2` /
`origen-provider/1` request on stdin and writes one JSON object on stdout.

Operator mode is separate and never reached from the protocol:

    python3 unattended-file-signer.py init --home /secure/path/origen-keys

`init` generates the keys and prints the Provider registry fragment to paste in.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile

ALGORITHM = "Ed25519"
KEY_TYPE = "ssh-ed25519"
SIGNER_NAMESPACE = "origen-signer"
AUTHORIZATION_NAMESPACE = "origen-root-authorization"
BOUNDARY_TYPE = "provider_authorization"
RECEIPT_VERSION = "origen-file-signer-receipt/1"
DEFAULT_PROVIDER_ID = "origen-file-signer"
HOME_VARIABLE = "ORIGEN_FILE_SIGNER_HOME"
SSH_KEYGEN_VARIABLE = "ORIGEN_SSH_KEYGEN"
SSH_KEYGEN_CANDIDATES = ("/usr/bin/ssh-keygen", "/opt/homebrew/bin/ssh-keygen", "/usr/local/bin/ssh-keygen")
OPERATIONS = ["authorize_root", "capabilities", "get_public_key", "health", "sign", "verify", "verify_authorization"]
# key_id -> (private key file, role, purpose)
KEYS = {
    "root": ("root.key", "root-attestor"),
    "final": ("final.key", "final-attestor"),
    "authorization": ("authorization.key", "workflow-boundary"),
}


class ProviderError(Exception):
    pass


def provider_id() -> str:
    return os.environ.get("ORIGEN_FILE_SIGNER_PROVIDER_ID", DEFAULT_PROVIDER_ID)


def signer_identity(key_id: str) -> str:
    return f"{provider_id()}:{key_id}"


def ssh_keygen() -> str:
    override = os.environ.get(SSH_KEYGEN_VARIABLE)
    candidates = (override,) if override else SSH_KEYGEN_CANDIDATES
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise ProviderError(f"ssh-keygen was not found; set {SSH_KEYGEN_VARIABLE} to an absolute path")


def signer_home() -> Path:
    raw = os.environ.get(HOME_VARIABLE)
    if not raw:
        raise ProviderError(f"{HOME_VARIABLE} must name the absolute key directory")
    home = Path(raw)
    if not home.is_absolute():
        raise ProviderError(f"{HOME_VARIABLE} must be absolute")
    return home


def private_key_path(home: Path, key_id: str) -> Path:
    if key_id not in KEYS:
        raise ProviderError(f"unknown key_id {key_id!r}")
    path = home / KEYS[key_id][0]
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ProviderError(f"{path} must be a regular non-symlink file")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ProviderError(f"{path} must not be group- or world-accessible")
    return path


def public_key(home: Path, key_id: str) -> str:
    """The two canonical SSH fields only; the trailing comment is not identity."""
    fields = (home / (KEYS[key_id][0] + ".pub")).read_text(encoding="utf-8").split()
    if len(fields) < 2 or fields[0] != KEY_TYPE:
        raise ProviderError(f"{key_id} public key is not an {KEY_TYPE} key")
    return f"{fields[0]} {fields[1]}"


def run_keygen(arguments: list[str], *, stdin: bytes | None = None) -> bytes:
    completed = subprocess.run(
        [ssh_keygen(), *arguments], input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
    )
    if completed.returncode != 0:
        raise ProviderError(f"ssh-keygen {arguments[0]} failed: {completed.stderr.decode('utf-8', 'replace').strip()}")
    return completed.stdout


def sshsig_sign(home: Path, key_id: str, namespace: str, payload: bytes) -> str:
    key = private_key_path(home, key_id)
    with tempfile.TemporaryDirectory() as work:
        message = Path(work) / "message"
        message.write_bytes(payload)
        run_keygen(["-Y", "sign", "-q", "-n", namespace, "-f", str(key), str(message)])
        armored = (Path(work) / "message.sig").read_bytes()
    return base64.b64encode(armored).decode("ascii")


def sshsig_verify(home: Path, key_id: str, namespace: str, payload: bytes, signature: str) -> bool:
    try:
        armored = base64.b64decode(signature.encode("ascii"), validate=True)
    except (ValueError, UnicodeEncodeError):
        return False
    identity = signer_identity(key_id)
    with tempfile.TemporaryDirectory() as work:
        allowed = Path(work) / "allowed_signers"
        allowed.write_text(f"{identity} {public_key(home, key_id)}\n", encoding="utf-8")
        signature_file = Path(work) / "message.sig"
        signature_file.write_bytes(armored)
        try:
            run_keygen(["-Y", "verify", "-q", "-f", str(allowed), "-I", identity, "-n", namespace, "-s", str(signature_file)], stdin=payload)
        except ProviderError:
            return False
    return True


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def key_from_request(request: dict) -> str:
    key_id = request.get("key_id")
    if key_id not in KEYS:
        raise ProviderError(f"unknown key_id {key_id!r}")
    if request.get("algorithm", ALGORITHM) != ALGORITHM:
        raise ProviderError("this Provider signs Ed25519 only")
    return key_id


def payload_bytes(request: dict) -> bytes:
    raw = request.get("payload")
    if not isinstance(raw, str):
        raise ProviderError("payload must be base64 text")
    return base64.b64decode(raw.encode("ascii"), validate=True)


def identity_fields(key_id: str) -> dict:
    return {
        "provider_id": provider_id(), "key_id": key_id,
        "algorithm": ALGORITHM, "signer_identity": signer_identity(key_id),
    }


def handle(request: dict) -> dict:
    operation = request.get("operation")
    if operation == "health":
        home = signer_home()
        ssh_keygen()
        for key_id in KEYS:
            private_key_path(home, key_id)
            public_key(home, key_id)
        return {"healthy": True}
    if operation == "capabilities":
        return {"operations": OPERATIONS}
    home = signer_home()
    if operation == "get_public_key":
        key_id = key_from_request(request)
        return {"key_id": key_id, "algorithm": ALGORITHM, "verifier": {"public_key": public_key(home, key_id)}}
    if operation == "sign":
        key_id = key_from_request(request)
        payload = payload_bytes(request)
        response = {**identity_fields(key_id), "signature": sshsig_sign(home, key_id, SIGNER_NAMESPACE, payload)}
        # Echo the authorization the Root statement already carries, so Origen can prove
        # the signed digest and the out-of-band boundary receipt are the same one.
        try:
            statement = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            statement = None
        if isinstance(statement, dict):
            digest = statement.get("authorization", {}).get("receipt_digest") if isinstance(statement.get("authorization"), dict) else None
            if isinstance(digest, str):
                response["authorization_receipt_digest"] = digest
        return response
    if operation == "verify":
        key_id = key_from_request(request)
        signature = request.get("signature")
        verified = isinstance(signature, str) and sshsig_verify(home, key_id, SIGNER_NAMESPACE, payload_bytes(request), signature)
        return {**identity_fields(key_id), "verified": verified}
    if operation == "authorize_root":
        subject = request.get("subject_sha256")
        if not isinstance(subject, str) or not subject:
            raise ProviderError("authorize_root requires subject_sha256")
        statement = {
            "schema_version": RECEIPT_VERSION,
            "boundary_id": signer_identity("authorization"),
            "boundary_type": BOUNDARY_TYPE,
            "policy_id": request.get("policy_id"),
            "policy_version": request.get("policy_version"),
            "subject_sha256": subject,
        }
        receipt = {"signature": sshsig_sign(home, "authorization", AUTHORIZATION_NAMESPACE, canonical(statement)), "statement": statement}
        return {
            "boundary_type": BOUNDARY_TYPE, "boundary_id": statement["boundary_id"],
            "subject_sha256": subject,
            "receipt": base64.b64encode(canonical(receipt)).decode("ascii"),
        }
    if operation == "verify_authorization":
        return {"verified": verify_authorization(home, request)}
    raise ProviderError(f"unsupported operation {operation!r}")


def verify_authorization(home: Path, request: dict) -> bool:
    raw = request.get("receipt")
    if not isinstance(raw, str):
        return False
    try:
        receipt = json.loads(base64.b64decode(raw.encode("ascii"), validate=True).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, UnicodeEncodeError):
        return False
    if not isinstance(receipt, dict) or not isinstance(receipt.get("statement"), dict):
        return False
    statement = receipt["statement"]
    if statement.get("schema_version") != RECEIPT_VERSION:
        return False
    for field in ("boundary_type", "boundary_id", "subject_sha256"):
        if statement.get(field) != request.get(field):
            return False
    signature = receipt.get("signature")
    return isinstance(signature, str) and sshsig_verify(home, "authorization", AUTHORIZATION_NAMESPACE, canonical(statement), signature)


def command_init(home: Path) -> int:
    """Operator-only key generation. Refuses to overwrite: rotation is a deliberate act."""
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(home, 0o700)
    for key_id, (filename, _) in KEYS.items():
        key = home / filename
        if key.exists() or (home / (filename + ".pub")).exists():
            print(f"refusing to overwrite existing {key_id} key at {key}", file=sys.stderr)
            return 1
        run_keygen(["-q", "-t", "ed25519", "-N", "", "-C", signer_identity(key_id), "-f", str(key)])
        os.chmod(key, 0o600)
    fragment = {
        "providers": {
            provider_id(): {
                "executable": sys.executable,
                "arguments": [str(Path(__file__).resolve())],
                "expected_executable_sha256": "<sha256 of the interpreter>",
                "expected_script_sha256": {str(Path(__file__).resolve()): "<sha256 of this file>"},
                "expected_resource_sha256": {},
                "provider_identity": provider_id(),
                "interaction": "none",
                "inherit_environment": [HOME_VARIABLE, SSH_KEYGEN_VARIABLE, "ORIGEN_FILE_SIGNER_PROVIDER_ID"],
                "version": "reference-1",
                "dependency_provenance": "OpenSSH ssh-keygen SSHSIG",
                "reproducible_install": "operator-generated Ed25519 key files, mode 0600",
            }
        },
        "signers": {
            "default-root": {
                "provider": provider_id(), "key_id": "root", "algorithm": ALGORITHM,
                "signer_identity": signer_identity("root"), "verifier": {"public_key": public_key(home, "root")},
                "root_authorization": {"accepted_boundaries": [BOUNDARY_TYPE]},
            },
            "default-final": {
                "provider": provider_id(), "key_id": "final", "algorithm": ALGORITHM,
                "signer_identity": signer_identity("final"), "verifier": {"public_key": public_key(home, "final")},
            },
        },
    }
    print(json.dumps(fragment, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"\nexport {HOME_VARIABLE}={home}", file=sys.stderr)
    return 0


def main(argv: list[str]) -> int:
    if argv:
        parser = argparse.ArgumentParser(prog="unattended-file-signer", description="Reference unattended Origen Signer Provider")
        commands = parser.add_subparsers(dest="command", required=True)
        initialize = commands.add_parser("init", help="generate Ed25519 key files and print a registry fragment")
        initialize.add_argument("--home", required=True, help="absolute directory that will hold the 0600 key files")
        arguments = parser.parse_args(argv)
        home = Path(arguments.home)
        if not home.is_absolute():
            print("--home must be absolute", file=sys.stderr)
            return 2
        return command_init(home)
    try:
        request = json.load(sys.stdin)
        if not isinstance(request, dict):
            raise ProviderError("request must be one JSON object")
        response = handle(request)
    except ProviderError as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except (OSError, ValueError) as error:
        print(json.dumps({"error": f"{type(error).__name__}: {error}"}, ensure_ascii=False), file=sys.stderr)
        return 1
    json.dump(response, sys.stdout, ensure_ascii=False, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
