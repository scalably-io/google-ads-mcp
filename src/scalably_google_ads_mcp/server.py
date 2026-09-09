# Google Ads MCP (read-only, multi-account via MCC). 11 tools covering the Google Ads API read surface.
#
# References:
#   - https://developers.google.com/google-ads/api/docs/query/overview
#   - https://developers.google.com/google-ads/api/docs/account-management/get-account-hierarchy
#   - https://developers.google.com/google-ads/api/docs/best-practices/quotas
#   - https://developers.google.com/google-ads/api/docs/release-notes

from __future__ import annotations

import json
import logging
import os
import re
import time
import functools
import inspect
from typing import Any, Iterable

import proto
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

# Read-only SDK imports. Do NOT import any *MutateService, *ApplyService, or
# CustomerService.create_customer_client. Adding a *Mutate import must come
# with a scoped review; the read-only guarantee depends on it.
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException

# --- Constants ---

DEFAULT_API_VERSION = os.environ.get("GOOGLE_ADS_API_VERSION", "v25")
MICROS_PER_UNIT = 1_000_000

CUSTOMER_ID_PATTERN = re.compile(r"^(\d{10})$|^(\d{3}-\d{3}-\d{4})$")
RESOURCE_NAME_PATTERN = re.compile(r"^[a-z][a-zA-Z0-9_]*$")
GAQL_ALLOWED_START = re.compile(r"^\s*(?:/\*.*?\*/\s*)*SELECT\s", re.IGNORECASE | re.DOTALL)
SELECT_WHITELIST_METHODS = frozenset(
    {
        "search_stream",
        "search",
        "list_accessible_customers",
        "search_google_ads_fields",
        "get_google_ads_field",
        "generate_keyword_ideas",
        "generate_keyword_historical_metrics",
        "generate_keyword_forecast_metrics",
    }
)

REDACTION_PATTERNS = (
    re.compile(r"(?i)(\"developer_token\"\s*:\s*\")([^\"]+)(\")"),
    re.compile(r"(?i)(\"client_secret\"\s*:\s*\")([^\"]+)(\")"),
    re.compile(r"(?i)(\"refresh_token\"\s*:\s*\")([^\"]+)(\")"),
    re.compile(r"(?i)(Bearer\s+)(\S+)"),
)

mcp = FastMCP("google-ads")
LOGGER = logging.getLogger(__name__)


def _reply(status: str, operation: str, summary: str, *, result=None, target=None, proof=None, warnings=None, recovery=None) -> str:
    return json.dumps({"status": status, "operation": operation, "summary": summary, "target": target, "result": result, "proof": proof, "warnings": warnings or [], "recovery": recovery}, indent=2)


def _fail(operation: str, message: str, retryable: bool = False) -> None:
    """Plain error: <code>: <message> <hint>. The operation name is already in the tool error the client shows."""
    hint = "Retry once after a delay." if retryable else "Correct credentials, permissions, identifiers, or parameters before retrying."
    raise RuntimeError(f"google_ads_request_failed: {message} {hint}")


_raw_tool=mcp.tool
def _canonical_tool(*tool_args,**tool_kwargs):
    register=_raw_tool(*tool_args,**tool_kwargs)
    def decorator(fn):
        sig=inspect.signature(fn)
        @functools.wraps(fn)
        def wrapped(*args,**kwargs):
            try: result=fn(*args,**kwargs)
            except Exception as exc:
                text=_redact(str(exc));retryable=any(token in text.upper() for token in ("RESOURCE_EXHAUSTED","UNAVAILABLE","DEADLINE_EXCEEDED","INTERNAL","429","500","503"));_fail(fn.__name__,text,retryable)
            bound=sig.bind_partial(*args,**kwargs);bound.apply_defaults();values=bound.arguments
            count=result.get("row_count",result.get("count")) if isinstance(result,dict) else None
            cap=values.get("max_rows") or values.get("page_size") or values.get("limit")
            partial=isinstance(count,int) and isinstance(cap,int) and count>=cap
            no_op=isinstance(count,int) and count==0
            customer=values.get("customer_id") or values.get("mcc_customer_id")
            proof={"complete":not partial,"apiVersion":DEFAULT_API_VERSION,"resultCount":count,"boundedBy":cap,"microsConverted":values.get("convert_micros",True) if fn.__name__=="google_ads_query" else None}
            return _reply("partial" if partial else "no_op" if no_op else "succeeded",fn.__name__,f"{fn.__name__} returned {count if count is not None else 'a'} result(s){' at the configured bound' if partial else ''}.",result=result,target={"type":"google_ads_customer","customerId":str(customer) if customer is not None else None},proof=proof,warnings=["The result reached its configured bound and may not be complete."] if partial else [],recovery={"nextAction":"Narrow or split the GAQL/date range, or continue with an explicit GAQL offset where supported."} if partial else None)
        wrapped.__signature__=sig.replace(return_annotation=str)
        register(wrapped)
        return fn
    return decorator
