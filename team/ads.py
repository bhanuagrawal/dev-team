"""Ads: paid acquisition as a committed plan plus a deterministic creator.

Why this exists
---------------
"Create a campaign for app x" was hand work: a throwaway script that hard-coded
one app's Play URL, one account's customer id, one budget, one keyword list. It
worked exactly once. The knowledge that made it work — who the app is, which
account pays, what a click may cost, which searches are worth buying — lives
with the app, so it belongs in the project, not in team code.

This module splits it the way the storefront pass is split:

    docs/ads-plan.json   what to buy, decided before anything is created.
                         Seeded empty of judgement (`--seed`), authored once
                         (`--apply`), validated by code (`--check`).

    devteam ads <p> --create   creates it, dry-run by default.

The plan carries the judging (audience, keywords, copy, negatives); the code
carries the mechanics (token, mutate, idempotency, limits) and refuses to guess.
No project name appears here: the app's identity is read from its own repo
(package id, listing copy), the payer from `config/ads.json`.

What code enforces, because a wrong campaign spends money:
  * Google's own field limits (headline 30, description 90, 3-15 headlines,
    2-4 descriptions) are checked before a request is sent;
  * the account's live currency must match the plan's, or nothing is created;
  * a campaign whose name already exists is never duplicated;
  * creation is PAUSED unless `--enable` is passed explicitly;
  * every create writes a run record under .pipeline/ads/ (ids + status).

Auth: the service account key in config/ads.json. Tokens are minted by signing
a JWT with `openssl` — no third-party library, so the CLI stays runnable with
the system python.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .core import load_json, resolve_project, save_json, pipeline_dir

API = "https://googleads.googleapis.com/v26"
SCOPE = "https://www.googleapis.com/auth/adwords"
CONFIG_REL = "config/ads.json"
PLAN_REL = "docs/ads-plan.json"
SEED_SCHEMA = 1

TEAM_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = TEAM_DIR / CONFIG_REL


class AdsError(RuntimeError):
    """An operator-readable failure (print `.message`, do not traceback)."""


# ─────────────────────────────── config / auth ───────────────────────────────
def load_config() -> dict[str, Any]:
    cfg = load_json(CONFIG_PATH, default=None)
    if cfg is None:
        raise AdsError(f"{CONFIG_REL} is missing — it names the service-account key "
                       "and the Ads accounts dev-team may spend from")
    cfg["key_file"] = str(Path(os.path.expanduser(cfg["key_file"])))
    if not Path(cfg["key_file"]).is_file():
        raise AdsError(f"service-account key not found: {cfg['key_file']}")
    return cfg


def accounts() -> list[dict[str, Any]]:
    return list(load_config().get("accounts") or [])


def _b64(obj: dict[str, Any]) -> str:
    raw = json.dumps(obj, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def token() -> str:
    """Mint an adwords-scoped access token from the service-account key."""
    key = json.loads(Path(load_config()["key_file"]).read_text())
    header = {"alg": "RS256", "typ": "JWT", "kid": key["private_key_id"]}
    now = int(time.time())
    claim = {"iss": key["client_email"], "scope": SCOPE, "aud": key["token_uri"],
             "iat": now, "exp": now + 3300}
    signing_input = f"{_b64(header)}.{_b64(claim)}".encode()
    fd, pem = tempfile.mkstemp(suffix=".pem")
    os.chmod(pem, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(key["private_key"])
    try:
        sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", pem],
                             input=signing_input, capture_output=True, check=True).stdout
    except FileNotFoundError:
        raise AdsError("openssl not found — it signs the service-account JWT")
    except subprocess.CalledProcessError as exc:
        raise AdsError(f"openssl failed to sign the JWT: {exc.stderr.decode()[:200]}")
    finally:
        os.unlink(pem)
    assertion = f"{signing_input.decode()}.{base64.urlsafe_b64encode(sig).rstrip(b'=').decode()}"
    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": assertion}).encode()
    req = urllib.request.Request(key["token_uri"], data=body, method="POST")
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read())["access_token"]
    except (urllib.error.URLError, KeyError) as exc:
        raise AdsError(f"could not mint an Ads token: {exc}")


def _post(path: str, body: dict[str, Any], tok: str,
          login_customer_id: str | None = None, method: str = "POST") -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    if login_customer_id:
        headers["login-customer-id"] = login_customer_id
    url = path if path.startswith("http") else f"{API}/{path}"
    data = json.dumps(body).encode() if method != "GET" else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read() or "{}")
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            payload = json.loads(exc.read().decode())
            errors = payload.get("error", {}).get("details", [{}])[0].get("errors", [])
            detail = "; ".join(e.get("message", "") for e in errors) or payload["error"]["message"]
        except Exception:  # noqa: BLE001 — never let a bad error body mask the failure
            pass
        raise AdsError(f"Ads API {exc.code}: {detail or exc.reason}")


def gaql(customer_id: str, query: str, tok: str | None = None) -> list[dict[str, Any]]:
    return _post(f"customers/{customer_id}/googleAds:search", {"query": query},
                 tok or token()).get("results", [])


# ───────────────────────────── project identity ─────────────────────────────
def app_meta(project: Path) -> dict[str, str]:
    """Read who this app is from its own repo — never from team code.

    The package id comes from the module's `applicationId` when there is one
    (flavored projects have several, so a single match wins and an ambiguous set
    is left blank for the plan to state), falling back to the Kotlin source root
    recorded in the profile. The listing copy comes from distribution/listing.
    """
    import re

    meta: dict[str, str] = {"name": project.name}
    profile = load_json(pipeline_dir(project) / "profile.json", default={}) or {}
    found: list[str] = []
    for gradle in sorted(project.glob("*/build.gradle.kts")) + [project / "build.gradle.kts"]:
        if gradle.is_file():
            found += re.findall(r'applicationId\s*=?\s*"([^"]+)"', gradle.read_text())
    package = found[0] if len(set(found)) == 1 else ""
    if not package:
        src_root = str(profile.get("src_root") or "")
        if "/kotlin/" in src_root:
            package = src_root.split("/kotlin/", 1)[1].replace("/", ".")
    if package and re.fullmatch(r"[a-zA-Z][\w]*(\.[\w]+)+", package):
        meta["package"] = package
        meta["store_url"] = ("https://play.google.com/store/apps/details?id=" + package)
    listing = project / "distribution" / "listing" / "en-US"
    for key, fname in (("title", "title.txt"), ("short", "short_description.txt")):
        f = listing / fname
        if f.is_file():
            meta[key] = f.read_text().strip()
    return meta


# ────────────────────────────────── the plan ──────────────────────────────────
def plan_path(project: Path) -> Path:
    return project / PLAN_REL


def load_plan(project: Path) -> dict[str, Any]:
    plan = load_json(plan_path(project), default=None)
    if plan is None:
        raise AdsError(f"no {PLAN_REL} — run: devteam ads {project.name} --seed")
    return plan


def seed(project: Path) -> dict[str, Any]:
    """A skeleton with the app's identity filled and every judgement blank."""
    meta = app_meta(project)
    known = accounts()
    return {
        "schema": SEED_SCHEMA,
        "app": meta,
        "account": {"customer_id": known[0]["customer_id"] if len(known) == 1 else "",
                    "login_customer_id": None},
        "campaign": {
            "name": "", "daily_budget": 0, "currency": "INR", "bidding": "manual_cpc",
            "geo": {"country": ""}, "languages": ["en"],
            "networks": {"google_search": True, "search_partners": False, "display": False},
        },
        "ad_groups": [],
        "negatives": [],
        "assets": {"sitelinks": [], "callouts": [], "snippet": None},
        "locked_by": "", "locked_at": None,
    }


