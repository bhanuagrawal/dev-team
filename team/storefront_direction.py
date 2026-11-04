"""The direction layer: what the listing should LOOK like, decided before anything is drawn.

Why this exists
---------------
The asset pipeline had two layers and skipped the first. The editorial review
decides *which* scenes ship and in what order; the composer decides where the
phone and the copy go. Nothing decided *what the listing is trying to say* — the
single idea frame 1 sells, the copy angle per frame, which accent to lead with,
what to avoid. Without it the plate prompt fell back to a generic recipe ("near
black, an accent glow, film grain") and the composition was whatever the vision
model proposed first: plausible, undirected, and impossible to review, because
there was nothing to review it against.

This module is that decision, as a committed artifact:

    docs/storefront-direction.json

It is READ by three consumers and trusted by none of them blindly:

  * the plate prompt  — concept, palette emphasis, and the avoid-list shape the
                        atmospheric plate Gemini paints;
  * the layout brief  — the vision model composes the poster around the stated
                        concept, not around its own taste;
  * the scene copy    — the review ranks scenes; the direction says what each one
                        is arguing, so copy and art agree.

What it can NEVER influence: the screen. The screenshot stays the app's own render
composited at 1:1 by code (`storefront_frames.adopt` refuses anything else), so a
wrong direction can waste a poster, never fake the product.

Determinism: the file is data. `--seed` writes a skeleton from the spec and brand
tokens with empty judgements, `--apply` validates an authored one, `--check`
verifies that it still matches the spec it was written against.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DIRECTION_REL = "docs/storefront-direction.json"
VERSION = 1


def _now() -> str:
    from .core import now_iso

    return now_iso()


def spec_fingerprint(spec: dict[str, Any]) -> str:
    from .storefront_review import spec_fingerprint as fp

    return fp(spec)


def _brand(project: Path) -> dict[str, str]:
    from .design import extract_design_tokens

    try:
        tokens = extract_design_tokens(project)
    except Exception:
        tokens = {}
    return {
        "accent": str(tokens.get("primary_accent") or "#00E5FF"),
        "base": str(tokens.get("surface_base") or "#0A0E14"),
    }


def seed(project: Path, spec: dict[str, Any]) -> dict[str, Any]:
    """A valid skeleton with the judgements deliberately EMPTY.

    Scaffolding, never invention: the structure comes from the spec and the brand
    tokens, and every field that needs a decision is blank so a run cannot mistake
    a template for a direction.
    """
    brand = _brand(project)
    return {
        "_comment": ("What the listing is trying to say, decided before anything is drawn. "
                     "Fill every judgement, keep the evidence list honest, then lock it with "
                     "`devteam storefront-direction <project> --apply <file>`. The screen is "
                     "never affected by this file: the screenshot is the app's own render."),
        "version": VERSION,
        "spec_sha": spec_fingerprint(spec),
        "created": _now(),
        "locked_by": "",            # owner | agent — must be filled
        "concept": "",              # ONE idea, the thing frame 1 sells
        "audience": "",             # who this is for, in their words
        "copy_angle": {},           # scene id -> the argument that frame makes
        "palette": {
            "mode": "dark",
            "accent": brand["accent"],
            "base": brand["base"],
            "emphasis": "",         # where the accent leads the eye
        },
        "avoid": [
            "a monetization surface (paywall/purchase/price) in any frame",
            "inventing metrics, features or copy the app does not have",
            "a repainted or mocked app screen — the screen is always the real render",
        ],
        "evidence": [],             # [{"source": ..., "note": ..., "date": ...}]
        "rationale": "",            # why this direction, in two sentences
    }


def validate(direction: dict[str, Any], spec: dict[str, Any]) -> list[str]:
    """Every reason this direction cannot drive a run, most specific first."""
    issues: list[str] = []
    if not isinstance(direction, dict):
        return ["direction is not a JSON object"]
    if direction.get("version") != VERSION:
        issues.append(f"version must be {VERSION} (found {direction.get('version')!r})")
    if direction.get("spec_sha") != spec_fingerprint(spec):
        issues.append("spec_sha does not match docs/storefront.json — re-seed and re-decide")
    if not str(direction.get("locked_by") or "").strip():
        issues.append("locked_by is empty — say who decided (owner or agent)")
    if not str(direction.get("concept") or "").strip():
        issues.append("concept is empty — one idea the listing sells, in one sentence")
    angles = direction.get("copy_angle") or {}
    scene_ids = [str(s.get("id")) for s in (spec.get("scenes") or []) if s.get("id")]
    missing = [s for s in scene_ids if not str(angles.get(s) or "").strip()]
    if missing:
        issues.append("copy_angle has no angle for: " + ", ".join(missing))
    unknown = [k for k in angles if k not in scene_ids]
    if unknown:
        issues.append("copy_angle names scenes not in the spec: " + ", ".join(unknown))
    palette = direction.get("palette") or {}
    if not str(palette.get("emphasis") or "").strip():
        issues.append("palette.emphasis is empty — where should the accent lead the eye?")
    if not (direction.get("avoid") or []):
        issues.append("avoid is empty — say what this listing must not do")
    if not (direction.get("evidence") or []):
        issues.append("evidence is empty — a direction with no evidence is a guess "
                      "(one competitor listing, review or market datapoint is enough)")
    return issues


def load(project: Path, spec: dict[str, Any]) -> dict[str, Any] | None:
    """The direction that belongs to THIS spec, or None (a stale one never applies)."""
    path = project / DIRECTION_REL
    if not path.is_file():
        return None
    try:
        direction = json.loads(path.read_text())
    except Exception:
        return None
    return direction if not validate(direction, spec) else None


def load_raw(project: Path) -> dict[str, Any] | None:
    path = project / DIRECTION_REL
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def save(project: Path, direction: dict[str, Any]) -> Path:
    from .core import save_json

    path = project / DIRECTION_REL
    save_json(path, direction)
    return path


# --------------------------------------------------------------------------- #
# consumers
# --------------------------------------------------------------------------- #
def plate_brief(direction: dict[str, Any], scene: dict[str, Any] | None = None) -> str:
    """The line the Gemini plate prompt should carry instead of a generic recipe."""
    palette = direction.get("palette") or {}
    parts = []
    concept = str(direction.get("concept") or "").strip()
    if concept:
        parts.append(f"The listing's idea is: {concept}.")
    emphasis = str(palette.get("emphasis") or "").strip()
    if emphasis:
        parts.append(f"Lead the eye with {palette.get('accent', 'the accent')} {emphasis}.")
    mode = str(palette.get("mode") or "").strip()
    if mode and mode != "dark":
        parts.append(f"The listing is {mode}-first.")
    # The owner's look decision (2026-11-04: frames read flat) applies to the plate
    # too: it is the light the whole frame sits in.
    look = str(direction.get("look") or "").strip()
    if look:
        parts.append(f"Look: {look}.")
    avoid = [a for a in (direction.get("avoid") or []) if "screen" not in a.lower()]
    if avoid:
        parts.append("Do not imply: " + "; ".join(avoid[:3]) + ".")
    if scene and str(scene.get("id")) in (direction.get("copy_angle") or {}):
        parts.append(f"This frame argues: {direction['copy_angle'][str(scene['id'])]}")
    return " ".join(parts)


def layout_brief(direction: dict[str, Any], scene: dict[str, Any]) -> str:
    """What the vision model is told before it composes numbers."""
    angle = (direction.get("copy_angle") or {}).get(str(scene.get("id")), "")
    emphasis = (direction.get("palette") or {}).get("emphasis", "")
    lines = [
        f"Concept: {direction.get('concept', '')}",
        f"Audience: {direction.get('audience', '')}",
        f"This frame argues: {angle}",
        f"Accent should lead: {emphasis}",
        f"Avoid: {'; '.join(direction.get('avoid') or [])}",
        "Compose for a first-time viewer scrolling the store: the phone is the proof, "
        "the copy is the promise. Never place art or text over the screen slot.",
    ]
    return "\n".join(line for line in lines if line.split(":", 1)[-1].strip())


def evidence_from_research(project: Path) -> list[dict[str, str]]:
    """Pointers from the project's last research pass, if one ran.

    Empty is a valid answer: the caller is told the direction is un-evidenced
    rather than being handed an invented citation.
    """
    path = project / ".pipeline/research.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    out: list[dict[str, str]] = []
    created = str(data.get("researched_at") or data.get("created") or "")[:10]
    for comp in (data.get("competitors") or [])[:3]:
        if isinstance(comp, dict) and comp.get("name"):
            out.append({"source": f"competitor: {comp['name']}",
                        "note": str(comp.get("note") or comp.get("positioning") or "")[:200],
                        "date": created})
    for item in (data.get("market") or [])[:3]:
        if isinstance(item, dict) and item.get("title"):
            out.append({"source": str(item["title"])[:120],
                        "note": str(item.get("summary") or "")[:200], "date": created})
    return out
