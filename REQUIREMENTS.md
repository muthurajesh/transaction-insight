# Transaction Insight — Requirements & Logic Framework

> Based on analysis of `ExportData-April-2026.csv` (227 transactions, Apr 1–30 2026).  
> This document defines **what** the future script should compute, **how** to classify rows, and **which questions** need your answers before automation is final.

---

## 1. Goals

| # | Goal | Output |
|---|------|--------|
| G1 | Validate and improve source `Category` using descriptions | Flagged rows + suggested `AI Category` / `AI Sub-Category` |
| G2 | Separate **real income** from internal money movement | Income tab + metrics |
| G3 | Classify spend into **Need / Want / Wish** (your 3-tier budget model) | `Budget Tier` column + summaries |
| G4 | Support **multiple analysis modules** (extensible) | Extra Excel sheets or summary tabs |
| G5 | Use **local LLM** (Ollama or LM Studio) + **editable lookup Excel** | Script reads/updates shared `transaction-lookups.xlsx` on every run |

---

## 2. Source data model

**Input:** CSV export (e.g. Quicken / bank aggregator).\n\n> For multi-month exports, parser must tolerate occasional malformed quoted rows.\n> Use robust CSV parsing (`encoding='utf-8-sig'`, Python engine, `on_bad_lines='skip'`) and emit an **ingest warning count** in the Summary sheet.

| Column | Role in logic |
|--------|----------------|
| `Original Description` | Primary text for rules + LLM |
| `User Description` | User overrides — **trust first** when non-empty |
| `Simple Description` | Best human-readable merchant label |
| `Category` | Source taxonomy — **hint only**, not ground truth |
| `Amount` | Sign = direction; magnitude = size (+ commas) |
| `Account Name` | Distinguish bank vs credit card (critical for CC payments) |
| `Date` | Month bucketing, recurrence detection, **sort key on ingest** |

**Derived columns (script output):**

| Column | Purpose |
|--------|---------|
| `Transaction Date` | Parsed `Date`, normalized |
| `Budget Month` | `YYYY-MM` used for monthly rollups (see §2.1) |
| `Income Attribution Month` | `YYYY-MM` for paycheck/salary totals (may differ from transaction date) |
| `Payroll Spillover` | `Y` if paycheck dated in prior-month tail but attributed to next month |
| `Flow Type` | `Income` \| `Expense` \| `Transfer` \| `Adjustment` |
| `AI Category` | Normalized top-level category |
| `AI Sub-Category` | Merchant / bill type |
| `Budget Tier` | `Need` \| `Want` \| `Wish` \| `N/A` (transfers) |
| `Cost Type` | `Fixed` \| `Variable` (recurring obligation vs flexible) |
| `Sub-Type` | Reserved (future, e.g. subscription vs one-off) |
| `Analysis Flags` | Comma-separated: `miscategorized`, `review`, `duplicate_risk` |
| `Include in Spend?` | `Y` / `N` — exclude internal transfers & CC payments |

---

## 2.1 CSV ingest: sort order & payroll spillover (salary before month-end)

**Intent:** Exports often include the **last week of the prior calendar month** because salary is deposited **before** the new month starts (e.g. March 29 payroll belongs to **April’s** budget). The script should not miss that income when summarizing “April.”

### Step 1 — Always sort on load

```
ON csv_load:
    parse Date → Transaction Date
    SORT all rows BY Transaction Date ASCENDING
```

All downstream logic (batch LLM, recurrence, summaries) uses this order.

### Step 2 — Detect “tail” at start of file

| Symbol | Meaning |
|--------|---------|
| `file_start` | Earliest `Transaction Date` in the CSV |
| `file_end` | Latest `Transaction Date` in the CSV |
| `tail_days` | Configurable, default **7** (`PAYROLL_SPILLOVER_DAYS`) |

```
tail_of_prior_month =
    file_start is NOT day 1 of its calendar month
    AND day-of-month(file_start) >= (days_in_month(file_start) - tail_days + 1)

primary_budget_month =
    if file spans two calendar months → month(file_end)   # e.g. Mar 28–Apr 30 → April
    else → month(file_start)                              # full single-month file
```

**Example**

| CSV date range | `tail_of_prior_month`? | `primary_budget_month` | Payroll on Mar 29 |
|----------------|------------------------|------------------------|-------------------|
| Apr 1 – Apr 30 | No | April | N/A (stays April) |
| Mar 28 – Apr 30 | Yes | April | Attribute to **April** income |
| Mar 15 – Apr 30 | No (starts mid-month) | April | Review flag only |

