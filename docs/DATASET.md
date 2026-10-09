# Dataset

`data/public/` is one synthetic world (seed 20251, generator 1.0.0) covering calendar year 2025. It contains 17 CSV tables plus `tasks.jsonl` (prompts and answer schemas), `answers.jsonl` (answer keys, decoys and notes) and `manifest.json` (row counts and SHA-256 hashes). The harness loads the CSVs into a read-only SQLite warehouse, cached under `data/<split>/.cache/`, and derives a `graph_edges` table.

## How the world is generated

`fincrime_bench/generator/` builds the world in order, from a single `random.Random(seed)`:

1. **Geography.** 12 US cities, each with bank branches for 3 domestic banks and 5 cell towers. There are also registered-agent addresses in DE and WY, and offshore addresses in BVI, Cayman, Panama, Cyprus, Seychelles, Belize and UAE.
2. **People.** About 2,400 people in households (singles, couples, families, elderly). Relationships include spouses, parents and children (also across households) and siblings. Each person has an employment status, a salary and a synthetic SSN-format TIN (`9xx-00-xxxx`, never a valid real SSN).
3. **Businesses.** About 370 companies in 16 industries, with owners (sometimes through family holding companies), officers, employees and revenue. The rest are infrastructure (utilities, card issuers, a payment processor, lenders, title companies, brokers, dealers, banks), foreign suppliers and legitimate offshore subsidiaries.
4. **Banking.** Accounts (some opened during 2025, some with family members as authorized signers) and a year of transactions:
   - payroll, rent and mortgage, utilities, card payments, person-to-person transfers, ATM and teller cash
   - card settlements and cash deposits for merchants, business-to-business invoices, owner draws, SBA loans, capital contributions, sweeps between a business's own accounts
   - interest, international supplier payments, intercompany flows, remittances, gifts and fund subscriptions
5. **Online banking.** Devices (including shared household devices and phone upgrades), home and mobile IPs, travel abroad, password resets, payees and transfers.
6. **Telecom.** Phones (postpaid and prepaid, family plans, number changes) and calls driven by a social graph of household, relatives, coworkers and friends, plus random noise calls.
7. **Assets.** Homes, vehicles, boats and commercial property, with financing and 2025 purchases and sales consistent with income.
8. **Tax.** Information returns (W-2, 1099-NEC/K/INT/DIV/MISC, including gig-platform 1099-Ks) derived from the activity above, then honest 1040s (single, joint, separate, head of household; dependents; paid preparers; refunds) and entity returns (1120/1120S/1065) whose receipts match deposited revenue.
9. **Schemes.** Each scheme reserves its actors (so schemes never overlap), injects records and decoys, and registers a task. Rule-based answer keys are computed after all planting, and the generator checks that they equal the planted cases.

**Design rule:** every feature a scheme relies on must also occur in legitimate background activity. That covers international wires, accounts opened in 2025, newly incorporated LLCs, offshore entities, teller cash, cash deposits over $10k and near the threshold, prepaid phones activated in 2025, password resets from abroad, third-party signers, owner draws, "management fee" memos, loans and more. Models therefore have to follow the specific evidence chain rather than filter on a rare value.

Ids are random rather than sequential, and rows are sorted by id or timestamp, so creation order (and therefore what was planted) cannot be inferred.

## Data dictionary

## persons

Natural persons known to any source (bank KYC, tax, telecom, registries).

| column | type | description |
|---|---|---|
| `person_id` | TEXT | Unique person id (P + 6 digits). |
| `tin` | TEXT | Taxpayer identification number (synthetic SSN format). Joins to tax tables. |
| `first_name` | TEXT | Given name. Names are NOT unique. |
| `last_name` | TEXT | Family name. |
| `dob` | TEXT | Date of birth, YYYY-MM-DD. |
| `date_of_death` | TEXT | Date of death if deceased, else NULL. |
| `address_id` | TEXT | Residential address -> addresses.address_id. |
| `phone` | TEXT | Phone number given to the bank at onboarding (KYC). May be stale. |
| `occupation` | TEXT | Self-described occupation. |
| `employer_id` | TEXT | Primary employer -> businesses.business_id (NULL if none). |

