# Espacenet MCP Server - Quick Start

Get up and running in 10 minutes!

## Prerequisites Checklist

- [ ] Python 3.10 or higher installed
- [ ] EPO OPS account (free) - [Register here](https://developers.epo.org/user/register)
- [ ] Claude Desktop installed

## Step 1: Get EPO OPS Credentials (5 minutes)

1. Go to https://developers.epo.org/user/register
2. Fill out the registration form (choose "Non-paying" access)
3. Verify your email
4. Log in to https://developers.epo.org/
5. Click "My Apps" in the top right
6. Click "Add a new App"
7. Enter app name (e.g., "Patent Research")
8. **Copy your Consumer Key and Consumer Secret** - you'll need these!

## Step 2: Set Up the MCP Server (3 minutes)

Open Terminal and run these commands:

```bash
# Navigate to where you saved the espacenet-mcp files
cd ~/Downloads/espacenet-mcp
# (adjust path if you saved it elsewhere)

# Create virtual environment with your Python
python3 -m venv .venv

# Activate it
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set up credentials
cp .env.example .env
nano .env  # or use your preferred text editor
```

In the `.env` file, replace:
```
OPS_CONSUMER_KEY=your_consumer_key_here
OPS_CONSUMER_SECRET=your_consumer_secret_here
```

With your actual credentials from Step 1. Save and exit (Ctrl+O, Enter, Ctrl+X in nano).

## Step 3: Configure Claude Desktop (2 minutes)

**Important:** The config file doesn't exist yet - you need to create it!

```bash
# Create the config file
touch ~/Library/Application\ Support/Claude/claude_desktop_config.json

# Get the full path to your espacenet-mcp directory
cd ~/Downloads/espacenet-mcp  # or wherever you saved it
pwd  # Copy this output!
```

Now edit the config file:

```bash
nano ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

Paste this configuration (replace `/FULL/PATH/TO/` with the output from `pwd` above):

```json
{
  "mcpServers": {
    "espacenet": {
      "command": "/FULL/PATH/TO/espacenet-mcp/.venv/bin/python",
      "args": [
        "/FULL/PATH/TO/espacenet-mcp/server.py"
      ]
    }
  }
}
```

**Example:** If `pwd` showed `/Users/yourname/Downloads/espacenet-mcp`, then use:
```json
{
  "mcpServers": {
    "espacenet": {
      "command": "/Users/yourname/Downloads/espacenet-mcp/.venv/bin/python",
      "args": [
        "/Users/yourname/Downloads/espacenet-mcp/server.py"
      ]
    }
  }
}
```

Save with Ctrl+O, Enter, Ctrl+X.

**Note:** Credentials will be read from your `.env` file automatically.

## Step 4: Restart Claude Desktop

**Completely quit** Claude Desktop (Claude menu → Quit) and reopen it.

## Step 5: Test It!

In Claude Desktop, try:

```
Get the claims for patent EP1000000A1
```

If it works, you should see the patent claims displayed!

## Quick Test Queries

Try these to verify everything works:

1. **Get bibliographic data**:
   ```
   Get bibliographic data for EP3123456A1
   ```

2. **Get claims**:
   ```
   Show me the claims of US10123456B2
   ```

3. **Get description**:
   ```
   Get the description of WO2020/123456A1
   ```

## Troubleshooting

### "Server disconnected" error

**Check the logs:**
```bash
tail -50 ~/Library/Logs/Claude/mcp-server-espacenet.log
```

**Common causes:**

1. **Dependencies not installed:**
   ```bash
   cd /path/to/espacenet-mcp
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Wrong path in config:**
   - Must be absolute path (no `~`)
   - Must include full path to both python AND server.py
   - Get path with `pwd` command

3. **Old/broken venv:**
   ```bash
   cd /path/to/espacenet-mcp
   rm -rf .venv
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

4. **Wrong Python version:**
   ```bash
   python3 --version  # Must be 3.10 or higher
   ```

### Python version too old

If you have Python < 3.10, install newer version:

```bash
# Mac with Homebrew
brew install python@3.12

# Then use it to create venv
python3.12 -m venv .venv
```

### "externally-managed-environment" error

Don't use `pip install` globally! Always use a virtual environment (`.venv`).

## Success!

Once configured, just use Claude Desktop normally and reference patents by their publication numbers.

Examples:
- "Get the filing date of EP3123456A1"
- "Show me claim 1 of US2020123456A1"  
- "Compare our claim with the claims of WO2020/123456"

For more usage examples, see [ATTORNEY_GUIDE.md](ATTORNEY_GUIDE.md)
