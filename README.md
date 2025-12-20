# Espacenet MCP Server

MCP server for accessing patent data from Espacenet via EPO Open Patent Services (OPS) API.

Built for patent attorneys to quickly retrieve patent specifications, claims, and bibliographic data directly in Claude Desktop.

## Features

- 🔍 Retrieve patent bibliographic data (title, inventors, dates, classifications)
- 📄 Access full patent descriptions and claims
- 🖼️ Get drawing/figure information
- 🌍 Support for EP, US, WO, and other publication formats
- ⚡ Fast access during patent prosecution workflow

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
