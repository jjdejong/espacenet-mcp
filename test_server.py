#!/usr/bin/env python3
"""
Test script for Espacenet MCP Server

This script tests the basic functionality without requiring an MCP client.
"""

import asyncio
import os
from dotenv import load_dotenv

# Import server functions
from server import (
    parse_publication_number,
    get_access_token,
    fetch_bibliographic_data,
    fetch_claims,
    fetch_description,
)

import httpx


async def test_parse_publication_number():
    """Test publication number parsing."""
    print("Testing publication number parsing...")
    print("-" * 80)
    
    test_cases = [
        "EP1234567A1",
        "EP 1234567 A1",
        "US2020123456A1",
        "WO2020/123456A1",
        "WO2020123456",
    ]
    
    for pub_num in test_cases:
        try:
            result = parse_publication_number(pub_num)
            print(f"✓ {pub_num:25s} → {result['country']}.{result['doc_number']}.{result['kind']}")
        except ValueError as e:
            print(f"✗ {pub_num:25s} → Error: {e}")
    
    print()


async def test_api_connection():
    """Test EPO OPS API connection."""
    print("Testing EPO OPS API connection...")
    print("-" * 80)
    
    load_dotenv()
    
    consumer_key = os.getenv("OPS_CONSUMER_KEY")
    consumer_secret = os.getenv("OPS_CONSUMER_SECRET")
    
    if not consumer_key or not consumer_secret:
        print("✗ EPO OPS credentials not configured")
        print("  Please set OPS_CONSUMER_KEY and OPS_CONSUMER_SECRET in .env file")
        return False
    
    print(f"  Consumer Key: {consumer_key[:10]}...")
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            token = await get_access_token(client)
            print(f"✓ Successfully obtained access token: {token[:20]}...")
            return True
    except Exception as e:
        print(f"✗ Failed to get access token: {e}")
        return False


async def test_fetch_patent_data():
    """Test fetching actual patent data."""
    print("\nTesting patent data retrieval...")
    print("-" * 80)
    
    load_dotenv()
    
    if not os.getenv("OPS_CONSUMER_KEY"):
        print("Skipping (credentials not configured)")
        return
    
    # Test with a known patent (EP1000000A1 - first EP patent)
    test_pub_num = "EP1000000A1"
    
    try:
        pub_info = parse_publication_number(test_pub_num)
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            print(f"Fetching bibliographic data for {test_pub_num}...")
            biblio = await fetch_bibliographic_data(client, pub_info)
            print("✓ Bibliographic data retrieved")
            
            print(f"Fetching claims for {test_pub_num}...")
            claims = await fetch_claims(client, pub_info)
            print(f"✓ Claims retrieved ({len(claims)} characters)")
            
            print(f"Fetching description for {test_pub_num}...")
            description = await fetch_description(client, pub_info)
            print(f"✓ Description retrieved ({len(description)} characters)")
            
            print("\n✓ All data successfully retrieved!")
            
    except Exception as e:
        print(f"✗ Error: {e}")


async def main():
    """Run all tests."""
    print("\n" + "=" * 80)
    print("Espacenet MCP Server - Test Suite")
    print("=" * 80 + "\n")
    
    # Test 1: Publication number parsing
    await test_parse_publication_number()
    
    # Test 2: API connection
    api_ok = await test_api_connection()
    
    # Test 3: Fetch patent data (only if API connection works)
    if api_ok:
        await test_fetch_patent_data()
    
    print("\n" + "=" * 80)
    print("Tests complete!")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    asyncio.run(main())
