# Espacenet MCP Server - Patent Attorney Guide

This guide provides practical examples for using the Espacenet MCP server in patent prosecution workflows.

## Common Use Cases

### 1. Responding to Office Actions

When you receive an office action citing prior art:

**Scenario**: EPO examiner cites EP3123456A1 in Art. 54(3) EPC

**Query to Claude**:
```
Get the claims and filing date for EP3123456A1
```

**What you get**:
- All claims in readable format
- Filing date and priority dates
- Publication date
- Applicant information

**Next step**:
```
Compare our independent claim 1 with claim 1 of EP3123456A1 and identify distinguishing features
```

### 2. Novelty and Inventive Step Analysis

**Query**:
```
Get the complete patent data for US2020123456A1
```

**What you get**:
- Bibliographic information
- Full description
- All claims
- Drawing information

**Analysis queries**:
```
Extract all technical features from claim 1 of US2020123456A1
```

```
Identify which embodiments in the description of US2020123456A1 
relate to [specific technical feature]
```

```
What problem does US2020123456A1 aim to solve according to the description?
```

### 3. Freedom to Operate Analysis

For multiple citations:

```
Get bibliographic data for the following patents:
- EP1234567A1
- US2019123456A1  
- WO2018/123456A1
```

Then:
```
For each patent, extract the independent claims and identify:
1. Technical field
2. Essential features
3. Optional features
```

### 4. Claim Drafting Support

**Query**:
```
Get claims for EP3123456A1 and US10123456B2
```

**Follow-up**:
```
Identify common claim language patterns in these patents for [technical field]
```

```
What dependencies and claim structure do these patents use?
```

### 5. Prior Art Searching Context

When evaluating search results:

```
For patents WO2020/111111 and WO2020/222222:
- Get publication dates
- Get priority dates
- Get IPC classifications
- Extract main technical concepts from claims
```

This helps you quickly determine:
- Whether they're prior art to your application
- Technical overlap
- Classification alignment

### 6. Patent Family Analysis

**Query**:
```
Get bibliographic data for EP3123456A1
```

From the response, you'll see priority claims. Then:

```
Get bibliographic data for US16123456A1 [if listed as priority or family member]
```

**Compare**:
```
How do the claims of EP3123456A1 differ from those in the US equivalent?
```

### 7. Multiple Language Patents

For PCT or EP patents with multiple languages:

```
Get claims for EP3123456A1
```

The server retrieves data in available languages. You can then:

```
Are there any differences in claim scope between the English and French versions?
```

### 8. Amendment Support

When drafting amendments based on cited art:

```
Get description sections for EP3123456A1 that relate to [specific feature]
```

```
Extract all disclosure in EP3123456A1 related to [technical aspect]
```

This helps identify:
- What's disclosed in prior art
- What's missing (basis for inventive step arguments)
- Language to avoid in amendments

## Workflow Examples

### Complete Prior Art Review Workflow

```
# Step 1: Get overview
Get bibliographic data and claims for EP3123456A1

# Step 2: Analyze technical content
Extract all technical features from claims 1-5 of EP3123456A1

# Step 3: Review specific sections
Show the sections in the description of EP3123456A1 that discuss [technical aspect]

# Step 4: Compare with your application
[Paste relevant parts of your claims]
Compare these claims with the claims of EP3123456A1 and identify:
1. Common features
2. Distinguishing features  
3. Potential amendments to overcome this citation
```

### Office Action Response Workflow

```
# Step 1: Gather cited art
Get claims for:
- D1: EP3111111A1
- D2: US2019222222A1
- D3: WO2018/333333A1

# Step 2: Analyze each document
For each document, identify:
- Filing date (for Art. 54(3) EPC analysis)
- Technical problem addressed
- Main technical teaching

# Step 3: Distinguish
Compare our claim 1 with:
- Claim 1 of D1
- Claim 3 of D2
- The embodiment in paragraph [0023] of D3

Identify what features are missing in the prior art.

# Step 4: Support amendments
Search the description of D1 for any disclosure of [feature X]
```

## Tips for Effective Use

### 1. Publication Number Formats

The server accepts many formats:
- `EP3123456A1` - standard format
- `EP 3123456 A1` - with spaces (common in office actions)
- `WO2020/123456` - with slash (PCT format)
- `US2020/0123456` - US format with slash

You can copy publication numbers directly from office actions.

### 2. Batching Requests

