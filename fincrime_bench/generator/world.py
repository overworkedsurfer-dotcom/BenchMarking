"""Synthetic world: background population and normal (legitimate) activity.

Everything is driven by a single ``random.Random(seed)`` so a seed fully
determines the dataset. Never iterate over a ``set`` of strings here (string
hashing is randomized per process); use lists / dicts (insertion ordered).
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import date, datetime, timedelta

from ..schema import TABLES
from . import names as N

YEAR = 2025
Y_START = datetime(YEAR, 1, 1)
Y_END = datetime(YEAR, 12, 31, 23, 59, 59)


def d2s(d) -> str | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime("%Y-%m-%d")


def dt2s(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%S")


def money(x: float) -> float:
    return round(float(x), 2)


class IdGen:
    """Random (non-sequential) unique ids so creation order never leaks."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.used: set[str] = set()

    def new(self, prefix: str, digits: int) -> str:
        lo, hi = 10 ** (digits - 1), 10 ** digits - 1
        while True:
            v = f"{prefix}{self.rng.randint(lo, hi)}"
            if v not in self.used:
                self.used.add(v)
                return v

    def tin(self) -> str:
        while True:
            v = f"9{self.rng.randint(10, 99)}-00-{self.rng.randint(1000, 9999)}"
            if v not in self.used:
                self.used.add(v)
                return v

    def ein(self) -> str:
        while True:
            v = f"00-{self.rng.randint(1000000, 9999999)}"
            if v not in self.used:
                self.used.add(v)
                return v

    def phone(self) -> str:
        while True:
            v = f"+1555{self.rng.randint(1000000, 9999999)}"
            if v not in self.used:
                self.used.add(v)
                return v


def poisson(rng: random.Random, lam: float) -> int:
    if lam <= 0:
        return 0
    if lam > 30:
        return max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
    L, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= L:
            return k
        k += 1


