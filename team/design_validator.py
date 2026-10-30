"""Design Drift Validator & Screen Fidelity Auditor for Stitch MCP designs.

Audits generated screens against the authoritative prompt specification:
1. Feature Fidelity: Verifies that ALL pre-locked CTAs, controls, and telemetry exist.
2. Drift & Hallucination Detection: Catches features or CTAs invented by Stitch that
   were NOT in the prompt (e.g. unwanted auth buttons, extra nav bars, ads, unasked features).
3. Anti-Generic & Brand Law: Checks that the design respects high-contrast OLED dark law
   and does not regress to stock Material 3 / Bootstrap cookie-cutter templates.
4. Generates an exact correction / remediation prompt if drift is detected.

Zero project names in code — generic across all dev-team projects.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .core import profile_of
from .design import extract_design_tokens


def _parse_prompt_requirements(prompt_text: str) -> dict[str, Any]:
    """Extract expected CTAs, telemetry values, and rules from a compiled prompt."""
    reqs: dict[str, Any] = {
        "expected_ctas": [],
        "expected_telemetry": [],
        "forbidden_terms": [
            "lorem ipsum", "dolor sit", "sign in with google", "upgrade to pro",
            "search bar", "navigation drawer", "shopping cart"
        ],
        "brand_tokens": [],
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
                # e.g. "- Undo Button (Reverts last score update...)" -> "Undo"
                item = line_clean[2:].split("(")[0].strip()
                if item:
                    reqs["expected_ctas"].append(item)
        elif "EXACT DATA TELEMETRY" in current_section:
            if line_clean.startswith("- "):
                parts = line_clean[2:].split(":")
                val = parts[-1].strip() if len(parts) > 1 else line_clean[2:].strip()
                if val:
                    reqs["expected_telemetry"].append(val)

    return reqs


def audit_design_drift(
    project: Path,
    prompt_text: str,
    generated_content: str,
    screen_name: str = "Screen",
) -> dict[str, Any]:
    """Audit generated screen code or text representation against the prompt spec.

    Args:
        project: Project root directory
        prompt_text: The exact prompt sent to Stitch
        generated_content: The HTML, DOM snapshot, or code returned by Stitch
        screen_name: Human-readable screen title
    """
    profile = profile_of(project)
    name = profile.get("name") or project.name
    tokens = extract_design_tokens(project)
    reqs = _parse_prompt_requirements(prompt_text)

    content_lower = generated_content.lower()

    # 1. Feature Fidelity Audit (Did Stitch include all required CTAs & Data?)
    missing_ctas = []
    found_ctas = []
    for cta in reqs["expected_ctas"]:
        # Keyword match (e.g. "Undo Button" -> match "undo")
        kw = re.sub(r"(?i)\b(button|zone|toggle|switch|pills?|deck|card)\b", "", cta).strip().lower()
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

    # 2. Drift & Hallucination Audit (Did Stitch invent unasked features?)
    hallucinated_features = []
    suspicious_patterns = [
        ("Google Sign-In / OAuth", [r"sign[ -]in with google", r"continue with apple", r"oauth"]),
        ("Social / Sharing Feed", [r"follow friends", r"news feed", r"community stories"]),
        ("Monetization / Ads", [r"upgrade to premium", r"subscribe now", r"sponsored", r"ad banner"]),
        ("E-Commerce / Cart", [r"shopping cart", r"checkout", r"\$\d+\.\d\d"]),
        ("Unasked Search Bar", [r"search players", r"search courts", r"type to search\.\.\."]),
    ]

    for label, patterns in suspicious_patterns:
        # If this pattern wasn't requested in prompt, but appeared in generated content:
        prompt_has_it = any(re.search(pat, prompt_text, re.IGNORECASE) for pat in patterns)
        gen_has_it = any(re.search(pat, generated_content, re.IGNORECASE) for pat in patterns)
        if gen_has_it and not prompt_has_it:
            hallucinated_features.append(f"{label} (Not requested in prompt)")

    # 3. Anti-Generic & Brand Token Audit
    anti_pattern_violations = []
    if "background: #fff" in content_lower or "background: white" in content_lower or "background-color: #ffffff" in content_lower:
        anti_pattern_violations.append("Generic white background detected (Violates dark-first law)")
    if "lorem ipsum" in content_lower:
        anti_pattern_violations.append("Lorem Ipsum dummy copy detected (Violates concrete copy law)")

    has_brand_accent = tokens["primary_accent"].lower() in content_lower
    if not has_brand_accent and "accent" not in content_lower:
        anti_pattern_violations.append(f"Missing authoritative brand accent ({tokens['primary_accent']})")

    # 4. Scoring Algorithm
    # 50 pts: CTA Fidelity
    total_ctas = len(reqs["expected_ctas"])
    cta_score = 50 if total_ctas == 0 else int((len(found_ctas) / total_ctas) * 50)

    # 25 pts: Telemetry Fidelity
    total_tel = len(reqs["expected_telemetry"])
    tel_score = 25 if total_tel == 0 else int((len(found_telemetry) / total_tel) * 25)

    # 15 pts: Zero Hallucinations (5 pts penalty per unasked feature)
    hallucination_penalty = min(15, len(hallucinated_features) * 5)
    hallucination_score = max(0, 15 - hallucination_penalty)

    # 10 pts: Brand & Anti-Generic compliance (5 pts penalty per violation)
    brand_penalty = min(10, len(anti_pattern_violations) * 5)
    brand_score = max(0, 10 - brand_penalty)

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

    # 5. Formulate Remediation Prompt for Stitch if drift exists
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
            f"CORRECTION DIRECTIVE FOR {screen_name.upper()}:\n"
            + "\n".join(f"- {d}" for d in remediation_directives)
            + f"\nEnforce dark-first base ({tokens['surface_base']}) and neon accent ({tokens['primary_accent']})."
        )

    return {
        "project": name,
        "screen": screen_name,
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


def format_drift_report(report: dict[str, Any]) -> str:
    """Format the design drift report into a clean, agent-readable CLI card."""
    out = []
    out.append("DESIGN DRIFT & SCREEN FIDELITY AUDIT")
    out.append("=" * 65)
    out.append(f"PROJECT: {report.get('project')}  |  SCREEN: {report.get('screen')}")
    out.append(f"OVERALL FIDELITY: {report.get('overall_score')}/100   [{report.get('verdict')}]   ({report.get('status')})")
    out.append("=" * 65)

    dims = report.get("dimensions", {})
    for k, v in dims.items():
        title = k.replace("_", " ").title()
        sc = v.get("score", 0)
        mx = v.get("max", 0)
        bar = "█" * int((sc / mx) * 15) if mx else ""
        bar = bar.ljust(15, "░")
        out.append(f"  {title.ljust(25)} {sc:2d}/{mx:2d} [{bar}]")

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
    if violations:
        out.append("\n[AESTHETIC & BRAND VIOLATIONS]")
        for viol in violations:
            out.append(f"  ✗ {viol}")
    else:
        out.append("  ✓ Brand Coherence: High-contrast OLED dark law strictly respected.")

    remediation = report.get("remediation_prompt")
    if remediation:
        out.append("\n[REMEDIATION DIRECTIVE FOR STITCH]")
        out.append(remediation)

    out.append("=" * 65)
    return "\n".join(out)