def validate(plan: dict[str, Any]) -> list[str]:
    """Every rejection a human would otherwise make after spending money."""
    issues: list[str] = []
    if not (plan.get("app") or {}).get("store_url"):
        issues.append("app.store_url is empty — the ad has to point somewhere")
    acc = plan.get("account") or {}
    cid = str(acc.get("customer_id") or "").replace("-", "")
    if not (cid.isdigit() and len(cid) == 10):
        issues.append("account.customer_id must be a 10-digit customer id")
    camp = plan.get("campaign") or {}
    if not (camp.get("name") or "").strip():
        issues.append("campaign.name is empty")
    if not isinstance(camp.get("daily_budget"), (int, float)) or camp["daily_budget"] <= 0:
        issues.append("campaign.daily_budget must be > 0")
    if not (camp.get("geo") or {}).get("country"):
        issues.append("campaign.geo.country is empty (e.g. 'IN')")
    if not camp.get("languages"):
        issues.append("campaign.languages is empty (e.g. ['en'])")
    groups = plan.get("ad_groups") or []
    if not groups:
        issues.append("ad_groups is empty — the plan must name at least one intent")
    for i, g in enumerate(groups, 1):
        tag = f"ad_groups[{i}] {g.get('name') or '(unnamed)'}"
        if not (g.get("name") or "").strip():
            issues.append(f"{tag}: name is empty")
        if not isinstance(g.get("cpc_bid"), (int, float)) or g["cpc_bid"] <= 0:
            issues.append(f"{tag}: cpc_bid must be > 0")
        heads = g.get("headlines") or []
        descs = g.get("descriptions") or []
        if not 3 <= len(heads) <= 15:
            issues.append(f"{tag}: {len(heads)} headlines (Google wants 3-15)")
        for h in heads:
            if len(h) > 30:
                issues.append(f"{tag}: headline '{h}' is {len(h)} chars (max 30)")
        if not 2 <= len(descs) <= 4:
            issues.append(f"{tag}: {len(descs)} descriptions (Google wants 2-4)")
        for d in descs:
            if len(d) > 90:
                issues.append(f"{tag}: description '{d[:40]}…' is {len(d)} chars (max 90)")
        kws = g.get("keywords") or []
        if len(kws) < 3:
            issues.append(f"{tag}: {len(kws)} keywords (3 minimum for a viable ad group)")
        for kw in kws:
            text = kw.get("text") if isinstance(kw, dict) else kw
            if not (text or "").strip():
                issues.append(f"{tag}: empty keyword")
            if text and len(text) > 80:
                issues.append(f"{tag}: keyword '{text[:30]}…' over 80 chars")
            if isinstance(kw, dict) and kw.get("match") not in (None, "PHRASE", "EXACT", "BROAD"):
                issues.append(f"{tag}: match type '{kw.get('match')}' is not PHRASE/EXACT/BROAD")
    if not plan.get("negatives"):
        issues.append("negatives is empty — unbranded spend always needs a guard list")
    if not (plan.get("locked_by") or "").strip():
        issues.append("locked_by is empty — the plan must say who decided this")
    return issues


