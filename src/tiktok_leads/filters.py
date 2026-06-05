from __future__ import annotations

from dataclasses import dataclass

from tiktok_leads.email_extractor import extract_emails
from tiktok_leads.models import CandidateProfile, Lead
from tiktok_leads.numbers import floor_view_count


@dataclass(frozen=True)
class LeadEvaluation:
    lead: Lead | None
    skip_reason: str | None = None


def candidate_to_lead(
    candidate: CandidateProfile,
    *,
    min_followers: int,
    min_average_views: int,
) -> Lead | None:
    return evaluate_candidate(
        candidate,
        min_followers=min_followers,
        min_average_views=min_average_views,
    ).lead


def evaluate_candidate(
    candidate: CandidateProfile,
    *,
    min_followers: int,
    min_average_views: int,
) -> LeadEvaluation:
    if candidate.followers_count is None or candidate.followers_count < min_followers:
        followers = "unknown" if candidate.followers_count is None else f"{candidate.followers_count:,}"
        return LeadEvaluation(
            lead=None,
            skip_reason=f"followers below threshold ({followers} < {min_followers:,})",
        )

    average_views = candidate.average_views
    if average_views is None or average_views < min_average_views:
        views = "unknown" if average_views is None else f"{average_views:,}"
        return LeadEvaluation(
            lead=None,
            skip_reason=f"average views below threshold ({views} < {min_average_views:,})",
        )

    emails = extract_emails(candidate.bio, *candidate.external_links)
    if not emails:
        return LeadEvaluation(lead=None, skip_reason="no email found")

    rounded_average_views = floor_view_count(average_views)

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
