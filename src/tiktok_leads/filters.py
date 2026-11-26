from __future__ import annotations

from dataclasses import dataclass

from tiktok_leads.email_extractor import extract_emails
from tiktok_leads.models import CandidateProfile, Lead
from tiktok_leads.niches import is_on_niche, rejected_group
from tiktok_leads.numbers import floor_view_count


@dataclass(frozen=True)
class LeadEvaluation:
    lead: Lead | None
    skip_reason: str | None = None
    # Stable, queryable code for the skip (verbose skip_reason is for logs).
    # "no_email" means the creator passed followers+views but had no public
    # email — the highest-yield candidate to re-check later.
    skip_code: str | None = None


def candidate_to_lead(
    candidate: CandidateProfile,
    *,
    min_followers: int,
    min_average_views: int,
    max_followers: int | None = None,
) -> Lead | None:
    return evaluate_candidate(
        candidate,
        min_followers=min_followers,
        min_average_views=min_average_views,
        max_followers=max_followers,
    ).lead


def evaluate_candidate(
    candidate: CandidateProfile,
    *,
    min_followers: int,
    min_average_views: int,
    max_followers: int | None = None,
) -> LeadEvaluation:
    if candidate.followers_count is None or candidate.followers_count < min_followers:
        followers = "unknown" if candidate.followers_count is None else f"{candidate.followers_count:,}"
        return LeadEvaluation(
            lead=None,
            skip_reason=f"followers below threshold ({followers} < {min_followers:,})",
            skip_code="low_followers",
        )

    if max_followers is not None and candidate.followers_count > max_followers:
        return LeadEvaluation(
            lead=None,
            skip_reason=f"followers above threshold ({candidate.followers_count:,} > {max_followers:,})",
            skip_code="high_followers",
        )

    emails = extract_emails(candidate.bio, *candidate.external_links) or list(candidate.extra_emails)
    if not emails:
        return LeadEvaluation(lead=None, skip_reason="no email found", skip_code="no_email")

    # Bio-only and country-email vetoes hold regardless of recent views/captions,
    # so check them before views (prefiltered candidates carry no videos).
    bio_group = rejected_group(candidate.niche, candidate.bio, [], emails[0])
    if bio_group is not None:
        return LeadEvaluation(
            lead=None,
            skip_reason=f"reviewers reject this group ({bio_group})",
            skip_code="off_niche",
        )

    average_views = candidate.average_views
    rounded_average_views = floor_view_count(average_views) if average_views is not None else None
    if rounded_average_views is None or rounded_average_views < min_average_views:
        views = "unknown" if rounded_average_views is None else f"{rounded_average_views:,}"
        return LeadEvaluation(
            lead=None,
            skip_reason=f"average views below threshold ({views} < {min_average_views:,})",
            skip_code="low_views",
        )

    if not is_on_niche(candidate.niche, candidate.bio, candidate.recent_video_captions):
        return LeadEvaluation(
            lead=None,
            skip_reason=f"bio and recent captions are not about {candidate.niche}",
            skip_code="off_niche",
        )

    group = rejected_group(candidate.niche, candidate.bio, candidate.recent_video_captions, emails[0])
    if group is not None:
        return LeadEvaluation(
            lead=None,
            skip_reason=f"reviewers reject this group ({group})",
            skip_code="off_niche",
        )

    return LeadEvaluation(
        lead=Lead(
            handle=candidate.handle,
            profile_url=candidate.profile_url,
            niche=candidate.niche,
            email=emails[0],
            followers_count=candidate.followers_count,
            average_views=rounded_average_views,
            source=candidate.source,
        )
    )
