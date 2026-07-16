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
import gzip
from email.utils import parsedate_to_datetime
from html import unescape
import json
import os
import random
import re
import shutil
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path
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
token_refresh_lock = asyncio.Lock()

DEFAULT_SEARCH_RESULTS = 10
MAX_SEARCH_RESULTS = 100
# Abstract excerpt length per search hit; bounds screening cost to roughly
# 75 tokens per result while keeping the device-and-elements opening sentences.
ABSTRACT_SNIPPET_CHARS = 300
MAX_CPC_RESULTS = 20
DEFAULT_CPC_HIERARCHY_RESULTS = 10
MAX_CPC_TITLE_CHARS = 240
DEFAULT_EXCERPT_CONTEXT_CHARS = 300
MAX_EXCERPT_CONTEXT_CHARS = 1000
MAX_EXCERPT_MATCHES = 10
DEFAULT_FULLTEXT_RESULTS = 10
MAX_FULLTEXT_RESULTS = 10
MAX_SEARCH_REPORT_CITATION_HINTS = 5
CITATION_TITLE_CHARS = 200
CITATION_ABSTRACT_CHARS = 260
USPTO_PDF_BASE_URL = "https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf"
USPTO_OCR_CONCURRENCY = 4
MAX_SHORTLIST_CANDIDATES = 3
DEFAULT_SHORTLIST_MATCHES = 3
DESCRIPTION_CACHE_VERSION = 1
DESCRIPTION_CACHE_DIR = Path(
    os.path.expanduser(
        os.getenv("ESPACENET_MCP_CACHE_DIR", "~/.cache/espacenet-mcp")
    )
) / "descriptions"

# Google Patents has no official API and throttles bursty or bot-shaped
# clients with 429/503 responses.  All requests to it share one paced lane
# and a bounded Retry-After-aware retry schedule so a transient throttle
# does not surface as a missing description.
GOOGLE_PATENTS_MIN_INTERVAL = 3.0
GOOGLE_PATENTS_INTERVAL_JITTER = 1.0
GOOGLE_PATENTS_RETRY_DELAYS = (30.0, 120.0)
GOOGLE_PATENTS_MAX_RETRY_AFTER = 180.0
# Authorities whose OPS publications carry an English specification; family
# members from these offices can stand in for a publication whose own text
# is unavailable.
ENGLISH_DESCRIPTION_AUTHORITIES = ("EP", "WO", "US", "GB", "CA", "AU")
MAX_FAMILY_DESCRIPTION_ATTEMPTS = 3


class RequestPacer:
    """Serialize requests to one host with a minimum jittered interval."""

    def __init__(self, min_interval: float, jitter: float) -> None:
        self._min_interval = min_interval
        self._jitter = jitter
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_allowed - now)
            self._next_allowed = (
                max(now, self._next_allowed)
                + self._min_interval
                + random.uniform(0.0, self._jitter)
            )
        if delay > 0:
            await asyncio.sleep(delay)


google_patents_pacer = RequestPacer(
    GOOGLE_PATENTS_MIN_INTERVAL, GOOGLE_PATENTS_INTERVAL_JITTER
)


def retry_after_seconds(response: httpx.Response) -> float | None:
    """Parse a Retry-After header as delay seconds or an HTTP date."""
    value = str(response.headers.get("Retry-After") or "").strip()
    if not value:
        return None
    if value.isdigit():
        return float(value)
    try:
        target = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, target.timestamp() - time.time())


async def google_patents_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, str] | None = None,
    accept: str,
) -> httpx.Response:
    """Paced GET against Google Patents with bounded throttle retries."""
    for retry_delay in (*GOOGLE_PATENTS_RETRY_DELAYS, None):
        await google_patents_pacer.wait()
        response = await client.get(
            url,
            params=params,
            headers={
                "User-Agent": "Mozilla/5.0 HermesEspacenetMCP/1.0",
                "Accept": accept,
            },
            follow_redirects=True,
        )
        if response.status_code in (429, 503) and retry_delay is not None:
            server_delay = retry_after_seconds(response)
            delay = (
                retry_delay
                if server_delay is None
                else min(server_delay, GOOGLE_PATENTS_MAX_RETRY_AFTER)
            )
            await asyncio.sleep(delay)
            continue
        response.raise_for_status()
        return response
    raise RuntimeError("unreachable: retry loop always returns or raises")


