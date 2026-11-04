"""Gemini browser automation runner — prefers headless Playwright, falls back to browser-control.

Executes Playwright scripts against Gemini (gemini.google.com) for poster and brand
asset generation:
1. Standalone Headless Playwright (default when authenticated):
   - Uses a persistent user data directory (~/.config/dev-team/gemini_profile).
   - Runs headless Chrome without opening visible tabs or stealing window focus.
   - Completely isolated from daily browsing and multiple open tabs.
2. Extension Relay Fallback (browser-control):
   - Falls back to `browser-control execute` if the persistent profile is not yet
     initialized or requires interactive re-authentication.

Zero project names — generic across all projects.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from .marketing_asset_generator import _find_chrome_executable

DEFAULT_PROFILE = Path.home() / ".config" / "dev-team" / "gemini_profile"

_NODE_RUNNER = """
const fs = require('fs');
const path = require('path');
const os = require('os');

function resolvePlaywright() {
  try { return require.resolve('playwright-core'); } catch (e) {}
  try { return require.resolve('playwright'); } catch (e) {}
  const cand = [
    process.env.PLAYWRIGHT_PATH || '',
    '/opt/homebrew/lib/node_modules/@opencode-ai/browser-control/node_modules/playwright-core',
    '/usr/local/lib/node_modules/@opencode-ai/browser-control/node_modules/playwright-core',
  ];
  for (const c of cand) {
    if (c && fs.existsSync(c)) return c;
  }
  return null;
}

const pwPath = resolvePlaywright();
if (!pwPath) {
  console.error(JSON.stringify({ ok: false, error: "playwright module not found" }));
  process.exit(2);
}

const scriptFile = process.argv[2];
if (!scriptFile || !fs.existsSync(scriptFile)) {
  console.error(JSON.stringify({ ok: false, error: "script file missing" }));
  process.exit(2);
}

const userScript = fs.readFileSync(scriptFile, 'utf8');
const { chromium } = require(pwPath);
const profileDir = process.env.DEVTEAM_GEMINI_PROFILE || path.join(os.homedir(), '.config/dev-team/gemini_profile');
const chromeExe = process.env.DEVTEAM_CHROME_BIN || '';
const isHeadless = process.env.DEVTEAM_GEMINI_HEADLESS !== '0';

