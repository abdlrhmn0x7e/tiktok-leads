from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from tiktok_leads.models import Lead
from tiktok_leads.niches import is_harvestable_hashtag, trending_found_via


SCHEMA = """
CREATE TABLE IF NOT EXISTS influencers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    handle TEXT NOT NULL,
    platform TEXT NOT NULL DEFAULT 'tiktok',
    profile_url TEXT NOT NULL,
    niche TEXT NOT NULL,
    email TEXT NOT NULL,
    followers_count INTEGER NOT NULL,
    average_views INTEGER NOT NULL,
    source TEXT NOT NULL,
    notified_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- exists_by_email keeps one lead per email. Per platform here only because a
    -- few emails got a TikTok and an Instagram lead while that was allowed.
    UNIQUE (email, platform)
);

CREATE INDEX IF NOT EXISTS idx_influencers_niche ON influencers(niche);
CREATE INDEX IF NOT EXISTS idx_influencers_handle ON influencers(handle);

CREATE TABLE IF NOT EXISTS scraped_profiles (
    handle TEXT PRIMARY KEY,
    niche TEXT NOT NULL,
    source TEXT NOT NULL,
    skip_reason TEXT,
    found_via TEXT,
    bio_link_enriched_at TEXT,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_scraped_profiles_niche ON scraped_profiles(niche);
CREATE INDEX IF NOT EXISTS idx_scraped_profiles_last_seen ON scraped_profiles(last_seen_at);

-- Instagram creators already evaluated, kept apart from scraped_profiles so
-- the same handle on both platforms is checked once per platform.
CREATE TABLE IF NOT EXISTS instagram_profiles (
    handle TEXT PRIMARY KEY,
    niche TEXT NOT NULL,
    skip_reason TEXT,
    found_via TEXT,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS feed_cursors (
    feed TEXT PRIMARY KEY,
    cursor INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS discovered_hashtags (
    hashtag TEXT NOT NULL,
    niche TEXT NOT NULL,
    times_seen INTEGER NOT NULL DEFAULT 1,
    last_scraped_at TEXT,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (hashtag, niche)
);

CREATE INDEX IF NOT EXISTS idx_discovered_hashtags_niche ON discovered_hashtags(niche);

-- Every hashtag/search a lead was found through, so a creator surfacing
-- again via a new one gets it appended to their found_via in Convex.
CREATE TABLE IF NOT EXISTS lead_sources (
    email TEXT NOT NULL,
    found_via TEXT NOT NULL,
    notified_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (email, found_via)
);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Creators from leads-hub whose following list gets crawled. Only 'due'
-- seeds are crawled; cursor is the list's minCursor to resume from.
CREATE TABLE IF NOT EXISTS following_seeds (
    handle TEXT PRIMARY KEY,
    niche TEXT NOT NULL,
    sec_uid TEXT,
    state TEXT NOT NULL DEFAULT 'due',
    cursor INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- 1: a lead from leads-hub. 2: an on-niche creator found in a seed's list.
    level INTEGER NOT NULL DEFAULT 1,
    related_crawled_at TEXT
);
"""

# done: list exhausted. hidden: the creator hides it. failed: no secUid, or the list answered an error.
FollowingState = Literal["due", "done", "hidden", "failed"]


@dataclass(frozen=True)
class FollowingSeed:
    handle: str
    niche: str
    sec_uid: str | None
    state: FollowingState
    cursor: int
    level: int = 1


