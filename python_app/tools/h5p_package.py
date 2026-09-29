"""Normalize and validate extracted H5P packages for h5p-standalone.

H5P archives occur in two layouts in the wild:

* canonical/versioned library folders, e.g. ``H5P.Text-1.1``;
* legacy/unversioned folders, e.g. ``H5P.Text``.

h5p-standalone resolves dependencies from ``h5p.json`` using canonical folder
names (``<machineName>-<major>.<minor>``). A legacy package therefore uploads
successfully but fails only in the learner's browser: every library.json request
404s, H5PIntegration gets ``library: "undefined "``, and no constructor can be
registered.

This module canonicalizes direct library folders and then validates the complete
runtime dependency graph before the package is previewed or published. It is
intentionally filesystem-only and has no Flask/OCI dependencies, so upload and
publish paths can share exactly the same behavior.
"""

from __future__ import annotations

import json
import re
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]+$")


class H5PPackageError(ValueError):
    """An extracted H5P package is incomplete or structurally unsafe."""


@dataclass
class H5PNormalizationReport:
    package_root: str
    main_library: str
    libraries_checked: int = 0
    renamed: list[dict[str, str]] = field(default_factory=list)

    def to_log_fields(self) -> dict[str, Any]:
        return {
            "h5p_package_root": self.package_root,
            "h5p_main_library": self.main_library,
            "h5p_libraries_checked": self.libraries_checked,
            "h5p_library_dirs_renamed": len(self.renamed),
            "h5p_library_renames": self.renamed,
        }


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise H5PPackageError(f"Missing {label}: {path.name}") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise H5PPackageError(f"Invalid JSON in {label}: {path.name}") from exc
    if not isinstance(value, dict):
        raise H5PPackageError(f"{label} must contain a JSON object: {path.name}")
    return value


def _version(value: Any, field_name: str, library: str) -> str:
    if isinstance(value, bool) or value in (None, ""):
        raise H5PPackageError(f"Library {library!r} has no {field_name}")
    text = str(value)
    if not text.isdigit():
        raise H5PPackageError(
            f"Library {library!r} has invalid {field_name}: {value!r}"
        )
    return str(int(text))


def _dependency_key(dep: dict[str, Any], owner: str) -> tuple[str, str, str]:
    machine = dep.get("machineName")
    if not isinstance(machine, str) or not _SAFE_COMPONENT.fullmatch(machine):
        raise H5PPackageError(
            f"{owner} declares an invalid dependency machineName: {machine!r}"
        )
    major = _version(dep.get("majorVersion"), "majorVersion", machine)
    minor = _version(dep.get("minorVersion"), "minorVersion", machine)
    return machine, major, minor


def _canonical_name(machine: str, major: str, minor: str) -> str:
    return f"{machine}-{major}.{minor}"


def _library_metadata(folder: Path) -> tuple[tuple[str, str, str], dict[str, Any]]:
    data = _read_json(folder / "library.json", f"library manifest in {folder.name}")
    key = _dependency_key(data, folder.name)
    return key, data


