# Changelog

## 1.0.1

- Failures now read `google_ads_request_failed: <message> <hint>` as the reply contract says; 1.0.0 repeated the tool name instead of a code.
- README documents the `operation` and `target` reply keys.
- The manifests test treats a field as a credential when its title or description says so, not only its env var name.

## 1.0.0

- First public release. Derived from `container/tools/google-ads-mcp/server.py` at `ef174fc3` (2026-08-31) in the private ScalablyAI repository. Changes from production: the private tool-outcome envelope is replaced by a plain JSON reply, tool annotations added (title, readOnlyHint, openWorldHint) on all 11 tools, no functional change.
- Tool titles derived from each tool's first docstring sentence.
- 15 em dashes stripped from `server.py` comments and docstrings (no wording change beyond the dash itself).
- README Setup section rewritten as a self-hoster walkthrough (developer token, Google Cloud OAuth client, refresh token, login customer ID); the production readme's per-deployment language, cloud project name and private repo URL are not carried over. Flagged as the heaviest setup in the gallery: developer token approval can take up to two days.
- `manifest.json` `user_config` rewritten from scratch with six fields (developer_token, client_id, client_secret, refresh_token, login_customer_id, api_version); `developer_token`, `client_secret` and `refresh_token` are marked sensitive. The production manifest's cloud project name and private repo URL are dropped.
