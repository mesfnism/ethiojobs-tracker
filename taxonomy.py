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

# A "skill" is a short noun phrase ("Teamwork", "Attention to detail"), not
# a sentence fragment that bled in from a free-text description (e.g. "a
# genuine passion for education and student development") and not a
# comma-joined list of fields/categories mistakenly tagged as a skill (e.g.
# "Education, Social Work, Project Management"). Reject anything shaped
# like either of those rather than counting it as a skill.
_MAX_SKILL_WORDS = 6
_SENTENCE_LIKE_SKILL_RE = re.compile(
    r"\b(a genuine|passion for|ability to|in order to|responsible for|"
    r"responsible to|work(ing)? with others|and other duties|such as|"
    r"including but not limited|is required|are required|will be|"
    r"must be able)\b",
    re.I,
)


def _looks_like_skill_noise(s):
    if _SENTENCE_LIKE_SKILL_RE.search(s):
        return True
    if len(s.split()) > _MAX_SKILL_WORDS:
        return True
    if "," in s:
        parts = [p.strip() for p in s.split(",") if p.strip()]
        # A comma list where every item starts with a capital letter reads
        # as a list of fields/categories ("Education, Social Work, Project
        # Management"), not a single skill.
        if len(parts) >= 2 and all(p[:1].isupper() for p in parts):
            return True
        if len(parts) >= 3:
            return True
    return False

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
    if _looks_like_skill_noise(s):
        return None
    return title_case_label(s.strip(". "))


def esco_group_for_skill(skill_label):
    for pattern, group in _ESCO_GROUP_RULES:
        if pattern.search(skill_label):
            return group
    return "Not classified"


# ---------------------------------------------------------------------------
# Display label casing
# ---------------------------------------------------------------------------
# Site text arrives in a mix of ALL CAPS, all lowercase, and Title Case.
# This normalizes any label shown on the dashboard to one consistent style,
# without mangling acronyms (NGO, IT, HR, ICT) that happen to be short and
# fully capitalized already.

_SMALL_WORDS = {"and", "or", "of", "the", "in", "on", "for", "to", "a", "an", "with", "&"}
_KNOWN_ACRONYMS = {
    "ngo", "ngos", "it", "hr", "ict", "erp", "crm", "hiv", "aids", "un",
    "eu", "usa", "uk", "phd", "ceo", "cfo", "coo", "cto", "gis", "gps",
    "plc", "sc", "llc", "ltd",
}


def title_case_label(label):
    """Title-cases a display label, preserving short acronyms and leaving
    an already mixed-case word (e.g. an acronym embedded mid-phrase) as
    written rather than re-lowercasing it."""
    if not label:
        return label
    tokens = re.split(r"(\s+)", label.strip())
    out = []
    word_i = 0
    for tok in tokens:
        if not tok.strip():
            out.append(tok)
            continue
        core = tok.strip(".,;:()")
        lead = tok[: len(tok) - len(tok.lstrip(".,;:()"))]
        trail = tok[len(lead) + len(core):]
        if not core:
            out.append(tok)
            word_i += 1
            continue
        lower_core = core.lower()
        if lower_core in _KNOWN_ACRONYMS:
            new_core = core.upper()
        elif any(c.isupper() for c in core[1:]) and not core.isupper():
            # already mixed-case (e.g. "McKinsey", "eLearning") -> leave as-is
            new_core = core
        elif word_i > 0 and lower_core in _SMALL_WORDS:
            new_core = lower_core
        else:
            new_core = lower_core[:1].upper() + lower_core[1:]
        out.append(lead + new_core + trail)
        word_i += 1
    return "".join(out)


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


CATEGORY_EXPANSIONS = {
    "Social Sciences and Com": "Social Sciences and Communications",
}

_BAD_CATEGORY_LABELS = {"other", "others", "n/a", "none", "general"}


def normalize_category_label(raw):
    """Cleans up a posting's own raw category/sub-sector tag for display as
    a second, finer-grained hierarchy level beneath the ISIC section (e.g.
    the ISIC section might be "Professional, scientific and technical
    activities" while this sub-category is "Economics" or "Project
    Management"). This does not map onto any external scheme — it is the
    site's own tag, just cleaned and cased consistently."""
    if not raw:
        return None
    s = raw.strip().rstrip(".")
    if not s:
        return None
    s = CATEGORY_EXPANSIONS.get(s, s)
    if s.lower() in _BAD_CATEGORY_LABELS:
        return None
    return title_case_label(s)


