# API AUDIT - JavaScript vs Python Backend

## METHODOLOGY
1. Analyzed all `api()` and `apiWrite()` calls in moves.js
2. Cross-referenced with Python route definitions in api/routes/
3. Checked field access patterns in JavaScript vs Pydantic response models
4. Identified structural and naming mismatches

## CRITICAL MISMATCHES FOUND

### 1. `/api/fund/status` - FundStatus Model Missing Fields
**JavaScript accesses:**
```js
const sharpe = d.sharpe_ratio ?? d.sharpe ?? 0;
```

**Python FundStatus model MISSING:**
- `sharpe_ratio` field not defined ✗
- `mode` field accessed by JS but not in Python model ✗

**Fix Required:** Add missing fields to FundStatus model

---

### 2. `/api/fund/exposure` - ExposureBreakdown Model Issues
**JavaScript accesses:**
```js
const cash = d.cash_pct ?? d.cash ?? Math.max(0, 100 - long - short);
```

**Python ExposureBreakdown model MISSING:**
- `cash_pct` field not defined ✗
- Also JS expects some fields that don't exist

**Fix Required:** Add cash_pct field to ExposureBreakdown model

---

### 3. `/api/fund/risk` - Structure Mismatch
**JavaScript expects:**
```js  
const d = await api('/api/fund/risk');
const metrics = [
  { label: 'Worst-Case Loss', val: d.worst_case_loss, ... },
  { label: 'Crash Impact (-20%)', val: d.crash_impact, ... },
  { label: 'Concentration', val: d.concentration, ... },
  { label: 'VaR (95%)', val: d.var_95, ... },
];
```

**Python returns:**
```python
return {
    "metrics": risk_metrics.dict(),  # nested structure
    "limits": [...],
    "alerts": [...]
}
```

**CRITICAL MISMATCH:** JS expects flat object, Python returns nested ✗

**Fix Required:** Flatten response or update JavaScript to access nested structure

---

### 4. `/api/fund/positions` - Missing Fields for Range Bar
**JavaScript uses:**
```js
const rangeBar = buildRangeBar(p.stop, cur, p.target);
```

**Python Position model MISSING:**
- `stop` field (stop loss price) ✗  
- `target` field (target price) ✗
- `review_days` field used in table ✗

**Fix Required:** Add stop, target, review_days to Position model

---

### 5. `/api/fund/theses` - Field Name Mismatches
**JavaScript accesses:**
```js
const syms = (t.symbols || t.tickers || []).join(', ');
```

**Python ThesisResponse provides:**
- `symbols` ✓
- `tickers` field not provided (but JS expects fallback) ✗

**JavaScript save operation:**
```js
const data = {
  symbols: card.querySelector('.te-symbols').value.split(',').map(s => s.trim()).filter(Boolean),
};
await apiWrite(`/api/fund/theses/${id}`, 'PATCH', data);
```

**Python PATCH endpoint expects:**
- Field name matches ✓

---

### 6. Missing Endpoints - Dead Code Detection
**JavaScript calls these endpoints that may not exist:**

1. **`/api/fund/correlation`** - ✓ EXISTS in risk.py
2. **`/api/fund/drawdown`** - ✓ EXISTS in performance.py  
3. **`/api/fund/signals`** - ✓ EXISTS in signals.py
4. **`/api/fund/congress-trades`** - ✓ EXISTS in intelligence.py
5. **`/api/fund/principles`** - ✓ EXISTS in intelligence.py
6. **`/api/fund/what-if`** - ✓ EXISTS in intelligence.py
7. **`/api/fund/users/me`** - ✓ EXISTS in users.py
8. **`/api/fund/shared-theses`** - ✓ EXISTS in theses.py
9. **`/api/fund/reset`** - Need to check admin.py

---

### 7. POST/PUT/DELETE Endpoint Mismatches
**JavaScript makes these write operations:**

1. **`POST /api/fund/theses`** - ✓ EXISTS
2. **`PATCH /api/fund/theses/{id}`** - ✓ EXISTS  
3. **`POST /api/fund/watchlist`** - ✓ EXISTS in watchlist.py
4. **`PUT /api/fund/watchlist/{id}`** - ✓ EXISTS
5. **`DELETE /api/fund/watchlist/{id}`** - ✓ EXISTS
6. **`POST /api/fund/principles`** - ✓ EXISTS
7. **`POST /api/fund/signals/{id}/approve`** - ✓ EXISTS
8. **`POST /api/fund/signals/{id}/reject`** - ✓ EXISTS
9. **`POST /api/fund/shared-theses/{id}/clone`** - ✓ EXISTS
10. **`POST /api/fund/trades/manual`** - ✓ EXISTS
11. **`POST /api/fund/reset`** - Need to verify in admin.py

## ENDPOINTS CALLED BY JS BUT NOT YET VERIFIED

### Tax & Account Endpoints
- `/api/fund/tax/summary` - Need to check tax.py ✓
- `/api/fund/tax/harvest-candidates` - Need to check tax.py ✓  
- `/api/fund/tax/accounts` - Need to check tax.py ✓

### Missing from Fund.py
JavaScript expects these under `/api/fund/` prefix but they're in separate route files:
- All routes are correctly mounted under `/api/fund/` in app.py ✓

## PRIORITY FIXES NEEDED

### HIGH PRIORITY (Breaking UI)
1. **Fix `/api/fund/risk` structure mismatch** - Critical for risk dashboard
2. **Add missing fields to FundStatus model** - Breaks summary cards  
3. **Add missing fields to Position model** - Breaks range bar display
4. **Fix ExposureBreakdown model** - Breaks exposure visualization

### MEDIUM PRIORITY  
1. Verify all missing endpoint implementations
2. Check field name consistency across all models
3. Update JavaScript to handle missing optional fields gracefully

### LOW PRIORITY
1. Remove unused JavaScript code for non-existent endpoints
2. Add TypeScript definitions based on Pydantic models
3. Standardize error handling between JS and Python