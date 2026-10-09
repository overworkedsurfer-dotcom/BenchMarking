"""Data dictionary for the synthetic investigative data warehouse.

The same definitions drive CSV writing (column order), SQLite loading (column
types), the ``describe_table`` tool, and the system prompt shown to models.
"""

from __future__ import annotations

TABLES: dict[str, dict] = {
    "persons": {
        "description": "Natural persons known to any source (bank KYC, tax, telecom, registries).",
        "columns": [
            ("person_id", "TEXT", "Unique person id (P + 6 digits)."),
            ("tin", "TEXT", "Taxpayer identification number (synthetic SSN format). Joins to tax tables."),
            ("first_name", "TEXT", "Given name. Names are NOT unique."),
            ("last_name", "TEXT", "Family name."),
            ("dob", "TEXT", "Date of birth, YYYY-MM-DD."),
            ("date_of_death", "TEXT", "Date of death if deceased, else NULL."),
            ("address_id", "TEXT", "Residential address -> addresses.address_id."),
            ("phone", "TEXT", "Phone number given to the bank at onboarding (KYC). May be stale."),
            ("occupation", "TEXT", "Self-described occupation."),
            ("employer_id", "TEXT", "Primary employer -> businesses.business_id (NULL if none)."),
        ],
    },
    "businesses": {
        "description": "Legal entities from corporate registries (US and offshore), including trusts.",
        "columns": [
            ("business_id", "TEXT", "Unique business id (B + 6 digits)."),
            ("ein", "TEXT", "Employer identification number (synthetic). Joins to tax tables as a TIN."),
            ("name", "TEXT", "Registered name."),
            ("entity_type", "TEXT", "LLC, Corporation, S-Corporation, Partnership, Trust, Foundation, Ltd, SA."),
            ("industry", "TEXT", "Declared industry."),
            ("jurisdiction", "TEXT", "US state code or ISO country code of registration."),
            ("address_id", "TEXT", "Registered address -> addresses.address_id."),
            ("incorporation_date", "TEXT", "YYYY-MM-DD."),
        ],
    },
    "addresses": {
        "description": "Physical / registered addresses. Several entities may share one address.",
        "columns": [
            ("address_id", "TEXT", "Unique address id."),
            ("line1", "TEXT", "Street line."),
            ("city", "TEXT", "City."),
            ("region", "TEXT", "US state code or region."),
            ("country", "TEXT", "ISO country code."),
            ("address_type", "TEXT", "residential, commercial, registered_agent."),
        ],
    },
    "ownership": {
        "description": (
            "Corporate registry roles. owner_id is a person_id or business_id; owned_id is a business_id. "
            "ownership_pct is the direct equity / beneficial interest (0-100) for roles shareholder, member, "
            "partner, beneficiary; it is NULL for control-only roles (director, officer, trustee, "
            "registered_agent). A role is active on a date if start_date <= date and (end_date IS NULL or "
            "end_date > date)."
        ),
        "columns": [
            ("owner_id", "TEXT", "Person or business holding the role."),
            ("owned_id", "TEXT", "Business in which the role is held."),
            ("role", "TEXT", "shareholder, member, partner, beneficiary, director, officer, trustee."),
            ("ownership_pct", "REAL", "Direct ownership percent (NULL for control-only roles)."),
            ("title", "TEXT", "Free-text title for officers (e.g. CFO, Procurement Director)."),
            ("start_date", "TEXT", "YYYY-MM-DD."),
            ("end_date", "TEXT", "YYYY-MM-DD or NULL if still active."),
        ],
    },
    "relationships": {
        "description": (
            "Family relationships between persons. relationship describes person_id_1 relative to "
            "person_id_2 (e.g. 'parent' means person_id_1 is a parent of person_id_2). Spouse and sibling "
            "rows are stored once per pair."
        ),
        "columns": [
            ("person_id_1", "TEXT", "Person."),
            ("person_id_2", "TEXT", "Person."),
            ("relationship", "TEXT", "spouse, parent, sibling."),
        ],
    },
    "accounts": {
        "description": "Bank accounts visible through the bank consortium feed (domestic and some foreign).",
        "columns": [
            ("account_id", "TEXT", "Unique account id (AC + 7 digits)."),
            ("bank_name", "TEXT", "Holding bank."),
            ("country", "TEXT", "ISO country code of the bank branch holding the account."),
            ("account_type", "TEXT", "checking, savings, business_checking, internal."),
            ("holder_id", "TEXT", "Account holder: person_id or business_id."),
            ("authorized_signer_id", "TEXT", "Additional person authorized to transact (NULL if none)."),
            ("open_date", "TEXT", "YYYY-MM-DD."),
            ("close_date", "TEXT", "YYYY-MM-DD or NULL."),
        ],
    },
    "transactions": {
        "description": (
            "All 2025 money movements. from_account is NULL for cash deposits; to_account is NULL for "
            "cash withdrawals. conducted_by is the person physically conducting a branch (teller) "
            "transaction; NULL for electronic/ATM activity."
        ),
        "columns": [
            ("txn_id", "TEXT", "Unique transaction id."),
            ("timestamp", "TEXT", "ISO-8601 local time as text, YYYY-MM-DDTHH:MM:SS (note the T: SQLite datetime() uses a space, so compare as text or use strftime('%Y-%m-%dT%H:%M:%S', ...))."),
            ("from_account", "TEXT", "Debited account (NULL for cash deposits)."),
            ("to_account", "TEXT", "Credited account (NULL for cash withdrawals)."),
            ("amount", "REAL", "USD amount."),
            ("channel", "TEXT", "ach, wire, international_wire, p2p, check, bill_payment, card_settlement, "
                                "cash_deposit, cash_withdrawal, interest."),
            ("branch_id", "TEXT", "Branch where a cash/teller transaction occurred."),
            ("conducted_by", "TEXT", "Person who conducted a teller transaction."),
            ("memo", "TEXT", "Free-text memo / reference."),
        ],
    },
    "branches": {
        "description": "Bank branches (location of cash activity).",
        "columns": [
            ("branch_id", "TEXT", "Branch id."),
            ("bank_name", "TEXT", "Bank."),
            ("city", "TEXT", "City."),
            ("region", "TEXT", "US state code."),
        ],
    },
    "logins": {
        "description": (
            "Online/mobile banking audit log. person_id is the customer whose credentials were used. "
            "Transfers initiated online carry txn_id."
        ),
        "columns": [
            ("event_id", "TEXT", "Unique event id."),
            ("timestamp", "TEXT", "ISO-8601 local time as text, YYYY-MM-DDTHH:MM:SS (note the T: SQLite datetime() uses a space, so compare as text or use strftime('%Y-%m-%dT%H:%M:%S', ...))."),
            ("person_id", "TEXT", "Customer credentials used."),
            ("device_id", "TEXT", "Device fingerprint."),
            ("ip_address", "TEXT", "Source IP."),
            ("ip_country", "TEXT", "Geolocated ISO country of the IP."),
            ("event_type", "TEXT", "login, transfer, add_payee, password_reset, change_phone."),
            ("txn_id", "TEXT", "Transaction initiated in this event (transfer events only)."),
        ],
    },
    "phones": {
        "description": (
            "Phone numbers from carrier records. subscriber_id is NULL for anonymous prepaid lines "
            "(common among ordinary customers too)."
        ),
        "columns": [
            ("phone_number", "TEXT", "E.164 number."),
            ("subscriber_id", "TEXT", "Registered subscriber person_id, or NULL (anonymous prepaid)."),
            ("carrier", "TEXT", "Carrier."),
            ("plan_type", "TEXT", "postpaid or prepaid."),
            ("activation_date", "TEXT", "YYYY-MM-DD."),
            ("deactivation_date", "TEXT", "YYYY-MM-DD or NULL."),
        ],
    },
    "calls": {
        "description": "Call detail records (voice calls and SMS) for 2025.",
        "columns": [
            ("call_id", "TEXT", "Unique record id."),
            ("timestamp", "TEXT", "ISO-8601 local time as text, YYYY-MM-DDTHH:MM:SS (note the T: SQLite datetime() uses a space, so compare as text or use strftime('%Y-%m-%dT%H:%M:%S', ...))."),
            ("caller", "TEXT", "Originating phone_number."),
            ("callee", "TEXT", "Receiving phone_number."),
            ("duration_sec", "INTEGER", "Duration in seconds (0 for SMS)."),
            ("call_type", "TEXT", "voice or sms."),
            ("caller_tower_id", "TEXT", "Cell tower serving the caller -> cell_towers.tower_id."),
        ],
    },
    "cell_towers": {
        "description": "Cell tower locations.",
        "columns": [
            ("tower_id", "TEXT", "Tower id."),
            ("city", "TEXT", "City."),
            ("region", "TEXT", "US state code."),
        ],
    },
    "tax_returns": {
        "description": (
            "Income tax returns for tax year 2025 (form 1040 for individuals; 1120/1120S/1065 for entities). "
            "For joint 1040s, spouse_tin is the second filer and income lines include both spouses. "
            "total_income = wages + interest + dividends + (business_gross_receipts - business_expenses) "
            "+ other_income."
        ),
        "columns": [
            ("return_id", "TEXT", "Unique return id."),
            ("tax_year", "INTEGER", "Tax year."),
            ("form_type", "TEXT", "1040, 1120, 1120S, 1065."),
            ("filer_tin", "TEXT", "Primary filer TIN (persons.tin or businesses.ein)."),
            ("spouse_tin", "TEXT", "Spouse TIN on a joint return, else NULL."),
            ("filing_status", "TEXT", "single, married_joint, married_separate, head_of_household, entity."),
            ("preparer_id", "TEXT", "Paid preparer -> preparers.preparer_id (NULL if self-prepared)."),
            ("wages", "REAL", "Wages reported."),
            ("interest", "REAL", "Interest income reported."),
            ("dividends", "REAL", "Dividend income reported."),
            ("business_gross_receipts", "REAL", "Gross receipts (Schedule C or entity return)."),
            ("business_expenses", "REAL", "Business expenses claimed."),
            ("other_income", "REAL", "Other income (rents, misc)."),
            ("total_income", "REAL", "Total income as defined above."),
            ("deductions", "REAL", "Standard or itemized deductions."),
            ("credits", "REAL", "Total credits claimed (incl. refundable credits)."),
            ("tax_liability", "REAL", "Tax after credits (may be negative when refundable credits exceed tax)."),
            ("withholding", "REAL", "Tax withheld / estimated payments."),
            ("refund_amount", "REAL", "Refund requested."),
            ("balance_due", "REAL", "Balance due."),
            ("refund_account_id", "TEXT", "Direct-deposit account for the refund (NULL = paper check)."),
            ("num_dependents", "INTEGER", "Number of dependents claimed."),
            ("filed_date", "TEXT", "YYYY-MM-DD."),
        ],
    },
    "return_dependents": {
        "description": "Dependents claimed on each return.",
        "columns": [
            ("return_id", "TEXT", "Return."),
            ("dependent_person_id", "TEXT", "Dependent person."),
        ],
    },
    "info_returns": {
        "description": (
            "Third-party information returns for tax year 2025: W-2 (wages), 1099-NEC (non-employee "
            "compensation), 1099-K (card/payment network receipts), 1099-INT, 1099-DIV, 1099-MISC."
        ),
        "columns": [
            ("info_return_id", "TEXT", "Unique id."),
            ("tax_year", "INTEGER", "Tax year."),
            ("form_type", "TEXT", "W-2, 1099-NEC, 1099-K, 1099-INT, 1099-DIV, 1099-MISC."),
            ("payer_tin", "TEXT", "Payer TIN (usually a business EIN)."),
            ("payee_tin", "TEXT", "Recipient TIN."),
            ("amount", "REAL", "Gross amount reported."),
        ],
    },
    "preparers": {
        "description": "Paid tax return preparers.",
        "columns": [
            ("preparer_id", "TEXT", "Preparer id (PTN + 6 digits)."),
            ("person_id", "TEXT", "The preparer as a person."),
            ("firm_business_id", "TEXT", "Preparer's firm (NULL for independents)."),
        ],
    },
    "assets": {
        "description": (
            "Real estate, vehicle, boat and aircraft records (deeds, DMV, registries). financing is cash, "
            "mortgage or loan; loan_amount is the financed portion. sale_* columns are set if sold."
        ),
        "columns": [
            ("asset_id", "TEXT", "Unique asset id."),
            ("asset_type", "TEXT", "real_estate, vehicle, boat, aircraft."),
            ("description", "TEXT", "Description."),
            ("owner_id", "TEXT", "Titled owner (person_id or business_id)."),
            ("purchase_date", "TEXT", "YYYY-MM-DD."),
            ("purchase_price", "REAL", "USD."),
            ("financing", "TEXT", "cash, mortgage, loan."),
            ("loan_amount", "REAL", "Financed amount (0 for cash)."),
            ("sale_date", "TEXT", "YYYY-MM-DD if sold."),
            ("sale_price", "REAL", "USD if sold."),
            ("address_id", "TEXT", "Location for real estate."),
        ],
    },
}

