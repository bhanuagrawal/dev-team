"""Storefront editorial review — the marketing pass over Play screenshots.

Rendering answers "does this look like the app?". This module answers the
question that actually moves installs: WHICH frames ship, in WHAT order, and
which are weak enough to cut. The judgment is an LLM's (a reviewer role model,
or the orchestrating agent authoring the JSON), recorded as data so the
renderer, the critic and the publisher all read the same decision.

Two guardrails the model cannot override, because they are policy, not taste:

1. A monetization surface (paywall / purchase / subscription / upgrade / price)
   never ships in the listing. A visitor who has not installed the app cannot
   act on it, it answers a question they have not asked yet, and it spends a
   scarce frame on the transaction instead of the product promise.
2. Play's own floor: at least two screenshots, every kept scene ranked exactly
   once. A scene the review does not rank is NOT shipped (silence never ships
   an asset).

Nothing project-shaped lives here: the vocabulary is generic product terms and
the scenes come from the project's own docs/storefront.json.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .core import now_iso, profile_of, run, save_json

SPEC_REL = "docs/storefront.json"
REVIEW_REL = "docs/storefront-review.json"

# Product vocabulary, not project facts: these words name the transaction
# surface, so a scene carrying them is in-app commerce, not the product.
MONETIZATION_TOKENS = (
    "paywall", "purchase", "subscribe", "subscription", "upgrade",
    "unlock", "pricing", "price", "buy now", "in-app",
)

MIN_KEPT = 2          # Play rejects a listing with fewer phone screenshots
MAX_KEPT = 8          # Play accepts eight; more frames is not better


def _slug(scene_id: str) -> str:
    """`01_home` -> `home`; the stable part of a scene id."""
    return re.sub(r"^\d+[-_]+", "", str(scene_id)).strip() or str(scene_id)


def scene_text(scene: dict[str, Any]) -> str:
    """Everything a reviewer can see about a frame, lowercased."""
    parts = [str(scene.get(k) or "") for k in ("id", "badge", "headline", "sub", "composable")]
    return " ".join(parts).lower()


def monetization_reason(scene: dict[str, Any]) -> str | None:
    """Why this scene must not ship, or None. Deterministic and absolute."""
    blob = scene_text(scene)
    hit = next((t for t in MONETIZATION_TOKENS if t in blob), None)
    if not hit:
        return None
    return (f"shows a monetization surface ('{hit}') — a listing visitor has not "
            "installed the app, so a price/paywall frame spends a working frame on "
            "the transaction instead of the product promise")


def hard_rejects(spec: dict[str, Any]) -> list[dict[str, str]]:
    out = []
    for scene in spec.get("scenes") or []:
        why = monetization_reason(scene)
        if why:
            out.append({"id": str(scene.get("id")), "reason": why, "policy": "monetization"})
    return out


def spec_fingerprint(spec: dict[str, Any]) -> str:
    """Scene identity + copy, so a stale review cannot outlive its spec."""
    payload = [
        {"id": s.get("id"), "badge": s.get("badge"), "headline": s.get("headline"),
         "sub": s.get("sub"), "composable": s.get("composable")}
        for s in (spec.get("scenes") or [])
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- #
# the prompt: a marketing expert, not a renderer
# --------------------------------------------------------------------------- #
def build_prompt(project: Path, spec: dict[str, Any]) -> str:
    profile = profile_of(project)
    name = profile.get("name") or project.name
    listing = project / "distribution/listing/en-US"
    title = (listing / "title.txt").read_text(errors="ignore").strip() if (listing / "title.txt").is_file() else name
    short = ""
    for f in ("short_description.txt", "short-description.txt"):
        if (listing / f).is_file():
            short = (listing / f).read_text(errors="ignore").strip()
            break

    rows = []
    for s in spec.get("scenes") or []:
        sig = str(s.get("composable") or "").split("(")[0]
        rows.append(
            f"- id: {s.get('id')}\n"
            f"  badge: {s.get('badge')}\n"
            f"  headline: {s.get('headline')}\n"
            f"  sub: {s.get('sub')}\n"
            f"  composable: {sig}"
        )

    return f"""You are the marketing lead for a Google Play listing. You are NOT
