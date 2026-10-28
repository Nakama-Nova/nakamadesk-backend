# Official letters

Four letters the shop issues, each numbered once and printed as an A4 PDF on the letterhead.

| Letter | Number | Who issues and sees it | Source |
|---|---|---|---|
| Quotation | `QTN-2026-27-0001` | owner, manager, sales | typed lines, or the lines of an order |
| Order confirmation and advance receipt | `OCF-...` | owner, manager, sales | an order and its payments |
| Purchase receipt (piece bought from another artisan) | `PRC-...` | owner, manager | supplier or typed seller, optional item |
| Certificate of completion | `CCT-...` | owner | a temple restoration in status `restored` |

- **Numbers** come from `sku_counters` (key `QTN-2026-27`), one series per kind per Indian financial year (April to March). The row stays locked until the letter commits, so two letters never share a number. A voided letter keeps its number.
- **Snapshot.** `letters.payload` holds everything the page shows, including the letterhead, party and amounts as they were at issue time. The PDF is drawn from it on demand, so later changes to an order, a restoration or the letterhead never alter an issued letter.
- **Void.** Owner only, with a reason. The row stays; its PDF gets a VOID watermark and the reason.
- **Letterhead.** `business_profile` (one row, owner edits through `PUT /letters/profile`); defaults apply until saved. GSTIN prints only when filled in.
- **Visibility.** A user who may not see a kind gets 404 for its letters, and the list leaves them out.
- The logo is `app/assets/logo.jpg` (cropped from `docs/brand/logo.jpg`; `docs/` is not shipped to Vercel).
- Money prints as `Rs. 1,23,456.50` with the amount in words (Indian grouping), as in the temple donation receipts.

Endpoints: `GET/PUT /letters/profile`, `POST /letters/quotation | order-confirmation | purchase-receipt | completion-certificate`, `GET /letters`, `GET /letters/{id}`, `GET /letters/{id}/pdf`, `POST /letters/{id}/void`.
