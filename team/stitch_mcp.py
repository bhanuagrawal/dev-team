"""Talk to Google Stitch over its MCP endpoint — from code, not from a skill.

Why this exists
---------------
The icon was the one deliverable an agent produced by following prose: read the
prompt from `storefront-brand --stitch-prompt`, open Stitch, generate, download,
adopt. Prose is missed, and the run that shipped the previous icon left no
artboard and no provenance behind — only a commit message claiming "stitch".
(Owner, 2026-11-01: "skills get missed very easily … we need more determinism".)

So the Stitch call is made here: a tiny MCP Streamable-HTTP client (the endpoint
is stateless, so no session handshake is needed). `brand_change` drives it, and
the artboard it returns is recorded in the commit.

Credentials are HOST configuration, never project data and never team code: read
`STITCH_API_KEY` from the environment, else the Stitch entry in the owner's
Antigravity / OpenCode MCP config.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_URL = "https://stitch.googleapis.com/mcp"
PROTOCOL = "2025-06-18"


class StitchError(RuntimeError):
    pass


def _credentials() -> tuple[str, str]:
    url = (os.environ.get("STITCH_MCP_URL") or DEFAULT_URL).strip()
    key = (os.environ.get("STITCH_API_KEY") or "").strip()
    if key:
        return url, key
    home = Path.home()
    cfg = home / ".gemini/antigravity/mcp_config.json"
    if cfg.is_file():
        try:
            servers = (json.loads(cfg.read_text()).get("mcpServers") or {})
        except Exception:
            servers = {}
        for server in servers.values():
            blob = json.dumps(server)
            if "stitch.googleapis.com" not in blob:
                continue
            m = re.search(r"X-Goog-Api-Key:\s*([A-Za-z0-9._\-]+)", blob)
            if m:
                return url, m.group(1)
    cfg = home / ".config/opencode/opencode.jsonc"
    if cfg.is_file():
        text = cfg.read_text(errors="ignore")
        block = re.search(r'"stitch"\s*:\s*\{(.{0,1200}?)\n\s*\}', text, re.S)
        if block:
            m = re.search(r'X-Goog-Api-Key"\s*:\s*"([^"]+)"', block.group(1))
            if m:
                return url, m.group(1)
    raise StitchError(
        "no Stitch credential: set STITCH_API_KEY (or add the stitch MCP server to "
        "the host's MCP config) — the icon cannot be generated without it")


def _rpc(method: str, params: dict[str, Any] | None = None,
         timeout: int = 300) -> dict[str, Any]:
    url, key = _credentials()
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params or {}}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "X-Goog-Api-Key": key,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        raise StitchError(f"Stitch MCP {method} failed: HTTP {exc.code} — {detail}")
    except Exception as exc:
        raise StitchError(f"Stitch MCP {method} failed: {exc}")
    # A stateless server answers JSON; Streamable HTTP may answer SSE.
    payload = raw.strip()
    if payload.startswith("event:") or payload.startswith("data:"):
        for line in payload.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                break
    try:
        data = json.loads(payload)
    except Exception:
        raise StitchError(f"Stitch MCP {method} returned no JSON: {payload[:300]}")
    if data.get("error"):
        raise StitchError(f"Stitch MCP {method} error: {data['error']}")
    return data.get("result") or {}


def call(tool: str, arguments: dict[str, Any], timeout: int = 300) -> dict[str, Any]:
    """Invoke one Stitch tool and return its structured payload.

    MCP returns the payload as JSON text in `content[0].text` (and sometimes as
    `structuredContent`); both shapes are accepted so a server-side change does
    not silently read as an empty result.
    """
    result = _rpc("tools/call", {"name": tool, "arguments": arguments}, timeout=timeout)
    if isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    for part in result.get("content") or []:
        text = part.get("text") if isinstance(part, dict) else None
        if not text:
            continue
        try:
            return json.loads(text)
        except Exception:
            continue
    return result


def create_project(title: str) -> str:
    project = call("create_project", {"title": title})
    name = str(project.get("name") or "")
    if not name:
        raise StitchError(f"Stitch created no project: {project}")
    return name


def list_screens(project_id: str) -> list[dict[str, Any]]:
    out = call("list_screens", {"projectId": project_id})
    return list(out.get("screens") or [])


def generate_icon(project_id: str, prompt: str, *, device: str = "AGNOSTIC",
                  timeout: int = 900, log=None) -> dict[str, Any]:
    """Ask Stitch for the icon artboard and return the screen that carries it.

    Generation is slow by nature. A timeout is not a failure: the screen keeps
    being produced server-side, so the newest screen of the project is polled
    instead of re-asking (a retry would produce a second, competing icon).
    """
    say = log or (lambda *_: None)
    try:
        res = call("generate_screen_from_text", {
            "projectId": project_id, "prompt": prompt, "deviceType": device,
        }, timeout=timeout)
        screen = _pick_screen(res)
        if screen:
            return screen
        say("  stitch: generated but returned no screen reference — polling the project")
    except StitchError as exc:
        say(f"  stitch: generation call did not return ({str(exc)[:160]}) — polling the project")
    deadline = time.time() + timeout
    seen = {s.get("name") for s in list_screens(project_id)}
    while time.time() < deadline:
        time.sleep(20)
        for s in list_screens(project_id):
            if s.get("name") not in seen and s.get("htmlCode"):
                return s
    raise StitchError(
        f"Stitch produced no icon screen within {timeout}s — the project is "
        f"{project_id}; `brand --design` resumes by adopting its newest screen")


def _pick_screen(res: dict[str, Any]) -> dict[str, Any] | None:
    designs = []
    for comp in res.get("outputComponents") or []:
        design = (comp or {}).get("design") or {}
        designs.extend(design.get("screens") or [])
    with_art = [s for s in designs if (s.get("htmlCode") or {}).get("name")]
    if not with_art:
        return None
    # The icon artboard is square; preference, then the last generated.
    for s in reversed(with_art):
        if str(s.get("width")) == str(s.get("height")) and s.get("width"):
            return s
    return with_art[-1]


def download(screen: dict[str, Any]) -> tuple[bytes, str]:
    """The design itself: (bytes, mime). Handles SVG, HTML and raster alike.

    A Stitch icon is a 512x512 SVG, but the API returns it in the `image`
    (base64) field with mimeType `image/svg+xml` — reading only `html` would call
    the design empty.
    """
    name = str((screen.get("htmlCode") or {}).get("name") or "")
    if not name:
        raise StitchError("the screen carries no design file")
    out = call("download_file", {"name": name})
    if out.get("html"):
        return str(out["html"]).encode(), str((screen.get("htmlCode") or {}).get(
            "mimeType") or "text/html")
    if out.get("image"):
        meta = out.get("imageMetadata") or {}
        return base64.b64decode(out["image"]), str(meta.get("mimeType") or "application/octet-stream")
    raise StitchError("Stitch returned neither html nor image bytes")
