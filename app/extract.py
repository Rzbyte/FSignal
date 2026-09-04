"""Identity extraction for social launch signals.

This module answers one question: *which company is this post about, and is the
post a founder announcing acceptance?* It deliberately does no scoring -- that
lives in `intelligence.py` -- because merging the two is how a "confidence" number
ends up meaning nothing.

The design is positive-first. Rather than trying to enumerate every kind of
YC-adjacent chatter, a candidate has to earn an identity through one of a few
high-precision shapes, and then survive a disqualifying-context check. Anything
that fails gets a reason code instead of an alert, so the suppression is
auditable rather than invisible.

Every pattern here was written against a captured corpus of real LinkedIn results,
including the cases that make naive matching fail: a genuine announcement that
also mentions a past rejection, YC's own congratulation posts, recruiter posts
that say "backed by Y Combinator", and page chrome like "Jane Doe's Post".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: YC season codes as founders write them. `P` is not a typo: the Spring 2026
#: batch appears in live posts as both "YC X26" (Minicor) and "YC P26" (Andustry,
#: Klaimee), and all three are "Spring 2026" in the official directory.
BATCH_TOKEN = r"[WXSFP]\s?\d{2}"
COHORT_TOKEN = r"SR\s?\d{3}"

BATCH_PATTERN = re.compile(rf"\b(?:YC\s*)?({BATCH_TOKEN}|{COHORT_TOKEN})\b", re.IGNORECASE)
YC_BATCH_REFERENCE = re.compile(rf"\bYC\s*{BATCH_TOKEN}\b", re.IGNORECASE)
SPEEDRUN_COHORT_REFERENCE = re.compile(rf"\b{COHORT_TOKEN}\b", re.IGNORECASE)

YC_TERMS = ("y combinator", "joining yc", "accepted into yc", "got into yc", "yc batch")
SPEEDRUN_TERMS = ("a16z speedrun", "speedrun")

DOMAIN_PATTERN = re.compile(r"https?://([^/\s)]+)", re.IGNORECASE)
_EXCLUDED_DOMAINS = (
    "x.com", "twitter.com", "t.co", "linkedin.com", "lnkd.in",
    "ycombinator.com", "a16z.com", "facebook.com", "instagram.com",
)

# --------------------------------------------------------------------------- #
# Identity patterns                                                            #
# --------------------------------------------------------------------------- #

#: Separators that cannot occur inside a company name. Search snippets glue page
#: chrome onto real text ("Jane Doe's Post - LinkedIn EVO HQ (YC F26)"), so the
#: name is recovered by walking back from a program tag to the nearest boundary
#: rather than by one big regex -- a single pattern reliably swallows the chrome.
_SEGMENT_SPLIT = re.compile(r"\s*[|•·—–]\s*|\s+-\s+|[:!?]\s+|\.\s+|,\s+|\n")

#: "(YC F26)", "(a16z Speedrun)", "(SR007)" -- the most reliable identity anchor
#: founders produce, and how YC itself renders company names.
PROGRAM_TAG = re.compile(
    rf"\(\s*(?:YC\s*{BATCH_TOKEN}|a16z\s+Speedrun|Speedrun\s+{COHORT_TOKEN}|{COHORT_TOKEN})\s*\)",
    re.IGNORECASE,
)

#: "<Name> is now backed by Y Combinator", "<Name> has been accepted into YC".
CLAIM_ANCHOR = re.compile(
    r"\s+(?:is|has been|was|are)\s+(?:now\s+|officially\s+)?"
    r"(?:backed by|accepted into|joining|part of)\s+(?:Y Combinator|YC\b|a16z Speedrun)",
    re.IGNORECASE,
)

#: "our company Atlas AI has been accepted ..." -- the name follows the anchor.
POSSESSIVE_ANCHOR = re.compile(r"(?:our company|our startup)[,:]?\s+", re.IGNORECASE)

#: "starting @codos_ai and joining a16z @speedrun SR007", "building @infragrid".
#:
#: On X a founder names their company by its handle far more often than in
#: prose, and the verb in front is what separates the company from every other
#: account in the post -- "@ashwinkodib and I" is a person, "building @infragrid"
#: is the company. The prose extractors were written against LinkedIn, where
#: this shape barely occurs; X was never live long enough to expose the gap, and
#: it is why Speedrun had never produced a single early signal.
PRODUCT_HANDLE = re.compile(
    r"\b(?:building|launching|starting|started|shipping|working on|founded|"
    r"co-?founded|co-?founding)\s+@([A-Za-z0-9_]{2,15})\b",
    re.IGNORECASE,
)

#: Handles belonging to the programs and their investors. A post naming one is
#: talking about the accelerator, never announcing a company called that.
_PROGRAM_HANDLES = frozenset(
    {"ycombinator", "yc", "speedrun", "a16z", "a16zspeedrun", "ycombinator_"}
)

#: "1/ Adalat AI is now backed by Y Combinator" -- the thread counter is not part
#: of the company's name, and it reached the alert as `1/ Adalat AI`.
_THREAD_MARKER = re.compile(r"^\s*\(?\d{1,2}\s*[/)\.]\s*(?:\d{1,2}\s*[)\.]?)?\s*")

#: A run of non-ASCII letters immediately followed by an ASCII one, which is a
#: localised verb the index put in front of the name ("查看Adalat AI"), never the
#: name itself. A company actually written in another script keeps its name,
#: because nothing Latin follows for the lookahead to find.
_LOCALE_VERB_PREFIX = re.compile(r"^[^\W\dA-Za-z_]+(?=[A-Za-z])", re.UNICODE)

#: Longest plausible company name, in tokens.
_MAX_NAME_TOKENS = 5

# --------------------------------------------------------------------------- #
# Identity validation                                                          #
# --------------------------------------------------------------------------- #

#: Name *components*, never a whole company name. Without this, "Lantern AI"
#: loses to the bare "AI" that also appears elsewhere in the same post.
_GENERIC_COMPONENTS = frozenset(
    {"ai", "ml", "labs", "lab", "inc", "co", "hq", "io", "app", "technologies",
     "tech", "systems", "software", "research", "health", "capital", "ventures"}
)

#: Never a company: the programs themselves, and LinkedIn page furniture that
#: search snippets drag in ("Jane Doe's Post - LinkedIn").
_NAME_STOPLIST = frozenset(
    {
        "y combinator", "ycombinator", "yc", "a16z", "a16z speedrun", "speedrun",
        "linkedin", "the company", "our company", "this company", "startup",
        "the startup", "company", "founder", "founders", "batch", "demo day",
    }
)

#: Tokens that only appear when a match has swallowed sentence text rather than a
#: name -- "had the privilege of writing 22", "pped self-service SSO at".
_FRAGMENT_TOKENS = frozenset(
    {
        "the", "a", "an", "and", "or", "but", "we", "our", "us", "i", "my", "me",
        "you", "your", "they", "their", "he", "she", "it", "is", "was", "are",
        "were", "be", "been", "am", "at", "in", "on", "of", "to", "for", "from",
        "with", "that", "this", "these", "those", "just", "now", "today", "most",
        "excited", "happy", "thrilled", "proud", "announce", "announcing",
        "share", "sharing", "welcome", "congrats", "congratulations", "post",
        "posted", "says", "said", "read", "here", "how", "why", "what", "when",
        "who", "getting", "got", "get", "after", "before", "into", "about",
        "privilege", "writing", "meet", "introducing", "news", "some", "very",
        # Program vocabulary: part of the accelerator's name, never the company's.
        "combinator", "yc", "a16z", "speedrun", "accelerator", "program",
        "batch", "cohort", "startup", "startups", "company", "linkedin",
        # Verbs that mean the match ran into the sentence around the name
        # ("we are building Andustry (YC P26)").
        "building", "build", "launching", "launch", "founded", "founding",
        "co-founding", "cofounding", "co-founded", "cofounded", "co-building",
        "backed", "using", "working", "shipped", "shipping", "making", "makes",
        "growing", "grew", "started", "starting", "join", "joining", "joined",
        "accepted", "raising", "raised", "presenting", "participating",
    }
)

_CHROME_MARKERS = (
    "posted this", "'s post", "\u2019s post", "linkedin", "view organization",
    "close menu", "report this post", "comments on",
)


def clean_company_name(candidate: str) -> str:
    # A trailing parenthetical is always a tag, never part of the name:
    # "General Legal (YC26)" -- note YC26 is not a valid program tag, so the
    # tag-stripping regex would not have removed it upstream.
    _EDGES = " -\u2013\u2014,:;.!\"'\u2019\n\t"
    name = re.sub(r"\s*\([^)]*\)\s*$", "", candidate.strip(_EDGES))
    # A thread counter is not part of anybody's name. "1/ Adalat AI is now
    # backed by Y Combinator" reached the alert as the company `1/ Adalat AI`.
    name = _THREAD_MARKER.sub("", name)
    # The provider serves whatever locale it ranked the result in, and the verb
    # it prefixes is not part of the company. A live run returned the LinkedIn
    # title `\u67e5\u770bAdalat AI` -- "view Adalat AI" -- and alerted on it as a company
    # of that name, which is a second identity for a company already found and
    # so a second alert for it. The X title regex already handles the same thing
    # for its own locales; this is the equivalent for a bare title.
    name = _LOCALE_VERB_PREFIX.sub("", name)
    return name.strip(_EDGES)


def valid_company_name(candidate: str | None) -> bool:
    """Reject sentence fragments, page chrome, and the programs themselves.

    A name that fails here is discarded rather than downgraded: alerting a GTM
    user about a company called "had the privilege of writing 22" costs more
    trust than the missed lead is worth.
    """
    if not candidate:
        return False
    name = clean_company_name(candidate)
    if not (2 <= len(name) <= 40):
        return False

    low = name.lower()
    if low in _NAME_STOPLIST:
        return False
    if any(marker in low for marker in _CHROME_MARKERS):
        return False

    tokens = low.replace("/", " ").split()
    if not (1 <= len(tokens) <= _MAX_NAME_TOKENS):
        return False
    if any(token.strip(".,'\u2019") in _FRAGMENT_TOKENS for token in tokens):
        return False
    if len(tokens) == 1 and tokens[0] in _GENERIC_COMPONENTS:
        return False
    if BATCH_PATTERN.fullmatch(name):
        return False
    return any(ch.isalnum() for ch in name)


def _pick(candidates: list[str], text: str) -> str | None:
    """Choose between overlapping candidate names.

    Prefer the phrase that recurs in the post, then the longer one. A real name
    is usually repeated (headline and body), whereas a span that has run into the
    surrounding sentence appears exactly once -- which is how "Prodigy Research"
    wins over "AI Model Prodigy Research".
    """
    if not candidates:
        return None
    low = text.lower()

    def occurrences(name: str) -> int:
        # Word-boundary counting: a substring count would score "AI" three times
        # inside "Lantern AI ... AI interviewer" and beat the real name.
        return len(re.findall(rf"(?<!\w){re.escape(name.lower())}(?!\w)", low))

    return max(candidates, key=lambda name: (occurrences(name), len(name.split())))


def _best_suffix(segment: str, text: str, require_upper: bool) -> str | None:
    """Trailing token run of *segment* that best reads as a company name."""
    tokens = segment.split()
    candidates = []
    for size in range(1, min(_MAX_NAME_TOKENS, len(tokens)) + 1):
        candidate = clean_company_name(" ".join(tokens[-size:]))
        if require_upper and candidate[:1].islower():
            continue
        if valid_company_name(candidate):
            candidates.append(candidate)
    return _pick(candidates, text)


def _best_prefix(segment: str, text: str, require_upper: bool) -> str | None:
    """Leading token run of *segment* that best reads as a company name."""
    tokens = segment.split()
    candidates = []
    for size in range(1, min(_MAX_NAME_TOKENS, len(tokens)) + 1):
        candidate = clean_company_name(" ".join(tokens[:size]))
        if require_upper and candidate[:1].islower():
            continue
        if valid_company_name(candidate):
            candidates.append(candidate)
    return _pick(candidates, text)


def extract_company(text: str) -> tuple[str | None, str | None]:
    """Resolve the company a post is about, and which pattern found it.

    The pattern matters downstream: a parenthesised program tag ("Locke (YC S26)")
    asserts batch membership on its own, while the looser prose patterns need a
    separate acceptance claim before the post counts as an announcement.
    """
    # 1. Parenthesised program tag: walk back to the nearest phrase boundary.
    for match in PROGRAM_TAG.finditer(text):
        segment = _SEGMENT_SPLIT.split(text[: match.start()])[-1]
        name = _best_suffix(segment, text, require_upper=False)
        if name:
            return name, "program_tag"

    # 2. "<Name> is now backed by Y Combinator".
    for match in CLAIM_ANCHOR.finditer(text):
        segment = _SEGMENT_SPLIT.split(text[: match.start()])[-1]
        name = _best_suffix(segment, text, require_upper=True)
        if name:
            return name, "claim_anchor"

    # 3. "our company <Name> ..." -- here the name follows the anchor.
    for match in POSSESSIVE_ANCHOR.finditer(text):
        segment = _SEGMENT_SPLIT.split(text[match.end():])[0]
        name = _best_prefix(segment, text, require_upper=True)
        if name:
            return name, "possessive"

    # 4. "building @infragrid" -- the X shape, where the handle is the identity.
    for match in PRODUCT_HANDLE.finditer(text):
        handle = match.group(1)
        if handle.lower() in _PROGRAM_HANDLES:
            continue
        return f"@{handle}", "product_handle"

    return None, None


# --------------------------------------------------------------------------- #
# Announcement vs. everything else                                             #
# --------------------------------------------------------------------------- #

#: First-person acceptance language. Presence of one of these is what separates a
#: founder announcing their own company from someone talking about the program.
FIRST_PERSON_CLAIMS = (
    "we got into", "i got into", "we got accepted", "i got accepted",
    "we've been accepted", "we have been accepted", "i've been accepted",
    "i have been accepted", "we're joining", "we are joining", "i'm joining",
    "i am joining", "we're in yc", "we are in yc", "we just got into",
    "our company", "our startup", "we founded", "we are building",
    "we're building", "accepted into y combinator", "accepted into yc",
    "joining y combinator", "we will be participating", "we are participating",
    # Speedrun's own vocabulary. Its cohorts announce in the same shapes, and
    # without these the source could find posts but never call one an
    # announcement -- which is why it had produced no early signal at all.
    "joining a16z speedrun", "joining speedrun", "joining the speedrun",
    "accepted into a16z speedrun", "accepted into speedrun",
    "part of the speedrun", "part of a16z speedrun", "in the speedrun batch",
    "part of the upcoming speedrun", "and joining a16z speedrun",
)

#: Acceptance stated with the *company* as the subject rather than the founder:
#: "EVO HQ (YC F26) got into Y Combinator!!". These read as third person but are
#: still the company announcing itself, so they count as a claim.
COMPANY_VOICE_CLAIMS = (
    "is now backed by", "is backed by", "is officially backed by",
    "has been accepted into", "is coming out of stealth",
    "got into y combinator", "got into yc", "joined y combinator",
    "is joining y combinator", "accepted into y combinator's",
    "is joining a16z speedrun", "joined a16z speedrun", "is part of a16z speedrun",
)

#: Context families that mean "this post is not a founder announcing acceptance".
#: Each maps to the reason code recorded in the suppression ledger.
DISQUALIFYING_CONTEXT: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "context_rejection",
        ("rejected from", "got rejected", "we got rejected", "rejection from",
         "didn't get in", "did not get in", "turned down"),
    ),
    (
        "context_application",
        ("application deadline", "deadline to apply", "applications are open",
         "applications are now open", "applying to yc", "apply to yc",
         "yc application", "request for startups", "here's what we look for",
         "improve your", "application tips", "closing applications",
         "about to submit", "final week to"),
    ),
    ("context_demo_day", ("demo day",)),
    ("context_alumni", ("yc alum", "alumni", "alum ")),
    (
        "context_hiring",
        ("we are hiring", "we're hiring", "hiring a", "hiring for",
         "join our team", "founding engineer role", "open roles", "we're looking for"),
    ),
    (
        "context_commentary",
        ("investment thesis", "yc's thesis", "here's what i learned",
         "key takeaways", "my advice", "advice from", "how to get into",
         "worth giving up", "by the numbers", "report on", "compiled statistics"),
    ),
    ("context_startup_school", ("startup school",)),
    (
        "context_congratulating_others",
        ("congrats to", "congratulations to", "congrats on", "congrats again",
         "congrats,", "congratulations,"),
    ),
)


@dataclass
class Extraction:
    """What a single social post yielded, and why it was or was not usable."""

    company_name: str | None = None
    company_domain: str | None = None
    batch: str | None = None
    program: str = "yc"
    author_voice: str = "third_party"
    identity_source: str | None = None
    claim: str | None = None
    claim_kind: str | None = None
    disqualifier: str | None = None
    reason: str | None = None
    evidence: list[str] = field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        """True when this post can become an alertable signal."""
        return self.reason is None and bool(self.company_name or self.company_domain)


def _has_any(low: str, terms) -> str | None:
    for term in terms:
        if term in low:
            return term
    return None


def extract_domain(text: str) -> str | None:
    for match in DOMAIN_PATTERN.finditer(text):
        domain = match.group(1).lower().removeprefix("www.")
        if not any(domain.endswith(item) for item in _EXCLUDED_DOMAINS):
            return domain
    return None


#: On X the programs are written as handles, so the prose the claim lists look
#: for never appears: "joining a16z @speedrun SR007" contains no "a16z speedrun".
#: Normalised only for claim and context matching -- the original text is what
#: identity extraction and the excerpt still see.
_PROGRAM_HANDLE_PROSE = (
    ("@ycombinator", "y combinator"),
    ("@a16zspeedrun", "a16z speedrun"),
    ("a16z @speedrun", "a16z speedrun"),
    ("@speedrun", "speedrun"),
    ("@a16z", "a16z"),
    ("@yc", "yc"),
)


def _claim_text(low: str) -> str:
    for handle, prose in _PROGRAM_HANDLE_PROSE:
        low = low.replace(handle, prose)
    return low


_LINKEDIN_COMPANY_URL = re.compile(
    r"linkedin\.com/company/([A-Za-z0-9\-_%.]+)", re.IGNORECASE
)


def _linkedin_company_slug(url: str | None) -> str | None:
    """The company a LinkedIn company-page URL is the page *for*."""
    match = _LINKEDIN_COMPANY_URL.search(url or "")
    return match.group(1) if match else None


def _squash(value: str) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def _identity_fits_slug(slug: str, name: str) -> bool:
    """Does an extracted name plausibly belong to this company page?

    Containment either way, because a page slug and a company's written name
    disagree in both directions routinely -- `adalat-ai` against "Adalat AI",
    but also `adalat-ai-official` against the same name, and `adalat-ai`
    against "Adalat AI India". What it will not accept is a name with nothing
    in common with the page it was read from.
    """
    a, b = _squash(slug), _squash(clean_company_name(name))
    if len(a) < 3 or len(b) < 3:
        return True          # too short to disagree meaningfully; other gates apply
    return a in b or b in a


def extract(text: str, url: str = "") -> Extraction:
    """Resolve a post into a company identity plus the reason if that failed."""
    result = Extraction()
    text = text or ""
    low = _claim_text(text.lower())

    yc_hit = _has_any(low, YC_TERMS) or (
        "yc batch reference" if YC_BATCH_REFERENCE.search(text) else None
    )
    speedrun_hit = _has_any(low, SPEEDRUN_TERMS) or (
        "speedrun cohort reference" if SPEEDRUN_COHORT_REFERENCE.search(text) else None
    )
    if not yc_hit and not speedrun_hit:
        result.reason = "no_program_evidence"
        return result

    result.program = "speedrun" if speedrun_hit and not yc_hit else "yc"
    result.evidence.append(
        f"Explicit {result.program.upper()} reference: {speedrun_hit or yc_hit!r}"
    )

    batch_match = BATCH_PATTERN.search(text)
    if batch_match:
        result.batch = batch_match.group(1).upper().replace(" ", "")
        result.evidence.append(f"Batch/cohort identified: {result.batch}")

    result.author_voice = (
        "company_account"
        if "linkedin.com/company/" in (url or "").lower()
        else "founder_first_person"
        if _has_any(low, FIRST_PERSON_CLAIMS)
        else "third_party"
    )
    first_person = _has_any(low, FIRST_PERSON_CLAIMS)
    company_voice = _has_any(low, COMPANY_VOICE_CLAIMS)
    result.claim = first_person or company_voice
    result.claim_kind = (
        "first_person" if first_person else "company_voice" if company_voice else None
    )

    for code, phrases in DISQUALIFYING_CONTEXT:
        hit = _has_any(low, phrases)
        if hit:
            result.disqualifier = f"{code}:{hit}"
            break

    result.company_name, result.identity_source = extract_company(text)
    result.company_domain = extract_domain(text)

    if not result.company_name and not result.company_domain:
        result.reason = "no_company_identity"
        return result

    # On a LinkedIn company page the company *is* the page, and a company page
    # is the one shape of candidate that skips the acceptance-claim gate below.
    # So an identity contradicting the page's own slug has to be caught here or
    # not at all: a live run alerted on
    # `cn.linkedin.com/company/mozilla-corporation` as the company Adalat AI,
    # having read a name out of a rail listing other pages. That is neither
    # Mozilla announcing anything nor Adalat AI's page.
    page_slug = _linkedin_company_slug(url)
    if page_slug and result.company_name and not _identity_fits_slug(
        page_slug, result.company_name
    ):
        result.reason = "identity_contradicts_page"
        return result

    if result.company_name:
        result.evidence.append(
            f"Company identity extracted: {result.company_name} "
            f"(via {result.identity_source})"
        )
    if result.company_domain:
        result.evidence.append(f"Company domain resolved: {result.company_domain}")

    # A disqualifying context is overridden only by an explicit acceptance claim
    # from the company itself. "EVO HQ (YC F26) got into Y Combinator!! and I have
    # my last YC interview rejection to thank for it" is a genuine announcement
    # that happens to mention a rejection; a post about someone else's demo day
    # is not, however cleanly a company name can be pulled out of it.
    if result.disqualifier and not (
        result.claim and result.author_voice != "third_party"
    ):
        result.reason = result.disqualifier
        return result

    # A parenthesised program tag ("Locke (YC S26) is changing the way...") is
    # itself an assertion of batch membership, so it does not additionally need
    # acceptance language. The looser prose patterns do -- without a claim they
    # match plenty of third-party commentary.
    if (
        not result.claim
        and result.identity_source != "program_tag"
        and result.author_voice != "company_account"
    ):
        result.reason = "no_acceptance_claim"
        return result

    result.evidence.append(
        f"Acceptance evidence: {result.claim or 'program tag on a company profile'}"
    )
    return result


def enrich_signal(signal):
    """Populate a SocialSignal from its text. Leaves `reason` for the caller."""
    result = extract(signal.text, getattr(signal, "url", "") or "")
    signal.program = result.program
    signal.batch = signal.batch or result.batch
    signal.company_name = signal.company_name or result.company_name
    signal.company_domain = signal.company_domain or result.company_domain
    signal.extraction = result
    return signal