class GooglePatentDescriptionParser(HTMLParser):
    """Collect visible text only from Google Patents' description section."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[tuple[str, bool]] = []
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        in_description = (
            (self._stack[-1][1] if self._stack else False)
            or (tag.lower() == "section" and attributes.get("itemprop", "").lower() == "description")
        )
        self._stack.append((tag.lower(), in_description))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if self._stack and self._stack[-1][1] and data.strip():
            self.parts.append(data)


def _publication_from_info(pub_info: dict[str, str]) -> str:
    return f"{pub_info['country']}{pub_info['doc_number_full']}{pub_info['kind']}"


def _description_cache_path(pub_info: dict[str, str]) -> Path:
    publication = re.sub(r"[^A-Z0-9]", "", _publication_from_info(pub_info).upper())
    return DESCRIPTION_CACHE_DIR / f"{publication}.json.gz"


def load_cached_description(
    pub_info: dict[str, str],
) -> tuple[str, str, str] | None:
    """Return immutable publication description text cached by publication number."""
    path = _description_cache_path(pub_info)
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        if payload.get("version") != DESCRIPTION_CACHE_VERSION:
            return None
        description = str(payload.get("description") or "")
        source = str(payload.get("source") or "")
        source_url = str(payload.get("source_url") or "")
        if description and source:
            return description, source, source_url
    except (OSError, ValueError, TypeError):
        return None
    return None


def cache_description(
    pub_info: dict[str, str], description: str, source: str, source_url: str
) -> None:
    """Persist description text so Google fetches and USPTO OCR are paid only once."""
    path = _description_cache_path(pub_info)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": DESCRIPTION_CACHE_VERSION,
            "publication_number": _publication_from_info(pub_info),
            "source": source,
            "source_url": source_url,
            "description": description,
        }
        temporary = path.with_suffix(path.suffix + ".tmp")
        with gzip.open(temporary, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        temporary.replace(path)
    except OSError:
        # Caching is an optimisation and must never make evidence retrieval fail.
        return


async def fetch_google_patent_description(
    client: httpx.AsyncClient, pub_info: dict[str, str]
) -> tuple[str, str]:
    """Fetch and parse full description text for targeted local phrase search."""
    publication = f"{pub_info['country']}{pub_info['doc_number_full']}{pub_info['kind']}"
    url = f"https://patents.google.com/patent/{publication}/en"
    response = await google_patents_get(client, url, accept="text/html,*/*;q=0.8")
    parser = GooglePatentDescriptionParser()
    parser.feed(response.text)
    description = " ".join(" ".join(parser.parts).split())
    if not description:
        raise ValueError("Google Patents returned no description text")
    return description, url


def isolate_uspto_description(ocr_text: str) -> str:
    """Keep the specification body from an OCRed US publication, excluding claims."""
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", ocr_text)
    text = text.replace("\r", "")
    heading = re.search(
        r"(?im)^\s*(?:CROSS[- ]REFERENCE TO RELATED APPLICATIONS|"
        r"FIELD OF (?:THE )?(?:DISCLOSURE|INVENTION)|TECHNICAL FIELD|"
        r"BACKGROUND(?: INFORMATION| OF THE INVENTION)?|SUMMARY(?: OF THE INVENTION)?)\s*$",
        text,
    )
    if not heading:
        raise ValueError("could not isolate the description in the USPTO publication PDF")
    description = text[heading.start():]
    claim_markers = [
        r"(?im)^\s*WHAT IS CLAIMED(?: IS)?:?\s*$",
        r"(?im)^\s*THE INVENTION CLAIMED IS:?\s*$",
        r"(?im)^\s*CLAIMS\s*$",
        r"(?i)\bthe terms used in\s+the following claims\b",
    ]
    cut_positions = []
    for marker in claim_markers:
        match = re.search(marker, description)
        if match:
            cut_positions.append(match.start())
    if cut_positions:
        description = description[:min(cut_positions)]
    description = " ".join(description.split())
    if len(description) < 200:
        raise ValueError("USPTO OCR returned no usable description text")
    return description


async def _run_process(*command: str, timeout: float = 90) -> tuple[bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.communicate()
        raise RuntimeError(f"Timed out running {Path(command[0]).name}")
    if process.returncode:
        detail = stderr.decode("utf-8", errors="replace").strip()[-500:]
        raise RuntimeError(f"{Path(command[0]).name} failed: {detail}")
    return stdout, stderr


async def fetch_uspto_pdf_description(
    client: httpx.AsyncClient, pub_info: dict[str, str]
) -> tuple[str, str]:
    """OCR the official US publication PDF and return its description only."""
    if pub_info.get("country") != "US":
        raise ValueError("USPTO publication PDF fallback is available only for US publications")
    pdftoppm = shutil.which("pdftoppm")
    tesseract = shutil.which("tesseract")
    if not pdftoppm or not tesseract:
        raise RuntimeError("USPTO PDF OCR requires pdftoppm and tesseract")

    document_number = pub_info["doc_number_full"]
    url = f"{USPTO_PDF_BASE_URL}/{document_number}"
    response = await client.get(
        url,
        headers={"User-Agent": "HermesEspacenetMCP/1.0", "Accept": "application/pdf"},
        follow_redirects=True,
    )
    response.raise_for_status()
    if not response.content.startswith(b"%PDF"):
        raise ValueError("USPTO publication endpoint did not return a PDF")

    with tempfile.TemporaryDirectory(prefix="espacenet-uspto-") as directory:
        workdir = Path(directory)
        pdf_path = workdir / "publication.pdf"
        pdf_path.write_bytes(response.content)
        page_prefix = workdir / "page"
        await _run_process(
            pdftoppm,
            "-jpeg",
            "-r",
            "150",
            str(pdf_path),
            str(page_prefix),
            timeout=90,
        )
        pages = sorted(workdir.glob("page-*.jpg"))
        if not pages:
            raise ValueError("USPTO publication PDF rendered no pages")
        semaphore = asyncio.Semaphore(USPTO_OCR_CONCURRENCY)

        async def ocr_page(page: Path) -> str:
            async with semaphore:
                stdout, _ = await _run_process(
                    tesseract,
                    str(page),
                    "stdout",
                    "-l",
                    "eng",
                    "--psm",
                    "3",
                    timeout=60,
                )
                return stdout.decode("utf-8", errors="replace")

        page_text = await asyncio.gather(*(ocr_page(page) for page in pages))
    return isolate_uspto_description("\n".join(page_text)), url


def _description_text_from_xml(description_xml: str) -> str:
    parsed = parse_description_xml(description_xml)
    return "\n".join(
        para
        for section in parsed.get("sections", [])
        for para in section.get("paragraphs", [])
    )


def _ops_description_url(pub_info: dict[str, str]) -> str:
    return (
        f"{OPS_BASE_URL}/published-data/publication/{pub_info['format']}/"
        f"{pub_info['country']}.{pub_info['doc_number']}.{pub_info['kind']}/description"
    )


async def fetch_family_members(
    client: httpx.AsyncClient, pub_info: dict[str, str]
) -> list[dict[str, str]]:
    """List INPADOC family members of a publication as parsed pub_info dicts."""
    token = await get_access_token(client)
    # The OPS family service resolves an epodoc reference without a kind code;
    # including the kind can 404 even when the publication itself is known.
    url = (
        f"{OPS_BASE_URL}/family/publication/"
        f"{pub_info['format']}/{pub_info['country']}.{pub_info['doc_number']}"
    )
    response = await client.get(
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()
    family = _ops_key(
        _ops_key(payload, "ops:world-patent-data", {}), "ops:patent-family", {}
    )
    members: list[dict[str, str]] = []
    for member in _as_list(_ops_key(family, "ops:family-member", [])):
        for reference in _as_list(_ops_key(member, "publication-reference", [])):
            for document_id in _as_list(_ops_key(reference, "document-id", [])):
                if _ops_key(document_id, "@document-id-type", "") != "docdb":
                    continue
                country = _ops_text(_ops_key(document_id, "country", ""))
                number = _ops_text(_ops_key(document_id, "doc-number", ""))
                kind = _ops_text(_ops_key(document_id, "kind", ""))
                if not country or not number:
                    continue
                try:
                    members.append(
                        parse_publication_number(f"{country}{number}{kind}")
                    )
                except ValueError:
                    continue
    return members


def _preferred_family_members(
    pub_info: dict[str, str], members: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Order distinct English-authority family members for text retrieval."""
    original = (pub_info["country"], pub_info["doc_number_full"])
    seen: set[tuple[str, str]] = set()
    ranked: list[tuple[int, str, dict[str, str]]] = []
    for member in members:
        identity = (member["country"], member["doc_number_full"])
        if identity == original or identity in seen:
            continue
        seen.add(identity)
        if member["country"] not in ENGLISH_DESCRIPTION_AUTHORITIES:
            continue
        ranked.append(
            (
                ENGLISH_DESCRIPTION_AUTHORITIES.index(member["country"]),
                member["kind"],
                member,
            )
        )
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [member for _, _, member in ranked[:MAX_FAMILY_DESCRIPTION_ATTEMPTS]]


