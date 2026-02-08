# /push

Push a validated thesis with its ticker universe to money_moves for autonomous execution.

## Usage

```
/push T001
/push T001 --dry-run    # Preview what would be sent
```

## Arguments

- `thesis_id` (required): Thesis ID to push (e.g., `T001`)
- `--dry-run` (optional): Preview payload without sending

## Behavior

1. **Load thesis** from `theses/active/` matching the provided ID
2. **Validate thesis is ready:**
   - Has at least one ticker in `ticker_universe`
   - Has validation and failure criteria defined
   - Has conviction level set
   - Has at least one linked idea with entry conditions
3. **Gather push payload:**
   - Thesis statement and evidence
   - Ticker universe with research summaries (read from `research/{symbol}.md`)
   - Linked ideas with entry/exit conditions (read from `ideas/`)
   - Validation/failure criteria
   - Position sizing guidance
   - Relevant principles from `memory/principles.md`
4. **If `--dry-run`:** Display the JSON payload and exit
5. **Write payload** to `../moves/inbound/` directory as JSON file
6. **Update thesis:** Set `pushed_to_moves: true` in frontmatter
7. **Log push event** in thesis Notes section

## Payload Format

Write to `../moves/inbound/{thesis_id}-{timestamp}.json`:

```json
{
  "thesis_id": "T001",
  "title": "AI inference shift away from NVIDIA",
  "conviction": "high",
  "thesis_statement": "...",
  "validation_criteria": ["..."],
  "failure_criteria": ["..."],
  "tickers": [
    {
      "symbol": "AMD",
      "action": "buy",
      "entry_condition": "Price <= $150",
      "target_price": 200,
      "stop_price": 120,
      "position_size_pct": 5.0,
      "research_summary": "..."
    }
  ],
  "principles": ["P3: Domain expertise creates durable edge"],
  "pushed_at": "2026-02-07T15:30:00Z"
}
```

## Validation Errors

If the thesis is not ready, report which criteria are missing:
- "No tickers in universe" -- need to run /discover first
- "No validation criteria defined" -- need to add validation criteria
- "No failure criteria defined" -- need to add failure criteria
- "No linked ideas with entry conditions" -- need to create ideas with /idea
- "Conviction not set" -- need to set conviction level

## Files Touched

- Reads: `theses/active/{slug}.md`, `research/*.md`, `ideas/*.md`, `memory/principles.md`
- Writes: `../moves/inbound/{thesis_id}-{timestamp}.json`, updates thesis frontmatter
