"""
Utilities for parsing EPO OPS XML responses into readable formats.
"""

import re
from xml.etree import ElementTree as ET
from typing import Dict, List, Any


def strip_namespace(tag: str) -> str:
    """Remove XML namespace from tag."""
    return tag.split('}')[-1] if '}' in tag else tag


def parse_claims_xml(xml_string: str) -> Dict[str, Any]:
    """
    Parse claims XML into structured format.
    
    Returns dictionary with:
    - language: Language code
    - claims: List of claim dictionaries with number and text
    """
    try:
        root = ET.fromstring(xml_string)
        
        claims_data = {
            "language": None,
            "claims": []
        }
        
        # Find all claim elements
        for claims_elem in root.iter():
            tag = strip_namespace(claims_elem.tag)
            
            if tag == "claims":
                claims_data["language"] = claims_elem.get("lang", "en")
                
                # Extract individual claims
                for claim_elem in claims_elem.findall(".//*"):
                    if strip_namespace(claim_elem.tag) == "claim":
                        claim_num = claim_elem.get("num", "")
                        claim_text = extract_text_from_element(claim_elem)
                        
                        claims_data["claims"].append({
                            "number": claim_num,
                            "text": claim_text.strip()
                        })
        
        return claims_data
    
    except ET.ParseError as e:
        return {
            "error": f"XML parsing error: {str(e)}",
            "raw": xml_string
        }


def parse_description_xml(xml_string: str) -> Dict[str, Any]:
    """
    Parse description XML into structured format.
    
    Returns dictionary with:
    - language: Language code
    - sections: List of section dictionaries with headings and text
    """
    try:
        root = ET.fromstring(xml_string)
        
        desc_data = {
            "language": None,
            "sections": []
        }
        
        # Find description element
        for desc_elem in root.iter():
            tag = strip_namespace(desc_elem.tag)
            
            if tag == "description":
                desc_data["language"] = desc_elem.get("lang", "en")
                
                current_section = {"heading": "", "paragraphs": []}
                
                # Process paragraphs and headings
                for child in desc_elem.iter():
                    child_tag = strip_namespace(child.tag)
                    
                    if child_tag == "heading":
                        # Save previous section if it has content
                        if current_section["paragraphs"]:
                            desc_data["sections"].append(current_section)
                        
                        # Start new section
                        current_section = {
                            "heading": extract_text_from_element(child).strip(),
                            "paragraphs": []
                        }
                    
                    elif child_tag == "p":
                        text = extract_text_from_element(child).strip()
                        if text:
                            current_section["paragraphs"].append(text)
                
                # Add final section
                if current_section["paragraphs"]:
                    desc_data["sections"].append(current_section)
        
        return desc_data
    
    except ET.ParseError as e:
        return {
            "error": f"XML parsing error: {str(e)}",
            "raw": xml_string
        }


def extract_text_from_element(element: ET.Element) -> str:
    """Recursively extract all text from an XML element."""
    text_parts = []
    
    if element.text:
        text_parts.append(element.text)
    
    for child in element:
        text_parts.append(extract_text_from_element(child))
        if child.tail:
            text_parts.append(child.tail)
    
    return " ".join(text_parts)


def format_claims_for_display(claims_data: Dict[str, Any]) -> str:
    """Format parsed claims data for readable display."""
    if "error" in claims_data:
        return f"Error parsing claims: {claims_data['error']}\n\nRaw XML:\n{claims_data.get('raw', '')}"
    
    output = []
    output.append(f"Claims (Language: {claims_data.get('language', 'Unknown')})")
    output.append("=" * 80)
    output.append("")
    
    for claim in claims_data.get("claims", []):
        output.append(f"Claim {claim['number']}:")
        output.append(claim['text'])
        output.append("")
    
    return "\n".join(output)