## businesses

Legal entities from corporate registries (US and offshore), including trusts.

| column | type | description |
|---|---|---|
| `business_id` | TEXT | Unique business id (B + 6 digits). |
| `ein` | TEXT | Employer identification number (synthetic). Joins to tax tables as a TIN. |
| `name` | TEXT | Registered name. |
| `entity_type` | TEXT | LLC, Corporation, S-Corporation, Partnership, Trust, Foundation, Ltd, SA. |
| `industry` | TEXT | Declared industry. |
| `jurisdiction` | TEXT | US state code or ISO country code of registration. |
| `address_id` | TEXT | Registered address -> addresses.address_id. |
| `incorporation_date` | TEXT | YYYY-MM-DD. |

## addresses

Physical / registered addresses. Several entities may share one address.

| column | type | description |
|---|---|---|
| `address_id` | TEXT | Unique address id. |
| `line1` | TEXT | Street line. |
| `city` | TEXT | City. |
| `region` | TEXT | US state code or region. |
| `country` | TEXT | ISO country code. |
| `address_type` | TEXT | residential, commercial, registered_agent. |

## ownership

Corporate registry roles. owner_id is a person_id or business_id; owned_id is a business_id. ownership_pct is the direct equity / beneficial interest (0-100) for roles shareholder, member, partner, beneficiary; it is NULL for control-only roles (director, officer, trustee, registered_agent). A role is active on a date if start_date <= date and (end_date IS NULL or end_date > date).

| column | type | description |
|---|---|---|
| `owner_id` | TEXT | Person or business holding the role. |
| `owned_id` | TEXT | Business in which the role is held. |
| `role` | TEXT | shareholder, member, partner, beneficiary, director, officer, trustee. |
| `ownership_pct` | REAL | Direct ownership percent (NULL for control-only roles). |
| `title` | TEXT | Free-text title for officers (e.g. CFO, Procurement Director). |
| `start_date` | TEXT | YYYY-MM-DD. |
| `end_date` | TEXT | YYYY-MM-DD or NULL if still active. |

## relationships

Family relationships between persons. relationship describes person_id_1 relative to person_id_2 (e.g. 'parent' means person_id_1 is a parent of person_id_2). Spouse and sibling rows are stored once per pair.

| column | type | description |
|---|---|---|
| `person_id_1` | TEXT | Person. |
| `person_id_2` | TEXT | Person. |
| `relationship` | TEXT | spouse, parent, sibling. |

## accounts

Bank accounts visible through the bank consortium feed (domestic and some foreign).

| column | type | description |
|---|---|---|
| `account_id` | TEXT | Unique account id (AC + 7 digits). |
| `bank_name` | TEXT | Holding bank. |
| `country` | TEXT | ISO country code of the bank branch holding the account. |
| `account_type` | TEXT | checking, savings, business_checking, internal. |
| `holder_id` | TEXT | Account holder: person_id or business_id. |
| `authorized_signer_id` | TEXT | Additional person authorized to transact (NULL if none). |
| `open_date` | TEXT | YYYY-MM-DD. |
| `close_date` | TEXT | YYYY-MM-DD or NULL. |

## transactions

All 2025 money movements. from_account is NULL for cash deposits; to_account is NULL for cash withdrawals. conducted_by is the person physically conducting a branch (teller) transaction; NULL for electronic/ATM activity.

| column | type | description |
|---|---|---|
| `txn_id` | TEXT | Unique transaction id. |
| `timestamp` | TEXT | ISO-8601 local time as text, YYYY-MM-DDTHH:MM:SS (note the T: SQLite datetime() uses a space, so compare as text or use strftime('%Y-%m-%dT%H:%M:%S', ...)). |
| `from_account` | TEXT | Debited account (NULL for cash deposits). |
| `to_account` | TEXT | Credited account (NULL for cash withdrawals). |
| `amount` | REAL | USD amount. |
| `channel` | TEXT | ach, wire, international_wire, p2p, check, bill_payment, card_settlement, cash_deposit, cash_withdrawal, interest. |
| `branch_id` | TEXT | Branch where a cash/teller transaction occurred. |
| `conducted_by` | TEXT | Person who conducted a teller transaction. |
| `memo` | TEXT | Free-text memo / reference. |

