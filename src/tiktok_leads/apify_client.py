"""Minimal Apify API client: start an actor run, wait for it, read its dataset."""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

APIFY_API = "https://api.apify.com/v2"


def run_actor(
    token: str, payload: dict, *, actor: str, max_items: int, max_charge_usd: float
) -> tuple[list[dict], float]:
    """Run an actor to completion and return its dataset items and what the run cost."""
    headers = {"Authorization": f"Bearer {token}"}
    response = requests.post(
        f"{APIFY_API}/acts/{actor}/runs",
        params={"maxItems": max_items, "maxTotalChargeUsd": max_charge_usd, "waitForFinish": 60},
        headers=headers,
        json=payload,
        timeout=90,
    )
    response.raise_for_status()
    run = response.json()["data"]
    logger.info("apify run %s started (dataset %s)", run["id"], run["defaultDatasetId"])
    while run["status"] in {"READY", "RUNNING", "TIMING-OUT", "ABORTING"}:
        response = requests.get(
            f"{APIFY_API}/actor-runs/{run['id']}", params={"waitForFinish": 60}, headers=headers, timeout=90
        )
        response.raise_for_status()
        run = response.json()["data"]
        logger.info("apify run %s: %s", run["id"], run["status"])
    # A run stopped by the charge cap still has a usable partial dataset.
    if run["status"] not in {"SUCCEEDED", "ABORTED", "TIMED-OUT"}:
        raise RuntimeError(f"Apify run {run['id']} ended with status {run['status']}")
    return dataset_items(token, run["defaultDatasetId"]), float(run.get("usageTotalUsd") or 0.0)


def dataset_items(token: str, dataset_id: str, *, page_size: int = 100, attempts: int = 3) -> list[dict]:
    """Read a dataset in pages. Instagram items run ~15 KB each, and one request
    for a few hundred of them can stall past any sane timeout."""
    items: list[dict] = []
    while True:
        for attempt in range(1, attempts + 1):
            try:
                response = requests.get(
                    f"{APIFY_API}/datasets/{dataset_id}/items",
                    params={"clean": "true", "format": "json", "offset": len(items), "limit": page_size},
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=120,
                )
                response.raise_for_status()
                break
            except requests.RequestException:
                if attempt == attempts:
                    raise
                logger.warning("dataset %s page at %s failed (attempt %s), retrying", dataset_id, len(items), attempt)
        page = response.json()
        items += page
        if len(page) < page_size:
            return items
