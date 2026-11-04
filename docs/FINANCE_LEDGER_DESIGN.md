# Finance ledger: plan for review

**Status:** Draft for the owner and the CA to review. **Nothing in this document is built yet.** Date: 2026-11-02.
This is step 4 of [MICROSERVICES_DESIGN.md](MICROSERVICES_DESIGN.md). It handles the business's money records, so it is planned before it is coded.

---

## 1. What this is, in plain words

Today the app records sales, purchases, expenses, wages and order payments in separate places, and one report (`/reports/profit`) adds them up. A **ledger** is the proper book underneath: every rupee that moves is written once as a **journal entry** with two sides (where it came from, where it went), and the books always balance.

What you get from it:
- Cash, bank/UPI, what customers owe you, what you owe suppliers, customer advances, machines, and staff advances, each with a running balance you can check against reality.
- Profit and loss, a balance sheet and a trial balance that a CA recognises.
- A trail: nothing is ever edited or deleted. A mistake is fixed by a **reversing entry**, so the history stays.

What it does **not** do in the first version: GST (you are not registered), stock valuation, or costing each piece. Those come later (section 9).

## 2. The most important design choice: the ledger is *built from* your records

Instead of changing the sales, purchase and wage code to write ledger entries, a **projector** reads those records and writes the matching ledger entries. It is safe to run at any time and as often as needed (nightly, or after each sale), because each source record is posted **exactly once** (the entry is tagged with the record it came from).

Why this way:
- **Zero risk to selling.** If the ledger has a bug, a sale still goes through. The ledger can be rebuilt from scratch.
- **Shadow mode for free.** It can run beside the current reports for a month and be compared to them, with no change to how staff work.
- **History.** It can post everything already in the database, not just new records.
- **Changes are caught.** Each entry remembers a fingerprint of its source. If the source changes later (for example an expense is voided), the old entry is reversed and a new one posted.

Where it lives: a separate package in the API, `app/finance/`, with its own tables. Nothing else imports it. The design doc planned a separate container; I now recommend **doing that later**, once the rules are proven, because a second container adds deployment work before there is anything to protect. The tables and package boundary make the move mechanical.

## 3. Chart of accounts (proposed, short on purpose)

| Code | Account | Type | Used for |
|---|---|---|---|
| 1000 | Cash | Asset | Cash taken at the shop, cash paid out |
| 1010 | Bank and UPI | Asset | UPI, card, bank and cheque receipts and payments |
| 1090 | Unmatched payments (clearing) | Asset | Money whose method or date the app never recorded (see section 7) |
| 1100 | Customers owe us | Asset | Credit sales and unpaid balances of billed orders |
| 1300 | Staff advances | Asset | Advances given to staff, until recovered |
| 1400 | Machines (at cost) | Asset | Machine purchase cost |
| 1410 | Machines: depreciation so far | Asset (negative) | Written-off amount |
| 2000 | We owe suppliers | Liability | Confirmed purchases not yet paid |
| 2100 | Customer advances | Liability | Money received on an order before it is billed |
| 2200 | Wages owed | Liability | Earned by staff, not yet paid |
| 2300 | GST payable | Liability | **Off** until you register |
| 3000 | Owner's capital and drawings | Equity | Money put in or taken out by the owner |
| 4000 | Sales | Income | Billed sales, before tax |
| 5000 | Raw material bought | Expense | Confirmed purchases (see decision 2) |
| 5100 | Wages | Expense | Pay-outs and daily wages |
| 5200 | Repairs and maintenance | Expense | Machine repairs and servicing |
| 5300 | Depreciation | Expense | **Off** in version 1 |
| 5400 | Rent, power, transport and other costs | Expense | One account per expense category (rows added from your category list) |
| 5900 | Other expenses | Expense | Anything with an unknown category |

The CA should approve this list. Adding accounts later is easy; renaming or merging them after postings exist is not.

## 4. Posting rules, version 1

These are written to **reproduce today's profit report exactly**, so the shadow month can be compared to the paisa. (Today: sales are counted when billed and without tax, purchases when confirmed at their total, wages when paid, expenses when spent and not voided.)