class World:
    def __init__(self, seed: int, scale: float = 1.0):
        self.seed = seed
        self.scale = scale
        self.rng = random.Random(seed)
        self.ids = IdGen(self.rng)
        self.n_persons = int(2400 * scale)

        self.tables: dict[str, list[dict]] = {name: [] for name in TABLES}
        self.P: dict[str, dict] = {}
        self.B: dict[str, dict] = {}
        self.A: dict[str, dict] = {}
        self.ADDR: dict[str, dict] = {}
        self.PHONE: dict[str, dict] = {}
        self.TIN: dict[str, str] = {}  # tin/ein -> entity id
        self.accounts_of: dict[str, list[str]] = defaultdict(list)
        self.infra: dict[str, list[str]] = defaultdict(list)  # kind -> business ids
        self.branches_by: dict[tuple, list[str]] = defaultdict(list)  # (bank, city) -> branch ids
        self.towers_by_city: dict[str, list[str]] = defaultdict(list)
        self.reserved: set[str] = set()
        self.txn_by_id: dict[str, dict] = {}
        self.tasks: list[dict] = []
        self.answers: list[dict] = []
        self.hh_counter = 0
        self.preparer_ids: list[str] = []
        self.deferred: dict = {}
        self.expected: dict[tuple[str, str], list[str]] = {}  # planted positives for rule-derived golds
        self.nominees: list[str] = []

    # ------------------------------------------------------------------ utils
    @property
    def persons(self) -> list[dict]:
        return self.tables["persons"]

    @property
    def businesses(self) -> list[dict]:
        return self.tables["businesses"]

    def r_date(self, start: date, end: date) -> date:
        return start + timedelta(days=self.rng.randint(0, (end - start).days))

    def r_dt(self, start: datetime, end: datetime) -> datetime:
        return start + timedelta(seconds=self.rng.randint(0, int((end - start).total_seconds())))

    def r_daytime(self, day: date, lo: int = 8, hi: int = 20) -> datetime:
        return datetime(day.year, day.month, day.day, self.rng.randint(lo, hi - 1),
                        self.rng.randint(0, 59), self.rng.randint(0, 59))

    def age(self, p: dict) -> int:
        return p["_age"]

    def expect(self, task_id: str, field_name: str, ids: list[str]) -> None:
        self.expected.setdefault((task_id, field_name), []).extend(ids)

    def is_reserved(self, eid: str) -> bool:
        return eid in self.reserved

    def reserve(self, *eids: str) -> None:
        for e in eids:
            self.reserved.add(e)

    # ------------------------------------------------------------- factories
    def new_address(self, city: str, region: str, kind: str = "residential", country: str = "US",
                    line1: str | None = None) -> str:
        aid = self.ids.new("AD", 6)
        if line1 is None:
            num = self.rng.randint(10, 9899)
            line1 = f"{num} {self.rng.choice(N.STREETS)} {self.rng.choice(N.STREET_SUFFIX)}"
            if kind == "commercial" and self.rng.random() < 0.4:
                line1 += f" Ste {self.rng.randint(100, 950)}"
        row = {"address_id": aid, "line1": line1, "city": city, "region": region, "country": country,
               "address_type": kind}
        self.tables["addresses"].append(row)
        self.ADDR[aid] = row
        return aid

    def new_person(self, first: str, last: str, age: int, address_id: str, city: str, state: str,
                   occupation: str = "", **private) -> dict:
        pid = self.ids.new("P", 6)
        tin = self.ids.tin()
        birth = date(YEAR - age, 1, 1) + timedelta(days=self.rng.randint(0, 364))
        # age as of 2025-12-31
        row = {
            "person_id": pid, "tin": tin, "first_name": first, "last_name": last, "dob": d2s(birth),
            "date_of_death": None, "address_id": address_id, "phone": None, "occupation": occupation,
            "employer_id": None,
            "_age": age, "_city": city, "_state": state, "_hh": None, "_status": "none", "_salary": 0.0,
            "_spouse": None, "_children": [], "_parents": [], "_contacts": [], "_phones": [],
            "_devices": [], "_home_ip": None, "_online": False, "_w2": [], "_nec": [], "_extra_se": 0.0,
            "_int": [], "_div": [], "_rent_income": 0.0, "_homeowner": False, "_mortgage_pay": 0.0,
            "_card": False,
        }
        row.update(private)
        self.tables["persons"].append(row)
        self.P[pid] = row
        self.TIN[tin] = pid
        return row

    def new_business(self, name: str, entity_type: str, industry: str, jurisdiction: str, address_id: str,
                     inc_date: date, city: str | None = None, **private) -> dict:
        bid = self.ids.new("B", 6)
        ein = self.ids.ein()
        row = {
            "business_id": bid, "ein": ein, "name": name, "entity_type": entity_type, "industry": industry,
            "jurisdiction": jurisdiction, "address_id": address_id, "incorporation_date": d2s(inc_date),
            "_city": city, "_employees": [], "_owners": [], "_infra": None, "_rev": 0.0, "_pay_freq": 12,
            "_ind": industry, "_shell": False, "_files_return": True,
        }
        row.update(private)
        self.tables["businesses"].append(row)
        self.B[bid] = row
        self.TIN[ein] = bid
        return row

    def add_owner(self, owner_id: str, owned_id: str, role: str, pct: float | None, start: date,
                  end: date | None = None, title: str | None = None) -> None:
        self.tables["ownership"].append({
            "owner_id": owner_id, "owned_id": owned_id, "role": role,
            "ownership_pct": None if pct is None else round(pct, 2), "title": title,
            "start_date": d2s(start), "end_date": d2s(end),
        })
        if role in ("shareholder", "member", "partner", "beneficiary"):
            self.B[owned_id]["_owners"].append((owner_id, pct))

    def add_rel(self, a: str, b: str, kind: str) -> None:
        self.tables["relationships"].append({"person_id_1": a, "person_id_2": b, "relationship": kind})

    def new_account(self, holder_id: str, bank: str, country: str, acct_type: str, open_date: date,
                    signer: str | None = None, close_date: date | None = None) -> str:
        aid = self.ids.new("AC", 7)
        row = {"account_id": aid, "bank_name": bank, "country": country, "account_type": acct_type,
               "holder_id": holder_id, "authorized_signer_id": signer, "open_date": d2s(open_date),
               "close_date": d2s(close_date)}
        self.tables["accounts"].append(row)
        self.A[aid] = row
        self.accounts_of[holder_id].append(aid)
        return aid

    def add_txn(self, ts: datetime, frm: str | None, to: str | None, amount: float, channel: str,
                memo: str = "", branch: str | None = None, conducted_by: str | None = None) -> str:
        tid = self.ids.new("TX", 9)
        row = {"txn_id": tid, "timestamp": dt2s(ts), "from_account": frm, "to_account": to,
               "amount": money(amount), "channel": channel, "branch_id": branch, "conducted_by": conducted_by,
               "memo": memo or None, "_ts": ts}
        self.tables["transactions"].append(row)
        self.txn_by_id[tid] = row
        return tid

    def add_login(self, ts: datetime, pid: str, device: str, ip: str, country: str, event: str,
                  txn_id: str | None = None) -> str:
        eid = self.ids.new("LG", 8)
        self.tables["logins"].append({"event_id": eid, "timestamp": dt2s(ts), "person_id": pid,
                                      "device_id": device, "ip_address": ip, "ip_country": country,
                                      "event_type": event, "txn_id": txn_id, "_ts": ts})
        return eid

    def new_phone(self, subscriber: str | None, plan: str, activation: date, deactivation: date | None = None,
                  user: str | None = None) -> str:
        num = self.ids.phone()
        row = {"phone_number": num, "subscriber_id": subscriber, "carrier": self.rng.choice(N.CARRIERS),
               "plan_type": plan, "activation_date": d2s(activation), "deactivation_date": d2s(deactivation),
               "_user": user, "_act": activation, "_deact": deactivation}
        self.tables["phones"].append(row)
        self.PHONE[num] = row
        if user:
            self.P[user]["_phones"].append(num)
        return num

    def add_call(self, ts: datetime, caller: str, callee: str, call_type: str | None = None,
                 duration: int | None = None, tower: str | None = None, city: str | None = None) -> str:
        cid = self.ids.new("CL", 8)
        if call_type is None:
            call_type = "voice" if self.rng.random() < 0.62 else "sms"
        if duration is None:
            duration = 0 if call_type == "sms" else max(5, int(self.rng.lognormvariate(4.6, 1.0)))
        if call_type == "sms":
            duration = 0
        if tower is None:
            tower = self.rng.choice(self.towers_by_city[city or self.rng.choice(list(self.towers_by_city))])
        self.tables["calls"].append({"call_id": cid, "timestamp": dt2s(ts), "caller": caller, "callee": callee,
                                     "duration_sec": duration, "call_type": call_type,
                                     "caller_tower_id": tower, "_ts": ts})
        return cid

    def new_device(self) -> str:
        return self.ids.new("DV", 6)

    def home_ip(self) -> str:
        return f"10.{self.rng.randint(0, 255)}.{self.rng.randint(0, 255)}.{self.rng.randint(1, 254)}"

    def mobile_ip(self) -> str:
        return f"100.{self.rng.randint(64, 127)}.{self.rng.randint(0, 255)}.{self.rng.randint(1, 254)}"

    def foreign_ip(self) -> str:
        return f"172.{self.rng.randint(16, 31)}.{self.rng.randint(0, 255)}.{self.rng.randint(1, 254)}"

    # ---------------------------------------------------------------- lookups
    def checking_of(self, holder: str) -> str | None:
        for a in self.accounts_of.get(holder, []):
            if self.A[a]["account_type"] in ("checking", "business_checking") and self.A[a]["country"] == "US" \
                    and self.A[a]["close_date"] is None:
                return a
        return None

    def bank_of(self, account_id: str) -> str:
        return self.A[account_id]["bank_name"]

    def branch_for(self, account_id: str, city: str) -> str:
        bank = self.bank_of(account_id)
        opts = self.branches_by.get((bank, city)) or self.branches_by[(bank, "*")]
        return self.rng.choice(opts)

    def active_phone(self, pid: str, when: date) -> str | None:
        for num in self.P[pid]["_phones"]:
            ph = self.PHONE[num]
            if ph["_act"] <= when and (ph["_deact"] is None or ph["_deact"] > when):
                return num
        return None

    def tower_in(self, city: str) -> str:
        return self.rng.choice(self.towers_by_city[city])

    def candidates(self, cond) -> list[dict]:
        return [p for p in self.persons if p["person_id"] not in self.reserved and cond(p)]

    def pick(self, cond, k: int = 1, reserve: bool = True) -> list[dict]:
        cands = self.candidates(cond)
        if len(cands) < k:
            raise RuntimeError(f"not enough candidates ({len(cands)} < {k})")
        chosen = self.rng.sample(cands, k)
        if reserve:
            self.reserve(*[c["person_id"] for c in chosen])
        return chosen

    def household_ids(self, p: dict) -> list[str]:
        return [q for q in self.hh_members[p["_hh"]]]

    def full_name(self, pid: str) -> str:
        p = self.P[pid]
        return f"{p['first_name']} {p['last_name']}"

    # ============================================================ BACKGROUND
    def build_background(self) -> None:
        self._geography()
        self._households()
        self._family_links()
        self._businesses()
        self._employment()
        self._foreign_counterparties()
        self._accounts()
        self._account_extras()
        self._phones()
        self._online_banking()
        self._assets()
        self._business_assets()
        self._transactions()
        self._international_flows()
        self._misc_banking()
        self._misc_banking_extra()
        self._calls()
        self._logins()

    # --------------------------------------------------------------- places
    def _geography(self) -> None:
        for city, st, _w in N.CITIES:
            for _ in range(5):
                tid = self.ids.new("TW", 4)
                self.tables["cell_towers"].append({"tower_id": tid, "city": city, "region": st})
                self.towers_by_city[city].append(tid)
        for bank, _w in N.DOMESTIC_BANKS:
            for city, st, w in N.CITIES:
                for _ in range(1 if w < 9 else 2):
                    bid = self.ids.new("BR", 3)
                    self.tables["branches"].append({"branch_id": bid, "bank_name": bank, "city": city,
                                                    "region": st})
                    self.branches_by[(bank, city)].append(bid)
                    self.branches_by[(bank, "*")].append(bid)
        self.city_weights = [(c, s) for c, s, w in N.CITIES for _ in range(w)]
        self.city_state = {c: s for c, s, _w in N.CITIES}
        # shared registered-agent addresses (used by legit and shell companies alike)
        self.reg_agent_addrs = [
            self.new_address("Wilmington", "DE", "registered_agent", line1="500 Corporate Plaza Ste 100"),
            self.new_address("Wilmington", "DE", "registered_agent", line1="1800 Commerce Way Ste 210"),
            self.new_address("Cheyenne", "WY", "registered_agent", line1="30 Frontier Ave Ste 4"),
            self.new_address("Sheridan", "WY", "registered_agent", line1="1021 Main St Ste 300"),
        ]
        self.offshore_addrs = {}
        for cc, label, _bank, _t in N.OFFSHORE:
            self.offshore_addrs[cc] = [
                self.new_address(label.split()[0] if cc != "AE" else "Dubai", cc, "registered_agent", country=cc,
                                 line1=f"{self.rng.randint(1, 99)} Harbour Chambers, Level {k + 1}")
                for k in range(2)
            ]

    def random_city(self) -> tuple[str, str]:
        return self.rng.choice(self.city_weights)

    # ------------------------------------------------------------ households
    def _households(self) -> None:
        rng = self.rng
        self.hh_members: dict[int, list[str]] = {}
        while len(self.persons) < self.n_persons:
            city, st = self.random_city()
            addr = self.new_address(city, st)
            hh = self.hh_counter
            self.hh_counter += 1
            members: list[str] = []
            last = rng.choice(N.LAST_NAMES)
            kind = rng.choices(["single", "couple", "family", "single_parent", "elderly_couple", "elderly_single"],
                               [24, 18, 30, 8, 11, 9])[0]
            if kind.startswith("elderly"):
                a1_age = rng.randint(66, 90)
            elif kind in ("family", "single_parent"):
                a1_age = rng.randint(27, 58)
            else:
                a1_age = rng.randint(21, 64)
            a1 = self.new_person(rng.choice(N.FIRST_NAMES), last, a1_age, addr, city, st)
            members.append(a1["person_id"])
            if kind in ("couple", "family", "elderly_couple"):
                s_age = max(20, min(95, a1_age + rng.randint(-6, 6)))
                s_last = last if rng.random() < 0.8 else rng.choice(N.LAST_NAMES)
                a2 = self.new_person(rng.choice(N.FIRST_NAMES), s_last, s_age, addr, city, st)
                members.append(a2["person_id"])
                a1["_spouse"], a2["_spouse"] = a2["person_id"], a1["person_id"]
                self.add_rel(a1["person_id"], a2["person_id"], "spouse")
            if kind in ("family", "single_parent"):
                n_kids = rng.choices([1, 2, 3, 4], [35, 40, 18, 7])[0]
                kids = []
                for _ in range(n_kids):
                    k_age = rng.randint(0, min(22, a1_age - 19))
                    kid = self.new_person(rng.choice(N.FIRST_NAMES), last, k_age, addr, city, st)
                    kids.append(kid["person_id"])
                    members.append(kid["person_id"])
                    for par in members[: (2 if kind == "family" else 1)]:
                        self.add_rel(par, kid["person_id"], "parent")
                        self.P[par]["_children"].append(kid["person_id"])
                        kid["_parents"].append(par)
                for i in range(len(kids)):
                    for j in range(i + 1, len(kids)):
                        self.add_rel(kids[i], kids[j], "sibling")
            for m in members:
                self.P[m]["_hh"] = hh
            self.hh_members[hh] = members

    def _family_links(self) -> None:
        """Elderly parents with adult children living elsewhere; adult siblings."""
        rng = self.rng
        adults = [p for p in self.persons if 28 <= p["_age"] <= 60]
        for p in self.persons:
            if p["_age"] >= 66 and rng.random() < 0.75:
                n = rng.randint(1, 2)
                opts = [a for a in adults if 20 <= p["_age"] - a["_age"] <= 40 and a["_hh"] != p["_hh"]]
                for child in rng.sample(opts, min(n, len(opts))):
                    if p["person_id"] in child["_parents"] or len(child["_parents"]) >= 2:
                        continue
                    self.add_rel(p["person_id"], child["person_id"], "parent")
                    p["_children"].append(child["person_id"])
                    child["_parents"].append(p["person_id"])
        for _ in range(int(len(adults) * 0.12)):
            a, b = rng.sample(adults, 2)
            if a["_hh"] != b["_hh"] and abs(a["_age"] - b["_age"]) < 12:
                self.add_rel(a["person_id"], b["person_id"], "sibling")
                a["_contacts"].append((b["person_id"], "relative"))
                b["_contacts"].append((a["person_id"], "relative"))

    # ------------------------------------------------------------ businesses
    def _biz_name(self, industry: str) -> tuple[str, str]:
        rng = self.rng
        word = rng.choice(N.BUSINESS_WORDS)
        noun = {
            "restaurant": rng.choice(["Grill", "Kitchen", "Bistro", "Diner", "Taqueria", "Cafe"]),
            "retail": rng.choice(["Outfitters", "Goods", "Supply", "Boutique", "Mercantile"]),
            "construction": rng.choice(["Builders", "Construction", "Contracting", "Renovations"]),
            "consulting": rng.choice(["Consulting", "Advisors", "Strategy Group"]),
            "healthcare": rng.choice(["Family Clinic", "Medical Group", "Health Partners", "Dental"]),
            "legal services": rng.choice(["Law Group", "Legal", "& Associates"]),
            "property management": rng.choice(["Property Management", "Realty", "Residential"]),
            "logistics": rng.choice(["Logistics", "Freight", "Transport", "Courier"]),
            "manufacturing": rng.choice(["Manufacturing", "Industries", "Fabrication", "Components"]),
            "software": rng.choice(["Software", "Labs", "Technologies", "Systems"]),
            "salon": rng.choice(["Salon", "Hair Studio", "Beauty Bar"]),
            "laundromat": rng.choice(["Laundromat", "Wash & Fold", "Laundry"]),
            "car wash": rng.choice(["Car Wash", "Auto Spa", "Express Wash"]),
            "grocery": rng.choice(["Market", "Grocers", "Foods"]),
            "accounting": rng.choice(["Tax & Accounting", "CPA Group", "Bookkeeping"]),
            "auto repair": rng.choice(["Auto Repair", "Motors Service", "Garage"]),
        }.get(industry, "Company")
        etype = rng.choices(["LLC", "Corporation", "S-Corporation", "Partnership"], [50, 20, 22, 8])[0]
        suffix = {"LLC": "LLC", "Corporation": "Inc", "S-Corporation": "Inc", "Partnership": "LP"}[etype]
        return f"{word} {noun} {suffix}", etype

    def _infra_business(self, kind: str, name: str, industry: str) -> dict:
        city, st = self.random_city()
        addr = self.new_address(city, st, "commercial")
        b = self.new_business(name, "Corporation", industry, st, addr, self.r_date(date(1960, 1, 1), date(2005, 1, 1)),
                              city=city, _infra=kind)
        self.infra[kind].append(b["business_id"])
        return b

    def _businesses(self) -> None:
        rng = self.rng
        for nm in ["Metro Power & Light Co", "Citywide Water Authority Inc", "Lumen Broadband Inc"]:
            self._infra_business("utility", nm, "utilities")
        for nm in ["Apex Card Services Inc", "Summit Card Company"]:
            self._infra_business("card_issuer", nm, "financial services")
        self._infra_business("processor", "PayFlow Merchant Services Inc", "payment processing")
        for nm in ["Keystone Mortgage Corp", "Harborline Home Loans Inc"]:
            self._infra_business("lender", nm, "mortgage lending")
        self._infra_business("sba_lender", "Main Street Capital Lending Inc", "commercial lending")
        for nm in ["Landmark Title & Escrow Inc", "Union Escrow Services Inc"]:
            self._infra_business("title", nm, "real estate services")
        for nm in ["Granite Securities Inc", "Beacon Investments Inc"]:
            self._infra_business("broker", nm, "securities brokerage")
        for bank, _w in N.DOMESTIC_BANKS:
            b = self._infra_business("bank", f"{bank} NA", "banking")
            b["_bank"] = bank
        for nm in ["Westbrook Motors Inc", "Prairie Auto Group Inc", "Summit Ford & Kia Inc"]:
            self._infra_business("auto_dealer", nm, "auto dealer")
        self._infra_business("marine_dealer", "Bluewater Marine Sales Inc", "boat dealer")

        n_regular = int(self.n_persons / 6.5)
        inds = list(N.INDUSTRIES)
        weights = [N.INDUSTRIES[i][0] for i in inds]
        owners_pool = [p for p in self.persons if 28 <= p["_age"] <= 74]
        holding_cos: list[str] = []
        for _ in range(n_regular):
            ind = rng.choices(inds, weights)[0]
            _w, (elo, ehi), rpe, _cs, _cash = N.INDUSTRIES[ind]
            city, st = self.random_city()
            name, etype = self._biz_name(ind)
            use_agent = rng.random() < 0.12
            addr = rng.choice(self.reg_agent_addrs) if use_agent else self.new_address(city, st, "commercial")
            juris = "DE" if use_agent and rng.random() < 0.6 else st
            inc = self.r_date(date(1990, 1, 1), date(2022, 12, 31))
            if rng.random() < 0.13:
                inc = self.r_date(date(2023, 1, 1), date(2024, 12, 15))
            n_emp = rng.randint(elo, ehi)
            b = self.new_business(name, etype, ind, juris, addr, inc, city=city, _size=n_emp,
                                  _pay_freq=rng.choice([12, 24]))
            b["_rev"] = n_emp * rpe * rng.uniform(0.75, 1.3)
            if etype == "LLC" and ind not in N.CASH_INTENSIVE and rng.random() < 0.1:
                b["_files_return"] = False  # single-member LLC reported on the owner's return
            # owners
            n_own = rng.choices([1, 2, 3], [55, 33, 12])[0]
            if rng.random() < 0.08 and holding_cos:
                # owned via a legit domestic holding company
                hc = rng.choice(holding_cos)
                self.add_owner(hc, b["business_id"], "member" if etype == "LLC" else "shareholder", 100.0, inc)
            else:
                owners = rng.sample(owners_pool, n_own)
                splits = self._splits(n_own)
                role = {"LLC": "member", "Partnership": "partner"}.get(etype, "shareholder")
                for o, pct in zip(owners, splits):
                    self.add_owner(o["person_id"], b["business_id"], role, pct, inc)
                    o["_biz_owner"] = b["business_id"]
                if n_emp >= 8:
                    self.add_owner(owners[0]["person_id"], b["business_id"], "director", None, inc)
            if rng.random() < 0.05:
                # a legit holding company owned by one person (used by later companies)
                hp = rng.choice(owners_pool)
                hname = f"{rng.choice(N.BUSINESS_WORDS)} Family Holdings LLC"
                hb = self.new_business(hname, "LLC", "holding company", "DE", rng.choice(self.reg_agent_addrs),
                                       self.r_date(date(2000, 1, 1), date(2020, 1, 1)), city=city, _size=0,
                                       _holding=True)
                self.add_owner(hp["person_id"], hb["business_id"], "member", 100.0, date(2000, 1, 1))
                holding_cos.append(hb["business_id"])
        # legit offshore subsidiaries of manufacturers / software firms (decoys for shell detection)
        parents = [b for b in self.businesses if b["_ind"] in ("manufacturing", "software", "logistics")
                   and b["_infra"] is None]
        for par in rng.sample(parents, min(4, len(parents))):
            cc, label, _bank, etype = rng.choice(N.OFFSHORE[:4])
            sub = self.new_business(f"{par['name'].rsplit(' ', 1)[0]} International {etype}", etype,
                                    par["_ind"], cc, rng.choice(self.offshore_addrs[cc]),
                                    self.r_date(date(2008, 1, 1), date(2020, 1, 1)), city=None, _size=0,
                                    _offshore_sub=par["business_id"], _files_return=False)
            self.add_owner(par["business_id"], sub["business_id"], "shareholder", 100.0,
                           date.fromisoformat(sub["incorporation_date"]))

    def _splits(self, n: int) -> list[float]:
        if n == 1:
            return [100.0]
        if n == 2:
            a = self.rng.choice([50, 50, 60, 70, 51, 80])
            return [float(a), float(100 - a)]
        a = self.rng.choice([40, 34, 50, 60])
        b = self.rng.choice([30, 33, 25, 20])
        return [float(a), float(b), float(100 - a - b)]

    # ------------------------------------------------------------ employment
    def _employment(self) -> None:
        rng = self.rng
        regular = [b for b in self.businesses if b["_infra"] is None and b.get("_size", 0) > 0]
        infra_emp = [b for b in self.businesses if b["_infra"] in ("utility", "card_issuer", "processor", "lender",
                                                                    "bank", "broker", "title", "auto_dealer")]
        slots: list[str] = []
        for b in regular:
            slots.extend([b["business_id"]] * b["_size"])
        rng.shuffle(slots)
        slot_i = 0
        for p in self.persons:
            age = p["_age"]
            if age < 16:
                p["_status"] = "child"
                p["occupation"] = "Student" if age >= 5 else ""
                continue
            if age < 22:
                if rng.random() < 0.35:
                    p["_status"] = "parttime"
                    b = self.B[slots[slot_i % len(slots)]]
                    slot_i += 1
                    p["employer_id"] = b["business_id"]
                    p["occupation"] = rng.choice(N.OCCUPATION_BY_INDUSTRY.get(b["_ind"], ["Associate"]))
                    p["_salary"] = rng.uniform(3_000, 19_000)
                    b["_employees"].append(p["person_id"])
                else:
                    p["_status"] = "student"
                    p["occupation"] = "Student"
                continue
            if age >= 67 or (age >= 62 and rng.random() < 0.5):
                p["_status"] = "retired"
                p["occupation"] = "Retired"
                continue
            r = rng.random()
            if r < 0.76:
                p["_status"] = "employed"
                if rng.random() < 0.14 and infra_emp:
                    b = rng.choice(infra_emp)
                    occ_ind = "infrastructure"
                else:
                    b = self.B[slots[slot_i % len(slots)]]
                    slot_i += 1
                    occ_ind = b["_ind"]
                p["employer_id"] = b["business_id"]
                p["occupation"] = rng.choice(N.OCCUPATION_BY_INDUSTRY.get(occ_ind, ["Associate"]))
                sal = rng.lognormvariate(math.log(56_000), 0.48)
                if p["occupation"] in ("Physician", "Attorney", "Partner", "Software Engineer", "Product Manager"):
                    sal *= rng.uniform(1.8, 4.0)
                p["_salary"] = max(19_000.0, min(sal, 950_000.0))
                b["_employees"].append(p["person_id"])
            elif r < 0.88:
                p["_status"] = "self"
                p["occupation"] = rng.choice(N.SELF_EMPLOYED_OCCUPATIONS)
                p["_salary"] = max(12_000.0, min(rng.lognormvariate(math.log(58_000), 0.55), 400_000.0))
            else:
                p["_status"] = "none"
                p["occupation"] = rng.choice(["Homemaker", "Unemployed", "Caregiver"])
        # officers for larger firms (CFO etc.)
        for b in regular:
            if len(b["_employees"]) >= 10:
                emps = [e for e in b["_employees"] if self.P[e]["_age"] >= 30]
                for title in ("CFO", "Controller", "Procurement Director")[: rng.randint(1, 3)]:
                    if emps:
                        e = emps.pop(rng.randrange(len(emps)))
                        self.add_owner(e, b["business_id"], "officer", None,
                                       self.r_date(date(2012, 1, 1), date(2024, 6, 1)), title=title)
                        self.P[e]["_title"] = (b["business_id"], title)
                        self.P[e]["_salary"] = max(self.P[e]["_salary"], rng.uniform(95_000, 240_000))
        # coworker contacts
        for b in self.businesses:
            emps = b["_employees"]
            for e in emps:
                k = min(len(emps) - 1, rng.randint(1, 4))
                for other in rng.sample([x for x in emps if x != e], k) if k > 0 else []:
                    self.P[e]["_contacts"].append((other, "coworker"))

    # -------------------------------------------------------------- accounts
    def _pick_bank(self) -> str:
        return self.rng.choices([b for b, _ in N.DOMESTIC_BANKS], [w for _, w in N.DOMESTIC_BANKS])[0]

    def _accounts(self) -> None:
        rng = self.rng
        for b in self.businesses:
            if b.get("_foreign"):
                continue
            if b["jurisdiction"] in [o[0] for o in N.OFFSHORE]:
                cc = b["jurisdiction"]
                bank = [o[2] for o in N.OFFSHORE if o[0] == cc][0]
                self.new_account(b["business_id"], bank, cc, "business_checking",
                                 date.fromisoformat(b["incorporation_date"]) + timedelta(days=30))
                continue
            if b["_infra"] == "bank":
                self.new_account(b["business_id"], b["_bank"], "US", "internal", date(1990, 1, 1))
                continue
            signer = None
            owners = [o for o, _pct in b["_owners"] if o.startswith("P")]
            if owners:
                signer = owners[0]
            opened = max(date.fromisoformat(b["incorporation_date"]), date(1995, 1, 1)) + timedelta(days=rng.randint(5, 90))
            self.new_account(b["business_id"], self._pick_bank(), "US", "business_checking", min(opened, date(2024, 6, 1)),
                             signer=signer)
        for p in self.persons:
            age = p["_age"]
            if age < 16:
                continue
            prob = 0.3 if age < 20 else 0.86
            if rng.random() < prob:
                p["_customer"] = True
                bank = self._pick_bank()
                opened = self.r_date(min(date(max(1985, YEAR - age + 18), 1, 1), date(2024, 1, 1)), date(2024, 10, 1))
                self.new_account(p["person_id"], bank, "US", "checking", opened)
                if age >= 20 and rng.random() < 0.42:
                    self.new_account(p["person_id"], bank if rng.random() < 0.7 else self._pick_bank(), "US",
                                     "savings", self.r_date(opened, date(2024, 12, 1)))
            else:
                p["_customer"] = False

    # ---------------------------------------------------------------- phones
    def _phones(self) -> None:
        rng = self.rng
        for p in self.persons:
            age = p["_age"]
            if age < 12 or rng.random() > (0.82 if age < 18 else 0.95):
                continue
            act = self.r_date(date(2012, 1, 1), date(2024, 11, 30))
            if rng.random() < 0.07:
                act = self.r_date(date(2025, 1, 5), date(2025, 10, 31))
            if age < 18 and p["_parents"]:
                sub, plan = p["_parents"][0], "postpaid"
            elif rng.random() < 0.13:
                sub, plan = None, "prepaid"
            else:
                sub, plan = p["person_id"], "postpaid"
            if rng.random() < 0.06 and age >= 18:
                # switches number mid-year (ordinary churn)
                sw = self.r_date(date(2025, 3, 1), date(2025, 10, 31))
                old = self.new_phone(sub, plan, act, sw, user=p["person_id"])
                new_sub, new_plan = (p["person_id"], "postpaid") if rng.random() < 0.7 else (None, "prepaid")
                new = self.new_phone(new_sub, new_plan, sw, None, user=p["person_id"])
                p["phone"] = new if rng.random() < 0.5 else old
            else:
                num = self.new_phone(sub, plan, act, None, user=p["person_id"])
                p["phone"] = num if p.get("_customer") or rng.random() < 0.5 else None
            if not p.get("_customer") and rng.random() < 0.5:
                p["phone"] = None

    def _online_banking(self) -> None:
        rng = self.rng
        for hh, members in self.hh_members.items():
            ip = self.home_ip()
            shared = self.new_device() if rng.random() < 0.18 else None
            for m in members:
                p = self.P[m]
                p["_home_ip"] = ip
                if p.get("_customer") and rng.random() < 0.82:
                    p["_online"] = True
                    p["_devices"] = [self.new_device()]
                    if rng.random() < 0.3:
                        p["_devices"].append(self.new_device())
                    if shared and p["_age"] >= 18:
                        p["_devices"].append(shared)

    # ---------------------------------------------------------------- assets
    def add_asset(self, asset_type: str, desc: str, owner: str, purchase: date, price: float, financing: str,
                  loan: float = 0.0, sale_date: date | None = None, sale_price: float | None = None,
                  address_id: str | None = None) -> str:
        aid = self.ids.new("AS", 7)
        self.tables["assets"].append({
            "asset_id": aid, "asset_type": asset_type, "description": desc, "owner_id": owner,
            "purchase_date": d2s(purchase), "purchase_price": money(price), "financing": financing,
            "loan_amount": money(loan), "sale_date": d2s(sale_date),
            "sale_price": None if sale_price is None else money(sale_price), "address_id": address_id,
        })
        return aid

    def household_income(self, p: dict) -> float:
        return sum(self.P[m]["_salary"] for m in self.hh_members[p["_hh"]])

    def _vehicle_desc(self, price: float) -> str:
        rng = self.rng
        if price > 90_000:
            mk = rng.choice(["Porsche 911", "Mercedes G-Class", "Range Rover", "BMW M8", "Maserati Levante"])
        elif price > 45_000:
            mk = rng.choice(["Tesla Model X", "BMW X5", "Audi Q7", "Lexus RX", "Ford F-250 Platinum"])
        else:
            mk = rng.choice(["Toyota Camry", "Honda CR-V", "Ford F-150", "Hyundai Elantra", "Subaru Outback",
                             "Chevrolet Malibu", "Nissan Rogue", "Kia Sorento"])
        return f"{rng.randint(2014, 2026)} {mk}"

    def _assets(self) -> None:
        rng = self.rng
        lender_accts = self.infra["lender"]
        for hh, members in self.hh_members.items():
            adults = [self.P[m] for m in members if self.P[m]["_age"] >= 22]
            if not adults:
                continue
            head = max(adults, key=lambda x: x["_salary"])
            inc = self.household_income(head)
            if head["_age"] >= 30 and (inc > 45_000 or head["_age"] >= 66) and rng.random() < 0.62:
                price = max(120_000, min(inc * rng.uniform(2.8, 4.8), 2_600_000)) if inc > 0 else rng.uniform(150_000, 420_000)
                pdate = self.r_date(date(1996, 1, 1), date(2024, 6, 30))
                loan = price * rng.uniform(0.75, 0.9)
                fin = "mortgage"
                if head["_age"] >= 66 and rng.random() < 0.5:
                    pdate = self.r_date(date(1985, 1, 1), date(2012, 1, 1))
                self.add_asset("real_estate", "Single-family residence", head["person_id"], pdate, price, fin, loan,
                               address_id=head["address_id"])
                head["_homeowner"] = True
                years_paid = YEAR - pdate.year
                if years_paid < 28:
                    head["_mortgage_pay"] = money(loan * 0.0062)
                    head["_lender"] = rng.choice(lender_accts)
            for a in adults:
                if a["_salary"] > 0 or a["_age"] >= 66:
                    if rng.random() < 0.72:
                        price = max(6_000, min(a["_salary"] * rng.uniform(0.25, 0.7), 140_000)) if a["_salary"] else rng.uniform(8_000, 35_000)
                        cash = rng.random() < 0.3
                        self.add_asset("vehicle", self._vehicle_desc(price), a["person_id"],
                                       self.r_date(date(2014, 1, 1), date(2024, 11, 30)), price,
                                       "cash" if cash else "loan", 0.0 if cash else price * rng.uniform(0.6, 0.9))

    # ----------------------------------------------------------- transactions
    def _transactions(self) -> None:
        rng = self.rng
        months = list(range(1, 13))
        proc = self.checking_of(self.infra["processor"][0])
        card_issuers = [self.checking_of(b) for b in self.infra["card_issuer"]]
        utilities = [self.checking_of(b) for b in self.infra["utility"]]
        bank_internal = {self.B[b]["_bank"]: self.accounts_of[b][0] for b in self.infra["bank"]}
        regular = [b for b in self.businesses if b["_infra"] is None and b.get("_size", 0) > 0]
        landlords_biz = [self.checking_of(b["business_id"]) for b in regular if b["_ind"] == "property management"]
        indiv_landlords = []
        for p in self.persons:
            if p["_homeowner"] and p.get("_customer") and p["_salary"] > 80_000 and rng.random() < 0.08:
                indiv_landlords.append(p)
                p["_landlord"] = True
        self.p2p_txns: list[tuple[str, str, datetime]] = []  # (person, txn, ts) for login audit
        self.new_payees: dict[str, list[str]] = defaultdict(list)

        # ---- payroll
        for b in self.businesses:
            if not b["_employees"]:
                continue
            bacct = self.checking_of(b["business_id"])
            freq = b["_pay_freq"]
            for e in b["_employees"]:
                p = self.P[e]
                gross = p["_salary"]
                wh_rate = min(0.32, 0.06 + gross / 900_000)
                p["_w2"].append((b["business_id"], money(gross), money(gross * wh_rate)))
                pacct = self.checking_of(e)
                if not pacct or not bacct:
                    continue
                net = gross * (1 - wh_rate - 0.0765) / freq
                for m in months:
                    days = [28] if freq == 12 else [15, 28]
                    for dday in days:
                        ts = datetime(YEAR, m, dday, 6, rng.randint(0, 59), rng.randint(0, 59))
                        self.add_txn(ts, bacct, pacct, net * rng.uniform(0.995, 1.005), "ach", "PAYROLL")

        # ---- household outflows
        for hh, members in self.hh_members.items():
            adults = [self.P[m] for m in members if self.P[m]["_age"] >= 18]
            payers = [a for a in adults if self.checking_of(a["person_id"])]
            if not payers:
                continue
            head = max(payers, key=lambda x: x["_salary"])
            hacct = self.checking_of(head["person_id"])
            inc = self.household_income(head)
            if head["_homeowner"] and head.get("_mortgage_pay"):
                dest, amt, memo = self.checking_of(head["_lender"]), head["_mortgage_pay"], "MORTGAGE PMT"
            elif not head["_homeowner"]:
                rent = max(850, min(inc * rng.uniform(0.18, 0.3) / 12, 6_500)) if inc else rng.uniform(800, 1500)
                if indiv_landlords and rng.random() < 0.15:
                    ll = rng.choice(indiv_landlords)
                    dest = self.checking_of(ll["person_id"])
                    ll["_rent_income"] += rent * 12
                else:
                    dest = rng.choice(landlords_biz)
                amt, memo = rent, "RENT"
                head["_rent"] = (dest, amt)
            else:
                dest = None
            for m in months:
                if dest:
                    self.add_txn(self.r_daytime(date(YEAR, m, rng.randint(1, 5)), 0, 6), hacct, dest, amt, "ach", memo)
                self.add_txn(self.r_daytime(date(YEAR, m, rng.randint(10, 20))), hacct, rng.choice(utilities),
                             rng.uniform(70, 260), "bill_payment", "UTILITY")
            for a in payers:
                acct = self.checking_of(a["person_id"])
                if a["_salary"] > 15_000 and rng.random() < 0.75:
                    a["_card"] = True
                    issuer = rng.choice(card_issuers)
                    base = a["_salary"] / 12 * rng.uniform(0.08, 0.3)
                    for m in months:
                        self.add_txn(self.r_daytime(date(YEAR, m, rng.randint(18, 27))), acct, issuer,
                                     max(25, base * rng.uniform(0.6, 1.4)), "bill_payment", "CARD PAYMENT")
                for _ in range(rng.randint(0, 9)):
                    day = self.r_date(date(YEAR, 1, 2), date(YEAR, 12, 30))
                    self.add_txn(self.r_daytime(day, 7, 23), acct, None, rng.choice([40, 60, 80, 100, 120, 200, 300, 400]),
                                 "cash_withdrawal", "ATM", branch=self.branch_for(acct, a["_city"]))
                if rng.random() < 0.15:
                    for _ in range(rng.randint(1, 3)):
                        day = self.r_date(date(YEAR, 1, 2), date(YEAR, 12, 30))
                        self.add_txn(self.r_daytime(day, 9, 17), None, acct, rng.uniform(100, 2_400), "cash_deposit",
                                     "", branch=self.branch_for(acct, a["_city"]), conducted_by=a["person_id"])
        # ---- interest on savings
        for acct in list(self.tables["accounts"]):
            if acct["account_type"] == "savings":
                holder = self.P.get(acct["holder_id"])
                if not holder:
                    continue
                amt = rng.lognormvariate(math.log(max(60.0, holder["_salary"] / 300 + 40)), 0.9)
                amt = min(amt, 9_000)
                ts = datetime(YEAR, 12, 31, 3, 0, rng.randint(0, 59))
                self.add_txn(ts, bank_internal[acct["bank_name"]], acct["account_id"], amt, "interest", "INTEREST")
                if amt >= 10:
                    bank_biz = [b for b in self.infra["bank"] if self.B[b]["_bank"] == acct["bank_name"]][0]
                    holder["_int"].append((bank_biz, money(amt)))
        # ---- dividends (no bank activity, info returns only)
        for p in self.persons:
            if (p["_salary"] > 110_000 and rng.random() < 0.5) or (p["_status"] == "retired" and rng.random() < 0.35):
                broker = rng.choice(self.infra["broker"])
                p["_div"].append((broker, money(rng.lognormvariate(math.log(2_500), 1.0))))
        # ---- p2p among contacts / family
        for p in self.persons:
            acct = self.checking_of(p["person_id"])
            if not acct or p["_age"] < 18:
                continue
            fam = [m for m in self.hh_members[p["_hh"]] if m != p["person_id"]] + p["_children"] + p["_parents"]
            fam += [c for c, _k in p["_contacts"]]
            fam = [f for f in fam if self.checking_of(f)]
            for _ in range(rng.randint(0, 7) if fam else 0):
                to = rng.choice(fam)
                ts = self.r_dt(Y_START, Y_END - timedelta(days=1))
                tid = self.add_txn(ts, acct, self.checking_of(to), rng.choice([20, 25, 40, 50, 60, 75, 100, 150, 200, 300, 450]),
                                   "p2p", rng.choice(["dinner", "rent share", "gift", "thanks", "tickets", "groceries", "", "loan",
                                                    "loan repayment", "for you", "gas money", "investment",
                                                    "for books", "services", "invoice"]))
                self.p2p_txns.append((p["person_id"], tid, ts))
        # ---- businesses: revenue
        self.biz_revenue_deposits: dict[str, float] = defaultdict(float)
        for b in regular:
            bacct = self.checking_of(b["business_id"])
            _w, _e, _rpe, card_share, cash_share = N.INDUSTRIES[b["_ind"]]
            rev = b["_rev"]
            card = rev * card_share
            cash = rev * cash_share
            b2b = rev - card - cash
            if card > 0:
                card_total = 0.0
                for wk in range(52):
                    ts = datetime(YEAR, 1, 3, 4, rng.randint(0, 59)) + timedelta(weeks=wk)
                    amt = card / 52 * rng.uniform(0.75, 1.25)
                    self.add_txn(ts, proc, bacct, amt, "card_settlement", "CARD SETTLEMENT")
                    card_total += money(amt)
                b["_card_total"] = money(card_total)
            if cash > 0:
                big = cash > 420_000 and rng.random() < 0.6
                n = max(26, min(104, math.ceil(cash / 16_000))) if big else max(24, min(300, math.ceil(cash / 3_600)))
                handlers = [o for o, _ in b["_owners"] if o.startswith("P")] + b["_employees"][:2]
                handlers = [h for h in handlers if self.P[h]["_age"] >= 18] or [None]
                for _ in range(n):
                    day = self.r_date(date(YEAR, 1, 2), date(YEAR, 12, 30))
                    amt = cash / n * rng.uniform(0.55, 1.45)
                    if big:
                        amt = max(10_600.0, cash / n * rng.uniform(0.75, 1.35))
                    h = rng.choice(handlers)
                    self.add_txn(self.r_daytime(day, 9, 17), None, bacct, amt, "cash_deposit", "",
                                 branch=self.branch_for(bacct, b["_city"]), conducted_by=h)
            if b2b > 0:
                payers = [x for x in regular if x["business_id"] != b["business_id"]]
                n = rng.randint(10, 36)
                for _ in range(n):
                    payer = rng.choice(payers)
                    ts = self.r_daytime(self.r_date(date(YEAR, 1, 2), date(YEAR, 12, 30)))
                    self.add_txn(ts, self.checking_of(payer["business_id"]), bacct, b2b / n * rng.uniform(0.5, 1.5),
                                 rng.choice(["ach", "ach", "wire", "check"]), self._invoice_memo(b))
        # ---- businesses: expenses (rent, utilities)
        for b in regular:
            bacct = self.checking_of(b["business_id"])
            rent = max(1_200, b["_rev"] * rng.uniform(0.03, 0.07) / 12)
            ll = rng.choice(landlords_biz)
            util = rng.choice(utilities)
            for m in months:
                if ll != bacct:
                    self.add_txn(self.r_daytime(date(YEAR, m, rng.randint(1, 5)), 0, 6), bacct, ll, rent, "ach", "LEASE")
                self.add_txn(self.r_daytime(date(YEAR, m, rng.randint(8, 22))), bacct, util, rng.uniform(150, 2_500),
                             "bill_payment", "UTILITY")
        # ---- self-employed income (1099-NEC from businesses + undocumented cash)
        for p in self.persons:
            if p["_status"] != "self":
                continue
            acct = self.checking_of(p["person_id"])
            target = p["_salary"]
            documented = target * rng.uniform(0.55, 0.95)
            clients = rng.sample(regular, rng.randint(2, 5))
            shares = [rng.random() + 0.2 for _ in clients]
            tot = sum(shares)
            if p["occupation"] in ("Rideshare Driver", "Personal Trainer", "Photographer", "Tutor",
                                   "Freelance Designer") and rng.random() < 0.6:
                # gig-platform income paid out by the processor (reported on a 1099-K)
                gig = documented * rng.uniform(0.3, 0.7)
                documented -= gig
                p["_k1099"] = money(gig)
                if acct:
                    for wk in range(0, 52, 2):
                        self.add_txn(self.r_daytime(date(YEAR, 1, 6) + timedelta(weeks=wk), 3, 5), proc, acct,
                                     gig / 26, "ach", "PAYFLOW PAYOUT")
            for c, s in zip(clients, shares):
                amt_total = documented * s / tot
                k = rng.randint(2, 10)
                paid = 0.0
                for _ in range(k):
                    ts = self.r_daytime(self.r_date(date(YEAR, 1, 5), date(YEAR, 12, 28)))
                    amt = money(amt_total / k)
                    if acct:
                        self.add_txn(ts, self.checking_of(c["business_id"]), acct, amt,
                                     rng.choice(["ach", "check"]), f"SERVICES {rng.randint(100, 999)}")
                    paid += amt
                if paid >= 600:
                    p["_nec"].append((c["business_id"], money(paid)))
                else:
                    p["_extra_se"] += paid
            p["_extra_se"] += target - documented
        # ---- 2025 big purchases (cars / homes / boats), consistent with income
        dealers = self.infra["auto_dealer"]
        titles = self.infra["title"]
        for p in self.persons:
            if p["_age"] < 22 or p["_salary"] <= 0:
                continue
            acct = self.checking_of(p["person_id"])
            r = rng.random()
            if r < 0.05:
                price = max(9_000, min(p["_salary"] * rng.uniform(0.2, 0.6), 160_000))
                day = self.r_date(date(YEAR, 1, 10), date(YEAR, 12, 15))
                cash = price < 0.3 * p["_salary"]
                self.add_asset("vehicle", self._vehicle_desc(price), p["person_id"], day, price,
                               "cash" if cash else "loan", 0.0 if cash else price * 0.8)
                if acct:
                    self.add_txn(self.r_daytime(day), acct, self.checking_of(rng.choice(dealers)),
                                 price if cash else price * 0.2, rng.choice(["wire", "check"]), "VEHICLE PURCHASE")
            elif r < 0.07 and p["_salary"] > 60_000 and not p["_homeowner"]:
                price = max(180_000, min(p["_salary"] * rng.uniform(3, 4.5), 2_000_000))
                day = self.r_date(date(YEAR, 2, 1), date(YEAR, 11, 30))
                addr = self.new_address(p["_city"], p["_state"])
                self.add_asset("real_estate", "Single-family residence", p["person_id"], day, price, "mortgage",
                               price * 0.8, address_id=addr)
                if acct:
                    self.add_txn(self.r_daytime(day), acct, self.checking_of(rng.choice(titles)), price * 0.2, "wire",
                                 "CLOSING FUNDS")
            elif r < 0.085 and p["_salary"] > 250_000:
                price = min(p["_salary"] * rng.uniform(0.1, 0.35), 400_000)
                day = self.r_date(date(YEAR, 3, 1), date(YEAR, 9, 30))
                self.add_asset("boat", f"{rng.randint(28, 52)}ft {rng.choice(['Sea Ray', 'Boston Whaler', 'Grady-White'])}",
                               p["person_id"], day, price, "cash")
                if acct:
                    self.add_txn(self.r_daytime(day), acct, self.checking_of(self.infra["marine_dealer"][0]), price,
                                 "wire", "VESSEL PURCHASE")
        # ---- a few legit home sales + cash purchases (upsizing / downsizing)
        homeowners = [p for p in self.persons if p["_homeowner"] and p["_age"] >= 45 and p["person_id"] not in self.reserved]
        for p in rng.sample(homeowners, min(int(8 * self.scale) + 2, len(homeowners))):
            for a in self.tables["assets"]:
                if a["owner_id"] == p["person_id"] and a["asset_type"] == "real_estate" and a["sale_date"] is None:
                    sale_day = self.r_date(date(YEAR, 1, 15), date(YEAR, 7, 31))
                    a["sale_date"] = d2s(sale_day)
                    a["sale_price"] = money(a["purchase_price"] * rng.uniform(1.3, 2.2))
                    new_price = a["sale_price"] * rng.uniform(0.55, 0.95)
                    nd = sale_day + timedelta(days=rng.randint(10, 60))
                    addr = self.new_address(p["_city"], p["_state"])
                    self.add_asset("real_estate", "Single-family residence", p["person_id"], nd, new_price, "cash",
                                   address_id=addr)
                    p["_home_sale"] = True
                    acct = self.checking_of(p["person_id"])
                    tacct = self.checking_of(rng.choice(titles))
                    if acct:
                        self.add_txn(self.r_daytime(sale_day), tacct, acct, a["sale_price"] - a["loan_amount"] * 0.5,
                                     "wire", "SALE PROCEEDS")
                        self.add_txn(self.r_daytime(nd), acct, tacct, new_price, "wire", "CLOSING FUNDS")
                    break

    # ----------------------------------------------------------------- calls
    def _calls(self) -> None:
        rng = self.rng
        edges: dict[tuple[str, str], float] = {}

        def add_edge(a: str, b: str, rate: float) -> None:
            if a == b:
                return
            k = (a, b) if a < b else (b, a)
            edges[k] = max(edges.get(k, 0.0), rate)

        for hh, members in self.hh_members.items():
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    add_edge(members[i], members[j], 11.0)
        for p in self.persons:
            for c in p["_children"] + p["_parents"]:
                add_edge(p["person_id"], c, 6.0)
            for c, kind in p["_contacts"]:
                add_edge(p["person_id"], c, 2.5 if kind == "coworker" else 5.0)
        by_city: dict[str, list[str]] = defaultdict(list)
        for p in self.persons:
            if p["_phones"] and p["_age"] >= 14:
                by_city[p["_city"]].append(p["person_id"])
        for p in self.persons:
            if not p["_phones"] or p["_age"] < 14:
                continue
            pool = by_city[p["_city"]]
            for f in rng.sample(pool, min(len(pool), rng.randint(2, 5))):
                add_edge(p["person_id"], f, 3.5)
                p["_contacts"].append((f, "friend"))
        self.call_edges = edges
        for (a, b), rate in edges.items():
            pa, pb = self.P[a], self.P[b]
            if not pa["_phones"] or not pb["_phones"]:
                continue
            for _ in range(poisson(rng, rate)):
                ts = self.r_dt(Y_START, Y_END)
                if rng.random() < 0.5:
                    pa, pb = pb, pa
                caller = self.active_phone(pa["person_id"], ts.date())
                callee = self.active_phone(pb["person_id"], ts.date())
                if caller and callee and 7 <= ts.hour <= 23:
                    self.add_call(ts, caller, callee, tower=self.tower_in(pa["_city"]))
        # business / service calls to random numbers (noise)
        all_nums = [n for n in self.PHONE]
        for _ in range(int(len(all_nums) * 1.2)):
            a, b = rng.sample(all_nums, 2)
            ts = self.r_dt(Y_START, Y_END)
            pa = self.PHONE[a]["_user"]
            if pa and self.active_phone(pa, ts.date()) == a and self.PHONE[b]["_user"] \
                    and self.active_phone(self.PHONE[b]["_user"], ts.date()) == b:
                self.add_call(ts, a, b, tower=self.tower_in(self.P[pa]["_city"]))

    # ---------------------------------------------------------------- logins
    def _logins(self) -> None:
        rng = self.rng
        upgrades: dict[str, tuple[date, str]] = {}
        for p in self.persons:
            if not p["_online"]:
                continue
            if rng.random() < 0.1:
                upgrades[p["person_id"]] = (self.r_date(date(YEAR, 2, 1), date(YEAR, 11, 1)), self.new_device())

        def device_for(pid: str, when: datetime) -> str:
            p = self.P[pid]
            devs = list(p["_devices"])
            up = upgrades.get(pid)
            if up and when.date() >= up[0]:
                devs[0] = up[1]
            return rng.choice(devs) if rng.random() < 0.35 else devs[0]

        def ip_for(pid: str) -> str:
            return self.P[pid]["_home_ip"] if rng.random() < 0.65 else self.mobile_ip()

        for p in self.persons:
            if not p["_online"]:
                continue
            pid = p["person_id"]
            self.add_login(self.r_dt(Y_START, datetime(YEAR, 2, 20)), pid, p["_devices"][0], ip_for(pid), "US", "login")
            for _ in range(rng.randint(4, 16)):
                ts = self.r_dt(Y_START, Y_END)
                self.add_login(ts, pid, device_for(pid, ts), ip_for(pid), "US", "login")
            up = upgrades.get(pid)
            if up and rng.random() < 0.5:
                ts = self.r_daytime(up[0])
                self.add_login(ts, pid, up[1], ip_for(pid), "US", "password_reset")
            elif rng.random() < 0.04:
                ts = self.r_dt(Y_START, Y_END)
                self.add_login(ts, pid, device_for(pid, ts), ip_for(pid), "US", "password_reset")
            if rng.random() < 0.035:
                # legit travel abroad on the usual device
                cc = rng.choice(N.FOREIGN_TRAVEL_COUNTRIES)
                start = self.r_dt(datetime(YEAR, 3, 1), Y_END - timedelta(days=15))
                for k in range(rng.randint(2, 5)):
                    ts = start + timedelta(days=rng.randint(0, 10), hours=rng.randint(0, 12))
                    self.add_login(ts, pid, p["_devices"][0], self.foreign_ip(), cc, "login")
                if rng.random() < 0.25:  # forgot the password on holiday
                    self.add_login(start + timedelta(hours=rng.randint(1, 30)), pid, p["_devices"][0],
                                   self.foreign_ip(), cc, "password_reset")
                friends = [c for c, _k in p["_contacts"] if self.checking_of(c)]
                if friends and self.checking_of(pid) and rng.random() < 0.35:
                    ts = start + timedelta(days=rng.randint(0, 10), hours=rng.randint(0, 12))
                    tid = self.add_txn(ts, self.checking_of(pid), self.checking_of(rng.choice(friends)),
                                       rng.choice([40, 75, 120, 200, 350]), "p2p", rng.choice(["dinner", "tour", "taxi"]))
                    self.add_login(ts, pid, p["_devices"][0], self.foreign_ip(), cc, "transfer", tid)
            if rng.random() < 0.02:
                ts = self.r_dt(Y_START, Y_END)
                self.add_login(ts, pid, device_for(pid, ts), ip_for(pid), "US", "change_phone")
        seen_payees: dict[str, list[str]] = defaultdict(list)
        for pid, tid, ts in sorted(self.p2p_txns, key=lambda x: x[2]):
            p = self.P[pid]
            if not p["_online"]:
                continue
            to = self.txn_by_id[tid]["to_account"]
            dev = device_for(pid, ts)
            ip = ip_for(pid)
            if to not in seen_payees[pid]:
                seen_payees[pid].append(to)
                self.add_login(ts - timedelta(minutes=rng.randint(1, 20)), pid, dev, ip, "US", "add_payee")
            self.add_login(ts, pid, dev, ip, "US", "transfer", txn_id=tid)
        self.login_upgrades = upgrades

    # ------------------------------------------------- realism: legit look-alikes
    # Every feature that planted schemes use (foreign wires, 2025 account openings, new LLCs, teller cash,
    # large cash deposits, new phones, third-party signers) must also occur in ordinary background activity,
    # otherwise a model could find the planted entities by filtering on rare features instead of investigating.
    def _new_foreign_person(self, cc: str, city: str, last: str | None = None) -> dict:
        rng = self.rng
        addr = self.new_address(city, cc, "residential", country=cc)
        p = self.new_person(rng.choice(N.FIRST_NAMES), last or rng.choice(N.LAST_NAMES), rng.randint(25, 80), addr,
                            city, cc, occupation=rng.choice(N.FOREIGN_OCCUPATIONS))
        del self.TIN[p["tin"]]
        p["tin"] = None
        p["_foreign"] = True
        return p

    def _foreign_counterparties(self) -> None:
        rng = self.rng
        self.foreign_suppliers: list[str] = []
        for cc, city, bank in N.FOREIGN_SUPPLIERS:
            for _ in range(rng.randint(1, 2)):
                suffix = {"MX": "SA de CV", "DE": "GmbH", "CN": "Co Ltd", "VN": "JSC"}.get(cc, "Ltd")
                name = f"{city.split()[0]} {rng.choice(['Industrial', 'Trading', 'Components', 'Textile', 'Export'])} {suffix}"
                inc = self.r_date(date(1995, 1, 1), date(2018, 1, 1))
                b = self.new_business(name, "Corporation", rng.choice(["manufacturing", "logistics", "retail"]), cc,
                                      self.new_address(city, cc, "commercial", country=cc), inc, city=None,
                                      _infra="foreign", _foreign=True, _files_return=False, _size=0)
                self.new_account(b["business_id"], bank, cc, "business_checking", inc + timedelta(days=40))
                self.foreign_suppliers.append(b["business_id"])
        fund = self.new_business("Coral Bay Global Opportunities Fund Ltd", "Ltd", "investment fund", "KY",
                                 self.offshore_addrs["KY"][0], date(2011, 4, 1), city=None, _infra="foreign_fund",
                                 _foreign=True, _files_return=False, _size=0)
        self.new_account(fund["business_id"], "Caymanian Fiduciary Bank", "KY", "business_checking", date(2011, 5, 1))
        self.fund_id = fund["business_id"]

    def _account_extras(self) -> None:
        rng = self.rng
        self.new_2025_accounts: list[tuple[str, str, str, date]] = []
        for p in self.persons:
            prim = self.checking_of(p["person_id"]) if p.get("_customer") else None
            if not prim:
                continue
            if p["_age"] >= 75 and rng.random() < 0.3:
                kids = [c for c in p["_children"] if self.P[c]["_age"] >= 30]
                if kids:
                    self.A[prim]["authorized_signer_id"] = kids[0]
            if rng.random() < 0.07:
                opened = self.r_date(date(YEAR, 1, 10), date(YEAR, 11, 10))
                bank = self.A[prim]["bank_name"] if rng.random() < 0.6 else self._pick_bank()
                acct = self.new_account(p["person_id"], bank, "US", "savings" if rng.random() < 0.6 else "checking",
                                        opened)
                self.new_2025_accounts.append((p["person_id"], prim, acct, opened))

    def _business_assets(self) -> None:
        rng = self.rng
        for b in self.businesses:
            if b["_infra"] is not None or not b.get("_size"):
                continue
            if rng.random() < 0.22:
                price = max(250_000, b["_rev"] * rng.uniform(0.3, 1.1))
                self.add_asset("real_estate", "Commercial building", b["business_id"],
                               self.r_date(date(2000, 1, 1), date(2023, 12, 1)), price, "mortgage",
                               price * rng.uniform(0.55, 0.8), address_id=b["address_id"])
            if rng.random() < 0.3:
                price = rng.uniform(24_000, 72_000)
                self.add_asset("vehicle", self._vehicle_desc(price), b["business_id"],
                               self.r_date(date(2016, 1, 1), date(2024, 11, 1)), price, "loan", price * 0.8)

    def _international_flows(self) -> None:
        rng = self.rng
        regular = [b for b in self.businesses if b["_infra"] is None and b.get("_size", 0) > 0]
        importers = [b for b in regular if b["_ind"] in ("retail", "manufacturing", "grocery", "auto repair",
                                                         "construction", "software", "logistics")]
        for b in importers:
            if rng.random() > 0.35:
                continue
            acct = self.checking_of(b["business_id"])
            for sup in rng.sample(self.foreign_suppliers, rng.randint(1, 2)):
                n = rng.randint(3, 12)
                for _ in range(n):
                    amt = b["_rev"] * rng.uniform(0.02, 0.07) / n
                    self.add_txn(self.r_daytime(self.r_date(date(YEAR, 1, 6), date(YEAR, 12, 20)), 8, 16), acct,
                                 self.accounts_of[sup][0], amt, "international_wire",
                                 rng.choice([f"PO {rng.randint(10000, 99999)}", "TRADE SETTLEMENT",
                                             f"INVOICE {rng.randint(1000, 9999)} PAYMENT"]))
        for sub in self.businesses:
            parent = sub.get("_offshore_sub")
            if not parent:
                continue
            pacct, sacct = self.checking_of(parent), self.accounts_of[sub["business_id"]][0]
            for frm, to, memos, k in ((pacct, sacct, ["INTERCOMPANY FUNDING", "INTERCOMPANY", "ADVISORY FEE",
                                                      "MANAGEMENT FEE", "SERVICES AGREEMENT"], rng.randint(3, 8)),
                                      (sacct, pacct, ["INTERCOMPANY SETTLEMENT", "INTERCOMPANY", "DIVIDEND",
                                                      "TRADE SETTLEMENT"], rng.randint(2, 6))):
                for _ in range(k):
                    self.add_txn(self.r_daytime(self.r_date(date(YEAR, 1, 6), date(YEAR, 12, 20)), 8, 16), frm, to,
                                 self.B[parent]["_rev"] * rng.uniform(0.004, 0.02), "international_wire",
                                 rng.choice(memos))
        customers = [p for p in self.persons if p.get("_customer") and 22 <= p["_age"] <= 75
                     and self.checking_of(p["person_id"])]
        for p in customers:
            r = rng.random()
            acct = self.checking_of(p["person_id"])
            if r < 0.045 and p["_salary"] > 15_000:
                cc, city, bank = rng.choice(N.REMITTANCE)
                rel = self._new_foreign_person(cc, city, p["last_name"])
                racct = self.new_account(rel["person_id"], bank, cc, "checking", self.r_date(date(2010, 1, 1),
                                                                                            date(2024, 1, 1)))
                self.add_rel(p["person_id"], rel["person_id"], rng.choice(["sibling", "parent"]))
                for _ in range(rng.randint(4, 12)):
                    amt = rng.choice([150, 200, 250, 300, 400, 500, 600, 800, 1000, 1200]) * rng.uniform(0.97, 1.03)
                    self.add_txn(self.r_daytime(self.r_date(date(YEAR, 1, 3), date(YEAR, 12, 28))), acct, racct, amt,
                                 "international_wire", "FAMILY SUPPORT")
            elif r < 0.055:
                cc, city, bank = rng.choice(N.REMITTANCE + N.FOREIGN_SUPPLIERS[:3])
                rel = self._new_foreign_person(cc, city, p["last_name"])
                racct = self.new_account(rel["person_id"], bank, cc, "checking", date(2008, 3, 1))
                self.add_rel(rel["person_id"], p["person_id"], "parent")
                for _ in range(rng.randint(1, 2)):
                    self.add_txn(self.r_daytime(self.r_date(date(YEAR, 1, 3), date(YEAR, 12, 28))), racct, acct,
                                 rng.uniform(2_000, 38_000), "international_wire", rng.choice(["GIFT", "INHERITANCE"]))
            elif p["_salary"] > 180_000 and r < 0.2:
                facct = self.accounts_of[self.fund_id][0]
                for _ in range(rng.randint(1, 2)):
                    self.add_txn(self.r_daytime(self.r_date(date(YEAR, 1, 3), date(YEAR, 11, 28))), acct, facct,
                                 rng.uniform(25_000, 150_000), "international_wire", "SUBSCRIPTION")
                if rng.random() < 0.4:
                    self.add_txn(self.r_daytime(self.r_date(date(YEAR, 6, 1), date(YEAR, 12, 28))), facct, acct,
                                 rng.uniform(4_000, 30_000), "international_wire", "DISTRIBUTION")

    def _misc_banking(self) -> None:
        rng = self.rng
        for pid, prim, acct, opened in self.new_2025_accounts:
            for _ in range(rng.randint(2, 6)):
                day = self.r_date(min(opened + timedelta(days=2), date(YEAR, 12, 20)), date(YEAR, 12, 28))
                self.add_txn(self.r_daytime(day), prim, acct, rng.choice([100, 200, 250, 500, 750, 1000, 1500, 2000]),
                             "ach", "TRANSFER")
        for p in self.persons:
            acct = self.checking_of(p["person_id"]) if p.get("_customer") and p["_age"] >= 18 else None
            if acct and rng.random() < 0.04:
                for _ in range(rng.randint(1, 2)):
                    day = self.r_date(date(YEAR, 1, 5), date(YEAR, 12, 28))
                    self.add_txn(self.r_daytime(day, 9, 17), acct, None, rng.uniform(500, 4_800), "cash_withdrawal", "",
                                 branch=self.branch_for(acct, p["_city"]), conducted_by=p["person_id"])

    def _invoice_memo(self, payee: dict) -> str:
        rng = self.rng
        if payee["_ind"] in ("consulting", "legal services", "accounting", "software") and rng.random() < 0.5:
            return rng.choice([f"CONSULTING SERVICES INV {rng.randint(100, 999)}", "ADVISORY FEE", "MANAGEMENT FEE",
                               f"MARKETING SERVICES INV {rng.randint(100, 999)}", "ADVISORY RETAINER",
                               f"CONSULTING SERVICES Q{rng.randint(1, 4)}"])
        return rng.choice([f"INV {rng.randint(1000, 99999)}", f"INV {rng.randint(1000, 99999)}",
                           f"INVOICE {rng.randint(1000, 9999)} PAYMENT", "SERVICES", "MARKETING", "MEDIA BUYING"
                           if payee["_ind"] == "software" else "SERVICES"])

    def _misc_banking_extra(self) -> None:
        """Owner draws, loans, capital injections, refunds and isolated near-threshold cash deposits."""
        rng = self.rng
        regular = [b for b in self.businesses if b["_infra"] is None and b.get("_size", 0) > 0]
        lenders = [self.checking_of(b) for b in self.infra["lender"]]
        sba = self.checking_of(self.infra["sba_lender"][0])
        utilities = [self.checking_of(b) for b in self.infra["utility"]]
        for b in regular:
            bacct = self.checking_of(b["business_id"])
            owners = [o for o, _p in b["_owners"] if o.startswith("P") and self.checking_of(o)]
            if owners and rng.random() < 0.45:
                o = rng.choice(owners)
                n = rng.randint(3, 10)
                for _ in range(n):
                    self.add_txn(self.r_daytime(self.r_date(date(YEAR, 1, 10), date(YEAR, 12, 28))), bacct,
                                 self.checking_of(o), b["_rev"] * rng.uniform(0.01, 0.04) / n * 4, "ach",
                                 rng.choice(["OWNER DRAW", "DISTRIBUTION", "MANAGEMENT FEE", "SHAREHOLDER DISTRIBUTION"]))
            if rng.random() < 0.04:
                self.add_txn(self.r_daytime(self.r_date(date(YEAR, 1, 10), date(YEAR, 11, 28))), sba, bacct,
                             rng.uniform(50_000, 400_000), "ach", "SBA 7A LOAN DISBURSEMENT")
            if owners and rng.random() < 0.04:
                self.add_txn(self.r_daytime(self.r_date(date(YEAR, 1, 10), date(YEAR, 11, 28))),
                             self.checking_of(owners[0]), bacct, rng.uniform(10_000, 80_000), "wire",
                             "CAPITAL CONTRIBUTION")
            if rng.random() < 0.03:
                self.add_txn(self.r_daytime(self.r_date(date(YEAR, 1, 10), date(YEAR, 12, 28))), rng.choice(utilities),
                             bacct, rng.uniform(40, 600), "ach", "REFUND")
        for p in self.persons:
            acct = self.checking_of(p["person_id"]) if p.get("_customer") and p["_age"] >= 21 else None
            if not acct:
                continue
            r = rng.random()
            if r < 0.02:
                self.add_txn(self.r_daytime(self.r_date(date(YEAR, 1, 10), date(YEAR, 11, 28))), rng.choice(lenders),
                             acct, rng.uniform(5_000, 35_000), "ach", "LOAN DISBURSEMENT")
            elif r < 0.03:
                self.add_txn(self.r_daytime(self.r_date(date(YEAR, 1, 10), date(YEAR, 12, 28))), rng.choice(utilities),
                             acct, rng.uniform(20, 300), "ach", "REFUND")
            elif r < 0.045:
                # one or two isolated near-threshold cash deposits (sold a car, cashed savings) — not structuring
                first = self.r_date(date(YEAR, 1, 10), date(YEAR, 6, 30))
                days = [first] + ([first + timedelta(days=rng.randint(50, 150))] if rng.random() < 0.3 else [])
                for d in days:
                    self.add_txn(self.r_daytime(d, 9, 17), None, acct, rng.uniform(7_100, 9_950), "cash_deposit",
                                 rng.choice(["", "", "vehicle sale"]), branch=self.branch_for(acct, p["_city"]),
                                 conducted_by=p["person_id"])
