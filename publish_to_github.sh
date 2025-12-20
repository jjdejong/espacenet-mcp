#!/bin/bash

# Script to publish to GitHub

echo "Publishing Espacenet MCP Server to GitHub..."
echo ""

# Check if git is initialized
if [ ! -d .git ]; then
    echo "Initializing git repository..."
    git init
    echo "✓ Git initialized"
fi

# Replace README.md with GitHub version
if [ -f README_GITHUB.md ]; then
    echo "Using GitHub-optimized README..."
    mv README.md README_FULL.md
    mv README_GITHUB.md README.md
    echo "✓ README updated"
fi

# Remove WHAT_WORKED.md if it exists
if [ -f WHAT_WORKED.md ]; then
    rm WHAT_WORKED.md
    echo "✓ Removed internal docs"
fi

# Create .gitignore if it doesn't exist
if [ ! -f .gitignore ]; then
    cat > .gitignore << 'EOF'
# Environment variables
.env

# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
build/
develop-eggs/
dist/
downloads/
eggs/
.eggs/
lib/
lib64/
parts/
sdist/
var/
wheels/
*.egg-info/
.installed.cfg
*.egg

# Virtual environments
.venv/
venv/
env/
ENV/

# IDEs
.vscode/
.idea/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db

# Testing
.pytest_cache/
.coverage
htmlcov/

# Logs
*.log

# Internal docs
README_FULL.md
WHAT_WORKED.md
EOF
    echo "✓ Created .gitignore"
fi

# Stage all files
echo ""
echo "Staging files..."
git add .

# Commit
echo ""
echo "Creating commit..."
git commit -m "Initial commit: Espacenet MCP Server for patent prosecution"

# Add remote (update with your actual GitHub repo)
echo ""
echo "Adding GitHub remote..."
git remote add origin https://github.com/jjdejong/espacenet-mcp.git

echo ""
echo "✓ Repository prepared!"
echo ""
echo "Next steps:"
echo "1. Create repository on GitHub: https://github.com/new"
echo "   - Name: espacenet-mcp"
echo "   - Description: MCP server for accessing patent data from Espacenet"
echo "   - Public or Private: Your choice"
echo "   - Do NOT initialize with README"
echo ""
echo "2. Then run:"
echo "   git push -u origin main"
echo ""
echo "3. (Optional) If you get an error about 'master' vs 'main':"
echo "   git branch -M main"
echo "   git push -u origin main"