| Event in the app | Debit | Credit | Notes |
|---|---|---|---|
| **Counter sale**, paid | Cash (cash) or Bank and UPI (upi, card, bank, online) | Sales (4000) | Not cancelled. Amount = total minus tax; tax goes to GST payable (zero today) |
| **Counter sale**, not paid | Customers owe us | Sales | |
| **Order advance received** (an order payment before the order is billed) | Cash or Bank and UPI | Customer advances | Money held for an order that has no bill yet |
| **Order billed** | Customer advances (up to what was received), Customers owe us (the rest) | Sales | |
| **Payment on a billed order** | Cash or Bank and UPI | Customers owe us | |
| **Purchase confirmed** | Raw material bought | We owe suppliers | At the purchase total, on the purchase date |
| **Purchase marked paid** | We owe suppliers | Unmatched payments | The app has no supplier-payment record (section 7) |
| **Expense** | The category's account | Cash or Bank and UPI | By the payment method; **voided** expenses are reversed |
| **Payroll pay-out** | Wages (gross daily plus extras) | Cash or Bank and UPI (net paid), Staff advances (advance taken back) | One entry per pay-out |
| **Staff advance given** | Staff advances | Cash | Advance taken back during a pay-out is already inside that pay-out |
| **Old-style wage payment** (`/wages/pay`, no pay-out) | Wages | Cash | |
| **Machine bought** | Machines (at cost) | Unmatched payments | On the purchase date; **not** part of the profit comparison |
| **Machine repair or service** | Repairs and maintenance | Unmatched payments | Same |

Every line can carry the **location** (the Shop, a plant unit) so profit and spending can be cut by place later.

## 5. How it is stored

- `accounts`: code, name, type, which side is normal, switched on or off.
- `journal_entries`: date, memo, which record it came from (`source_type`, `source_id`, and a fingerprint of that record), whether it is a posting or a **reversal** (and of which entry), who or what made it, when.
- `journal_lines`: entry, account, debit or credit (one of them, never both, never negative), optional location and party.
- **The database itself enforces the rules**, not just the app: each entry's debits must equal its credits, and entries and lines can never be updated or deleted (a trigger refuses). A source record can have only one live posting (a unique key).
- **Month closing**: the owner can close the books up to a date; nothing can then be posted on or before it, so reports you have already filed do not move.

## 6. What you will be able to do (routes)

