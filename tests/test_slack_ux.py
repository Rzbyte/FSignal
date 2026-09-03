"""Slack is the product interface, so its output is tested like a product.

These assert what a Senior GTM professional actually sees: a real founder name
rather than an instruction, the state without having to infer it, the independent
verification with its provenance, and links that say why to click them. They also
assert what must *never* appear -- matcher vocabulary, weak evidence crowding out
strong, or a fabricated YC profile URL for a company that has no profile yet.
"""

import asyncio
import json
from dataclasses import replace

import pytest

import app.slack as slack_module
from app.config import settings as base_settings
from app.slack import official_listing_moment, post_age_note
from app.presenter import (
    company_display,
    company_site_url,
    founder_profile_url,
    outreach_links,
    display_evidence,
    excerpt,
    founder_display,
    official_batch_label,
    verify_url,
)

# The real signal FSignal delivered on 2026-09-03, as persisted.
LARK = {
    "id": 10,
    "source": "linkedin",
    "url": "https://www.linkedin.com/posts/michael-wang-40061923b_im-dropping-out-of-nyu-activity-7500964549022523392-lukT",
    "text": (
        "Michael Wang's Post - LinkedIn Now I'm leaving NYU to work on Lark (YC F26) "
        "full time with my brother, Jason Wang. Lark is part of Y Combinator F26. "
        "We're building AI for wholesale ..."
    ),
    "author_name": None,
    "author_handle": None,
    "company_name": "Lark",
    "company_domain": None,
    "batch": "F26",
    "program": "yc",
    "confidence": 75,
    "confidence_label": "likely",
    "gtm_score": 65,
    "gtm_priority": "medium",
    "detected_at": "2026-09-03T17:14:17.525624+00:00",
    "raw_json": json.dumps({"title": "Michael Wang's Post - LinkedIn"}),
    "evidence_json": json.dumps(
        [
            "Explicit YC reference: 'y combinator'",
            "Batch/cohort identified: F26",
            "Company identity extracted: Lark (via program_tag)",
            "Acceptance evidence: we're building",
            "Founder is speaking in the first person about their own company",
            "No matching official-directory entry at detection time",
        ]
    ),
    "gtm_reasons_json": json.dumps(
        [
            "Pre-directory timing advantage",
            "Founder is directly reachable from the post",
            "Current accelerator cohort identified",
        ]
    ),
    "official_check_json": json.dumps(
        {
            "matched": False,
            "methods_tried": ["exact_name", "batch_prefix"],
            "batch_scope": "F26",
            "records_checked": 6460,
            "snapshot_source": "yc_directory",
            "snapshot_size": 6199,
            "snapshot_taken_at": "2026-09-03T17:14:11.648424+00:00",
        }
    ),
}

CONFIRMED_SIGNAL = dict(
    LARK,
    confirmed_at="2026-09-05T16:32:00+00:00",
    official_check_json=json.dumps({"matched": True, "official_name": "Lark"}),
)
CONFIRMED_COMPANY = {
    "name": "Lark",
    "batch": "Fall 2026",
    "source": "yc_directory",
    "url": "https://www.ycombinator.com/companies/lark",
}

# The real directory addition FSignal alerted on during a live scan, as
# persisted -- it entered the Fall 2026 batch while this code was being written.
ONEPATCH = {
    "id": 6461,
    "name": "OnePatch",
    "source": "yc_directory",
    "external_id": "onepatch",
    "batch": "Fall 2026",
    "url": "https://www.ycombinator.com/companies/onepatch",
    "description": "Automate on-call at agent-scale.",
    "first_seen_at": "2026-09-03T21:18:44.401303+00:00",
}

# A second post about a company already alerted on, which replies in-thread
# rather than raising a new alert.
CORROBORATING_SIGNAL = dict(
    LARK,
    id=11,
    source="x",
    url="https://x.com/janef/status/2090071721856712924",
    author_name="Jane Founder",
    author_handle="janef",
    collection_mode="indexed_fallback",
)

#: Vocabulary that belongs to the implementation, never to the user.
INTERNAL_TERMS = (
    "program_tag", "claim_anchor", "possessive", "deterministic", "regex",
    "matcher", "prefix algorithm", "scorer", "normalized", "extraction",
    "batch_prefix", "exact_name", "company_key", "ghost",
)


def render(coro_factory):
    """Build a payload without touching Slack."""
    slack_module.settings = replace(base_settings, demo_mode=True)
    notifier = slack_module.SlackNotifier()
    captured = {}

    async def capture(title, blocks, thread_ts=None):
        captured.update(text=title, blocks=blocks)
        return {"ok": True, "ts": "1.1"}

    notifier._send = capture
    asyncio.run(coro_factory(notifier))
    return captured


