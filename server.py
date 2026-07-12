#!/usr/bin/env python3
"""
Espacenet MCP Server

This MCP server provides access to patent specifications from Espacenet using the EPO Open Patent Services (OPS) API.
It retrieves bibliographic data, descriptions, claims, and drawings for published patent applications.

The server handles publication numbers in various formats commonly cited in office actions:
- EP1234567A1
- US2020123456A1
- WO2020/123456
- US 2018/0189236 (with leading zero)
- etc.
"""

import asyncio
import json
import os
import re
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree as ET

import httpx
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# Import XML parsing utilities
from xml_parser import (
    parse_claims_xml,
    parse_description_xml,
    parse_biblio_json,
    format_claims_for_display,
    format_description_for_display,
    format_biblio_for_display
)

# Load environment variables
load_dotenv()

# EPO OPS API configuration
OPS_CONSUMER_KEY = os.getenv("OPS_CONSUMER_KEY")
OPS_CONSUMER_SECRET = os.getenv("OPS_CONSUMER_SECRET")
OPS_BASE_URL = "https://ops.epo.org/3.2/rest-services"
OPS_AUTH_URL = "https://ops.epo.org/3.2/auth/accesstoken"

# Global access token cache
access_token: str | None = None
token_expiry: float = 0


def parse_publication_number(pub_num: str) -> dict[str, str]:
    """
    Parse publication number into components (country code, number, kind code).
    
    Handles various formats:
    - EP1234567A1
    - US2020123456A1  
    - WO2020/123456A1
    - EP 1234567 A1
    - US 2018/0189236 (with leading zero - will be removed for OPS, kept for Google Patents)
    """
    # Remove spaces, hyphens, slashes and normalize
    pub_num_normalized = pub_num.replace(" ", "").replace("/", "").replace("-", "").upper()
    
    # Pattern: CC + number + optional kind code
    # Country code: 2 letters, Number: digits, Kind: 1-2 alphanumeric
    pattern = r'^([A-Z]{2})(\d+)([A-Z]\d?)?$'
    match = re.match(pattern, pub_num_normalized)
    
    if not match:
        raise ValueError(f"Invalid publication number format: {pub_num}")
    
    country_code, number, kind_code = match.groups()
    
    return {
        "country": country_code,
        "doc_number": str(int(number)),  # Remove leading zeros for EPO OPS
        "doc_number_full": number,  # Keep leading zeros for Google Patents
        "kind": kind_code or "",
        "format": "epodoc"  # EPO document format
    }


async def get_access_token(client: httpx.AsyncClient) -> str:
    """Get or refresh EPO OPS access token."""
    global access_token, token_expiry
    
    current_time = asyncio.get_event_loop().time()
    
    # Return cached token if still valid (with 60s buffer)
    if access_token and current_time < (token_expiry - 60):
        return access_token
    
    # Request new token
    auth = (OPS_CONSUMER_KEY, OPS_CONSUMER_SECRET)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    data = {"grant_type": "client_credentials"}
    
    response = await client.post(OPS_AUTH_URL, auth=auth, headers=headers, data=data)
    response.raise_for_status()
    
    token_data = response.json()
    access_token = token_data["access_token"]
    # Token typically expires in 20 minutes (1200 seconds)
    expires_in = int(token_data.get("expires_in", 1200))
    token_expiry = current_time + expires_in
    
    return access_token


async def fetch_bibliographic_data(
    client: httpx.AsyncClient, pub_info: dict[str, str]
) -> dict[str, Any]:
    """Fetch bibliographic data for a patent publication."""
    token = await get_access_token(client)
    
    url = (
        f"{OPS_BASE_URL}/published-data/publication/"
        f"{pub_info['format']}/{pub_info['country']}.{pub_info['doc_number']}.{pub_info['kind']}/biblio"
    )
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    
    response = await client.get(url, headers=headers)
    response.raise_for_status()
    
    return response.json()


