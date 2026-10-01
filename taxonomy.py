"""
Standard classification schemes used to group the raw, messy text that
EthioJobs postings contain into recognized international taxonomies,
instead of ad hoc labels invented for this project alone.

Four schemes, one per field:

  skills_required        -> ESCO   (EU skills/competences taxonomy)
  job_title               -> ISCO-08 (ILO International Standard
                                     Classification of Occupations)
  education_required      -> ISCED 2011 (UNESCO International Standard
                                     Classification of Education)
  category / employer     -> ISIC Rev.4 (UN International Standard
                                     Industrial Classification)

None of these require an internet call at run time: each is a small,
static reference table (the handful of top-level groups relevant to a
labour-market dashboard, not the full multi-thousand-row official
download), matched against posting text with keyword rules. That keeps
the pipeline offline and fast, at the cost of being a best-effort
classifier rather than an exact lookup — a posting whose text does not
match any rule is left "Not classified" rather than guessed at, the
same honesty-over-coverage approach used elsewhere in this project.

Where a posting doesn't match anything, it is excluded from that
chart's counts rather than forced into a wrong bucket - the dashboard
already states "X of Y state a level" for this reason.
"""

import re
from collections import Counter

# ---------------------------------------------------------------------------
# ESCO — skills
# ---------------------------------------------------------------------------
# ESCO's top-level "skill reuse level" pillars (the broadest tier of its
# skills hierarchy). Every individual skill phrase gets normalized (merging
# spacing/casing/plural variants) and then assigned to one of these groups.

ESCO_SKILL_GROUPS = [
    "Communication, collaboration and creativity",
    "Information skills",
    "Assisting and caring",
    "Management skills",
    "Working with computers",
    "Constructing",
    "Handling and moving",
    "Working with machinery and specialised equipment",
]

# Normalizes raw skill text into one canonical phrase, so "Teamwork",
# "Team work" and "team-working skills" all collapse into a single count
# instead of splitting the same skill across several bars.
_SKILL_NORMALIZE_RULES = [
    (re.compile(r"team[\s\-]?work(ing)?( skills?)?", re.I), "Teamwork"),
    (re.compile(r"problem[\s\-]?solving( skills?)?", re.I), "Problem solving"),
    (re.compile(r"decision[\s\-]?making( skills?)?", re.I), "Decision-making"),
    (re.compile(r"communication( skills?)?", re.I), "Communication"),
    (re.compile(r"time[\s\-]?management( skills?)?", re.I), "Time management"),
    (re.compile(r"attention to detail", re.I), "Attention to detail"),
    (re.compile(r"leadership( skills?)?", re.I), "Leadership"),
    (re.compile(r"interpersonal( skills?)?", re.I), "Interpersonal skills"),
    (re.compile(r"negotiation( skills?)?", re.I), "Negotiation"),
    (re.compile(r"analytical( thinking)?( skills?)?", re.I), "Analytical thinking"),
    (re.compile(r"creativity|creative( skills?)?", re.I), "Creativity"),
    (re.compile(r"customer service( skills?)?", re.I), "Customer service"),
    (re.compile(r"report(ing)? writing", re.I), "Report writing"),
    (re.compile(r"microsoft office|ms office", re.I), "Microsoft Office"),
    (re.compile(r"computer( literacy| skills?)?", re.I), "Computer literacy"),
    (re.compile(r"accounting( principles)?", re.I), "Accounting"),
    (re.compile(r"coordination( and networking)?( skills?)?", re.I), "Coordination"),
]

_BAD_SKILL_LABELS = {"desired skill", "required skill", "skills", "skill", "other"}
_SKILL_DATE_RE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d",
    re.I,
)

# Keyword -> ESCO top-level group, checked against the (already normalized)
# skill label. Order matters: first match wins.
_ESCO_GROUP_RULES = [
    (re.compile(r"team|communicat|creativ|interpersonal|negotiat|leadership", re.I),
     "Communication, collaboration and creativity"),
    (re.compile(r"report|analy|research|attention to detail|decision", re.I),
     "Information skills"),
    (re.compile(r"customer service|care|assist", re.I),
     "Assisting and caring"),
    (re.compile(r"management|coordinat|planning|supervis|project", re.I),
     "Management skills"),
    (re.compile(r"computer|excel|office|software|erp|it\b|ifrs|gaap|accounting|finance", re.I),
     "Working with computers"),
    (re.compile(r"construct|build|engineer", re.I), "Constructing"),
    (re.compile(r"logistic|transport|handling|moving|warehouse|driving", re.I),
     "Handling and moving"),
    (re.compile(r"machin|equipment|technical maintenance|operat", re.I),
     "Working with machinery and specialised equipment"),
]


def normalize_skill(raw):
    s = raw.strip(" •-\t.")
    if not s or len(s) < 3 or _SKILL_DATE_RE.search(s):
        return None
    if s.lower().strip(".") in _BAD_SKILL_LABELS:
        return None
    for pattern, canon in _SKILL_NORMALIZE_RULES:
        if pattern.fullmatch(s.strip(".")) or pattern.search(s):
            return canon
    return s.strip(". ")


def esco_group_for_skill(skill_label):
    for pattern, group in _ESCO_GROUP_RULES:
        if pattern.search(skill_label):
            return group
    return "Not classified"


# ---------------------------------------------------------------------------
# ISCO-08 — occupations (job titles)
# ---------------------------------------------------------------------------
# The 10 major groups of the ILO's International Standard Classification
# of Occupations, in their official order (used as the sort order when a
# window has postings in every group).