def save(project: Path, plan: dict[str, Any]) -> Path:
    plan["locked_at"] = plan.get("locked_at") or datetime.now(timezone.utc).isoformat()
    return save_json(plan_path(project), plan) or plan_path(project)


# ───────────────────────────────── creation ─────────────────────────────────
def _match(m: str | None) -> str:
    return {"phrase": "PHRASE", "exact": "EXACT"}.get((m or "PHRASE").lower(), "PHRASE")


def find_campaign(customer_id: str, name: str) -> dict[str, Any] | None:
    rows = gaql(customer_id,
                "SELECT campaign.id, campaign.name, campaign.status, "
                "campaign_budget.amount_micros FROM campaign "
                f"WHERE campaign.status != 'REMOVED' AND campaign.name = '{name}'")
    return rows[0]["campaign"] if rows else None


def live_currency(customer_id: str) -> str:
    rows = gaql(customer_id, "SELECT customer.currency_code, customer.descriptive_name "
                             "FROM customer")
    return rows[0]["customer"]["currencyCode"] if rows else ""


def build_ops(plan: dict[str, Any], enable: bool) -> tuple[list[dict[str, Any]], dict[str, str]]:
    cid = str(plan["account"]["customer_id"]).replace("-", "")
    camp = plan["campaign"]
    budget_res = f"customers/{cid}/campaignBudgets/-1"
    camp_res = f"customers/{cid}/campaigns/-2"
    ops: list[dict[str, Any]] = [
        {"campaignBudgetOperation": {"create": {
            "resourceName": budget_res, "name": f"{camp['name']} budget",
            "amountMicros": str(int(float(camp["daily_budget"]) * 1_000_000)),
            "deliveryMethod": "STANDARD", "explicitlyShared": False}}},
        {"campaignOperation": {"create": {
            "resourceName": camp_res, "name": camp["name"],
            "status": "ENABLED" if enable else "PAUSED",
            "advertisingChannelType": "SEARCH", "campaignBudget": budget_res,
            "containsEuPoliticalAdvertising": "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
            "manualCpc": {"enhancedCpcEnabled": False},
            "networkSettings": {
                "targetGoogleSearch": bool(camp["networks"]["google_search"]),
                "targetSearchNetwork": bool(camp["networks"]["search_partners"]),
                "targetContentNetwork": bool(camp["networks"].get("display", False))}}}},
    ]
    for i, group in enumerate(plan["ad_groups"], start=1):
        res = f"customers/{cid}/adGroups/-{i + 2}"
        ops.append({"adGroupOperation": {"create": {
            "resourceName": res, "name": group["name"], "status": "ENABLED",
            "campaign": camp_res, "type": "SEARCH_STANDARD",
            "cpcBidMicros": str(int(float(group["cpc_bid"]) * 1_000_000))}}})
        ops.append({"adGroupAdOperation": {"create": {
            "adGroup": res, "status": "ENABLED",
            "ad": {"responsiveSearchAd": {
                "headlines": [{"text": h} for h in group["headlines"]],
                "descriptions": [{"text": d} for d in group["descriptions"]]},
                "finalUrls": [plan["app"]["store_url"]]}}}})
        for kw in group["keywords"]:
            text = kw["text"] if isinstance(kw, dict) else kw
            ops.append({"adGroupCriterionOperation": {"create": {
                "adGroup": res, "status": "ENABLED",
                "keyword": {"text": text,
                            "matchType": _match(kw.get("match") if isinstance(kw, dict) else None)}}}})
    geo = _geo_constant(str(camp["geo"]["country"]))
    ops.append({"campaignCriterionOperation": {"create": {
        "campaign": camp_res,
        "location": {"geo_target_constant": geo}}}})
    for lang in camp["languages"]:
        ops.append({"campaignCriterionOperation": {"create": {
            "campaign": camp_res,
            "language": {"language_constant": _language_constant(lang)}}}})
    for neg in plan["negatives"]:
        ops.append({"campaignCriterionOperation": {"create": {
            "campaign": camp_res, "negative": True,
            "keyword": {"text": neg, "match_type": "PHRASE"}}}})
    n = 10
    assets = plan.get("assets") or {}
    for site in assets.get("sitelinks") or []:
        ops.append({"assetOperation": {"create": {
            "resourceName": f"customers/{cid}/assets/-{n}", "finalUrls": [plan["app"]["store_url"]],
            "sitelinkAsset": {"linkText": site["text"], "description1": site["description1"],
                              "description2": site["description2"]}}}})
        ops.append({"campaignAssetOperation": {"create": {
            "campaign": camp_res, "asset": f"customers/{cid}/assets/-{n}",
            "fieldType": "SITELINK"}}})
        n += 1
    for callout in assets.get("callouts") or []:
        ops.append({"assetOperation": {"create": {
            "resourceName": f"customers/{cid}/assets/-{n}",
            "calloutAsset": {"calloutText": callout}}}})
        ops.append({"campaignAssetOperation": {"create": {
            "campaign": camp_res, "asset": f"customers/{cid}/assets/-{n}",
            "fieldType": "CALLOUT"}}})
        n += 1
    snippet = assets.get("snippet")
    if snippet:
        ops.append({"assetOperation": {"create": {
            "resourceName": f"customers/{cid}/assets/-{n}",
            "structuredSnippetAsset": {"header": snippet["header"], "values": snippet["values"]}}}})
        ops.append({"campaignAssetOperation": {"create": {
            "campaign": camp_res, "asset": f"customers/{cid}/assets/-{n}",
            "fieldType": "STRUCTURED_SNIPPET"}}})
    return ops, {"customer_id": cid, "campaign_resource": camp_res}