reviewing code; you are deciding which screenshots earn a scarce slot in the
store, and in what order a first-time visitor should meet them.

PRODUCT
  name: {name}
  listing title: {title}
  short description: {short}

CANDIDATE FRAMES
{chr(10).join(rows)}

HOW A GOOD PLAY LISTING IS ORDERED (apply this, do not recite it)
  1. Frame 1 is the hero: the product's promise visible in one glance, from the
     app's real UI. If a frame needs the caption to be understood, it is not the
     hero.
  2. Frame 2 is the differentiator: the mechanism that makes this app worth
     installing instead of the default OS experience.
  3. Then proof and utility: concrete outcomes, privacy, insight — the frames
     that answer "will this actually work for me?".
  4. Settings screens, menus, onboarding walls and empty states are weak: they
     sell configuration, not the benefit. Put them last or cut them.
  5. Cut any frame that shows a paywall, purchase, subscription, price or
     upgrade surface. A visitor has not installed the app yet; the transaction
     is not the product promise, and it is policy here that such a frame never
     ships. This rule overrides your ranking.
  6. Cut any frame that is visually weak or misleading: blank/placeholder app
     icons, cut-off content, contradictory copy (e.g. "pay once" beside a
     monthly price), unreadable contrast, or a screen a user would never show
     off.
  7. Fewer, stronger frames beat filling every slot. Play needs at least two.

