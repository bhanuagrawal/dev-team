"""dev-team keystore backup & verification module.

Provides multi-destination off-repo backup for Android/KMP release signing keys:
1. Google Drive (Mac): ~/Google Drive/My Drive/App Keystores/<project>/
   (auto-synced to owner's Google Drive cloud).
2. Local Dev-Team Vault: ~/.config/devteam/keystores/<project>/
3. Optional Private GitHub Vault Repository: gh-backed private secrets repo.

Enforces that release signing keystores are never trapped solely in ephemeral
project checkouts where a clean or disk incident could lose app signing capability.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def get_google_drive_base() -> Path | None:
    """Find the mounted Google Drive root on macOS if available."""
    candidates = [
        Path.home() / "Google Drive" / "My Drive",
        Path.home() / "Library" / "CloudStorage",
    ]
    # Check direct link/path first
    if candidates[0].is_dir():
        return candidates[0]

    # Check CloudStorage provider directories
    if candidates[1].is_dir():
        for item in sorted(candidates[1].glob("GoogleDrive-*")):
            my_drive = item / "My Drive"
            if my_drive.is_dir():
                return my_drive

    return None


def get_google_drive_keystore_dir() -> Path | None:
    """Get the target App Keystores directory in Google Drive."""
    base = get_google_drive_base()
    if not base:
        return None
    target = base / "App Keystores"
    target.mkdir(parents=True, exist_ok=True)
    return target


def get_local_vault_dir() -> Path:
    """Get the machine-local central dev-team keystore vault."""
    vault = Path.home() / ".config" / "devteam" / "keystores"
    vault.mkdir(parents=True, exist_ok=True)
    return vault


def find_keystore_info(project: Path) -> dict | None:
    """Detect keystore file and credentials from project directory."""
    # Look for keystore.properties in root or config/
    prop_candidates = [
        project / "keystore.properties",
        project / "config" / "keystore.properties",
    ]
    props_path = next((p for p in prop_candidates if p.is_file()), None)

    props = {}
    if props_path:
        for line in props_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            props[k.strip()] = v.strip()

    # Determine keystore file
    store_file_rel = props.get("storeFile")
    keystore_path = None
    if store_file_rel:
        p = project / store_file_rel
        if p.is_file():
            keystore_path = p
        elif (project / "config" / store_file_rel).is_file():
            keystore_path = project / "config" / store_file_rel

    if not keystore_path:
        # Fallback search for .jks / .keystore files
        jks_files = list(project.glob("config/*.jks")) + list(project.glob("*.jks")) + list(project.glob("*.keystore"))
        # Exclude debug keystores if release exists
        release_candidates = [f for f in jks_files if "debug" not in f.name.lower()]
        if release_candidates:
            keystore_path = release_candidates[0]
        elif jks_files:
            keystore_path = jks_files[0]

    if not keystore_path or not keystore_path.is_file():
        return None

    return {
        "project": project.name,
        "keystore_path": keystore_path,
        "props_path": props_path,
        "storePassword": props.get("storePassword", ""),
        "keyAlias": props.get("keyAlias", "upload"),
        "keyPassword": props.get("keyPassword", props.get("storePassword", "")),
    }


def extract_certificate_metadata(keystore_path: Path, storepass: str) -> dict:
    """Run keytool to extract certificate fingerprints and alias info."""
    meta = {
        "file_name": keystore_path.name,
        "file_size": keystore_path.stat().st_size,
        "sha1": "",
        "sha256": "",
        "alias": "",
        "valid_until": "",
        "owner": "",
    }
    cmd = ["keytool", "-list", "-v", "-keystore", str(keystore_path)]
    if storepass:
        cmd.extend(["-storepass", storepass])

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        out = res.stdout
        for line in out.splitlines():
            line_str = line.strip()
            if line_str.startswith("Alias name:"):
                meta["alias"] = line_str.split(":", 1)[1].strip()
            elif "SHA1:" in line_str or "SHA-1:" in line_str:
                meta["sha1"] = line_str.split(":", 1)[1].strip()
            elif "SHA256:" in line_str or "SHA-256:" in line_str:
                meta["sha256"] = line_str.split(":", 1)[1].strip()
            elif line_str.startswith("Owner:"):
                meta["owner"] = line_str.split(":", 1)[1].strip()
            elif "Valid from:" in line_str and "until:" in line_str:
                parts = line_str.split("until:", 1)
                if len(parts) > 1:
                    meta["valid_until"] = parts[1].strip()

    return meta


def backup_keystore(
    project: Path,
    backup_drive: bool = True,
    backup_vault: bool = True,
    github_vault_repo: str | None = None,
) -> dict:
    """Perform a backup of the project's keystore to all available destinations."""
    info = find_keystore_info(project)
    if not info:
        return {"ok": False, "error": f"No keystore found in {project}"}

    keystore_path: Path = info["keystore_path"]
    props_path: Path | None = info["props_path"]
    storepass: str = info["storePassword"]

    cert_meta = extract_certificate_metadata(keystore_path, storepass)
    now_str = datetime.now(timezone.utc).isoformat()

    manifest = {
        "project": project.name,
        "backed_up_at": now_str,
        "keystore_file": keystore_path.name,
        "key_alias": info["keyAlias"] or cert_meta.get("alias", "upload"),
        "certificate": cert_meta,
        "has_credentials": bool(props_path and props_path.exists()),
    }

    readme_content = f"""# Keystore Backup: {project.name}
Created at: {now_str}

## Files
- {keystore_path.name}: Signing keystore
- keystore.properties: Signing passwords and alias

## Certificate Details
- Alias: {manifest['key_alias']}
- Owner: {cert_meta.get('owner', 'N/A')}
- Validity Until: {cert_meta.get('valid_until', 'N/A')}
- SHA-1 Fingerprint: {cert_meta.get('sha1', 'N/A')}
- SHA-256 Fingerprint: {cert_meta.get('sha256', 'N/A')}

## Restore Instructions
Copy {keystore_path.name} into the project's config directory, and keystore.properties
into the project root.
"""

    destinations: list[str] = []

    # 1. Local Dev-Team Vault (~/.config/devteam/keystores/<project>/)
    if backup_vault:
        local_dest = get_local_vault_dir() / project.name
        local_dest.mkdir(parents=True, exist_ok=True)
        shutil.copy2(keystore_path, local_dest / keystore_path.name)
        if props_path and props_path.exists():
            shutil.copy2(props_path, local_dest / "keystore.properties")
            os.chmod(local_dest / "keystore.properties", 0o600)
        (local_dest / "metadata.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (local_dest / "README.md").write_text(readme_content, encoding="utf-8")
        destinations.append(str(local_dest))

    # 2. Google Drive on Mac (~/Google Drive/My Drive/App Keystores/<project>/)
    if backup_drive:
        drive_dir = get_google_drive_keystore_dir()
        if drive_dir:
            drive_dest = drive_dir / project.name
            drive_dest.mkdir(parents=True, exist_ok=True)
            shutil.copy2(keystore_path, drive_dest / keystore_path.name)
            if props_path and props_path.exists():
                shutil.copy2(props_path, drive_dest / "keystore.properties")
                os.chmod(drive_dest / "keystore.properties", 0o600)
            (drive_dest / "metadata.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            (drive_dest / "README.md").write_text(readme_content, encoding="utf-8")
            destinations.append(str(drive_dest))

    # 3. Optional GitHub Vault Repository
    if github_vault_repo:
        gh_dest = _sync_to_github_vault(project.name, keystore_path, props_path, manifest, readme_content, github_vault_repo)
        if gh_dest:
            destinations.append(gh_dest)

    return {
        "ok": len(destinations) > 0,
        "project": project.name,
        "destinations": destinations,
        "certificate": cert_meta,
    }


def _sync_to_github_vault(
    project_name: str,
    keystore_path: Path,
    props_path: Path | None,
    manifest: dict,
    readme_content: str,
    vault_repo: str,
) -> str | None:
    """Clone or pull a private GitHub vault repo and push updated keystore."""
    tmp_dir = Path.home() / ".config" / "devteam" / ".tmp_vault"
    try:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)

        # Clone or initialize
        clone_cmd = ["gh", "repo", "clone", vault_repo, str(tmp_dir)]
        clone_res = subprocess.run(clone_cmd, capture_output=True, text=True)
        if clone_res.returncode != 0:
            return None

        proj_dir = tmp_dir / project_name
        proj_dir.mkdir(parents=True, exist_ok=True)

        shutil.copy2(keystore_path, proj_dir / keystore_path.name)
        if props_path and props_path.exists():
            shutil.copy2(props_path, proj_dir / "keystore.properties")
        (proj_dir / "metadata.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (proj_dir / "README.md").write_text(readme_content, encoding="utf-8")

        subprocess.run(["git", "add", "."], cwd=tmp_dir, capture_output=True)
        subprocess.run(["git", "commit", "-m", f"backup(keystore): update {project_name}"], cwd=tmp_dir, capture_output=True)
        push_res = subprocess.run(["git", "push", "origin", "main"], cwd=tmp_dir, capture_output=True)

        if push_res.returncode == 0:
            return f"github:{vault_repo}/{project_name}"
    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)

    return None


def verify_backup(project: Path) -> tuple[bool, str]:
    """Check whether a valid backup exists in Google Drive or local vault."""
    info = find_keystore_info(project)
    if not info:
        return False, "no keystore found in project"

    keystore_name = info["keystore_path"].name

    # Check Google Drive
    drive_dir = get_google_drive_keystore_dir()
    if drive_dir:
        drive_file = drive_dir / project.name / keystore_name
        if drive_file.is_file() and drive_file.stat().st_size > 0:
            return True, f"Google Drive backup verified: {drive_file}"

    # Check Local Vault
    local_file = get_local_vault_dir() / project.name / keystore_name
    if local_file.is_file() and local_file.stat().st_size > 0:
        return True, f"Local vault backup verified: {local_file}"

    return False, "no off-repo backup found in Google Drive or ~/.config/devteam/keystores/"
