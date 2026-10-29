"""Tavily web search integration for the dev-team research agent.

Provides on-demand and agentic web research (competitor features, market gaps,
technical libraries, design system standards) using the Tavily Search API.

Principle: zero project names in code, zero hardcoded keys. Reads TAVILY_API_KEY
from the environment (agentconfig / secrets.env).

Results are stored as structured JSON in .pipeline/market/report.json so that
deterministic collectors fold them into research.json (signals.market) without
introducing network flakiness into nightly build runs.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .core import now_iso, pipeline_dir


TAVILY_API_URL = "https://api.tavily.com/search"


def is_available() -> bool:
    """Returns True if TAVILY_API_KEY is present in the environment."""
    return bool(os.environ.get("TAVILY_API_KEY", "").strip())


def tavily_search(query: str,
                  search_depth: str = "advanced",
                  max_results: int = 5,
                  include_answer: bool = True,
                  timeout: int = 30) -> dict[str, Any]:
    """Execute a single search query against Tavily API.

    Returns the parsed JSON response dict, or a dict with {'error': ...}.
    Degrades gracefully on network/auth errors without crashing callers.
    """
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        return {
            "status": "unavailable",
            "error": "TAVILY_API_KEY is not set in environment (check ~/agentconfig/secrets.env)"
        }

    payload = {
        "api_key": api_key,
        "query": query,
        "search_depth": search_depth,
        "include_answer": include_answer,
        "max_results": max_results,
    }

    try:
        req = urllib.request.Request(
            TAVILY_API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "dev-team-researcher/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            data["status"] = "ok"
            return data
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        return {"status": "error", "error": f"HTTP {e.code}: {e.reason} — {body}"}
    except urllib.error.URLError as e:
        return {"status": "error", "error": f"Network error: {e.reason}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def search_market(project: Path,
                  query_or_topic: str,
                  search_depth: str = "advanced",
                  max_results: int = 5,
                  log=None) -> dict[str, Any]:
    """Execute market/web search for a project and write .pipeline/market/report.json.

    The saved report is deterministically pulled by research_collect() into
    signals.market, so the synthesis prompt has access to verified web evidence.
    """
    if log:
        log(f"~ market research via Tavily: {query_or_topic[:100]}")

    raw = tavily_search(query_or_topic, search_depth=search_depth, max_results=max_results)
    if raw.get("status") != "ok":
        if log:
            log(f"  Tavily search error: {raw.get('error')}")
        return raw

    market_dir = pipeline_dir(project) / "market"
    market_dir.mkdir(parents=True, exist_ok=True)

    results = raw.get("results", []) or []
    cleaned_results = []
    for r in results:
        cleaned_results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", "")[:1200],
            "score": r.get("score"),
        })

    report = {
        "updated_at": now_iso(),
        "query": query_or_topic,
        "answer": raw.get("answer", ""),
        "results_count": len(cleaned_results),
        "results": cleaned_results,
        "sources": [r.get("url", "") for r in cleaned_results if r.get("url")],
    }

    # Write main report
    report_file = market_dir / "report.json"
    report_file.write_text(json.dumps(report, indent=2) + "\n")

    # Append to append-only search log for audit trail
    slug = re.sub(r"[^a-z0-9]+", "-", query_or_topic.lower()).strip("-")[:40]
    log_file = market_dir / "searches.jsonl"
    with log_file.open("a") as f:
        f.write(json.dumps({"at": now_iso(), "query": query_or_topic, "slug": slug,
                            "results": len(cleaned_results)}) + "\n")

    if log:
        log(f"  market research saved → {report_file.name} ({len(cleaned_results)} sources)")

    return report