### Step 3 — Attribute paycheck to the **next** budget month

Applies only when `tail_of_prior_month = true`.

```
is_paycheck(row) =
    source Category IN (Paychecks/Salary, …) OR
    description matches PAYROLL / employer rules from lookups

IF tail_of_prior_month
   AND is_paycheck(row)
   AND month(Transaction Date) = month(file_start):   # prior-month tail only

    Income Attribution Month = primary_budget_month
    Budget Month             = primary_budget_month   # for income rollups
    Payroll Spillover        = Y

ELSE:
    Income Attribution Month = month(Transaction Date)
    Budget Month             = month(Transaction Date)   # expenses use actual date
    Payroll Spillover        = N
```

**Non-payroll rows** in the prior-month tail (groceries, transfers, etc.) keep `Budget Month = month(Transaction Date)` — only **salary/paycheck** shifts to the next month.

### Step 4 — Income summary uses attribution, not raw date

```
Gross income for April =
    SUM(amount)
    WHERE Flow Type = Income
      AND is_paycheck
      AND Income Attribution Month = '2026-04'
```

Report both on the Summary sheet:

| Line | Definition |
|------|------------|
| Gross income (calendar) | By `Transaction Date` month |
| Gross income (budget month) | By `Income Attribution Month` — **primary** for your monthly view |
| Payroll spillover included | List rows where `Payroll Spillover = Y` |

### Configuration (`.env` or lookups)

| Key | Default | Purpose |
|-----|---------|---------|
| `PAYROLL_SPILLOVER_DAYS` | `7` | Length of “last week” window |
| `PAYROLL_CATEGORIES` | `Paychecks/Salary` | Source categories treated as paycheck |
| `PAYROLL_KEYWORDS` | `PAYROLL`, employer names | Backup match on descriptions |

### Edge cases

| Case | Behavior |
|------|----------|
| Two paychecks in tail (biweekly) | Both attributed to `primary_budget_month` |
| Paycheck on day 1 of month | Normal attribution — no spillover flag |
| Export starts Apr 1 | No tail detection; logic is a no-op |
| Bonus / non-payroll inflow in tail | Not shifted unless added to paycheck rules |
| User overrides | Non-empty `User Description` + lookup row can force attribution |

### 2.2 Multi-month spillover extension

For files spanning 2+ months, apply paycheck spillover attribution per month boundary, not only at file start.

```
FOR each calendar month M present in the file:
    IF paycheck date is in last `PAYROLL_SPILLOVER_DAYS` of month M:
        Income Attribution Month = month(M + 1)
        Payroll Spillover = Y
```

This captures scenarios like:
- Jan 30 paycheck attributed to February budget month
- Mar 31 paycheck attributed to April budget month

### 2.3 Account-aware card handling (including Amazon Synchrony)

Use `Account Name` + description rules to avoid card double counting:

- Card purchase on credit/store-card account → `Flow Type = Expense` and `Include in Spend? = Y`
- Bank payment to card (e.g., `AMAZON CORP ... SYF PAYMNT`) → `Flow Type = Transfer`, `Include in Spend? = N`
- Payment received on card account (`ONLINE PAYMENT, THANK YOU`) → `Flow Type = Transfer`, `Include in Spend? = N`

Add lookup sheet `Account Rules`:

| Account Name pattern | Account Type | Include in Spend? | Notes |
|----------------------|--------------|-------------------|-------|
| `*Store Card*` | Credit | `Y` for purchases, `N` for payments | Amazon Synchrony support |
| `*Credit Card*` | Credit | same as above | Generic rule |
| `*Bank*` | Bank | Source of transfer payments | Checking/savings |

---

## 3. Flow Type logic (income vs transfer vs expense)

**Problem:** Raw positive amounts include payroll **and** credit card payments **and** savings transfers — treating all inflows as “income” overstates earnings by ~3×.

### 3.1 Rules (deterministic first, LLM for edge cases)

```
IF matches_credit_card_payment_received(account, description):
    Flow Type = Transfer
ELSE IF category IN (Paychecks/Salary, Interest) AND amount > 0:
    Flow Type = Income
ELSE IF category IN (Savings, Securities Trades) OR transfer_keywords:
    Flow Type = Transfer
ELSE IF category = Credit Card Payments:
    Flow Type = Transfer
ELSE IF category = Refunds/Adjustments AND amount > 0:
    Flow Type = Adjustment   # may reduce prior expense, not salary
ELSE IF category IN (Rewards, Other Income, Expense Reimbursement) AND amount > 0:
    Flow Type = Adjustment   # usually offsets prior spend or is a non-salary credit
ELSE IF amount > 0 AND zelle_small_peer_inflow:
    Flow Type = Adjustment   # default: reimbursement, not income
ELSE IF amount > 0:
    Flow Type = Income       # LLM review
ELSE:
    Flow Type = Expense
```

