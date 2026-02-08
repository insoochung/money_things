# Multi-User Design — Separate Portfolios

## Model

Each user owns their own everything: portfolio, theses, signals, trades, risk limits, kill switch.
Thesis sharing is opt-in — a user can publish a thesis for others to clone into their own portfolio.

## Users Table

```sql
users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT UNIQUE NOT NULL,       -- Google OAuth email, login identifier
    name          TEXT NOT NULL,              -- Display name
    telegram_id   TEXT,                       -- For signal notifications (nullable until linked)
    role          TEXT DEFAULT 'user',        -- 'admin' | 'user'
    settings      TEXT DEFAULT '{}',          -- JSON: timezone, notification prefs, expertise profile
    active        BOOLEAN DEFAULT TRUE,
    created_at    TEXT DEFAULT (datetime('now')),
    last_login    TEXT
)
```

## Ownership — What Gets `user_id`

Every portfolio-specific table gets a `user_id INTEGER REFERENCES users(id)`:

| Table | user_id | Reasoning |
|-------|---------|-----------|
| accounts | ✅ | Your brokerage accounts |
| positions | ✅ | Your holdings |
| lots | ✅ | Your tax lots |
| theses | ✅ | Your investment theses |
| thesis_versions | (via thesis) | Inherited through thesis_id |
| thesis_news | (via thesis) | Inherited through thesis_id |
| signals | ✅ | Your signals, your decisions |
| signal_scores | ✅ | Your source accuracy history |
| trades | ✅ | Your execution history |
| orders | ✅ | Your orders |
| portfolio_value | ✅ | Your NAV history |
| exposure_snapshots | ✅ | Your exposure |
| risk_limits | ✅ | Your risk tolerance |
| kill_switch | ✅ | Your emergency stop |
| drawdown_events | ✅ | Your drawdown tracking |
| what_if | ✅ | Your passed signals |
| principles | ✅ | Your learned principles |
| trading_windows | ✅ | Your restricted windows (META etc.) |
| scheduled_tasks | ✅ | Your task schedules |
| audit_log | ✅ | Your activity log |

## Shared (Global) Tables — No `user_id`

| Table | Why shared |
|-------|-----------|
| congress_trades | Public data, same for everyone |
| price_history | Market prices are universal |
| schema_version | System-level |

## Thesis Sharing

Opt-in publishing. A user can share a thesis; others can clone it.

```sql
shared_theses (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    thesis_id     INTEGER REFERENCES theses(id),   -- original thesis
    shared_by     INTEGER REFERENCES users(id),    -- who shared it
    shared_at     TEXT DEFAULT (datetime('now')),
    active        BOOLEAN DEFAULT TRUE              -- can unshare
)
```

When user B "clones" a shared thesis:
- A new row is inserted into `theses` with `user_id = B` and `cloned_from = original_thesis_id`
- B's copy is fully independent — they can modify, add symbols, change conviction
- B's copy tracks its lineage for attribution but diverges freely

Add to `theses` table:
```sql
cloned_from   INTEGER REFERENCES theses(id)  -- NULL if original
```

No real-time sync. Clone is a snapshot. If A updates, B's copy doesn't change.
B can always re-clone to get A's latest version (creates a new thesis).

## Session & Auth Flow

1. User hits dashboard → Google OAuth → get email
2. Look up email in `users` table
3. If not found → reject (allowlist model, admin creates users)
4. Session cookie stores `user_id`
5. Every API request → middleware extracts `user_id` from session
6. Every DB query filters by `user_id` (enforced at dependency injection layer)

## API Changes

### New endpoints
```
GET  /api/users/me              — Current user profile
PUT  /api/users/me              — Update settings (timezone, notifications)
GET  /api/users/me/link-telegram — Get Telegram link code
POST /api/admin/users           — Create user (admin only)
GET  /api/admin/users           — List users (admin only)

GET  /api/fund/shared-theses    — Browse shared theses from other users
POST /api/fund/shared-theses/{id}/clone — Clone a shared thesis into your portfolio
POST /api/fund/theses/{id}/share — Share one of your theses
DELETE /api/fund/theses/{id}/share — Unshare a thesis
```

### Modified endpoints (all existing)
All `/api/fund/*` endpoints automatically scoped to authenticated user.
No URL changes needed — the user context comes from the session, not the URL.

## Telegram Bot — Per-User Routing

Each user links their Telegram account:
1. Dashboard shows a unique 6-digit link code (expires in 10 min)
2. User sends `/link <code>` to the bot
3. Bot validates code, stores `telegram_id` on user record
4. From then on, signals for that user go to their Telegram DM

The bot needs to know which user is messaging it:
- Look up `telegram_id` in users table
- Route approve/reject to the correct user's signal

## Dashboard Changes

- Login screen (Google OAuth)
- User avatar/name in header
- "Shared Theses" section — browse and clone
- Share button on thesis cards
- Settings page: notification prefs, timezone, Telegram linking

## Migration Strategy

1. Create `users` table
2. Create a default user (Insoo) with admin role
3. Add `user_id` column to all owned tables (DEFAULT 1 for existing data)
4. Add NOT NULL constraint after backfill
5. Create `shared_theses` table
6. Add `cloned_from` to `theses`
7. Update all engine methods to accept/filter by `user_id`
8. Update all API routes to extract `user_id` from session
9. Update Telegram bot to route by `telegram_id`

## Dependency Injection

Current: `EngineContainer` holds engines shared across all requests.
New: Engines are still shared (stateless), but every method takes `user_id` param.

```python
# Before
def get_positions(self) -> list[Position]:
    return self.db.query("SELECT * FROM positions")

# After  
def get_positions(self, user_id: int) -> list[Position]:
    return self.db.query("SELECT * FROM positions WHERE user_id = ?", (user_id,))
```

API dependency:
```python
async def get_current_user(request: Request) -> User:
    """Extract authenticated user from session."""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(401)
    return get_user_by_id(user_id)
```

Routes:
```python
@router.get("/positions")
async def get_positions(
    engines = Depends(get_engines),
    user: User = Depends(get_current_user),
):
    return engines.get_positions(user_id=user.id)
```

## Security Rules

1. Users can ONLY see/modify their own data (enforced at query level)
2. Admin can view any user's data (for support)
3. Shared theses show author name but not their portfolio details
4. Telegram link codes are single-use and expire
5. Session cookies are httponly, secure, samesite=strict