class LeadRepository:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path, timeout=30.0)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute("PRAGMA busy_timeout=30000")

    def commit(self) -> None:
        self.connection.commit()

    def initialize(self) -> None:
        self.connection.executescript(SCHEMA)
        self._migrate_add_influencers_platform()
        self._migrate_influencers_email_unique_per_platform()
        self._migrate_scraped_profiles_handle_unique()
        self._migrate_add_scraped_profiles_skip_reason()
        self._migrate_add_scraped_profiles_found_via()
        self._migrate_add_scraped_profiles_bio_link_enriched_at()
        self._migrate_add_following_seeds_level()
        self._migrate_add_following_seeds_related_crawled_at()
        self._backfill_scraped_profiles_from_influencers()
        self._backfill_lead_sources()
        self.connection.commit()

    def _migrate_add_following_seeds_level(self) -> None:
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(following_seeds)")}
        if "level" not in columns:
            self.connection.execute("ALTER TABLE following_seeds ADD COLUMN level INTEGER NOT NULL DEFAULT 1")

    def _migrate_add_following_seeds_related_crawled_at(self) -> None:
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(following_seeds)")}
        if "related_crawled_at" not in columns:
            self.connection.execute("ALTER TABLE following_seeds ADD COLUMN related_crawled_at TEXT")

    def _migrate_add_influencers_platform(self) -> None:
        columns = {row["name"] for row in self.connection.execute("PRAGMA table_info(influencers)")}
        if "platform" not in columns:
            self.connection.execute(
                "ALTER TABLE influencers ADD COLUMN platform TEXT NOT NULL DEFAULT 'tiktok'"
            )

    def _migrate_influencers_email_unique_per_platform(self) -> None:
        table = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'influencers'"
        ).fetchone()
        if "email TEXT NOT NULL UNIQUE" not in str(table["sql"]):
            return
        columns = (
            "id, handle, platform, profile_url, niche, email, followers_count, "
            "average_views, source, notified_at, created_at, updated_at"
        )
        # The renamed table keeps the index names, so SCHEMA runs again after the drop to rebuild them.
        self.connection.executescript(
            f"""
            BEGIN;
            ALTER TABLE influencers RENAME TO influencers_old;
            {SCHEMA}
            INSERT INTO influencers ({columns}) SELECT {columns} FROM influencers_old;
            DROP TABLE influencers_old;
            {SCHEMA}
            COMMIT;
            """
        )

    def _backfill_lead_sources(self) -> None:
        # Each lead's original source already reached Convex with the lead.
        self.connection.execute(
            """
            INSERT OR IGNORE INTO lead_sources (email, found_via, notified_at)
            SELECT i.email, sp.found_via, CURRENT_TIMESTAMP
            FROM influencers i
            JOIN scraped_profiles sp ON sp.handle = lower(replace(i.handle, '@', ''))
            WHERE sp.found_via IS NOT NULL AND i.platform = 'tiktok'
            """
        )

    def _migrate_add_scraped_profiles_found_via(self) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(scraped_profiles)").fetchall()
        }
        if "found_via" not in columns:
            self.connection.execute("ALTER TABLE scraped_profiles ADD COLUMN found_via TEXT")

    def _migrate_add_scraped_profiles_bio_link_enriched_at(self) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(scraped_profiles)").fetchall()
        }
        if "bio_link_enriched_at" not in columns:
            self.connection.execute("ALTER TABLE scraped_profiles ADD COLUMN bio_link_enriched_at TEXT")
            self.connection.execute(
                """
                UPDATE scraped_profiles
                SET bio_link_enriched_at = last_seen_at
                WHERE found_via = 'enrich:bio_link' AND bio_link_enriched_at IS NULL
                """
            )

    def _migrate_add_scraped_profiles_skip_reason(self) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(scraped_profiles)").fetchall()
        }
        if "skip_reason" not in columns:
            self.connection.execute("ALTER TABLE scraped_profiles ADD COLUMN skip_reason TEXT")
        # Created here (not in SCHEMA) so it runs only after the column exists.
        self.connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_scraped_profiles_recheck "
            "ON scraped_profiles(skip_reason, last_seen_at)"
        )

    def exists_by_email(self, email: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM influencers WHERE email = ? LIMIT 1",
            (email,),
        ).fetchone()
        return row is not None

    def seen_handles(self) -> set[str]:
        rows = self.connection.execute(
            "SELECT handle FROM scraped_profiles "
            "UNION SELECT handle FROM influencers WHERE platform = 'tiktok'",
        ).fetchall()
        return {str(row["handle"]).removeprefix("@").lower() for row in rows}

    def record_scraped_profile(
        self,
        *,
        handle: str,
        niche: str,
        source: str,
        skip_reason: str | None = None,
        found_via: str | None = None,
        bio_link_enriched: bool = False,
        commit: bool = True,
    ) -> None:
        normalized_handle = handle.removeprefix("@").lower()
        self.connection.execute(
            """
            INSERT INTO scraped_profiles (
                handle, niche, source, skip_reason, found_via, bio_link_enriched_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE NULL END, CURRENT_TIMESTAMP)
            ON CONFLICT(handle) DO UPDATE SET
                source = excluded.source,
                niche = excluded.niche,
                skip_reason = excluded.skip_reason,
                found_via = coalesce(excluded.found_via, scraped_profiles.found_via),
                bio_link_enriched_at = CASE
                    WHEN excluded.bio_link_enriched_at IS NOT NULL THEN excluded.bio_link_enriched_at
                    ELSE scraped_profiles.bio_link_enriched_at
                END,
                last_seen_at = CURRENT_TIMESTAMP
            """,
            (normalized_handle, niche, source, skip_reason, found_via, 1 if bio_link_enriched else 0),
        )
        if commit:
            self.connection.commit()

    def seen_instagram_handles(self) -> set[str]:
        rows = self.connection.execute(
            "SELECT handle FROM instagram_profiles "
            "UNION SELECT lower(replace(handle, '@', '')) FROM influencers WHERE platform = 'instagram'"
        ).fetchall()
        return {str(row[0]) for row in rows}

    def record_instagram_profile(
        self, *, handle: str, niche: str, skip_reason: str | None, found_via: str | None
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO instagram_profiles (handle, niche, skip_reason, found_via, last_seen_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(handle) DO UPDATE SET
                niche = excluded.niche,
                skip_reason = excluded.skip_reason,
                found_via = excluded.found_via,
                last_seen_at = CURRENT_TIMESTAMP
            """,
            (handle.removeprefix("@").lower(), niche, skip_reason, found_via),
        )
        self.connection.commit()

    def excluded_handles(self, recheck_after_days: int) -> set[str]:
        """Handles to skip on crawls: confirmed leads, plus anything evaluated
        within the recheck window. Stale non-leads fall out so they can be
        re-evaluated when a feed surfaces them again."""
        rows = self.connection.execute(
            """
            SELECT handle FROM influencers WHERE platform = 'tiktok'
            UNION
            SELECT handle FROM scraped_profiles
            WHERE last_seen_at >= datetime('now', ?)
            """,
            (f"-{recheck_after_days} days",),
        ).fetchall()
        return {str(row["handle"]).removeprefix("@").lower() for row in rows}

    def handles_due_for_recheck(
        self,
        *,
        niches: list[str],
        after_days: int,
        limit: int,
        reasons: tuple[str, ...] = ("no_email",),
    ) -> list[tuple[str, str]]:
        """Stale, non-lead profiles in `niches` worth re-checking for a
        newly-added email. Returns (handle, niche) ordered oldest-first."""
        placeholders = ",".join("?" for _ in reasons)
        niche_placeholders = ",".join("?" for _ in niches)
        rows = self.connection.execute(
            f"""
            SELECT sp.handle AS handle, sp.niche AS niche
            FROM scraped_profiles sp
            LEFT JOIN influencers i
              ON lower(replace(i.handle, '@', '')) = sp.handle AND i.platform = 'tiktok'
            WHERE i.handle IS NULL
              AND sp.skip_reason IN ({placeholders})
              AND sp.niche IN ({niche_placeholders})
              AND sp.last_seen_at < datetime('now', ?)
            ORDER BY sp.last_seen_at ASC
            LIMIT ?
            """,
            (*reasons, *niches, f"-{after_days} days", limit),
        ).fetchall()
        return [(str(row["handle"]), str(row["niche"])) for row in rows]

    def no_email_seed_handles(self, niche: str, *, limit: int = 5000) -> list[tuple[str, str | None]]:
        """Un-enriched no_email creators in `niche` worth checking for clickable
        TikTok bioLinks / handle probes, ordered highest-signal first:
          1. Following seeds (pre-vetted gym bios)
          2. Accounts followed directly by a level-1 leads-hub seed
          3. Explicit search-term discoveries (`search:...`)
          4. Clean harvestable niche hashtags (`#tag` passing is_harvestable_hashtag)
        Returns (handle, original_found_via) preserving each creator's real source."""
        inf_handles = {
            str(r["handle"]).removeprefix("@").lower()
            for r in self.connection.execute(
                "SELECT handle FROM influencers WHERE platform = 'tiktok'"
            ).fetchall()
        }
        fs_level = {
            str(r["handle"]).lower(): int(r["level"])
            for r in self.connection.execute(
                "SELECT handle, level FROM following_seeds WHERE niche = ?",
                (niche,),
            ).fetchall()
        }
        l1_seeds = {h for h, lvl in fs_level.items() if lvl == 1}

        tiers: dict[int, list[tuple[str, str | None]]] = {1: [], 2: [], 3: [], 4: []}
        rows = self.connection.execute(
            """
            SELECT handle, found_via
            FROM scraped_profiles
            WHERE niche = ?
              AND skip_reason = 'no_email'
              AND bio_link_enriched_at IS NULL
              AND coalesce(found_via, '') != 'enrich:bio_link'
            ORDER BY last_seen_at DESC, rowid ASC
            """,
            (niche,),
        ).fetchall()
        for row in rows:
            h = str(row["handle"]).removeprefix("@").lower()
            if not h or h in inf_handles:
                continue
            fv_raw = str(row["found_via"]) if row["found_via"] else ""
            fv: str | None = None if fv_raw in ("", "recheck", "enrich:bio_link") else fv_raw
            if h in fs_level:
                tiers[1].append((h, fv))
            elif fv_raw.startswith("following:@") and fv_raw[len("following:@"):].lower() in l1_seeds:
                tiers[2].append((h, fv))
            elif fv_raw.startswith("search:"):
                tiers[3].append((h, fv))
            elif fv_raw.startswith("#") and is_harvestable_hashtag(fv_raw[1:], niche):
                tiers[4].append((h, fv))

        results: list[tuple[str, str | None]] = []
        for tier in (1, 2, 3, 4):
            if len(results) >= limit:
                break
            need = limit - len(results)
            results.extend(tiers[tier][:need])
        return results

    def mark_rechecked(self, handle: str) -> None:
        """A renamed creator is recorded under the new handle, so the old one
        would stay due for a recheck forever."""
        self.connection.execute(
            "UPDATE scraped_profiles SET last_seen_at = CURRENT_TIMESTAMP WHERE handle = ?",
            (handle.removeprefix("@").lower(),),
        )
        self.connection.commit()

    def get_feed_cursor(self, feed: str, *, max_age_days: int) -> int:
        """Where to resume a feed crawl. Stale cursors fall back to 0 so we
        periodically re-read the head of the feed, where new videos land."""
        row = self.connection.execute(
            "SELECT cursor FROM feed_cursors WHERE feed = ? AND updated_at >= datetime('now', ?)",
            (feed, f"-{max_age_days} days"),
        ).fetchone()
        return int(row["cursor"]) if row else 0

    def set_feed_cursor(self, feed: str, cursor: int) -> None:
        self.connection.execute(
            """
            INSERT INTO feed_cursors (feed, cursor, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(feed) DO UPDATE SET
                cursor = excluded.cursor,
                updated_at = CURRENT_TIMESTAMP
            """,
            (feed, max(0, cursor)),
        )
        self.connection.commit()

    def add_following_seeds(self, seeds: Iterable[tuple[str, str]]) -> None:
        """(handle, niche) pairs. A seed already stored keeps its state, so a re-sync never re-crawls it."""
        self.connection.executemany(
            "INSERT INTO following_seeds (handle, niche) VALUES (?, ?) "
            "ON CONFLICT(handle) DO UPDATE SET level = 1",
            seeds,
        )
        self.connection.commit()

    def add_found_seed(
        self, handle: str, niche: str, sec_uid: str | None, *, level: int = 2, commit: bool = True
    ) -> None:
        """An on-niche creator found in a seed's list. Crawled after lower-level seeds."""
        self.connection.execute(
            "INSERT OR IGNORE INTO following_seeds (handle, niche, sec_uid, level) VALUES (?, ?, ?, ?)",
            (handle.removeprefix("@").lower(), niche, sec_uid, level),
        )
        if commit:
            self.connection.commit()

    def due_following_seeds(self, niche: str, *, limit: int) -> list[str]:
        rows = self.connection.execute(
            "SELECT handle FROM following_seeds WHERE niche = ? AND state = 'due' ORDER BY level, added_at LIMIT ?",
            (niche, limit),
        ).fetchall()
        return [str(row["handle"]) for row in rows]

    def due_related_seeds(self, niche: str, *, limit: int) -> list[str]:
        """Level-1 leads whose following list is hidden or failed and haven't
        been expanded through /api/related/item_list/ yet (newest first)."""
        rows = self.connection.execute(
            """
            SELECT handle FROM following_seeds
            WHERE niche = ?
              AND level = 1
              AND state IN ('hidden', 'failed')
              AND related_crawled_at IS NULL
            ORDER BY added_at DESC
            LIMIT ?
            """,
            (niche, limit),
        ).fetchall()
        return [str(row["handle"]) for row in rows]

    def mark_related_crawled(self, handle: str) -> None:
        self.connection.execute(
            "UPDATE following_seeds SET related_crawled_at = CURRENT_TIMESTAMP WHERE handle = ?",
            (handle.removeprefix("@").lower(),),
        )
        self.connection.commit()

    def following_seed(self, handle: str) -> FollowingSeed | None:
        row = self.connection.execute(
            "SELECT handle, niche, sec_uid, state, cursor, level FROM following_seeds WHERE handle = ?",
            (handle,),
        ).fetchone()
        return FollowingSeed(**row) if row else None

    def save_following_progress(
        self, handle: str, *, sec_uid: str | None, cursor: int, state: FollowingState
    ) -> None:
        self.connection.execute(
            """
            UPDATE following_seeds
            SET sec_uid = coalesce(?, sec_uid), cursor = ?, state = ?, updated_at = CURRENT_TIMESTAMP
            WHERE handle = ?
            """,
            (sec_uid, cursor, state, handle),
        )
        self.connection.commit()

    def record_discovered_hashtags(self, hashtags: list[str], *, niche: str, commit: bool = True) -> None:
        for raw in hashtags:
            tag = raw.removeprefix("#").strip().lower()
            if not tag or not is_harvestable_hashtag(tag, niche):
                continue
            self.connection.execute(
                """
                INSERT INTO discovered_hashtags (hashtag, niche, times_seen, last_seen_at)
                VALUES (?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(hashtag, niche) DO UPDATE SET
                    times_seen = times_seen + 1,
                    last_seen_at = CURRENT_TIMESTAMP
                """,
                (tag, niche),
            )
        if commit:
            self.connection.commit()

    def due_hashtags(
        self,
        *,
        niche: str,
        min_times_seen: int = 2,
        not_scraped_within_days: int = 7,
    ) -> list[str]:
        """Harvested hashtags seen enough times and not scraped recently,
        strongest signal first. The caller applies is_harvestable_hashtag and
        its cap, so rows harvested before a rule existed never get crawled."""
        rows = self.connection.execute(
            """
            SELECT hashtag FROM discovered_hashtags
            WHERE niche = ?
              AND times_seen >= ?
              AND (last_scraped_at IS NULL OR last_scraped_at < datetime('now', ?))
            ORDER BY times_seen DESC, last_seen_at DESC
            """,
            (niche, min_times_seen, f"-{not_scraped_within_days} days"),
        ).fetchall()
        return [str(row["hashtag"]) for row in rows]

    def mark_hashtag_scraped(self, hashtag: str, *, niche: str) -> None:
        tag = hashtag.removeprefix("#").strip().lower()
        self.connection.execute(
            "UPDATE discovered_hashtags SET last_scraped_at = CURRENT_TIMESTAMP WHERE hashtag = ? AND niche = ?",
            (tag, niche),
        )
        self.connection.commit()

    def _migrate_scraped_profiles_handle_unique(self) -> None:
        table = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'scraped_profiles'"
        ).fetchone()
        if table is None or "PRIMARY KEY (handle, niche)" not in str(table["sql"]):
            return

        self.connection.executescript(
            """
            ALTER TABLE scraped_profiles RENAME TO scraped_profiles_old;

            CREATE TABLE scraped_profiles (
                handle TEXT PRIMARY KEY,
                niche TEXT NOT NULL,
                source TEXT NOT NULL,
                last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            INSERT INTO scraped_profiles (handle, niche, source, last_seen_at, first_seen_at)
            SELECT
                lower(replace(handle, '@', '')) AS handle,
                niche,
                source,
                max(last_seen_at) AS last_seen_at,
                min(last_seen_at) AS first_seen_at
            FROM scraped_profiles_old
            GROUP BY lower(replace(handle, '@', ''));

            DROP TABLE scraped_profiles_old;
            CREATE INDEX IF NOT EXISTS idx_scraped_profiles_niche ON scraped_profiles(niche);
CREATE INDEX IF NOT EXISTS idx_scraped_profiles_last_seen ON scraped_profiles(last_seen_at);
            """
        )

    def _backfill_scraped_profiles_from_influencers(self) -> None:
        self.connection.execute(
            """
            INSERT INTO scraped_profiles (handle, niche, source, last_seen_at, first_seen_at)
            SELECT
                lower(replace(handle, '@', '')) AS handle,
                niche,
                source,
                COALESCE(updated_at, created_at, CURRENT_TIMESTAMP) AS last_seen_at,
                COALESCE(created_at, CURRENT_TIMESTAMP) AS first_seen_at
            FROM influencers
            WHERE handle IS NOT NULL AND handle != ''
            ON CONFLICT(handle) DO UPDATE SET
                last_seen_at = max(scraped_profiles.last_seen_at, excluded.last_seen_at)
            """
        )

    def insert_lead(self, lead: Lead) -> bool:
        try:
            self.connection.execute(
                """
                INSERT INTO influencers (
                    handle, platform, profile_url, niche, email, followers_count,
                    average_views, source, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lead.handle,
                    lead.platform,
                    lead.profile_url,
                    lead.niche,
                    lead.email,
                    lead.followers_count,
                    lead.average_views,
                    lead.source,
                    lead.created_at.isoformat(),
                    lead.created_at.isoformat(),
                ),
            )
            self.connection.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def unnotified_leads(self, limit: int = 20) -> list[Lead]:
        """Leads whose notification failed at insert time (oldest first)."""
        rows = self.connection.execute(
            f"""
            SELECT {_LEAD_COLUMNS}, coalesce(sp.found_via, ip.found_via) AS found_via
            FROM influencers i
            {_FOUND_VIA_JOINS}
            WHERE i.notified_at IS NULL
            ORDER BY i.created_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_lead_from_row(row, row["found_via"]) for row in rows]

    def add_lead_source(self, handle: str, found_via: str, *, niche: str | None = None) -> Lead | None:
        """Record that a creator turned up via found_via, moving them to `niche`
        when given. Returns their lead, carrying that found_via, when they are a
        lead and the source or niche is new, so the change still needs pushing."""
        row = self.connection.execute(
            f"SELECT {_LEAD_COLUMNS} FROM influencers i "
            "WHERE lower(replace(i.handle, '@', '')) = ? AND i.platform = 'tiktok' LIMIT 1",
            (handle.removeprefix("@").lower(),),
        ).fetchone()
        if row is None:
            return None
        niche_changed = niche is not None and niche != row["niche"]
        if niche_changed:
            self.connection.execute(
                "UPDATE influencers SET niche = ?, updated_at = CURRENT_TIMESTAMP WHERE email = ? AND platform = 'tiktok'",
                (niche, row["email"]),
            )
        cursor = self.connection.execute(
            "INSERT OR IGNORE INTO lead_sources (email, found_via) VALUES (?, ?)",
            (row["email"], found_via),
        )
        if niche_changed and not cursor.rowcount:
            # Known source, new niche: re-queue it so the daemon's retry pushes the niche too.
            self.connection.execute(
                "UPDATE lead_sources SET notified_at = NULL WHERE email = ? AND found_via = ?",
                (row["email"], found_via),
            )
        self.connection.commit()
        if not (niche_changed or cursor.rowcount):
            return None
        lead = _lead_from_row(row, found_via)
        return replace(lead, niche=niche) if niche_changed else lead

    def unnotified_lead_sources(self, limit: int = 20) -> list[Lead]:
        """Added sources whose push to Convex failed (oldest first)."""
        rows = self.connection.execute(
            f"""
            SELECT {_LEAD_COLUMNS}, ls.found_via
            FROM lead_sources ls
            JOIN influencers i ON i.email = ls.email AND i.platform = 'tiktok'
            WHERE ls.notified_at IS NULL
            ORDER BY ls.created_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_lead_from_row(row, str(row["found_via"])) for row in rows]

    def mark_lead_source_notified(self, email: str, found_via: str) -> None:
        self.connection.execute(
            "UPDATE lead_sources SET notified_at = CURRENT_TIMESTAMP WHERE email = ? AND found_via = ?",
            (email, found_via),
        )
        self.connection.commit()

    def mark_notified(self, email: str, platform: str) -> None:
        self.connection.execute(
            "UPDATE influencers SET notified_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
            "WHERE email = ? AND platform = ?",
            (email, platform),
        )
        self.connection.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.connection.execute(
            """
            INSERT INTO meta (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
            """,
            (key, value),
        )
        self.connection.commit()

    def stats(self) -> dict:
        """Aggregate pipeline health numbers for --stats and the daily digest."""
        trending = sorted(trending_found_via())

        def rows(query: str, *params: object) -> list[sqlite3.Row]:
            return self.connection.execute(query, params).fetchall()

        def scalar(query: str, *params: object) -> int:
            row = self.connection.execute(query, params).fetchone()
            return int(row[0]) if row and row[0] is not None else 0

        return {
            "total_leads": scalar("SELECT COUNT(*) FROM influencers"),
            "leads_by_niche": [
                (str(r["niche"]), int(r["n"]))
                for r in rows(
                    "SELECT niche, COUNT(*) n FROM influencers GROUP BY niche ORDER BY n DESC"
                )
            ],
            "leads_last_7_days": [
                (str(r["day"]), int(r["n"]))
                for r in rows(
                    """
                    SELECT date(created_at) day, COUNT(*) n FROM influencers
                    WHERE created_at >= datetime('now', '-7 days')
                    GROUP BY day ORDER BY day DESC
                    """
                )
            ],
            "evaluated_total": scalar("SELECT COUNT(*) FROM scraped_profiles"),
            "evaluated_last_24h": scalar(
                "SELECT COUNT(*) FROM scraped_profiles WHERE last_seen_at >= datetime('now', '-1 day')"
            ),
            "leads_last_24h": scalar(
                "SELECT COUNT(*) FROM influencers WHERE created_at >= datetime('now', '-1 day')"
            ),
            "skip_reasons": [
                (str(r["skip_reason"] or "lead"), int(r["n"]))
                for r in rows(
                    "SELECT skip_reason, COUNT(*) n FROM scraped_profiles GROUP BY skip_reason ORDER BY n DESC"
                )
            ],
            # Which hashtags/searches produced actual leads (skip_reason NULL = lead).
            "lead_sources": [
                (str(r["found_via"]), int(r["n"]))
                for r in rows(
                    """
                    SELECT found_via, COUNT(*) n FROM scraped_profiles
                    WHERE skip_reason IS NULL AND found_via IS NOT NULL
                    GROUP BY found_via ORDER BY n DESC LIMIT 15
                    """
                )
            ],
            "trending_leads_last_7_days": scalar(
                f"""
                SELECT COUNT(*) FROM influencers i
                JOIN scraped_profiles sp ON sp.handle = lower(ltrim(i.handle, '@'))
                WHERE i.created_at >= datetime('now', '-7 days')
                  AND sp.found_via IN ({",".join("?" * len(trending))})
                """,
                *trending,
            ),
            "harvested_hashtags": scalar("SELECT COUNT(*) FROM discovered_hashtags"),
            "pending_recheck": scalar(
                "SELECT COUNT(*) FROM scraped_profiles WHERE skip_reason = 'no_email'"
            ),
        }

    def close(self) -> None:
        self.connection.close()


_LEAD_COLUMNS = (
    "i.handle, i.platform, i.profile_url, i.niche, i.email, i.followers_count, i.average_views, i.source"
)

# found_via lives in each platform's evaluated-profiles table.
_FOUND_VIA_JOINS = """
    LEFT JOIN scraped_profiles sp
      ON i.platform = 'tiktok' AND sp.handle = lower(replace(i.handle, '@', ''))
    LEFT JOIN instagram_profiles ip
      ON i.platform = 'instagram' AND ip.handle = lower(replace(i.handle, '@', ''))
"""


def _lead_from_row(row: sqlite3.Row, found_via: str | None) -> Lead:
    return Lead(
        handle=str(row["handle"]),
        platform=row["platform"],
        profile_url=str(row["profile_url"]),
        niche=str(row["niche"]),
        email=str(row["email"]),
        followers_count=int(row["followers_count"]),
        average_views=int(row["average_views"]),
        source=str(row["source"]),
        found_via=found_via,
    )