def _geo_constant(country: str) -> str:
    code = country.upper()
    rows = gaql_by_manager("", f"SELECT geo_target_constant.id, geo_target_constant.name, "
                               f"geo_target_constant.resource_name FROM geo_target_constant "
                               f"WHERE geo_target_constant.country_code = '{code}' "
                               f"AND geo_target_constant.target_type = 'Country'")
    if not rows:
        raise AdsError(f"unknown country code: {country}")
    return rows[0]["geoTargetConstant"]["resourceName"]


_LANG_CACHE: dict[str, str] = {"en": "languageConstants/1000"}


def _language_constant(code: str) -> str:
    if code in _LANG_CACHE:
        return _LANG_CACHE[code]
    rows = gaql_by_manager("", f"SELECT language_constant.id, language_constant.resource_name "
                               f"FROM language_constant WHERE language_constant.code = '{code}'")
    if not rows:
        raise AdsError(f"unknown language code: {code}")
    _LANG_CACHE[code] = rows[0]["languageConstant"]["resourceName"]
    return _LANG_CACHE[code]


_ANY_CUSTOMER: dict[str, str] = {}


def gaql_by_manager(customer_id: str, query: str) -> list[dict[str, Any]]:
    """Reference tables (geo/language) need any accessible customer, not the payer."""
    cid = customer_id or _ANY_CUSTOMER.get("id")
    if not cid:
        cid = _ANY_CUSTOMER["id"] = first_accessible_customer()
    return gaql(cid, query)