### 3.2 April 2026 reference numbers

| Metric | Amount | Notes |
|--------|--------|-------|
| Raw total inflow | $22,556 | Misleading headline number |
| Payroll (2 deposits) | **$6,656.12** | Trinity Dist ~$3,328 × 2 |
| Interest | **$5.82** | |
| **True recurring income** | **~$6,662** | Excludes transfers & CC noise |
| Zelle in (14 txns) | $458.66 | Mostly $7–$25 — likely splits/reimbursements |
| Refunds/adjustments | $3,766.62 | Includes $3,644 AllView Real Est — **confirm with user** |
| CC payment received (on cards) | $7,138 | **Not income** — paying off cards |
| Savings + invest outflows | $9,438 | Capital One, Robinhood ($3,328), etc. |
| Brokerage transfer (BRK) | $7,500 | User-triggered move |

---

## 4. Income analysis module (G2)

### 4.1 Metrics (per month)

Use **`Income Attribution Month`** for paycheck totals (§2.1). Use **`Budget Month`** for expenses unless otherwise noted.

| Metric | Definition |
|--------|------------|
| **Gross income (budget month)** | Sum of paycheck `Flow Type = Income` where `Income Attribution Month` = target month |
| **Gross income (calendar)** | Same, but grouped by `Transaction Date` month (reconciliation) |
| **Payroll spillover** | Paychecks with `Payroll Spillover = Y` included in target month |
| **Net adjustments** | Refunds/credits that offset spend (optional separate line) |
| **User-triggered savings** | Outflows to: savings accounts, brokerage, Robinhood, labeled “Savings” / “Securities Trades” |
| **Savings rate** | `User-triggered savings / Gross income` |
| **Peer transfer net** | Zelle/Venmo in minus out (social/reimbursement layer) |

### 4.2 April insights (from this file)

- **Payroll is steady:** 2 × ~$3,328 ≈ $6,656/mo gross from employer.
- **Aggressive savings:** ~$9,438 to savings/invest + $7,500 to brokerage ≈ **$16,938** moved out of checking (plus Robinhood amount ≈ one paycheck).
- **Interest** is negligible ($5.82).
- **Do not count** $7,138 CC “payments received” as income.

---

## 4.3 Feb–Apr 2026 snapshot (3-month file with Amazon card)

- Rows parsed: **773** (1 malformed quoted row skipped during ingest)
- Date range: **2026-01-29 → 2026-04-30**
- New account detected: **Amazon Credit Card (Synchrony) - Amazon Store Card** (51 txns)
- Amazon card purchases: **$2,720.38** total (Feb $857.97, Mar $1,374.63, Apr $487.78)
- Matching bank payments to Synchrony (`SYF PAYMNT`): **$2,636.14**

Monthly payroll (calendar month):

| Month | Payroll | Spend (excl internal transfers) | Savings/Invest out |
|------|--------:|----------------------------------:|-------------------:|
| 2026-02 | $6,656.12 | $10,856.87 | $14,115.57 |
| 2026-03 | $21,577.77 | $17,060.52 | $12,420.85 |
| 2026-04 | $6,656.12 | $11,212.47 | $9,438.37 |

> March includes an unusual payroll spike ($14,921.65). Keep this as payroll unless user marks it as bonus/one-off in lookup rules.

## 4.4 Jun 2025–Apr 2026 snapshot (11-month file; Amazon + paycheck spillover)

- Rows parsed: **2784** (some malformed quoted CSV lines may be skipped during ingest)
- Date range: **2025-05-28 → 2026-04-30**
- Paychecks: **24 deposits** in `Paychecks/Salary`
- Paycheck “tail” behavior (last **7 days** of month):
  - Paychecks in the tail: **13**
  - Tail month buckets observed: each of `2025-05` through `2025-12` has 1 tail paycheck; `2026-03` has 2 tail paychecks

Payroll by calendar month:

| Calendar Month | Count | Total |
|---|---:|---:|
| 2025-05 | 1 | $3,302 |
| 2025-06 | 2 | $6,604 |
| 2025-07 | 2 | $6,604 |
| 2025-08 | 2 | $6,604 |
| 2025-09 | 2 | $6,604 |
| 2025-10 | 2 | $6,604 |
| 2025-11 | 2 | $6,604 |
| 2025-12 | 2 | $6,604 |
| 2026-01 | 2 | $6,656 |
| 2026-02 | 2 | $6,656 |
| 2026-03 | 3 | $21,578 |
| 2026-04 | 2 | $6,656 |

