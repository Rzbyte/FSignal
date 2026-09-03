"""Deciding whether a socially-announced company is already officially listed.

This is the adjudicator behind every EARLY claim, so it has to be wrong in the
safe direction in both possible ways:

* Match too loosely and an unconfirmed founder claim gets silently "confirmed"
  against the wrong company.
* Match too strictly and a company that *is* already listed gets announced as an
  early discovery -- which is the failure that makes a GTM user stop trusting the
  feed, because they can check it in ten seconds.

Both were live-observed on real data before this module was rewritten: `Nodus`
failed to match the official `Nodus Compute`, and `Shepherd (YC S26)` matched a
different, older `Shepherd` from Winter 2021.

The resolution order is therefore domain, then exact normalized name, then a
batch-scoped strict token-prefix -- and never fuzzy similarity.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .db import normalize_name
from .targeting import batch_code, batch_codes, cohort_code

#: A one-token prefix has to be this long before it can stand in for a company.
#: "Nodus" -> "Nodus Compute" is fine; "AI" -> "AI Labs" is not.
MIN_PREFIX_CHARS = 5


def _normalize_domain(value: str | None) -> str:
    value = (value or "").lower().strip()
    value = value.removeprefix("https://").removeprefix("http://")
    value = value.removeprefix("www.")
    return value.split("/", 1)[0]


def batch_identity(value: str | None) -> frozenset[str]:
    """Every token that can name this batch, for comparison across both sides.

    Official rows carry "Fall 2026"; founder posts carry "F26". Spring carries two
    live codes. Speedrun uses "SR007" on both sides.
    """
    if not value:
        return frozenset()
    text = str(value).strip()
    codes = batch_codes(text)
    if codes:
        return frozenset(codes)
    cohort = cohort_code(text)
    if cohort:
        return frozenset({cohort})
    collapsed = text.upper().replace(" ", "")
    return frozenset({collapsed}) if collapsed else frozenset()


def normalize_batch(value: str | None) -> str | None:
    """The single display token for a batch, or None."""
    if not value:
        return None
    text = str(value).strip()
    return batch_code(text) or cohort_code(text) or text.upper().replace(" ", "") or None


def batches_match(left: str | None, right) -> bool:
    """True when two batch labels can name the same cohort."""
    right = right if isinstance(right, frozenset) else batch_identity(right)
    return bool(batch_identity(left) & right)


def _tokens(name: str | None) -> list[str]:
    return [token for token in (name or "").lower().replace("-", " ").split() if token]


def _is_token_prefix(candidate: str, official: str) -> bool:
    """True when *candidate* is the leading whole-token run of *official*.

    Token-based rather than substring-based so "Dream" matches "Dream Labs" but
    not "Dreamworks".
    """
    candidate_tokens, official_tokens = _tokens(candidate), _tokens(official)
    if not candidate_tokens or len(candidate_tokens) >= len(official_tokens):
        return False
    if official_tokens[: len(candidate_tokens)] != candidate_tokens:
        return False
    if len(candidate_tokens) == 1 and len(candidate_tokens[0]) < MIN_PREFIX_CHARS:
        return False
    return True


@dataclass
class OfficialCheck:
    """The receipt behind an EARLY claim: what was compared, and how."""

    match: dict | None = None
    method: str | None = None
    methods_tried: list[str] = field(default_factory=list)
    batch_scope: str | None = None
    records_checked: int = 0

    @property
    def is_early(self) -> bool:
        return self.match is None

    def as_dict(self) -> dict:
        return {
            "matched": self.match is not None,
            "method": self.method,
            "methods_tried": self.methods_tried,
            "batch_scope": self.batch_scope,
            "records_checked": self.records_checked,
            "official_name": (self.match or {}).get("name"),
            "official_batch": (self.match or {}).get("batch"),
            "official_url": (self.match or {}).get("url"),
        }


def resolve_official(
    company_name: str | None,
    company_domain: str | None,
    official_rows,
    batch: str | None = None,
) -> OfficialCheck:
    """Resolve a social claim against the official snapshot, recording the work."""
    rows = list(official_rows)
    check = OfficialCheck(records_checked=len(rows), batch_scope=normalize_batch(batch))
    scope = batch_identity(batch)

    domain = _normalize_domain(company_domain)
    normalized = normalize_name(company_name)

    if domain:
        check.methods_tried.append("domain")
        for row in rows:
            if _normalize_domain(row.get("domain")) == domain:
                check.match, check.method = row, "domain"
                return check

    if normalized:
        check.methods_tried.append("exact_name")
        named = [row for row in rows if row.get("normalized_name") == normalized]
        if named and scope:
            # A name can repeat across batches ("Shepherd" exists in both Winter
            # 2021 and Winter 2024). When the post names a batch, prefer that
            # batch. A row with no batch recorded cannot conflict, so it still
            # counts; a row whose batch genuinely differs is a different company.
            same_batch = [row for row in named if batches_match(row.get("batch"), scope)]
            unknown_batch = [row for row in named if not batch_identity(row.get("batch"))]
            winner = (same_batch or unknown_batch or [None])[0]
            if winner is not None:
                check.match, check.method = winner, "exact_name"
                return check
        elif named:
            check.match, check.method = named[0], "exact_name"
            return check

    # Founders shorten their own names ("Nodus" for "Nodus Compute"). Allowed only
    # inside the batch the post itself names, so it cannot reach across cohorts.
    if company_name and scope:
        check.methods_tried.append("batch_prefix")
        for row in rows:
            if not batches_match(row.get("batch"), scope):
                continue
            if _is_token_prefix(company_name, row.get("name") or ""):
                check.match, check.method = row, "batch_prefix"
                return check

    return check


def match_official(company_name, company_domain, official_rows, batch=None):
    """Backwards-compatible wrapper returning just the matched row."""
    return resolve_official(company_name, company_domain, official_rows, batch).match


def company_key(
    company_name: str | None,
    company_domain: str | None,
    program: str = "yc",
    batch: str | None = None,
) -> str:
    """Stable identity for alert-level deduplication.

    Post-level dedup is not enough: one founder posting on both X and LinkedIn,
    or three people congratulating the same company, produced three separate
    "EARLY" alerts. A domain is the strongest key; otherwise the name is scoped
    by program and batch so two unrelated companies sharing a name stay distinct.
    """
    domain = _normalize_domain(company_domain)
    if domain:
        return f"domain:{domain}"
    return f"{program}:{normalize_batch(batch) or 'nobatch'}:{normalize_name(company_name)}"