def first_accessible_customer() -> str:
    cfg = load_config()
    tok = token()
    rows = _post("customers:listAccessibleCustomers", {}, tok, method="GET").get("resourceNames") or []
    if rows:
        return rows[0].split("/")[-1]
    listed = [a["customer_id"] for a in cfg.get("accounts") or []]
    if not listed:
        raise AdsError("the service account has no accessible Ads customers")
    return listed[0].replace("-", "")


def create(project: Path, plan: dict[str, Any], *, commit: bool, enable: bool) -> dict[str, Any]:
    tok = token()
    cid = str(plan["account"]["customer_id"]).replace("-", "")
    issues = validate(plan)
    if issues:
        raise AdsError("plan rejected: " + "; ".join(issues))
    expected = (plan["campaign"].get("currency") or "").upper()
    actual = live_currency(cid).upper()
    if expected and actual and expected != actual:
        raise AdsError(f"plan says {expected} but account {cid} bills in {actual}")
    existing = find_campaign(cid, plan["campaign"]["name"])
    if existing:
        return {"status": "exists", "campaign": existing, "customer_id": cid,
                "name": plan["campaign"]["name"]}
    ops, meta = build_ops(plan, enable)
    resp = _post(f"customers/{cid}/googleAds:mutate",
                 {"validateOnly": not commit, "mutateOperations": ops}, tok)
    result: dict[str, Any] = {
        "status": "created" if commit else "dry_run",
        "customer_id": cid,
        "name": plan["campaign"]["name"],
        "enabled": enable,
        "operations": len(ops),
        "responses": [list(r.items())[0][1] for r in resp.get("mutateOperationResponses") or []],
    }
    return result


# ──────────────────────────────── editing live ───────────────────────────────
def _geo_countries(camp: dict[str, Any]) -> list[str]:
    """Every country the plan asks for — `geo.country` plus optional `geo.countries`."""
    out = [str(camp["geo"]["country"]).upper()]
    for c in camp["geo"].get("countries") or []:
        c = str(c).upper()
        if c not in out:
            out.append(c)
    return out


