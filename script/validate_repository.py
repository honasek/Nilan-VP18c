"""Validate the distributable HACS layout without connecting to a device."""
from __future__ import annotations

import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "nilan_cts600"


def validate() -> dict:
    components = ROOT / "custom_components"
    names = sorted(p.name for p in components.iterdir() if p.is_dir() and p.name != "__pycache__")
    if names != [DOMAIN]:
        raise ValueError(f"Expected exactly one integration: {DOMAIN}; found {names}")

    component = components / DOMAIN
    manifest = json.loads((component / "manifest.json").read_text(encoding="utf-8"))
    required = ("domain", "name", "codeowners", "documentation", "issue_tracker", "version")
    if any(not manifest.get(key) for key in required) or manifest["domain"] != DOMAIN:
        raise ValueError("Integration manifest is incomplete or its domain does not match its folder")
    if not (component / "__init__.py").is_file():
        raise ValueError("Integration entry point is missing")
    if not (component / "brand" / "icon.png").is_file():
        raise ValueError("Integration brand icon is missing")

    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    if not hacs.get("name") or hacs.get("content_in_root", False) or hacs.get("zip_release", False):
        raise ValueError("HACS manifest must describe the standard custom_components layout")
    for filename in ("README.md", "LICENSE"):
        if not (ROOT / filename).is_file():
            raise ValueError(f"Missing repository file: {filename}")

    modules = 0
    for path in component.rglob("*.py"):
        compile(path.read_text(encoding="utf-8-sig"), str(path), "exec")
        modules += 1
    for path in component.rglob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))

    # A tag must publish the version that Home Assistant will actually load.
    if os.environ.get("GITHUB_REF_TYPE") == "tag":
        tag = os.environ.get("GITHUB_REF_NAME", "").removeprefix("v")
        if tag != manifest["version"]:
            raise ValueError(f"Release tag {tag!r} differs from manifest version {manifest['version']!r}")

    return {"domain": DOMAIN, "version": manifest["version"], "python_modules": modules, "valid": True}


if __name__ == "__main__":
    print(json.dumps(validate()))
