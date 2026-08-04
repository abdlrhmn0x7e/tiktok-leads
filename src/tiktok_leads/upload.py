"""Upload leads from the local SQLite database to the leads-hub ingest endpoint.

The endpoint dedupes by email, so re-running is always safe: existing leads are
skipped, new ones are created.

Usage (settings come from .env, flags override):

    uv run python -m tiktok_leads.upload
    uv run python -m tiktok_leads.upload --database data/leads.sqlite --dry-run
    uv run python -m tiktok_leads.upload --url https://<deployment>.convex.site --api-key ...
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import requests

from tiktok_leads.settings import Settings

BATCH_SIZE = 100

QUERY = """
SELECT i.handle, i.profile_url, i.niche, i.email, i.followers_count,
       i.average_views, i.source, i.created_at, sp.found_via
FROM influencers i
LEFT JOIN scraped_profiles sp
  ON sp.handle = lower(replace(i.handle, '@', ''))
ORDER BY i.id ASC
"""


def to_iso(timestamp: str | None) -> str | None:
    """SQLite stores CURRENT_TIMESTAMP as 'YYYY-MM-DD HH:MM:SS' (UTC, no zone)
    and the scraper's own inserts as ISO strings. Normalize both."""
    if not timestamp:
        return None
    value = timestamp.strip().replace(" ", "T")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat()


def read_leads(database_path: Path) -> list[dict]:
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(QUERY).fetchall()
    finally:
        connection.close()

    return [
        {
            "handle": row["handle"],
            "profile_url": row["profile_url"],
            "email": row["email"],
            "niche": row["niche"],
            "followers_count": row["followers_count"],
            "average_views": row["average_views"],
            "source": row["source"],
            "found_via": row["found_via"],
            "scraped_at": to_iso(row["created_at"]),
        }
        for row in rows
    ]


def upload(leads: list[dict], *, ingest_url: str, api_key: str) -> tuple[int, int, int]:
    created = skipped = invalid = 0
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {api_key}"})
    endpoint = f"{ingest_url.rstrip('/')}/ingest/leads"

    for offset in range(0, len(leads), BATCH_SIZE):
        batch = leads[offset : offset + BATCH_SIZE]
        response = session.post(endpoint, json=batch, timeout=60)
        if not response.ok:
            raise RuntimeError(
                f"Batch at offset {offset} failed: {response.status_code} {response.text}"
            )
        result = response.json()
        created += result.get("created", 0)
        skipped += result.get("skipped", 0)
        invalid += len(result.get("invalid", []))
        done = min(offset + BATCH_SIZE, len(leads))
        print(
            f"  {done}/{len(leads)} uploaded: {created} created, "
            f"{skipped} skipped, {invalid} invalid"
        )

    return created, skipped, invalid


def main() -> int:
    settings = Settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--database",
        type=Path,
        default=settings.database_path,
        help=f"Path to the SQLite database (default: {settings.database_path})",
    )
    parser.add_argument(
        "--url",
        default=settings.convex_site_url,
        help="Convex site URL, e.g. https://<deployment>.convex.site (default: CONVEX_SITE_URL)",
    )
    parser.add_argument(
        "--api-key",
        default=settings.convex_ingest_api_key,
        help="Ingest API key (default: CONVEX_INGEST_API_KEY)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read and validate the database without uploading anything",
    )
    args = parser.parse_args()

    if not args.database.exists():
        print(f"Database not found: {args.database}", file=sys.stderr)
        return 1

    leads = read_leads(args.database)
    print(f"Read {len(leads)} leads from {args.database}")
    if not leads:
        return 0

    if args.dry_run:
        print("Dry run, nothing uploaded. First lead:")
        print(f"  {leads[0]}")
        return 0

    if not args.url or not args.api_key:
        print(
            "Missing endpoint config: set CONVEX_SITE_URL and CONVEX_INGEST_API_KEY "
            "in .env or pass --url and --api-key",
            file=sys.stderr,
        )
        return 1

    created, skipped, invalid = upload(leads, ingest_url=args.url, api_key=args.api_key)
    print(f"Done. Created {created}, skipped {skipped} (already present), invalid {invalid}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
