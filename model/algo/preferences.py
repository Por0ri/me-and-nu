"""Map persisted preference codes to existing model features without rewriting them."""

import csv
from dataclasses import dataclass
from pathlib import Path


# The source keys are from backend/app/seeds/onboarding_catalog_starter.json.
# Targets are exact rows in catalog/chips.csv. Korean label equivalence is
# documented in scripts/make_chip_catalog.py (MAIN_GENRES/MAIN_THEMES).
# Cross-kind aliases affect only the model feature, never the stored Q2 kind.
LEGACY_ALIASES = {
    ("genre", "science_fiction"): ("genre", "sci_fi"),  # SF
    ("theme", "growth"): ("theme", "coming_of_age"),  # 성장
    ("theme", "supernatural"): ("genre", "supernatural"),  # 초자연
    ("theme", "superpowers"): ("theme", "super_power"),  # 초능력
    ("theme", "journey"): ("theme", "travel"),  # 여행 / Travel
    ("theme", "body_swap"): ("theme", "body_swapping"),  # Body Swapping
    ("theme", "other_world"): ("theme", "isekai"),  # 이세계
    ("theme", "music"): ("genre", "music"),  # 음악
    ("theme", "mecha"): ("genre", "mecha"),  # 로봇
    ("theme", "dungeons"): ("theme", "dungeon"),  # 던전
    ("theme", "time_travel"): ("theme", "time_manipulation"),  # 시간 여행
}


@dataclass(frozen=True)
class ChipFeature:
    kind: str
    name: str
    canonical_code: str
    aliased: bool = False


def load_chip_features(path: Path) -> dict[tuple[str, str], ChipFeature]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        features = {
            (row["kind"], row["code"]): ChipFeature(row["kind"], row["anilist_name"], row["code"])
            for row in csv.DictReader(stream)
        }
    for source, target in LEGACY_ALIASES.items():
        if target not in features:
            raise ValueError(f"Preference alias target is absent from chips.csv: {target[0]}/{target[1]}")
        if source not in features:
            feature = features[target]
            features[source] = ChipFeature(feature.kind, feature.name, feature.canonical_code, aliased=True)
    return features


def apply_chip(user: dict, kind: str, code: str, label: str, tag_id: int, features: dict) -> None:
    """Retain original identity and expose unresolved concepts instead of guessing."""
    original = {"tag_id": tag_id, "kind": kind, "code": code, "label": label}
    user.setdefault("preference_tags", []).append(original)
    feature = features.get((kind, code))
    if feature is None:
        user.setdefault("unmapped_chips", []).append({**original, "reason": "no_exact_model_feature"})
        user["unknown_chips"] = user.get("unknown_chips", 0) + 1
        return
    destination = "genres" if feature.kind == "genre" else "themes"
    # Canonical and legacy IDs can express the same feature: do not double its weight.
    if feature.name not in user[destination]:
        user[destination].append(feature.name)


def unmapped_summary(users: dict) -> list[dict]:
    """Aggregate diagnostics without printing user/Tap IDs or silently losing choices."""
    counts = {}
    for user in users.values():
        for chip in user.get("unmapped_chips", []):
            key = (chip["kind"], chip["code"], chip["label"])
            counts[key] = counts.get(key, 0) + 1
    return [dict(kind=k[0], code=k[1], label=k[2], selections=count,
                 reason="no_exact_model_feature") for k, count in sorted(counts.items())]
