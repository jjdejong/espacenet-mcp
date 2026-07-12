import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import server


class SearchHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, name, arguments):
        with (
            patch.object(server, "OPS_CONSUMER_KEY", "key"),
            patch.object(server, "OPS_CONSUMER_SECRET", "secret"),
        ):
            result = await server.call_tool(name, arguments)
        self.assertEqual(len(result), 1)
        return result[0].text

    async def test_search_patents_serializes_json(self):
        payload = {"ops:world-patent-data": {"ops:biblio-search": {}}}
        with patch.object(
            server, "search_published_data", AsyncMock(return_value=payload)
        ):
            text = await self.call(
                "search_patents", {"query": "cpc=H04N25/00", "limit": 2}
            )
        self.assertEqual(json.loads(text), payload)

    async def test_search_cpc_serializes_json(self):
        xml = """<root><classification-item classification-symbol="H04N25/00">
        <text>Image sensors</text></classification-item></root>"""
        with patch.object(server, "search_cpc_classes", AsyncMock(return_value=xml)):
            text = await self.call("search_cpc", {"query": "event camera pixel"})
        self.assertEqual(json.loads(text)["classes"][0]["symbol"], "H04N25/00")

    async def test_get_cpc_hierarchy_serializes_json(self):
        xml = """<root><classification-item classification-symbol="H04N25/00">
        <text>Image sensors</text></classification-item></root>"""
        with patch.object(server, "fetch_cpc_hierarchy", AsyncMock(return_value=xml)):
            text = await self.call(
                "get_cpc_hierarchy", {"symbol": "H04N25/00", "depth": 1}
            )
        self.assertEqual(json.loads(text)["requested_symbol"], "H04N25/00")

    async def test_search_http_error_does_not_reference_publication_number(self):
        request = httpx.Request("GET", "https://ops.epo.org/search")
        response = httpx.Response(404, request=request, text="not found")
        error = httpx.HTTPStatusError(
            "not found", request=request, response=response
        )
        with patch.object(
            server, "search_published_data", AsyncMock(side_effect=error)
        ):
            text = await self.call("search_patents", {"query": "bad=query"})
        self.assertIn("search_patents request failed: HTTP 404", text)
        self.assertNotIn("pub_num", text)


if __name__ == "__main__":
    unittest.main()
