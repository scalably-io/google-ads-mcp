import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scalably_google_ads_mcp import server


class CustomerService:
    def list_accessible_customers(self):
        return SimpleNamespace(resource_names=["customers/1234567890"])


class GoogleAdsService:
    def search_stream(self, customer_id, query):
        if "FROM customer_client" in query:
            status = SimpleNamespace(name="ENABLED")
            cc = SimpleNamespace(id=1111111111, descriptive_name="Client", currency_code="USD",
                                  time_zone="America/New_York", manager=False, level=1, status=status,
                                  hidden=False, resource_name="customers/123/customerClients/111")
            return [SimpleNamespace(results=[SimpleNamespace(customer_client=cc)])]
        rows = [{"campaign": {"id": str(i), "name": f"C{i}"}, "metrics": {"cost_micros": 1000000 * (i + 1)}} for i in range(3)]
        return [SimpleNamespace(results=rows)]


class Client:
    def get_service(self, name):
        return {"CustomerService": CustomerService(), "GoogleAdsService": GoogleAdsService()}[name]


server._CLIENT = Client()
server.mcp.run()
