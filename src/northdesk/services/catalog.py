import json
import re
from importlib.resources import files


class UnknownCustomer(Exception):
    pass


class Catalog:
    def __init__(self) -> None:
        self.customers = self._load("customers")
        self.invoices = self._load("invoices")
        self.articles = self._load("kb")

    @staticmethod
    def _load(name: str) -> list[dict]:
        return json.loads(files("northdesk").joinpath(f"data/{name}.json").read_text())

    def get_customer(self, customer_id: str) -> dict:
        for customer in self.customers:
            if customer["customer_id"] == customer_id:
                return dict(customer)
        raise UnknownCustomer("unknown_customer")

    def get_invoices(self, customer_id: str) -> list[dict]:
        self.get_customer(customer_id)
        return [dict(i) for i in self.invoices if i["customer_id"] == customer_id]

    def search_kb(self, query: str) -> list[dict]:
        words = set(re.findall(r"[a-z]{3,}", query.lower()))
        matches = []
        for article in self.articles:
            tokens = set(
                re.findall(r"[a-z]{3,}", (article["title"] + " " + article["text"]).lower())
            )
            score = len(words & tokens)
            if score:
                matches.append((score, article))
        return [dict(a) for _, a in sorted(matches, key=lambda m: -m[0])[:3]]