def sync(project: Path, plan: dict[str, Any], *, commit: bool) -> dict[str, Any]:
    """Apply the locked plan to the campaign that already exists.

    WHY THIS EXISTS
    ---------------
    A plan is a decision; a campaign is a snapshot of it taken at creation time.
    Raising a bid, adding a keyword or widening the geo afterwards had no verb —
    so the only way to act on "the bid is too low" was a hand edit in the Ads
    console, which leaves no record and no review. This applies the diff:

      * the daily budget and every ad-group CPC bid, exactly (money decisions);
      * keywords the plan names but the account lacks, added;
      * geo/language targets the plan names but the account lacks, added.

    It never removes a keyword, a geo or an ad group: a plan that forgot
    something must not silently delete live targeting. Extras are reported, and
    removing them stays a human edit. Dry run unless `commit`, and every commit
    asks the API to validate first, so a rejected op spends nothing.
    """
    tok = token()
    cid = str(plan["account"]["customer_id"]).replace("-", "")
    camp_plan = plan["campaign"]
    issues = validate(plan)
    if issues:
        raise AdsError("plan rejected: " + "; ".join(issues))
    live = find_campaign(cid, camp_plan["name"])
    if not live:
        raise AdsError(f"no live campaign named '{camp_plan['name']}' — "
                       "run `--create --commit` first")
    where = f"campaign.id = {live['id']}"
    changes: list[str] = []
    extras: list[str] = []
    ops: list[dict[str, Any]] = []

    # budget — exact, because it is the ceiling on spend
    row = gaql(cid, "SELECT campaign.id, campaign.campaign_budget, "
                    "campaign_budget.amount_micros FROM campaign "
                    f"WHERE campaign.id = {live['id']}")[0]
    budget_res = row["campaign"]["campaignBudget"]
    live_budget = int(row["campaignBudget"]["amountMicros"])
    want_budget = int(float(camp_plan["daily_budget"]) * 1_000_000)
    if live_budget != want_budget:
        ops.append({"campaignBudgetOperation": {
            "update": {"resourceName": budget_res, "amountMicros": str(want_budget)},
            "updateMask": "amount_micros"}})
        changes.append(f"budget ₹{live_budget / 1e6:.0f} → ₹{want_budget / 1e6:.0f}/day")

    groups = {g["adGroup"]["name"]: g["adGroup"]
              for g in gaql(cid, "SELECT ad_group.id, ad_group.name, "
                                 "ad_group.resource_name, ad_group.cpc_bid_micros "
                                 f"FROM ad_group WHERE {where} "
                                 "AND ad_group.status != 'REMOVED'")}
    live_kw_rows = gaql(cid, "SELECT ad_group.name, ad_group_criterion.keyword.text, "
                             "ad_group_criterion.keyword.match_type "
                             "FROM ad_group_criterion "
                             f"WHERE {where} AND ad_group_criterion.type = 'KEYWORD' "
                             "AND ad_group_criterion.negative = false")
    live_kw = {(r["adGroup"]["name"],
                (r["adGroupCriterion"]["keyword"]["text"] or "").lower(),
                r["adGroupCriterion"]["keyword"].get("matchType")) for r in live_kw_rows}

    placeholder = 0
    for group in plan["ad_groups"]:
        name = group["name"]
        want_bid = int(float(group["cpc_bid"]) * 1_000_000)
        g = groups.get(name)
        if not g:
            placeholder += 1
            res = f"customers/{cid}/adGroups/-{100 + placeholder}"
            ops.append({"adGroupOperation": {"create": {
                "resourceName": res, "name": name, "status": "ENABLED",
                "campaign": live["resourceName"], "type": "SEARCH_STANDARD",
                "cpcBidMicros": str(want_bid)}}})
            ops.append({"adGroupAdOperation": {"create": {
                "adGroup": res, "status": "ENABLED",
                "ad": {"responsiveSearchAd": {
                    "headlines": [{"text": h} for h in group["headlines"]],
                    "descriptions": [{"text": d} for d in group["descriptions"]]}},
                "finalUrls": [plan["app"]["store_url"]]}}})
            changes.append(f"+ ad group '{name}' (₹{want_bid / 1e6:.0f} max CPC)")
            live_kw = {k for k in live_kw if k[0] != name}
            g = {"resourceName": res, "cpcBidMicros": "0"}
        elif int(g.get("cpcBidMicros") or 0) != want_bid:
            ops.append({"adGroupOperation": {
                "update": {"resourceName": g["resourceName"],
                           "cpcBidMicros": str(want_bid)},
                "updateMask": "cpc_bid_micros"}})
            changes.append(f"~ '{name}' CPC ₹{int(g.get('cpcBidMicros') or 0) / 1e6:.0f} "
                           f"→ ₹{want_bid / 1e6:.0f}")
        for kw in group["keywords"]:
            text = kw["text"] if isinstance(kw, dict) else kw
            match = _match(kw.get("match") if isinstance(kw, dict) else None)
            if (name, text.lower(), match) in live_kw:
                continue
            ops.append({"adGroupCriterionOperation": {"create": {
                "adGroup": g["resourceName"], "status": "ENABLED",
                "keyword": {"text": text, "matchType": match}}}})
            changes.append(f"+ keyword '{text}' [{match.lower()}] in '{name}'")

    # geo / language — additive only; removing live targeting is a human call
    crit = gaql(cid, "SELECT campaign_criterion.type, "
                     "campaign_criterion.location.geo_target_constant, "
                     "campaign_criterion.language.language_constant "
                     f"FROM campaign_criterion WHERE {where} "
                     "AND campaign_criterion.negative = false")
    live_geo = {r["campaignCriterion"].get("location", {}).get("geoTargetConstant")
                for r in crit if r["campaignCriterion"].get("type") == "LOCATION"}
    live_lang = {r["campaignCriterion"].get("language", {}).get("languageConstant")
                 for r in crit if r["campaignCriterion"].get("type") == "LANGUAGE"}
    for code in _geo_countries(camp_plan):
        geo = _geo_constant(code)
        if geo in live_geo:
            continue
        ops.append({"campaignCriterionOperation": {"create": {
            "campaign": live["resourceName"], "location": {"geo_target_constant": geo}}}})
        changes.append(f"+ geo {code}")
    for lang in camp_plan["languages"]:
        const = _language_constant(lang)
        if const in live_lang:
            continue
        ops.append({"campaignCriterionOperation": {"create": {
            "campaign": live["resourceName"],
            "language": {"language_constant": const}}}})
        changes.append(f"+ language {lang}")

    planned_names = {g["name"] for g in plan["ad_groups"]}
    for name in sorted(set(groups) - planned_names):
        extras.append(f"ad group '{name}' is live but not in the plan")
    planned_geo = {_geo_constant(c) for c in _geo_countries(camp_plan)}
    for geo in sorted(x for x in live_geo - planned_geo if x):
        extras.append(f"{geo.split('/')[-1]} is targeted but not in the plan")

    applied = False
    if commit and ops:
        _post(f"customers/{cid}/googleAds:mutate",
              {"validateOnly": True, "mutateOperations": ops}, tok)
        _post(f"customers/{cid}/googleAds:mutate",
              {"validateOnly": False, "mutateOperations": ops}, tok)
        applied = True
    return {"status": "synced" if applied else "dry_run",
            "customer_id": cid, "name": camp_plan["name"],
            "campaign_id": live["id"], "operations": len(ops),
            "changes": changes, "extras": extras}


