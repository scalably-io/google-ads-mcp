import asyncio, json, os, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from conftest import REPO

# The Google Ads SDK client is a plain Python object (get_service(name) -> a service
# object with plain methods), not an HTTP layer we can point at a fake base URL.
# e2e_server.py imports the real server module and swaps server._CLIENT for a fake
# before serving over stdio, so this launches that harness script directly rather
# than "-m scalably_google_ads_mcp".


def reply(result):
    return json.loads(next(x.text for x in result.content if x.type == "text"))


async def run():
    env = {**os.environ, "PYTHONPATH": str(REPO / "src"),
           "GOOGLE_ADS_LOGIN_CUSTOMER_ID": "1234567890", "GOOGLE_ADS_API_VERSION": "v25"}
    params = StdioServerParameters(command=sys.executable, args=["e2e_server.py"], env=env, cwd=str(REPO / "tests"))
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            tools = await s.list_tools()
            assert len(tools.tools) == 11

            access = reply(await s.call_tool("google_ads_list_accessible_customers", {}))
            assert access["status"] == "succeeded" and access["proof"]["apiVersion"] == "v25"
            assert "schema" not in access and "changed" not in access

            clients = reply(await s.call_tool("google_ads_list_customer_clients", {}))
            assert clients["result"]["count"] == 1

            partial = reply(await s.call_tool("google_ads_query", {
                "customer_id": "1234567890", "gaql": "SELECT campaign.id, metrics.cost_micros FROM campaign", "max_rows": 2,
            }))
            assert partial["status"] == "partial" and partial["result"]["rows"][0]["metrics"]["cost_units"] == 1

            complete = reply(await s.call_tool("google_ads_query", {
                "customer_id": "1234567890", "gaql": "SELECT campaign.id FROM campaign",
            }))
            assert complete["status"] == "succeeded" and complete["result"]["row_count"] == 3

            recommendations = reply(await s.call_tool("google_ads_recommendations", {
                "customer_id": "1234567890", "limit": 2,
            }))
            assert recommendations["status"] == "partial" and recommendations["operation"] == "google_ads_recommendations"

            bad = await s.call_tool("google_ads_query", {"customer_id": "1234567890", "gaql": "DELETE FROM campaign"})
            assert bad.isError
            error_text = next(x.text for x in bad.content if x.type == "text")
            assert "tool-outcome" not in error_text and "schema" not in error_text


def test_e2e():
    asyncio.run(run())
