"""Choose a cover from sourced artwork for the actual editorial angle of a RAG deck."""

from __future__ import annotations

from dataclasses import replace
import re
import unicodedata
from typing import Mapping, Sequence

from ani_rag.agents.content_generation.layout_director import ImageCopyMismatchError
from ani_rag.content import CardDraft, ImageAsset, kitsu_image_slot
from ani_rag.models import RagContentType
from ani_rag.retrieval import EvidenceChunk, RetrievedEvidence


COVER_TYPES = {
    "beginner_guide": RagContentType.GUIDE,
    "cast_intro_grid": RagContentType.CHARACTER,
    "fan_recommendation": RagContentType.CURATION,
}
_POSTER_ROLES = {"poster", "catalog_poster", "anime_poster", "work_poster"}
_WORK_ART = _POSTER_ROLES | {"cover", "key_art", "catalog_cover"}
_RECOMMENDATION = re.compile(r"추천|recommend|fans? of|(?:anime|shows) like|if you (?:like|love)|好きなら|おすすめ|オススメ", re.IGNORECASE)
_NEGATIVE_RECOMMENDATION = re.compile(r"비추천|추천하지\s*않|(?:not|never|n.t)\s+recommend|おすすめしない", re.IGNORECASE)
_MAIN_CAST = re.compile(r"주연|주인공|주요 인물|주요 캐릭터|主人公|主要人物|main (?:cast|character|role)|protagonist|character role: main", re.IGNORECASE)