def record(project: Path, plan: dict[str, Any], result: dict[str, Any]) -> Path:
    """The run record: what was created, when, for which app — nothing hangs on memory."""
    ads_dir = pipeline_dir(project) / "ads"
    ads_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    path = ads_dir / f"{stamp}-{result['customer_id']}.json"
    save_json(path, {"at": stamp, "app": plan.get("app"), "plan": PLAN_REL,
                     "result": result})
    return path


def set_status(project: Path, *, enable: bool) -> dict[str, Any]:
    """Start or stop the plan's campaign. The verb `--create` never had.

    A campaign created PAUSED can only be started by something that holds the
    plan — the plan already names the budget, so starting it is a state change
    on a decided thing, not a new decision. Idempotent: asking for the state a
    campaign is already in changes nothing and says so. Records the run like a
    create, because spending is the one edit that must never hang on memory.
    """
    plan = load_plan(project)
    cid = str(plan["account"]["customer_id"]).replace("-", "")
    name = plan["campaign"]["name"]
    rows = gaql(cid, "SELECT campaign.id, campaign.name, campaign.status, "
                     "campaign_budget.amount_micros FROM campaign "
                     f"WHERE campaign.name = '{name}' AND campaign.status != 'REMOVED'")
    if not rows:
        raise AdsError(f"'{name}' does not exist in {cid} — run "
                       f"`devteam ads {project.name} --create --commit` first")
    camp = rows[0]["campaign"]
    want = "ENABLED" if enable else "PAUSED"
    result: dict[str, Any] = {
        "customer_id": cid, "name": name, "campaign_id": camp["id"],
        "before": camp["status"], "after": want,
        "daily_budget": int(rows[0]["campaignBudget"]["amountMicros"]) / 1e6,
    }
    if camp["status"] == want:
        result["status"] = "unchanged"
        return result
    _post(f"customers/{cid}/campaigns:mutate",
          {"operations": [{"update": {
              "resourceName": f"customers/{cid}/campaigns/{camp['id']}",
              "status": want}, "updateMask": "status"}]}, token())
    live = gaql(cid, "SELECT campaign.status FROM campaign "
                     f"WHERE campaign.id = {camp['id']}")
    result["status"] = "enabled" if enable else "paused"
    result["live_status"] = (live[0]["campaign"]["status"] if live else "?")
    return result