# ---------------------------------------------------------------------------
# Experience required — bucketed into standard ranges
# ---------------------------------------------------------------------------
# Postings state years-of-experience in free text ("2 years", "3-5 years",
# "minimum 5 years", "fresh graduate"). This buckets that text into a small
# number of standard ranges for a chart, rather than one bar per distinct
# phrasing.

EXPERIENCE_BUCKET_ORDER = ["0-1 years", "1-3 years", "3-5 years", "5-8 years", "8+ years"]
_EXPERIENCE_BUCKETS = [
    ("0-1 years", 0, 1),
    ("1-3 years", 1, 3),
    ("3-5 years", 3, 5),
    ("5-8 years", 5, 8),
    ("8+ years", 8, None),
]
_EXP_FRESH_RE = re.compile(r"fresh|no experience|\b0\s*years?\b|entry[\s\-]?level", re.I)
_EXP_RANGE_RE = re.compile(r"(\d+)\s*(?:-|to|–)\s*(\d+)")
_EXP_PLUS_RE = re.compile(r"(\d+)\s*\+")
_EXP_NUM_RE = re.compile(r"(\d+)")


def experience_bucket(raw):
    """Returns one of EXPERIENCE_BUCKET_ORDER, or None if the text doesn't
    state a number of years at all (e.g. blank, or non-numeric text our
    rules don't recognize — left out of the chart rather than guessed)."""
    if not raw:
        return None
    s = raw.strip()
    if _EXP_FRESH_RE.search(s):
        return "0-1 years"
    m = _EXP_RANGE_RE.search(s)
    if m:
        lo = int(m.group(1))
    else:
        m = _EXP_PLUS_RE.search(s)
        if m:
            lo = int(m.group(1))
        else:
            m = _EXP_NUM_RE.search(s)
            if not m:
                return None
            lo = int(m.group(1))
    for label, start, end in _EXPERIENCE_BUCKETS:
        if end is None:
            if lo >= start:
                return label
        elif start <= lo < end:
            return label
    return "8+ years"


# ---------------------------------------------------------------------------
# Education specialization — "<level> in what?"
# ---------------------------------------------------------------------------
# ISCED (above) only captures the level (Bachelor's, Master's, PhD...).
# This extracts the FIELD of study mentioned in a posting's free-text
# description, where available, so the dashboard can show "PhD in what?"
# when a user drills into a level. Coverage is necessarily partial: only
# postings with free-text descriptions (mainly HaHuJobs) carry this; a
# site that only stores the structured level (EthioJobs) will show as
# "specialization not stated" rather than being guessed at.

SPECIALIZATION_FIELDS = [
    "Business Administration", "Accounting", "Economics", "Management",
    "Civil Engineering", "Electrical Engineering", "Mechanical Engineering",
    "Engineering", "Computer Science", "Information Technology",
    "Information Systems", "Statistics", "Social Work", "Sociology",
    "Public Health", "Nursing", "Medicine", "Law", "Agriculture",
    "Agricultural Economics", "Project Management", "Finance", "Marketing",
    "Human Resource Management", "Education", "Development Studies",
    "Gender Studies", "Environmental Science", "Supply Chain Management",
    "Logistics", "International Relations", "Political Science",
    "Psychology", "Architecture", "Pharmacy", "Veterinary Medicine",
    "Public Administration", "Banking and Finance", "Procurement",
    "Journalism", "Communications",
]

# Longer/more specific phrases first, so "Agricultural Economics" matches
# before the more generic "Economics" inside the same text.
_SPECIALIZATION_FIELDS_SORTED = sorted(SPECIALIZATION_FIELDS, key=len, reverse=True)
_FIELD_PATTERNS = [
    (re.compile(r"\b" + re.escape(f) + r"\b", re.I), f) for f in _SPECIALIZATION_FIELDS_SORTED
]


def extract_specializations(text):
    """Returns the list of recognized fields of study mentioned in free
    text (e.g. a job description), without double-counting a more
    specific field that's a substring of a more general one."""
    if not text:
        return []
    found = []
    consumed_spans = []
    for pattern, field in _FIELD_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        span = m.span()
        if any(span[0] >= a and span[1] <= b for a, b in consumed_spans):
            continue
        found.append(field)
        consumed_spans.append(span)
    return found