EARLY = render(lambda n: n.send_ghost(LARK))
CONFIRMED = render(lambda n: n.send_confirmed(CONFIRMED_SIGNAL, CONFIRMED_COMPANY))

# The other two variants a reader can actually receive. They used to ship
# unvalidated: the Block Kit and symbol-hygiene checks below covered only the
# two above, so a malformed NEW OFFICIAL payload would have reached Slack.
NEW_OFFICIAL = render(lambda n: n.send_official(ONEPATCH))
CORROBORATION = render(lambda n: n.send_corroboration(CORROBORATING_SIGNAL, "1.1"))

ALL_VARIANTS = [EARLY, CONFIRMED, NEW_OFFICIAL, CORROBORATION]


def all_text(payload) -> str:
    parts = [payload["text"]]
    for block in payload["blocks"]:
        text = block.get("text")
        if isinstance(text, dict):
            parts.append(text["text"])
        for field in block.get("fields", []):
            parts.append(field["text"])
        for element in block.get("elements", []):
            parts.append(element.get("text") if isinstance(element.get("text"), str) else "")
            if isinstance(element.get("text"), dict):
                parts.append(element["text"]["text"])
    return "\n".join(p for p in parts if p)


def buttons(payload):
    return [
        (element["text"]["text"], element["url"])
        for block in payload["blocks"]
        if block["type"] == "actions"
        for element in block["elements"]
    ]


# --------------------------------------------------------------------------- #
# Identity                                                                     #
# --------------------------------------------------------------------------- #


def test_founder_name_is_recovered_from_a_linkedin_post_title():
    assert founder_display(LARK) == "Michael Wang"


def test_founder_name_falls_back_to_the_profile_slug():
    signal = dict(LARK, raw_json=json.dumps({"title": "Some headline with no author"}))
    assert founder_display(signal) == "Michael Wang"


def test_founder_name_uses_the_x_author_when_present():
    signal = dict(LARK, source="x", author_name="Jane Founder", author_handle="janef")
    assert founder_display(signal) == "Jane Founder (@janef)"


def test_founder_falls_back_honestly_rather_than_instructing_the_reader():
    signal = dict(LARK, raw_json=json.dumps({"title": "no author here"}), url="https://x.com/i/status/1")
    assert founder_display(signal) == "Unknown founder"
    assert "See post" not in all_text(EARLY)


def test_a_company_page_is_not_presented_as_a_person():
    signal = dict(LARK, url="https://www.linkedin.com/company/shepherdai")
    assert founder_display(signal) == "Company page"


def _field_labels(payload) -> list[str]:
    fields = next(b for b in payload["blocks"] if b.get("fields"))["fields"]
    return [f["text"].split("\n")[0].strip("*") for f in fields]


def test_identity_fields_are_present_and_two_column():
    labels = _field_labels(EARLY)
    assert labels == ["Company", "Founder", "Program", "Batch", "Source", "Detected"]
    body = all_text(EARLY)
    assert "Lark" in body and "Michael Wang" in body
    assert "F26" in body
    assert "LinkedIn" in body and "Linkedin" not in body   # platform's own casing
    assert "Sep 3, 2026" in body


# --------------------------------------------------------------------------- #
# State, source badge, excerpt                                                 #
# --------------------------------------------------------------------------- #


def test_header_states_the_lifecycle_without_decoration():
    assert EARLY["text"] == "🔥 EARLY YC SIGNAL · Lark"
    assert CONFIRMED["text"] == "✅ YC CONFIRMED · Lark"
    assert "HIGH PRIORITY" not in EARLY["text"]
    assert EARLY["text"].count("🔥") == 1


def test_state_is_stated_not_inferred():
    assert "Founder announced · not yet listed in YC F26" in all_text(EARLY)
    assert "Officially listed in YC Fall 2026" in all_text(CONFIRMED)


def test_source_badge_is_fsignal_native_text():
    assert "`SOURCE · LINKEDIN`" in all_text(EARLY)
    x_payload = render(lambda n: n.send_ghost(dict(LARK, source="x")))
    assert "`SOURCE · X`" in all_text(x_payload)


def test_excerpt_drops_search_result_boilerplate():
    quote = excerpt(LARK)
    assert quote.startswith("Now I'm leaving NYU")
    assert "Post - LinkedIn" not in quote
    assert "We're building AI for wholesale" not in quote   # filler, not the claim
    assert "Lark is part of Y Combinator F26." in quote


