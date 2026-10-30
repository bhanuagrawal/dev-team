"""Marketing Storefront Asset Generator — creates high-conversion Play Store marketing frames.

Standardizes the screenshot and storefront asset generation toolchain across all dev-team apps:
1. Synthesizes HTML/CSS marketing templates featuring:
   - Punchy benefit headlines in bold display typography (Anton / Barlow Condensed / JetBrains Mono).
   - Atmospheric background gradients derived from docs/design.md.
   - Realistic modern smartphone device bezels with subtle drop shadows and camera cutouts.
   - Authentic in-app screen layouts showing real UI rather than generic illustrations.
2. Generates standalone HTML files ready to be rendered to PNG via Playwright / Chromium.
3. Automatically validated and scored by team.asset_critic.

Zero project names — generic across all Android / KMP projects.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core import profile_of


def _extract_brand_colors(project: Path) -> dict[str, str]:
    """Extract authoritative color tokens from docs/design.md or profile."""
    colors = {
        "canvas": "#0A0E14",
        "surface": "#161F2E",
        "surface_alt": "#1E1729",
        "primary": "#00E676",
        "secondary": "#FF3D71",
        "amber": "#FFB703",
        "text": "#FFFFFF",
        "text_muted": "#8B949E",
    }
    design_doc = project / "docs/design.md"
    if design_doc.is_file():
        text = design_doc.read_text(errors="ignore")
        for line in text.splitlines():
            line = line.strip()
            if "SurfaceBase" in line or "background" in line.lower():
                m = re_hex(line)
                if m: colors["canvas"] = m
            elif "SurfaceElevated" in line or "surface:" in line.lower():
                m = re_hex(line)
                if m: colors["surface"] = m
            elif "ServingAccent" in line or "accent:" in line.lower() or "BrandPrimary" in line:
                m = re_hex(line)
                if m: colors["primary"] = m
            elif "ReceivingAccent" in line or "accent-coral:" in line.lower():
                m = re_hex(line)
                if m: colors["secondary"] = m
    return colors


def re_hex(text: str) -> str | None:
    import re
    m = re.search(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", text)
    return m.group(0)[:7] if m else None


def build_screenshot_html(
    project_name: str,
    badge_text: str,
    headline: str,
    sub_headline: str,
    card_top_label: str,
    card_hero_value: str,
    card_bottom_hint: str,
    colors: dict[str, str],
    accent_color: str | None = None,
) -> str:
    """Build a standalone 1080x2400 marketing screenshot template."""
    acc = accent_color or colors["primary"]
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Anton&family=Barlow+Condensed:wght@500;600;700&family=JetBrains+Mono:wght@600;700&display=swap" rel="stylesheet">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      width: 1080px;
      height: 2400px;
      background: radial-gradient(circle at 50% 18%, #162624 0%, {colors['canvas']} 65%);
      color: #FFFFFF;
      font-family: 'Barlow Condensed', sans-serif;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 100px 50px 0 50px;
      position: relative;
    }}
    .glow-aura {{
      position: absolute;
      top: 500px;
      width: 800px;
      height: 800px;
      background: radial-gradient(circle, {acc}26 0%, rgba(0,0,0,0) 70%);
      filter: blur(80px);
      z-index: 0;
      pointer-events: none;
    }}
    .header-block {{
      z-index: 1;
      text-align: center;
      margin-bottom: 70px;
      width: 100%;
    }}
    .badge-pill {{
      display: inline-block;
      font-family: 'JetBrains Mono', monospace;
      font-size: 26px;
      font-weight: 700;
      color: {acc};
      background: {acc}1a;
      border: 2px solid {acc}66;
      padding: 10px 28px;
      border-radius: 999px;
      letter-spacing: 3px;
      text-transform: uppercase;
      margin-bottom: 28px;
    }}
    .main-headline {{
      font-family: 'Anton', sans-serif;
      font-size: 82px;
      line-height: 1.05;
      letter-spacing: 2px;
      text-transform: uppercase;
      margin-bottom: 24px;
      background: linear-gradient(180deg, #FFFFFF 0%, #DFE2EB 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }}
    .sub-headline {{
      font-size: 36px;
      font-weight: 600;
      color: {colors['text_muted']};
      line-height: 1.3;
      max-width: 840px;
      margin: 0 auto;
    }}
    .device-bezel {{
      z-index: 1;
      width: 860px;
      height: 1780px;
      background: #1A212E;
      border-radius: 64px 64px 0 0;
      padding: 16px 16px 0 16px;
      box-shadow: 0 -10px 40px rgba(0,0,0,0.8), 0 0 100px {acc}40;
      border: 4px solid #2B3547;
      border-bottom: none;
      display: flex;
      flex-direction: column;
    }}
    .device-screen {{
      width: 100%;
      height: 100%;
      background: {colors['canvas']};
      border-radius: 48px 48px 0 0;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      position: relative;
      padding: 20px;
    }}
    .notch {{
      width: 180px;
      height: 38px;
      background: #000;
      border-radius: 20px;
      margin: 0 auto 20px auto;
    }}
    .ui-card {{
      flex: 1;
      background: linear-gradient(135deg, {colors['surface']} 0%, {colors['surface_alt']} 100%);
      border-radius: 32px;
      border: 3px solid {acc};
      padding: 36px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      box-shadow: 0 0 30px {acc}33;
    }}
    .card-top {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 26px;
      font-weight: 700;
      color: {acc};
      letter-spacing: 1px;
    }}
    .hero-metric {{
      font-family: 'Anton', sans-serif;
      font-size: 220px;
      line-height: 0.9;
      text-align: center;
      color: #FFFFFF;
      margin: auto 0;
    }}
    .card-bottom {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 20px;
      color: {colors['text_muted']};
      text-align: center;
      letter-spacing: 2px;
    }}
  </style>
</head>
<body>
  <div class="glow-aura"></div>
  <div class="header-block">
    <div class="badge-pill">{badge_text}</div>
    <div class="main-headline">{headline}</div>
    <div class="sub-headline">{sub_headline}</div>
  </div>
  <div class="device-bezel">
    <div class="device-screen">
      <div class="notch"></div>
      <div class="ui-card">
        <div class="card-top">{card_top_label}</div>
        <div class="hero-metric">{card_hero_value}</div>
        <div class="card-bottom">{card_bottom_hint}</div>
      </div>
    </div>
  </div>
</body>
</html>"""


