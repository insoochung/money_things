# /thesis

Create or update an investment thesis.

## Usage

```
/thesis "AI inference shift away from NVIDIA"    # Create new thesis
/thesis T001                                      # View/update existing thesis
/thesis T001 "add AMD benchmark data"            # Update with new evidence
```

## Arguments

- `title` (for new): Thesis statement in quotes
- `id` (for existing): Thesis ID (e.g., `T001`)
- `update` (optional): Context for update

## Behavior

### Create mode (with title string)

1. Engage user in conversation to develop the thesis
2. Ask clarifying questions about core belief, timeframe, conviction
3. Check `memory/principles.md` for relevant principles to apply
4. Generate slug from title (lowercase, hyphens, no special chars)
5. Assign next sequential ID by scanning `theses/active/` and `theses/archive/`
6. Write to `theses/active/{slug}.md` using the format below
7. Prompt: "Want to /discover tickers for this thesis?"

### View mode (with ID, no update)

1. Find thesis file in `theses/active/` or `theses/archive/` matching the ID
2. Display thesis with current status
3. Show linked research and ideas
4. Check validation/failure criteria against current evidence

### Update mode (with ID + update context)

1. Find thesis file matching the ID
2. Add new evidence to thesis
3. Re-evaluate conviction level
4. Update validation/failure criteria progress
5. Update `updated` date in frontmatter

## Thesis File Format

Write to `theses/active/{slug}.md`:

```markdown
---
id: T{NNN}
title: "{thesis title}"
created: {YYYY-MM-DD}
updated: {YYYY-MM-DD}
status: active
conviction: {low|medium|high}
themes: [{theme1}, {theme2}]
ticker_universe: []
pushed_to_moves: false
---

# T{NNN}: {Thesis Title}

## Core Belief

{Thesis statement - the macro belief about the world}

## Supporting Evidence

1. {Evidence point 1}
2. {Evidence point 2}

## Validation Criteria

Conditions that would confirm this thesis:
- [ ] {Criterion 1}
- [ ] {Criterion 2}

## Failure Criteria

Conditions that would invalidate this thesis:
- [ ] {Criterion 1}
- [ ] {Criterion 2}

## Ticker Universe

Candidates aligned with this thesis:

| Ticker | Role | Status | Idea |
|--------|------|--------|------|

## Principles Applied

{Reference relevant principles from memory/principles.md}

## Linked Ideas

{Links to ideas/ files}

## Notes

{Free-form evolving notes from conversations}
```

## ID Assignment

To assign the next ID:
1. Scan all files in `theses/active/` and `theses/archive/`
2. Extract IDs from frontmatter (format: T001, T002, etc.)
3. Return next sequential ID

## Slug Generation

From title, generate slug:
- Lowercase
- Replace spaces with hyphens
- Remove special characters
- Truncate to 50 chars max
- Example: "AI inference shift away from NVIDIA" -> "ai-inference-shift-away-from-nvidia"
