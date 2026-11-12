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
import subprocess
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


def ping(timeout: int = 8) -> tuple[bool, str]:
    """Is the Stitch MCP answering right now?

    The tools the agent sees come from the host's MCP connection, not from this
    module: when that connection blips (ECONNRESET, a 5xx, a restart), the
    session's tool catalog silently loses `stitch.*` and every design task looks
    impossible. This check answers the only question that matters - can we reach
    the endpoint with the key we hold - so a dead MCP is REPORTED, not
    discovered halfway through a task.
    """
    url = os.environ.get("STITCH_MCP_URL") or DEFAULT_URL
    try:
        res = _rpc("tools/list", {}, timeout=timeout)
        n = len(res.get("tools") or [])
        return (n > 0, f"{n} tool(s) at {url}")
    except Exception as exc:  # noqa: BLE001 - a probe must never raise
        return (False, f"{url}: {str(exc)[:140]}")


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


def _clean_project_id(project_id: str) -> str:
    return str(project_id or "").strip().split("/")[-1]


def _clean_screen_id(screen_id: str) -> str:
    return str(screen_id or "").strip().split("/")[-1]


def _clean_asset_id(asset_id: str) -> str:
    return str(asset_id or "").strip().split("/")[-1]


def create_project(title: str) -> str:
    project = call("create_project", {"title": title})
    name = str(project.get("name") or "")
    if not name:
        raise StitchError(f"Stitch created no project: {project}")
    return name


def get_project(project_id: str) -> dict[str, Any]:
    pid = _clean_project_id(project_id)
    return call("get_project", {"name": f"projects/{pid}"})


def ping(timeout: int = 10) -> int:
    """How many tools the Stitch MCP answers with, right now.

    The host loads MCP tools once per catalog rebuild; a remote server that is
    slow or restarting silently loses its tools for that turn (seen 2026-11-10:
    stitch vanished mid-run after ECONNRESET). This client talks to the same
    endpoint over plain HTTP and is the path scripted work should use, so a
    dropped catalog never blocks a design task.
    """
    r = _rpc("tools/list", {}, timeout=timeout)
    return len(r.get("tools") or [])


def list_screens(project_id: str, timeout: int = 300) -> list[dict[str, Any]]:
    pid = _clean_project_id(project_id)
    out = call("list_screens", {"projectId": pid}, timeout=timeout)
    return list(out.get("screens") or [])


def get_screen(project_id: str, screen_id: str) -> dict[str, Any]:
    raw = str(screen_id or "").strip()
    if raw.startswith("projects/") and "/screens/" in raw:
        name = raw
    else:
        name = f"projects/{_clean_project_id(project_id)}/screens/{_clean_screen_id(raw)}"
    return call("get_screen", {"name": name})


def list_design_systems(project_id: str = "") -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    pid = _clean_project_id(project_id)
    if pid:
        params["projectId"] = pid
    out = call("list_design_systems", params)
    return list(out.get("designSystems") or out.get("design_systems") or [])


