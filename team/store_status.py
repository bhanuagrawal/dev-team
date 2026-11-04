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
                  r"violation of .* policy", r"\bpolicy issue")),
    ("review-pending", (r"not yet sent for review", r"not sent for review",
                        r"ready to send for review", r"ready to publish",
                        r"send for review")),
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
    extra = ""
    if snap.get("submission_rejected") and snap.get("state") != "rejected":
        extra += " · a submission was REJECTED (policy issue open)"
    if snap.get("production_staged"):
        extra += (f" · production rollout staged for {snap.get('production_staged_version') or '?'}"
                  " (promotion is owner-only)")
    return f"store:       {snap.get('state', 'unknown')}{stale} ({when}){note}{extra}"


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
// Open THIS app: the home page links each app as `a[aria-label^="View "]`, and
// the row around it carries the package id. Reached by matching the package,
// never by hardcoding one project's console ids.
await page.evaluate((pkg) => {
  const links = [...document.querySelectorAll('a[aria-label^="View "]')];
  const hit = links.find((a) => {
    let el = a;
    for (let i = 0; i < 8 && el; i++) {
      if ((el.innerText || "").includes(pkg)) return true;
      el = el.parentElement;
    }
    return false;
  });
  if (hit) hit.click();
}, "PLAY_PACKAGE");
await page.waitForTimeout(5000);
const text = await page.locator("body").innerText();
// Publishing overview is one click from the dashboard and its URL carries the
// account/app ids, so it is reached by CLICKING (the console's own link), never
// by hardcoding ids that belong to one project.
let publishing = "";
try {
  const link = page.getByText(/Go to Publishing overview/i).first();
  if (await link.count()) {
    await link.click();
    await page.waitForTimeout(5000);
    publishing = await page.locator("body").innerText();
  }
} catch (e) {}
return JSON.stringify({ url: page.url(), text: text, publishing: publishing });
"""


def _console_text(project: Path, account: str = "", package: str = "") -> tuple[dict, str]:
    """({dashboard, publishing}, why-not) from the Play Console in the owner's browser."""
    from .core import run

    js = (_SYNC_JS.replace("PLAY_ACCOUNT", account.replace('"', ""))
                 .replace("PLAY_PACKAGE", package.replace('"', "")))
    r = run(["browser-control", "execute", js], project, timeout=240)
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        return {}, out.strip()[-400:] or "browser-control failed"
    # The relay prints the returned value verbatim; the script returns one JSON
    # string so the page body (newlines and all) arrives escaped, not as a
    # pretty-printed JS object this module would have to re-parse by eye.
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('{"url"'):
            try:
                return json.loads(line), ""
            except Exception:
                break
    return {}, "browser-control returned no readable page body"


def _row_state(text: str, package: str) -> dict[str, str]:
    """Cut the console's dashboard text down to THIS package's card."""
    lines = [l.strip() for l in text.splitlines()]
    idx = next((i for i, l in enumerate(lines) if package and package in l), None)
    if idx is None:
        return {}
    # A card is: name, [chip], [status], package, last updated — the status sits in
    # the <=4 lines above the package id. Classified as ONE string: a card can say
    # both "1 policy issue" and "changes not yet sent for review", and the open
    # issue is the fact that matters.
    window = [l for l in lines[max(0, idx - 4):idx] if l]
    state, phrase = classify(" | ".join(window))
    if state == "unknown":
        return {}
    return {"state": state, "phrase": phrase, "card": " | ".join(window)}


def _status_block(text: str) -> dict[str, str]:
    """The app DASHBOARD's "Update status" panel — the same verdict, closer up.

    The home page's card and the app dashboard say the same thing in different
    words ("1 policy issue / Changes rejected" vs "Update status / Update
    rejected"). Reading both means a sync works from whichever page the browser
    happens to open.
    """
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    idx = next((i for i, l in enumerate(lines) if re.fullmatch(r"update status", l, re.I)), None)
    if idx is None:
        return {}
    window = lines[idx:idx + 4]
    state, phrase = classify(" | ".join(window))
    if state == "unknown":
        return {}
    return {"state": state, "phrase": phrase, "card": " | ".join(window)}


def sync(project: Path, package: str, account: str = "") -> dict[str, Any]:
    page_text, why = _console_text(project, account, package)
    if why:
        return {"status": "unavailable", "reason": why}
    text = page_text.get("text", "")
    row = _row_state(text, package) or _status_block(text)
    if not row:
        return {"status": "unavailable",
                "reason": f"the console did not show a status for {package} — is the account "
                          f"logged in and is the app on this developer account?"}
    extra = _publishing_state(page_text.get("publishing", ""))
    snap = write(project, row["state"], note=row.get("card", ""), source="play-console",
                 evidence=row.get("phrase", ""), extra=extra)
    return {"status": "OK", "snapshot": snap}


def _publishing_state(text: str) -> dict[str, Any]:
    """What is staged in Publishing overview — including a PRODUCTION rollout.

    Found live on 2026-11-04: the rejected build sat in Publishing overview as a
    staged production rollout ("Start full rollout", 1.0.9 (10010)) next to the
    country changes. Pressing the page's single "Submit N changes for review"
    would therefore have promoted the very release Play had just rejected, and
    nothing in the pipeline could see it — git, CI and the run ledger all said
    "internal testing". Recorded, never acted on: production promotion is the
    owner's own manual step.
    """
    out: dict[str, Any] = {}
    if not text:
        return out
    m = re.search(r"submit\s+(\d+)\s+changes?\s+for\s+review", text, re.I)
    if m:
        out["pending_for_review"] = int(m.group(1))
    staged = re.findall(r"(\d+\.\d+(?:\.\d+)?(?:-[0-9A-Za-z.-]+)?)\s*\((\d+)\)", text)
    if staged and re.search(r"start full rollout", text, re.I):
        out["production_staged"] = True
        out["production_staged_version"] = f"{staged[0][0]} ({staged[0][1]})"
    if re.search(r"were rejected|rejected by google", text, re.I):
        out["submission_rejected"] = True
    return out
