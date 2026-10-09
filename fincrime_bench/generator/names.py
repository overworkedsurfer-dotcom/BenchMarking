"""Vocabulary for synthetic entities. All names are fictional combinations."""

FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer", "Michael", "Linda", "David", "Elizabeth",
    "William", "Barbara", "Richard", "Susan", "Joseph", "Jessica", "Thomas", "Sarah", "Charles", "Karen",
    "Christopher", "Lisa", "Daniel", "Nancy", "Matthew", "Betty", "Anthony", "Sandra", "Mark", "Margaret",
    "Donald", "Ashley", "Steven", "Kimberly", "Andrew", "Emily", "Paul", "Donna", "Joshua", "Michelle",
    "Kenneth", "Carol", "Kevin", "Amanda", "Brian", "Melissa", "George", "Deborah", "Timothy", "Stephanie",
    "Ronald", "Dorothy", "Jason", "Rebecca", "Edward", "Sharon", "Jeffrey", "Laura", "Ryan", "Cynthia",
    "Jacob", "Amy", "Gary", "Kathleen", "Nicholas", "Angela", "Eric", "Shirley", "Jonathan", "Brenda",
    "Stephen", "Emma", "Larry", "Anna", "Justin", "Pamela", "Scott", "Nicole", "Brandon", "Samantha",
    "Benjamin", "Katherine", "Samuel", "Christine", "Gregory", "Helen", "Alexander", "Debra", "Patrick",
    "Rachel", "Frank", "Carolyn", "Raymond", "Janet", "Jack", "Maria", "Dennis", "Olivia", "Jerry",
    "Heather", "Tyler", "Diane", "Aaron", "Julie", "Jose", "Joyce", "Adam", "Victoria", "Nathan", "Ruth",
    "Henry", "Virginia", "Zachary", "Lauren", "Douglas", "Kelly", "Peter", "Christina", "Kyle", "Joan",
    "Noah", "Evelyn", "Ethan", "Judith", "Jeremy", "Andrea", "Walter", "Hannah", "Christian", "Megan",
    "Keith", "Cheryl", "Roger", "Jacqueline", "Terry", "Martha", "Austin", "Madison", "Sean", "Teresa",
    "Gerald", "Gloria", "Carl", "Sara", "Harold", "Janice", "Dylan", "Ann", "Arthur", "Kathryn", "Lawrence",
    "Abigail", "Jordan", "Sophia", "Jesse", "Frances", "Bryan", "Jean", "Billy", "Alice", "Bruce", "Judy",
    "Gabriel", "Isabella", "Joe", "Julia", "Logan", "Grace", "Alan", "Amber", "Juan", "Denise", "Albert",
    "Danielle", "Willie", "Marilyn", "Elijah", "Beverly", "Wayne", "Charlotte", "Randy", "Natalie", "Mason",
    "Theresa", "Vincent", "Diana", "Liam", "Brittany", "Roy", "Doris", "Bobby", "Kayla", "Caleb", "Alexis",
    "Priya", "Wei", "Hiroshi", "Fatima", "Mohammed", "Aisha", "Carlos", "Lucia", "Dmitri", "Anya", "Kwame",
    "Ngozi", "Raj", "Mei", "Tariq", "Leila", "Mateo", "Sofia", "Ivan", "Elena", "Omar", "Yara", "Kenji",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
    "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson",
    "Walker", "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores", "Green",
    "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell", "Mitchell", "Carter", "Roberts", "Gomez",
    "Phillips", "Evans", "Turner", "Diaz", "Parker", "Cruz", "Edwards", "Collins", "Reyes", "Stewart",
    "Morris", "Morales", "Murphy", "Cook", "Rogers", "Gutierrez", "Ortiz", "Morgan", "Cooper", "Peterson",
    "Bailey", "Reed", "Kelly", "Howard", "Ramos", "Kim", "Cox", "Ward", "Richardson", "Watson", "Brooks",
    "Chavez", "Wood", "James", "Bennett", "Gray", "Mendoza", "Ruiz", "Hughes", "Price", "Alvarez", "Castillo",
    "Sanders", "Patel", "Myers", "Long", "Ross", "Foster", "Jimenez", "Powell", "Jenkins", "Perry", "Russell",
    "Sullivan", "Bell", "Coleman", "Butler", "Henderson", "Barnes", "Gonzales", "Fisher", "Vasquez",
    "Simmons", "Romero", "Jordan", "Patterson", "Alexander", "Hamilton", "Graham", "Reynolds", "Griffin",
    "Wallace", "Moreno", "West", "Cole", "Hayes", "Bryant", "Herrera", "Gibson", "Ellis", "Tran", "Medina",
    "Aguilar", "Stevens", "Murray", "Ford", "Castro", "Marshall", "Owens", "Harrison", "Fernandez", "McDonald",
    "Woods", "Washington", "Kennedy", "Wells", "Vargas", "Henry", "Chen", "Freeman", "Webb", "Tucker",
    "Guzman", "Burns", "Crawford", "Olson", "Simpson", "Porter", "Hunter", "Gordon", "Mendez", "Silva",
    "Shaw", "Snyder", "Mason", "Dixon", "Munoz", "Hunt", "Hicks", "Holmes", "Palmer", "Wagner", "Black",
    "Robertson", "Boyd", "Rose", "Stone", "Salazar", "Fox", "Warren", "Mills", "Meyer", "Rice", "Schmidt",
    "Okafor", "Novak", "Kowalski", "Ivanova", "Haddad", "Yamamoto", "Sato", "Singh", "Kaur", "Wang", "Zhang",
    "Petrov", "Rossi", "Dubois", "Müller", "Andersen", "Larsen", "Mensah", "Abara", "Farouk", "Navarro",
]

