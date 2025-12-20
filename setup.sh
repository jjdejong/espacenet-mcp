#!/bin/bash
# Quick setup script for Espacenet MCP Server

set -e

echo "=========================================="
echo "Espacenet MCP Server - Setup"
echo "=========================================="
echo ""

# Check if uv is installed
if command -v uv &> /dev/null; then
    echo "✓ Found uv - will use for dependency management"
    USE_UV=true
else
    echo "⚠️  uv not found - will use pip with virtual environment"
    echo "   For better experience, install uv: https://github.com/astral-sh/uv"
    USE_UV=false
    
    # Check Python version
    echo ""
    echo "Checking Python version..."
    python_version=$(python3 --version 2>&1 | awk '{print $2}')
    echo "Found Python $python_version"
    
    # Check if we meet minimum version (3.8)
    if ! python3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)"; then
        echo "ERROR: Python 3.8 or higher is required"
        exit 1
    fi
fi

# Create .env file if it doesn't exist
if [ ! -f .env ]; then
    echo ""
    echo "Creating .env file from template..."
    cp .env.example .env
    echo "✓ Created .env file"
    echo ""
    echo "⚠️  IMPORTANT: Edit .env file and add your EPO OPS credentials"
    echo "   Register at: https://developers.epo.org/"
    echo ""
else
    echo "✓ .env file already exists"
fi

# Check if OPS credentials are configured
if grep -q "your_consumer_key_here" .env 2>/dev/null; then
    echo ""
    echo "⚠️  WARNING: EPO OPS credentials not configured in .env"
    echo "   Please edit .env and add your credentials before running the server"
    echo ""
fi

# Install dependencies
if [ "$USE_UV" = true ]; then
    echo ""
    echo "Using uv - dependencies will be installed automatically when you run the server"
    echo "No manual installation needed!"
else
    echo ""
    echo "Creating virtual environment and installing dependencies..."
    
    # Create virtual environment if it doesn't exist
    if [ ! -d "venv" ]; then
        python3 -m venv venv
        echo "✓ Created virtual environment"
    fi
    
    # Activate virtual environment
    source venv/bin/activate
    
    # Install dependencies
    pip install --upgrade pip
    pip install -r requirements.txt
    
    echo "✓ Dependencies installed in virtual environment"
    
    # Deactivate
    deactivate
fi

echo ""
echo "=========================================="
echo "Setup complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Edit .env file with your EPO OPS credentials"
echo "2. Add server configuration to Claude Desktop:"
echo ""

if [ "$USE_UV" = true ]; then
    echo '   {
     "mcpServers": {
       "espacenet": {
         "command": "uv",
         "args": ["--directory", "'$(pwd)'", "run", "server.py"]
       }
     }
   }'
else
    echo '   {
     "mcpServers": {
       "espacenet": {
         "command": "'$(pwd)'/venv/bin/python",
         "args": ["server.py"]
       }
     }
   }'
fi

echo ""
echo "3. Restart Claude Desktop"
echo ""
echo "See README.md for detailed configuration instructions"
echo "See ATTORNEY_GUIDE.md for usage examples"
echo ""