def _norm(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _role(asset: ImageAsset) -> str:
    return asset.image_role.partition(":")[0].casefold()


def is_work_poster(asset: ImageAsset) -> bool:
    if asset.work_link_status != "matched" or len(asset.menu_work_ids) != 1:
        return False
    slot = kitsu_image_slot(asset.source_url)
    return slot[0] == "poster" if slot else _role(asset) in _POSTER_ROLES


def _cited_chunks(card: CardDraft, chunks: Mapping[str, EvidenceChunk]) -> list[EvidenceChunk]:
    return [chunks[key] for claim in card.claims for key in claim.evidence_chunk_ids if key in chunks]


def cast_identity(asset: ImageAsset, chunks: Mapping[str, EvidenceChunk]) -> set[tuple[str, str]]:
    """Only depict a named lead whose identity and role have a source in this deck."""
    if (asset.work_link_status != "matched" or len(asset.menu_work_ids) != 1
            or _role(asset) not in {"character", "character_art", "portrait"}):
        return set()
    depicted = {link.entity_id for link in asset.entity_links
                if link.status == "matched" and link.relation_type == "depicts"}
    if len(depicted) > 1:
        return set()
    identities: set[tuple[str, str]] = set()
    for link in asset.entity_links:
        chunk = chunks.get(link.source_chunk_id)
        if (chunk and chunk.evidence_id == link.source_document_id and chunk.menu_work_ids == asset.menu_work_ids
                and link.status == "matched" and link.relation_type == "depicts"
                and (":main" in asset.image_role or _MAIN_CAST.search(chunk.text))):
            identities.add(("entity", str(link.entity_id)))
            if link.entity_name:
                identities.add(("name", _norm(link.entity_name)))
    slot = kitsu_image_slot(asset.source_url)
    kitsu_character = None
    if slot and slot[0] == "character":
        kitsu_character = slot[1]
    elif not slot and asset.source_id == "kitsu_anime_catalog" and asset.image_role.startswith("character:main:"):
        kitsu_character = asset.source_native_id
    if kitsu_character:
        for chunk in chunks.values():
            if (chunk.source_id == "kitsu_anime_catalog" and chunk.menu_work_ids == asset.menu_work_ids
                    and chunk.locator.startswith(f"kitsu:characters/{kitsu_character}/")
                    and "Anime character role: main" in chunk.text):
                identities.add(("kitsu", kitsu_character))
                identities.add(("name", _norm(re.sub(r":\d+-\d+$", "", chunk.locator.split("/", 2)[2]))))
    if identities and asset.alt_text:
        identities.add(("name", _norm(asset.alt_text.rsplit("—", 1)[-1])))
    return identities


def select_cast_detail(
    card: CardDraft, chunks: Mapping[str, EvidenceChunk], assets: Mapping[str, ImageAsset],
    used: set[tuple[str, str]], reserved_ids: set[str],
) -> CardDraft:
    selected = assets[card.image_asset_id]
    cited = {chunk.chunk_id: chunk for chunk in _cited_chunks(card, chunks)}
    works = {w for chunk in cited.values() for w in chunk.menu_work_ids}
    candidates = sorted((asset for asset in assets.values() if asset.work_link_status == "matched"
                         and len(asset.menu_work_ids) == 1 and asset.menu_work_ids[0] in works
                         and not used.intersection(asset.source_identity_keys)
                         and (asset == selected or asset.asset_id not in reserved_ids)),
                        key=lambda a: (a != selected, a.recent_use_count, a.asset_id))
    if selected in candidates and _role(selected) in _WORK_ART:
        return card
    copy = _norm(" ".join((card.headline, card.body, *(claim.text for claim in card.claims))))
    portraits = [(a, cast_identity(a, cited)) for a in candidates]
    portraits = [(a, identity) for a, identity in portraits if identity]
    distinct = [(a, identity) for a, identity in portraits
                if any(name in copy for kind, name in identity if kind == "name")]
    matches = distinct or portraits
    if matches and all(identities & matches[0][1] for _, identities in matches):
        return _set_art(card, matches[0][0])
    alternative = next((asset for asset in candidates if _role(asset) in _WORK_ART), None)
    if alternative is not None:
        return _set_art(card, alternative)
    raise ImageCopyMismatchError("No independently identified portrait or work illustration for cast page",
                                 card_id=card.card_id, image_asset_id=card.image_asset_id,
                                 headline=card.headline, body=card.body)


def _set_art(card: CardDraft, asset: ImageAsset, extra: Sequence[ImageAsset] = (), template: str | None = None) -> CardDraft:
    return replace(card, image_asset_id=asset.asset_id,
                   component_image_asset_ids=tuple(a.asset_id for a in extra),
                   source_image_url=asset.source_url, source_page_url=asset.source_page_url or asset.source_url,
                   image_credit=asset.source_id, layout=replace(card.layout, template=template or card.layout.template))


def _work_sequence(
    cards: Sequence[CardDraft], chunks: Mapping[str, EvidenceChunk],
    assets: Mapping[str, ImageAsset],
) -> tuple[int, ...]:
    """Identify the anchor, then the four featured works in article order."""
    result: list[int] = []
    for card in cards:
        linked = {work for chunk in _cited_chunks(card, chunks) for work in chunk.menu_work_ids}
        depicted = assets[card.image_asset_id].menu_work_ids
        if len(depicted) != 1 or depicted[0] not in linked:
            return ()
        work = depicted[0]
        if work not in result:
            result.append(work)
    return tuple(result)


def _fan_posters(
    cards: Sequence[CardDraft], assets: Mapping[str, ImageAsset], available: Sequence[ImageAsset],
    chunks: Mapping[str, EvidenceChunk], source_title: str, statements: Sequence[str], scope: Sequence[int],
) -> tuple[ImageAsset, ...]:
    recommendations = [statement for statement in statements if _RECOMMENDATION.search(statement)
                       and not _NEGATIVE_RECOMMENDATION.search(statement)]
    if _NEGATIVE_RECOMMENDATION.search(source_title) or not (_RECOMMENDATION.search(source_title) or recommendations):
        return ()
    works = _work_sequence(cards, chunks, assets)
    if len(works) != 5 or (len(scope) == 1 and works[0] != scope[0]):
        return ()
    chosen: list[ImageAsset] = []
    used_keys: set[tuple[str, str]] = set()
    recommendations_text = "\n".join(recommendations)
    for work in works:
        work_assets = [asset for asset in available if asset.menu_work_ids == (work,)
                       and is_work_poster(asset) and (chosen or not asset.used_as_cover_before)
                       and not used_keys.intersection(asset.source_identity_keys)]
        if not work_assets:
            return ()
        # A work link alone does not mean the source article recommends this title.
        names = {_norm(asset.alt_text.rsplit("—", 1)[-1]) for asset in work_assets if asset.alt_text}
        for chunk in chunks.values():
            if chunk.menu_work_ids == (work,) and chunk.text.startswith("Work: "):
                names.update(_norm(name) for name in chunk.text.split("\n", 1)[0][6:].split(" / ") if name.strip())
        name_patterns = [r"(?<![a-z0-9])" + re.escape(name) + r"(?![a-z0-9])" for name in names if name]
        if not chosen:
            # The title can identify the anchor, while some articles put it only
            # in a verified 'fans of …' claim. A recommended title is not the anchor.
            anchor_supported = any(re.search(name, source_title) or re.search(
                r"(?:fans? of|if you (?:liked?|loved?)|(?:anime|shows) like)\s+[〈「『]?" + name
                + "|" + name + r"[〉」』]?(?:을|를|의)?\s*(?:좋아하|좋아했|팬|ファン|好き)",
                recommendations_text) for name in name_patterns)
            if not anchor_supported:
                return ()
        elif not any(re.search(name, recommendations_text) for name in name_patterns):
            return ()
        asset = work_assets[0]
        chosen.append(asset)
        used_keys.update(asset.source_identity_keys)
    return tuple(chosen)


def plan_cover(
    cards: list[CardDraft], mode: str, retrieval: RetrievedEvidence,
    assets: Mapping[str, ImageAsset], source_context: Mapping[str, object] | None,
) -> CardDraft:
    cover = cards[0]
    if COVER_TYPES.get(mode) != retrieval.content_type:
        return cover
    cited_ids = {key for card in cards for claim in card.claims for key in claim.evidence_chunk_ids}
    chunks = {chunk.chunk_id: chunk for chunk in retrieval.chunks if chunk.chunk_id in cited_ids}
    used = {key for card in cards[1:] for key in assets[card.image_asset_id].source_identity_keys}
    available = sorted((asset for asset in assets.values() if asset.work_link_status == "matched"
                        and len(asset.menu_work_ids) == 1
                        and not used.intersection(asset.source_identity_keys)),
                       key=lambda a: (a.asset_id != cover.image_asset_id, a.recent_use_count,
                                      -((a.width or 0) * (a.height or 0)), a.asset_id))
    primary = assets[cover.image_asset_id]
    portraits: list[ImageAsset] = []
    if mode == "fan_recommendation":
        # The web article (or the seeded editorial document) must actually feature
        # these works; unrelated titles elsewhere in the retrieval cannot supply a list.
        if source_context is not None:
            title = str(source_context.get("title") or "")
            statements = [str(row["claim"]) for row in (source_context.get("verified_claims") or ())
                          if isinstance(row, dict) and row.get("claim")]
        else:
            sources = [d for d in retrieval.documents if d.document_id in retrieval.seed_evidence_ids
                       and d.content_type == RagContentType.CURATION]
            title = sources[0].title if len(sources) == 1 else ""
            statements = [*sources[0].claims, *(e.text for e in sources[0].excerpts)] if len(sources) == 1 else []
        selection = _fan_posters(cards, assets, available, chunks, _norm(title),
                                 [_norm(s) for s in statements], retrieval.menu_work_ids)
    else:
        work_ids = set(retrieval.menu_work_ids)
        cited_works = {w for chunk in chunks.values() for w in chunk.menu_work_ids}
        if not work_ids:
            work_ids = cited_works
        available = [asset for asset in available if asset.menu_work_ids == tuple(work_ids)
                     and work_ids <= cited_works]
        if mode == "beginner_guide":
            selection = tuple(asset for asset in available if is_work_poster(asset) and not asset.used_as_cover_before)[:1]
        else:
            seen: set[tuple[str, str]] = set()
            for asset in sorted(available, key=lambda item: item.used_as_cover_before):
                identity = cast_identity(asset, chunks)
                if (not identity or identity & seen or used.intersection(asset.source_identity_keys)
                        or (not portraits and asset.used_as_cover_before)):
                    continue
                portraits.append(asset)
                seen.update(identity)
                used.update(asset.source_identity_keys)
                if len(portraits) == 4:
                    break
            selection = tuple(portraits) if len(portraits) >= 3 else ()
    template = mode
    if not selection:
        template = cover.layout.template
        # A representative work image can illustrate the topic when there are not
        # enough separate attributed assets for a collage. Its normal visual check still runs.
        scope = set(retrieval.menu_work_ids)
        if mode == "fan_recommendation":
            scope = {w for ch in _cited_chunks(cover, chunks) for w in ch.menu_work_ids}
        available = [asset for asset in available if not asset.used_as_cover_before]
        if len(scope) == 1:
            available = [asset for asset in available if asset.menu_work_ids == tuple(scope)]
        else:
            available = [asset for asset in available if asset.asset_id == primary.asset_id]
        selection = tuple(a for a in available if _role(a) in _WORK_ART)[:1]
        if (not selection and primary in available and mode != "cast_intro_grid"
                and _role(primary) not in {"character", "character_art", "portrait"}):
            selection = (primary,)
        if not selection and mode == "cast_intro_grid" and portraits:
            selection = (portraits[0],)
        if not selection:
            raise ImageCopyMismatchError(
                "No distinct subject-matched cover artwork is available", card_id=cover.card_id,
                image_asset_id=primary.asset_id, headline=cover.headline, body=cover.body,
            )
    selected, *extra = selection
    return _set_art(cover, selected, extra, template)
