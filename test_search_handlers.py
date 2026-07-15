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

    async def test_search_patents_returns_compact_deduplicated_identifiers(self):
        payload = {
            "ops:world-patent-data": {
                "ops:biblio-search": {
                    "@total-result-count": "3",
                    "ops:search-result": {
                        "ops:publication-reference": [
                            {
                                "document-id": {
                                    "@document-id-type": "docdb",
                                    "country": {"$": "US"},
                                    "doc-number": {"$": "20210344867"},
                                    "kind": {"$": "A1"},
                                }
                            },
                            {
                                "document-id": {
                                    "@document-id-type": "docdb",
                                    "country": {"$": "US"},
                                    "doc-number": {"$": "20210344867"},
                                    "kind": {"$": "A1"},
                                }
                            },
                            {
                                "document-id": {
                                    "@document-id-type": "docdb",
                                    "country": {"$": "EP"},
                                    "doc-number": {"$": "4391573"},
                                    "kind": {"$": "A1"},
                                }
                            },
                        ]
                    },
                }
            }
        }
        with patch.object(
            server, "search_published_data", AsyncMock(return_value=payload)
        ):
            text = await self.call(
                "search_patents", {"query": "cpc=H04N25/00", "limit": 2}
            )
        compact = json.loads(text)
        self.assertEqual(compact["returned"], 2)
        self.assertEqual(
            [item["publication_number"] for item in compact["results"]],
            ["US20210344867A1", "EP4391573A1"],
        )
        self.assertNotIn("ops:world-patent-data", compact)

    async def test_search_patents_canonicalizes_us_pregrant_number(self):
        payload = {
            "ops:world-patent-data": {
                "ops:biblio-search": {
                    "@total-result-count": "1",
                    "ops:search-result": {
                        "ops:publication-reference": {
                            "document-id": {
                                "@document-id-type": "docdb",
                                "country": {"$": "US"},
                                "doc-number": {"$": "2022201236"},
                                "kind": {"$": "A1"},
                            }
                        }
                    },
                }
            }
        }
        with patch.object(
            server, "search_published_data", AsyncMock(return_value=payload)
        ):
            text = await self.call(
                "search_patents", {"query": "ta=simultaneous", "limit": 25}
            )
        result = json.loads(text)["results"][0]
        self.assertEqual(result["publication_number"], "US20220201236A1")
        self.assertTrue(result["google_patents_url"].endswith("US20220201236A1/en"))

    def test_us_pregrant_input_forms_map_to_same_ops_number(self):
        compact = server.parse_publication_number("US2022201236A1")
        conventional = server.parse_publication_number("US2022/0201236 A1")
        self.assertEqual(compact["doc_number"], "2022201236")
        self.assertEqual(conventional["doc_number"], "2022201236")
        self.assertEqual(compact["doc_number_full"], "20220201236")
        self.assertEqual(conventional["doc_number_full"], "20220201236")

    def test_biblio_publication_number_is_canonicalized(self):
        parsed = {
            "publication": {
                "country": "US",
                "number": "2022201236",
                "kind": "A1",
            }
        }
        server.canonicalize_biblio_publication(parsed)
        self.assertEqual(parsed["publication"]["number"], "20220201236")

    async def test_search_patents_raw_is_explicit(self):
        payload = {"ops:world-patent-data": {"ops:biblio-search": {}}}
        with patch.object(
            server, "search_published_data", AsyncMock(return_value=payload)
        ):
            text = await self.call(
                "search_patents", {"query": "ta=sensor", "raw": True}
            )
        self.assertEqual(json.loads(text), payload)

    async def test_search_patents_allows_compact_multi_page_results(self):
        search = AsyncMock(
            return_value={
                "ops:world-patent-data": {"ops:biblio-search": {}}
            }
        )
        with patch.object(server, "search_published_data", search):
            text = await self.call(
                "search_patents", {"query": "ta=sensor", "limit": 50}
            )
        search.assert_awaited_once()
        self.assertEqual(search.await_args.args[3], 50)
        self.assertEqual(json.loads(text)["effective_limit"], 50)

    async def test_search_patents_rejects_ambiguous_cql_fields(self):
        text = await self.call(
            "search_patents", {"query": "an=Prophesee and ta=event"}
        )
        self.assertIn("Unsupported or ambiguous CQL field", text)
        self.assertIn("Use pa= for applicant", text)

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

    async def test_get_cpc_hierarchy_bounds_records_and_parent_titles(self):
        children = "".join(
            f'<classification-item classification-symbol="H04N25/{index}">'
            f'<class-title><text>Child title {index}</text></class-title>'
            "</classification-item>"
            for index in range(12)
        )
        xml = (
            '<root><classification-item classification-symbol="H04N25/00">'
            '<class-title><text>Parent image-sensor title</text></class-title>'
            f"{children}</classification-item></root>"
        )
        with patch.object(server, "fetch_cpc_hierarchy", AsyncMock(return_value=xml)):
            text = await self.call(
                "get_cpc_hierarchy", {"symbol": "H04N25/00", "depth": 2}
            )
        result = json.loads(text)
        self.assertEqual(result["returned"], server.DEFAULT_CPC_HIERARCHY_RESULTS)
        self.assertEqual(len(result["classes"]), server.DEFAULT_CPC_HIERARCHY_RESULTS)
        self.assertEqual(result["classes"][0]["title"], "Parent image-sensor title")
        self.assertNotIn("Child title", result["classes"][0]["title"])

    async def test_timeout_error_is_typed_and_retryable(self):
        with patch.object(
            server,
            "fetch_bibliographic_data",
            AsyncMock(side_effect=httpx.ReadTimeout("")),
        ):
            text = await self.call(
                "get_patent_biblio", {"publication_number": "EP4391573A1"}
            )
        error = json.loads(text)["error"]
        self.assertEqual(error["type"], "timeout")
        self.assertTrue(error["retryable"])
        self.assertIn("retry this call once", error["message"])

    async def test_search_404_returns_compact_no_results(self):
        request = httpx.Request("GET", "https://ops.epo.org/search")
        response = httpx.Response(404, request=request, text="not found")
        error = httpx.HTTPStatusError(
            "not found", request=request, response=response
        )
        with patch.object(
            server, "search_published_data", AsyncMock(side_effect=error)
        ):
            text = await self.call("search_patents", {"query": "bad=query"})
        result = json.loads(text)
        self.assertEqual(result["total_results"], 0)
        self.assertEqual(result["results"], [])

    async def test_tool_schema_limits_search_and_targeted_excerpts(self):
        tools = {tool.name: tool for tool in await server.list_tools()}
        search_properties = tools["search_patents"].inputSchema["properties"]
        self.assertEqual(search_properties["limit"]["maximum"], 100)
        self.assertEqual(search_properties["limit"]["default"], 25)
        self.assertFalse(search_properties["raw"]["default"])
        excerpt_properties = tools["find_text_in_patent"].inputSchema[
            "properties"
        ]
        self.assertEqual(excerpt_properties["max_matches"]["maximum"], 10)

    async def test_search_hits_carry_title_and_bounded_abstract(self):
        long_abstract = (
            "An optical apparatus includes a sensing element producing a first "
            "signal and a second signal read out through separate circuits. "
        ) * 5
        payload = {
            "ops:world-patent-data": {
                "ops:biblio-search": {
                    "@total-result-count": "1",
                    "ops:search-result": {
                        "exchange-documents": {
                            "exchange-document": {
                                "abstract": {
                                    "@lang": "en",
                                    "p": {"$": long_abstract},
                                },
                                "bibliographic-data": {
                                    "publication-reference": {
                                        "document-id": {
                                            "@document-id-type": "docdb",
                                            "country": {"$": "EP"},
                                            "doc-number": {"$": "4391573"},
                                            "kind": {"$": "A1"},
                                        }
                                    },
                                    "invention-title": [
                                        {"@lang": "de", "$": "Titel"},
                                        {"@lang": "en", "$": "Generic sensing device"},
                                    ],
                                },
                            }
                        }
                    },
                }
            }
        }
        with patch.object(
            server, "search_published_data", AsyncMock(return_value=payload)
        ):
            text = await self.call("search_patents", {"query": "cpc=G01J"})
        hit = json.loads(text)["results"][0]
        self.assertEqual(hit["publication_number"], "EP4391573A1")
        self.assertEqual(hit["title"], "Generic sensing device")
        self.assertLessEqual(
            len(hit["abstract"]), server.ABSTRACT_SNIPPET_CHARS + 1
        )
        self.assertTrue(hit["abstract"].endswith("…"))

    async def test_biblio_surfaces_citations_cpc_abstract_and_family(self):
        payload = {
            "ops:world-patent-data": {
                "exchange-documents": {
                    "exchange-document": {
                        "@family-id": "84982418",
                        "abstract": {
                            "@lang": "en",
                            "p": {"$": "A merged frame-based and event-based pixel."},
                        },
                        "bibliographic-data": {
                            "publication-reference": {
                                "document-id": {
                                    "country": {"$": "EP"},
                                    "doc-number": {"$": "4391573"},
                                    "kind": {"$": "A1"},
                                    "date": {"$": "20240626"},
                                }
                            },
                            "patent-classifications": {
                                "patent-classification": [
                                    {
                                        "classification-scheme": {"@scheme": "CPCI"},
                                        "section": {"$": "H"},
                                        "class": {"$": "04"},
                                        "subclass": {"$": "N"},
                                        "main-group": {"$": "25"},
                                        "subgroup": {"$": "47"},
                                    }
                                ]
                            },
                            "references-cited": {
                                "citation": [
                                    {
                                        "@cited-phase": "national-search-report",
                                        "category": [{"$": "X"}, {"$": "Y"}],
                                        "patcit": {
                                            "document-id": [
                                                {
                                                    "@document-id-type": "docdb",
                                                    "country": {"$": "US"},
                                                    "doc-number": {"$": "2022201236"},
                                                    "kind": {"$": "A1"},
                                                }
                                            ]
                                        },
                                    },
                                    {
                                        "@cited-phase": "undefined",
                                        "nplcit": {
                                            "text": {
                                                "$": "- LALANNE ET AL: A native HDR pixel"
                                            }
                                        },
                                    },
                                ]
                            },
                        },
                    }
                }
            }
        }
        with patch.object(
            server, "fetch_bibliographic_data", AsyncMock(return_value=payload)
        ):
            text = await self.call(
                "get_patent_biblio", {"publication_number": "EP4391573A1"}
            )
        self.assertIn("US20220201236A1 [X Y] (national-search-report)", text)
        self.assertIn("CPC Classifications:", text)
        self.assertIn("H04N25/47", text)
        self.assertIn("Abstract:", text)
        self.assertIn("merged frame-based and event-based pixel", text)
        self.assertIn("LALANNE ET AL: A native HDR pixel", text)
        self.assertNotIn("(undefined)", text)
        self.assertIn("INPADOC Family ID: 84982418", text)

    async def test_find_text_falls_back_when_ops_lacks_description(self):
        request = httpx.Request("GET", "https://ops.epo.org/description")
        response = httpx.Response(404, request=request, text="not found")
        error = httpx.HTTPStatusError("not found", request=request, response=response)
        with patch.object(server, "fetch_description", AsyncMock(side_effect=error)):
            text = await self.call(
                "find_text_in_patent",
                {"publication_number": "US2022201236A1", "search_text": "anode"},
            )
        self.assertIn("Description text not available", text)
        self.assertIn("patents.google.com/patent/US20220201236A1/en", text)

    def test_excerpt_is_bounded_around_match(self):
        text = "a" * 500 + " event driven sensing " + "b" * 500
        excerpt = server.excerpt_around(text, "event driven", 40)
        self.assertIn("event driven", excerpt)
        self.assertLessEqual(len(excerpt), 110)
        self.assertTrue(excerpt.startswith("…"))
        self.assertTrue(excerpt.endswith("…"))


if __name__ == "__main__":
    unittest.main()