async def get_description_text(
    client: httpx.AsyncClient, pub_info: dict[str, str]
) -> tuple[str, str, str, bool]:
    """Retrieve a complete description through the fastest available route.

    Publication descriptions are immutable evidence.  Cache the parsed text so
    later phrase probes and later Hermes sessions avoid another Google request
    or a full USPTO PDF render/OCR pass.  When OPS has no text for the exact
    publication, an English-authority family member's OPS text is preferred
    over any Google Patents request; the official USPTO PDF of the exact
    publication or a US family member is the last resort.
    """
    cached = load_cached_description(pub_info)
    if cached:
        description, source, source_url = cached
        return description, source, source_url, True

    publication = _publication_from_info(pub_info)
    try:
        description = _description_text_from_xml(
            await fetch_description(client, pub_info)
        )
        if not description:
            raise ValueError("EPO OPS returned no description text")
        source = "epo_ops_description"
        source_url = _ops_description_url(pub_info)
    except httpx.HTTPStatusError as error:
        if error.response.status_code != 404:
            raise
        failures = [f"EPO OPS has no description text for {publication}"]
        description = ""
        source = ""
        source_url = ""
        try:
            members = await fetch_family_members(client, pub_info)
        except Exception as family_error:
            members = []
            failures.append(f"OPS family lookup failed: {family_error}")
        candidates = _preferred_family_members(pub_info, members)
        for member in candidates:
            member_number = _publication_from_info(member)
            try:
                description = _description_text_from_xml(
                    await fetch_description(client, member)
                )
                if not description:
                    raise ValueError("EPO OPS returned no description text")
                source = "epo_ops_family_member_description"
                source_url = _ops_description_url(member)
                break
            except Exception as member_error:
                description = ""
                failures.append(f"family member {member_number}: {member_error}")
        if not description:
            try:
                description, source_url = await fetch_google_patent_description(
                    client, pub_info
                )
                source = "google_patents_description_fallback"
            except Exception as google_error:
                failures.append(f"Google Patents fallback failed: {google_error}")
        if not description:
            us_candidates = [
                candidate
                for candidate in (pub_info, *candidates)
                if candidate.get("country") == "US"
            ][:2]
            if not us_candidates:
                failures.append(
                    "Official USPTO PDF fallback unavailable: no US publication "
                    "in the family"
                )
            for candidate in us_candidates:
                candidate_number = _publication_from_info(candidate)
                try:
                    description, source_url = await fetch_uspto_pdf_description(
                        client, candidate
                    )
                    source = (
                        "uspto_publication_pdf_ocr_fallback"
                        if candidate is pub_info
                        else "uspto_family_member_pdf_ocr_fallback"
                    )
                    break
                except Exception as uspto_error:
                    description = ""
                    failures.append(
                        f"USPTO PDF fallback {candidate_number}: {uspto_error}"
                    )
        if not description:
            raise RuntimeError(
                f"Description text not available for {publication}. "
                + " ".join(f"{failure}." for failure in failures)
            )

    cache_description(pub_info, description, source, source_url)
    return description, source, source_url, False


def _google_fulltext_query(query: str) -> str:
    """Quote hyphenated concepts while preserving a compact all-term query."""
    terms = re.findall(r'"[^"]+"|\S+', " ".join(query.split()))
    normalized = [
        term if term.startswith('"') or "-" not in term else f'"{term}"'
        for term in terms
    ]
    return "+".join(normalized)


async def search_google_patents_fulltext(
    client: httpx.AsyncClient, query: str
) -> dict[str, Any]:
    encoded_query = quote(_google_fulltext_query(query), safe="+")
    response = await google_patents_get(
        client,
        "https://patents.google.com/xhr/query",
        params={"url": f"q={encoded_query}", "exp": ""},
        accept="application/json",
    )
    return response.json()


def compact_google_fulltext_response(
    data: dict[str, Any], query: str, limit: int
) -> dict[str, Any]:
    results_block = data.get("results", {}) if isinstance(data, dict) else {}
    compact: list[dict[str, str]] = []
    seen: set[str] = set()
    for cluster in results_block.get("cluster", []) or []:
        for item in cluster.get("result", []) or []:
            patent = item.get("patent", {}) if isinstance(item, dict) else {}
            publication = re.sub(
                r"[^A-Z0-9]", "", str(patent.get("publication_number", "")).upper()
            )
            if not publication or publication in seen:
                continue
            seen.add(publication)
            title = " ".join(
                unescape(re.sub(r"<[^>]+>", " ", str(patent.get("title", "")))).split()
            )
            snippet = " ".join(
                unescape(re.sub(r"<[^>]+>", " ", str(patent.get("snippet", "")))).split()
            )
            compact.append(
                {
                    "publication_number": publication,
                    "title": title,
                    "snippet": snippet[:500],
                    "priority_date": str(patent.get("priority_date", "")),
                    "publication_date": str(patent.get("publication_date", "")),
                    "inventor": str(patent.get("inventor", "")),
                    "assignee": str(patent.get("assignee", "")),
                    "url": f"https://patents.google.com/patent/{publication}/en",
                }
            )
            if len(compact) >= limit:
                break
        if len(compact) >= limit:
            break
    return {
        "query": query,
        "source": "google_patents_fulltext",
        "total_results": int(results_block.get("total_num_results", 0) or 0),
        "returned": len(compact),
        "results": compact,
        "note": (
            "Full-text discovery leads only. Shortlist by technical relevance, then use "
            "get_patent_biblio and description evidence; do not retrieve claims."
        ),
    }


def _is_us_pregrant(country: str, number: str, kind: str) -> bool:
    return (
        country == "US"
        and kind.startswith("A")
        and len(number) > 4
        and number[:4].startswith(("19", "20"))
        and number[4:].isdigit()
    )


def canonical_document_number(country: str, number: str, kind: str) -> str:
    """Return the conventional display form of a publication number body."""
    if _is_us_pregrant(country, number, kind):
        return f"{number[:4]}{number[4:].zfill(7)}"
    return number


def canonicalize_biblio_publication(parsed: dict[str, Any]) -> dict[str, Any]:
    publication = parsed.get("publication")
    if isinstance(publication, dict):
        country = str(publication.get("country", "")).upper()
        number = re.sub(r"\D", "", str(publication.get("number", "")))
        kind = str(publication.get("kind", "")).upper()
        if number:
            publication["number"] = canonical_document_number(
                country, number, kind
            )
    for cited in parsed.get("cited_documents", []):
        if not isinstance(cited, dict):
            continue
        match = re.fullmatch(r"([A-Z]{2})(\d+)([A-Z]\d?)", str(cited.get("number", "")).upper())
        if match:
            country, number, kind = match.groups()
            cited["number"] = f"{country}{canonical_document_number(country, number, kind)}{kind}"
    return parsed


