#!/usr/bin/env python3
"""
Espacenet MCP Server

This MCP server provides access to patent specifications from Espacenet using the EPO Open Patent Services (OPS) API.
It retrieves bibliographic data, descriptions, claims, and drawings for published patent applications.

The server handles publication numbers in various formats commonly cited in office actions:
- EP1234567A1
- US2020123456A1
- WO2020/123456
- etc.
"""

import asyncio
import base64
import os
import re
from typing import Any

import httpx
from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    Resource,
    TextContent,
    ImageContent,
    Tool,
)
from pydantic import AnyUrl

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
    """
    # Remove spaces, hyphens, slashes and normalize
    pub_num = pub_num.replace(" ", "").replace("/", "").replace("-", "").upper()
    
    # Pattern: CC + number + optional kind code
    # Country code: 2 letters, Number: digits, Kind: 1-2 alphanumeric
    pattern = r'^([A-Z]{2})(\d+)([A-Z]\d?)?$'
    match = re.match(pattern, pub_num)
    
    if not match:
        raise ValueError(f"Invalid publication number format: {pub_num}")
    
    country_code, number, kind_code = match.groups()
    
    # Remove leading zeros from number for EPO format
    number = str(int(number))
    
    return {
        "country": country_code,
        "doc_number": number,
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


async def fetch_full_text_document(
    client: httpx.AsyncClient, pub_info: dict[str, str], doc_format: str = "pdf"
) -> bytes:
    """Fetch full text document (PDF or TIFF) for a patent publication."""
    token = await get_access_token(client)
    
    # Get document instance URL from images endpoint first
    images_data = await fetch_images(client, pub_info)
    
    if not images_data or 'ops:world-patent-data' not in images_data:
        raise ValueError("No document available for this publication")
    
    # Extract document reference
    # This is a simplified version - actual implementation would parse the JSON structure
    url = (
        f"{OPS_BASE_URL}/published-data/publication/"
        f"{pub_info['format']}/{pub_info['country']}.{pub_info['doc_number']}.{pub_info['kind']}/fulltext"
    )
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/pdf" if doc_format == "pdf" else "image/tiff"
    }
    
    response = await client.get(url, headers=headers)
    response.raise_for_status()
    
    return response.content


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
    
    pub_num = arguments.get("publication_number")
    if not pub_num:
        raise ValueError("publication_number is required")
    
    try:
        pub_info = parse_publication_number(pub_num)
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {str(e)}")]
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
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
                description_xml = await fetch_description(client, pub_info)
                # Parse and format description
                parsed = parse_description_xml(description_xml)
                formatted = format_description_for_display(parsed)
                return [TextContent(
                    type="text",
                    text=formatted
                )]
            
            elif name == "get_patent_claims":
                claims_xml = await fetch_claims(client, pub_info)
                # Parse and format claims
                parsed = parse_claims_xml(claims_xml)
                formatted = format_claims_for_display(parsed)
                return [TextContent(
                    type="text",
                    text=formatted
                )]
            
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
                            # Try to extract paragraph number from original text
                            # (paragraph numbers are often in the heading or structure)
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
