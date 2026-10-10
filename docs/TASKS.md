# Task families

Each family is a **planted scheme**: a pattern of transactions, logins, calls, registry entries or tax records injected into a realistic background, plus **decoys**: innocent activity designed to look similar. Every task is checked by a reference solver (`fincrime_bench/solvers.py`) that derives the answer from the warehouse alone. The generator also asserts that rule-based answer keys contain exactly the planted cases.

Task prompts state the business question and the exact answer fields, never the method. `fincrime-bench tasks --show <task_id>` prints a prompt exactly as the model sees it.

Ids, people, amounts, dates and chain shapes change with the seed. The structure below holds for every seed.

---

## AML (anti-money laundering)

### `aml_layering_01..04`: trace business-email-compromise proceeds
A company's wire to a fraudster is pushed through 3–7 intermediary accounts. These are mules' new or existing personal accounts and freshly formed LLCs. Each intermediary keeps a 0.5–2.5% cut. The trail ends in teller cash withdrawals or an international wire to an offshore company.

| | 01 (easy) | 02 (medium) | 03 (hard) | 04 (hard) |
|---|---|---|---|---|
| hops | 3 | 4 | 5 + split | 6 + split |
| exit | cash | offshore wire | offshore wire | cash |
| beneficiary via | signer who withdrew the cash | offshore shell's shareholder | 2-layer offshore chain with an expired nominee shareholder | signer who withdrew the cash |
| distractors | – | one long-standing account with normal traffic | out-transfers before the money arrived, partial out-transfers | same |

**Skills:** amount and time matching across hops, splitting and recombining, recognising an exit, and resolving the controlling person rather than the nominal holder.

### `aml_structuring_01`: structured cash (population scan)
Find everyone who broke cash into deposits under the $10,000 Currency Transaction Report threshold. This includes "smurfs" depositing into someone else's account.

**Decoys:**
- people who made large reported deposits
- isolated near-threshold deposits, such as a single vehicle sale
- two near-threshold deposits months apart
- businesses' routine cash deposits

### `aml_smurfing_01`: smurfs and the real controller (easy)
Several people deposit $7–10k each into one LLC account, and the money then flows to one individual. Name the depositors and the controller. The decoy is the LLC's owner of record, who is a relative.

### `aml_mule_01/02`: romance-scam mule network
Start from one elderly victim's complaint. Map the mule accounts, the collector account and every victim. Then identify the operator from the online-banking audit log:

- **01:** the operator uses the same device for the mules' banking and for their own banking.
- **02:** each mule has its own device, but all share the operator's home IP. The operator lives alone, so no household members share that IP.

**Decoys:** small transfers from mules' families, recruited account holders, and the collector's nominee holder.

### `aml_ato_01`: account takeover (population scan)
Fraudulent transfers follow a credential reset from a never-before-seen device on a foreign IP, a new payee, and a quick transfer to a mule, with irregular timing. One attacker device is reused across two victims.

**Decoys:**
- travellers logging in, and sometimes resetting passwords or changing phones, from abroad on their usual device; the travel destinations include the countries attackers' IPs geolocate to
- phone upgrades with password resets
- a customer who resets their password on a new phone and wires a car dealer for a car they really bought

### `aml_roundtrip_01`: round-tripped investment (hard)
A company's "equity investment" from an offshore investor is the company's own money. It went out as a consulting fee to a US LLC fronted by the owner's relative, then through Cyprus and the BVI, and back. Report whether it is a round trip, the cycle, and the investor's ultimate beneficial owner, who is the company's owner.

---

## Corporate ownership

### `own_ubo_01..03`: ultimate beneficial owners
List every natural person with at least 25% effective ownership. Effective ownership is the product of percentages along a chain, summed over all chains.