Return STRICT JSON only, no prose, no markdown:
{{
  "order": ["<id>", ...],           // every KEPT frame, best first, no duplicates
  "rejected": [{{"id": "<id>", "reason": "<one sentence>"}}],
  "notes": {{"<id>": "<why it sits where it does, or what to fix>"}},
  "rationale": "<two sentences: the story the sequence tells>"
}}
"""


def _extract_json(text: str) -> dict[str, Any]:
    """The model may wrap JSON in prose or fences; take the outermost object."""
    raw = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.S)
    if fence:
        raw = fence.group(1)
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the review output")
    return json.loads(raw[start:end + 1])


def llm_review(project: Path, spec: dict[str, Any], log: Any = print,
               timeout: int = 600) -> dict[str, Any]:
    """Ask the configured reviewer model. Raises when no model is configured —
    the orchestrator then authors the JSON and applies it (never silently
    falls back to filename order)."""
    from .core import agent_argv, role_cfg
    cfg = role_cfg("reviewer") or role_cfg("product_synthesis")
    argv = agent_argv(cfg, build_prompt(project, spec))
    if not argv:
        raise SystemExit(
            "storefront review needs a model: set roles.reviewer.command in "
            "config/runtimes.json or DEVTEAM_AGENT_CMD, or author the review JSON "
            "and pass --apply <file> (the orchestrating agent may do the review).")
    if log:
        log(f"~ editorial review via {argv[0]} (model {cfg.get('model', 'default')})")
    r = run(argv, None, timeout=timeout)
    if r.returncode != 0:
        raise SystemExit("storefront review model failed: " +
                         ((r.stdout or "") + (r.stderr or ""))[-600:])
    try:
        raw = _extract_json(r.stdout)
    except Exception as exc:
        raise SystemExit(f"storefront review returned invalid JSON: {exc}")
    return normalize_review(spec, raw, source="llm",
                            model=str(cfg.get("model") or "default"))


def normalize_review(spec: dict[str, Any], raw: dict[str, Any],
                     source: str, model: str = "") -> dict[str, Any]:
    """Validate a review against the spec and the policy.

    - hard policy rejects always win over the model;
    - unknown ids are dropped, but a kept scene that the model never ranked is
      REJECTED (silence must not ship a frame);
    - a review that leaves fewer than two frames is invalid (Play refuses it).
    """
    scenes = [s for s in (spec.get("scenes") or []) if s.get("id")]
    by_id = {str(s["id"]): s for s in scenes}
    policy = {r["id"]: r for r in hard_rejects(spec)}

    rejected: dict[str, dict[str, str]] = {}
    for entry in raw.get("rejected") or []:
        if not isinstance(entry, dict):
            continue
        sid = str(entry.get("id") or "").strip()
        if sid in by_id:
            rejected[sid] = {"id": sid, "reason": str(entry.get("reason") or "").strip(),
                             "policy": "review"}

    order: list[str] = []
    for sid in raw.get("order") or []:
        sid = str(sid).strip()
        if sid in by_id and sid not in order and sid not in rejected and sid not in policy:
            order.append(sid)

    # A kept-but-unranked scene is a hole in the decision, not an implicit keep.
    for sid in by_id:
        if sid not in order and sid not in rejected and sid not in policy:
            rejected[sid] = {"id": sid, "reason": "not ranked by the editorial review",
                             "policy": "unranked"}

    # Frames the review retires from the SPEC entirely (e.g. a paywall scene
    # deleted from docs/storefront.json). They are not candidates any more, but
    # recording them here lets the renderer delete the frame Play already has.
    for entry in raw.get("retired") or []:
        if not isinstance(entry, dict):
            continue
        sid = str(entry.get("id") or "").strip()
        if sid and sid not in by_id:
            rejected[sid] = {"id": sid, "reason": str(entry.get("reason") or "").strip(),
                             "policy": "retired"}

    # Policy always wins.
    rejected.update(policy)
    order = [sid for sid in order if sid not in policy]
    order = order[:MAX_KEPT]

    if len(order) < MIN_KEPT:
        raise ValueError(
            f"editorial review kept {len(order)} frame(s); Play needs at least "
            f"{MIN_KEPT}. Candidate ids: {', '.join(by_id)}")

    notes = {str(k): str(v) for k, v in (raw.get("notes") or {}).items() if str(k) in by_id}
    return {
        "spec_sha": spec_fingerprint(spec),
        "reviewed_at": now_iso(),
        "source": source,
        "model": model,
        "order": order,
        "rejected": sorted(rejected.values(), key=lambda r: r["id"]),
        "notes": notes,
        "rationale": str(raw.get("rationale") or "").strip(),
    }


def save_review(project: Path, review: dict[str, Any]) -> Path:
    path = project / REVIEW_REL
    save_json(path, review)
    return path


def load_review(project: Path, spec: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """The review that belongs to THIS spec, or None (stale review never applies)."""
    path = project / REVIEW_REL
    if not path.is_file():
        return None
    try:
        review = json.loads(path.read_text())
    except Exception:
        return None
    if spec is not None and review.get("spec_sha") != spec_fingerprint(spec):
        return None
    return review


def ordered_scenes(spec: dict[str, Any], review: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Scenes in shipping order. No review => spec order (the caller says so)."""
    by_id = {str(s["id"]): s for s in (spec.get("scenes") or []) if s.get("id")}
    if not review:
        return [by_id[sid] for sid in by_id]
    return [by_id[sid] for sid in review.get("order") or [] if sid in by_id]


def rejected_ids(review: dict[str, Any] | None) -> set[str]:
    return {str(r.get("id")) for r in (review or {}).get("rejected") or []}


def format_review(review: dict[str, Any]) -> str:
    lines = [
        "STOREFRONT EDITORIAL REVIEW",
        "=" * 60,
        f"source: {review.get('source')}   model: {review.get('model') or '-'}   "
        f"spec: {review.get('spec_sha')}",
    ]
    if review.get("rationale"):
        lines.append(f"story: {review['rationale']}")
    lines.append("\nSHIPPING ORDER")
    for i, sid in enumerate(review.get("order") or [], 1):
        note = (review.get("notes") or {}).get(sid)
        lines.append(f"  {i}. {sid}" + (f" — {note}" if note else ""))
    lines.append("\nCUT")
    for r in review.get("rejected") or []:
        lines.append(f"  ✗ {r.get('id')} — {r.get('reason')}")
    if not review.get("rejected"):
        lines.append("  (none)")
    lines.append("=" * 60)
    return "\n".join(lines)