CITIES = [
    # city, state, weight
    ("Columbus", "OH", 10), ("Austin", "TX", 12), ("Denver", "CO", 9), ("Portland", "OR", 8),
    ("Tampa", "FL", 9), ("Raleigh", "NC", 7), ("Phoenix", "AZ", 11), ("Sacramento", "CA", 8),
    ("Nashville", "TN", 7), ("Minneapolis", "MN", 7), ("Newark", "NJ", 6), ("Las Vegas", "NV", 6),
]

STREETS = [
    "Oak", "Maple", "Cedar", "Pine", "Elm", "Washington", "Lake", "Hill", "Park", "Main", "Sunset", "River",
    "Highland", "Meadow", "Forest", "Ridge", "Willow", "Spring", "Church", "Mill", "Valley", "Cherry",
    "Jefferson", "Lincoln", "Franklin", "Madison", "Chestnut", "Walnut", "Birch", "Aspen", "Lakeview",
    "Grove", "Prospect", "Broad", "Market", "Union", "Center", "Harbor", "Canyon", "Mesa",
]
STREET_SUFFIX = ["St", "Ave", "Rd", "Blvd", "Ln", "Dr", "Ct", "Way", "Pl"]

BUSINESS_WORDS = [
    "Summit", "Pioneer", "Evergreen", "Liberty", "Keystone", "Horizon", "Granite", "Cascade", "Beacon",
    "Sterling", "Redwood", "Lakeside", "Northgate", "Silverline", "Bluebird", "Ironwood", "Crescent",
    "Heritage", "Trailhead", "Copperfield", "Harborview", "Riverside", "Prairie", "Falcon", "Oakmont",
    "Brightwater", "Clearview", "Westbrook", "Stonebridge", "Golden", "Patriot", "Union", "Frontier",
    "Maplewood", "Highland", "Atlas", "Juniper", "Mosaic", "Cobalt", "Vertex", "Orchard", "Lantern",
]

SHELL_WORDS = [
    "Aurora", "Meridian", "Northstar", "Solace", "Zenith", "Paragon", "Equinox", "Orion", "Halcyon",
    "Vantage", "Apex", "Corvus", "Lumen", "Altura", "Nexus", "Sapphire", "Obsidian", "Monarch", "Cygnus",
    "Valor", "Talisman", "Arcadia", "Polaris", "Vesper", "Sirius", "Elysian", "Kestrel", "Onyx", "Avalon",
    "Celeste", "Marlin", "Tidewater", "Ambrose", "Belmont", "Carrick", "Dunmore", "Everly", "Fairhaven",
]
SHELL_SUFFIX = ["Holdings", "Ventures", "Capital", "Global", "Consulting Group", "Trading", "Partners",
                "Investments", "Advisory", "International", "Management", "Resources"]

# industry -> (weight, employee range, revenue per employee, card share, cash share)
INDUSTRIES = {
    "restaurant": (14, (4, 30), 75_000, 0.68, 0.30),
    "retail": (12, (3, 25), 110_000, 0.85, 0.12),
    "construction": (9, (3, 40), 160_000, 0.0, 0.03),
    "consulting": (8, (1, 20), 170_000, 0.0, 0.0),
    "healthcare": (6, (5, 40), 140_000, 0.25, 0.0),
    "legal services": (4, (2, 15), 210_000, 0.10, 0.0),
    "property management": (6, (2, 12), 150_000, 0.0, 0.0),
    "logistics": (5, (5, 50), 130_000, 0.0, 0.0),
    "manufacturing": (5, (10, 80), 190_000, 0.0, 0.0),
    "software": (5, (3, 40), 230_000, 0.05, 0.0),
    "salon": (5, (2, 10), 60_000, 0.55, 0.42),
    "laundromat": (3, (1, 5), 70_000, 0.25, 0.72),
    "car wash": (3, (2, 10), 65_000, 0.45, 0.52),
    "grocery": (4, (5, 30), 120_000, 0.80, 0.18),
    "accounting": (4, (2, 15), 140_000, 0.05, 0.0),
    "auto repair": (5, (2, 12), 95_000, 0.70, 0.20),
}