def ops_document_number(country: str, number: str, kind: str) -> str:
    """Return the epodoc number expected by OPS."""
    if _is_us_pregrant(country, number, kind):
        return f"{number[:4]}{int(number[4:])}"
    return str(int(number))


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
    
    canonical_number = canonical_document_number(
        country_code, number, kind_code or ""
    )
    return {
        "country": country_code,
        "doc_number": ops_document_number(
            country_code, canonical_number, kind_code or ""
        ),
        "doc_number_full": canonical_number,
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

    # A shortlist bundle starts several OPS requests together. Only the first
    # coroutine should refresh a cold/expired token; the others reuse it after
    # the lock is released instead of racing the OAuth endpoint.
    async with token_refresh_lock:
        current_time = asyncio.get_event_loop().time()
        if access_token and current_time < (token_expiry - 60):
            return access_token

        auth = (OPS_CONSUMER_KEY, OPS_CONSUMER_SECRET)
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        data = {"grant_type": "client_credentials"}

        response = await client.post(
            OPS_AUTH_URL, auth=auth, headers=headers, data=data
        )
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


async def enrich_search_report_citations(
    client: httpx.AsyncClient, parsed: dict[str, Any]
) -> dict[str, Any]:
    """Add bounded screening hints to unlabelled search-report citations.

    OPS sometimes returns useful national-search-report citations without X/Y/A
    categories.  Fetching every cited document through separate model tool calls
    is slow and token-heavy, so enrich at most five with only title and a short
    abstract.  Failures are deliberately local to the individual citation.
    """
    candidates = [
        cited
        for cited in parsed.get("cited_documents", [])
        if isinstance(cited, dict)
        and not str(cited.get("category", "")).strip()
        and str(cited.get("phase", "")).casefold() == "national-search-report"
        and str(cited.get("number", "")).strip()
    ][:MAX_SEARCH_REPORT_CITATION_HINTS]

    async def enrich_one(cited: dict[str, Any]) -> None:
        try:
            pub_info = parse_publication_number(str(cited["number"]))
            data = await fetch_bibliographic_data(client, pub_info)
            hint = canonicalize_biblio_publication(parse_biblio_json(data))
            title = " ".join(str(hint.get("title", "")).split())
            abstract = " ".join(str(hint.get("abstract", "")).split())
            if title:
                cited["title"] = (
                    title
                    if len(title) <= CITATION_TITLE_CHARS
                    else title[: CITATION_TITLE_CHARS - 1].rstrip() + "…"
                )
            if abstract:
                cited["abstract_hint"] = (
                    abstract
                    if len(abstract) <= CITATION_ABSTRACT_CHARS
                    else abstract[: CITATION_ABSTRACT_CHARS - 1].rstrip() + "…"
                )
        except Exception:
            return

    await asyncio.gather(*(enrich_one(cited) for cited in candidates))
    return parsed


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
    client: httpx.AsyncClient,
    query: str,
    start: int = 1,
    limit: int = DEFAULT_SEARCH_RESULTS,
) -> dict[str, Any]:
    """Search OPS published data with an Espacenet CQL query.

    Requests the ``biblio`` constituent so each hit carries its title and
    abstract for screening; ``compact_search_response`` bounds what survives.
    """
    token = await get_access_token(client)
    end = start + limit - 1
    response = await client.get(
        f"{OPS_BASE_URL}/published-data/search/biblio",
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
    seen_symbols: set[str] = set()

    def compact_item_text(item: ET.Element) -> tuple[str | None, list[str]]:
        symbol = item.attrib.get("classification-symbol")
        titles: list[str] = []

        def visit(node: ET.Element) -> None:
            nonlocal symbol
            for child in node:
                local_name = child.tag.rsplit("}", 1)[-1]
                if local_name in {"classification-item", "classification-statistics"}:
                    continue
                text = " ".join("".join(child.itertext()).split())
                if local_name == "classification-symbol" and text:
                    symbol = text
                elif local_name == "text" and text and text not in titles:
                    titles.append(text)
                else:
                    visit(child)

        visit(item)
        return symbol, titles

    for item in root.iter():
        item_type = item.tag.rsplit("}", 1)[-1]
        if item_type not in {"classification-item", "classification-statistics"}:
            continue
        symbol, titles = compact_item_text(item)
        if symbol and symbol not in seen_symbols:
            title = " ".join(titles)
            if len(title) > MAX_CPC_TITLE_CHARS:
                title = title[: MAX_CPC_TITLE_CHARS - 1].rstrip() + "…"
            record: dict[str, Any] = {
                "symbol": symbol,
                "title": title,
            }
            for attribute in ("level", "additional-only", "not-allocatable"):
                if attribute in item.attrib:
                    record[attribute.replace("-", "_")] = item.attrib[attribute]
            if "percentage" in item.attrib:
                record["score"] = float(item.attrib["percentage"])
            records.append(record)
            seen_symbols.add(symbol)
    return records


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _ops_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("$", ""))
    return str(value or "")


def _ops_key(mapping: Any, name: str, default: Any = None) -> Any:
    if not isinstance(mapping, dict):
        return default
    return mapping.get(name, mapping.get(f"ops:{name}", default))


def _publication_number(reference: Any) -> str:
    """Extract one stable publication identifier from an OPS search reference."""
    document_ids = _as_list(_ops_key(reference, "document-id"))
    if not document_ids:
        publication_reference = _ops_key(reference, "publication-reference")
        document_ids = _as_list(_ops_key(publication_reference, "document-id"))
    preferred = next(
        (
            item
            for item in document_ids
            if isinstance(item, dict)
            and item.get("@document-id-type") == "docdb"
        ),
        document_ids[0] if document_ids else {},
    )
    if not isinstance(preferred, dict):
        return ""
    country = _ops_text(_ops_key(preferred, "country")).upper()
    number = re.sub(r"\D", "", _ops_text(_ops_key(preferred, "doc-number")))
    kind = _ops_text(_ops_key(preferred, "kind")).upper()
    number = canonical_document_number(country, number, kind)
    return f"{country}{number}{kind}" if country and number else ""