## branches

Bank branches (location of cash activity).

| column | type | description |
|---|---|---|
| `branch_id` | TEXT | Branch id. |
| `bank_name` | TEXT | Bank. |
| `city` | TEXT | City. |
| `region` | TEXT | US state code. |

## logins

Online/mobile banking audit log. person_id is the customer whose credentials were used. Transfers initiated online carry txn_id.

| column | type | description |
|---|---|---|
| `event_id` | TEXT | Unique event id. |
| `timestamp` | TEXT | ISO-8601 local time as text, YYYY-MM-DDTHH:MM:SS (note the T: SQLite datetime() uses a space, so compare as text or use strftime('%Y-%m-%dT%H:%M:%S', ...)). |
| `person_id` | TEXT | Customer credentials used. |
| `device_id` | TEXT | Device fingerprint. |
| `ip_address` | TEXT | Source IP. |
| `ip_country` | TEXT | Geolocated ISO country of the IP. |
| `event_type` | TEXT | login, transfer, add_payee, password_reset, change_phone. |
| `txn_id` | TEXT | Transaction initiated in this event (transfer events only). |

## phones

Phone numbers from carrier records. subscriber_id is NULL for anonymous prepaid lines (common among ordinary customers too).

| column | type | description |
|---|---|---|
| `phone_number` | TEXT | E.164 number. |
| `subscriber_id` | TEXT | Registered subscriber person_id, or NULL (anonymous prepaid). |
| `carrier` | TEXT | Carrier. |
| `plan_type` | TEXT | postpaid or prepaid. |
| `activation_date` | TEXT | YYYY-MM-DD. |
| `deactivation_date` | TEXT | YYYY-MM-DD or NULL. |

## calls

Call detail records (voice calls and SMS) for 2025.

| column | type | description |
|---|---|---|
| `call_id` | TEXT | Unique record id. |
| `timestamp` | TEXT | ISO-8601 local time as text, YYYY-MM-DDTHH:MM:SS (note the T: SQLite datetime() uses a space, so compare as text or use strftime('%Y-%m-%dT%H:%M:%S', ...)). |
| `caller` | TEXT | Originating phone_number. |
| `callee` | TEXT | Receiving phone_number. |
| `duration_sec` | INTEGER | Duration in seconds (0 for SMS). |
| `call_type` | TEXT | voice or sms. |
| `caller_tower_id` | TEXT | Cell tower serving the caller -> cell_towers.tower_id. |

## cell_towers

Cell tower locations.

| column | type | description |
|---|---|---|
| `tower_id` | TEXT | Tower id. |
| `city` | TEXT | City. |
| `region` | TEXT | US state code. |

## tax_returns

Income tax returns for tax year 2025 (form 1040 for individuals; 1120/1120S/1065 for entities). For joint 1040s, spouse_tin is the second filer and income lines include both spouses. total_income = wages + interest + dividends + (business_gross_receipts - business_expenses) + other_income.