CASH_INTENSIVE = ["restaurant", "salon", "laundromat", "car wash"]

OCCUPATION_BY_INDUSTRY = {
    "restaurant": ["Cook", "Server", "Restaurant Manager", "Dishwasher", "Host"],
    "retail": ["Sales Associate", "Store Manager", "Cashier", "Stock Clerk"],
    "construction": ["Carpenter", "Electrician", "Project Manager", "Laborer", "Plumber"],
    "consulting": ["Consultant", "Analyst", "Partner", "Office Manager"],
    "healthcare": ["Nurse", "Physician", "Medical Assistant", "Billing Specialist"],
    "legal services": ["Attorney", "Paralegal", "Legal Secretary"],
    "property management": ["Property Manager", "Maintenance Technician", "Leasing Agent"],
    "logistics": ["Driver", "Dispatcher", "Warehouse Associate", "Logistics Coordinator"],
    "manufacturing": ["Machinist", "Production Supervisor", "Quality Engineer", "Assembler"],
    "software": ["Software Engineer", "Product Manager", "Data Analyst", "Designer"],
    "salon": ["Stylist", "Receptionist", "Nail Technician"],
    "laundromat": ["Attendant", "Manager"],
    "car wash": ["Attendant", "Shift Manager"],
    "grocery": ["Cashier", "Butcher", "Stock Clerk", "Store Manager"],
    "accounting": ["Accountant", "Tax Preparer", "Bookkeeper"],
    "auto repair": ["Mechanic", "Service Writer", "Technician"],
    "infrastructure": ["Customer Service Rep", "Engineer", "Analyst", "Manager"],
}

SELF_EMPLOYED_OCCUPATIONS = [
    "Freelance Designer", "Independent Contractor", "Real Estate Agent", "Handyman", "Photographer",
    "IT Consultant", "Personal Trainer", "Rideshare Driver", "Tutor", "Copywriter", "Landscaper",
]

DOMESTIC_BANKS = [("First Meridian Bank", 0.55), ("Coastal Federal Bank", 0.28), ("Pinnacle Trust", 0.17)]

OFFSHORE = [
    # country code, jurisdiction label, bank name, entity type
    ("VG", "British Virgin Islands", "Tortola Private Bank", "Ltd"),
    ("KY", "Cayman Islands", "Caymanian Fiduciary Bank", "Ltd"),
    ("PA", "Panama", "Banco Istmo Privado", "SA"),
    ("CY", "Cyprus", "Limassol Commercial Bank", "Ltd"),
    ("SC", "Seychelles", "Mahe International Bank", "Ltd"),
    ("BZ", "Belize", "Belize Harbour Bank", "Ltd"),
    ("AE", "United Arab Emirates", "Gulf Meridian Bank", "FZE"),
]

FOREIGN_TRAVEL_COUNTRIES = ["MX", "CA", "GB", "FR", "IT", "ES", "JP", "DE", "PT", "CR", "GR"]
ATTACKER_COUNTRIES = ["RO", "NG", "VN", "UA", "BR", "ID", "GB", "NL", "DE"]

CARRIERS = ["Vertex Wireless", "Nimbus Mobile", "Coastline Cellular", "PrairieTel"]

# Legitimate foreign counterparties (country, city, bank)
FOREIGN_SUPPLIERS = [
    ("CN", "Shenzhen", "Pearl River Commercial Bank"), ("MX", "Monterrey", "Banco Regiomontano"),
    ("DE", "Hamburg", "Hanse Handelsbank"), ("VN", "Ho Chi Minh City", "Saigon Trade Bank"),
    ("IN", "Pune", "Deccan Commerce Bank"), ("CA", "Toronto", "Maple Commercial Bank"),
    ("IT", "Milan", "Banca Lombarda Commerciale"), ("KR", "Busan", "Busan Harbor Bank"),
    ("TW", "Taichung", "Formosa Trade Bank"), ("TR", "Izmir", "Aegean Commerce Bank"),
]
REMITTANCE = [
    ("MX", "Guadalajara", "Banco Regiomontano"), ("PH", "Cebu", "Visayas Savings Bank"),
    ("IN", "Hyderabad", "Deccan Commerce Bank"), ("NG", "Lagos", "Lagoon Commercial Bank"),
    ("GT", "Quetzaltenango", "Banco del Altiplano"), ("VN", "Da Nang", "Saigon Trade Bank"),
    ("CO", "Medellin", "Banco Antioqueno"), ("PL", "Krakow", "Wisla Savings Bank"),
]
FOREIGN_OCCUPATIONS = ["Engineer", "Teacher", "Shop Owner", "Nurse", "Farmer", "Accountant", "Retired", "Driver"]
