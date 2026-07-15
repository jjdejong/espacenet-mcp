# Espacenet MCP Server

MCP server for accessing patent data from Espacenet via EPO Open Patent Services (OPS) API.

Built for patent attorneys to quickly retrieve patent specifications, claims, and bibliographic data directly in Claude Desktop.

## Features

- 🔍 Retrieve patent bibliographic data (title, inventors, dates, classifications)
- 📄 Access full patent descriptions and claims
- 🖼️ Get drawing/figure information
- 🌍 Support for EP, US, WO, and other publication formats
- ⚡ Fast access during patent prosecution workflow

## Compact, recall-safe search defaults

`search_patents` returns compact publication identifiers and pagination metadata rather than the
raw OPS response. It returns 25 identifiers by default and accepts up to 100; use `start` and
`next_start` to paginate a high-signal query. Use `raw: true` only for diagnostics, then call
`get_patent_biblio` for shortlisted identifiers. The search endpoint covers bibliographic data and
title/abstract text, not claims or description full text.

U.S. pre-grant publications are emitted in conventional zero-padded form while both conventional and
OPS epodoc forms are accepted as input. OPS may return any member of a patent family; retain a pertinent
member during discovery and resolve a convenient-language equivalent during verification.

Ambiguous aliases such as `an=` and `applicant=` are rejected with guidance. Use `pa=` for applicant,
`in=` for inventor, and `get_patent_biblio` for a known publication number.

`find_text_in_patent` searches descriptions only and returns bounded excerpts (five by default), never
claims or a complete specification. If OPS lacks description text it tries Google Patents, then for US
publications the official USPTO publication PDF with local OCR (`pdftoppm` and `tesseract`).
This is the preferred screening tool for technical disclosure in prior-art work.
OPS 404 search responses are normalized to an empty result set without replaying the XML fault body.

## Quick Start

See [QUICKSTART.md](QUICKSTART.md) for complete setup instructions.

```bash
# 1. Clone and install
git clone https://github.com/jjdejong/espacenet-mcp.git
cd espacenet-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Get EPO OPS credentials (free)
# Register at: https://developers.epo.org/

# 3. Configure
cp .env.example .env
# Edit .env with your credentials

# 4. Add to Claude Desktop config
# See QUICKSTART.md for details
```

## Requirements

- Python 3.10+
- EPO OPS API credentials (free registration)
- Claude Desktop

## Documentation

- [QUICKSTART.md](QUICKSTART.md) - Setup guide
- [ATTORNEY_GUIDE.md](ATTORNEY_GUIDE.md) - Patent prosecution workflows
- [README.md](README.md) - Full documentation

## License

MIT License - see [LICENSE](LICENSE)

## Support

For issues or questions, please open an issue on GitHub.