def validate_cql_query(query: str) -> None:
    """Reject ambiguous field aliases that previously caused silent recall loss."""
    fields = {
        match.group(1).lower()
        for match in re.finditer(
            r"\b([A-Za-z][A-Za-z0-9]*)\s*(?:=|within\b|<=|>=|<|>)",
            query,
            re.IGNORECASE,
        )
    }
    invalid = fields & {"an", "applicant", "inventor"}
    if invalid:
        names = ", ".join(sorted(invalid))
        raise ValueError(
            f"Unsupported or ambiguous CQL field(s): {names}. "
            "Use pa= for applicant and in= for inventor. For a known "
            "publication number, call get_patent_biblio instead of search_patents."
        )


def _preferred_language_entry(value: Any) -> Any:
    """Pick the English entry from an OPS single-or-list language field."""
    entries = _as_list(value)
    for entry in entries:
        if isinstance(entry, dict) and entry.get("@lang") == "en":
            return entry
    return entries[0] if entries else None


def _search_hit_title(bibliographic_data: Any) -> str:
    title = _preferred_language_entry(_ops_key(bibliographic_data, "invention-title"))
    return _ops_text(title) if title is not None else ""


def _search_hit_abstract(exchange_document: Any) -> str:
    abstract = _preferred_language_entry(_ops_key(exchange_document, "abstract"))
    if not isinstance(abstract, dict):
        return ""
    paragraphs = [_ops_text(p) for p in _as_list(abstract.get("p"))]
    return " ".join(part.strip() for part in paragraphs if part).strip()


def _snippet(text: str, max_chars: int) -> str:
    """Whitespace-normalise and truncate at a word boundary with an ellipsis."""
    normalized = " ".join(text.split())
    if max_chars <= 0 or len(normalized) <= max_chars:
        return normalized
    cut = normalized.rfind(" ", 0, max_chars)
    if cut <= 0:
        cut = max_chars
    return normalized[:cut] + "…"


def compact_search_response(
    data: dict[str, Any],
    query: str,
    start: int,
    requested_limit: int,
    abstract_chars: int = ABSTRACT_SNIPPET_CHARS,
) -> dict[str, Any]:
    """Reduce an OPS search response to screenable hits and pagination metadata."""
    world = data.get("ops:world-patent-data", data.get("world-patent-data", {}))
    search = _ops_key(world, "biblio-search", {})
    search_result = _ops_key(search, "search-result", {})
    # ``exchange-documents`` may be one wrapper or a list of wrappers, each
    # holding one or more ``exchange-document`` entries.
    exchange_documents: list = []
    for container in _as_list(_ops_key(search_result, "exchange-documents")):
        exchange_documents.extend(_as_list(_ops_key(container, "exchange-document")))
    if not exchange_documents:
        # Some collections answer without the biblio constituent; fall back to
        # bare publication references.
        exchange_documents = _as_list(_ops_key(search_result, "publication-reference"))

    seen: set[str] = set()
    results: list[dict[str, str]] = []
    for document in exchange_documents:
        bibliographic_data = _ops_key(document, "bibliographic-data") or document
        publication_number = _publication_number(bibliographic_data)
        if not publication_number or publication_number in seen:
            continue
        seen.add(publication_number)
        hit: dict[str, str] = {
            "publication_number": publication_number,
            "google_patents_url": (
                f"https://patents.google.com/patent/{publication_number}/en"
            ),
        }
        title = _search_hit_title(bibliographic_data)
        if title:
            hit["title"] = title
        if abstract_chars > 0:
            abstract = _snippet(_search_hit_abstract(document), abstract_chars)
            if abstract:
                hit["abstract"] = abstract
        results.append(hit)
        if len(results) >= min(requested_limit, MAX_SEARCH_RESULTS):
            break
    references = exchange_documents

    try:
        total = int(search.get("@total-result-count", len(results)))
    except (TypeError, ValueError):
        total = len(results)
    consumed = max(len(references), len(results))
    end = start + consumed - 1 if consumed else start - 1
    response: dict[str, Any] = {
        "query": query,
        "total_results": total,
        "start": start,
        "requested_limit": requested_limit,
        "effective_limit": min(requested_limit, MAX_SEARCH_RESULTS),
        "returned": len(results),
        "deduplication": "publication_number",
        "results": results,
        "note": (
            "Screen hits from the title and abstract excerpt; use get_patent_biblio "
            "only for shortlisted identifiers (full record, citations, family). OPS "
            "may return any member of a pertinent family; retain the hit and resolve "
            "a convenient-language equivalent during verification."
        ),
    }
    if end < total:
        response["next_start"] = end + 1
    return response


def no_search_results_response(
    query: str, start: int, requested_limit: int
) -> dict[str, Any]:
    return {
        "query": query,
        "total_results": 0,
        "start": start,
        "requested_limit": requested_limit,
        "effective_limit": min(requested_limit, MAX_SEARCH_RESULTS),
        "returned": 0,
        "results": [],
        "note": (
            "No OPS results found. Broaden terminology or use a full-text patent "
            "source before adding classification constraints."
        ),
    }


def literal_match_span(text: str, needle: str) -> tuple[int, int] | None:
    """Find a case-insensitive literal phrase without matching inside a word."""
    normalized_text = " ".join(text.split())
    normalized_needle = " ".join(needle.split())
    if not normalized_needle:
        return None
    escaped = re.escape(normalized_needle)
    prefix = r"(?<!\w)" if normalized_needle[0].isalnum() else ""
    suffix = r"(?!\w)" if normalized_needle[-1].isalnum() else ""
    match = re.search(prefix + escaped + suffix, normalized_text, re.IGNORECASE)
    return match.span() if match else None


def excerpt_around(text: str, needle: str, context_chars: int) -> str:
    """Return a bounded excerpt centred on a word-bounded literal match."""
    normalized_text = " ".join(text.split())
    span = literal_match_span(normalized_text, needle)
    if span is None:
        return normalized_text[: context_chars * 2]
    index, match_end = span
    start = max(0, index - context_chars)
    end = min(len(normalized_text), match_end + context_chars)
    prefix = "…" if start else ""
    suffix = "…" if end < len(normalized_text) else ""
    return f"{prefix}{normalized_text[start:end]}{suffix}"


def individual_term_pattern(term: str) -> re.Pattern[str]:
    """Match a keyword as a word, including its simple singular/plural form."""
    escaped = re.escape(term)
    if term.lower().endswith("s") and len(term) > 3:
        escaped = re.escape(term[:-1]) + "s?"
    elif len(term) > 3:
        escaped += "s?"
    return re.compile(r"(?<!\w)" + escaped + r"(?!\w)", re.IGNORECASE)