def format_description_for_display(desc_data: Dict[str, Any]) -> str:
    """Format parsed description data for readable display."""
    if "error" in desc_data:
        return f"Error parsing description: {desc_data['error']}\n\nRaw XML:\n{desc_data.get('raw', '')}"
    
    output = []
    output.append(f"Description (Language: {desc_data.get('language', 'Unknown')})")
    output.append("=" * 80)
    output.append("")
    
    for section in desc_data.get("sections", []):
        if section.get("heading"):
            output.append(section["heading"])
            output.append("-" * len(section["heading"]))
            output.append("")
        
        for para in section.get("paragraphs", []):
            # Wrap long paragraphs
            output.append(para)
            output.append("")
    
    return "\n".join(output)


def _as_list(value: Any) -> list:
    """Normalise OPS JSON values that may be a single object or a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def parse_biblio_json(biblio_json: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract key bibliographic fields from EPO OPS JSON response.

    Returns simplified dictionary with:
    - publication_number, publication_date, publication_kind
    - application_number, application_date
    - title, abstract
    - inventors
    - applicants
    - ipc and cpc classifications
    - priorities
    - cited_documents (search-report/applicant citations with categories) and cited_npl
    - family_id
    """
    result = {
        "publication": {},
        "application": {},
        "title": "",
        "abstract": "",
        "inventors": [],
        "applicants": [],
        "classifications": {
            "ipc": [],
            "cpc": []
        },
        "priorities": [],
        "cited_documents": [],
        "cited_npl": [],
        "family_id": ""
    }
    
    try:
        # Navigate the nested JSON structure
        # EPO OPS JSON has a specific structure: ops:world-patent-data -> exchange-documents -> exchange-document
        world_data = biblio_json.get("ops:world-patent-data", {})
        
        exchange_docs = world_data.get("exchange-documents", {})
        if not exchange_docs:
            return result
        
        # Get first exchange document
        exchange_doc = exchange_docs.get("exchange-document", {})
        if isinstance(exchange_doc, list):
            exchange_doc = exchange_doc[0]
        
        # Bibliographic data
        biblio_data = exchange_doc.get("bibliographic-data", {})

        # INPADOC family identifier (exchange-document attribute)
        result["family_id"] = str(exchange_doc.get("@family-id", "") or "")

        # Abstract (exchange-document level; prefer English)
        abstracts = _as_list(exchange_doc.get("abstract"))
        chosen_abstract = None
        for abstract in abstracts:
            if isinstance(abstract, dict) and abstract.get("@lang") == "en":
                chosen_abstract = abstract
                break
        if chosen_abstract is None and abstracts:
            chosen_abstract = abstracts[0]
        if isinstance(chosen_abstract, dict):
            paragraphs = [
                p.get("$", "") if isinstance(p, dict) else str(p)
                for p in _as_list(chosen_abstract.get("p"))
            ]
            result["abstract"] = " ".join(part.strip() for part in paragraphs if part).strip()

        # Publication reference
        pub_ref = biblio_data.get("publication-reference", {})
        if pub_ref:
            doc_id = pub_ref.get("document-id", {})
            if isinstance(doc_id, list):
                doc_id = doc_id[0]  # Take first (usually DOCDB format)
            
            result["publication"] = {
                "country": doc_id.get("country", {}).get("$", ""),
                "number": doc_id.get("doc-number", {}).get("$", ""),
                "kind": doc_id.get("kind", {}).get("$", ""),
                "date": doc_id.get("date", {}).get("$", "")
            }
        
        # Application reference
        app_ref = biblio_data.get("application-reference", {})
        if app_ref:
            doc_id = app_ref.get("document-id", {})
            if isinstance(doc_id, list):
                doc_id = doc_id[0]
            
            result["application"] = {
                "country": doc_id.get("country", {}).get("$", ""),
                "number": doc_id.get("doc-number", {}).get("$", ""),
                "date": doc_id.get("date", {}).get("$", "")
            }
        
        # Title
        invention_title = biblio_data.get("invention-title", [])
        if invention_title:
            if isinstance(invention_title, list):
                # Get English title if available
                for title in invention_title:
                    if title.get("@lang") == "en":
                        result["title"] = title.get("$", "")
                        break
                if not result["title"] and invention_title:
                    result["title"] = invention_title[0].get("$", "")
            else:
                result["title"] = invention_title.get("$", "")
        
        # Parties (inventors and applicants)
        parties = biblio_data.get("parties", {})
        
        # Inventors
        inventors = parties.get("inventors", {}).get("inventor", [])
        if not isinstance(inventors, list):
            inventors = [inventors]
        for inv in inventors:
            inventor_name = inv.get("inventor-name", {})
            if isinstance(inventor_name, list):
                inventor_name = inventor_name[0]
            name = inventor_name.get("name", {}).get("$", "")
            if name:
                result["inventors"].append(name)
        
        # Applicants
        applicants = parties.get("applicants", {}).get("applicant", [])
        if not isinstance(applicants, list):
            applicants = [applicants]
        for app in applicants:
            applicant_name = app.get("applicant-name", {})
            if isinstance(applicant_name, list):
                applicant_name = applicant_name[0]
            name = applicant_name.get("name", {}).get("$", "")
            if name:
                result["applicants"].append(name)
        
        # Classifications
        classifications = biblio_data.get("classifications-ipcr", {}).get("classification-ipcr", [])
        if not isinstance(classifications, list):
            classifications = [classifications]
        for cls in classifications:
            ipc_class = "".join([
                cls.get("section", {}).get("$", ""),
                cls.get("class", {}).get("$", ""),
                cls.get("subclass", {}).get("$", ""),
                cls.get("main-group", {}).get("$", ""),
                "/",
                cls.get("subgroup", {}).get("$", "")
            ])
            if ipc_class != "/":
                result["classifications"]["ipc"].append(ipc_class)

        # CPC (and other scheme) classifications
        patent_classifications = _as_list(
            biblio_data.get("patent-classifications", {}).get("patent-classification")
        )
        for cls in patent_classifications:
            if not isinstance(cls, dict):
                continue
            scheme = ""
            scheme_info = cls.get("classification-scheme")
            if isinstance(scheme_info, dict):
                scheme = str(scheme_info.get("@scheme", "") or "")
            if scheme and not scheme.upper().startswith("CPC"):
                continue
            text_value = cls.get("text")
            if isinstance(text_value, dict) and text_value.get("$"):
                symbol = str(text_value["$"]).strip()
            else:
                parts = [
                    cls.get(key, {}).get("$", "") if isinstance(cls.get(key), dict) else ""
                    for key in ("section", "class", "subclass", "main-group", "subgroup")
                ]
                symbol = "".join(parts[:4])
                if parts[4]:
                    symbol = f"{symbol}/{parts[4]}"
            if symbol and symbol != "/" and symbol not in result["classifications"]["cpc"]:
                result["classifications"]["cpc"].append(symbol)

        # Cited documents (search report and applicant citations)
        citations = _as_list(biblio_data.get("references-cited", {}).get("citation"))
        for citation in citations:
            if not isinstance(citation, dict):
                continue
            categories = [
                cat.get("$", "") if isinstance(cat, dict) else str(cat)
                for cat in _as_list(citation.get("category"))
            ]
            category = " ".join(part for part in categories if part)
            phase = str(citation.get("@cited-phase", "") or "")
            if phase.lower() == "undefined":
                phase = ""
            patcit = citation.get("patcit")
            if isinstance(patcit, dict):
                number = ""
                for doc_id in _as_list(patcit.get("document-id")):
                    if not isinstance(doc_id, dict):
                        continue
                    if doc_id.get("@document-id-type") == "docdb":
                        number = "".join(
                            doc_id.get(key, {}).get("$", "") if isinstance(doc_id.get(key), dict) else ""
                            for key in ("country", "doc-number", "kind")
                        )
                        break
                if number:
                    result["cited_documents"].append(
                        {"number": number, "category": category, "phase": phase}
                    )
                continue
            nplcit = citation.get("nplcit")
            if isinstance(nplcit, dict):
                text_value = nplcit.get("text")
                text = text_value.get("$", "") if isinstance(text_value, dict) else str(text_value or "")
                text = " ".join(text.split()).lstrip("- ")
                if text:
                    if len(text) > 300:
                        text = text[:297] + "..."
                    result["cited_npl"].append({"text": text, "category": category})

        # Priority data
        priorities = biblio_data.get("priority-claims", {}).get("priority-claim", [])
        if not isinstance(priorities, list):
            priorities = [priorities]
        for prio in priorities:
            doc_id = prio.get("document-id", {})
            if isinstance(doc_id, list):
                doc_id = doc_id[0]
            
            priority = {
                "country": doc_id.get("country", {}).get("$", ""),
                "number": doc_id.get("doc-number", {}).get("$", ""),
                "date": doc_id.get("date", {}).get("$", "")
            }
            result["priorities"].append(priority)
        
    except Exception as e:
        result["parsing_error"] = str(e)
    
    return result