async def fetch_description(
    client: httpx.AsyncClient, pub_info: dict[str, str]
) -> str:
    """Fetch description (specification) text for a patent publication."""
    token = await get_access_token(client)
    
    url = (
        f"{OPS_BASE_URL}/published-data/publication/"
        f"{pub_info['format']}/{pub_info['country']}.{pub_info['doc_number']}.{pub_info['kind']}/description"
    )
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/xml"  # Description is in XML format
    }
    
    response = await client.get(url, headers=headers)
    response.raise_for_status()
    
    # Return raw XML - could parse this but raw is useful for patent attorneys
    return response.text


async def fetch_claims(
    client: httpx.AsyncClient, pub_info: dict[str, str]
) -> str:
    """Fetch claims text for a patent publication."""
    token = await get_access_token(client)
    
    url = (
        f"{OPS_BASE_URL}/published-data/publication/"
        f"{pub_info['format']}/{pub_info['country']}.{pub_info['doc_number']}.{pub_info['kind']}/claims"
    )
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/xml"
    }
    
    response = await client.get(url, headers=headers)
    response.raise_for_status()
    
    return response.text


async def fetch_images(
    client: httpx.AsyncClient, pub_info: dict[str, str]
) -> list[dict[str, Any]]:
    """Fetch drawing/image information for a patent publication."""
    token = await get_access_token(client)
    
    url = (
        f"{OPS_BASE_URL}/published-data/publication/"
        f"{pub_info['format']}/{pub_info['country']}.{pub_info['doc_number']}.{pub_info['kind']}/images"
    )
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json"
    }
    
    try:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return []  # No images available
        raise


async def search_published_data(
    client: httpx.AsyncClient, query: str, start: int = 1, limit: int = 25
) -> dict[str, Any]:
    """Search OPS published data with an Espacenet CQL query."""
    token = await get_access_token(client)
    end = start + limit - 1
    response = await client.get(
        f"{OPS_BASE_URL}/published-data/search",
        params={"q": query},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Range": f"{start}-{end}",
        },
    )
    response.raise_for_status()
    return response.json()


async def search_cpc_classes(client: httpx.AsyncClient, query: str) -> str:
    """Find likely CPC symbols from keywords using the OPS CPC search service."""
    token = await get_access_token(client)
    response = await client.get(
        f"{OPS_BASE_URL}/classification/cpc/search/",
        params={"q": query},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/cpc+xml",
        },
    )
    response.raise_for_status()
    return response.text


async def fetch_cpc_hierarchy(
    client: httpx.AsyncClient,
    symbol: str,
    depth: int = 1,
    include_ancestors: bool = True,
) -> str:
    """Retrieve a CPC class and its descendants, optionally with ancestors."""
    token = await get_access_token(client)
    params: dict[str, str | int] = {"depth": depth}
    if include_ancestors:
        params["ancestors"] = ""
    response = await client.get(
        f"{OPS_BASE_URL}/classification/cpc/{quote(symbol, safe='/')}",
        params=params,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/cpc+xml",
        },
    )
    response.raise_for_status()
    return response.text


def parse_cpc_xml(xml_text: str) -> list[dict[str, Any]]:
    """Convert CPC XML responses into compact, agent-friendly records."""
    root = ET.fromstring(xml_text)
    records: list[dict[str, Any]] = []
    for item in root.iter():
        item_type = item.tag.rsplit("}", 1)[-1]
        if item_type not in {"classification-item", "classification-statistics"}:
            continue
        symbol = item.attrib.get("classification-symbol")
        titles: list[str] = []
        for child in item.iter():
            local_name = child.tag.rsplit("}", 1)[-1]
            text = " ".join("".join(child.itertext()).split())
            if local_name == "classification-symbol" and text:
                symbol = text
            elif local_name == "text" and text and text not in titles:
                titles.append(text)
        if symbol:
            record: dict[str, Any] = {
                "symbol": symbol,
                "title": " ".join(titles),
            }
            for attribute in ("level", "additional-only", "not-allocatable"):
                if attribute in item.attrib:
                    record[attribute.replace("-", "_")] = item.attrib[attribute]
            if "percentage" in item.attrib:
                record["score"] = float(item.attrib["percentage"])
            records.append(record)
    return records


