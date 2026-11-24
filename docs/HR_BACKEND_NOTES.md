# HR upgrade: backend notes

Built to the desktop repo's `docs/HR_CONTRACT.md` (phases H2, H4, H5, H6). This file
records how each part works and where the backend differs from the contract.

## Where things live

| Part | Code |
|---|---|
| Tables and columns | `app/models/hr.py`, `app/models/user.py`, `app/models/attendance.py` |
| Migrations | `alembic/versions/f1a2b3c4d5e7_add_hr_upgrade.py` (schema, backfill, seed), `f2b3c4d5e6a8_grant_hr_actions.py` (IAM grants) |
| Wage history and pay types | `app/services/wage_service.py`, `GET/POST /people/users/{id}/wages` in `app/api/people.py` |
| Payroll | `app/services/payroll_service.py` (summary row gains the new fields; pay-out includes weekly/monthly pay) |
| Leave | `app/services/leave_service.py`, `app/api/leave.py` |
| Shifts and holidays | `app/api/shifts.py`, `app/services/hr_service.py`; late / early minutes in `workforce_service.apply_shift_minutes` |
| Profile and documents | `app/schemas/user.py` (`ProfileFields`), `app/services/hr_service.py`, `app/api/people.py` |
| Tests | `tests/test_hr.py`, `tests/test_iam.py` (permission table) |

## How pay works

* `user_wages` holds every rate with its start date. Existing people get one row
  (their pay today, from the day they were added) in the migration; a person created
  or edited later gets one when their rate is first set.
* **daily / hourly**: the day's pay is fixed when the day is recorded and sits in
  `daily_wages` exactly as before (hourly = daily wage / 8 x hours). A later rate
  change never touches recorded days.
* **weekly / monthly**: those days' `daily_wages` rows are 0. Pay is worked out for
  every day since the last pay-out's `through` date (present 1, half-day 0.5, paid
  leave 1, paid holiday 1, weekly off 1, absent and unpaid leave 0) at the rate in
  force on each day, and the pay-out that covers them moves `through` forward.
* `users.base_daily_wage` follows the rate in force today (weekly / 7, monthly / days
  in this month, hourly x 8); a dated rate takes over on its day.
* Dates are the server's date and shift times are workshop wall-clock time, UTC+5:30
  (`BUSINESS_UTC_OFFSET_MINUTES`).

## Deviations from the contract

1. **Leave on a day that already has an attendance row.** Approval skips days where the
   person already has any attendance record; leave never overwrites a worked or marked day.
2. **Daily and hourly staff are not paid for leave or holidays.** The contract only pays
   leave and holidays for weekly and monthly pay types; that is what is built.
3. **`late_minutes`** is the minutes after the shift start, and only when that is past the
   grace (09:25 with a 09:00 start and 10 minutes grace is 25, 09:05 is 0). The contract
   says "after shift start + grace" without saying which minutes are counted.
4. **`unpaid_days`** in the summary counts, for weekly and monthly people, every day since
   the last pay-out that earned less than a full day, except unworked holidays and weekly
   offs. For daily and hourly people `payable_days` is the unpaid present days (half-day 0.5)
   and `unpaid_days` is 0.
5. **Salaried pay-outs stop at today.** `POST /payroll/pay` with `through` in the future
   is 400 for anyone who has ever been on a weekly or monthly rate.
6. **Offboard.** `net_to_pay` = pending pay + extras minus the advance (the advance taken
   back is capped at what is owed); `gross` and `deactivates_on` are added to the response.
   When the last working day is already past the account is switched off at once; when it
   is today or later it stays on and `get_current_user` refuses it from the day after.
7. **Holiday rows in attendance** are built, never stored, and only added when both
   `start_date` and `end_date` are asked for (`/attendance/all` and `/attendance/my`;
   `start_date` / `end_date` are new optional parameters on `/my`). At most three months.
   Without a date range the answers are exactly what they were.
8. **Extra routes** the contract did not list: `POST /leave/types`, `PUT /leave/types/{id}`
   (`leave:type:manage`), and an optional `on=` date on the balance routes to see another
   leave year.
9. **Leave type reads** (`GET /leave/types`) hide inactive types unless
   `include_inactive=true` and the caller has `leave:type:manage`.
10. **`leave:request:create`** is a normal permission granted to all built-in roles (the
    migration adds it to their policies); a custom role has to be given it.
11. **Documents** gain a hidden `confirmed` flag: a row is created by `upload-url` and only
    listed after `confirm` has checked the object in the bucket. Delete (and upload) use
    `people:user:write_documents` (owner), which is the "owner-only" delete.
12. **Wage history on the old route.** `PATCH /auth/users/{id}` still takes
    `base_daily_wage`; if only that is sent for a weekly / monthly / hourly person it is
    converted to that type's rate. Re-sending the unchanged figure writes no row.
13. **The wage lock** (409 "Wages up to <date> are already paid") applies to
    `POST /people/users/{id}/wages` when `effective_from` is on or before the last pay-out's
    `through` date. The old route is not locked (it writes a row effective today and never
    changes days already recorded).
14. **Visibility.** `pay_type`, `pay_amount`, `pay_upi`, `id_proof_*` and the wage fields
    are hidden (null) in `GET /auth/` for people without `people:user:read_pay`;
    `employment`, `joining_date`, address and emergency contact stay visible with
    `people:user:read`.
15. **Overtime hint** `overtime_hours_suggested` counts hours past the shift end on days
    that are not yet paid; it is never added to pay.

## Migrations and the billing PR

Both new migrations chain from `d1e2f3a4b5c6` (the head of `main` when this was written).
The billing branch (`billing-b3-b4`) also adds migrations after that head, so once both
are merged `alembic heads` shows two; the second PR to merge adds an `alembic merge`
revision.