def status(project: Path) -> dict[str, Any]:
    """What exists live for this plan's campaign, and whether it actually serves.

    `campaign.status = ENABLED` is not the answer to "are my ads running": an
    account can be eligible, approved and funded and still show nothing because
    every keyword sits below the first-page bid. That is exactly how one app sat
    for 30 days with 0 impressions while `--status` printed ENABLED. So the
    status carries the serving facts too: 30-day impressions/clicks/cost and the
    keyword-level primary-status reasons, which is where the cause lives.
    """
    plan = load_plan(project)
    cid = str(plan["account"]["customer_id"]).replace("-", "")
    name = plan["campaign"]["name"]
    rows = gaql(cid, "SELECT campaign.id, campaign.name, campaign.status, "
                     "campaign.primary_status, campaign_budget.amount_micros "
                     f"FROM campaign WHERE campaign.name = '{name}'")
    if not rows:
        return {"exists": False, "customer_id": cid, "name": name}
    camp = rows[0]["campaign"]
    cid_where = f"campaign.id = {camp['id']}"
    groups = gaql(cid, "SELECT ad_group.name, ad_group.status FROM ad_group "
                       f"WHERE {cid_where}")
    ads = gaql(cid, "SELECT ad_group_ad.ad.id FROM ad_group_ad "
                    f"WHERE {cid_where}")
    kws = gaql(cid, "SELECT ad_group_criterion.criterion_id, "
                    "ad_group_criterion.primary_status, "
                    "ad_group_criterion.primary_status_reasons "
                    f"FROM ad_group_criterion WHERE {cid_where} "
                    "AND ad_group_criterion.type = 'KEYWORD' "
                    "AND ad_group_criterion.negative = false")
    perf = gaql(cid, "SELECT campaign.id, metrics.impressions, metrics.clicks, "
                     "metrics.cost_micros FROM campaign WHERE "
                     f"segments.date DURING LAST_30_DAYS AND {cid_where}")
    metrics = (perf[0].get("metrics") if perf else {}) or {}
    reasons = [k["adGroupCriterion"].get("primaryStatusReasons") or [] for k in kws]
    below = sum(1 for r in reasons if "AD_GROUP_CRITERION_BELOW_FIRST_PAGE_BID" in r)
    rarely = sum(1 for r in reasons if "AD_GROUP_CRITERION_RARELY_SERVED" in r)
    eligible = sum(1 for k in kws
                   if k["adGroupCriterion"].get("primaryStatus") == "ELIGIBLE")
    return {"exists": True, "customer_id": cid, "name": name,
            "campaign_id": camp["id"], "status": camp["status"],
            "primary_status": camp.get("primaryStatus"),
            "daily_budget": int(rows[0]["campaignBudget"]["amountMicros"]) / 1e6,
            "ad_groups": len(groups), "ads": len(ads), "keywords": len(kws),
            "impressions_30d": int(metrics.get("impressions") or 0),
            "clicks_30d": int(metrics.get("clicks") or 0),
            "cost_30d": int(metrics.get("costMicros") or 0) / 1e6,
            "eligible_keywords": eligible,
            "below_first_page_bid": below,
            "rarely_served": rarely}