- **01 (easy):** a holding LLC plus direct members. A 10% member and a non-owning "Managing Director" are decoys.
- **02 (medium):** two paths to the same person, a foreign holding layer with a nominee director, and a person whose 28% comes only through an indirect chain.
- **03 (hard):** a US LLC owned 80% by a Cyprus company, which is owned 100% by a BVI company. The BVI company is owned 70% by a Cayman trust (beneficiaries 75% / 25%) and 30% by a Delaware LLC. The 25% trust beneficiary also owns that Delaware LLC, and reaches 38% only by **summing both paths** (14% + 24%); neither path alone crosses 25%. Traps:
  - an expired 100% shareholder of record
  - a trustee, which is a control-only role
  - a direct 20% member just under the threshold
  - nominee directors

### `own_kickback_01/02`: insider kickback vendor
A company pays a new "Strategic Consulting LLC" every month. That LLC forwards the money offshore to a holding company owned by:

- **01:** the company's own officer, who is also the LLC's manager and account signer.
- **02:** the officer's **spouse**. The LLC's director and signer is a nominee, so the link to the insider is a family relationship.

**Decoys:** two other genuinely new vendors with staff, payroll and tax returns. Background payments to vendors owned by company staff are removed, so the insider link is unique.

---

## Telecom (call chaining)

### `tel_chain_01/02`: from a crew to its boss
Four crew members call a prepaid "handler" number, the only contact they all share. The handler reports to the boss:

- **01:** the handler calls the boss's registered phone.
- **02:** the handler calls the boss's anonymous burner. The boss must be found by **co-location**: their personal phone places calls from the same cell tower within minutes of the burner's calls, many times over.

**Decoys:**
- the handler's occasional calls to random people
- in 02, a family member of the boss who is sometimes at the same tower, but far less consistently
- subscribers of family-plan lines can be someone other than the user

### `tel_burner_01/02`: replacement phones
A suspect drops their registered phone on a given date and continues on an anonymous prepaid number. In 02 they switch twice. The replacement keeps the suspect's inner circle of contacts.

**Decoys:**
- a relative who switches numbers a few days later and shares some contacts
- a stray new prepaid number touching one of the suspect's contacts
- ordinary background number changes

---

## Tax

### `tax_unreported_01`: information-return matching (population scan)
Find taxpayers whose return falls at least $10k short of their W-2/1099 total, and quantify the shortfall. Planted cases:

- an omitted 1099-NEC
- an unreported second W-2 job
- unreported gig-platform 1099-K income
- understated wages
- two non-filers

Information returns are gross amounts, so the comparison is against gross income (receipts before expenses).

**Traps:**
- joint returns: a spouse's W-2 appears under the spouse's TIN, not the filer's
- income reported on a different line, so the total is unchanged
- small omissions under the threshold
- rental income not covered by any 1099

### `tax_lifestyle_01`: expenditure method (population scan)
Find people whose 2025 cash outlays on assets exceed reported income and documented sources by more than $250k. Planted cases include:

- a house paid for by offshore wires
- two supercars and a boat bought with large cash deposits
- a condo with no visible funding
- a heavily financed house paid down from an LLC's distributions

**Decoys:**
- high earners
- mortgage buyers
- people who sold a home and bought a cheaper one for cash

### `tax_networth_01`: net-worth computation
Apply a fully specified formula to one taxpayer. The formula requires using the **joint** return's income, netting the financed part of a vehicle purchase, and subtracting a vehicle sale.

### `tax_preparer_01`: refund mill (hard)
One preparer inflates clients' credits and deductions and redirects 12 refunds into three accounts: their own, a relative's account on which they are the signer, and their new LLC's account.

**Decoy:** a preparer whose low-income clients legitimately get large refundable credits, deposited to their own accounts.

### `tax_dependents_01`: invalid dependent claims (easy)
Find duplicate claims, for example by a grandparent of a child already claimed by a parent, and dependents who died before 2025.

**Decoy:** a parent who died during 2025, which is still valid.