# Create MCP server instance
app = Server("espacenet-ops")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="get_patent_biblio",
            description="Retrieve bibliographic data for a patent publication (title, inventors, applicants, dates, etc.)",
            inputSchema={
                "type": "object",
                "properties": {
                    "publication_number": {
                        "type": "string",
                        "description": "Patent publication number (e.g., EP1234567A1, US2020123456A1, WO2020/123456A1)"
                    }
                },
                "required": ["publication_number"]
            }
        ),
        Tool(
            name="get_patent_description",
            description="Retrieve the description (specification) of a patent publication",
            inputSchema={
                "type": "object",
                "properties": {
                    "publication_number": {
                        "type": "string",
                        "description": "Patent publication number"
                    }
                },
                "required": ["publication_number"]
            }
        ),
        Tool(
            name="get_patent_claims",
            description="Retrieve the claims of a patent publication",
            inputSchema={
                "type": "object",
                "properties": {
                    "publication_number": {
                        "type": "string",
                        "description": "Patent publication number"
                    }
                },
                "required": ["publication_number"]
            }
        ),
        Tool(
            name="get_patent_images",
            description="Retrieve information about drawings/figures for a patent publication",
            inputSchema={
                "type": "object",
                "properties": {
                    "publication_number": {
                        "type": "string",
                        "description": "Patent publication number"
                    }
                },
                "required": ["publication_number"]
            }
        ),
        Tool(
            name="get_full_patent_data",
            description="Retrieve all available data for a patent publication (biblio, description, claims)",
            inputSchema={
                "type": "object",
                "properties": {
                    "publication_number": {
                        "type": "string",
                        "description": "Patent publication number"
                    }
                },
                "required": ["publication_number"]
            }
        ),
        Tool(
            name="find_text_in_patent",
            description="Search for quoted text in a patent's description and identify the paragraph number. Useful when examiner cites column/line numbers with a quote.",
            inputSchema={
                "type": "object",
                "properties": {
                    "publication_number": {
                        "type": "string",
                        "description": "Patent publication number"
                    },
                    "search_text": {
                        "type": "string",
                        "description": "Text excerpt to find in the patent description (e.g., text quoted by examiner)"
                    }
                },
                "required": ["publication_number", "search_text"]
            }
        ),
        Tool(
            name="search_patents",
            description="Search worldwide patent publications in EPO OPS using Espacenet CQL. Supports CPC/IPC, keyword, applicant, inventor, publication-date and other CQL fields; combine them with AND/OR/NOT.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Espacenet CQL query, e.g. 'cpc=H04L9/32 and ta=authentication' or 'ipc=G06F and pd within 2024'"
                    },
                    "start": {
                        "type": "integer",
                        "minimum": 1,
                        "default": 1,
                        "description": "One-based index of the first result"
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100,
                        "default": 25,
                        "description": "Number of results to return (1-100)"
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="search_cpc",
            description="Suggest likely Cooperative Patent Classification (CPC) symbols from technical keywords searched against patent titles and abstracts.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Technical keywords or CQL title/abstract query, e.g. 'event camera pixel'"
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_cpc_hierarchy",
            description="Retrieve a CPC symbol, its title, child classes and optionally its ancestors for classification expansion.",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "CPC symbol, e.g. H04L9/32"
                    },
                    "depth": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": 5,
                        "default": 1,
                        "description": "Number of descendant levels to include (0-5)"
                    },
                    "include_ancestors": {
                        "type": "boolean",
                        "default": True,
                        "description": "Include parent classes up to the CPC section"
                    }
                },
                "required": ["symbol"]
            }
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool calls."""
    
    if not OPS_CONSUMER_KEY or not OPS_CONSUMER_SECRET:
        raise ValueError(
            "EPO OPS credentials not configured. "
            "Set OPS_CONSUMER_KEY and OPS_CONSUMER_SECRET environment variables."
        )
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if name == "search_patents":
                query = str(arguments.get("query", "")).strip()
                start = int(arguments.get("start", 1))
                limit = int(arguments.get("limit", 25))
                if not query:
                    raise ValueError("query is required")
                if start < 1 or not 1 <= limit <= 100:
                    raise ValueError("start must be at least 1 and limit must be between 1 and 100")
                data = await search_published_data(client, query, start, limit)
                return [TextContent(type="text", text=json.dumps(data, indent=2, ensure_ascii=False))]

            if name == "search_cpc":
                query = str(arguments.get("query", "")).strip()
                if not query:
                    raise ValueError("query is required")
                records = parse_cpc_xml(await search_cpc_classes(client, query))
                return [TextContent(type="text", text=json.dumps({"query": query, "classes": records}, indent=2, ensure_ascii=False))]

            if name == "get_cpc_hierarchy":
                symbol = re.sub(r"\s+", "", str(arguments.get("symbol", ""))).upper()
                depth = int(arguments.get("depth", 1))
                include_ancestors = bool(arguments.get("include_ancestors", True))
                if not re.fullmatch(r"[A-HY]\d{2}[A-Z](?:\d+(?:/\d+)?)?", symbol):
                    raise ValueError("symbol must be a CPC symbol such as H04L9/32")
                if not 0 <= depth <= 5:
                    raise ValueError("depth must be between 0 and 5")
                records = parse_cpc_xml(await fetch_cpc_hierarchy(client, symbol, depth, include_ancestors))
                return [TextContent(type="text", text=json.dumps({"requested_symbol": symbol, "classes": records}, indent=2, ensure_ascii=False))]

            pub_num = arguments.get("publication_number")
            if not pub_num:
                raise ValueError("publication_number is required")
            try:
                pub_info = parse_publication_number(pub_num)
            except ValueError as e:
                return [TextContent(type="text", text=f"Error: {str(e)}")]

            if name == "get_patent_biblio":
                data = await fetch_bibliographic_data(client, pub_info)
                # Parse and format bibliographic data
                parsed = parse_biblio_json(data)
                formatted = format_biblio_for_display(parsed)
                return [TextContent(
                    type="text",
                    text=formatted
                )]
            
            elif name == "get_patent_description":
                try:
                    description_xml = await fetch_description(client, pub_info)
                    # Parse and format description
                    parsed = parse_description_xml(description_xml)
                    formatted = format_description_for_display(parsed)
                    return [TextContent(
                        type="text",
                        text=formatted
                    )]
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 404:
                        # Fallback to Google Patents (use doc_number_full with leading zeros)
                        google_patents_url = f"https://patents.google.com/patent/{pub_info['country']}{pub_info['doc_number_full']}{pub_info['kind']}/en"
                        return [TextContent(
                            type="text",
                            text=f"Description not available for {pub_num} via EPO OPS API.\n\n"
                                 f"Try fetching from Google Patents.\n\n"
                                 f"Please paste this into the chat:\n"
                                 f"Fetch {google_patents_url}"
                        )]
                    raise
            
            elif name == "get_patent_claims":
                try:
                    claims_xml = await fetch_claims(client, pub_info)
                    # Parse and format claims
                    parsed = parse_claims_xml(claims_xml)
                    formatted = format_claims_for_display(parsed)
                    return [TextContent(
                        type="text",
                        text=formatted
                    )]
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 404:
                        # Fallback to Google Patents (use doc_number_full with leading zeros)
                        google_patents_url = f"https://patents.google.com/patent/{pub_info['country']}{pub_info['doc_number_full']}{pub_info['kind']}/en"
                        return [TextContent(
                            type="text",
                            text=f"Claims not available for {pub_num} via EPO OPS API.\n\n"
                                 f"Try fetching from Google Patents.\n\n"
                                 f"Please paste this into the chat:\n"
                                 f"Fetch {google_patents_url}"
                        )]
                    raise
            
            elif name == "get_patent_images":
                images = await fetch_images(client, pub_info)
                import json
                formatted_images = json.dumps(images, indent=2)
                return [TextContent(
                    type="text",
                    text=f"Images information for {pub_num}:\n\n{formatted_images}"
                )]
            
            elif name == "find_text_in_patent":
                search_text = arguments.get("search_text", "")
                if not search_text:
                    return [TextContent(
                        type="text",
                        text="Error: search_text is required"
                    )]
                
                # Get description
                description_xml = await fetch_description(client, pub_info)
                parsed = parse_description_xml(description_xml)
                
                # Search for text in paragraphs
                search_normalized = " ".join(search_text.lower().split())
                matches = []
                
                for section in parsed.get("sections", []):
                    for i, para in enumerate(section.get("paragraphs", [])):
                        para_normalized = " ".join(para.lower().split())
                        if search_normalized in para_normalized:
                            matches.append({
                                "section": section.get("heading", "Unknown section"),
                                "paragraph_index": i,
                                "text": para,
                                "context_before": section["paragraphs"][i-1] if i > 0 else None,
                                "context_after": section["paragraphs"][i+1] if i < len(section["paragraphs"])-1 else None
                            })
                
                if not matches:
                    return [TextContent(
                        type="text",
                        text=f"Text not found in {pub_num}.\n\nSearched for: {search_text}\n\nThe quoted text may be in the claims instead of the description, or may be paraphrased differently in the XML version."
                    )]
                
                # Format results
                output = [f"Found {len(matches)} match(es) in {pub_num}:\n"]
                for idx, match in enumerate(matches, 1):
                    output.append(f"\nMatch {idx}:")
                    output.append(f"Section: {match['section']}")
                    output.append(f"Paragraph index in section: {match['paragraph_index']}")
                    output.append(f"\nMatching text:")
                    output.append(match['text'])
                    
                    if match['context_before']:
                        output.append(f"\nPrevious paragraph:")
                        output.append(match['context_before'])
                    
                    if match['context_after']:
                        output.append(f"\nNext paragraph:")
                        output.append(match['context_after'])
                    
                    output.append("\n" + "="*80)
                
                return [TextContent(
                    type="text",
                    text="\n".join(output)
                )]
            
            elif name == "get_full_patent_data":
                # Fetch all data
                biblio_json = await fetch_bibliographic_data(client, pub_info)
                description_xml = await fetch_description(client, pub_info)
                claims_xml = await fetch_claims(client, pub_info)
                images = await fetch_images(client, pub_info)
                
                # Parse data
                biblio_parsed = parse_biblio_json(biblio_json)
                desc_parsed = parse_description_xml(description_xml)
                claims_parsed = parse_claims_xml(claims_xml)
                
                # Format each section
                output_parts = [
                    f"Complete Patent Data for {pub_num}",
                    "=" * 80,
                    "",
                    format_biblio_for_display(biblio_parsed),
                    "",
                    format_description_for_display(desc_parsed),
                    "",
                    format_claims_for_display(claims_parsed)
                ]
                
                if images:
                    import json
                    output_parts.extend([
                        "",
                        "Drawing Information:",
                        json.dumps(images, indent=2)
                    ])
                
                return [TextContent(
                    type="text",
                    text="\n".join(output_parts)
                )]
            
            else:
                raise ValueError(f"Unknown tool: {name}")
        
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return [TextContent(
                    type="text",
                    text=f"Error: Patent publication {pub_num} not found in EPO OPS database."
                )]
            elif e.response.status_code == 403:
                return [TextContent(
                    type="text",
                    text=f"Error: Access denied. Check your EPO OPS credentials."
                )]
            else:
                return [TextContent(
                    type="text",
                    text=f"Error: HTTP {e.response.status_code} - {e.response.text}"
                )]
        
        except Exception as e:
            return [TextContent(
                type="text",
                text=f"Error: {str(e)}"
            )]


async def main():
    """Run the MCP server."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )


if __name__ == "__main__":
    asyncio.run(main())
