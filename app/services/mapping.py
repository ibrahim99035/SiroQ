"""Mapping confirmation + pharmacy identifier detection/resolution.

When an application has no fixed ``pharmacy_id`` (multi-pharmacy source) the
mapping step must expose an explicit "which column identifies the pharmacy"
choice and a dataset cannot be committed until it is resolved (hard gate).
"""
import uuid

import pandas as pd

from app.models.models import Pharmacies, Applications, MappingProfiles


def pick_pharmacy_identifier_column(df) -> str | None:
    """Auto-detect a low-cardinality, non-numeric column that names pharmacies.

    Returns the best candidate header or None when nothing is obvious so the
    UI can present an explicit dropdown instead of guessing silently.
    """
    best = None
    best_score = -1
    for col in df.columns:
        if not str(col).strip():
            continue
        series = df[col]
        if series.dtype.kind in "ifc":  # numeric
            continue
        distinct = series.nunique(dropna=True)
        total = max(series.notna().sum(), 1)
        ratio = distinct / total
        if distinct <= 1:
            continue
        if 0.02 < ratio < 0.5:
            if total > best_score:
                best_score = total
                best = col
    return best


def resolve_pharmacy(db, association_id, identifier_value) -> str:
    """Return the pharmacy id for an identifier value, creating it on demand."""
    value = str(identifier_value).strip()
    if not value:
        raise ValueError("Empty pharmacy identifier")
    ph = db.query(Pharmacies).filter(
        Pharmacies.association_id == association_id,
        Pharmacies.external_code == value,
    ).first()
    if ph:
        return ph.id
    ph = db.query(Pharmacies).filter(
        Pharmacies.association_id == association_id,
        Pharmacies.name == value,
    ).first()
    if ph:
        return ph.id
    ph = Pharmacies(id=str(uuid.uuid4()), name=value, external_code=value,
                    association_id=association_id)
    db.add(ph)
    db.flush()
    return ph.id


def save_mapping_profile(db, application_id, association_id, field_map, confirmed_by):
    """Upsert the application's mapping profile after a confirmed commit."""
    profile = db.query(MappingProfiles).filter(
        MappingProfiles.application_id == application_id
    ).first()
    from datetime import datetime, timezone
    if profile is None:
        profile = MappingProfiles(
            id=str(uuid.uuid4()),
            application_id=application_id,
            association_id=association_id,
            field_map=field_map,
            confidence_map={},
            confirmed_by=confirmed_by,
            confirmed_at=datetime.now(timezone.utc),
        )
        db.add(profile)
    else:
        profile.field_map = field_map
        profile.confirmed_by = confirmed_by
        profile.confirmed_at = datetime.now(timezone.utc)
    db.flush()
    return profile