"""Atlas access. Collections follow contract v1.2 (+ design.rtl text, v1.3)."""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache

from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument

from . import config


def now() -> datetime:
    """BSON Date (contract conventions)."""
    return datetime.now(timezone.utc)


@lru_cache(maxsize=1)
def client() -> MongoClient:
    if not config.MONGODB_URI:
        raise RuntimeError("MONGODB_URI not set (.env in repo root)")
    return MongoClient(config.MONGODB_URI, appname="chipharness", serverSelectionTimeoutMS=10000)


def db():
    return client()[config.MONGODB_DB]


def trials():
    return db().trials


def versions():
    return db().harness_versions


def lessons():
    return db().lessons


def settings():
    return db().harness_settings


def ensure_indexes() -> None:
    trials().create_index([("harness_version", ASCENDING), ("created_at", ASCENDING)])
    trials().create_index([("status", ASCENDING)])
    trials().create_index([("score.fmax_mhz", DESCENDING)])
    lessons().create_index([("fix_family", ASCENDING), ("created_at", DESCENDING)])
    versions().create_index([("version", ASCENDING)])


# ---------------------------------------------------------------- trials
def upsert_trial(doc: dict) -> None:
    """Idempotent on the deterministic trial id."""
    trials().replace_one({"_id": doc["_id"]}, doc, upsert=True)


def get_trial(trial_id: str) -> dict | None:
    return trials().find_one({"_id": trial_id})


def version_trials(version_id: str) -> list[dict]:
    return list(trials().find({"harness_version": version_id, "status": {"$in": ["done", "error"]}})
                .sort("created_at", ASCENDING))


def best_trial(version_id: str | None = None) -> dict | None:
    q = {"score.valid": True}
    if version_id:
        q["harness_version"] = version_id
    return trials().find_one(q, sort=[("score.fmax_mhz", DESCENDING)])


# ---------------------------------------------------------------- versions
def active_version() -> dict | None:
    return versions().find_one({"status": "active"}, sort=[("version", DESCENDING)])


def get_version(version_id: str) -> dict | None:
    return versions().find_one({"_id": version_id})


def refresh_version_stats(version_id: str) -> dict:
    ts = version_trials(version_id)
    valid = [t for t in ts if t.get("score", {}).get("valid")]
    best = max(valid, key=lambda t: t["score"]["fmax_mhz"], default=None)
    stats = {"trials": len(ts), "valid_trials": len(valid),
             "best_fmax_mhz": best["score"]["fmax_mhz"] if best else None,
             "best_trial_id": best["_id"] if best else None}
    return versions().find_one_and_update({"_id": version_id}, {"$set": {"stats": stats}},
                                          return_document=ReturnDocument.AFTER)


def plateau_policy() -> dict:
    return settings().find_one({"_id": "plateau_policy"})


# ---------------------------------------------------------------- lessons
def add_lesson(doc: dict) -> None:
    lessons().replace_one({"_id": doc["_id"]}, doc, upsert=True)


def recent_lessons(fix_family: str | None = None, limit: int = 5) -> list[dict]:
    q = {"outcome": {"$ne": "neutral"}}
    if fix_family:
        q["fix_family"] = fix_family
    return list(lessons().find(q).sort("created_at", DESCENDING).limit(limit))
