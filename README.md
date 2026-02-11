# Money System

Two-module AI-driven investment system: **money_thoughts** (thesis development) + **money_moves** (autonomous execution).

## How It Connects

```mermaid
graph TB
    subgraph interfaces ["User Interfaces"]
        CLI["Claude Code CLI<br>(terminal sessions)"]
        TG["Telegram<br>(mobile, always-on)"]
        WEB["Web Dashboard<br>(munnythoughts.com)"]
    end

    subgraph openclaw ["OpenClaw (VPS)"]
        OC["Bot Bridge<br>routes between Telegram<br>and both modules"]
    end

    subgraph thoughts ["money_thoughts — Thinking"]
        CMDS["/think /note /journal<br>/brief /trade"]
        AGENT["Research Sub-Agent<br>Researcher · Analyst · Critic"]
        MEM["Memory<br>principles · portfolio"]
    end

    subgraph moves ["money_moves — Executing"]
        ENGINE["Engine<br>Thesis · Signal · Risk<br>Pricing · Scheduler"]
        DASH["Dashboard<br>15 sections, real-time"]
        TGBOT["Signal Bot<br>approve / reject"]
        BROKER["Broker<br>Mock · Schwab"]
        DB[(SQLite<br>23 tables)]
    end

    CLI -->|direct| CMDS
    TG <--> OC
    OC <-->|"/think, /brief"| CMDS
    OC <-->|"approve/reject"| TGBOT
    WEB --> DASH
    DASH <--> ENGINE
    TGBOT <--> ENGINE
    ENGINE <--> DB
    ENGINE --> BROKER
    CMDS --> AGENT
    AGENT --> MEM
    MEM -->|context| CMDS
    CMDS -->|push thesis| ENGINE
    ENGINE -.->|results + outcomes| CMDS
```

### Which interface does what?

| Interface         | Reaches                   | What you do there                                                      |
| ----------------- | ------------------------- | ---------------------------------------------------------------------- |
| **Telegram**      | Both modules via OpenClaw | Research (`/think`), daily briefing (`/brief`), approve/reject signals |
| **Web Dashboard** | money_moves               | View portfolio, positions, theses, performance, risk — read-only       |
| **CLI**           | money_thoughts directly   | Development sessions, deep research, debugging                         |

### Data flow

1. **Think** — Develop thesis conversationally via `/think` (Telegram or CLI)
2. **Push** — Validated thesis pushed to money_moves via API
3. **Signal** — Gate checks pass → signal sent to Telegram with approve/reject buttons
4. **Execute** — Approved signal → broker executes trade
5. **Learn** — Outcomes feed back to money_thoughts, principles updated

## Quick Start

```bash
# Dashboard
cd moves && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && python3 run.py
# → http://localhost:8000

# Tests
cd moves && ./run_tests.sh
cd thoughts && python3 -m pytest
```

## Documentation

| File                        | Purpose                                |
| --------------------------- | -------------------------------------- |
| `CLAUDE.md`                 | System architecture + coding standards |
| `moves/CLAUDE.md`           | Execution engine — module docs         |
| `moves/spec/money_moves.md` | Design decisions (high-level)          |
| `moves/spec/clawdbot.md`    | OpenClaw deployment guide              |
| `thoughts/CLAUDE.md`        | Thinking engine — module docs          |
| `thoughts/AGENT_PROMPT.md`  | Sub-agent system prompt                |

---

*Evolved from `money_journal/` — the original research system.*