mcp.tool=_canonical_tool

_CLIENT: GoogleAdsClient | None = None


# --- Security / redaction ---


def _redact(text: str) -> str:
    def _replace(m: re.Match[str]) -> str:
        if (m.lastindex or 0) >= 3:
            return f"{m.group(1)}***REDACTED***{m.group(3)}"
        if (m.lastindex or 0) >= 2:
            return f"{m.group(1)}***REDACTED***"
        return "***REDACTED***"

    redacted = text
    for pattern in REDACTION_PATTERNS:
        redacted = pattern.sub(_replace, redacted)
    return redacted


# --- Client ---


def _load_client() -> GoogleAdsClient:
    """Load a Google Ads client from env.

    Required env:
      GOOGLE_ADS_DEVELOPER_TOKEN
      GOOGLE_ADS_CLIENT_ID
      GOOGLE_ADS_CLIENT_SECRET
      GOOGLE_ADS_REFRESH_TOKEN
      GOOGLE_ADS_LOGIN_CUSTOMER_ID  (the MCC; digits only, no hyphens)

    Optional:
      GOOGLE_ADS_API_VERSION        (default v22)
      GOOGLE_ADS_USE_PROTO_PLUS     (default true)
    """
    required = [
        "GOOGLE_ADS_DEVELOPER_TOKEN",
        "GOOGLE_ADS_CLIENT_ID",
        "GOOGLE_ADS_CLIENT_SECRET",
        "GOOGLE_ADS_REFRESH_TOKEN",
        "GOOGLE_ADS_LOGIN_CUSTOMER_ID",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            "Google Ads MCP missing required env vars: "
            + ", ".join(missing)
            + ". Configure OAuth2 refresh-token auth + developer_token + "
            "login_customer_id (MCC) in .env."
        )
    use_proto_plus = os.environ.get("GOOGLE_ADS_USE_PROTO_PLUS", "true").lower() == "true"
    # load_from_env also accepts YAML config; env-only path is enough for us.
    client = GoogleAdsClient.load_from_env(version=DEFAULT_API_VERSION)
    client.use_proto_plus = use_proto_plus
    return client


def _client() -> GoogleAdsClient:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = _load_client()
    return _CLIENT


# --- Helpers ---


def _normalize_customer_id(value: str | int) -> str:
    s = str(value).strip()
    m = CUSTOMER_ID_PATTERN.match(s)
    if not m:
        raise ValueError(
            f"Invalid customer_id {s!r}. Expected 10-digit number (e.g. "
            f"'1234567890') or hyphenated form ('123-456-7890')."
        )
    return s.replace("-", "")


def _assert_read_only_gaql(gaql: str) -> None:
    """Reject anything that isn't a plain GAQL SELECT.

    GAQL itself is read-only by language design; mutations are a different
    API surface (Mutate*Service), not reachable from the SDK methods we expose.
    This guard is defense-in-depth: reject queries that don't begin with SELECT
    (after optional /* … */ comments + whitespace) and reject semicolons
    (GAQL doesn't use them and any appearance is suspicious).
    """
    if not isinstance(gaql, str) or not gaql.strip():
        raise ValueError("gaql must be a non-empty string.")
    if ";" in gaql:
        raise ValueError(
            "GAQL does not use semicolons. Remove the ';' and resubmit."
        )
    if not GAQL_ALLOWED_START.match(gaql):
        raise ValueError(
            "GAQL queries must start with SELECT. Mutations are not supported "
            "by this MCP; use read-only reporting only."
        )


def _proto_to_dict(obj: Any) -> Any:
    """Serialize a proto-plus message, protobuf wrapper, or iterable to plain dict/list."""
    if isinstance(obj, proto.Message):
        return type(obj).to_dict(obj, preserving_proto_field_name=True)
    # Raw protobuf (use_proto_plus=False path): has _pb / DESCRIPTOR.
    if hasattr(obj, "DESCRIPTOR") and hasattr(obj, "ListFields"):
        from google.protobuf.json_format import MessageToDict

        return MessageToDict(obj, preserving_proto_field_name=True)
    if isinstance(obj, dict):
        return {k: _proto_to_dict(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)) or (
        hasattr(obj, "__iter__") and not isinstance(obj, (str, bytes))
    ):
        try:
            return [_proto_to_dict(x) for x in obj]
        except TypeError:
            return obj
    return obj


def _micros_to_currency(row: dict[str, Any]) -> dict[str, Any]:
    """Convert *_micros metrics in-place to _units (float) alongside the micro values."""

    def _walk(d: Any) -> Any:
        if isinstance(d, dict):
            out = {}
            for k, v in d.items():
                out[k] = _walk(v)
                if isinstance(v, (int, str)) and k.endswith("_micros"):
                    try:
                        micros = int(v)
                        out[k[: -len("_micros")] + "_units"] = micros / MICROS_PER_UNIT
                    except (TypeError, ValueError):
                        pass
            return out
        if isinstance(d, list):
            return [_walk(x) for x in d]
        return d

    return _walk(row)