def normalize_h5p_package(package_root: str | Path) -> H5PNormalizationReport:
    """Canonicalize library folders and validate all runtime dependencies.

    Direct child directories containing ``library.json`` are renamed to
    ``<machineName>-<major>.<minor>`` when necessary. Existing canonical
    destinations are preserved; a duplicate legacy folder is left untouched
    but never selected for runtime validation.

    After normalization, the complete dependency graph starting at
    ``h5p.json.preloadedDependencies`` is checked. Every exact version must
    exist, every declared preloaded JS/CSS asset must exist, and the manifest's
    main library must be among the runtime dependencies.

    Returns a structured report suitable for application logs. Raises
    H5PPackageError before preview/publish when the package is incomplete.
    """
    root = Path(package_root).resolve()
    if not root.is_dir():
        raise H5PPackageError(f"H5P package directory does not exist: {root}")

    h5p = _read_json(root / "h5p.json", "H5P manifest")
    main_library = h5p.get("mainLibrary")
    if not isinstance(main_library, str) or not _SAFE_COMPONENT.fullmatch(main_library):
        raise H5PPackageError(f"Invalid or missing mainLibrary: {main_library!r}")

    report = H5PNormalizationReport(str(root), main_library)

    # Snapshot first: renaming while iterating Path.iterdir() can otherwise
    # produce platform-dependent duplicate/missed entries.
    library_dirs = [
        child for child in root.iterdir()
        if child.is_dir() and (child / "library.json").is_file()
    ]
    for folder in library_dirs:
        key, _ = _library_metadata(folder)
        expected = _canonical_name(*key)
        if folder.name == expected:
            continue
        target = root / expected
        if target.exists():
            # A canonical copy already wins. Verify it identifies the same
            # exact library; do not delete the legacy folder automatically.
            existing_key, _ = _library_metadata(target)
            if existing_key != key:
                raise H5PPackageError(
                    f"Conflicting H5P library folders {folder.name!r} and "
                    f"{target.name!r}"
                )
            continue
        folder.rename(target)
        report.renamed.append({"from": folder.name, "to": target.name})

    # Build an exact-version index from the canonical folders now on disk.
    libraries: dict[tuple[str, str, str], tuple[Path, dict[str, Any]]] = {}
    for folder in root.iterdir():
        if not folder.is_dir() or not (folder / "library.json").is_file():
            continue
        key, metadata = _library_metadata(folder)
        expected = _canonical_name(*key)
        if folder.name != expected:
            # A duplicate legacy directory can remain only when its canonical
            # equivalent already exists; don't let it shadow the canonical one.
            continue
        libraries[key] = (folder, metadata)

    declared = h5p.get("preloadedDependencies")
    if not isinstance(declared, list) or not declared:
        raise H5PPackageError("h5p.json has no preloadedDependencies")

    queue: deque[tuple[str, str, str]] = deque(
        _dependency_key(dep, "h5p.json") for dep in declared
    )
    visited: set[tuple[str, str, str]] = set()
    main_found = False

    while queue:
        key = queue.popleft()
        if key in visited:
            continue
        visited.add(key)
        machine, major, minor = key
        if machine == main_library:
            main_found = True
        item = libraries.get(key)
        if item is None:
            raise H5PPackageError(
                f"Missing H5P dependency: {_canonical_name(machine, major, minor)}"
            )
        folder, metadata = item

        for asset_group in ("preloadedJs", "preloadedCss"):
            assets = metadata.get(asset_group, [])
            if not isinstance(assets, list):
                raise H5PPackageError(
                    f"{folder.name}/{asset_group} must be an array"
                )
            for asset in assets:
                relative = asset.get("path") if isinstance(asset, dict) else None
                if not isinstance(relative, str) or not relative:
                    raise H5PPackageError(
                        f"{folder.name} has an invalid {asset_group} entry"
                    )
                asset_path = (folder / relative).resolve()
                try:
                    asset_path.relative_to(folder.resolve())
                except ValueError as exc:
                    raise H5PPackageError(
                        f"{folder.name} asset escapes its library directory: {relative}"
                    ) from exc
                if not asset_path.is_file():
                    raise H5PPackageError(
                        f"Missing H5P asset: {folder.name}/{relative}"
                    )

        dependencies = metadata.get("preloadedDependencies", [])
        if not isinstance(dependencies, list):
            raise H5PPackageError(
                f"{folder.name}/preloadedDependencies must be an array"
            )
        queue.extend(
            _dependency_key(dep, folder.name) for dep in dependencies
        )

    if not main_found:
        raise H5PPackageError(
            f"Main library {main_library!r} is not listed in preloadedDependencies"
        )

    report.libraries_checked = len(visited)
    return report
