"""What Play thinks of the last thing we shipped — a snapshot the planner can read.

Why this exists
---------------
A delivery can be green end to end and still be dead at the store. On 2026-11-04
the build passed, CI uploaded the AAB, the internal track took it — and Play then
rejected the app update under the Misleading Claims policy. Every signal the
planner had (git, CI conclusion, the run ledger) said "released"; not one of them
could see the store. The next cycle planned polish while the app sat rejected.

The Play Developer API does not expose policy state: console-only, human-readable,
behind a login. So this is a SNAPSHOT with an explicit age, not a live feed:

    devteam store-status <project> --sync      # read the console through the browser
    devteam store-status <project> --state rejected --note "…" --location "App icon"
    devteam store-status <project>             # what the planner will see, with age

`research` injects it as `signals.store_status` and says how old it is. Stale is
not the same as good: past `DEVTEAM_STORE_STATUS_MAX_HOURS` (default 72) the
planner is told the snapshot is stale and must not claim the store is fine.
Nothing here ever *concludes* an app is approved — it only reports what was
observed, when, and where.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REL = ".pipeline/store_status.json"
MAX_HOURS = float(os.environ.get("DEVTEAM_STORE_STATUS_MAX_HOURS", "72"))

# The console's own words → a state the code can order work by. Ordered from
# worst to best; the planner escalates on the first three.
STATES = ("rejected", "suspended", "review-pending", "in-review", "approved",
          "published", "unknown")
_HINTS = (
    ("suspended", (r"app (has been )?removed", r"suspended", r"blocked by google")),
    ("rejected", (r"update rejected", r"changes rejected", r"app update rejected",
                  r"violation of .* policy")),
    ("review-pending", (r"not sent for review", r"ready to send for review",
                        r"changes ready to publish", r"send for review")),
    ("in-review", (r"in review", r"pending review", r"changes in review")),
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _path(project: Path) -> Path:
    return Path(project) / REL


def read(project: Path) -> dict[str, Any]:
    """The recorded snapshot with its age; `{}` when nothing was ever recorded."""
    p = _path(project)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text())
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    stamp = str(data.get("observed_at") or "")
    try:
        seen = datetime.fromisoformat(stamp)
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        data["age_hours"] = round((datetime.now(timezone.utc) - seen).total_seconds() / 3600, 1)
        data["stale"] = data["age_hours"] > MAX_HOURS
    except Exception:
        data["age_hours"] = None
        data["stale"] = True
    return data


def write(project: Path, state: str, note: str = "", *, location: str = "",
          source: str = "console", evidence: str = "", version: str = "",
          extra: dict | None = None) -> dict[str, Any]:
    state = (state or "unknown").strip().lower()
    if state not in STATES:
        state = "unknown"
    data = {
        "observed_at": _now(),
        "state": state,
        "note": note.strip(),
        "location": location.strip(),
        "source": source,
        "evidence": evidence.strip()[:2000],
        "version": version.strip(),
    }
    if extra:
        data.update(extra)
    p = _path(project)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n")
    return read(project)


def classify(text: str) -> tuple[str, str]:
    """(state, matched phrase) from console text — evidence, never a guess.

    Order matters: "changes rejected" must win over the "ready to publish" chip
    that Play shows for the SAME listing while an unrelated update is rejected.
    """
    low = (text or "").lower()
    for state, patterns in _HINTS:
        for pat in patterns:
            m = re.search(pat, low)
            if m:
                return state, m.group(0)
    return "unknown", ""


def line(project: Path) -> str:
    """One line for `devteam status`: the store's verdict, or that we never looked."""
    snap = read(project)
    if not snap:
        return "store:       no snapshot — `devteam store-status <project> --sync` reads the console"
    age = snap.get("age_hours")
    when = f"{age}h ago" if isinstance(age, (int, float)) else "unknown age"
    stale = " ⚠ STALE" if snap.get("stale") else ""
    note = f" — {snap['note'][:110]}" if snap.get("note") else ""
    return f"store:       {snap.get('state', 'unknown')}{stale} ({when}){note}"


# --------------------------------------------------------------------------- #
# sync — read the console through the owner's own browser, then record it
# --------------------------------------------------------------------------- #
_SYNC_JS = r"""
await page.goto("https://play.google.com/console/developers", { waitUntil: "domcontentloaded", timeout: 45000 });
await page.waitForTimeout(2000);
// The console opens on a developer-account picker when several accounts exist.
// Its rows are material-select-item[role=option]; the business name is a child
// div. Click the account PLAY_ACCOUNT names, else the first one.
const rows = page.locator("material-select-item[role=option]");
const n = await rows.count();
if (n > 0) {
  const want = "PLAY_ACCOUNT";
  let target = rows.first();
  if (want) {
    for (let i = 0; i < n; i++) {
      const txt = (await rows.nth(i).innerText()).trim();
      if (txt.includes(want)) { target = rows.nth(i); break; }
    }
  }
  await target.click();
  await page.waitForTimeout(5000);
}
const text = await page.locator("body").innerText();
return JSON.stringify({ url: page.url(), text: text });
"""


def _console_text(project: Path, account: str = "") -> tuple[str, str]:
    """(text, why-not) from the Play Console dashboard in the owner's browser."""
    from .core import run

    js = _SYNC_JS.replace("PLAY_ACCOUNT", account.replace('"', ""))
    r = run(["browser-control", "execute", js], project, timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        return "", out.strip()[-400:] or "browser-control failed"
    # The relay prints the returned value verbatim; the script returns one JSON
    # string so the page body (newlines and all) arrives escaped, not as a
    # pretty-printed JS object this module would have to re-parse by eye.
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('{"url"'):
            try:
                return json.loads(line).get("text", ""), ""
            except Exception:
                break
    return "", "browser-control returned no readable page body"


def _row_state(text: str, package: str) -> dict[str, str]:
    """Cut the console's dashboard text down to THIS package's card."""
    lines = [l.strip() for l in text.splitlines()]
    idx = next((i for i, l in enumerate(lines) if package and package in l), None)
    if idx is None:
        return {}
    # A card is: name, [chip], [status], package, last updated — the status sits in
    # the <=4 lines above the package id.
    window = [l for l in lines[max(0, idx - 4):idx] if l]
    for line in reversed(window):
        state, phrase = classify(line)
        if state != "unknown":
            return {"state": state, "phrase": phrase, "card": " | ".join(window)}
    return {"state": "unknown", "phrase": "", "card": " | ".join(window)}


def sync(project: Path, package: str, account: str = "") -> dict[str, Any]:
    text, why = _console_text(project, account)
    if why:
        return {"status": "unavailable", "reason": why}
    row = _row_state(text, package)
    if not row:
        return {"status": "unavailable",
                "reason": f"the console dashboard did not show {package} — is the account "
                          f"logged in and is the app on this developer account?"}
    snap = write(project, row["state"], note=row.get("card", ""), source="play-console",
                 evidence=row.get("phrase", ""))
    return {"status": "OK", "snapshot": snap}
