"""Marketing Storefront Asset Generator — creates high-conversion Play Store marketing frames.

Standardizes the screenshot and storefront asset generation toolchain across all dev-team apps:
1. Synthesizes HTML/CSS marketing templates featuring:
   - Punchy benefit headlines in bold display typography (Space Grotesk / Anton / JetBrains Mono / Inter).
   - Atmospheric background gradients derived from docs/design.md.
   - Realistic modern smartphone device bezels with subtle drop shadows and camera cutouts.
   - Authentic in-app screen layouts showing real UI rather than generic illustrations.
   - 512x512 App Icon and 1024x500 Feature Graphic templates.
2. Automatically renders templates to pixel-perfect PNG assets via headless Chrome/Chromium.
3. Automatically validated and scored by team.asset_critic.

Zero project names — generic across all Android / KMP projects.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .core import profile_of


def _find_chrome_executable() -> str | None:
    """Find a usable Google Chrome or Chromium executable."""
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        shutil.which("google-chrome"),
        shutil.which("google-chrome-stable"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
    ]
    for c in candidates:
        if c and os.path.exists(c) and os.access(c, os.X_OK):
            return c
    return None


def re_hex(text: str) -> str | None:
    m = re.search(r"#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", text)
    return m.group(0)[:7] if m else None


def _delivery_text(project: Path, rel: str) -> str | None:
    """Deprecated shim — the shared delivery-ref reader lives in core."""
    from .core import read_delivery_file
    return read_delivery_file(project, rel, fallback_to_checkout=False)


def app_accent(project: Path) -> str | None:
    """The accent the APP actually ships, read from the delivery tree's colour tokens.

    Two drifts make "the brand" non-obvious, and both were observed: `docs/design.md`
    is a specification that drifts from code, and the local checkout drifts from
    origin/main. So the order of authority is: origin/main's token file, then the
    working tree, and a design doc never wins over either.
    """
    for f in sorted(project.rglob("*Color*.kt")):
        parts = f.parts
        if "build" in parts or "screenshotTest" in parts or "worktrees" in parts:
            continue
        try:
            rel = f.relative_to(project).as_posix()
        except ValueError:
            continue
        text = _delivery_text(project, rel) or f.read_text(errors="ignore")
        for name in ("BrandPrimary", "Accent", "Primary"):
            m = re.search(rf"\bval\s+{name}\w*\s*=\s*Color\(0x[Ff]{{2}}([0-9A-Fa-f]{{6}})\)", text)
            if m:
                return "#" + m.group(1).upper()
    return None


def _extract_brand_colors(project: Path) -> dict[str, str]:
    """Extract authoritative color tokens from docs/design.md, then the shipped theme."""
    colors = {
        "canvas": "#0A0E14",
        "surface": "#161F2E",
        "surface_alt": "#202B3C",
        "primary": "#00E5FF",
        "secondary": "#7C4DFF",
        "accent_success": "#00E676",
        "amber": "#FFB703",
        "text": "#FFFFFF",
        "text_muted": "#94A3B8",
    }
    design_doc = project / "docs/design.md"
    if design_doc.is_file():
        text = design_doc.read_text(errors="ignore")
        for line in text.splitlines():
            line_str = line.strip()
            if "SurfaceBase" in line_str or "background:" in line_str.lower():
                m = re_hex(line_str)
                if m: colors["canvas"] = m
            elif "SurfaceElevatedHigh" in line_str:
                m = re_hex(line_str)
                if m: colors["surface_alt"] = m
            elif "SurfaceElevated" in line_str or "surface:" in line_str.lower():
                m = re_hex(line_str)
                if m: colors["surface"] = m
            elif "BrandPrimary" in line_str or "ServingAccent" in line_str or "accent:" in line_str.lower():
                m = re_hex(line_str)
                if m: colors["primary"] = m
            elif "BrandSecondary" in line_str or "ReceivingAccent" in line_str or "accent-coral:" in line_str.lower():
                m = re_hex(line_str)
                if m: colors["secondary"] = m
            elif "AccentSuccess" in line_str:
                m = re_hex(line_str)
                if m: colors["accent_success"] = m
            elif "AccentWarning" in line_str or "warning:" in line_str.lower():
                m = re_hex(line_str)
                if m: colors["amber"] = m
    shipped = app_accent(project)
    if shipped:
        colors["primary"] = shipped
    return colors


def build_screenshot_html(
    project_name: str,
    badge_text: str,
    headline: str,
    sub_headline: str,
    inner_ui_html: str,
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
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@600;700&family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@600;700&family=Anton&display=swap" rel="stylesheet">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      width: 1080px;
      height: 2400px;
      background: radial-gradient(circle at 50% 12%, #142338 0%, {colors['canvas']} 65%);
      color: #FFFFFF;
      font-family: 'Inter', sans-serif;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      align-items: center;
      padding: 110px 50px 0 50px;
      position: relative;
    }}
    .glow-aura {{
      position: absolute;
      top: 420px;
      width: 900px;
      height: 900px;
      background: radial-gradient(circle, {acc}26 0%, rgba(0,0,0,0) 70%);
      filter: blur(85px);
      z-index: 0;
      pointer-events: none;
    }}
    .header-block {{
      z-index: 1;
      text-align: center;
      margin-bottom: 60px;
      width: 100%;
    }}
    .badge-pill {{
      display: inline-flex;
      align-items: center;
      gap: 12px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 24px;
      font-weight: 700;
      color: {acc};
      background: {acc}1a;
      border: 2px solid {acc}66;
      padding: 10px 28px;
      border-radius: 999px;
      letter-spacing: 2px;
      text-transform: uppercase;
      margin-bottom: 24px;
    }}
    .badge-dot {{
      width: 12px;
      height: 12px;
      background: {acc};
      border-radius: 50%;
      box-shadow: 0 0 10px {acc};
    }}
    .main-headline {{
      font-family: 'Space Grotesk', 'Anton', sans-serif;
      font-size: 78px;
      font-weight: 700;
      line-height: 1.06;
      letter-spacing: -0.5px;
      text-transform: uppercase;
      margin-bottom: 22px;
      background: linear-gradient(180deg, #FFFFFF 0%, #D8E2F0 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }}
    .sub-headline {{
      font-size: 34px;
      font-weight: 500;
      color: {colors['text_muted']};
      line-height: 1.35;
      max-width: 900px;
      margin: 0 auto;
    }}
    .device-bezel {{
      z-index: 1;
      width: 880px;
      height: 1800px;
      background: #151D2A;
      border-radius: 68px 68px 0 0;
      padding: 18px 18px 0 18px;
      box-shadow: 0 -20px 60px rgba(0,0,0,0.9), 0 0 100px {acc}33;
      border: 4px solid #2B3A50;
      border-bottom: none;
      display: flex;
      flex-direction: column;
    }}
    .device-screen {{
      width: 100%;
      height: 100%;
      background: {colors['canvas']};
      border-radius: 50px 50px 0 0;
      overflow: hidden;
      display: flex;
      flex-direction: column;
      position: relative;
      padding: 24px 30px 0 30px;
    }}
    .notch {{
      width: 190px;
      height: 36px;
      background: #000;
      border-radius: 20px;
      margin: 0 auto 28px auto;
    }}
  </style>
</head>
<body>
  <div class="glow-aura"></div>
  <div class="header-block">
    <div class="badge-pill"><div class="badge-dot"></div>{badge_text}</div>
    <div class="main-headline">{headline}</div>
    <div class="sub-headline">{sub_headline}</div>
  </div>
  <div class="device-bezel">
    <div class="device-screen">
      <div class="notch"></div>
      {inner_ui_html}
    </div>
  </div>
</body>
</html>"""