def excerpt_around_individual_term(
    text: str, term: str, context_chars: int, *, last: bool = False
) -> str:
    """Return context around the first or last singular/plural keyword match."""
    normalized_text = " ".join(text.split())
    matches = list(individual_term_pattern(term).finditer(normalized_text))
    if not matches:
        return normalized_text[: context_chars * 2]
    match = matches[-1] if last else matches[0]
    start = max(0, match.start() - context_chars)
    end = min(len(normalized_text), match.end() + context_chars)
    prefix = "…" if start else ""
    suffix = "…" if end < len(normalized_text) else ""
    return f"{prefix}{normalized_text[start:end]}{suffix}"


def literal_excerpt_matches(
    text: str, needle: str, context_chars: int, max_matches: int
) -> list[str]:
    """Return bounded excerpts for successive word-bounded literal matches."""
    remaining = " ".join(text.split())
    excerpts: list[str] = []
    while remaining and len(excerpts) < max_matches:
        span = literal_match_span(remaining, needle)
        if span is None:
            break
        excerpts.append(excerpt_around(remaining, needle, context_chars))
        remaining = remaining[max(span[1], 1) :]
    return excerpts



# Closed-class English words carry no evidence on their own. A prose-style
# relationship_text otherwise feeds connectives such as "both" or "between"
# into the term bag, where their low description counts outrank substantive
# terms and consume the bounded excerpt slots.
EXCERPT_STOPWORDS = frozenset(
    """
    a an and any are all also as at be been being between both but by can
    could did do does each either for from had has have how into is it its may might
    more most not of on one only onto or other over shall should some such
    than that the their them then there these they this those through thus
    too two under upon use used uses using via was were what when where
    which while will with within would
    """.split()
)


