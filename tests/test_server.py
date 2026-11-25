"""End-to-end: start server.py as a real MCP stdio server and call it like an agent would."""
import asyncio
import json
import os
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests import fixture

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ServerWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "recalls.db")
        fixture.build(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_session(self, work):
        async def main():
            params = StdioServerParameters(
                command=sys.executable,
                args=[os.path.join(ROOT, "server.py")],
                env={**os.environ, "RECALLS_DB": self.path},
            )
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()

                    async def call(name, **args):
                        result = await session.call_tool(name, args)
                        return json.loads(result.content[0].text)

                    return await work(session, call)

        return asyncio.run(asyncio.wait_for(main(), timeout=60))

    def test_tools_are_registered_with_the_new_parameters(self):
        async def work(session, call):
            return {t.name: t for t in (await session.list_tools()).tools}

        tools = self.run_session(work)
        self.assertEqual(
            set(tools),
            {"search_recalls", "count_recalls", "sum_units", "get_recall", "recent_recalls", "add_note", "server_status"},
        )
        for name in ("search_recalls", "count_recalls", "sum_units", "recent_recalls"):
            self.assertIn("retailer", tools[name].inputSchema["properties"], name)
        self.assertIn("year", tools["sum_units"].inputSchema["properties"])

    def test_questions_answer_over_mcp(self):
        async def work(session, call):
            return {
                "count": await call("count_recalls", year=2026),
                "walmart": await call("count_recalls", retailer="Walmart"),
                "units": await call("sum_units", retailer="Amazon", top=1),
                "bad": await call("count_recalls", retailer="x"),
                "status": await call("server_status"),
            }

        out = self.run_session(work)
        self.assertEqual(out["count"], {"count": 8})
        self.assertEqual(out["walmart"], {"count": 2})
        self.assertEqual(out["units"]["units_total"], 900 + 4000 + 3300)
        self.assertEqual(out["units"]["top_recalls"][0]["recall_number"], "26101")
        self.assertIn("error", out["bad"])
        self.assertIn("data_may_be_stale", out["status"])

    def test_note_round_trip_over_mcp(self):
        async def work(session, call):
            saved = await call("add_note", recall_number="26-101", note="Checked listings.", author="agent:test")
            return saved, await call("get_recall", recall_number="26101")

        saved, record = self.run_session(work)
        self.assertTrue(saved["saved"])
        self.assertEqual(record["notes"][-1]["body"], "Checked listings.")


if __name__ == "__main__":
    unittest.main()