def format_biblio_for_display(biblio_data: Dict[str, Any]) -> str:
    """Format parsed bibliographic data for readable display."""
    output = []
    
    output.append("Patent Bibliographic Data")
    output.append("=" * 80)
    output.append("")
    
    # Publication info
    pub = biblio_data.get("publication", {})
    if pub:
        output.append(f"Publication Number: {pub.get('country')}{pub.get('number')}{pub.get('kind')}")
        output.append(f"Publication Date: {pub.get('date', 'N/A')}")
        output.append("")
    
    # Application info
    app = biblio_data.get("application", {})
    if app:
        output.append(f"Application Number: {app.get('country')}{app.get('number')}")
        output.append(f"Application Date: {app.get('date', 'N/A')}")
        output.append("")
    
    # Title
    if biblio_data.get("title"):
        output.append("Title:")
        output.append(f"  {biblio_data['title']}")
        output.append("")

    # Abstract
    if biblio_data.get("abstract"):
        output.append("Abstract:")
        output.append(f"  {biblio_data['abstract']}")
        output.append("")

    # Inventors
    if biblio_data.get("inventors"):
        output.append("Inventors:")
        for inv in biblio_data["inventors"]:
            output.append(f"  - {inv}")
        output.append("")
    
    # Applicants
    if biblio_data.get("applicants"):
        output.append("Applicants:")
        for app in biblio_data["applicants"]:
            output.append(f"  - {app}")
        output.append("")
    
    # Classifications
    classifications = biblio_data.get("classifications", {})
    if classifications.get("cpc"):
        output.append("CPC Classifications:")
        for cpc in classifications["cpc"]:
            output.append(f"  - {cpc}")
        output.append("")
    if classifications.get("ipc"):
        output.append("IPC Classifications:")
        for ipc in classifications["ipc"]:
            output.append(f"  - {ipc}")
        output.append("")

    # Priorities
    if biblio_data.get("priorities"):
        output.append("Priority Claims:")
        for prio in biblio_data["priorities"]:
            output.append(f"  - {prio.get('country')}{prio.get('number')} ({prio.get('date', 'N/A')})")
        output.append("")

    # Cited documents (from search report or applicant)
    if biblio_data.get("cited_documents"):
        output.append("Cited Documents:")
        for cited in biblio_data["cited_documents"]:
            marker = f" [{cited['category']}]" if cited.get("category") else ""
            phase = f" ({cited['phase']})" if cited.get("phase") else ""
            title = f" — {cited['title']}" if cited.get("title") else ""
            output.append(f"  - {cited['number']}{marker}{phase}{title}")
            if cited.get("abstract_hint"):
                output.append(f"    Abstract hint: {cited['abstract_hint']}")
        if any(cited.get("category") for cited in biblio_data["cited_documents"]):
            output.append(
                "  Categories: X = particularly relevant alone; Y = relevant combined; "
                "A = background. X/Y citations are strong prior-art leads."
            )
        output.append("")

    # Cited non-patent literature
    if biblio_data.get("cited_npl"):
        output.append("Cited Non-Patent Literature:")
        for npl in biblio_data["cited_npl"]:
            marker = f" [{npl['category']}]" if npl.get("category") else ""
            output.append(f"  - {npl['text']}{marker}")
        output.append("")

    # Family
    if biblio_data.get("family_id"):
        output.append(f"INPADOC Family ID: {biblio_data['family_id']}")
        output.append("")

    if biblio_data.get("parsing_error"):
        output.append(f"\nNote: Parsing error occurred: {biblio_data['parsing_error']}")
    
    return "\n".join(output)