# --------------------------------------------------------------------------- #
# Official check                                                               #
# --------------------------------------------------------------------------- #


def test_official_check_leads_with_the_conclusion_not_the_method():
    body = all_text(EARLY)
    assert "OFFICIAL CHECK" in body
    assert "Not found in YC Fall 2026" in body
    assert "6,199 YC records checked" in body
    assert "snapshot 17:14 UTC" in body


def test_official_check_counts_the_right_directory():
    """6,199 is the YC snapshot; 6,460 also counts Speedrun rows."""
    assert "6,199" in all_text(EARLY)
    assert "6,460" not in all_text(EARLY)


def test_no_internal_vocabulary_reaches_the_user():
    body = all_text(EARLY).lower() + all_text(CONFIRMED).lower()
    for term in INTERNAL_TERMS:
        assert term not in body, term


# --------------------------------------------------------------------------- #
# Evidence                                                                     #
# --------------------------------------------------------------------------- #


def test_strongest_evidence_is_selected_and_rephrased():
    assert display_evidence(LARK) == [
        'Explicit "Y Combinator F26" statement',
        "Company identity: Lark",
        "First-person founder announcement",
    ]


def test_weak_evidence_is_suppressed():
    evidence = display_evidence(LARK)
    assert not any("we're building" in item.lower() for item in evidence)
    # The official check block already says this, far more clearly.
    assert not any("no matching official-directory" in item.lower() for item in evidence)


def test_evidence_is_capped():
    assert len(display_evidence(LARK)) <= 3


# --------------------------------------------------------------------------- #
# Calls to action                                                              #
# --------------------------------------------------------------------------- #


def test_early_ctas_say_why_to_click_and_where_they_go():
    labels_urls = buttons(EARLY)
    assert len(labels_urls) == 2
    (primary_label, primary_url), (secondary_label, secondary_url) = labels_urls

    assert primary_label.startswith("View founder post")
    assert primary_url == LARK["url"]          # the exact post, not a profile

    assert secondary_label.startswith("Verify YC status")
    assert secondary_url.startswith("https://www.ycombinator.com/companies?")
    assert "query=Lark" in secondary_url and "batch=Fall+2026" in secondary_url


def test_no_fabricated_profile_url_for_an_unlisted_company():
    """Lark has no YC profile page yet; inventing one would be a lie."""
    for _, url in buttons(EARLY):
        assert not url.startswith("https://www.ycombinator.com/companies/")
    assert verify_url(LARK) != "https://www.ycombinator.com/companies/lark"


def test_speedrun_verification_points_at_the_speedrun_directory():
    signal = dict(LARK, program="speedrun", batch="SR007")
    assert verify_url(signal) == "https://speedrun.a16z.com/companies"


def test_confirmed_ctas_lead_with_the_real_profile():
    labels_urls = buttons(CONFIRMED)
    assert len(labels_urls) == 2
    (primary_label, primary_url), (secondary_label, secondary_url) = labels_urls
    assert primary_label.startswith("View YC profile")
    assert primary_url == CONFIRMED_COMPANY["url"]
    assert secondary_label.startswith("View original announcement")
    assert secondary_url == LARK["url"]


def test_generic_cta_labels_are_gone():
    for payload in (EARLY, CONFIRMED):
        for label, _ in buttons(payload):
            assert label.strip() not in {"Open link", "View source", "Open directory"}


def test_at_most_two_navigation_actions():
    for payload in (EARLY, CONFIRMED):
        for block in payload["blocks"]:
            if block["type"] == "actions":
                assert len(block["elements"]) <= 2


# --------------------------------------------------------------------------- #
# Company website                                                              #
# --------------------------------------------------------------------------- #


def test_a_verified_domain_sits_beside_the_company_and_adds_no_button():
    payload = render(lambda n: n.send_ghost(dict(LARK, company_domain="lark.ai")))
    assert "Lark · lark.ai" in all_text(payload)
    assert len(buttons(payload)) == 2


def test_no_domain_means_no_domain_shown():
    """An unverified or absent domain is simply not displayed."""
    from app.presenter import company_display

    assert company_display(LARK) == "Lark"
    assert company_display(dict(LARK, company_domain="lark.ai")) == "Lark · lark.ai"


# --------------------------------------------------------------------------- #
# Scores                                                                       #
# --------------------------------------------------------------------------- #


