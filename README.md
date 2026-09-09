# Google Ads MCP

Read-only MCP server for the Google Ads API. Eleven tools cover discovery, GAQL reporting, resource schema, recommendations, keyword planning and forecasts, and change history for any account reachable under a manager account (MCC). We run this server in production for every ads client. Read-only is enforced at three layers: no mutate or apply service imports, an SDK method allowlist, and a GAQL-starts-with-SELECT guard.

<!-- mcp-name: io.scalably/google-ads-mcp -->

## Install

Claude Code:

```bash
claude mcp add google-ads \
  -e GOOGLE_ADS_DEVELOPER_TOKEN=your-token \
  -e GOOGLE_ADS_CLIENT_ID=your-client-id \
  -e GOOGLE_ADS_CLIENT_SECRET=your-client-secret \
  -e GOOGLE_ADS_REFRESH_TOKEN=your-refresh-token \
  -e GOOGLE_ADS_LOGIN_CUSTOMER_ID=1234567890 \
  -- uvx scalably-google-ads-mcp
```

Codex:

```bash
codex mcp add google-ads \
  --env GOOGLE_ADS_DEVELOPER_TOKEN=your-token \
  --env GOOGLE_ADS_CLIENT_ID=your-client-id \
  --env GOOGLE_ADS_CLIENT_SECRET=your-client-secret \
  --env GOOGLE_ADS_REFRESH_TOKEN=your-refresh-token \
  --env GOOGLE_ADS_LOGIN_CUSTOMER_ID=1234567890 \
  -- uvx scalably-google-ads-mcp
```

Claude Desktop: download `google-ads-mcp.mcpb` from the latest GitHub release and open it.

## Setup

This is the heaviest setup in the gallery: expect up to two days waiting on developer token approval. One set of credentials, rooted at a manager account (MCC), can read any customer account linked under it, so there is no per-client configuration beyond linking an account to the MCC.

1. **Google Ads developer token.** Sign in at ads.google.com under your manager account, go to Tools and Settings, API Center, and apply for a developer token. A new token starts on the Explorer access level, which Google grants after a review that can take a few days; discovery, query and recommendation tools work there. Basic access (a separate application) is needed for higher quotas and may be needed for the keyword-planning tools.
2. **Google Cloud OAuth client.** In a Google Cloud project, enable the Google Ads API, then create an OAuth client ID of type Desktop app under APIs and Services, Credentials. Note the client ID and client secret, and add the `https://www.googleapis.com/auth/adwords` scope to the OAuth consent screen.
3. **Refresh token.** Generate a refresh token once, as an admin user of the manager account, using Google's `oauth2l` tool or the `generate_user_credentials.py` example shipped with the `google-ads` Python library. Save the refresh token; it does not expire from normal use.
4. **Login customer ID.** Use the 10-digit customer ID of the manager account itself (no hyphens) as `GOOGLE_ADS_LOGIN_CUSTOMER_ID`. Any client account accepts an invite to link under this manager account in its own Google Ads UI, after which `google_ads_list_customer_clients` sees it.

## Tools (11)

| Tool | What it does |
|---|---|
| `google_ads_list_accessible_customers` | List every customer_id the OAuth user has direct access to |
| `google_ads_list_customer_clients` | Walk the MCC hierarchy and return every child account |
| `google_ads_query` | Run a GAQL query against a specific customer account |
| `google_ads_describe_resource` | Schema (fields, metrics, segments) for a GAQL resource |
| `google_ads_list_resources` | Return the full catalog of GAQL resources |
| `google_ads_recommendations` | Read Google's optimization recommendations for a customer account |
| `google_ads_keyword_ideas` | Generate keyword ideas with search volume, competition and CPC estimates |
| `google_ads_keyword_historical_metrics` | Historical monthly search-volume and competition metrics for specific keywords |
| `google_ads_keyword_forecast_metrics` | Forecast KPIs for a proposed keyword plan |
| `google_ads_change_events` | Audit trail of who changed what, last 30 days |
| `google_ads_change_status` | Lightweight last-modified change tracker per resource |

## Configuration

| Variable | Required | Purpose |
|---|---|---|
| `GOOGLE_ADS_DEVELOPER_TOKEN` | yes | Google Ads API developer token from the Ads API Center |
| `GOOGLE_ADS_CLIENT_ID` | yes | OAuth2 client_id for a Desktop-app credential |
| `GOOGLE_ADS_CLIENT_SECRET` | yes | OAuth2 client_secret paired with the client_id |
| `GOOGLE_ADS_REFRESH_TOKEN` | yes | Long-lived OAuth2 refresh_token for the adwords scope |
| `GOOGLE_ADS_LOGIN_CUSTOMER_ID` | yes | 10-digit customer ID of the manager account (MCC), no hyphens |
| `GOOGLE_ADS_API_VERSION` | no | Google Ads API version to target, default v25 |
| `GOOGLE_ADS_USE_PROTO_PLUS` | no | Use proto-plus message types, default true |
| `GOOGLE_ADS_LOG_LEVEL` | no | Python log level, default WARNING |

## Reply shape

Every tool returns JSON with `status` (`succeeded`, `partial`, `no_op`), `summary`, `result`, `proof`, `warnings`, `recovery`. `proof.apiVersion` names the Google Ads API version used. `proof.microsConverted` is set on `google_ads_query`. A `partial` status means the result hit its configured bound (`max_rows`, `page_size` or `limit`); `recovery.nextAction` says how to continue.

## Limits

`google_ads_query` responses are capped at 64 MB per call by the Google Ads API; split large date ranges across calls. `KeywordPlanIdeaService` (keyword ideas, historical metrics, forecasts) is rate-limited to 1 query per second and may require Basic Access if you get a permission error on the Explorer tier. `google_ads_change_events` covers up to 30 days back; `google_ads_change_status` covers up to 14 days back. `metrics.cost_micros` and other `*_micros` fields are 1 unit = $0.000001; `google_ads_query` emits a matching `*_units` field alongside when `convert_micros` is true (the default).

## Verify

Each release lists the package version, the `.mcpb` sha256 and the production commit it was derived from in CHANGELOG.md. CI runs the tests and a clean install of the built wheel on every push.

## Privacy Policy

This server runs locally, on your machine, under your own credentials. It collects no personal data, contains no telemetry, stores nothing persistently, and talks only to the vendor API it wraps. No third party, including Scalably, receives your data. Contact: hello@scalably.io. Canonical copy: https://scalably.io/connector-privacy.html

## License

MIT. Copyright Scalably.
