# OpenClaw (Clawdbot) Setup and Integration Guide

## What It Is

**OpenClaw** is an open-source, self-hosted AI assistant framework created by Peter Steinberger ([@steipete](https://github.com/steipete)), founder of PSPDFKit. It runs on your own hardware (Mac, Linux, VPS) and bridges LLMs like Claude, GPT, and local models to messaging platforms including Telegram, WhatsApp, Discord, Slack, Signal, and iMessage.

- **Repository**: [github.com/openclaw/openclaw](https://github.com/openclaw/openclaw)
- **Docs**: [docs.openclaw.ai](https://docs.openclaw.ai)
- **License**: MIT (fully open source, free software)

### Naming History

The project has gone through three names:
1. **Clawdbot** (Nov 2025) -- original name by Steinberger
2. **Moltbot** (Jan 27, 2026) -- renamed after Anthropic trademark complaint
3. **OpenClaw** (Jan 30, 2026) -- current name

Legacy URLs like `molt.bot` and `docs.clawd.bot` may still work but redirect to `openclaw.ai`.

### Why OpenClaw for Our System

OpenClaw serves three roles in the money_thoughts + money_moves architecture:

1. **Telegram Interface** -- Receives signals from money_moves, delivers them to the user via Telegram, and routes approvals back.
2. **Glue Layer** -- Bridges money_thoughts and money_moves with scheduled cron jobs (e.g., daily `/pulse`, weekly `/review`).
3. **Development Tool** -- Always-on agent on a VPS that can run Claude Code sessions against both codebases remotely.

---

## System Requirements

| Requirement | Minimum | Recommended |
|-------------|---------|-------------|
| **CPU** | 1 vCPU | 2 vCPU |
| **RAM** | 1 GB | 2 GB |
| **Disk** | 5 GB | 20 GB |
| **Node.js** | 22+ | 22 LTS |
| **OS** | Linux, macOS, Windows (WSL2) | Ubuntu 22.04+ or Debian 12+ |

OpenClaw itself is lightweight -- it is a gateway/router, not an LLM host. The actual model inference happens via API calls to Anthropic, OpenAI, etc.

### Recommended VPS Providers

| Provider | Plan | Cost/month | Notes |
|----------|------|------------|-------|
| **Hetzner** | CX22 (2 vCPU, 4GB) | ~$5 | Best value, EU/US datacenters |
| **DigitalOcean** | Basic Droplet | $6-12 | Hardened OpenClaw image available |
| **Oracle Cloud** | Free Tier ARM | $0 | Free forever tier, 4 vCPU/24GB |
| **Hostinger** | KVM 2 | ~$6 | OpenClaw one-click setup available |

---

## Installation

### Method 1: Installer Script (Recommended)

```bash
# macOS / Linux / WSL2
curl -fsSL https://openclaw.ai/install.sh | bash
```

This handles Node.js detection, installation, and launches the onboarding wizard.

### Method 2: npm Global Install

```bash
# Requires Node 22+
npm install -g openclaw@latest
openclaw onboard --install-daemon
```

### Method 3: From Source (for contributors)

```bash
git clone https://github.com/openclaw/openclaw.git
cd openclaw
pnpm install
pnpm ui:build
pnpm build
pnpm link --global
openclaw onboard --install-daemon
```

### Post-Installation

```bash
openclaw doctor      # Verify configuration
openclaw status      # Check gateway status
openclaw dashboard   # Launch web UI
```

If `openclaw` is not found after install, add npm's global bin to your PATH:
```bash
export PATH="$(npm prefix -g)/bin:$PATH"
```

### VPS Deployment with Docker

For production VPS deployments, Docker is recommended:

```bash
# See Simon Willison's guide: https://til.simonwillison.net/llms/openclaw-docker
docker pull openclaw/openclaw:latest
docker run -d \
  --name openclaw \
  --restart unless-stopped \
  -v ~/.openclaw:/root/.openclaw \
  -p 18789:18789 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -e TELEGRAM_BOT_TOKEN=123456789:ABC... \
  openclaw/openclaw:latest
```

---

## Telegram Bot Setup

### Step 1: Create Bot via BotFather

1. Open Telegram, search for `@BotFather`
2. Send `/newbot`
3. Choose a name (e.g., "Money Moves Bot")
4. Choose a username (e.g., `money_moves_bot`)
5. Copy the bot token (format: `123456789:ABCdefGHIjklMNOpqrsTUVwxyz`)

### Step 2: Get Your Telegram User ID

1. Search for `@userinfobot` on Telegram
2. Send `/start` -- it will reply with your numeric user ID
3. Save this ID for the allowlist configuration

### Step 3: Configure OpenClaw

The configuration file lives at `~/.openclaw/config/channels.json` (or `.json5`).

```json5
{
  channels: {
    telegram: {
      enabled: true,
      botToken: "${TELEGRAM_BOT_TOKEN}",  // or hardcode the token
      dmPolicy: "allowlist",               // only allow your user ID
      allowFrom: ["tg:YOUR_USER_ID"],
      historyLimit: 50,
      linkPreview: true,
      mediaMaxMb: 5,
      replyToMode: "first",
      customCommands: [
        { command: "pulse", description: "Run portfolio pulse scan" },
        { command: "review", description: "Weekly portfolio review" },
        { command: "approve", description: "Approve a pending signal" },
        { command: "reject", description: "Reject a pending signal" }
      ]
    }
  }
}
```

### Step 4: Pairing

If using `dmPolicy: "pairing"` (the default), the first time you message the bot it will show a pairing code. Approve it on the server:

```bash
openclaw pairing approve telegram <the-code>
```

For `dmPolicy: "allowlist"`, no pairing is needed -- only listed user IDs can interact.

### Step 5: Verify

Send a message to your bot on Telegram. OpenClaw should respond.

---

## Integration Architecture

### Signal Flow

```
money_moves (signals)
     |
     v
OpenClaw Gateway (VPS)
     |
     v
Telegram Bot --> User's Phone
     |
     v
User taps Approve / Reject
     |
     v
Telegram Bot --> OpenClaw Gateway
     |
     v
money_moves (execute or discard)
```

### Scheduled Automation

```
OpenClaw Cron (VPS)
     |
     +-- Daily 7am ----> money_thoughts: /pulse
     +-- Daily 7am ----> money_thoughts: /refresh
     +-- Weekly Mon ----> money_thoughts: /review
     +-- On signal -----> Telegram: "BUY AAPL signal. Approve?"
```

### Cron Job Configuration

OpenClaw has a built-in scheduler. Jobs are stored in `~/.openclaw/cron/jobs.json`.

**Schedule types:**
- `at` -- one-time (ISO 8601 timestamp)
- `every` -- fixed interval (milliseconds)
- `cron` -- recurring (5-field cron expression + optional timezone)

**Example: Daily morning pulse**

```bash
openclaw cron add \
  --name "Morning Pulse" \
  --cron "0 7 * * *" \
  --tz "America/Los_Angeles" \
  --session isolated \
  --message "Run /pulse on the money_thoughts workspace. Summarize any alerts." \
  --announce \
  --channel telegram \
  --to "tg:YOUR_USER_ID"
```

**Example: Weekly review**

```bash
openclaw cron add \
  --name "Weekly Review" \
  --cron "0 9 * * 1" \
  --tz "America/Los_Angeles" \
  --session isolated \
  --message "Run /review on the money_thoughts workspace. Post the summary." \
  --announce \
  --channel telegram \
  --to "tg:YOUR_USER_ID"
```

**Example: One-shot reminder**

```bash
openclaw cron add \
  --name "META Window Check" \
  --at "2026-02-15T09:00:00-08:00" \
  --session main \
  --system-event "Check if META trading window is open" \
  --wake now \
  --delete-after-run
```

**Manage cron jobs:**

```bash
openclaw cron list              # List all jobs
openclaw cron run <jobId>       # Force-run a job now
openclaw cron runs --id <jobId> # View run history
openclaw cron remove <jobId>    # Delete a job
```

### Agent & Workspace Configuration

Configure OpenClaw to work with both codebases:

```json5
{
  agents: {
    defaults: {
      workspace: "~/workspace/money_thoughts",
      model: {
        primary: "anthropic/claude-opus-4-6",
        fallbacks: ["anthropic/claude-sonnet-4-5-20250929"]
      },
      timeoutSeconds: 600,
      maxConcurrent: 2
    }
  }
}
```

The SOUL.md file (OpenClaw's equivalent of CLAUDE.md) in the workspace root defines the agent's personality and boundaries. Since money_thoughts already has a CLAUDE.md, OpenClaw will pick it up as workspace context.

### Multi-Workspace Setup

To manage both money_thoughts and money_moves from one OpenClaw instance, you can configure separate agents or use the workspace switching capabilities. Each cron job can target a specific workspace via the `--session` and `--message` flags.

---

## Development Use

OpenClaw running on a VPS gives you an always-on Claude Code instance accessible from anywhere.

### Remote Development via Telegram

1. Open Telegram on your phone
2. Message your bot: "In the money_moves repo, add error handling to the signal router"
3. OpenClaw runs Claude Code against the workspace
4. You receive the diff/summary in Telegram
5. Review and approve from your phone

### Capabilities

- Full file system access within the workspace
- Git operations (commit, push, pull)
- Run tests and builds
- Install dependencies
- Create PRs via GitHub CLI

### Practical Patterns

- **Quick fixes while commuting**: Message the bot to fix a bug, review the diff later
- **Code review from phone**: "Review the latest PR on money_moves and summarize changes"
- **Deploy triggers**: "Run the deploy script for money_moves"
- **Status checks**: "What's the git status of money_thoughts? Any uncommitted changes?"

---

## Security

### Critical: Access Control

OpenClaw has broad system access. Security is paramount.

**Minimum security requirements:**

1. **Restrict Telegram access** -- Use `dmPolicy: "allowlist"` with your user ID only
2. **Store secrets in environment variables** -- Never hardcode API keys in config files
3. **Use a dedicated OS user** -- Don't run OpenClaw as root
4. **File permissions** -- 700 on directories, 600 on files
5. **Firewall** -- Only expose port 18789 if you need the web dashboard; otherwise block it

### Environment Variables

Store all secrets in `~/.openclaw/.env` or system environment:

```bash
ANTHROPIC_API_KEY=sk-ant-...
TELEGRAM_BOT_TOKEN=123456789:ABC...
GITHUB_TOKEN=ghp_...
```

OpenClaw resolves `${VAR_NAME}` in config files from these sources. Missing variables throw errors at startup (fail-safe).

### Elevated Permissions

By default, dangerous operations require explicit allowlisting:

```json5
{
  tools: {
    elevated: {
      enabled: true,
      allowFrom: {
        telegram: ["tg:YOUR_USER_ID"]
      }
    }
  }
}
```

Only grant elevated permissions to your own user ID.

### Known Risks

Security researchers have identified real-world issues with OpenClaw deployments:

- **Exposed instances**: A Shodan scan found tens of thousands of OpenClaw instances on the public internet, most with weak authentication. Always firewall your VPS.
- **Shared context leaks**: In group chats, secrets loaded for one user can be visible to others. Use DM-only mode for sensitive operations.
- **Supply chain risks**: Third-party skills/plugins can execute arbitrary code. Only install trusted skills from the official registry.
- **Prompt injection**: Malicious content in processed files could manipulate the agent. Be cautious with automated file processing.

### Hardening Checklist

- [ ] `dmPolicy` set to `"allowlist"` (not `"open"`)
- [ ] `allowFrom` contains only your Telegram user ID
- [ ] API keys stored in environment variables, not config files
- [ ] OpenClaw runs as non-root dedicated user
- [ ] VPS firewall blocks all ports except SSH (and 18789 if needed)
- [ ] Full-disk encryption enabled on VPS
- [ ] Regular `openclaw doctor` runs to check configuration
- [ ] Only official/trusted skills installed
- [ ] Docker deployment with container isolation (if applicable)

---

## Costs

### Software

OpenClaw itself is free (MIT license).

### VPS Hosting

| Tier | Cost/month | Notes |
|------|------------|-------|
| Free | $0 | Oracle Cloud Free Tier, or run on existing Mac |
| Budget | $5-6 | Hetzner CX22, sufficient for most use cases |
| Standard | $6-12 | DigitalOcean, Hostinger with managed images |

### Claude API Usage

If using Anthropic API directly:

| Model | Input | Output |
|-------|-------|--------|
| Claude Sonnet 4.5 | $3/M tokens | $15/M tokens |
| Claude Opus 4.6 | $15/M tokens | $75/M tokens |

**Estimated monthly costs by usage pattern:**

| Pattern | Model | Est. Cost |
|---------|-------|-----------|
| Light (daily pulse + occasional chat) | Sonnet | $10-30/month |
| Moderate (daily pulse + development) | Sonnet | $30-80/month |
| Heavy (continuous development) | Opus | $100-300/month |

### Using Existing Subscriptions

If you already pay for Claude Pro ($20/month) or Claude Max ($100/month), OpenClaw can reuse Claude Code CLI OAuth credentials. This means **no additional API costs** for operations that go through Claude Code. This is the most cost-effective approach for our use case.

### Total Estimated Cost

For our setup (VPS + existing Claude subscription):
- **VPS**: $5-6/month (Hetzner)
- **API**: $0 (using existing Claude subscription via OAuth)
- **Total**: ~$5-6/month

---

## Alternatives

If OpenClaw does not fit (too complex, security concerns, or feature gaps), here are alternatives:

### 1. claude-code-telegram (Lightweight)

A standalone Telegram bot that wraps Claude Code for remote development access.

- **Repo**: [github.com/RichardAtCT/claude-code-telegram](https://github.com/RichardAtCT/claude-code-telegram)
- **Pros**: Simpler, purpose-built for Claude Code + Telegram, session persistence
- **Cons**: No built-in cron/scheduler, fewer integrations, less mature
- **Best for**: If you only need Telegram + Claude Code and will handle scheduling separately

### 2. Claude-Code-Remote

Control Claude Code remotely via email, Discord, or Telegram.

- **Repo**: [github.com/JessyTsui/Claude-Code-Remote](https://github.com/JessyTsui/Claude-Code-Remote)
- **Pros**: Multi-channel (email, Discord, Telegram), notification-driven
- **Cons**: Less feature-rich than OpenClaw, no cron

### 3. Custom Telegram Bot + Claude API

Build a minimal bot tailored to the money_moves signal flow.

```
Python (python-telegram-bot) + Anthropic SDK
  - Receives signals from money_moves via webhook/file watch
  - Sends approval buttons to Telegram
  - Routes approvals back to money_moves
  - Cron via systemd timers or crontab
```

- **Pros**: Full control, minimal attack surface, no external dependencies
- **Cons**: Must build and maintain everything, no web dashboard
- **Best for**: Maximum security, minimal footprint

### 4. n8n (No-Code Automation)

Self-hosted workflow automation with Telegram and AI nodes.

- **Pros**: Visual workflow builder, 400+ integrations, self-hosted
- **Cons**: Heavier resource usage, different paradigm than code-first
- **Best for**: Non-developers or rapid prototyping of complex flows

### Recommendation

**Start with OpenClaw.** It is the most complete solution for our three requirements (Telegram interface, scheduled automation, remote development). The cron system alone justifies it over simpler alternatives. If security concerns arise or the setup proves too heavy, fall back to `claude-code-telegram` for development access + a custom Python bot for the signal approval flow.

---

## Quick Start for Our System

### 1. Provision VPS

```bash
# Hetzner CX22 or equivalent
# Ubuntu 24.04 LTS
# SSH key authentication only
```

### 2. Install OpenClaw

```bash
curl -fsSL https://openclaw.ai/install.sh | bash
```

### 3. Configure Telegram

```bash
# Set environment variables
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> ~/.openclaw/.env
echo 'TELEGRAM_BOT_TOKEN=123456789:ABC...' >> ~/.openclaw/.env

# Run onboarding wizard
openclaw onboard
```

### 4. Clone Workspaces

```bash
cd ~/workspace
git clone <money_thoughts_repo>
git clone <money_moves_repo>
```

### 5. Set Up Cron Jobs

```bash
# Daily morning pulse
openclaw cron add \
  --name "Morning Pulse" \
  --cron "0 7 * * *" \
  --tz "America/Los_Angeles" \
  --session isolated \
  --message "Run /pulse. Summarize alerts and notable price moves." \
  --announce --channel telegram --to "tg:YOUR_USER_ID"

# Weekly review
openclaw cron add \
  --name "Weekly Review" \
  --cron "0 9 * * 1" \
  --tz "America/Los_Angeles" \
  --session isolated \
  --message "Run /review. Post summary of idea outcomes and portfolio changes." \
  --announce --channel telegram --to "tg:YOUR_USER_ID"

# Price refresh
openclaw cron add \
  --name "Price Refresh" \
  --cron "0 7 * * 1-5" \
  --tz "America/Los_Angeles" \
  --session isolated \
  --message "Run /refresh to update all prices in research files and watchlist." \
  --announce --channel telegram --to "tg:YOUR_USER_ID"
```

### 6. Verify

```bash
openclaw doctor
openclaw status
openclaw cron list
```

Send a test message to your Telegram bot to confirm connectivity.

---

## References

- [OpenClaw GitHub](https://github.com/openclaw/openclaw)
- [OpenClaw Docs - Install](https://docs.openclaw.ai/install)
- [OpenClaw Docs - Configuration](https://docs.openclaw.ai/gateway/configuration)
- [OpenClaw Docs - Cron Jobs](https://docs.openclaw.ai/automation/cron-jobs)
- [OpenClaw Docs - Security](https://docs.openclaw.ai/gateway/security)
- [OpenClaw Skills Registry](https://github.com/openclaw/clawhub)
- [DigitalOcean Setup Gist](https://gist.github.com/dabit3/42cce744beaa6a0d47d6a6783e443636)
- [Docker Deployment Guide (Simon Willison)](https://til.simonwillison.net/llms/openclaw-docker)
- [claude-code-telegram Alternative](https://github.com/RichardAtCT/claude-code-telegram)