### `tax_skimming_01`: bank-deposits method (hard)
Salons, laundromats and car washes report their card and customer receipts but only a sliver of their cash, so they understate by 28–60%. Quantify the gap between deposited revenue and reported receipts.

**Decoys:**
- businesses whose deposits include SBA loan proceeds, owner capital contributions or refunds; these are not revenue, and some background businesses have them too
- sweeps between a business's own accounts

---

## Cross-domain investigations

### `x_capstone_01`: from a scam call to the organizer's tax return
The starting point is an elderly victim and the unknown number that called them. The chain to follow:

1. calls from the burner, each followed within the hour by a payment
2. the victims; people called who did not pay are decoys
3. the mule accounts
4. the collector LLC, whose office manager is a decoy
5. its offshore parent
6. the organizer
7. the "management fees" the organizer received, absent from their W-2-only tax return

### `x_capstone_02`: source of funds for a cash home purchase
The starting point is a taxpayer whose cash home purchase dwarfs their income. The chain to follow:

1. international wires labelled "loan disbursement" from an offshore account
2. that account's funding by a US "marketing" LLC; the taxpayer is the LLC's signer, but a relative is its member of record
3. the LLC's real funding source: monthly invoices to the taxpayer's own employer, where the taxpayer is an officer

Report the funding account, conduit, source company, nominee, amount diverted and unreported income.

---

## Longevity sessions

Four sessions put every task above into long, multi-round conversations (`sessions.jsonl`). Each opens with a **case file** of at least 32k tokens and then asks its rounds one at a time:

| Session | Round plan (CF = case file, tools off · INV = tools on · R = recall · S = synthesis) |
|---|---|
| `long_01` Laundering desk | 1 CF `aml_layering_03` · 2 INV `aml_layering_01` · 3 INV `aml_layering_02` · 4 CF `aml_smurfing_01` · 5 INV `aml_roundtrip_01` · 6 INV `own_ubo_02` · 7 CF `aml_layering_04` · 8 R round-1 txn · 9 R round-3 exit account · 10 S beneficiaries/controllers |
| `long_02` Telecom and fraud rings | 1 CF `tel_chain_01` · 2 INV `aml_mule_01` · 3 INV `tel_chain_02` · 4 CF `tel_burner_01` · 5 INV `x_capstone_01` · 6 INV `tel_burner_02` · 7 R round-1 crew · 8 R round-2 collector · 9 S bosses/controller/organizer |
| `long_03` Tax desk | 1 CF `tax_networth_01` · 2 INV `tax_unreported_01` · 3 CF `tax_dependents_01` · 4 INV `tax_skimming_01` · 5 INV `tax_lifestyle_01` · 6 CF `tax_preparer_01` · 7 INV `x_capstone_02` · 8 R round-1 taxpayer · 9 R round-6 preparer · 10 S flagged people and nominee |
| `long_04` Corporate and account security | 1 CF `own_ubo_01` · 2 INV `aml_ato_01` · 3 CF `aml_structuring_01` · 4 INV `own_kickback_01` · 5 INV `aml_mule_02` · 6 CF `own_ubo_03` · 7 INV `own_kickback_02` · 8 R round-1 company · 9 R round-4 vendor · 10 S insiders/controller/owner |

**Design points:**
- **Long-range retrieval.** Case-file rounds appear early (round 1) and late (rounds 6–7). Late case-file rounds make the model retrieve evidence from tens of thousands of tokens back, after several tool-heavy investigations.
- **Hidden evidence.** Each case file buries the evidence among unrelated but complete records of the same kinds (other accounts' statements, other phones' calls, other companies' registry entries, other tax units). The case file states that it is complete for the entities its questions concern.
- **Validated.** `validate` parses each case file back into tables and runs the reference solvers on that extract alone, so every case-file round is provably answerable from the text the model receives.
- **Tools off where it matters.** In tool-disabled rounds a native-tools model is only offered `submit_answer`. Any other tool call (native or text mode) returns an error telling the model to answer from the conversation.