def _format_google_ads_exception(exc: GoogleAdsException) -> dict[str, Any]:
    errs = []
    for err in exc.failure.errors:
        errs.append(
            {
                "message": err.message,
                "error_code": _proto_to_dict(err.error_code),
                "location": _proto_to_dict(err.location) if err.location else None,
                "trigger": str(err.trigger.string_value)
                if getattr(err, "trigger", None)
                else None,
            }
        )
    return {"request_id": exc.request_id, "errors": errs}


# --- Tool 1: list_accessible_customers ---


@mcp.tool(annotations=ToolAnnotations(title="List accessible customers", readOnlyHint=True, openWorldHint=True))
def google_ads_list_accessible_customers() -> dict[str, Any]:
    """List every customer_id the OAuth user has direct access to.

    This is a small, cheap list, typically just the MCC itself and any
    standalone accounts. For the full tree of accounts under the MCC (agency
    use case), call google_ads_list_customer_clients instead.

    Returns: {"resource_names": [...], "customer_ids": [...]}.
    Each customer_id is 10 digits, no hyphens.
    """
    client = _client()
    svc = client.get_service("CustomerService")
    resp = svc.list_accessible_customers()
    names = list(resp.resource_names)
    ids = [n.split("/")[-1] for n in names if "/" in n]
    return {"resource_names": names, "customer_ids": ids}


# --- Tool 2: list_customer_clients (MCC hierarchy walk) ---


@mcp.tool(annotations=ToolAnnotations(title="List MCC customer clients", readOnlyHint=True, openWorldHint=True))
def google_ads_list_customer_clients(
    mcc_customer_id: str | None = None,
    include_managers: bool = True,
    include_hidden: bool = False,
    max_level: int = 3,
) -> dict[str, Any]:
    """Walk the MCC (Manager Account) hierarchy and return every child account.

    For agency use: this is the canonical "which clients can I query?" call.
    Uses GAQL on the `customer_client` resource. Fast: one streaming call.

    Args:
      mcc_customer_id: MCC customer_id. Defaults to GOOGLE_ADS_LOGIN_CUSTOMER_ID
        (the configured MCC). Accepts 10-digit or hyphenated.
      include_managers: include sub-MCCs in the result (default True).
      include_hidden: include customer_clients marked hidden=True (default False).
      max_level: max hierarchy depth (default 3, hard cap 10).

    Returns per account: customer_id, descriptive_name, currency_code, time_zone,
    manager (bool), level, status (ENABLED|CANCELED|…), hidden, resource_name.
    """
    mcc = _normalize_customer_id(
        mcc_customer_id or os.environ["GOOGLE_ADS_LOGIN_CUSTOMER_ID"]
    )
    level_cap = min(max(int(max_level), 0), 10)
    where_clauses = [f"customer_client.level <= {level_cap}"]
    if not include_hidden:
        where_clauses.append("customer_client.hidden = FALSE")
    if not include_managers:
        where_clauses.append("customer_client.manager = FALSE")
    gaql = (
        "SELECT customer_client.client_customer, customer_client.id, "
        "customer_client.descriptive_name, customer_client.currency_code, "
        "customer_client.time_zone, customer_client.manager, "
        "customer_client.level, customer_client.status, "
        "customer_client.hidden, customer_client.resource_name "
        "FROM customer_client WHERE "
        + " AND ".join(where_clauses)
    )
    _assert_read_only_gaql(gaql)
    client = _client()
    svc = client.get_service("GoogleAdsService")
    rows = []
    try:
        stream = svc.search_stream(customer_id=mcc, query=gaql)
        for batch in stream:
            for row in batch.results:
                cc = row.customer_client
                rows.append(
                    {
                        "customer_id": str(cc.id),
                        "descriptive_name": cc.descriptive_name,
                        "currency_code": cc.currency_code,
                        "time_zone": cc.time_zone,
                        "manager": bool(cc.manager),
                        "level": int(cc.level),
                        "status": cc.status.name
                        if hasattr(cc.status, "name")
                        else str(cc.status),
                        "hidden": bool(cc.hidden),
                        "resource_name": cc.resource_name,
                    }
                )
    except GoogleAdsException as exc:
        raise RuntimeError(
            f"Google Ads error listing customer_clients: "
            f"{json.dumps(_format_google_ads_exception(exc), indent=2)}"
        ) from exc
    return {"mcc": mcc, "clients": rows, "count": len(rows)}


# --- Tool 3: query (universal GAQL workhorse) ---


