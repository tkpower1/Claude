# Project Memory

## Building Skills for Claude - Complete Reference

Skills are instruction packages that extend Claude's capabilities for specialized tasks. They function like "an onboarding guide for a new hire."

### File Structure

```
my-skill/
├── SKILL.md           # Required - main instructions
├── reference.md       # Optional - detailed docs
├── examples.md        # Optional - usage examples
└── scripts/
    └── helper.py      # Optional - executable scripts
```

### SKILL.md Format

```yaml
---
name: skill-name              # kebab-case, max 64 chars
description: What it does and when to use it  # Under 1024 chars
argument-hint: [arg1] [arg2]  # Shown in autocomplete
disable-model-invocation: true  # Only user can invoke
user-invocable: false         # Only Claude can invoke
allowed-tools: Read, Grep     # Tools without permission
context: fork                 # Run in subagent
agent: Explore                # Subagent type
model: claude-sonnet-4-5-20250514        # Model override
---

Your instructions here...
```

### Skill Locations

| Location   | Path                                     | Scope                  |
|------------|------------------------------------------|------------------------|
| Enterprise | Managed settings                         | All org users          |
| Personal   | ~/.claude/skills/<name>/SKILL.md         | All your projects      |
| Project    | .claude/skills/<name>/SKILL.md           | This project only      |
| Plugin     | <plugin>/skills/<name>/SKILL.md          | Where plugin enabled   |

### String Substitutions

- `$ARGUMENTS` - All arguments passed
- `$ARGUMENTS[N]` or `$N` - Specific argument by index
- `${CLAUDE_SESSION_ID}` - Current session ID
- `${CLAUDE_SKILL_DIR}` - Skill directory path

### Dynamic Context Injection

Use `!`command`` to run shell commands before sending to Claude:

```yaml
---
name: pr-summary
context: fork
agent: Explore
---

## PR Data
- Diff: !`gh pr diff`
- Comments: !`gh pr view --comments`
```

### Progressive Disclosure (3 Levels)

1. **YAML frontmatter** - Always loaded, minimal context
2. **SKILL.md body** - Loaded when relevant
3. **Linked files** - Accessed on-demand via references/

### Five Common Patterns

1. **Sequential workflow orchestration** - Multi-step ordered processes
2. **Multi-MCP coordination** - Workflows spanning multiple services
3. **Iterative refinement** - Quality improvement loops
4. **Context-aware tool selection** - Conditional tool usage
5. **Domain-specific intelligence** - Embedded specialized knowledge

### Invocation Control

| Frontmatter                        | User can invoke | Claude can invoke |
|------------------------------------|-----------------|-------------------|
| (default)                          | Yes             | Yes               |
| `disable-model-invocation: true`   | Yes             | No                |
| `user-invocable: false`            | No              | Yes               |

### Running in Subagent

Add `context: fork` for isolated execution:

```yaml
---
name: deep-research
context: fork
agent: Explore
---

Research $ARGUMENTS thoroughly:
1. Find relevant files using Glob and Grep
2. Read and analyze the code
3. Summarize findings
```

Agent types: `Explore`, `Plan`, `general-purpose`, or custom agents in `.claude/agents/`

### Best Practices

- Keep SKILL.md under 500 lines
- Use kebab-case for folder names
- No README.md inside skill folders
- Avoid `<` `>` in frontmatter (reserved XML)
- Don't use "claude" or "anthropic" in skill names
- Include "what it does" AND "when to use it" in description
- Reference supporting files from SKILL.md so Claude knows when to load them

### Testing Approach

1. **Triggering tests** - Verify skill loads appropriately
2. **Functional tests** - Confirm correct outputs
3. **Performance comparison** - Demonstrate improvement vs baseline

### Security

- Only install skills from trusted sources
- Audit code dependencies and network connections
- Skills can include executable scripts - review them

### Sources

- [Official Claude Code Skills Docs](https://code.claude.com/docs/en/skills)
- [Agent Skills Open Standard](https://agentskills.io)
- [Anthropic Engineering Blog](https://claude.com/blog/equipping-agents-for-the-real-world-with-agent-skills)
- [GitHub Skills Repository](https://github.com/anthropics/skills)
- [Complete Guide PDF](https://resources.anthropic.com/hubfs/The-Complete-Guide-to-Building-Skill-for-Claude.pdf)