def individual_term_excerpt_matches(
    text: str,
    query: str,
    context_chars: int,
    max_matches: int,
    priority_text: str = "",
) -> tuple[list[dict[str, str]], dict[str, int]]:
    """Fall back from a keyword bag, prioritising its rarer description terms."""
    terms: list[str] = []
    for term in re.findall(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", query):
        if (
            len(term) >= 3
            and term.lower() not in EXCERPT_STOPWORDS
            and term.lower() not in {item.lower() for item in terms}
        ):
            terms.append(term)
    counts: dict[str, int] = {}
    normalized = " ".join(text.split())
    for term in terms:
        pattern = individual_term_pattern(term)
        counts[term] = len(pattern.findall(normalized))

    # Query bags commonly begin with broad capability words (for example,
    # "image", "sensor", or "control"). Returning matches in query order lets
    # those words consume the bounded excerpt budget before a rare structural or
    # relational term is reached. Rarity within this candidate's description is
    # a cheap, technology-neutral proxy for discriminative evidence.
    query_order = {term: index for index, term in enumerate(terms)}
    priority_terms = {
        term.lower()
        for term in re.findall(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", priority_text)
    }
    ranked_terms = sorted(
        (term for term in terms if counts[term]),
        key=lambda term: (
            0 if term.lower() in priority_terms else 1,
            counts[term],
            query_order[term],
        ),
    )
    prioritized = [term for term in ranked_terms if term.lower() in priority_terms]
    ordinary = [term for term in ranked_terms if term.lower() not in priority_terms]
    matches = [
        {
            "term": term,
            "excerpt": excerpt_around_individual_term(
                normalized, term, context_chars
            ),
        }
        for term in prioritized[:max_matches]
    ]
    # When only one side of a requested relationship is named literally, keep a
    # later occurrence too. Patent specifications often introduce a connection
    # in a schematic paragraph and explain its physical operation in a later
    # embodiment. This costs no fetch and avoids mistaking the first occurrence
    # for the complete disclosure.
    for term in prioritized:
        if len(matches) >= max_matches:
            break
        if counts[term] > 1:
            matches.append(
                {
                    "term": term,
                    "excerpt": excerpt_around_individual_term(
                        normalized, term, context_chars, last=True
                    ),
                }
            )
    for term in ordinary:
        if len(matches) >= max_matches:
            break
        matches.append(
            {
                "term": term,
                "excerpt": excerpt_around_individual_term(
                    normalized, term, context_chars
                ),
            }
        )
    return matches, counts


def description_evidence(
    description: str,
    search_text: str,
    context_chars: int,
    max_matches: int,
    priority_text: str = "",
) -> dict[str, Any]:
    """Build the same bounded evidence shape for OPS, Google, or OCR text."""
    excerpts = literal_excerpt_matches(
        description, search_text, context_chars, max_matches
    )
    term_matches: list[dict[str, str]] = []
    term_counts: dict[str, int] = {}
    match_mode = "exact_phrase"
    if not excerpts and len(search_text.split()) > 1:
        term_matches, term_counts = individual_term_excerpt_matches(
            description, search_text, context_chars, max_matches, priority_text
        )
        match_mode = "individual_terms"
    matches = (
        [{"section": "Description", "excerpt": excerpt} for excerpt in excerpts]
        if excerpts
        else [
            {
                "section": "Description",
                "term": match["term"],
                "excerpt": match["excerpt"],
            }
            for match in term_matches
        ]
    )
    return {
        "match_mode": match_mode,
        "term_counts": term_counts,
        "returned": len(matches),
        "matches": matches,
    }


def compact_biblio_evidence(parsed: dict[str, Any]) -> dict[str, Any]:
    """Keep only fields needed to identify, date, and rank a final candidate."""
    citations = []
    for cited in parsed.get("cited_documents", []):
        if not isinstance(cited, dict):
            continue
        citations.append(
            {
                key: cited[key]
                for key in ("number", "category", "phase")
                if cited.get(key)
            }
        )
    return {
        "publication": parsed.get("publication", {}),
        "title": parsed.get("title", ""),
        "abstract": _snippet(str(parsed.get("abstract") or ""), 700),
        "inventors": parsed.get("inventors", []),
        "applicants": parsed.get("applicants", []),
        "priorities": parsed.get("priorities", []),
        "cpc": parsed.get("classifications", {}).get("cpc", []),
        "cited_documents": citations,
        "family_id": parsed.get("family_id", ""),
    }


async def build_shortlist_candidate_evidence(
    client: httpx.AsyncClient,
    candidate: dict[str, Any],
    context_chars: int,
    max_matches: int,
) -> dict[str, Any]:
    publication_number = str(candidate.get("publication_number") or "").strip()
    search_text = str(candidate.get("search_text") or "").strip()
    relationship_text = str(candidate.get("relationship_text") or "").strip()
    if not publication_number or not search_text:
        raise ValueError(
            "Each shortlist candidate requires publication_number and search_text"
        )
    pub_info = parse_publication_number(publication_number)

    async def get_biblio() -> dict[str, Any]:
        data = await fetch_bibliographic_data(client, pub_info)
        return canonicalize_biblio_publication(parse_biblio_json(data))

    biblio, description_result = await asyncio.gather(
        get_biblio(), get_description_text(client, pub_info)
    )
    description, source, source_url, cache_hit = description_result
    evidence_query = " ".join(part for part in (relationship_text, search_text) if part)
    evidence = description_evidence(
        description,
        evidence_query,
        context_chars,
        max_matches,
        priority_text=relationship_text,
    )
    evidence.update(
        {
            "publication_number": _publication_from_info(pub_info),
            "search_text": search_text,
            "relationship_text": relationship_text,
            "source": source,
            "source_url": source_url + (
                "#description"
                if source == "google_patents_description_fallback"
                else ""
            ),
            "description_cache_hit": cache_hit,
            "biblio": compact_biblio_evidence(biblio),
        }
    )
    return evidence


# Create MCP server instance
app = Server("espacenet-ops")


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available tools."""
    return [
        Tool(
            name="get_patent_biblio",
            description=(
                "Retrieve bibliographic data for a patent publication: title, abstract, "
                "inventors, applicants, dates, CPC/IPC classifications, INPADOC family id, "
                "and cited documents with search-report categories (X/Y citations are "
                "examiner-flagged prior-art leads worth harvesting)."
            ),
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
            description="Search for literal text in a patent description and return bounded matching excerpts. Prefer this over retrieving a complete description during screening.",
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
                    },
                    "max_matches": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_EXCERPT_MATCHES,
                        "default": 5,
                        "description": "Maximum matching excerpts to return (1-10)"
                    },
                    "context_chars": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": MAX_EXCERPT_CONTEXT_CHARS,
                        "default": DEFAULT_EXCERPT_CONTEXT_CHARS,
                        "description": "Characters of surrounding context on each side (0-1000)"
                    }
                },
                "required": ["publication_number", "search_text"]
            }
        ),
        Tool(
            name="verify_patent_shortlist",
            description=(
                "Verify up to three final patent candidates in one parallel call. "
                "Returns compact bibliography and bounded description excerpts for each "
                "candidate, never claims. Use only after discovery has selected the final "
                "shortlist; this reduces serial model/tool round trips."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "candidates": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": MAX_SHORTLIST_CANDIDATES,
                        "items": {
                            "type": "object",
                            "properties": {
                                "publication_number": {
                                    "type": "string",
                                    "description": "A publication identifier returned by discovery or a bibliographic record"
                                },
                                "search_text": {
                                    "type": "string",
                                    "description": "Short phrase or keyword bag expressing the technical relationship to verify"
                                },
                                "relationship_text": {
                                    "type": "string",
                                    "description": "Optional source-derived relationship objects to prioritize within the candidate description; these are evidence terms, not database filters"
                                }
                            },
                            "required": ["publication_number", "search_text"]
                        }
                    },
                    "max_matches": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 5,
                        "default": DEFAULT_SHORTLIST_MATCHES,
                        "description": "Maximum bounded description excerpts per candidate (1-5)"
                    },
                    "context_chars": {
                        "type": "integer",
                        "minimum": 0,
                        "maximum": MAX_EXCERPT_CONTEXT_CHARS,
                        "default": DEFAULT_EXCERPT_CONTEXT_CHARS,
                        "description": "Characters of context on each side of each match"
                    }
                },
                "required": ["candidates"]
            }
        ),
        Tool(
            name="search_patent_fulltext",
            description=(
                "Search indexed Google Patents full text and return compact patent leads. "
                "Use as a bounded fallback when ordinary web discovery misses description-only "
                "terminology. This is independent of OPS and does not search or return claims."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Short technical query using relationship and function terms, without party names"
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_FULLTEXT_RESULTS,
                        "default": DEFAULT_FULLTEXT_RESULTS,
                        "description": "Maximum compact full-text leads to return (1-10)"
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="search_patents",
            description="Search EPO OPS bibliographic data and title/abstract text using Espacenet CQL; this is not a claims or description full-text search. Returns compact hits with title and a bounded abstract excerpt for screening, plus pagination metadata. Supports keyword-free classification intersection (cpc=X and cpc=Y) and citation queries (ct=). Use pa= for applicant, in= for inventor, parenthesise mixed AND/OR expressions, and use get_patent_biblio for a known publication number.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Espacenet CQL query, e.g. 'cpc=H04L9/32 and ta=authentication', 'cpc=G01J1/44 and cpc=G01S7/486', or 'ct=EP1000000'"
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
                        "maximum": MAX_SEARCH_RESULTS,
                        "default": DEFAULT_SEARCH_RESULTS,
                        "description": "Number of compact results to return (1-100). Use start/next_start to paginate a high-signal query."
                    },
                    "raw": {
                        "type": "boolean",
                        "default": False,
                        "description": "Return raw OPS JSON for diagnostics. Keep false during normal research."
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
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": MAX_CPC_RESULTS,
                        "default": DEFAULT_CPC_HIERARCHY_RESULTS,
                        "description": "Maximum compact hierarchy records to return (1-20)"
                    }
                },
                "required": ["symbol"]
            }
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool calls."""
    
    if name != "search_patent_fulltext" and (not OPS_CONSUMER_KEY or not OPS_CONSUMER_SECRET):
        raise ValueError(
            "EPO OPS credentials not configured. "
            "Set OPS_CONSUMER_KEY and OPS_CONSUMER_SECRET environment variables."
        )
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            if name == "search_patent_fulltext":
                query = str(arguments.get("query", "")).strip()
                requested_limit = int(arguments.get("limit", DEFAULT_FULLTEXT_RESULTS))
                if not query:
                    raise ValueError("query is required")
                if not 1 <= requested_limit <= MAX_FULLTEXT_RESULTS:
                    raise ValueError(
                        f"limit must be between 1 and {MAX_FULLTEXT_RESULTS}"
                    )
                payload = compact_google_fulltext_response(
                    await search_google_patents_fulltext(client, query),
                    query,
                    requested_limit,
                )
                return [TextContent(
                    type="text",
                    text=json.dumps(payload, ensure_ascii=False),
                )]

            if name == "verify_patent_shortlist":
                candidates = arguments.get("candidates")
                if not isinstance(candidates, list) or not (
                    1 <= len(candidates) <= MAX_SHORTLIST_CANDIDATES
                ):
                    raise ValueError(
                        f"candidates must contain between 1 and {MAX_SHORTLIST_CANDIDATES} items"
                    )
                if not all(isinstance(item, dict) for item in candidates):
                    raise ValueError("each shortlist candidate must be an object")
                max_matches = int(
                    arguments.get("max_matches", DEFAULT_SHORTLIST_MATCHES)
                )
                context_chars = int(
                    arguments.get(
                        "context_chars", DEFAULT_EXCERPT_CONTEXT_CHARS
                    )
                )
                if not 1 <= max_matches <= 5:
                    raise ValueError("max_matches must be between 1 and 5")
                if not 0 <= context_chars <= MAX_EXCERPT_CONTEXT_CHARS:
                    raise ValueError("context_chars must be between 0 and 1000")

                started = time.monotonic()
                raw_results = await asyncio.gather(
                    *(
                        build_shortlist_candidate_evidence(
                            client, candidate, context_chars, max_matches
                        )
                        for candidate in candidates
                    ),
                    return_exceptions=True,
                )
                results: list[dict[str, Any]] = []
                for candidate, result in zip(candidates, raw_results):
                    if isinstance(result, Exception):
                        results.append(
                            {
                                "publication_number": str(
                                    candidate.get("publication_number") or ""
                                ),
                                "error": f"{type(result).__name__}: {result}",
                            }
                        )
                    else:
                        results.append(result)
                payload = {
                    "source": "parallel_shortlist_verification",
                    "requested": len(candidates),
                    "verified": sum("error" not in item for item in results),
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "candidates": results,
                    "note": (
                        "Final-shortlist evidence only. Rank on the returned description "
                        "passages, keep technical relevance separate from date status, and "
                        "do not retrieve claims."
                    ),
                }
                return [TextContent(
                    type="text",
                    text=json.dumps(payload, ensure_ascii=False),
                )]

            if name == "search_patents":
                query = str(arguments.get("query", "")).strip()
                start = int(arguments.get("start", 1))
                requested_limit = int(arguments.get("limit", DEFAULT_SEARCH_RESULTS))
                limit = min(requested_limit, MAX_SEARCH_RESULTS)
                raw = arguments.get("raw") is True
                if not query:
                    raise ValueError("query is required")
                if start < 1 or requested_limit < 1:
                    raise ValueError("start and limit must be at least 1")
                validate_cql_query(query)
                try:
                    data = await search_published_data(client, query, start, limit)
                except httpx.HTTPStatusError as error:
                    if error.response.status_code == 404:
                        compact = no_search_results_response(
                            query, start, requested_limit
                        )
                        return [TextContent(
                            type="text",
                            text=json.dumps(compact, ensure_ascii=False),
                        )]
                    raise
                payload = (
                    data
                    if raw
                    else compact_search_response(
                        data, query, start, requested_limit
                    )
                )
                return [TextContent(
                    type="text",
                    text=json.dumps(payload, ensure_ascii=False),
                )]

            if name == "search_cpc":
                query = str(arguments.get("query", "")).strip()
                if not query:
                    raise ValueError("query is required")
                records = parse_cpc_xml(await search_cpc_classes(client, query))
                payload = {
                    "query": query,
                    "returned": min(len(records), MAX_CPC_RESULTS),
                    "classes": records[:MAX_CPC_RESULTS],
                }
                return [TextContent(
                    type="text",
                    text=json.dumps(payload, ensure_ascii=False),
                )]

            if name == "get_cpc_hierarchy":
                symbol = re.sub(r"\s+", "", str(arguments.get("symbol", ""))).upper()
                depth = int(arguments.get("depth", 1))
                include_ancestors = bool(arguments.get("include_ancestors", True))
                limit = int(arguments.get("limit", DEFAULT_CPC_HIERARCHY_RESULTS))
                if not re.fullmatch(r"[A-HY]\d{2}[A-Z](?:\d+(?:/\d+)?)?", symbol):
                    raise ValueError("symbol must be a CPC symbol such as H04L9/32")
                if not 0 <= depth <= 5:
                    raise ValueError("depth must be between 0 and 5")
                if not 1 <= limit <= MAX_CPC_RESULTS:
                    raise ValueError(f"limit must be between 1 and {MAX_CPC_RESULTS}")
                records = parse_cpc_xml(await fetch_cpc_hierarchy(client, symbol, depth, include_ancestors))
                payload = {
                    "requested_symbol": symbol,
                    "returned": min(len(records), limit),
                    "total_available": len(records),
                    "classes": records[:limit],
                }
                return [TextContent(
                    type="text",
                    text=json.dumps(payload, ensure_ascii=False),
                )]

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
                parsed = canonicalize_biblio_publication(parse_biblio_json(data))
                parsed = await enrich_search_report_citations(client, parsed)
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

                max_matches = int(arguments.get("max_matches", 5))
                context_chars = int(
                    arguments.get(
                        "context_chars", DEFAULT_EXCERPT_CONTEXT_CHARS
                    )
                )
                if not 1 <= max_matches <= MAX_EXCERPT_MATCHES:
                    raise ValueError("max_matches must be between 1 and 10")
                if not 0 <= context_chars <= MAX_EXCERPT_CONTEXT_CHARS:
                    raise ValueError("context_chars must be between 0 and 1000")
                try:
                    description, source, source_url, cache_hit = await get_description_text(
                        client, pub_info
                    )
                except RuntimeError as error:
                    google_patents_url = (
                        f"https://patents.google.com/patent/"
                        f"{_publication_from_info(pub_info)}/en"
                    )
                    return [TextContent(
                        type="text",
                        text=f"{error}\nFetch {google_patents_url}#description",
                    )]

                payload = {
                    "publication_number": _publication_from_info(pub_info),
                    "search_text": str(search_text),
                    "source": source,
                    "source_url": source_url + (
                        "#description"
                        if source == "google_patents_description_fallback"
                        else ""
                    ),
                    "description_cache_hit": cache_hit,
                }
                payload.update(
                    description_evidence(
                        description,
                        str(search_text),
                        context_chars,
                        max_matches,
                    )
                )
                if not payload["matches"]:
                    payload["note"] = (
                        "No literal description match. Try one paraphrase or retrieve a "
                        "bounded description window; do not retrieve claims."
                    )
                return [TextContent(
                    type="text",
                    text=json.dumps(payload, ensure_ascii=False),
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
            if name in {"search_patents", "search_cpc", "get_cpc_hierarchy"}:
                return [TextContent(
                    type="text",
                    text=(
                        f"Error: {name} request failed with HTTP "
                        f"{e.response.status_code}. Broaden or correct the query; "
                        "raw server bodies are omitted."
                    )
                )]
            elif e.response.status_code == 404:
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
        
        except httpx.TimeoutException:
            return [TextContent(
                type="text",
                text=json.dumps({
                    "error": {
                        "type": "timeout",
                        "tool": name,
                        "retryable": True,
                        "message": "EPO OPS request timed out after 30 seconds; retry this call once.",
                    }
                })
            )]

        except Exception as e:
            detail = str(e).strip() or type(e).__name__
            return [TextContent(
                type="text",
                text=f"Error: {detail}"
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