# Derived table built at load time (not shipped as CSV).
DERIVED_TABLES: dict[str, dict] = {
    "graph_edges": {
        "description": (
            "Convenience graph view derived from the base tables. Edge types: holds (holder->account), "
            "signer (person->account), owns (owner->business, weight=pct, role in detail), "
            "control (owner->business, control-only roles), relationship (person->person), "
            "employs (business->person), subscriber (person->phone), "
            "transfer (account->account, weight=total amount, count=#txns), "
            "called (phone->phone, count=#calls), uses_device (person->device, count=#events). "
            "Ownership edges include inactive roles; check start_date/end_date in detail."
        ),
        "columns": [
            ("src", "TEXT", "Source node id."),
            ("dst", "TEXT", "Destination node id."),
            ("edge_type", "TEXT", "Edge type (see description)."),
            ("weight", "REAL", "Amount or percent where meaningful."),
            ("count", "INTEGER", "Number of underlying records."),
            ("first_seen", "TEXT", "Earliest timestamp/date."),
            ("last_seen", "TEXT", "Latest timestamp/date."),
            ("detail", "TEXT", "Extra info (role, relationship, title, dates)."),
        ],
    },
}

ALL_TABLES = {**TABLES, **DERIVED_TABLES}


def column_names(table: str) -> list[str]:
    return [c[0] for c in ALL_TABLES[table]["columns"]]


def compact_schema() -> str:
    """One line per table: name(col, col, ...) — used in the system prompt."""
    lines = []
    for name, spec in ALL_TABLES.items():
        cols = ", ".join(c[0] for c in spec["columns"])
        lines.append(f"- {name}({cols})")
    return "\n".join(lines)