# ---------------------------------------------------------------------------
# Employer type — Private / Public / NGO
# ---------------------------------------------------------------------------
# A keyword heuristic on the employer's own name, not a verified registry
# lookup. Ethiopian employer names are fairly consistent about signaling
# their own type (legal suffixes for private companies, "Ministry"/
# "Authority"/"University" for public bodies, "Foundation"/"International"/
# relief-agency names for NGOs) so this catches most cases, but an
# unmatched name is reported as "Unclassified" rather than guessed at.

_PUBLIC_EMPLOYER_RE = re.compile(
    r"\b(ministry|minister|bureau|authority|commission|federal|"
    r"regional government|city administration|public service|"
    r"government|agency|kebele|woreda|university|regional state)\b",
    re.I,
)
_NGO_EMPLOYER_RE = re.compile(
    r"\b(ngo|non[\s\-]?governmental|foundation|relief|charity|"
    r"humanitarian|cooperative|association|save the children|"
    r"plan international|care international|unicef|undp|unhcr|unfpa|"
    r"usaid|world food programme|\bwfp\b|red cross|world vision|"
    r"international rescue committee|catholic relief|action aid)\b",
    re.I,
)
_PRIVATE_EMPLOYER_RE = re.compile(
    r"\b(plc|p\.l\.c\.?|s\.c\.?|share company|ltd|l\.l\.c\.?|"
    r"pvt\.?\s*ltd|trading|manufacturing|industries|private limited|"
    r"enterprise|business group|industrial)\b",
    re.I,
)


# ---------------------------------------------------------------------------
# Salary / remuneration — bucketed, where a posting states one
# ---------------------------------------------------------------------------
# Postings state pay in free text ("Birr 20000/Mon", "ETB 87,975.00",
# "Negotiable", "As per company scale"). This only extracts a figure when
# the text itself marks it as a monthly Birr/ETB amount — a bare number
# with no currency or "/month" marker is left unclassified rather than
# guessed at, since it's just as likely to be a typo, a reference number,
# or something that isn't pay at all.

SALARY_BUCKET_ORDER = [
    "Under 10,000 ETB", "10,000–20,000 ETB", "20,000–35,000 ETB",
    "35,000–60,000 ETB", "60,000+ ETB",
]
_SALARY_BUCKETS = [
    ("Under 10,000 ETB", 0, 10000),
    ("10,000–20,000 ETB", 10000, 20000),
    ("20,000–35,000 ETB", 20000, 35000),
    ("35,000–60,000 ETB", 35000, 60000),
    ("60,000+ ETB", 60000, None),
]
_SALARY_CURRENCY_RE = re.compile(r"\b(birr|etb)\b", re.I)
_SALARY_PER_MONTH_RE = re.compile(r"/\s*mon\b|per\s*month|/\s*month|monthly", re.I)
_SALARY_RANGE_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*(?:-|to|–)\s*([\d,]+(?:\.\d+)?)")
_SALARY_NUM_RE = re.compile(r"([\d,]+(?:\.\d+)?)")


def _salary_to_number(s):
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def parse_monthly_salary_etb(raw):
    """Returns a single monthly-ETB figure (the midpoint, for a stated
    range) only when the text itself marks the number as a Birr/ETB
    amount or a monthly figure — otherwise None."""
    if not raw:
        return None
    s = raw.strip()
    if not (_SALARY_CURRENCY_RE.search(s) or _SALARY_PER_MONTH_RE.search(s)):
        return None
    m = _SALARY_RANGE_RE.search(s)
    if m:
        lo, hi = _salary_to_number(m.group(1)), _salary_to_number(m.group(2))
        if lo is None or hi is None:
            return None
        return (lo + hi) / 2
    m = _SALARY_NUM_RE.search(s)
    if not m:
        return None
    return _salary_to_number(m.group(1))


def salary_bucket(raw):
    amount = parse_monthly_salary_etb(raw)
    if amount is None:
        return None
    for label, start, end in _SALARY_BUCKETS:
        if end is None:
            if amount >= start:
                return label
        elif start <= amount < end:
            return label
    return SALARY_BUCKET_ORDER[-1]


def employer_type(name):
    """Returns "Public", "NGO", "Private", or "Unclassified". Checked in
    this order because a public body (e.g. a university) occasionally
    also carries words that could otherwise look NGO-like."""
    if not name:
        return None
    if _PUBLIC_EMPLOYER_RE.search(name):
        return "Public"
    if _NGO_EMPLOYER_RE.search(name):
        return "NGO"
    if _PRIVATE_EMPLOYER_RE.search(name):
        return "Private"
    return "Unclassified"
