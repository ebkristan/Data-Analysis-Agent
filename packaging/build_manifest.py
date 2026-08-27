#!/usr/bin/env python3
"""Create a clean release staging directory from an explicit allowlist."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from pathlib import Path

from package_policy import ALLOWED_ROOT_DIRS, ALLOWED_ROOT_FILES, classify_path


class ManifestPolicyError(RuntimeError):
    """Raised before copying when an allowed source tree contains forbidden data."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _candidate_files(source: Path) -> tuple[list[tuple[Path, Path]], list[str], list[str]]:
    files: list[tuple[Path, Path]] = []
    excluded: list[str] = []
    violations: list[str] = []

    for name in sorted(ALLOWED_ROOT_FILES):
        candidate = source / name
        if candidate.is_symlink():
            violations.append(f"{name}: symbolic links are forbidden")
        elif candidate.is_file():
            files.append((candidate, Path(name)))

    if not (source / "app.py").is_file():
        violations.append("app.py: required release entry is missing")

    for root_name in ALLOWED_ROOT_DIRS:
        allowed_root = source / root_name
        if not allowed_root.exists():
            continue
        if allowed_root.is_symlink() or not allowed_root.is_dir():
            violations.append(f"{root_name}: allowed root must be a real directory")
            continue
        for current, dirs, names in os.walk(allowed_root, followlinks=False):
            current_path = Path(current)
            kept_dirs: list[str] = []
            for directory in sorted(dirs):
                item = current_path / directory
                relative = item.relative_to(source)
                if item.is_symlink():
                    violations.append(f"{relative.as_posix()}: symbolic links are forbidden")
                    continue
                status, reason = classify_path(relative.as_posix())
                if status == "allow":
                    kept_dirs.append(directory)
                elif status in {"exclude", "deny"}:
                    # Runtime/cache trees are never traversed. They may exist
                    # in a developer checkout, but their content cannot affect
                    # or enter the release staging directory.
                    excluded.append(f"{relative.as_posix()}/ ({reason})")
            dirs[:] = kept_dirs
            for filename in sorted(names):
                item = current_path / filename
                relative = item.relative_to(source)
                if item.is_symlink():
                    violations.append(f"{relative.as_posix()}: symbolic links are forbidden")
                    continue
                status, reason = classify_path(relative.as_posix())
                if status == "allow":
                    if item.is_file():
                        files.append((item, relative))
                elif status == "exclude":
                    excluded.append(f"{relative.as_posix()} ({reason})")
                else:
                    violations.append(f"{relative.as_posix()} ({reason})")
    return files, excluded, violations


def build_staging(source: Path, destination: Path, manifest_path: Path | None = None) -> dict:
    source = source.resolve(strict=True)
    destination = destination.resolve(strict=False)
    if destination == source:
        raise ManifestPolicyError("staging destination cannot be the source root")
    if source in destination.parents:
        relative_destination = destination.relative_to(source)
        # build/ is deliberately outside the release allowlist, so staging
        # cannot recursively copy itself.
        if relative_destination.parts[0].lower() not in {"build", ".package-staging"}:
            raise ManifestPolicyError("staging inside the source root must use build/ or .package-staging/")
    if destination.exists():
        raise ManifestPolicyError(f"staging destination already exists: {destination}")

    files, excluded, violations = _candidate_files(source)
    if violations:
        details = "\n".join(f"- {item}" for item in violations)
        raise ManifestPolicyError(f"packaging policy rejected the source tree:\n{details}")

    destination.mkdir(parents=True)
    manifest_files = []
    for item, relative in files:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(item, target)
        manifest_files.append({
            "path": relative.as_posix(),
            "size": target.stat().st_size,
            "sha256": _sha256(target),
        })
    manifest = {
        "schema_version": 1,
        "source": str(source),
        "staging": str(destination),
        "file_count": len(manifest_files),
        "total_bytes": sum(item["size"] for item in manifest_files),
        "files": manifest_files,
        "excluded": sorted(excluded),
    }
    output = manifest_path or destination.parent / f"{destination.name}-manifest.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    try:
        manifest = build_staging(args.source, args.destination, args.manifest)
    except (ManifestPolicyError, OSError, ValueError) as exc:
        print(f"[packaging] blocked: {exc}")
        return 2
    print(
        f"[packaging] staged {manifest['file_count']} files "
        f"({manifest['total_bytes']} bytes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