| column | type | description |
|---|---|---|
| `return_id` | TEXT | Unique return id. |
| `tax_year` | INTEGER | Tax year. |
| `form_type` | TEXT | 1040, 1120, 1120S, 1065. |
| `filer_tin` | TEXT | Primary filer TIN (persons.tin or businesses.ein). |
| `spouse_tin` | TEXT | Spouse TIN on a joint return, else NULL. |
| `filing_status` | TEXT | single, married_joint, married_separate, head_of_household, entity. |
| `preparer_id` | TEXT | Paid preparer -> preparers.preparer_id (NULL if self-prepared). |
| `wages` | REAL | Wages reported. |
| `interest` | REAL | Interest income reported. |
| `dividends` | REAL | Dividend income reported. |
| `business_gross_receipts` | REAL | Gross receipts (Schedule C or entity return). |
| `business_expenses` | REAL | Business expenses claimed. |
| `other_income` | REAL | Other income (rents, misc). |
| `total_income` | REAL | Total income as defined above. |
| `deductions` | REAL | Standard or itemized deductions. |
| `credits` | REAL | Total credits claimed (incl. refundable credits). |
| `tax_liability` | REAL | Tax after credits (may be negative when refundable credits exceed tax). |
| `withholding` | REAL | Tax withheld / estimated payments. |
| `refund_amount` | REAL | Refund requested. |
| `balance_due` | REAL | Balance due. |
| `refund_account_id` | TEXT | Direct-deposit account for the refund (NULL = paper check). |
| `num_dependents` | INTEGER | Number of dependents claimed. |
| `filed_date` | TEXT | YYYY-MM-DD. |

## return_dependents

Dependents claimed on each return.

| column | type | description |
|---|---|---|
| `return_id` | TEXT | Return. |
| `dependent_person_id` | TEXT | Dependent person. |

## info_returns

Third-party information returns for tax year 2025: W-2 (wages), 1099-NEC (non-employee compensation), 1099-K (card/payment network receipts), 1099-INT, 1099-DIV, 1099-MISC.

| column | type | description |
|---|---|---|
| `info_return_id` | TEXT | Unique id. |
| `tax_year` | INTEGER | Tax year. |
| `form_type` | TEXT | W-2, 1099-NEC, 1099-K, 1099-INT, 1099-DIV, 1099-MISC. |
| `payer_tin` | TEXT | Payer TIN (usually a business EIN). |
| `payee_tin` | TEXT | Recipient TIN. |
| `amount` | REAL | Gross amount reported. |

## preparers

Paid tax return preparers.

| column | type | description |
|---|---|---|
| `preparer_id` | TEXT | Preparer id (PTN + 6 digits). |
| `person_id` | TEXT | The preparer as a person. |
| `firm_business_id` | TEXT | Preparer's firm (NULL for independents). |

## assets

Real estate, vehicle, boat and aircraft records (deeds, DMV, registries). financing is cash, mortgage or loan; loan_amount is the financed portion. sale_* columns are set if sold.

| column | type | description |
|---|---|---|
| `asset_id` | TEXT | Unique asset id. |
| `asset_type` | TEXT | real_estate, vehicle, boat, aircraft. |
| `description` | TEXT | Description. |
| `owner_id` | TEXT | Titled owner (person_id or business_id). |
| `purchase_date` | TEXT | YYYY-MM-DD. |
| `purchase_price` | REAL | USD. |
| `financing` | TEXT | cash, mortgage, loan. |
| `loan_amount` | REAL | Financed amount (0 for cash). |
| `sale_date` | TEXT | YYYY-MM-DD if sold. |
| `sale_price` | REAL | USD if sold. |
| `address_id` | TEXT | Location for real estate. |

## graph_edges

Convenience graph view derived from the base tables. Edge types: holds (holder->account), signer (person->account), owns (owner->business, weight=pct, role in detail), control (owner->business, control-only roles), relationship (person->person), employs (business->person), subscriber (person->phone), transfer (account->account, weight=total amount, count=#txns), called (phone->phone, count=#calls), uses_device (person->device, count=#events). Ownership edges include inactive roles; check start_date/end_date in detail.

| column | type | description |
|---|---|---|
| `src` | TEXT | Source node id. |
| `dst` | TEXT | Destination node id. |
| `edge_type` | TEXT | Edge type (see description). |
| `weight` | REAL | Amount or percent where meaningful. |
| `count` | INTEGER | Number of underlying records. |
| `first_seen` | TEXT | Earliest timestamp/date. |
| `last_seen` | TEXT | Latest timestamp/date. |
| `detail` | TEXT | Extra info (role, relationship, title, dates). |