@mcp.tool(annotations=ToolAnnotations(title="Run a GAQL query", readOnlyHint=True, openWorldHint=True))
def google_ads_query(
    customer_id: str,
    gaql: str,
    max_rows: int | None = None,
    convert_micros: bool = True,
) -> dict[str, Any]:
    """Run a GAQL (Google Ads Query Language) query against a specific customer account.

    GAQL is SQL-like: `SELECT ... FROM <single_resource> WHERE ... ORDER BY ... LIMIT ...`.
    No JOINs; attribute fields from related resources by selecting them directly.
    Common resources: campaign, ad_group, keyword_view, search_term_view,
    shopping_performance_view, customer, customer_client, conversion_action,
    change_event, change_status, recommendation, asset, asset_group.

    Use google_ads_describe_resource first if unsure of field names on a new
    resource; saves a FIELD_NOT_FOUND roundtrip.

    Args:
      customer_id: 10-digit customer_id (no hyphens) for the account to query.
      gaql: the full GAQL query string (must start with SELECT; no semicolons).
      max_rows: if set, stop streaming after this many rows. Defaults to
        unbounded (the 64 MB response cap still applies).
      convert_micros: if True (default), also emit <metric>_units alongside
        any <metric>_micros field (divided by 1,000,000). E.g. cost_micros
        = 12345678 → cost_units = 12.345678.

    Example queries:

      SELECT campaign.id, campaign.name, metrics.cost_micros
      FROM campaign WHERE segments.date DURING LAST_7_DAYS
      ORDER BY metrics.cost_micros DESC LIMIT 50

      SELECT ad_group.name, ad_group_criterion.keyword.text,
             metrics.impressions, metrics.clicks
      FROM keyword_view WHERE segments.date DURING LAST_30_DAYS

      SELECT segments.product_item_id, metrics.conversions_value
      FROM shopping_performance_view WHERE segments.date DURING LAST_30_DAYS

    Gotchas (surface to user where relevant):
      - metrics.cost_micros: 1 unit = $0.000001. Divide by 1M for currency.
      - LAST_30_DAYS = 30 days ending YESTERDAY (today excluded).
      - Last 72h data is partial; conversions retroactively re-attribute.
      - Zero-metric rows are excluded when segmenting by date.
      - Response cap is 64 MB per call; split by date range for huge reports.
      - GAQL `IN (...)` clause is capped at 20,000 items.
    """
    cid = _normalize_customer_id(customer_id)
    _assert_read_only_gaql(gaql)
    client = _client()
    svc = client.get_service("GoogleAdsService")
    rows: list[dict[str, Any]] = []
    total = 0
    try:
        stream = svc.search_stream(customer_id=cid, query=gaql)
        for batch in stream:
            for row in batch.results:
                row_dict = _proto_to_dict(row)
                if convert_micros:
                    row_dict = _micros_to_currency(row_dict)
                rows.append(row_dict)
                total += 1
                if max_rows is not None and total >= max_rows:
                    break
            if max_rows is not None and total >= max_rows:
                break
    except GoogleAdsException as exc:
        raise RuntimeError(
            f"Google Ads error: "
            f"{json.dumps(_format_google_ads_exception(exc), indent=2)}"
        ) from exc
    return {"customer_id": cid, "row_count": total, "rows": rows}


# --- Tool 4: describe_resource ---


@mcp.tool(annotations=ToolAnnotations(title="Describe GAQL resource schema", readOnlyHint=True, openWorldHint=True))
def google_ads_describe_resource(resource_name: str) -> dict[str, Any]:
    """Schema (fields, metrics, segments) for a GAQL resource.

    Queries GoogleAdsFieldService for every selectable field on a resource
    plus its data type, selectability, filterability, sort-ability, and
    `selectable_with` (which other fields can coexist in a single SELECT).

    Args:
      resource_name: the GAQL resource, e.g. "campaign", "ad_group",
        "shopping_performance_view", "change_event", "recommendation".

    Returns: {resource, fields: [{name, category: ATTRIBUTE|METRIC|SEGMENT,
    data_type, selectable, filterable, sortable, enum_values, type_url,
    selectable_with}]}.

    Essential before composing GAQL on an unfamiliar resource; prevents
    FIELD_NOT_COMPATIBLE and FIELD_NOT_FOUND errors.
    """
    if not RESOURCE_NAME_PATTERN.match(resource_name):
        raise ValueError(
            f"Invalid resource_name {resource_name!r}. Expected lowercase "
            "snake_case (e.g. 'campaign', 'shopping_performance_view')."
        )
    client = _client()
    svc = client.get_service("GoogleAdsFieldService")
    # Google Ads Field Service is itself queried via GAQL.
    gaql = (
        "SELECT name, category, data_type, selectable, filterable, "
        "sortable, enum_values, type_url, is_repeated, selectable_with "
        f"FROM google_ads_field WHERE category != 'UNKNOWN' "
        f"AND (name LIKE '{resource_name}.%' OR name = '{resource_name}')"
    )
    req = client.get_type("SearchGoogleAdsFieldsRequest")
    req.query = gaql
    fields = []
    for f in svc.search_google_ads_fields(request=req):
        fields.append(
            {
                "name": f.name,
                "category": f.category.name
                if hasattr(f.category, "name")
                else str(f.category),
                "data_type": f.data_type.name
                if hasattr(f.data_type, "name")
                else str(f.data_type),
                "selectable": bool(f.selectable),
                "filterable": bool(f.filterable),
                "sortable": bool(f.sortable),
                "is_repeated": bool(f.is_repeated),
                "enum_values": list(f.enum_values) if f.enum_values else [],
                "type_url": f.type_url if f.type_url else None,
                "selectable_with": list(f.selectable_with)
                if f.selectable_with
                else [],
            }
        )
    return {"resource": resource_name, "fields": fields, "count": len(fields)}