(async () => {
  let context;
  try {
    const opts = {
      headless: isHeadless,
      viewport: { width: 1280, height: 900 },
      args: ['--disable-blink-features=AutomationControlled'],
      ignoreDefaultArgs: ['--enable-automation'],
    };
    if (chromeExe) opts.executablePath = chromeExe;

    context = await chromium.launchPersistentContext(profileDir, opts);
    const pages = context.pages().filter(p => p.url().includes("gemini.google.com"));
    const page = pages[0] || context.pages()[0] || await context.newPage();

    const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
    // The user script runs in its own BLOCK so a `const page = ...` (or `context`)
    // inside it SHADOWS the injected handle instead of colliding with the
    // parameter name. A parameter collision is a SyntaxError, and a SyntaxError
    // here kills the whole headless path before Chrome is even asked anything —
    // which is how every Gemini asset run reported "browser execution failed"
    // while a script that declared nothing worked (observed 2026-11-04).
    const fn = new AsyncFunction('context', 'page', 'fs', `{\n${userScript}\n}`);
    const result = await fn(context, page, fs);
    console.log(JSON.stringify({ ok: true, value: result }));
  } catch (err) {
    console.error(err);
    console.log(JSON.stringify({ ok: false, error: String(err && err.message || err) }));
    process.exit(1);
  } finally {
    if (context) await context.close().catch(() => {});
  }
})();
"""


def gemini_profile_dir() -> Path:
    """Location of the persistent Gemini Playwright profile."""
    env = os.environ.get("DEVTEAM_GEMINI_PROFILE")
    if env:
        return Path(env).expanduser().resolve()
    return DEFAULT_PROFILE


def is_headless_ready() -> tuple[bool, str]:
    """Check whether standalone headless Playwright can drive Gemini."""
    profile = gemini_profile_dir()
    if not profile.is_dir():
        return False, f"no persistent profile at {profile} (run `devteam gemini-auth` to initialize)"
    chrome = _find_chrome_executable()
    if not chrome:
        return False, "Google Chrome or Chromium binary not found"
    node = shutil.which("node")
    if not node:
        return False, "node binary not found on PATH"
    return True, f"ready (profile: {profile}, chrome: {chrome})"


def release_stale_profile_lock(profile: Path, log: Callable[[str], None] | None = None) -> bool:
    """Drop Chrome's singleton files when no process holds the profile.

    A headless run that is killed (timeout, Ctrl-C, a crashed node) leaves
    `SingletonLock` behind. The next launch then cannot open the profile, the
    runner reports only "browser execution failed", and the fallback relay is
    asked to paint with no tab attached — so a single killed run takes the whole
    asset pipeline down until a human deletes the lock by hand. The lock names
    its owner (`<host>-<pid>`); if that pid is gone, the lock is not a lock.
    """
    lock = profile / "SingletonLock"
    if not lock.is_symlink() and not lock.exists():
        return False
    owner = ""
    try:
        owner = os.readlink(lock)
    except OSError:
        return False
    pid = owner.rsplit("-", 1)[-1]
    alive = False
    if pid.isdigit():
        try:
            os.kill(int(pid), 0)
            alive = True
        except OSError:
            alive = False
    if alive:
        return False
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            (profile / name).unlink()
        except OSError:
            pass
    if log:
        log(f"  gemini: cleared a stale profile lock (owner pid {pid or '?'} is gone)")
    return True


_HEADLESS_SESSION: tuple[bool, str] | None = None

_SESSION_PROBE = """
await page.goto("https://gemini.google.com/app", { waitUntil: "domcontentloaded" });
await page.waitForTimeout(3000);
const signedIn = await page.locator("button:has-text('Sign in'), a:has-text('Sign in')").count() === 0;
return { signedIn: signedIn, url: page.url() };
"""


def headless_signed_in(log: Callable[[str], None] | None = None) -> tuple[bool, str]:
    """Is the persistent Gemini profile actually SIGNED IN?

    `is_headless_ready` only proves a profile directory and a Chrome binary exist,
    so a profile whose session expired kept reporting "ready" while every prompt
    produced nothing — and the run waited the full image timeout, then blamed the
    model ("no NEW image in the model's answer"). Signing in is a credential act:
    only the owner can do it (`devteam gemini-auth --force`). Everything else — the
    relay through the owner's own logged-in Chrome — is agent work, and this check
    is what lets the run take that path immediately instead of stalling.
    """
    global _HEADLESS_SESSION
    if _HEADLESS_SESSION is not None:
        return _HEADLESS_SESSION
    ready, reason = is_headless_ready()
    if not ready:
        _HEADLESS_SESSION = (False, reason)
        return _HEADLESS_SESSION
    profile = gemini_profile_dir()
    release_stale_profile_lock(profile, log)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as s_file:
        s_file.write(_SESSION_PROBE)
        script_path = s_file.name
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as r_file:
        r_file.write(_NODE_RUNNER)
        runner_path = r_file.name
    env = os.environ.copy()
    env["DEVTEAM_GEMINI_PROFILE"] = str(profile)
    chrome = _find_chrome_executable()
    if chrome:
        env["DEVTEAM_CHROME_BIN"] = chrome
    try:
        res = subprocess.run(["node", runner_path, script_path], capture_output=True,
                             text=True, timeout=180, env=env)
        value = (json.loads(res.stdout or "{}") or {}).get("value") or {}
    except Exception:
        value = {}
    finally:
        for p in (script_path, runner_path):
            try:
                os.unlink(p)
            except OSError:
                pass
    if value.get("signedIn"):
        _HEADLESS_SESSION = (True, "signed in")
    else:
        _HEADLESS_SESSION = (
            False,
            "the persistent Gemini profile is SIGNED OUT — every prompt returns "
            "nothing; re-authenticate once with `devteam gemini-auth --force` "
            "(owner), or let the run fall back to the relay in the owner's Chrome",
        )
    return _HEADLESS_SESSION


def execute(script: str, timeout: int = 900,
            log: Callable[[str], None] | None = None,
            fallback: bool = True) -> subprocess.CompletedProcess:
    """Execute a Playwright script against Gemini.

    Prefers standalone headless Playwright when a valid profile exists.
    Falls back to `browser-control execute --json` if headless execution is not
    ready or encounters an error.
    """
    ready, reason = is_headless_ready()
    if ready:
        signed, why = headless_signed_in(log)
        if not signed:
            if log:
                log(f"  gemini: headless profile unusable — {why}")
            ready, reason = False, why
    if ready:
        release_stale_profile_lock(gemini_profile_dir(), log)
        chrome = _find_chrome_executable()
        profile = gemini_profile_dir()
        if log:
            log(f"  gemini: running via headless Playwright (profile: {profile.name})")

        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as s_file:
            s_file.write(script)
            script_path = s_file.name

        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as r_file:
            r_file.write(_NODE_RUNNER)
            runner_path = r_file.name

        env = os.environ.copy()
        env["DEVTEAM_GEMINI_PROFILE"] = str(profile)
        if chrome:
            env["DEVTEAM_CHROME_BIN"] = chrome

        try:
            res = subprocess.run(
                ["node", runner_path, script_path],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            # If the script ran and produced valid JSON, return it
            if res.returncode == 0 and res.stdout.strip():
                try:
                    payload = json.loads(res.stdout)
                    if payload.get("ok"):
                        return res
                    if log:
                        log(f"  gemini: headless runner reported: {payload.get('error')}")
                except Exception:
                    pass
        except Exception as exc:
            if log:
                log(f"  gemini: headless run failed ({exc})")
        finally:
            Path(script_path).unlink(missing_ok=True)
            Path(runner_path).unlink(missing_ok=True)

        if not fallback:
            return subprocess.CompletedProcess(
                args=["node", "headless-runner"],
                returncode=1,
                stdout="",
                stderr="headless execution failed and fallback disabled",
            )
        if log:
            log("  gemini: falling back to browser-control extension relay")

    # Fallback to browser-control
    if log and not ready:
        log(f"  gemini: headless Playwright not active ({reason}) — using browser-control")

    return subprocess.run(
        ["browser-control", "execute", "--json", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def auth_flow(timeout: int = 300, log: Callable[[str], None] | None = None) -> bool:
    """Launch a headed Chrome instance to authenticate Gemini and save the profile."""
    profile = gemini_profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    chrome = _find_chrome_executable()
    if not chrome:
        if log:
            log("Google Chrome executable not found")
        return False

    script = """
    if (!page.url().includes("gemini.google.com")) {
      await page.goto("https://gemini.google.com/app", { waitUntil: "domcontentloaded" });
    }
    const start = Date.now();
    while (Date.now() - start < 280000) {
      const url = page.url();
      if (url.startsWith("https://gemini.google.com/app") && !url.includes("signin") && !url.includes("accounts.google.com")) {
        const composer = await page.locator("rich-textarea, [contenteditable=true]").count().catch(() => 0);
        if (composer > 0) return { ok: true, chat: url };
      }
      await page.waitForTimeout(2000);
    }
    return { ok: false, error: "login timed out" };
    """

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as s_file:
        s_file.write(script)
        script_path = s_file.name

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as r_file:
        r_file.write(_NODE_RUNNER)
        runner_path = r_file.name

    env = os.environ.copy()
    env["DEVTEAM_GEMINI_PROFILE"] = str(profile)
    env["DEVTEAM_CHROME_BIN"] = chrome
    env["DEVTEAM_GEMINI_HEADLESS"] = "0"  # Headed for login

    try:
        if log:
            log(f"Opening Chrome to https://gemini.google.com/app (profile: {profile})...")
        res = subprocess.run(
            ["node", runner_path, script_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
        if res.returncode == 0:
            payload = json.loads(res.stdout)
            if payload.get("ok") and payload.get("value", {}).get("ok"):
                if log:
                    log("Gemini authentication successful!")
                return True
        if log:
            log(f"Authentication was not completed: {res.stderr or res.stdout}")
        return False
    finally:
        Path(script_path).unlink(missing_ok=True)
        Path(runner_path).unlink(missing_ok=True)