def apply_design_system(project_id: str, asset_id: str,
                        screen_instance_ids: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Apply an existing Stitch design system to screen instances without redesigning."""
    pid = _clean_project_id(project_id)
    aid = _clean_asset_id(asset_id)
    instances = list(screen_instance_ids or [])
    if not instances:
        proj = get_project(pid)
        for inst in proj.get("screenInstances") or []:
            iid = str(inst.get("id") or "")
            src = str(inst.get("sourceScreen") or "")
            if iid and src:
                instances.append({"id": iid, "sourceScreen": src})
    if not instances:
        raise StitchError(f"no screen instances found in project {pid} to apply design system {aid}")
    return call("apply_design_system", {
        "projectId": pid,
        "assetId": aid,
        "selectedScreenInstances": instances,
    })


def _extract_screens(res: dict[str, Any]) -> list[dict[str, Any]]:
    designs = []
    for comp in res.get("outputComponents") or []:
        design = (comp or {}).get("design") or {}
        designs.extend(design.get("screens") or [])
    return [s for s in designs if (s.get("htmlCode") or {}).get("name")]


def _pick_screen(res: dict[str, Any], prefer_square: bool = False) -> dict[str, Any] | None:
    with_art = _extract_screens(res)
    if not with_art:
        return None
    if prefer_square:
        for s in reversed(with_art):
            if str(s.get("width")) == str(s.get("height")) and s.get("width"):
                return s
    return with_art[-1]


def _poll_changed_screens(project_id: str, before: dict[str, str],
                          timeout: int = 900, interval: int = 20) -> list[dict[str, Any]]:
    """Poll list_screens for newly created or updated screens after a slow/dropped call.

    The BUDGET is the whole poll, not one iteration: each `list_screens` is itself
    an MCP round trip, and an unbounded one let a tweak that the editor never
    produced sit in the poll for 20+ minutes past its own deadline (2026-11-13: a
    `stitch-tweak` hung until it was killed, and the run had no design export).
    Every read now gets what is left of the budget, so a dead editor surfaces as a
    refusal the caller can act on instead of a wedged run.
    """
    pid = _clean_project_id(project_id)
    deadline = time.time() + timeout
    while True:
        remaining = deadline - time.time()
        if remaining <= 0:
            return []
        time.sleep(min(interval, remaining))
        budget = max(5, int(deadline - time.time()))
        try:
            screens = list_screens(pid, timeout=min(budget, 60))
        except StitchError:
            if time.time() >= deadline:
                return []
            continue
        changed = []
        for s in screens:
            name = str(s.get("name") or "")
            html_name = str((s.get("htmlCode") or {}).get("name") or "")
            if html_name and before.get(name) != html_name:
                changed.append(s)
        if changed:
            return changed


def generate_icon(project_id: str, prompt: str, *, device: str = "AGNOSTIC",
                  timeout: int = 900, log=None) -> dict[str, Any]:
    """Ask Stitch for the icon artboard and return the screen that carries it.

    Generation is slow by nature. A timeout is not a failure: the screen keeps
    being produced server-side, so the newest screen of the project is polled
    instead of re-asking (a retry would produce a second, competing icon).
    Uses GEMINI_3_8_FLASH for maximum precision and layout craft.
    """
    pid = _clean_project_id(project_id)
    say = log or (lambda *_: None)
    before = {str(s.get("name") or ""): str((s.get("htmlCode") or {}).get("name") or "")
              for s in list_screens(pid)}
    try:
        res = call("generate_screen_from_text", {
            "projectId": pid, "prompt": prompt, "deviceType": device,
            "modelId": "GEMINI_3_8_FLASH",
        }, timeout=timeout)
        screen = _pick_screen(res, prefer_square=True)
        if screen:
            return screen
        say("  stitch: generated but returned no screen reference — polling the project")
    except StitchError as exc:
        say(f"  stitch: generation call did not return ({str(exc)[:160]}) — polling the project")
    changed = _poll_changed_screens(pid, before, timeout=timeout)
    if changed:
        return changed[-1]
    raise StitchError(
        f"Stitch produced no icon screen within {timeout}s — the project is "
        f"{pid}; `brand --design` resumes by adopting its newest screen")


def generate_screen(project_id: str, prompt: str, *, device: str = "MOBILE",
                    model_id: str = "GEMINI_3_8_FLASH",
                    design_system: str | None = None,
                    timeout: int = 900, log=None) -> dict[str, Any]:
    """Ask Stitch for a new mobile screen design using Gemini 3.8 Flash."""
    pid = _clean_project_id(project_id)
    say = log or (lambda *_: None)
    before = {str(s.get("name") or ""): str((s.get("htmlCode") or {}).get("name") or "")
              for s in list_screens(pid)}
    params: dict[str, Any] = {
        "projectId": pid,
        "prompt": prompt,
        "deviceType": device,
        "modelId": model_id,
    }
    if design_system:
        params["designSystem"] = design_system
    try:
        res = call("generate_screen_from_text", params, timeout=timeout)
        screen = _pick_screen(res, prefer_square=False)
        if screen:
            return screen
        say("  stitch: generated but returned no screen reference — polling the project")
    except StitchError as exc:
        say(f"  stitch: generation call did not return ({str(exc)[:160]}) — polling the project")
    changed = _poll_changed_screens(pid, before, timeout=timeout)
    if changed:
        return changed[-1]
    raise StitchError(
        f"Stitch produced no screen within {timeout}s for project {pid}")


def edit_screens(project_id: str, screen_ids: list[str], prompt: str, *,
                 device: str = "MOBILE", model_id: str = "GEMINI_3_8_FLASH",
                 timeout: int = 900, log=None) -> list[dict[str, Any]]:
    """Apply a targeted tweak to existing Stitch screen(s) via edit_screens (never retries)."""
    pid = _clean_project_id(project_id)
    ids = [_clean_screen_id(sid) for sid in screen_ids if str(sid or "").strip()]
    if not ids:
        raise StitchError("edit_screens requires at least one screen id")
    say = log or (lambda *_: None)
    before = {str(s.get("name") or ""): str((s.get("htmlCode") or {}).get("name") or "")
              for s in list_screens(pid)}
    params: dict[str, Any] = {
        "projectId": pid,
        "selectedScreenIds": ids,
        "prompt": prompt,
        "deviceType": device,
        "modelId": model_id,
    }
    try:
        res = call("edit_screens", params, timeout=timeout)
        screens = _extract_screens(res)
        if screens:
            return screens
        say("  stitch: edit_screens returned no screen reference — polling for updated screen")
    except StitchError as exc:
        say(f"  stitch: edit_screens did not return ({str(exc)[:160]}) — polling (no retry)")
    changed = _poll_changed_screens(pid, before, timeout=timeout)
    if changed:
        return changed
    raise StitchError(
        f"Stitch edit_screens produced no updated screen within {timeout}s for {pid} ({ids})")


def generate_variants(project_id: str, screen_ids: list[str], prompt: str, *,
                      creative_range: str = "REFINE",
                      aspects: list[str] | None = None,
                      variant_count: int = 1,
                      device: str = "MOBILE", model_id: str = "GEMINI_3_8_FLASH",
                      timeout: int = 900, log=None) -> list[dict[str, Any]]:
    """Generate controlled variants of existing screen(s), defaulting to REFINE."""
    pid = _clean_project_id(project_id)
    ids = [_clean_screen_id(sid) for sid in screen_ids if str(sid or "").strip()]
    if not ids:
        raise StitchError("generate_variants requires at least one screen id")
    say = log or (lambda *_: None)
    before = {str(s.get("name") or ""): str((s.get("htmlCode") or {}).get("name") or "")
              for s in list_screens(pid)}
    variant_opts: dict[str, Any] = {
        "creativeRange": creative_range,
        "variantCount": max(1, min(5, int(variant_count))),
    }
    if aspects:
        variant_opts["aspects"] = list(aspects)
    params: dict[str, Any] = {
        "projectId": pid,
        "selectedScreenIds": ids,
        "prompt": prompt,
        "variantOptions": variant_opts,
        "deviceType": device,
        "modelId": model_id,
    }
    try:
        res = call("generate_variants", params, timeout=timeout)
        screens = _extract_screens(res)
        if screens:
            return screens
        say("  stitch: generate_variants returned no screen reference — polling")
    except StitchError as exc:
        say(f"  stitch: generate_variants did not return ({str(exc)[:160]}) — polling (no retry)")
    changed = _poll_changed_screens(pid, before, timeout=timeout)
    if changed:
        return changed
    raise StitchError(
        f"Stitch generate_variants produced no variant within {timeout}s for {pid} ({ids})")


def update_screen_content(project_id: str, screen_id: str, *, html: str | None = None,
                          png: bytes | None = None) -> dict[str, Any]:
    """Overwrite a screen IN PLACE — the id survives, so nothing has to be retired.

    `edit_screens` and `generate_*` ALWAYS return a new screen id, and Stitch has
    no delete-screen call, so the screen they replaced would linger in the
    project forever (BookSwap reached 51 live screens for 34 surfaces this way).
    Writing the reviewed result back onto the surface's own id keeps one screen
    per surface: the manifest, the export directory and RETIRED.md stop drifting.

    Proven 2026-11-11 on BookSwap: same id, same title, same geometry, new file.
    """
    name = (f"projects/{_clean_project_id(project_id)}"
            f"/screens/{_clean_screen_id(screen_id)}")
    out: dict[str, Any] = {}
    if html is not None:
        out = call("update_screen", {
            "name": name,
            "htmlCodeBase64": base64.b64encode(html.encode()).decode(),
            "updateMask": ["html_code"],
        })
    if png:
        out = call("update_screen", {
            "name": name,
            "screenshotBase64": base64.b64encode(png).decode(),
            "updateMask": ["screenshot"],
        })
    return out


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


def _fetch_file_by_url(download_url: str, timeout: int = 120) -> bytes:
    """Download a Stitch file straight from its serving URL.

    `download_file` answers for HTML but 404s ("Requested entity was not found")
    for the screenshot blobs of the same screen, which silently left every
    exported screen without its `screen.png` — the design gate then refused every
    reuse of an existing screen. The serving URL the screen payload carries is
    authoritative, so it is the fallback.
    """
    url = str(download_url or "").strip()
    if not url:
        raise StitchError("no downloadUrl on the file")
    _, key = _credentials()
    req = urllib.request.Request(url, headers={"X-Goog-Api-Key": key})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:200]
        raise StitchError(f"file download failed: HTTP {exc.code} — {detail}")
    except Exception as exc:
        raise StitchError(f"file download failed: {exc}")


def _render_html_png(html_path: Path, out_png: Path, width: int, height: int,
                     log=None) -> bool:
    """Render an exported screen's own HTML to `screen.png`.

    Stitch stopped serving the screenshot blob for every screen created before
    ~2026-11-01 (the file entity answers 404 while `get_screen` still names it),
    so an export that trusted the blob shipped a design spec with no picture and
    the design gate refused every reuse. The HTML Stitch DOES serve is the same
    screen, so its own render is the snapshot: width/height come from the screen
    payload, never guessed.
    """
    log = log or (lambda *_: None)
    try:
        from .marketing_asset_generator import _find_chrome_executable
    except Exception:
        return False
    chrome = _find_chrome_executable()
    if not chrome:
        log(f"  ! {html_path.name}: no Chrome to render the screen snapshot")
        return False
    w = max(320, int(width or 780))
    h = max(320, int(height or 1768))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run([chrome, "--headless", "--disable-gpu", "--hide-scrollbars",
                        "--force-device-scale-factor=1",
                        f"--window-size={w},{h}",
                        f"--screenshot={out_png.resolve()}", html_path.resolve().as_uri()],
                       capture_output=True, text=True, timeout=120)
    except Exception as exc:  # noqa: BLE001 - a render must never kill the export
        log(f"  ! {html_path.name}: screen render failed — {exc}")
        return False
    return out_png.is_file() and out_png.stat().st_size > 0


def export_one_screen(screen: dict[str, Any], out_dir: Path, log=None,
                      *, logical_id: str | None = None,
                      title_hint: str | None = None) -> dict[str, Any] | None:
    """Download one screen into <out_dir>/<logical_id>/.

    `edit_screens` FORKS: a tweak returns a new screen id and the old one stays in
    the project. If the export took the new id as its directory, every tweak would
    add a second copy of the same surface to the repo. `logical_id` keeps the
    stable name (the manifest entry) and the live Stitch id is recorded beside it.

    A generated screen sometimes comes back with NO title, and the entry then
    recorded the raw Stitch id as its title. Everything downstream that resolves a
    screen BY TITLE (`design-consult --record` → `verify`'s design gate) then reports
    "the exported snapshot is missing" for a screen that is right there, and the run
    only recovers by re-recording the answer against the id. `title_hint` is the name
    the caller asked for, used whenever Stitch returns nothing usable.
    """
    log = log or (lambda *_: None)
    sid = str(screen.get("name") or "").split("/")[-1]
    title = str(screen.get("title") or "").strip()
    if not title or title == sid:
        title = str(title_hint or "").strip() or sid
    if not sid:
        return None
    dir_id = str(logical_id or sid)
    out_dir = Path(out_dir)
    screen_dir = out_dir / dir_id
    screen_dir.mkdir(parents=True, exist_ok=True)
    entry: dict[str, Any] = {
        "id": dir_id,
        "stitch_id": sid,
        "title": title,
        "surface": screen.get("surface") or "",
    }
    if screen.get("deviceType"):
        entry["deviceType"] = screen["deviceType"]
    html_name = str((screen.get("htmlCode") or {}).get("name") or "")
    if html_name:
        try:
            html = call("download_file", {"name": html_name})
            text = html.get("html") or ""
            if text:
                (screen_dir / "index.html").write_text(text)
                entry["html"] = f"screens/{dir_id}/index.html"
        except StitchError as exc:
            log(f"  ! {title}: html export failed — {exc}")
    shot_name = str((screen.get("screenshot") or {}).get("name") or "")
    shot_url = str((screen.get("screenshot") or {}).get("downloadUrl") or "")
    if shot_name or shot_url:
        png: bytes = b""
        try:
            shot = call("download_file", {"name": shot_name}) if shot_name else {}
            b64 = shot.get("image") or ""
            if b64:
                png = base64.b64decode(b64)
        except StitchError as exc:
            log(f"  · {title}: screenshot via the MCP file API failed ({exc}) — trying its serving URL")
        if not png and shot_url:
            try:
                png = _fetch_file_by_url(shot_url)
            except StitchError as exc:
                log(f"  ! {title}: screenshot export failed — {exc}")
        if png:
            (screen_dir / "screen.png").write_bytes(png)
            entry["screenshot"] = f"screens/{dir_id}/screen.png"
        else:
            html_file = screen_dir / "index.html"
            if html_file.is_file() and _render_html_png(
                    html_file, screen_dir / "screen.png",
                    int(screen.get("width") or 0), int(screen.get("height") or 0),
                    log=log):
                entry["screenshot"] = f"screens/{dir_id}/screen.png"
                entry["snapshot_source"] = "rendered-from-exported-html"
                log(f"  ~ {title}: Stitch serves no screenshot blob; rendered the "
                    "exported design HTML at its own size")
    log(f"  ✓ {title} → {screen_dir}")
    return entry


def export_screens(project_id: str, out_dir: Path, *,
                   screen_ids: set[str] | None = None, log=None) -> dict[str, Any]:
    """Download every screen's HTML and screenshot into a tracked directory.

    The Stitch screen is the design spec (owner directive 2026-11-09: there is no
    docs/design.md). A spec that lives only in the Stitch cloud cannot be read by
    a worktree, diffed in review, or compared to a render — so the export is a
    deliverable, committed beside the code.
    """
    log = log or (lambda *_: None)
    pid = _clean_project_id(project_id)
    screens = list_screens(pid)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = {_clean_screen_id(s) for s in screen_ids} if screen_ids else None
    manifest: list[dict[str, Any]] = []
    for s in screens:
        sid = str(s.get("name") or "").split("/")[-1]
        if wanted and sid not in wanted:
            continue
        entry = export_one_screen(s, out_dir, log=log)
        if entry:
            manifest.append(entry)
    return {"project_id": pid,
            "synced_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "canonical_screens": manifest}