Tier spend totals (last 6 calendar months; excl internal transfers, abs(outflows)):

| Calendar Month | Need | Want | Wish | Review |
|---|---:|---:|---:|---:|
| 2025-11 | $1,452 | $2,456 | $6,604 | $307 |
| 2025-12 | $3,027 | $2,334 | $9,226 | $183 |
| 2026-01 | $1,382 | $2,686 | $6,439 | $83 |
| 2026-02 | $2,243 | $2,527 | $5,855 | $232 |
| 2026-03 | $4,385 | $3,261 | $9,168 | $247 |
| 2026-04 | $3,699 | $2,360 | $4,914 | $240 |

Amazon Store Card usage:

- Total Amazon-card spend (abs outflows): **$9,348** across the period
- Purchases by month include: `2025-06 $1,562`, `2025-03 $1,375`, `2025-10 $1,208`, `2026-04 $488` (with smaller months in between)

Practical implication for the script:
- The deterministic default rules should cover most rows; LLM is only needed for small “Review” remainder (in an offline run, the script flagged ~90 review rows for LLM refinement).

## 5. Budget Tier logic (Need / Want / Wish)

Your model (aligned to 50/30/20-style thinking but with explicit “cut list”):

| Tier | Meaning | Examples from April data |
|------|---------|---------------------------|
| **Need** | Must pay to keep household running | Utilities ($781), T-Mobile ($174), Cox ($100), Insurance ($2,098), HOA/Dues ($320 Dove Canyon + $125 Parkside), healthcare, home warranty (AHS) |
| **Want** | Necessary life spend, but amount/timing flexible | Groceries ($1,270), gas ($224), pets ($513), gym ($150) |
| **Wish** | Discretionary — primary budget-cut candidates | Dining ($1,643), entertainment ($476), subscriptions, shopping, gifts, travel |

**Exclusions from tier totals:** `Flow Type = Transfer` (CC payments, Zelle net funding, savings moves).

### 5.3 Extended Need/Want/Wish mapping (from Jun 2025–Apr 2026)

From the 11-month dataset, these categories frequently represent your 3 spend tiers:

| Tier | Likely categories (source taxonomy) |
|------|--------------------------------------|
| Need | Utilities; Telephone Services; Internet Services / Internet Services - COX; Insurance; Healthcare/Medical; Household Repairs; Services; Mortgages; Mortgages - HOA; Rent; Dues and Subscriptions; Other Expenses (often HOA/home-related); Automotive Expenses (repairs/maintenance) |
| Want | Groceries; Gasoline/Fuel; Pets/Pet Care; Personal Care / Personal Care - Gym; Clothing/Shoes; Electronics; Education |
| Wish | Restaurants/Dining (including - Coffee and - Restaurants Misc); Entertainment; Travel; Hobbies; Gifts; Charitable Giving; General Merchandise; Online Services (often subscriptions/tools) |
| Review | Checks; Service Charges/Fees; Office Supplies; Child/Dependent Expenses; Home Misc; Deposits; Rewards (typically not spend) |

### 5.1 April spend (excluding internal transfers) ≈ **$10,725**

| Tier (proposed) | Amount | % of spend |
|-----------------|--------|------------|
| Need | ~$3,582 | 33% |
| Want | ~$2,205 | 21% |
| Wish | ~$4,731 | 44% |
| Review | ~$207 | 2% |

> Dining alone ($1,643) ≈ **15%** of total spend — top single “Wish” lever.

### 5.2 Lookup table drives tier

`lookups.xlsx` sheet **Budget Tier Rules**:

| AI Category | AI Sub-Category | Budget Tier | Cost Type |
|-------------|-----------------|-------------|-----------|
| Utilities | Electric | Need | Fixed |
| Groceries | Supermarket | Want | Variable |
| Dining | Fast food | Wish | Variable |
| … | … | … | … |

Script: **rules first** → **LLM for unknown merchants** → write back new rows to lookup file.

---

## 6. Category validation (G1)

### 6.1 Generally correct

| Source category | Verdict |
|-----------------|---------|
| Groceries (Trader Joe’s, Pavillions, Target food) | ✓ |
| Restaurants/Dining (In-N-Out, Chipotle, etc.) | ✓ |
| Utilities (Edison, SoCalGas, water) | ✓ |
| Paychecks/Salary | ✓ |
| Gasoline/Fuel | ✓ |