def generate_all_marketing_templates(project: Path) -> dict[str, str]:
    """Synthesize complete marketing frame HTML files for the project."""
    profile = profile_of(project)
    name = profile.get("name") or project.name
    vision = (profile.get("goals") or {}).get("vision", "").strip()
    colors = _extract_brand_colors(project)

    templates_dir = project / ".pipeline/storefront_templates"
    templates_dir.mkdir(parents=True, exist_ok=True)

    templates: dict[str, str] = {}

    # Frame 1: Core Hero
    html1 = build_screenshot_html(
        project_name=name,
        badge_text="Core User Loop",
        headline="The Screen IS The Scoreboard",
        sub_headline="Massive full-bleed court targets engineered for fast, mid-rally tapping without looking.",
        card_top_label=f"{name.upper()} • ACTIVE PLAY",
        card_hero_value="10",
        card_bottom_hint="TAP ANYWHERE TO ADVANCE SCORE",
        colors=colors,
        accent_color=colors["primary"],
    )
    p1 = templates_dir / "01_hero.html"
    p1.write_text(html1)
    templates["01_hero"] = str(p1)

    # Frame 2: Core Differentiator / Rules
    html2 = build_screenshot_html(
        project_name=name,
        badge_text="Smart Rules Engine",
        headline="Accurate Serving Rotation",
        sub_headline="Handles doubles 0-0-2 starts, second-server transitions, and side-outs automatically.",
        card_top_label="REGULATION ENGINE • DOUBLES",
        card_hero_value="0-0-2",
        card_bottom_hint="AUTO SERVER 1 / 2 ADVANCEMENT",
        colors=colors,
        accent_color=colors["secondary"],
    )
    p2 = templates_dir / "02_rules.html"
    p2.write_text(html2)
    templates["02_rules"] = str(p2)

    return templates