For multiple patents, request them in one query:

```
Get bibliographic data for EP3123456A1, US2019123456A1, and WO2018/123456A1
```

This is faster than separate requests.

### 3. Combining Data Types

Request specific combinations:

```
For EP3123456A1, I need:
1. Filing and priority dates
2. Independent claims only
3. Any drawings related to [specific feature]
```

### 4. Language Considerations

- Claims and descriptions are provided in the language published by EPO
- For EP patents, this is usually English, French, or German
- For PCT (WO) publications, it's the filing language
- You can ask Claude to translate specific sections if needed

### 5. Dealing with Large Documents

For very long descriptions:

```
Get only the summary and main embodiments from the description of EP3123456A1
```

Or:

```
Search the description of EP3123456A1 for paragraphs mentioning [technical term]
```

### 6. Claim Analysis

```
For EP3123456A1:
- Number the features in claim 1
- Identify which features are functional limitations
- Identify which features are structural
```

### 7. Combining with Your Own Documents

Upload your draft claims or application, then:

```
Compare my claim 1 [from uploaded file] with claim 1 of EP3123456A1
```

```
Identify any language in EP3123456A1 that I should avoid in my claims
```

## Best Practices

### For Novelty Analysis
1. Get bibliographic data first (check dates)
2. Then get claims
3. Feature-by-feature comparison
4. Check description for implicit disclosure

### For Inventive Step Arguments
1. Get description to understand technical problem
2. Extract explicit teaching
3. Identify missing combinations
4. Look for technical prejudice statements

### For Amendments
1. Get claims to see what's actually claimed
2. Get relevant description sections for support
3. Verify exact language used
4. Check consistency across documents

### For Multiple Citations
1. Create a comparison table
2. Request key data for each
3. Identify which document is closest
4. Focus detailed analysis on closest prior art

## Data Limitations

Be aware of these limitations:

1. **Publication lag**: Very recent applications may not yet be available
2. **Withdrawn applications**: Some applications are withdrawn before publication
3. **National phase entries**: PCT national phase entries may not be linked
4. **Corrected versions**: The server retrieves the published version, not corrections
5. **Legal status**: The bibliographic data doesn't include current legal status

For legal status information, use:
- EPO Register (for EP patents)
- INPADOC (for international status)
- National patent offices (for specific countries)

## Troubleshooting Common Issues

### "Patent not found"
- Verify the publication number is correct
- Check if the patent is actually published
- Try searching on Espacenet.com first
- Some very old patents may not be in the database

### Incomplete data
- Some fields may be empty if not available
- Very old patents may have limited data
- Some national offices provide limited information

### Language issues
- EPO provides data in the published language
- Ask Claude to translate if needed
- Be aware of potential translation nuances

### Large XML responses
- For very long descriptions, ask for specific sections
- Use targeted queries rather than requesting everything
- Consider the full_patent_data tool carefully (may be very large)

## Advanced Techniques

### Creating Custom Reports

```
For patents EP3111111A1, EP3222222A1, and EP3333333A1:

Create a comparison table showing:
- Publication number
- Filing date
- Applicant
- Independent claim count
- Technical field (from IPC)
- Key features in independent claims
```

### Tracking Patent Families

```
Get priority information for EP3123456A1

Then get bibliographic data for all family members sharing the same priority
```

### Technical Feature Extraction

```
From the claims of EP3123456A1, extract:
1. All numerical ranges
2. All material specifications
3. All process parameters
4. All functional requirements
```

### Combining Multiple Sources

```
Get claims from:
- The cited patent EP3123456A1
- Our pending application [reference]
- The closest prior art from search [reference]

Create a three-way feature comparison
```

## Integration with Other Tools

This MCP server works well with:
- **Claude's analysis capabilities**: For technical comparisons
- **Your uploaded documents**: For claim comparison
- **Web search**: For finding additional information
- **Other MCP servers**: For complementary data sources

## Privacy and Confidentiality

Remember:
- EPO OPS provides only published patent data
- Your queries to retrieve published patents are not confidential
- Don't paste confidential application text in public queries
- Consider using Claude in projects for client work
- Published patents are public information

## Questions and Support

For issues with:
- This MCP server: Check the main README
- EPO OPS API: ops-support@epo.org
- Patent law questions: Consult appropriate legal resources
- Claude usage: Claude documentation

## Version Notes

This guide is for version 1.0.0 of the Espacenet MCP server.