| Route | Who | What |
|---|---|---|
| `GET /finance/trial-balance?as_of=` | owner, manager, accountant | Every account's balance; debits equal credits |
| `GET /finance/ledger/{account}?from=&to=` | owner, manager, accountant | One account's entries with a running balance |
| `GET /finance/profit-and-loss?from=&to=` | owner, manager, accountant | Income minus expenses |
| `GET /finance/balance-sheet?as_of=` | owner, accountant | Assets, liabilities, equity |
| `GET /finance/reconciliation?from=&to=` | owner, manager, accountant | The ledger against the existing reports, line by line, with differences |
| `GET /finance/exceptions` | owner, manager, accountant | Records the ledger could not post cleanly (section 7) |
| `POST /finance/journal` | owner | A manual, balanced entry with a required reason (opening balances, owner's drawings, corrections) |
| `POST /finance/entries/{id}/reverse` | owner | Reverse an entry, with a reason |
| `PUT /finance/entries/{id}` | owner | Correct a manual entry: the old one is reversed and the corrected one posted, in one step |
| `POST /finance/sync` | owner | Run the projector now |

The read-only **accountant** role for your CA (built) can use every `GET` route above and nothing else.

## 7. What your current data cannot tell the ledger (gaps I found)

These are facts about today's app, not guesses. Each becomes an **exception** on the report until fixed, and I propose a small fix for each.

1. **Credit sales have no collections.** A sale carries one status (`pending`, `partial`, `paid`) and one payment method, not a list of payments. Customers owing money cannot be reconstructed, and a `partial` sale does not say how much was paid. *Fix:* a "receive payment on a sale" record, like the one orders already have.
2. **Suppliers have no payment records.** A purchase has only a `payment_status` flag, with no date, method or amount paid. What you owe suppliers cannot be proven. *Fix:* a supplier-payment record.
3. **Machine purchases and repairs have no payment method.** They post against "Unmatched payments" until you say how they were paid.
4. **Cost of goods sold is not available.** Sales and stock movements record the selling price, not what the piece cost to make. That is why version 1 does not value stock or work out margin per piece. *Fix later:* record the cost on each sale line.
5. **No returns or cancellations of billed sales.** There is no route for it, so there is nothing to reverse. *Fix:* a credit note, when the shop needs one.
6. **Bill numbers run per calendar year** (`NTD-2026-00001`); GST wants one series per financial year (April to March). Not a ledger problem, but it must be decided before you register.
7. **Raw material stock is valued at the current price**, not what was paid, so its value moves when the price list changes.

## 8. Shadow mode: how it goes live safely

1. **Build and post history.** The projector posts everything already in the database. The reconciliation compares it, month by month, with the profit report: sales, purchases, wages, expenses. They must agree to the paisa.
2. **Run beside the old reports for one full month.** Staff change nothing. Every day the reconciliation runs; every difference is explained or fixed.
3. **Checks that run automatically:** debits equal credits on every entry; the trial balance nets to zero; no source record is missing a posting; no posting points at a record that has changed or vanished; ledger totals equal the report totals.
4. **Go-live needs three things:** one month with no unexplained difference; your CA's sign-off on the chart of accounts and the rules; and a restore test of the backup taken the same day.
5. Until then the existing reports stay the official numbers. The ledger only becomes the source of truth after you say so.

## 9. Not in version 1 (on purpose)

- **GST.** The accounts and a switch exist but stay off. When you register, GST is turned on from a chosen date with its own invoice series; earlier bills are not rewritten. Your CA should confirm the rules before that day.
- **Stock and cost of goods.** No inventory valuation or margin per piece until the cost of each sale is recorded.
- **Machine depreciation.** The register already works it out; posting it to the books (monthly) needs your CA to choose the method. Tax depreciation in India uses written-down-value rates, which differs from the straight-line figure the machine register shows.
- **Bank statement matching, multi-currency, per-location tills and cash counts.**

## 10. Build order

| Step | What | Size |
|---|---|---|
| A | **This document**, reviewed by you and your CA | done when you approve |
| B | **Ledger core**: accounts, entries, lines, the database rules, reversal, month closing, trial balance, manual journal. No posting from your records yet | M |
| C | **Sales and customer money**: counter sales, order advances, billed orders, payments on orders, and the reconciliation against the sales part of the profit report | M. **Built** (ONBOARDING 4g) |
| D | **Costs**: purchases, expenses, wages, staff advances, machines; the full profit reconciliation and the exceptions list | M to L. **Built** (ONBOARDING 4g) |
| E | **Reports**: profit and loss, balance sheet, accountant role | M. **Built** (ONBOARDING 4g) |
| F | Small fixes the gaps need: sale collections, supplier payments, cost on sale lines | S each |
| Then | One month in shadow mode, review, go-live decision | a month |

## 11. Decisions and questions

**For the owner and the CA.** My proposed answer is in brackets; please correct me.

1. **Method.** Version 1 mirrors today's profit report: income when billed, raw material when bought, wages when paid. Is that the basis your CA wants for your books? (Yes for the shadow month; the CA may move it to full accrual later.)
2. **Raw material: expense or stock?** (Expense when bought, as today. Stock held as an asset needs costing we do not have yet.)
3. **Opening balances.** The ledger starts from a date. Which date, and what were the balances then: cash, bank/UPI, owed by customers, owed to suppliers, machines at cost, staff advances, owner's capital? (Go-live date or 1 April 2026; I need the numbers from you or your CA.)
4. **Chart of accounts** in section 3: approve, rename or add?
5. **Owner's money.** How should money the owner puts in or takes out be recorded? (A manual journal entry to account 3000.)
6. **Expense categories.** Which of your existing categories map to which expense accounts? I will list the ones in use for you to map.
7. **Machines.** Cost above which a purchase counts as a machine rather than an expense, and the depreciation method for the books. (Cost above Rs 10,000 is a machine; method as your CA says.)
8. **Fixing the data gaps** in section 7 (customer collections, supplier payments, cost on sale lines): shall I add them? (Yes, small and separate.)
9. **Returns and cancellations** of billed sales: do you need them soon? (Not yet; credit notes later.)
10. **Financial year and bill series.** Keep one calendar-year series until GST, then start a financial-year series? (Ask your CA before you register.)
11. **Who can post and reverse.** Owner only; manager and the CA read-only. (Yes.)
12. **Accounts to track.** Which bank accounts, and does each plant unit keep a small cash float? (One bank/UPI account and one shop cash account to begin with.)

## 12. Risks

- **Wrong rules, quietly.** Mitigation: the shadow month, the automatic checks, and CA sign-off before go-live.
- **Source records changing after posting.** Mitigation: the fingerprint; changes cause a reversal and a repost, never an edit.
- **Opening balances wrong.** Everything after them inherits the error. Mitigation: your CA signs the opening balances.
- **Over-trusting a new report.** Until go-live the old reports stay official.