# --- Tool 5: list_resources (GAQL resource catalog) ---


@mcp.tool(annotations=ToolAnnotations(title="List GAQL resource catalog", readOnlyHint=True, openWorldHint=True))
def google_ads_list_resources() -> dict[str, Any]:
    """Return the full catalog of GAQL resources (FROM-able tables).

    Useful when the agent needs to pick the right resource for a question.
    Each entry: name, category, selectable (whether it can be the FROM target).

    Response is cacheable for the session; the catalog is stable across queries.
    """
    client = _client()
    svc = client.get_service("GoogleAdsFieldService")
    gaql = (
        "SELECT name, category, selectable "
        "FROM google_ads_field WHERE category = 'RESOURCE'"
    )
    req = client.get_type("SearchGoogleAdsFieldsRequest")
    req.query = gaql
    resources = []
    for f in svc.search_google_ads_fields(request=req):
        resources.append(
            {
                "name": f.name,
                "category": f.category.name
                if hasattr(f.category, "name")
                else str(f.category),
                "selectable": bool(f.selectable),
            }
        )
    resources.sort(key=lambda r: r["name"])
    return {"resources": resources, "count": len(resources)}


# --- Tool 6: recommendations ---


@mcp.tool(annotations=ToolAnnotations(title="Read optimization recommendations", readOnlyHint=True, openWorldHint=True))
def google_ads_recommendations(
    customer_id: str,
    types: list[str] | None = None,
    dismissed: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    """Read Google's optimization recommendations for a customer account.

    Returns suggestions only; does NOT apply any. Types include:
    KEYWORD, CAMPAIGN_BUDGET, KEYWORD_MATCH_TYPE, TARGET_CPA_OPT_IN,
    MAXIMIZE_CLICKS_OPT_IN, OPTIMIZE_AD_ROTATION, RESPONSIVE_SEARCH_AD,
    ENHANCED_CPC_OPT_IN, SEARCH_PARTNERS_OPT_IN, SITELINK_EXTENSION,
    CALL_EXTENSION, CALLOUT_EXTENSION, STRUCTURED_SNIPPET_EXTENSION,
    DISPLAY_EXPANSION_OPT_IN, KEYWORD_MATCH_TYPE, FORECASTING_*,
    SHOPPING_*, MOVE_UNUSED_BUDGET. (Google adds new types over time.)

    Args:
      customer_id: 10-digit account ID.
      types: optional list to filter by recommendation_type. Case-sensitive.
      dismissed: include dismissed recommendations (default False).
      limit: max rows (default 200).

    Each row contains: type, impact (absolute_metrics, base_metrics,
    potential_metrics), campaign / ad_group ref (if applicable), dismissed flag,
    recommendation-specific payload (e.g. suggested_keywords, budget_increase).
    """
    cid = _normalize_customer_id(customer_id)
    where = []
    if types:
        quoted = ", ".join(f"'{t}'" for t in types)
        where.append(f"recommendation.type IN ({quoted})")
    if not dismissed:
        where.append("recommendation.dismissed = FALSE")
    where_clause = (" WHERE " + " AND ".join(where)) if where else ""
    gaql = (
        "SELECT recommendation.type, recommendation.campaign, "
        "recommendation.ad_group, recommendation.dismissed, "
        "recommendation.impact, recommendation.campaign_budget, "
        "recommendation.resource_name"
        f" FROM recommendation{where_clause} LIMIT {int(limit)}"
    )
    return google_ads_query(customer_id=cid, gaql=gaql, convert_micros=True)


# --- Tools 7-9: keyword planning ---


@mcp.tool(annotations=ToolAnnotations(title="Generate keyword ideas", readOnlyHint=True, openWorldHint=True))
def google_ads_keyword_ideas(
    customer_id: str,
    keywords: list[str] | None = None,
    page_url: str | None = None,
    language_id: str = "1000",
    geo_target_ids: list[str] | None = None,
    keyword_network: str = "GOOGLE_SEARCH",
    include_adult: bool = False,
    page_size: int = 100,
) -> dict[str, Any]:
    """Generate keyword ideas with search volume + competition + CPC estimates.

    Args:
      customer_id: customer_id to attribute the request (any Ads account under MCC works).
      keywords: seed keywords, list of strings. Optional if page_url is given.
      page_url: seed URL (crawled for keyword extraction). Optional if keywords given.
      language_id: Google language criterion ID. Default '1000' (English).
        Common: '1001' Spanish, '1002' French, '1003' German, '1007' Italian.
      geo_target_ids: list of geo criterion IDs. Default ['2840'] (US).
        Common: '2826' UK, '2124' Canada, '2036' Australia, '2276' Germany.
      keyword_network: GOOGLE_SEARCH (default) | GOOGLE_SEARCH_AND_PARTNERS.
      include_adult: include adult-category keywords (default False).
      page_size: max ideas to return (default 100, capped at 10000).

    Returns: {ideas: [{text, avg_monthly_searches, competition,
    low_top_of_page_bid_micros, high_top_of_page_bid_micros, concept_group}]}.

    Rate limit: 1 QPS for this service. Back off on RESOURCE_EXHAUSTED.
    Requires Basic Access (Explorer may 403); if you get
    USER_PERMISSION_DENIED, the dev_token tier is too low.
    """
    if not keywords and not page_url:
        raise ValueError("Provide either keywords or page_url (or both).")
    cid = _normalize_customer_id(customer_id)
    client = _client()
    svc = client.get_service("KeywordPlanIdeaService")
    geos = geo_target_ids or ["2840"]

    req = client.get_type("GenerateKeywordIdeasRequest")
    req.customer_id = cid
    req.language = f"languageConstants/{language_id}"
    for geo in geos:
        req.geo_target_constants.append(f"geoTargetConstants/{geo}")
    network_map = {
        "GOOGLE_SEARCH": client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH,
        "GOOGLE_SEARCH_AND_PARTNERS": client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH_AND_PARTNERS,
    }
    if keyword_network not in network_map:
        raise ValueError(
            f"keyword_network must be one of {sorted(network_map)}; got {keyword_network!r}"
        )
    req.keyword_plan_network = network_map[keyword_network]
    req.include_adult_keywords = bool(include_adult)
    req.page_size = min(max(int(page_size), 1), 10000)

    if keywords and page_url:
        req.keyword_and_url_seed.url = page_url
        req.keyword_and_url_seed.keywords.extend(keywords)
    elif keywords:
        req.keyword_seed.keywords.extend(keywords)
    elif page_url:
        req.url_seed.url = page_url

    try:
        resp = svc.generate_keyword_ideas(request=req)
    except GoogleAdsException as exc:
        raise RuntimeError(
            f"Google Ads keyword_ideas error: "
            f"{json.dumps(_format_google_ads_exception(exc), indent=2)}"
        ) from exc

    ideas = []
    for idea in resp.results:
        metrics = idea.keyword_idea_metrics
        ideas.append(
            {
                "text": idea.text,
                "avg_monthly_searches": int(metrics.avg_monthly_searches)
                if metrics.avg_monthly_searches
                else 0,
                "competition": metrics.competition.name
                if hasattr(metrics.competition, "name")
                else str(metrics.competition),
                "competition_index": int(metrics.competition_index)
                if metrics.competition_index
                else None,
                "low_top_of_page_bid_micros": int(metrics.low_top_of_page_bid_micros)
                if metrics.low_top_of_page_bid_micros
                else None,
                "high_top_of_page_bid_micros": int(metrics.high_top_of_page_bid_micros)
                if metrics.high_top_of_page_bid_micros
                else None,
                "concept_group_name": idea.keyword_annotations.concepts[0].name
                if idea.keyword_annotations.concepts
                else None,
            }
        )
    return {"customer_id": cid, "ideas": ideas, "count": len(ideas)}


@mcp.tool(annotations=ToolAnnotations(title="Historical keyword search metrics", readOnlyHint=True, openWorldHint=True))
def google_ads_keyword_historical_metrics(
    customer_id: str,
    keywords: list[str],
    language_id: str = "1000",
    geo_target_ids: list[str] | None = None,
    keyword_network: str = "GOOGLE_SEARCH",
    include_adult: bool = False,
    year_month_start: dict[str, int] | None = None,
    year_month_end: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Historical monthly search-volume + competition metrics for specific keywords.

    Args:
      customer_id: 10-digit customer_id.
      keywords: list of keyword strings (max 10,000 per call).
      language_id / geo_target_ids / keyword_network: same shape as keyword_ideas.
      year_month_start / year_month_end: optional date range as
        {year: 2025, month: 1..12}. Defaults to last 12 months.

    Returns: {results: [{text, approximate_monthly_searches,
    monthly_search_volumes: [{year, month, monthly_searches}], competition,
    high/low_top_of_page_bid_micros}]}.

    Rate limit: 1 QPS. May require Basic Access.
    """
    if not keywords:
        raise ValueError("keywords list is required.")
    cid = _normalize_customer_id(customer_id)
    client = _client()
    svc = client.get_service("KeywordPlanIdeaService")
    req = client.get_type("GenerateKeywordHistorical" "MetricsRequest")
    req.customer_id = cid
    req.keywords.extend(keywords[:10000])
    req.language = f"languageConstants/{language_id}"
    for geo in geo_target_ids or ["2840"]:
        req.geo_target_constants.append(f"geoTargetConstants/{geo}")
    network_map = {
        "GOOGLE_SEARCH": client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH,
        "GOOGLE_SEARCH_AND_PARTNERS": client.enums.KeywordPlanNetworkEnum.GOOGLE_SEARCH_AND_PARTNERS,
    }
    req.keyword_plan_network = network_map[keyword_network]
    req.include_adult_keywords = bool(include_adult)
    if year_month_start and year_month_end:
        month_range = req.historical_metrics_options.year_month_range
        month_range.start.year = int(year_month_start["year"])
        month_range.start.month = int(year_month_start["month"])
        month_range.end.year = int(year_month_end["year"])
        month_range.end.month = int(year_month_end["month"])
    try:
        resp = svc.generate_keyword_historical_metrics(request=req)
    except GoogleAdsException as exc:
        raise RuntimeError(
            f"Google Ads keyword_historical_metrics error: "
            f"{json.dumps(_format_google_ads_exception(exc), indent=2)}"
        ) from exc
    results = []
    for r in resp.results:
        m = r.keyword_metrics
        monthly = []
        for mv in m.monthly_search_volumes:
            monthly.append(
                {
                    "year": int(mv.year),
                    "month": int(mv.month),
                    "monthly_searches": int(mv.monthly_searches)
                    if mv.monthly_searches
                    else 0,
                }
            )
        results.append(
            {
                "text": r.text,
                "close_variants": list(r.close_variants),
                "avg_monthly_searches": int(m.avg_monthly_searches)
                if m.avg_monthly_searches
                else 0,
                "competition": m.competition.name
                if hasattr(m.competition, "name")
                else str(m.competition),
                "competition_index": int(m.competition_index)
                if m.competition_index
                else None,
                "low_top_of_page_bid_micros": int(m.low_top_of_page_bid_micros)
                if m.low_top_of_page_bid_micros
                else None,
                "high_top_of_page_bid_micros": int(m.high_top_of_page_bid_micros)
                if m.high_top_of_page_bid_micros
                else None,
                "monthly_search_volumes": monthly,
            }
        )
    return {"customer_id": cid, "results": results, "count": len(results)}


@mcp.tool(annotations=ToolAnnotations(title="Forecast keyword plan KPIs", readOnlyHint=True, openWorldHint=True))
def google_ads_keyword_forecast_metrics(
    customer_id: str,
    campaign_spec: dict[str, Any],
) -> dict[str, Any]:
    """Forecast KPIs (impressions, clicks, cost, conversions) for a proposed keyword plan.

    Args:
      customer_id: 10-digit customer_id.
      campaign_spec: a KeywordPlanCampaign forecast spec. Minimum shape:
        {
          "bidding_strategy": "MANUAL_CPC",
          "daily_budget_micros": 10000000,
          "keyword_match_type": "BROAD" | "PHRASE" | "EXACT",
          "language_id": "1000",
          "geo_target_ids": ["2840"],
          "keywords": ["running shoes", "trail running shoes"]
        }

    Returns: {campaign_forecast: {impressions, clicks, cost_micros, conversions,
    average_cpc_micros}, weekly_time_series: [...]}.

    Rate limit: 1 QPS. Requires Basic Access.
    """
    cid = _normalize_customer_id(customer_id)
    client = _client()
    svc = client.get_service("KeywordPlanIdeaService")
    req = client.get_type("GenerateKeywordForecast" "MetricsRequest")
    req.customer_id = cid
    kp_campaign = req.campaign
    kp_campaign.language_constants.append(
        f"languageConstants/{campaign_spec.get('language_id', '1000')}"
    )
    for geo in campaign_spec.get("geo_target_ids", ["2840"]):
        kp_campaign.geo_modifiers.append(
            client.get_type("CriterionBidModifier")(
                geo_target_constant=f"geoTargetConstants/{geo}"
            )
        )
    kp_campaign.bidding_strategy.manual_cpc_bidding_strategy.max_cpc_bid_micros = int(
        campaign_spec.get("max_cpc_bid_micros", 1_000_000)
    )
    kp_campaign.daily_budget_micros = int(
        campaign_spec.get("daily_budget_micros", 10_000_000)
    )
    match_type_enum = client.enums.KeywordMatchTypeEnum
    match_type = campaign_spec.get("keyword_match_type", "BROAD").upper()
    mt = getattr(match_type_enum, match_type, match_type_enum.BROAD)
    ad_group = kp_campaign.ad_groups.add()
    for kw in campaign_spec.get("keywords", []):
        kw_obj = ad_group.keywords.add()
        kw_obj.text = kw
        kw_obj.match_type = mt
    try:
        resp = svc.generate_keyword_forecast_metrics(request=req)
    except GoogleAdsException as exc:
        raise RuntimeError(
            f"Google Ads keyword_forecast_metrics error: "
            f"{json.dumps(_format_google_ads_exception(exc), indent=2)}"
        ) from exc
    return _micros_to_currency(_proto_to_dict(resp))


# --- Tool 10: change_events (last 30 days audit) ---


@mcp.tool(annotations=ToolAnnotations(title="Audit trail of changes", readOnlyHint=True, openWorldHint=True))
def google_ads_change_events(
    customer_id: str,
    days_back: int = 7,
    resource_types: list[str] | None = None,
    limit: int = 10000,
) -> dict[str, Any]:
    """Audit trail: who changed what in the last 30 days.

    Max window is 30 days back. Returns: change_date_time, user_email,
    change_resource_type (CAMPAIGN|AD_GROUP|AD_GROUP_CRITERION|CAMPAIGN_BUDGET|
    AD|AD_GROUP_BID_MODIFIER|…), old_resource, new_resource, changed_fields,
    client_type (GOOGLE_ADS_WEB_CLIENT|GOOGLE_ADS_API|…).

    Args:
      customer_id: 10-digit customer_id.
      days_back: 1–30.
      resource_types: optional filter to specific change_resource_type enum values.
      limit: max rows (default 10000, capped 100000).
    """
    cid = _normalize_customer_id(customer_id)
    days_back = min(max(int(days_back), 1), 30)
    where = [f"change_event.change_date_time >= '{_iso_days_ago(days_back)}'"]
    if resource_types:
        quoted = ", ".join(f"'{t}'" for t in resource_types)
        where.append(f"change_event.change_resource_type IN ({quoted})")
    gaql = (
        "SELECT change_event.change_date_time, change_event.user_email, "
        "change_event.change_resource_type, change_event.change_resource_name, "
        "change_event.client_type, change_event.changed_fields, "
        "change_event.old_resource, change_event.new_resource, "
        "change_event.resource_change_operation "
        f"FROM change_event WHERE {' AND '.join(where)} "
        f"ORDER BY change_event.change_date_time DESC LIMIT {min(int(limit), 100000)}"
    )
    return google_ads_query(customer_id=cid, gaql=gaql, convert_micros=False)


# --- Tool 11: change_status (last 14 days change markers) ---


@mcp.tool(annotations=ToolAnnotations(title="Lightweight change status tracker", readOnlyHint=True, openWorldHint=True))
def google_ads_change_status(
    customer_id: str,
    days_back: int = 14,
    resource_types: list[str] | None = None,
    limit: int = 10000,
) -> dict[str, Any]:
    """Lightweight change tracker: last-modified-at timestamps for resources.

    Unlike change_event, this is a per-resource "last modified" view rather
    than a change-diff log. Covers up to 14 days.

    Args:
      customer_id: 10-digit customer_id.
      days_back: 1–14.
      resource_types: optional filter by change_status.resource_type enum.
      limit: max rows.
    """
    cid = _normalize_customer_id(customer_id)
    days_back = min(max(int(days_back), 1), 14)
    where = [f"change_status.last_change_date_time >= '{_iso_days_ago(days_back)}'"]
    if resource_types:
        quoted = ", ".join(f"'{t}'" for t in resource_types)
        where.append(f"change_status.resource_type IN ({quoted})")
    gaql = (
        "SELECT change_status.last_change_date_time, change_status.resource_type, "
        "change_status.resource_status, change_status.campaign, "
        "change_status.ad_group, change_status.resource_name "
        f"FROM change_status WHERE {' AND '.join(where)} "
        f"ORDER BY change_status.last_change_date_time DESC LIMIT {min(int(limit), 100000)}"
    )
    return google_ads_query(customer_id=cid, gaql=gaql, convert_micros=False)


# --- Helpers ---


def _iso_days_ago(days: int) -> str:
    """ISO-8601 datetime string, UTC, N days ago. Used inside GAQL WHERE."""
    from datetime import datetime, timedelta, timezone

    dt = datetime.now(timezone.utc) - timedelta(days=days)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# --- Entry ---


def _configure_logging() -> None:
    level = os.environ.get("GOOGLE_ADS_LOG_LEVEL", "WARNING").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.WARNING),
        format="%(asctime)s %(levelname)s google-ads-mcp %(message)s",
    )
    # google-ads SDK logs entire gRPC bodies at DEBUG in older versions.
    logging.getLogger("google.ads").setLevel(logging.WARNING)


def main() -> None:
    _configure_logging()
    try:
        _client()
    except Exception as exc:
        LOGGER.error("Google Ads client load failed at startup: %s", _redact(str(exc)))
    mcp.run()


if __name__ == "__main__":
    main()
