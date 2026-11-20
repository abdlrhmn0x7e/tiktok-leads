"""Merge handle lists into one clean CSV for the Instagram script.

Reads three layouts:
- outreach log (tab-separated, first cell an email): creator email, sender, TikTok handle, Instagram handle
- handle sheet (tab-separated): Instagram handle, TikTok handle (an "Instagram Handle" header is skipped)
- email list: one "Instagram: @handle: email" per line

Writes instagram,tiktok,email,source with one row per Instagram handle. Junk around
handles and +N suffixes are stripped. An email is blanked when it's invalid, when an
earlier row already has it (one lead per email), or when it's just the handle at
gmail.com in an outreach log, where it's a guess rather than a known address; blanked
rows still go through the script's bio-email check. Rows whose email is already a lead,
rows from --skip-sender, and rows without an Instagram handle are left out.

With --tiktok-out, rows that have a TikTok handle and no email go to that list instead,
since the TikTok scraper checks them for free:

    uv run tiktok-leads-handles data/handles/sheet-7.tsv data/handles/ig-emails-1.txt \\
        --known-emails data/convex-emails.txt -o data/handles/instagram-2.csv --tiktok-out data/handles/tiktok-1.txt
    uv run tiktok-leads --niche lifestyle --handles-file data/handles/tiktok-1.txt

    uv run tiktok-leads-handles data/handles/calai-outreach.tsv data/handles/sheet-*.tsv \\
        --known-emails data/convex-emails.txt --skip-sender reukia@calai.app -o data/handles/instagram.csv
    uv run tiktok-leads-apify --platform instagram --max-charge-usd 0.5 --handles-file data/handles/instagram.csv
"""

from __future__ import annotations

import argparse
import csv
import re
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

from tiktok_leads.apify_instagram import clean_handle
from tiktok_leads.db import LeadRepository
from tiktok_leads.email_extractor import EMAIL_RE, normalize_email
from tiktok_leads.settings import Settings

COLUMNS = ["instagram", "tiktok", "email", "source"]
LABELED = re.compile(r"instagram:\s*(\S+?):\s*(\S+@\S+)", re.IGNORECASE)


def read_rows(path: Path) -> Iterator[tuple[str, str, str, str]]:
    """(instagram, tiktok, email, sender) per row, raw."""
    with path.open(newline="") as file:
        lines = [
            [*cells, "", "", "", ""] for cells in csv.reader(file, delimiter="\t") if any(c.strip() for c in cells)
        ]
    if lines and lines[0][0].strip().lower().startswith("instagram") and not LABELED.fullmatch(lines[0][0].strip()):
        lines = lines[1:]
    is_log = bool(lines) and EMAIL_RE.fullmatch(normalize_email(lines[0][0])) is not None
    for cells in lines:
        if labeled := LABELED.fullmatch(cells[0].strip()):
            yield labeled[1], "", labeled[2], ""
        elif is_log:
            yield cells[3], cells[2], cells[0], cells[1].strip().lower()
        else:
            yield cells[0], cells[1], "", ""


def guessed(email: str, handle: str) -> bool:
    local, _, domain = email.partition("@")
    letters = lambda text: re.sub(r"[^a-z]", "", text)  # noqa: E731
    return domain == "gmail.com" and letters(local) == letters(handle)


def build(
    paths: list[Path],
    known_emails: set[str],
    skip_senders: set[str] = frozenset(),
    split_tiktok: bool = False,
) -> tuple[list[dict[str, str]], list[str], Counter[str]]:
    rows: dict[str, dict[str, str]] = {}
    tiktok_handles: dict[str, None] = {}
    used: set[str] = set()
    stats: Counter[str] = Counter()
    for path in paths:
        for raw_instagram, raw_tiktok, raw_email, sender in read_rows(path):
            if sender in skip_senders:
                stats[f"sender {sender} skipped"] += 1
                continue
            instagram = clean_handle(raw_instagram)
            if not instagram:
                stats["no usable Instagram handle"] += 1
                continue
            email = normalize_email(raw_email)
            if email and not EMAIL_RE.fullmatch(email):
                stats["invalid email blanked"] += 1
                email = ""
            if sender and email and guessed(email, instagram):
                stats["handle@gmail.com guess blanked"] += 1
                email = ""
            if email in known_emails:
                stats["email already a lead"] += 1
                continue
            if email in used:
                stats["repeat email blanked"] += 1
                email = ""
            tiktok = clean_handle(raw_tiktok) or ""
            if split_tiktok and tiktok and not email:
                tiktok_handles[tiktok] = None
                continue
            row = rows.get(instagram)
            if row is not None:
                stats["duplicate handle merged"] += 1
                if email and not row["email"]:
                    row["email"] = email
                    used.add(email)
                continue
            rows[instagram] = {
                "instagram": instagram,
                "tiktok": tiktok,
                "email": email,
                "source": path.stem,
            }
            if email:
                used.add(email)
    return list(rows.values()), list(tiktok_handles), stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "files",
        nargs="+",
        type=Path,
        help="Outreach logs and handle sheets (tab-separated)",
    )
    parser.add_argument("-o", "--output", type=Path, required=True, help="CSV to write")
    parser.add_argument("--known-emails", type=Path, help="Emails already in leads-hub, one per line")
    parser.add_argument(
        "--skip-sender",
        action="append",
        default=[],
        help="Drop outreach-log rows from this sender",
    )
    parser.add_argument(
        "--tiktok-out",
        type=Path,
        help="Send rows with a TikTok handle and no email to this list",
    )
    args = parser.parse_args()

    settings = Settings()
    repository = LeadRepository(settings.database_path)
    repository.initialize()
    known = {str(row[0]).lower() for row in repository.connection.execute("SELECT email FROM influencers")}
    repository.close()
    if args.known_emails:
        known |= {normalize_email(line) for line in args.known_emails.read_text().split()}

    rows, tiktok_handles, stats = build(
        args.files,
        known,
        {sender.lower() for sender in args.skip_sender},
        split_tiktok=bool(args.tiktok_out),
    )
    if args.tiktok_out:
        args.tiktok_out.write_text("".join(f"{handle}\n" for handle in tiktok_handles))
        print(f"Wrote {len(tiktok_handles)} TikTok handles to {args.tiktok_out}")
    with args.output.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    with_email = sum(1 for row in rows if row["email"])
    print(f"Wrote {len(rows)} Instagram handles to {args.output} ({with_email} with an email)")
    for reason, count in stats.most_common():
        print(f"  {reason}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
