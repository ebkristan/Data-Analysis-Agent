#!/usr/bin/env python3
"""Audit a staging tree, ZIP, or package file for forbidden paths and secrets."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Iterable

from package_policy import classify_path


MAX_CONTENT_SCAN = 8 * 1024 * 1024
SECRET_PATTERNS = (
    ("private_key", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("aws_access_key", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(rb"\bgh[oprsu]_[A-Za-z0-9]{30,}\b")),
    ("openai_style_key", re.compile(rb"\bsk-[A-Za-z0-9_-]{32,}\b")),
)


def _content_findings(data: bytes, relative: str) -> list[str]:
    if len(data) > MAX_CONTENT_SCAN or b"\x00" in data[:4096]:
        return []
    findings = []
    for label, pattern in SECRET_PATTERNS:
        if pattern.search(data):
            findings.append(f"{relative}: detected {label}")
    return findings


def _is_contained_symlink(root: Path, item: Path) -> bool:
    try:
        item.resolve(strict=True).relative_to(root)
    except (OSError, ValueError):
        return False
    return True


def _directory_entries(
    root: Path,
    *,
    allow_contained_symlinks: bool = False,
) -> Iterable[tuple[str, bytes | None, int, bool]]:
    for item in sorted(root.rglob("*")):
        relative = item.relative_to(root).as_posix()
        if item.is_symlink():
            yield relative, None, -1, allow_contained_symlinks and _is_contained_symlink(root, item)
        elif item.is_file():
            size = item.stat().st_size
            yield relative, item.read_bytes() if size <= MAX_CONTENT_SCAN else None, size, False


def _zip_entries(path: Path) -> Iterable[tuple[str, bytes | None, int, bool]]:
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            size = info.file_size
            data = archive.read(info) if size <= MAX_CONTENT_SCAN else None
            yield info.filename, data, size, False


def audit(target: Path, *, allow_contained_symlinks: bool = False) -> dict:
    target = target.resolve(strict=True)
    if target.is_dir():
        entries = _directory_entries(target, allow_contained_symlinks=allow_contained_symlinks)
        kind = "directory"
    elif zipfile.is_zipfile(target):
        entries = _zip_entries(target)
        kind = "zip"
    elif target.is_file():
        size = target.stat().st_size
        entries = [(target.name, target.read_bytes() if size <= MAX_CONTENT_SCAN else None, size, False)]
        kind = "file"
    else:
        raise ValueError("audit target must be a directory, regular file, or ZIP")

    findings: list[str] = []
    file_count = 0
    total_bytes = 0
    digest = hashlib.sha256()
    symlink_count = 0
    for relative, data, size, symlink_allowed in entries:
        file_count += 1
        if size >= 0:
            total_bytes += size
        digest.update(relative.encode("utf-8", errors="replace"))
        digest.update(str(size).encode("ascii"))
        if size == -1:
            symlink_count += 1
            if not symlink_allowed:
                findings.append(f"{relative}: symbolic links must resolve inside the package")
                continue
        try:
            status, reason = classify_path(relative)
        except ValueError as exc:
            findings.append(f"{relative}: {exc}")
            continue
        if status != "allow":
            findings.append(f"{relative}: {reason}")
        if data is not None:
            digest.update(hashlib.sha256(data).digest())
            findings.extend(_content_findings(data, relative))
    return {
        "schema_version": 1,
        "target": str(target),
        "kind": kind,
        "ok": not findings,
        "file_count": file_count,
        "symlink_count": symlink_count,
        "total_bytes": total_bytes,
        "content_manifest_sha256": digest.hexdigest(),
        "findings": sorted(set(findings)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--allow-contained-symlinks",
        action="store_true",
        help=(
            "Permit symlinks that resolve inside the audited directory. "
            "Used for macOS app bundles produced by PyInstaller."
        ),
    )
    args = parser.parse_args()
    try:
        report = audit(args.target, allow_contained_symlinks=args.allow_contained_symlinks)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"[audit] failed: {exc}")
        return 2
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload, encoding="utf-8")
    print(payload)
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