def test_confidence_and_gtm_priority_stay_distinct_and_uninflated():
    body = all_text(EARLY)
    assert "Confidence: *75%* · Likely" in body
    assert "GTM priority: *65/100* · Medium" in body


@pytest.mark.parametrize("payload", [EARLY, CONFIRMED])
def test_lead_time_is_never_invented(payload):
    if payload is EARLY:
        assert "lead" not in all_text(payload).lower()


def test_confirmed_shows_the_measured_transition():
    body = all_text(CONFIRMED)
    assert "EARLY DETECTED  →  OFFICIALLY CONFIRMED" in body
    assert "Early detection lead:" in body
    assert "47h 17m" in body or "47h 18m" in body


# --------------------------------------------------------------------------- #
# Block Kit compliance                                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("payload", ALL_VARIANTS)
def test_block_kit_limits_are_respected(payload):
    assert len(payload["blocks"]) <= 50
    assert len(payload["text"]) <= 3000
    for block in payload["blocks"]:
        if block["type"] == "header":
            assert len(block["text"]["text"]) <= 150
        if isinstance(block.get("text"), dict):
            assert len(block["text"]["text"]) <= 3000
        assert len(block.get("fields", [])) <= 10
        for field in block.get("fields", []):
            assert len(field["text"]) <= 2000
        for element in block.get("elements", []):
            if element.get("type") == "button":
                assert len(element["text"]["text"]) <= 75


def test_early_block_order_reads_top_to_bottom():
    order = [b["type"] for b in EARLY["blocks"]]
    assert order[0] == "header"
    assert order[1] == "section"          # identity fields
    assert "divider" in order
    assert order.index("divider") < order.index("actions")
    assert order[-1] == "context"         # scores footer
    assert order.count("actions") == 1


def test_no_single_giant_markdown_dump():
    sections = [
        b for b in EARLY["blocks"]
        if b["type"] == "section" and isinstance(b.get("text"), dict)
    ]
    assert all(len(b["text"]["text"]) < 1200 for b in sections)
    assert len(sections) >= 4


def test_only_functional_symbols_are_used():
    body = "".join(all_text(payload) for payload in ALL_VARIANTS)
    # Each of these does a job: severity, verdict, receipt, state, navigation,
    # separation, enumeration, and "one more of the same" on a threaded reply.
    allowed = {"🔥", "✅", "🔎", "⚡", "➕", "→", "·", "•"}
    exotic = {
        ch for ch in body
        if ord(ch) > 0x2100 and ch not in allowed
    }
    assert not exotic, exotic


def test_batch_label_maps_to_what_the_directory_displays():
    assert official_batch_label("F26") == "Fall 2026"
    assert official_batch_label("X26") == "Spring 2026"
    assert official_batch_label("P26") == "Spring 2026"
    assert official_batch_label("SR007") is None


# --------------------------------------------------------------------------- #
# Whose clock the lead time is measured on                                     #
# --------------------------------------------------------------------------- #


CONFIRMED_SIGNAL_TIMED = dict(
    LARK,
    detected_at="2026-08-19T09:14:00+00:00",
    # What our own polling saw. On a four-hour cadence this is up to four hours
    # later than the moment the directory actually published.
    confirmed_at="2026-09-08T14:02:00+00:00",
)
LISTED_COMPANY = dict(
    CONFIRMED_COMPANY, listed_at="2026-09-08T11:33:22+00:00"
)


def test_the_lead_is_measured_against_the_directorys_own_clock():
    """Otherwise the headline number depends on our polling interval.

    YC publishes `launched_at` per company. Measuring against our own
    observation instead would silently add up to one polling interval to every
    lead time we report -- inflating the one figure the whole product is judged
    on, in our own favour.
    """
    moment, note = official_listing_moment(CONFIRMED_SIGNAL_TIMED, LISTED_COMPANY)
    assert moment == "2026-09-08T11:33:22+00:00"
    assert "directory's own" in note


def test_without_a_published_timestamp_we_say_which_clock_we_used():
    """The fallback understates the lead, so it is safe -- but not silent."""
    moment, note = official_listing_moment(
        CONFIRMED_SIGNAL_TIMED, dict(CONFIRMED_COMPANY, listed_at=None)
    )
    assert moment == CONFIRMED_SIGNAL_TIMED["confirmed_at"]
    assert "at least this" in note


def test_the_confirmed_alert_shows_the_directorys_moment_and_says_so():
    payload = render(
        lambda n: n.send_confirmed(CONFIRMED_SIGNAL_TIMED, LISTED_COMPANY)
    )
    body = all_text(payload)
    assert "20d 2h" in body            # YC's clock
    assert "20d 4h" not in body        # not ours, which is 2h28m later
    assert "directory's own published listing time" in body