ISCO_MAJOR_GROUPS = [
    "Managers",
    "Professionals",
    "Technicians and associate professionals",
    "Clerical support workers",
    "Service and sales workers",
    "Skilled agricultural, forestry and fishery workers",
    "Craft and related trades workers",
    "Plant and machine operators, and assemblers",
    "Elementary occupations",
    "Armed forces occupations",
]

_ISCO_RULES = [
    (re.compile(r"\b(director|head of|chief|general manager|branch manager|"
                r"country manager|managing director|principal|president)\b", re.I),
     "Managers"),
    (re.compile(r"\bmanager\b", re.I), "Managers"),
    (re.compile(r"\b(officer|specialist|analyst|engineer|accountant|auditor|"
                r"economist|consultant|researcher|advisor|adviser|expert|"
                r"scientist|lawyer|architect|pharmacist|physician|doctor|"
                r"nurse practitioner|professor|lecturer)\b", re.I),
     "Professionals"),
    (re.compile(r"\b(technician|associate|supervisor|coordinator|inspector|"
                r"nurse|foreman)\b", re.I),
     "Technicians and associate professionals"),
    (re.compile(r"\b(secretary|clerk|cashier|receptionist|data encoder|"
                r"admin(istrative)? assistant|typist|record keeper)\b", re.I),
     "Clerical support workers"),
    (re.compile(r"\b(sales|marketing|customer service|waiter|waitress|"
                r"security guard|guard|barista|cook|chef|sales consultant|"
                r"promoter|cashier)\b", re.I),
     "Service and sales workers"),
    (re.compile(r"\b(farm|agricultur|fishery|forestry|livestock)\b", re.I),
     "Skilled agricultural, forestry and fishery workers"),
    (re.compile(r"\b(mechanic|electrician|plumber|welder|carpenter|"
                r"craftsman|technician \(trade\)|mason|tailor)\b", re.I),
     "Craft and related trades workers"),
    (re.compile(r"\b(machine operator|driver|assembler|forklift)\b", re.I),
     "Plant and machine operators, and assemblers"),
    (re.compile(r"\b(cleaner|laborer|labourer|messenger|porter|helper)\b", re.I),
     "Elementary occupations"),
]


def isco_group_for_title(title):
    if not title:
        return "Not classified"
    for pattern, group in _ISCO_RULES:
        if pattern.search(title):
            return group
    return "Not classified"


# ---------------------------------------------------------------------------
# ISCED 2011 — education levels
# ---------------------------------------------------------------------------
# UNESCO's International Standard Classification of Education. Levels 0-3
# rarely appear as a job *requirement*, so only the levels actually seen in
# EthioJobs postings are mapped; anything else (garbled text, free-form
# requirements with no clean structured label) is left unclassified.

ISCED_LEVELS = {
    "Certificate": (4, "Post-secondary non-tertiary education"),
    "Diploma": (5, "Short-cycle tertiary education"),
    "Bachelor's degree": (6, "Bachelor's or equivalent"),
    "Master's degree": (7, "Master's or equivalent"),
    "PhD": (8, "Doctoral or equivalent"),
}

# Ordered by ISCED level, ascending
ISCED_ORDER = ["Certificate", "Diploma", "Bachelor's degree", "Master's degree", "PhD"]


def isced_for_education(raw):
    if raw in ISCED_LEVELS:
        level, label = ISCED_LEVELS[raw]
        return {"raw": raw, "isced_level": level, "isced_label": label}
    return None


# ---------------------------------------------------------------------------
# ISIC Rev.4 — sectors (from the posting's own category tags / employer name)
# ---------------------------------------------------------------------------
# The UN's International Standard Industrial Classification, sections A-U.
# Only the sections that plausibly show up on an Ethiopian general job
# board are given keyword rules; anything unmatched is left unclassified
# rather than guessed at.

ISIC_SECTIONS = {
    "A": "Agriculture, forestry and fishing",
    "C": "Manufacturing",
    "F": "Construction",
    "G": "Wholesale and retail trade",
    "H": "Transportation and storage",
    "I": "Accommodation and food service activities",
    "J": "Information and communication",
    "K": "Financial and insurance activities",
    "M": "Professional, scientific and technical activities",
    "N": "Administrative and support service activities",
    "O": "Public administration and defence",
    "P": "Education",
    "Q": "Human health and social work activities",
    "U": "Activities of extraterritorial organizations and bodies",
}

_ISIC_RULES = [
    (re.compile(r"agricultur|farm|forestry|fishery", re.I), "A"),
    (re.compile(r"manufactur|factory|industrial engineering", re.I), "C"),
    (re.compile(r"construction|civil engineering|architecture", re.I), "F"),
    (re.compile(r"sales and marketing|retail|wholesale|trade\b", re.I), "G"),
    (re.compile(r"logistics|transport|supply chain|warehous", re.I), "H"),
    (re.compile(r"hotel|hospitality|tourism|food service|restaurant", re.I), "I"),
    (re.compile(r"it,? computer science|information technology|software|"
                r"telecom", re.I), "J"),
    (re.compile(r"accounting and finance|banking|insurance|microfinance", re.I), "K"),
    (re.compile(r"economics|management|business and administrat|consulting|"
                r"engineering\b|natural sciences|legal", re.I), "M"),
    (re.compile(r"admin, secretarial|human resource|hr\b", re.I), "N"),
    (re.compile(r"government|public administration|ngo|non-?profit", re.I), "O"),
    (re.compile(r"education|training|teaching", re.I), "P"),
    (re.compile(r"health care|medical|pharma|social work", re.I), "Q"),
    (re.compile(r"international|humanitarian|donor|un\b|relief", re.I), "U"),
]


def isic_section_for_text(text):
    if not text:
        return None
    for pattern, code in _ISIC_RULES:
        if pattern.search(text):
            return {"code": code, "label": ISIC_SECTIONS[code]}
    return None