### 6.2 Likely wrong or ambiguous (April)

| Issue | Row pattern | Suggested fix |
|-------|-------------|---------------|
| HOA as Credit Card Payments | Dove Canyon ASSN DUES | `Housing` / `HOA Dues` / **Need** |
| Church as Restaurants | STJOHNS-ES.ORG | `Charitable` or `Community` / **Wish** |
| Cursor/ChatGPT as Travel | CURSOR, OPENAI | `Subscriptions` / **Wish** |
| AHS as Other Expenses | AHS.COM (also Insurance elsewhere) | Consolidate → `Insurance` or `Home` / **Need** |
| Walmart in Groceries vs General Merchandise | Mixed | Sub-category by description |
| Kohl’s +$104.70 | Return in General Merchandise | `Adjustment`, exclude from spend |
| AllView Real Est +$3,644 | Refunds/Adjustments | Confirm: deposit return vs income |

---

## 7. Additional analysis modules (future sheets)

| Module | Key outputs |
|--------|-------------|
| **M1 — Income & savings** | Gross income, savings rate, brokerage transfers |
| **M2 — Need / Want / Wish** | Tier totals, % of income, month-over-month |
| **M3 — Subscription audit** | Hulu, Netflix, Spotify, Apple, OpenAI, Zoom, T-Mobile, etc. |
| **M4 — Dining & delivery** | Merchant frequency, avg ticket, “cut $X if reduce 2 visits/week” |
| **M5 — Transfer map** | Zelle/Venmo/Apple Cash net by person |
| **M6 — Credit card float** | Charges on cards vs payments from checking (avoid double-count) |
| **M7 — Business vs personal** | AWS, Google Cloud, etc. |
| **M8 — Recurring bill calendar** | Fixed bills by due date / amount band |

---

## 8. Script architecture (target)

```
CSV → [Parse dates, sort ASC] → [Detect payroll spillover / set Budget Month]
    → [Normalize] → [Rules engine + transaction-lookups.xlsx] → [LLM batch for unknowns]
    → [Update transaction-lookups.xlsx] → Excel workbook:
        - Raw Data
        - Income
        - Expenses
        - Transfers (optional)
        - Summary (metrics)
        - Analysis: Need/Want/Wish
        - Analysis: Income & Savings
        - (future modules)
```

**LLM role:** Nuance only (Zelle intent, mixed merchants, new vendors).  
**Lookup role:** Your edits persist — merge “Cloud Services” vs “Cloud services” duplicates.

**Providers:** `ollama` \| `lmstudio` \| `openai` (already in `process_transactions.py`).

---

## 9. Open questions (need your input)

1. **Mortgage / rent:** No clear mortgage payment in April — different account, autopay elsewhere, or paid via AllView?
2. **AllView Real Est +$3,644:** Security deposit return, rent rebate, or other?
3. **Rob Segura Zelle −$4,700:** Rent to person, family, savings holding?
4. **Capital One transfers (−$6,110):** Emergency fund / HYSA / other goal?
5. **Robinhood −$3,328:** Invest full paycheck — always, or one-time?
6. **Zelle in (~$24–$25):** Kids activity splits — treat as reimbursement (reduce Want/Wish) not income?
7. **AHS charges:** Home warranty — classify under **Need**?
8. **Business expenses (AWS/GCP):** Separate P&L or ignore in personal budget?
9. **Priority modules:** Which 2–3 analyses after Need/Want/Wish (subscriptions, dining, transfers)?
10. **Groceries tier:** Confirm **Want** (not Need) per your message?

11. `Other Income` (AllView Real Est): is it a refund/credit that should reduce spend, or is it truly income?
12. `Rewards` category: should card rewards be treated as `Adjustment` (reduce spend) rather than income?
13. `Mortgages - HOA` and `Dues and Subscriptions`: confirm they should be in **Need** and treated as **Fixed**.

---

## 10. Acceptance criteria (MVP)

- [ ] Loads any monthly CSV with same column schema
- [ ] **Sorts by `Date` ascending immediately after load**
- [ ] **Attributes tail-of-prior-month paychecks to `primary_budget_month` (§2.1)**
- [ ] Summary shows gross income by budget month **and** calendar month
- [ ] Applies lookup rules; appends new category/tier rows when LLM classifies
- [ ] Income sheet excludes transfers and CC payments
- [ ] Summary tab: Gross income, spend by Need/Want/Wish, savings rate
- [ ] Flags miscategorized rows with reason
- [ ] Runs on Ollama or LM Studio without cloud API