# --------------------------------------------------------------------------- #
# Outreach                                                                     #
# --------------------------------------------------------------------------- #


def test_an_x_signal_offers_the_founders_profile():
    """The brief is written by someone who wants to contact these founders.

    Telling them who announced and when, and then stopping, is one step short of
    the job.
    """
    signal = dict(LARK, source="x", author_handle="Adalat_AI",
                  url="https://x.com/Adalat_AI/status/2090071662784086176")
    assert founder_profile_url(signal) == "https://x.com/Adalat_AI"
    assert "Reach out" in all_text(render(lambda n: n.send_ghost(signal)))


def test_a_linkedin_post_yields_the_authors_real_profile():
    """LinkedIn post URLs embed the author's own slug, so this is not a guess."""
    assert founder_profile_url(LARK) == (
        "https://www.linkedin.com/in/michael-wang-40061923b"
    )


def test_a_company_page_has_no_person_to_open():
    signal = dict(LARK, url="https://www.linkedin.com/company/shepherdai")
    assert founder_profile_url(signal) is None


def test_a_resolved_domain_becomes_somewhere_to_click():
    signal = dict(LARK, company_domain="adalat.ai")
    assert company_site_url(signal) == "https://adalat.ai"
    assert "<https://adalat.ai|adalat.ai>" in outreach_links(signal)


def test_no_outreach_line_rather_than_a_dead_one():
    """A label with nothing behind it is worse than no label."""
    signal = dict(LARK, author_handle=None, company_domain=None,
                  url="https://www.linkedin.com/feed/")
    assert outreach_links(signal) is None
    assert "Reach out" not in all_text(render(lambda n: n.send_ghost(signal)))


def test_outreach_adds_no_buttons():
    """Two buttons is a call to action. Five is none."""
    signal = dict(LARK, source="x", author_handle="janef",
                  company_domain="lark.ai",
                  url="https://x.com/janef/status/1")
    payload = render(lambda n: n.send_ghost(signal))
    assert len(buttons(payload)) == 2
    assert "Reach out" in all_text(payload)


# --------------------------------------------------------------------------- #
# When the founder posted, versus when we caught it                            #
# --------------------------------------------------------------------------- #


STALE = dict(
    LARK,
    source="x",
    author_handle="Adalat_AI",
    company_name="Adalat AI",
    # Day precision: this came from a search index, which knows the day and not
    # the hour.
    posted_at="2026-08-19",
    detected_at="2026-09-03T21:18:41+00:00",
)


def test_a_day_precision_date_never_grows_a_clock_time():
    """The index knows the day. Rendering midnight would invent the rest.

    A reader acts on what the alert says, so "Aug 19, 2026, 12:00 AM PT" is a
    number nobody measured presented as one somebody did.
    """
    body = all_text(render(lambda n: n.send_ghost(STALE)))
    assert "Aug 19, 2026" in body
    assert "Aug 19, 2026, 12:00 AM" not in body


def test_an_exact_timestamp_keeps_its_clock():
    """The platform API knows the moment, so the alert may show it."""
    signal = dict(STALE, posted_at="2026-08-19T09:14:00+00:00")
    assert "Aug 19, 2026, 2:14 AM PT" in all_text(render(lambda n: n.send_ghost(signal)))


def test_an_old_post_says_so_rather_than_reading_as_fresh():
    """A monitor that has just started finds posts that were already public.

    The alert used to say only "Detected: today". A reader who clicked through
    to a three-week-old post would rightly wonder what else it was not saying.
    """
    note = post_age_note(STALE)
    assert note and "16 days old" in note
    assert "early against the directory, not against the post" in note
    assert note in all_text(render(lambda n: n.send_ghost(STALE)))


def test_a_fresh_post_gets_no_note():
    """Saying "posted 2 hours ago, detected 2 hours ago" twice is noise."""
    fresh = dict(STALE, posted_at="2026-09-03T19:00:00+00:00")
    assert post_age_note(fresh) is None
    assert "already" not in all_text(render(lambda n: n.send_ghost(fresh)))


def test_an_unknown_post_date_shows_no_field_and_no_note():
    """A field reading "unknown" is a dead label; the reader still has Detected."""
    unknown = dict(STALE, posted_at=None)
    assert post_age_note(unknown) is None
    assert "Posted" not in _field_labels(render(lambda n: n.send_ghost(unknown)))