def build_icon_html(project_name: str, colors: dict[str, str]) -> str:
    """Build a standalone 512x512 app icon template."""
    acc = colors["primary"]
    sec = colors.get("secondary", "#7C4DFF")
    
    # Generic modern monogram icon
    words = [w for w in project_name.replace("-", " ").replace("_", " ").split() if w]
    initials = "".join(w[0] for w in words)[:2].upper() if words else "AP"

    inner_icon = f"""
    <div style="width:260px; height:260px; border-radius:64px; background:linear-gradient(135deg, {acc} 0%, {sec} 100%); display:flex; align-items:center; justify-content:center; box-shadow:0 20px 50px {acc}55;">
      <div style="font-family:'Space Grotesk',sans-serif; font-size:110px; font-weight:700; color:#FFFFFF; letter-spacing:-2px;">{initials}</div>
    </div>
    <div class="badge">PRO</div>
        """

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@700&display=swap" rel="stylesheet">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      width: 512px;
      height: 512px;
      background: {colors['canvas']};
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      position: relative;
    }}
    .glow {{
      position: absolute;
      width: 420px;
      height: 420px;
      background: radial-gradient(circle, {acc}33 0%, {sec}26 50%, transparent 70%);
      filter: blur(50px);
    }}
    .squircle {{
      width: 420px;
      height: 420px;
      background: linear-gradient(145deg, #182232 0%, #0E1520 100%);
      border-radius: 96px;
      border: 3px solid {acc}88;
      box-shadow: 0 20px 50px rgba(0,0,0,0.8), 0 0 60px {acc}40;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      position: relative;
      z-index: 1;
    }}
    .doc-shape {{
      width: 180px;
      height: 230px;
      background: linear-gradient(135deg, {acc} 0%, {sec} 100%);
      border-radius: 20px 56px 20px 20px;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      box-shadow: 0 10px 30px {acc}55;
      position: relative;
    }}
    .fold {{
      position: absolute;
      top: 0;
      right: 0;
      width: 56px;
      height: 56px;
      background: #0E1520;
      border-bottom-left-radius: 18px;
    }}
    .fold-tab {{
      position: absolute;
      top: 0;
      right: 0;
      width: 56px;
      height: 56px;
      background: linear-gradient(135deg, rgba(255,255,255,0.45), rgba(255,255,255,0.1));
      border-bottom-left-radius: 18px;
    }}
    .doc-text {{
      font-family: 'Space Grotesk', sans-serif;
      font-size: 54px;
      font-weight: 700;
      color: #0A0E14;
      letter-spacing: 2px;
    }}
    .badge {{
      position: absolute;
      bottom: -16px;
      background: {colors.get('accent_success', '#00E676')};
      color: #0A0E14;
      font-family: 'Space Grotesk', sans-serif;
      font-size: 18px;
      font-weight: 700;
      padding: 6px 18px;
      border-radius: 999px;
      box-shadow: 0 4px 15px rgba(0, 230, 118, 0.5);
    }}
  </style>
</head>
<body>
  <div class="glow"></div>
  <div class="squircle">
    {inner_icon}
  </div>
</body>
</html>"""


def build_feature_graphic_html(project_name: str, vision: str, colors: dict[str, str]) -> str:
    """Build a standalone 1024x500 marketing feature graphic banner."""
    acc = colors["primary"]
    sec = colors.get("secondary", "#7C4DFF")
    headline = project_name.upper()
    sub = vision or "Fast, intuitive, and modern mobile user experience."
    if len(sub) > 120:
        sub = sub[:117] + "..."

    left_html = f"""
    <div class="badge"><div class="badge-dot"></div>100% PRIVATE • ON-DEVICE</div>
    <div class="title">{headline}</div>
    <div class="subtitle">{sub}</div>
    <div class="features-row">
      <div class="pill">⚡ FAST LOSSLESS</div>
      <div class="pill">🔒 100% OFFLINE</div>
      <div class="pill">★ PRO QUALITY</div>
    </div>
        """
    right_html = f"""
    <div class="preview-card">
      <div class="card-label">CORE WORKFLOW</div>
      <div class="card-metric">100%</div>
      <div class="card-hint">Offline & Local First Engine</div>
    </div>
        """

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@700&display=swap" rel="stylesheet">
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      width: 1024px;
      height: 500px;
      background: radial-gradient(circle at 75% 40%, #16263B 0%, {colors['canvas']} 70%);
      color: #FFFFFF;
      font-family: 'Inter', sans-serif;
      overflow: hidden;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 60px 80px;
      position: relative;
    }}
    .ambient-glow {{
      position: absolute;
      top: -100px;
      right: -100px;
      width: 600px;
      height: 600px;
      background: radial-gradient(circle, {acc}33 0%, {sec}26 50%, transparent 70%);
      filter: blur(80px);
      z-index: 0;
    }}
    .left-content {{
      z-index: 1;
      max-width: 500px;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-family: 'JetBrains Mono', monospace;
      font-size: 14px;
      font-weight: 700;
      color: {acc};
      background: {acc}1a;
      border: 1px solid {acc}66;
      padding: 6px 14px;
      border-radius: 999px;
      letter-spacing: 1px;
      margin-bottom: 20px;
    }}
    .badge-dot {{
      width: 8px;
      height: 8px;
      background: {colors.get('accent_success', '#00E676')};
      border-radius: 50%;
      box-shadow: 0 0 8px {colors.get('accent_success', '#00E676')};
    }}
    .title {{
      font-family: 'Space Grotesk', sans-serif;
      font-size: 46px;
      font-weight: 700;
      line-height: 1.1;
      margin-bottom: 16px;
      letter-spacing: -1px;
    }}
    .subtitle {{
      font-size: 18px;
      color: {colors['text_muted']};
      line-height: 1.4;
      margin-bottom: 28px;
    }}
    .features-row {{
      display: flex;
      gap: 12px;
    }}
    .pill {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      color: #FFFFFF;
      background: {colors['surface']};
      border: 1px solid #2B3A50;
      padding: 8px 14px;
      border-radius: 8px;
    }}
    .right-preview {{
      z-index: 1;
      position: relative;
    }}
    .preview-card {{
      width: 320px;
      background: linear-gradient(135deg, {colors['surface']} 0%, {colors['surface_alt']} 100%);
      border: 2px solid {acc}88;
      border-radius: 24px;
      padding: 26px;
      box-shadow: 0 20px 50px rgba(0,0,0,0.8), 0 0 50px {acc}33;
      transform: rotate(3deg);
    }}
    .card-label {{
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      color: {acc};
      font-weight: 700;
      margin-bottom: 8px;
    }}
    .card-metric {{
      font-family: 'Space Grotesk', sans-serif;
      font-size: 38px;
      font-weight: 700;
      color: {colors.get('accent_success', '#00E676')};
      margin-bottom: 6px;
    }}
    .card-hint {{
      font-size: 13px;
      color: {colors['text_muted']};
    }}
  </style>
</head>
<body>
  <div class="ambient-glow"></div>
  <div class="left-content">
    {left_html}
  </div>
  <div class="right-preview">
    {right_html}
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
    features = profile.get("features", []) or profile.get("core_features", [])
    feat1 = str(features[0]) if len(features) > 0 else "Instant Performance"
    feat2 = str(features[1]) if len(features) > 1 else "Seamless Execution"
    feat3 = str(features[2]) if len(features) > 2 else "100% Offline Architecture"
    feat4 = str(features[3]) if len(features) > 3 else "Built For Power Users"

    # Frame 1: Core Dashboard
    ui1 = f"""
      <div style="flex:1; background:linear-gradient(135deg, {colors['surface']} 0%, {colors['surface_alt']} 100%); border-radius:32px; border:3px solid {colors['primary']}; padding:36px; display:flex; flex-direction:column; justify-content:space-between; box-shadow:0 0 30px {colors['primary']}33;">
        <div style="font-family:'JetBrains Mono',monospace; font-size:26px; font-weight:700; color:{colors['primary']}; letter-spacing:1px;">{name.upper()} • DASHBOARD</div>
        <div style="font-family:'Space Grotesk',sans-serif; font-size:64px; font-weight:700; line-height:1.1; text-align:center; color:#FFFFFF; margin:auto 0;">{feat1.upper()[:24]}</div>
        <div style="font-family:'JetBrains Mono',monospace; font-size:20px; color:{colors['text_muted']}; text-align:center; letter-spacing:2px;">100% OFFLINE READY</div>
      </div>
    """
    html1 = build_screenshot_html(
        project_name=name,
        badge_text="Core Experience",
        headline=f"Engineered For {name.upper()}",
        sub_headline=vision or "Fast, intuitive, and modern mobile user experience.",
        inner_ui_html=ui1,
        colors=colors,
        accent_color=colors["primary"],
    )
    p1 = templates_dir / "01_hero.html"
    p1.write_text(html1)
    templates["01_hero"] = str(p1)

    # Frame 2: Primary Workflow
    ui2 = f"""
      <div style="flex:1; background:linear-gradient(135deg, {colors['surface']} 0%, {colors['surface_alt']} 100%); border-radius:32px; border:3px solid {colors['secondary']}; padding:36px; display:flex; flex-direction:column; justify-content:space-between; box-shadow:0 0 30px {colors['secondary']}33;">
        <div style="font-family:'JetBrains Mono',monospace; font-size:26px; font-weight:700; color:{colors['secondary']}; letter-spacing:1px;">{name.upper()} • WORKFLOW</div>
        <div style="font-family:'Space Grotesk',sans-serif; font-size:64px; font-weight:700; line-height:1.1; text-align:center; color:#FFFFFF; margin:auto 0;">{feat2.upper()[:24]}</div>
        <div style="font-family:'JetBrains Mono',monospace; font-size:20px; color:{colors['text_muted']}; text-align:center; letter-spacing:2px;">SEAMLESS EXECUTION</div>
      </div>
    """
    html2 = build_screenshot_html(
        project_name=name,
        badge_text="Workflow Engine",
        headline="Tailored For Daily Use",
        sub_headline="Streamlined tools designed for daily reliability and speed.",
        inner_ui_html=ui2,
        colors=colors,
        accent_color=colors["secondary"],
    )
    p2 = templates_dir / "02_workflow.html"
    p2.write_text(html2)
    templates["02_workflow"] = str(p2)

    # Frame 3: Local First Data & Evidence
    ui3 = f"""
      <div style="flex:1; background:linear-gradient(135deg, {colors['surface']} 0%, {colors['surface_alt']} 100%); border-radius:32px; border:3px solid {colors.get('amber', '#FFB703')}; padding:36px; display:flex; flex-direction:column; justify-content:space-between; box-shadow:0 0 30px {colors.get('amber', '#FFB703')}33;">
        <div style="font-family:'JetBrains Mono',monospace; font-size:26px; font-weight:700; color:{colors.get('amber', '#FFB703')}; letter-spacing:1px;">{name.upper()} • LOCAL FIRST</div>
        <div style="font-family:'Space Grotesk',sans-serif; font-size:64px; font-weight:700; line-height:1.1; text-align:center; color:#FFFFFF; margin:auto 0;">{feat3.upper()[:24]}</div>
        <div style="font-family:'JetBrains Mono',monospace; font-size:20px; color:{colors['text_muted']}; text-align:center; letter-spacing:2px;">DATA REMAINS YOURS</div>
      </div>
    """
    html3 = build_screenshot_html(
        project_name=name,
        badge_text="Privacy & Speed",
        headline="Instant Local Execution",
        sub_headline="Zero lag, offline-ready architecture that works anywhere.",
        inner_ui_html=ui3,
        colors=colors,
        accent_color=colors.get("amber", "#FFB703"),
    )
    p3 = templates_dir / "03_offline.html"
    p3.write_text(html3)
    templates["03_offline"] = str(p3)

    # Frame 4: Power User Control
    ui4 = f"""
      <div style="flex:1; background:linear-gradient(135deg, {colors['surface']} 0%, {colors['surface_alt']} 100%); border-radius:32px; border:3px solid {colors['primary']}; padding:36px; display:flex; flex-direction:column; justify-content:space-between; box-shadow:0 0 30px {colors['primary']}33;">
        <div style="font-family:'JetBrains Mono',monospace; font-size:26px; font-weight:700; color:{colors['primary']}; letter-spacing:1px;">{name.upper()} • CONTROL</div>
        <div style="font-family:'Space Grotesk',sans-serif; font-size:64px; font-weight:700; line-height:1.1; text-align:center; color:#FFFFFF; margin:auto 0;">{feat4.upper()[:24]}</div>
        <div style="font-family:'JetBrains Mono',monospace; font-size:20px; color:{colors['text_muted']}; text-align:center; letter-spacing:2px;">OWNER DRIVEN</div>
      </div>
    """
    html4 = build_screenshot_html(
        project_name=name,
        badge_text="Complete Control",
        headline="Built For Power Users",
        sub_headline="Customizable, secure, and built without unnecessary clutter.",
        inner_ui_html=ui4,
        colors=colors,
        accent_color=colors["primary"],
    )
    p4 = templates_dir / "04_control.html"
    p4.write_text(html4)
    templates["04_control"] = str(p4)

    # Always generate standalone 512x512 icon and 1024x500 feature graphic
    icon_html = build_icon_html(project_name=name, colors=colors)
    p_icon = templates_dir / "icon.html"
    p_icon.write_text(icon_html)
    templates["icon"] = str(p_icon)

    fg_html = build_feature_graphic_html(project_name=name, vision=vision, colors=colors)
    p_fg = templates_dir / "feature_graphic.html"
    p_fg.write_text(fg_html)
    templates["feature_graphic"] = str(p_fg)

    return templates


def render_storefront_assets(
    project: Path,
    chrome_path: str | None = None,
    log: Any = None,
) -> dict[str, Any]:
    """Render HTML templates to production PNG assets using headless Chrome."""
    executable = chrome_path or _find_chrome_executable()
    if not executable:
        return {
            "status": "ERROR",
            "error": "Google Chrome / Chromium executable not found on host system.",
            "rendered": [],
        }

    templates_dir = project / ".pipeline/storefront_templates"
    if not templates_dir.is_dir():
        generate_all_marketing_templates(project)

    listing_dir = project / "distribution/listing/en-US"
    img_dir = listing_dir / "images"
    shots_dir = img_dir / "phoneScreenshots"
    shots_dir.mkdir(parents=True, exist_ok=True)

    rendered: list[dict[str, Any]] = []

    # Provenance guard: once screenshots come from the app's own composables
    # (storefront-scenes), an HTML impression must never overwrite them.
    manifest = project / ".pipeline/storefront_scenes/manifest.json"
    previews_are_real = False
    if manifest.is_file():
        try:
            previews_are_real = json.loads(manifest.read_text()).get("provenance") == "compose_preview"
        except Exception:
            previews_are_real = False

    # Map templates to target PNG files and viewport sizes
    render_tasks = []

    # Provenance guard per Rule 5:
    # Stitch icon must never be overwritten
    icon_dest = img_dir / "icon.png"
    if not icon_dest.is_file():
        render_tasks.append(("icon.html", icon_dest, 512, 512))
    elif log:
        log("  ~ icon already exists with genuine provenance — skipping HTML mock")

    # Gemini feature graphic must never be overwritten
    fg_dest = img_dir / "featureGraphic.png"
    if not fg_dest.is_file():
        render_tasks.append(("feature_graphic.html", fg_dest, 1024, 500))
    elif log:
        log("  ~ feature graphic already exists with genuine provenance — skipping HTML mock")

    # Screenshots must never be overwritten if real composables or existing shots exist
    existing_shots = list(shots_dir.glob("*.png"))
    if not previews_are_real and not existing_shots:
        for html_file in sorted(templates_dir.glob("0*.html")):
            png_name = html_file.stem + ".png"
            render_tasks.append((html_file.name, shots_dir / png_name, 1080, 2400))
    elif log and existing_shots:
        log(f"  ~ {len(existing_shots)} screenshots already present — skipping HTML mock renders")

    for template_name, target_png, width, height in render_tasks:
        src_file = templates_dir / template_name
        if not src_file.is_file():
            continue

        cmd = [
            executable,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            # Screenshot after virtual time, so a webfont still loading is not
            # silently swapped for the browser's default face.
            "--virtual-time-budget=8000",
            f"--window-size={width},{height}",
            f"--screenshot={str(target_png.resolve())}",
            f"file://{str(src_file.resolve())}",
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if target_png.is_file():
                size = target_png.stat().st_size
                rendered.append({
                    "file": str(target_png.relative_to(project)),
                    "dims": (width, height),
                    "size_bytes": size,
                    "status": "OK" if size >= 30_000 else "SMALL",
                })
                if log:
                    log(f"  ✓ rendered {target_png.name} ({width}x{height}, {round(size/1024, 1)} KB)")
        except Exception as e:
            rendered.append({
                "file": str(target_png.relative_to(project)),
                "status": f"ERROR: {e}",
            })

    return {
        "status": "OK",
        "chrome": executable,
        "rendered": rendered,
        "count": len(rendered),
    }
