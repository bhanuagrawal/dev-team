"""Google Play Console Browser Automation Engine — generic across all dev-team projects.

Automates the 10 mandatory Google Play Console App Content questionnaires
(Data Safety, IARC Content Rating, App Access, Ads, Target Audience) and
Store Listing metadata using browser automation (Playwright / Browser MCP).

Zero project names — generic across all Android / KMP projects.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core import RunLog, profile_of, run


# Mandatory Play Console App Content declarations that have no public REST API
APP_CONTENT_DECLARATIONS = [
    {
        "id": "privacy_policy",
        "title": "Privacy Policy",
        "description": "Hosted public HTTPS privacy policy URL",
        "field": "privacy_policy_url",
    },
    {
        "id": "app_access",
        "title": "App Access",
        "default": "all_access_without_restriction",
        "description": "All functionality is available without special access / login",
    },
    {
        "id": "ads",
        "title": "Ads Declaration",
        "default": "no_ads",
        "description": "App does not contain advertisements",
    },
    {
        "id": "content_rating",
        "title": "IARC Content Rating",
        "category": "sports_or_utility",
        "rating": "everyone",
        "description": "Utility / Sports questionnaire (all violence/drugs/explicit answers: No)",
    },
    {
        "id": "target_audience",
        "title": "Target Audience and Content",
        "target_ages": ["13-15", "16-17", "18+"],
        "unintentional_appeal_to_children": False,
        "description": "Target audience 13+ to avoid COPPA / child-directed Families policies",
    },
    {
        "id": "data_safety",
        "title": "Data Safety Form",
        "collects_data": False,
        "shares_data": False,
        "description": "Offline-first zero data collection (or declared analytics if GA4 connected)",
    },
    {
        "id": "government_apps",
        "title": "Government Apps",
        "is_government": False,
        "description": "Not a government app",
    },
    {
        "id": "financial_features",
        "title": "Financial Features",
        "has_financial": False,
        "description": "Does not provide financial services",
    },
]


def store_listing_payload(project: Path) -> dict[str, Any]:
    """Gather storefront text and image paths from distribution/listing/en-US."""
    listing_dir = project / "distribution/listing/en-US"
    if not listing_dir.is_dir():
        listing_dir = project / "fastlane/metadata/android/en-US"

    payload: dict[str, Any] = {
        "title": "",
        "short_description": "",
        "full_description": "",
        "icon_path": None,
        "feature_graphic_path": None,
        "screenshot_paths": [],
    }

    if not listing_dir.is_dir():
        return payload

    title_file = listing_dir / "title.txt"
    if title_file.is_file():
        payload["title"] = title_file.read_text(errors="ignore").strip()

    short_file = listing_dir / "short_description.txt" if (listing_dir / "short_description.txt").is_file() else listing_dir / "short-description.txt"
    if short_file and short_file.is_file():
        payload["short_description"] = short_file.read_text(errors="ignore").strip()

    full_file = listing_dir / "full_description.txt" if (listing_dir / "full_description.txt").is_file() else listing_dir / "full-description.txt"
    if full_file and full_file.is_file():
        payload["full_description"] = full_file.read_text(errors="ignore").strip()

    img_dir = listing_dir / "images" if (listing_dir / "images").is_dir() else (listing_dir / "graphics" if (listing_dir / "graphics").is_dir() else listing_dir)
    icon_file = img_dir / "icon.png"
    if icon_file.is_file():
        payload["icon_path"] = str(icon_file)

    fg_file = img_dir / "featureGraphic.png" if (img_dir / "featureGraphic.png").is_file() else img_dir / "feature-graphic.png"
    if fg_file and fg_file.is_file():
        payload["feature_graphic_path"] = str(fg_file)

    shots_dir = img_dir / "phoneScreenshots" if (img_dir / "phoneScreenshots").is_dir() else img_dir / "phone-screenshots"
    if shots_dir.is_dir():
        payload["screenshot_paths"] = [str(p) for p in sorted(shots_dir.glob("*.png")) + sorted(shots_dir.glob("*.jpg"))]

    return payload


def generate_console_setup_plan(project: Path) -> dict[str, Any]:
    """Synthesize a complete Google Play Console setup plan based on profile.json and storefront pack."""
    profile = profile_of(project)
    name = profile.get("name") or project.name
    conns = profile.get("connections", {})
    privacy_url = (conns.get("play_store", {}).get("privacy_policy_url") or
                   profile.get("privacy_policy_url") or "")

    listing = store_listing_payload(project)

    # Check analytics connections
    has_analytics = bool(conns.get("ga4") or conns.get("firebase"))

    plan = {
        "project": name,
        "app_id": profile.get("app_id") or profile.get("applicationId") or "",
        "privacy_policy_url": privacy_url,
        "store_listing": {
            "title": listing["title"],
            "short_description": listing["short_description"],
            "full_description_length": len(listing["full_description"]),
            "icon_present": bool(listing["icon_path"]),
            "feature_graphic_present": bool(listing["feature_graphic_path"]),
            "screenshots_count": len(listing["screenshot_paths"]),
        },
        "declarations": [
            {
                "id": "privacy_policy",
                "value": privacy_url,
                "status": "ready" if privacy_url.startswith("https://") else "needs_url",
            },
            {
                "id": "app_access",
                "value": "All functionality is available without special access",
                "status": "ready",
            },
            {
                "id": "ads",
                "value": "No ads",
                "status": "ready",
            },
            {
                "id": "content_rating",
                "value": "Sports / Utility (Everyone / PEGI 3)",
                "status": "ready",
            },
            {
                "id": "target_audience",
                "value": "13+ (13-15, 16-17, 18+; No unintentional child appeal)",
                "status": "ready",
            },
            {
                "id": "data_safety",
                "value": "Anonymous usage data only" if has_analytics else "No data collected or shared",
                "status": "ready",
            },
            {
                "id": "government_apps",
                "value": "Not a government app",
                "status": "ready",
            },
            {
                "id": "financial_features",
                "value": "No financial features",
                "status": "ready",
            },
        ],
        "closed_testing": {
            "track": "alpha",
            "requirement": "20 testers opted in for 14 continuous days",
            "group_email": f"{project.name.lower()}-testers@googlegroups.com",
        },
    }
    return plan
