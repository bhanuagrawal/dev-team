"""Design Drift Validator & Screen Fidelity Auditor for Stitch MCP designs.

Audits generated or tweaked screens against the prompt specification or baseline HTML:
1. Feature Fidelity / Change Realization: Verifies required CTAs/telemetry or requested tweaks landed.
2. Preservation (Anti-Overstepping): On tweaks, checks that untouched baseline controls and headings survived.
3. Drift & Hallucination Detection: Catches unprompted features (unwanted OAuth, ads, cart, search bars).
4. Craft & Theme Coherence: Checks against artificial neon halos, Lorem Ipsum, and respects the project's actual theme mode (light/warm/dark) rather than assuming dark mode.
5. Generates a targeted remediation tweak directive if overstepping or a miss is detected.

Zero project names in code — generic across all dev-team projects.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .core import profile_of
from .design import extract_design_tokens, extract_html_summary


_SUSPICIOUS_PATTERNS: list[tuple[str, list[str]]] = [
    ("Google Sign-In / OAuth", [r"sign[ -]in with google", r"continue with apple", r"oauth"]),
    ("Social / Sharing Feed", [r"follow friends", r"news feed", r"community stories"]),
    ("Monetization / Ads", [r"upgrade to premium", r"subscribe now", r"sponsored", r"ad banner"]),
    ("E-Commerce / Cart", [r"shopping cart", r"checkout", r"\$\d+\.\d\d"]),
    ("Unasked Search Bar", [r"search players", r"search courts", r"type to search\.\.\."]),
]

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "onto", "only",
    "make", "keep", "add", "move", "update", "change", "tweak", "adjust", "fix",
    "screen", "button", "label", "section", "card", "text", "more", "less",
    "existing", "current", "should", "without", "instead", "preserve",
}


def _check_craft_and_hallucinations(
    prompt_text: str,
    generated_content: str,
    tokens: dict[str, str],
    *,
    baseline_text: str = "",
) -> tuple[list[str], list[str]]:
    content_lower = generated_content.lower()
    hallucinated: list[str] = []
    for label, patterns in _SUSPICIOUS_PATTERNS:
        prompt_has_it = any(re.search(pat, prompt_text, re.IGNORECASE) for pat in patterns)
        baseline_has_it = bool(baseline_text) and any(
            re.search(pat, baseline_text, re.IGNORECASE) for pat in patterns
        )
        gen_has_it = any(re.search(pat, generated_content, re.IGNORECASE) for pat in patterns)
        if gen_has_it and not prompt_has_it and not baseline_has_it:
            hallucinated.append(f"{label} (Not requested in prompt)")

    violations: list[str] = []
    if (
        re.search(r"box-shadow:[^;]*(?:0\s+0\s+(?:2\d|3\d|4\d|5\d)px|#[0-9a-fA-F]{6}33)", content_lower)
        or "radiant-aura" in content_lower
        or "glow-aura" in content_lower
    ):
        violations.append("Artificial neon glow / radiant halo detected (Violates human craftsmanship mandate)")
    if "lorem ipsum" in content_lower:
        violations.append("Lorem Ipsum dummy copy detected (Violates concrete copy law)")

    # Only enforce exact brand hex when tokens came from actual project artifacts,
    # not an archetype guess.
    if tokens.get("source") in ("stitch_tokens", "stitch_html", "design_doc"):
        accent = tokens["primary_accent"].lower()
        if accent and accent not in content_lower and "primary" not in content_lower and "accent" not in content_lower:
            violations.append(f"Missing authoritative brand accent ({tokens['primary_accent']})")

    return hallucinated, violations


def _parse_prompt_requirements(prompt_text: str) -> dict[str, Any]:
    """Extract expected CTAs, telemetry values, and rules from a compiled prompt."""
    reqs: dict[str, Any] = {
        "expected_ctas": [],
        "expected_telemetry": [],
    }

    lines = prompt_text.splitlines()
    current_section = ""
    for line in lines:
        line_clean = line.strip()
        if line_clean.startswith("=== SECTION"):
            current_section = line_clean
            continue

        if "EXACT CTAs" in current_section:
            if line_clean.startswith("- "):
                if line_clean.lower().startswith("- derive focused"):
                    continue
                item = line_clean[2:].split("(")[0].strip()
                if item:
                    reqs["expected_ctas"].append(item)
        elif "EXACT DATA TELEMETRY" in current_section or "REAL-WORLD COPY" in current_section:
            if line_clean.startswith("- "):
                parts = line_clean[2:].split(":")
                val = parts[-1].strip() if len(parts) > 1 else line_clean[2:].strip()
                if val and not val.lower().startswith("use realistic"):
                    reqs["expected_telemetry"].append(val)

    return reqs


def audit_design_drift(
    project: Path,
    prompt_text: str,
    generated_content: str,
    screen_name: str = "Screen",
) -> dict[str, Any]:
    """Audit generated screen HTML/code against the prompt spec."""
    profile = profile_of(project)
    name = profile.get("name") or project.name
    tokens = extract_design_tokens(project)
    reqs = _parse_prompt_requirements(prompt_text)

    if not generated_content.strip():
        return {
            "mode": "screen",
            "project": name,
            "screen": screen_name,
            "theme_mode": tokens.get("theme_mode", "light"),
            "overall_score": 0,
            "verdict": "FAIL",
            "status": "NO GENERATED CONTENT TO AUDIT",
            "dimensions": {},
            "missing_ctas": reqs["expected_ctas"],
            "found_ctas": [],
            "missing_telemetry": reqs["expected_telemetry"],
            "hallucinated_features": [],
            "anti_pattern_violations": ["Empty screen content"],
            "remediation_prompt": "",
        }

    content_lower = generated_content.lower()

    missing_ctas = []
    found_ctas = []
    for cta in reqs["expected_ctas"]:
        kw = re.sub(r"(?i)\b(button|zone|toggle|switch|pills?|deck|card|action)\b", "", cta).strip().lower()
        if kw and kw in content_lower:
            found_ctas.append(cta)
        else:
            missing_ctas.append(cta)

    missing_telemetry = []
    found_telemetry = []
    for tel in reqs["expected_telemetry"]:
        tel_clean = tel.strip().lower()
        if tel_clean and tel_clean in content_lower:
            found_telemetry.append(tel)
        else:
            missing_telemetry.append(tel)

    hallucinated_features, anti_pattern_violations = _check_craft_and_hallucinations(
        prompt_text, generated_content, tokens
    )

    total_ctas = len(reqs["expected_ctas"])
    if total_ctas == 0:
        # Standalone screen audit without pre-locked CTA list: verify real interactive controls exist
        summary = extract_html_summary(generated_content)
        has_controls = bool(summary["controls"]) or ("<button" in content_lower) or ("<a " in content_lower)
        cta_score = 50 if has_controls else 25
        if has_controls:
            found_ctas = summary["controls"][:6]
    else:
        cta_score = int((len(found_ctas) / total_ctas) * 50)

    total_tel = len(reqs["expected_telemetry"])
    tel_score = 25 if total_tel == 0 else int((len(found_telemetry) / total_tel) * 25)

    hallucination_score = max(0, 15 - min(15, len(hallucinated_features) * 5))
    brand_score = max(0, 10 - min(10, len(anti_pattern_violations) * 5))

    overall_score = cta_score + tel_score + hallucination_score + brand_score

    if overall_score >= 85 and not missing_ctas:
        verdict = "PASS"
        status = "ZERO DRIFT"
    elif overall_score >= 70:
        verdict = "WARNING"
        status = "MINOR DRIFT"
    else:
        verdict = "FAIL"
        status = "SIGNIFICANT DRIFT DETECTED"

    remediation_directives = []
    if missing_ctas:
        remediation_directives.append(f"Restore missing required CTAs: {', '.join(missing_ctas)}.")
    if hallucinated_features:
        remediation_directives.append(f"Remove hallucinated unprompted elements: {', '.join(hallucinated_features)}.")
    if anti_pattern_violations:
        remediation_directives.append(f"Fix aesthetic violations: {', '.join(anti_pattern_violations)}.")

    remediation_prompt = ""
    if remediation_directives:
        remediation_prompt = (
            f"SURGICAL CORRECTION FOR {screen_name.upper()} (EDIT IN PLACE — DO NOT REDESIGN):\n"
            + "\n".join(f"- {d}" for d in remediation_directives)
            + f"\nPreserve existing layout, palette ({tokens['surface_base']} / {tokens['primary_accent']}), and untouched controls."
        )

    return {
        "mode": "screen",
        "project": name,
        "screen": screen_name,
        "theme_mode": tokens.get("theme_mode", "light"),
        "overall_score": overall_score,
        "verdict": verdict,
        "status": status,
        "dimensions": {
            "cta_fidelity": {"score": cta_score, "max": 50, "found": len(found_ctas), "total": total_ctas},
            "telemetry_fidelity": {"score": tel_score, "max": 25, "found": len(found_telemetry), "total": total_tel},
            "hallucination_freedom": {"score": hallucination_score, "max": 15, "penalties": len(hallucinated_features)},
            "brand_compliance": {"score": brand_score, "max": 10, "violations": len(anti_pattern_violations)},
        },
        "missing_ctas": missing_ctas,
        "found_ctas": found_ctas,
        "missing_telemetry": missing_telemetry,
        "hallucinated_features": hallucinated_features,
        "anti_pattern_violations": anti_pattern_violations,
        "remediation_prompt": remediation_prompt,
    }


# A tweak is asked for in words, and Stitch complies about 90% of the time: one
# instruction in ten is silently dropped, and nothing in a "preservation" score
# notices, because the layout is intact. These two checks close that gap: the
# change actually landed, and nothing the product forbids came back with it.
_BANNED_RULES: list[tuple[str, str]] = [
    (r"\bescrow\b", "escrow does not exist - deposits settle in person"),
    (r"\bautomatically refunded\b", "no automatic refund exists"),
    (r"\bsplit fee\b", "no fees exist"),
    (r"\bzero deposits\b", "deposits exist (200/400/500)"),
    (r"\bswap pass\b", "invented artefact"),
    (r"\b(?:tier 1|sovereign)\b", "invented tier"),
    (r"\bcalibrated monthly\b", "invented mechanism"),
    (r"\bvery good\b", "condition is Brand New / Like New / Well-Loved"),
    (r"\bgood condition\b", "condition is Brand New / Like New / Well-Loved"),
    (r"\bmi\b", "miles - bands in km only"),
]

_DECIMAL_KM = re.compile(r"\b(\d+\.\d+)\s?km\b", re.I)


def banned_present(text: str) -> list[str]:
    """Product-forbidden copy found in a rendered screen, as readable reasons."""
    low = re.sub(r"\s+", " ", text or "").lower()
    hits = [why for pat, why in _BANNED_RULES if re.search(pat, low)]
    # The only decimal band the product allows is 1.5 km; anything else is an
    # exact distance wearing a unit.
    for value in _DECIMAL_KM.findall(text or ""):
        if value != "1.5":
            hits.append(f"decimal distance {value} km - use a band")
    return sorted(set(hits))


def assert_change_applied(baseline_html: str, tweaked_html: str,
                          change_request: str) -> list[str]:
    """Instructions the tweak was asked for and did not carry out.

    Reads the request the way it is written: a quoted phrase near delete/remove/
    drop/replace must be GONE; a quoted phrase named as a replacement must be
    PRESENT. Returns the survivors, so the caller can re-issue just those.
    """
    def _text(html: str) -> str:
        import html as _html
        t = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html or "", flags=re.S | re.I)
        t = re.sub(r"<[^>]+>", " ", t)
        return re.sub(r"\s+", " ", _html.unescape(t)).lower()

    after = _text(tweaked_html)
    req = change_request or ""
    req_low = req.lower()
    survivors: list[str] = []
    for phrase in re.findall(r"[\"'“”‘’]([^\"'“”‘’]{3,80})[\"'“”‘’]", req):
        needle = re.sub(r"\s+", " ", phrase).strip().lower()
        if not needle:
            continue
        # Is this phrase being removed, or being introduced?
        idx = req_low.find(needle)
        before = req_low[max(0, idx - 120): idx]
        immediate = before[-16:]
        # "replace A WITH B" introduces B; "delete A" removes A.
        introduced = any(op in immediate for op in ("with", "to ", "into", "as "))
        removing = (not introduced) and any(
            op in before for op in ("delete", "remove", "drop", "hide"))
        if removing:
            if needle in after:
                survivors.append(f"still present: \"{phrase.strip()}\"")
        else:
            if needle not in after:
                survivors.append(f"never landed: \"{phrase.strip()}\"")
    return survivors


def audit_tweak_drift(
    project: Path,
    baseline_html: str,
    tweaked_html: str,
    change_request: str,
    screen_name: str = "Screen",
    preserve: list[str] | None = None,
) -> dict[str, Any]:
    """Audit an in-place Stitch tweak for OVERSTEPPING (dropped controls/sections) and MISSES."""
    profile = profile_of(project)
    name = profile.get("name") or project.name
    tokens = extract_design_tokens(project)

    if not tweaked_html.strip():
        return {
            "mode": "tweak",
            "project": name,
            "screen": screen_name,
            "theme_mode": tokens.get("theme_mode", "light"),
            "overall_score": 0,
            "verdict": "FAIL",
            "status": "NO TWEAKED CONTENT",
            "dimensions": {},
            "dropped_controls": [],
            "dropped_headings": [],
            "missed_terms": [],
            "hallucinated_features": [],
            "anti_pattern_violations": ["Empty tweaked screen content"],
            "remediation_prompt": "",
        }

    import html as _html
    tweaked_text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", tweaked_html, flags=re.S | re.I)
    tweaked_text = re.sub(r"<[^>]+>", " ", tweaked_text)
    tweaked_text = _html.unescape(tweaked_text)
    tweaked_lower = re.sub(r"\s+", " ", tweaked_text).lower()
    change_lower = (change_request or "").lower()

    base_summary = extract_html_summary(baseline_html)
    kept_controls: list[str] = []
    dropped_controls: list[str] = []
    for ctrl in base_summary["controls"]:
        cl = ctrl.lower()
        # Skip checks on items the change request explicitly mentions replacing/removing
        explicitly_modified = any(
            w in change_lower for w in re.findall(r"[a-z0-9]{4,}", cl)
        ) and any(op in change_lower for op in ("remove", "delete", "replace", "rename", "drop", "hide"))
        if explicitly_modified:
            continue
        if cl in tweaked_lower:
            kept_controls.append(ctrl)
        else:
            dropped_controls.append(ctrl)

    kept_headings: list[str] = []
    dropped_headings: list[str] = []
    for hd in base_summary["headings"]:
        hl = hd.lower()
        explicitly_modified = any(
            w in change_lower for w in re.findall(r"[a-z0-9]{4,}", hl)
        ) and any(op in change_lower for op in ("remove", "delete", "replace", "rename", "drop", "hide"))
        if explicitly_modified:
            continue
        if hl in tweaked_lower:
            kept_headings.append(hd)
        else:
            dropped_headings.append(hd)

    for item in (preserve or []):
        il = str(item).strip().lower()
        if il and il not in tweaked_lower and item not in dropped_controls:
            dropped_controls.append(str(item).strip())

    # Change realization check: look for key action/content words from change_request
    req_terms = [
        w for w in re.findall(r"[a-z0-9]{4,}", change_lower)
        if w not in _STOPWORDS
    ]
    seen_terms: list[str] = []
    for w in req_terms:
        if w not in seen_terms:
            seen_terms.append(w)
    req_terms = seen_terms[:8]
    matched_terms = [w for w in req_terms if w in tweaked_lower]
    missed_terms = [w for w in req_terms if w not in tweaked_lower]

    hallucinated_features, anti_pattern_violations = _check_craft_and_hallucinations(
        change_request, tweaked_html, tokens, baseline_text=baseline_html
    )

    # Two checks a preservation score cannot make: did the instruction land, and
    # did anything forbidden come back with the new render.
    unapplied_changes = assert_change_applied(baseline_html, tweaked_html, change_request)
    banned_present_reasons = banned_present(tweaked_text)

    total_base = len(kept_controls) + len(dropped_controls) + len(kept_headings) + len(dropped_headings)
    kept_base = len(kept_controls) + len(kept_headings)
    preservation_score = 45 if total_base == 0 else int((kept_base / total_base) * 45)
    realization_score = 30 if not req_terms else int((len(matched_terms) / len(req_terms)) * 30)
    hallucination_score = max(0, 15 - min(15, len(hallucinated_features) * 5))
    craft_score = max(0, 10 - min(10, len(anti_pattern_violations) * 5))

    overall_score = preservation_score + realization_score + hallucination_score + craft_score

    dropped_ratio = (len(dropped_controls) + len(dropped_headings)) / total_base if total_base else 0.0
    if banned_present_reasons or unapplied_changes:
        verdict = "FAIL"
        status = ("FORBIDDEN COPY RENDERED" if banned_present_reasons
                  else "TWEAK INSTRUCTION NOT APPLIED")
    elif dropped_ratio > 0.35 or overall_score < 65:
        verdict = "FAIL"
        status = "OVERSTEPPING OR MISS DETECTED" if dropped_ratio > 0.35 else "TWEAK DRIFT DETECTED"
    elif overall_score >= 85 and not dropped_controls and not hallucinated_features:
        verdict = "PASS"
        status = "SURGICAL TWEAK PRESERVED"
    else:
        verdict = "WARNING"
        status = "MINOR TWEAK DRIFT"

    remediation_directives = []
    if dropped_controls or dropped_headings:
        lost = (dropped_controls + dropped_headings)[:6]
        remediation_directives.append(f"Restore accidentally removed existing elements: {', '.join(lost)}.")
    if missed_terms and realization_score < 15:
        remediation_directives.append(f"Ensure the requested tweak is visibly realized ({change_request.strip()}).")
    if hallucinated_features:
        remediation_directives.append(f"Remove unrequested additions: {', '.join(hallucinated_features)}.")
    if anti_pattern_violations:
        remediation_directives.append(f"Fix craft violations: {', '.join(anti_pattern_violations)}.")
    if unapplied_changes:
        remediation_directives.append("Apply the instruction that did not land: "
                                      + "; ".join(unapplied_changes))
    if banned_present_reasons:
        remediation_directives.append("Remove copy the product forbids: "
                                      + "; ".join(banned_present_reasons))

    retry_prompt = ""
    if unapplied_changes or banned_present_reasons:
        retry_prompt = (
            "Keep layout, spacing, colours and components identical. Text only. "
            "Apply exactly these and change nothing else: "
            + "; ".join(unapplied_changes + banned_present_reasons) + ".")

    remediation_prompt = ""
    if remediation_directives:
        remediation_prompt = (
            f"FOLLOW-UP TWEAK FOR {screen_name.upper()} (KEEP EXISTING DESIGN):\n"
            + "\n".join(f"- {d}" for d in remediation_directives)
        )

    return {
        "mode": "tweak",
        "project": name,
        "screen": screen_name,
        "theme_mode": tokens.get("theme_mode", "light"),
        "overall_score": overall_score,
        "verdict": verdict,
        "status": status,
        "dimensions": {
            "preservation": {"score": preservation_score, "max": 45, "kept": kept_base, "total": total_base},
            "change_realization": {"score": realization_score, "max": 30, "matched": len(matched_terms), "total": len(req_terms)},
            "hallucination_freedom": {"score": hallucination_score, "max": 15, "penalties": len(hallucinated_features)},
            "craft_compliance": {"score": craft_score, "max": 10, "violations": len(anti_pattern_violations)},
        },
        "dropped_controls": dropped_controls,
        "dropped_headings": dropped_headings,
        "missed_terms": missed_terms,
        "hallucinated_features": hallucinated_features,
        "anti_pattern_violations": anti_pattern_violations,
        "unapplied_changes": unapplied_changes,
        "banned_present": banned_present_reasons,
        "remediation_prompt": remediation_prompt,
        "retry_prompt": retry_prompt,
    }


def format_drift_report(report: dict[str, Any]) -> str:
    """Format a screen or tweak drift report into a clean CLI card."""
    out = []
    is_tweak = report.get("mode") == "tweak"
    title = "STITCH TWEAK PRESERVATION & DRIFT AUDIT" if is_tweak else "DESIGN DRIFT & SCREEN FIDELITY AUDIT"
    out.append(title)
    out.append("=" * 65)
    out.append(f"PROJECT: {report.get('project')}  |  SCREEN: {report.get('screen')}")
    out.append(f"OVERALL FIDELITY: {report.get('overall_score')}/100   [{report.get('verdict')}]   ({report.get('status')})")
    out.append("=" * 65)

    dims = report.get("dimensions", {})
    for k, v in dims.items():
        dim_title = k.replace("_", " ").title()
        sc = v.get("score", 0)
        mx = v.get("max", 0)
        bar = "█" * int((sc / mx) * 15) if mx else ""
        bar = bar.ljust(15, "░")
        out.append(f"  {dim_title.ljust(25)} {sc:2d}/{mx:2d} [{bar}]")

    if is_tweak:
        out.append("\n[PRESERVATION & TWEAK CHECK]")
        dropped = (report.get("dropped_controls") or []) + (report.get("dropped_headings") or [])
        if dropped:
            out.append(f"  ✗ OVERSTEPPED (dropped from baseline): {', '.join(dropped[:8])}")
        else:
            out.append("  ✓ Baseline Preserved: Existing headings and controls kept intact.")
        missed = report.get("missed_terms") or []
        if missed:
            out.append(f"  ! Terms from tweak request not matched in text: {', '.join(missed)}")
        else:
            out.append("  ✓ Requested Tweak Reflected in screen output.")
    else:
        out.append("\n[FEATURE INTEGRITY AUDIT]")
        found = report.get("found_ctas", [])
        missing = report.get("missing_ctas", [])
        if found:
            out.append(f"  ✓ Confirmed CTAs: {', '.join(found)}")
        if missing:
            out.append(f"  ✗ MISSING CTAs:   {', '.join(missing)}")
        else:
            out.append("  ✓ Zero Missing CTAs: All prompt controls accurately realized.")

    hallucinated = report.get("hallucinated_features", [])
    if hallucinated:
        out.append("\n[HALLUCINATION / DRIFT DETECTED]")
        for h in hallucinated:
            out.append(f"  ⚠ UNPROMPTED FEATURE: {h}")
    else:
        out.append("  ✓ Zero Hallucinations: No unprompted features or rogue buttons added.")

    violations = report.get("anti_pattern_violations", [])
    theme_mode = str(report.get("theme_mode") or "light").upper()
    if violations:
        out.append("\n[AESTHETIC & BRAND VIOLATIONS]")
        for viol in violations:
            out.append(f"  ✗ {viol}")
    else:
        out.append(f"  ✓ Craft & Theme Coherence: {theme_mode} design system respected (zero neon/lorem violations).")

    remediation = report.get("remediation_prompt")
    if remediation:
        out.append("\n[REMEDIATION DIRECTIVE FOR STITCH]")
        out.append(remediation)

    out.append("=" * 65)
    return "\n".join(out)
