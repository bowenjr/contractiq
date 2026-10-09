"""Atomic promotion of received-file citations to controlled document versions."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from core.deliverable_discovery import SourcePromotion

AuditWriter = Callable[[sqlite3.Connection, str, str, str, str], None]


def promote_source(
    conn: sqlite3.Connection,
    *,
    bid_id: str,
    deliverable_id: str,
    citation_id: str,
    document_version_id: str,
    rationale: str,
    operation_id: str,
    actor: str,
    audit: AuditWriter,
) -> SourcePromotion:
    """Validate and append a promotion and controlled-version deliverable link."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = conn.execute(
            """SELECT * FROM deliverable_discovery_source_promotions
            WHERE bid_id=? AND operation_id=?""",
            (bid_id, operation_id),
        ).fetchone()
        if existing is not None:
            conn.commit()
            return SourcePromotion.model_validate(dict(existing))

        source = conn.execute(
            """SELECT source.*,proposal.bid_id FROM
            deliverable_discovery_proposal_sources_v2 source
            JOIN deliverable_discovery_proposals proposal
            ON proposal.proposal_id=source.proposal_id
            WHERE source.citation_id=?""",
            (citation_id,),
        ).fetchone()
        if source is None or source["bid_id"] != bid_id:
            raise ValueError(
                "citation is missing or belongs to another Bid; choose a citation from this Bid"
            )
        proposal_id = str(source["proposal_id"])

        current = conn.execute(
            """SELECT base.decision,base.linked_deliverable_id
            FROM deliverable_discovery_review_events base
            WHERE base.proposal_id=? AND base.bid_id=? AND NOT EXISTS(
                SELECT 1 FROM deliverable_discovery_review_events newer
                WHERE newer.supersedes_event_id=base.event_id)
            ORDER BY base.reviewed_at DESC LIMIT 1""",
            (proposal_id, bid_id),
        ).fetchone()
        if (
            current is None
            or current["decision"] != "ACCEPTED"
            or current["linked_deliverable_id"] != deliverable_id
        ):
            raise ValueError(
                "proposal is not currently accepted for this deliverable; accept the proposal "
                "before promoting its source"
            )
        deliverable = conn.execute(
            "SELECT bid_id FROM deliverable_items WHERE deliverable_id=?",
            (deliverable_id,),
        ).fetchone()
        if deliverable is None or deliverable["bid_id"] != bid_id:
            raise ValueError(
                "deliverable is missing or belongs to another Bid; choose the accepted "
                "deliverable in this Bid"
            )
        if source["document_version_id"] is not None:
            raise ValueError(
                "citation already names a controlled document version and cannot be promoted; "
                "use its existing source link"
            )
        received_file_id = str(source["received_file_id"])
        received_file = conn.execute(
            """SELECT release.bid_id FROM bid_received_files file
            JOIN bid_received_releases release ON release.release_id=file.release_id
            WHERE file.file_id=?""",
            (received_file_id,),
        ).fetchone()
        if received_file is None or received_file["bid_id"] != bid_id:
            raise ValueError(
                "received file is missing or belongs to another Bid; restore the citation's "
                "Package Intake file before promoting it"
            )

        prior = conn.execute(
            """SELECT * FROM deliverable_discovery_source_promotions
            WHERE deliverable_id=? AND citation_id=?""",
            (deliverable_id, citation_id),
        ).fetchone()
        if prior is not None:
            raise ValueError("already promoted")

        file_link = conn.execute(
            """SELECT base.* FROM bid_received_file_document_links base
            WHERE base.file_id=? AND NOT EXISTS(
                SELECT 1 FROM bid_received_file_document_links newer
                WHERE newer.supersedes_link_id=base.file_document_link_id)""",
            (received_file_id,),
        ).fetchone()
        link_guidance = (
            "the human must first link the received file to the controlled version in Package "
            "Intake using EXACT_BYTES or REPRESENTS_VERSION"
        )
        if file_link is None:
            raise ValueError(f"no current file-to-version link exists; {link_guidance}")
        if file_link["relationship"] not in ("EXACT_BYTES", "REPRESENTS_VERSION"):
            raise ValueError(
                f"current file-to-version link is SUPPORTING_EVIDENCE; {link_guidance}"
            )
        if file_link["document_version_id"] != document_version_id:
            raise ValueError(
                "supplied document version does not match the current Package Intake link; "
                "select the linked controlled version or correct the link in Package Intake"
            )

        version = conn.execute(
            """SELECT d.bid_id,d.control_managed FROM document_versions v
            JOIN documents d ON d.id=v.document_id WHERE v.document_version_id=?""",
            (document_version_id,),
        ).fetchone()
        if version is None or version["bid_id"] != bid_id or not int(version["control_managed"]):
            raise ValueError(
                "document version is not a controlled version in this Bid; link the received "
                "file to a controlled version for this Bid in Package Intake"
            )

        promotion = SourcePromotion(
            bid_id=bid_id,
            deliverable_id=deliverable_id,
            proposal_id=proposal_id,
            citation_id=citation_id,
            received_file_id=received_file_id,
            document_version_id=document_version_id,
            file_document_link_id=str(file_link["file_document_link_id"]),
            link_relationship=str(file_link["relationship"]),
            rationale=rationale,
            operation_id=operation_id,
            promoted_by=actor,
            promoted_at=datetime.now(UTC),
        )
        conn.execute(
            """INSERT INTO deliverable_discovery_source_promotions VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                promotion.promotion_id,
                promotion.bid_id,
                promotion.deliverable_id,
                promotion.proposal_id,
                promotion.citation_id,
                promotion.received_file_id,
                promotion.document_version_id,
                promotion.file_document_link_id,
                promotion.link_relationship,
                promotion.rationale,
                promotion.operation_id,
                promotion.promoted_by,
                promotion.promoted_at.isoformat(),
            ),
        )
        link_id = f"DLK-{uuid4().hex}"
        conn.execute(
            "INSERT INTO deliverable_links VALUES (?,?,?,?,?,?,?,?)",
            (
                link_id,
                bid_id,
                deliverable_id,
                "DOCUMENT_VERSION",
                document_version_id,
                "SOURCE_DOCUMENT_VERSION",
                promotion.promoted_at.isoformat(),
                actor,
            ),
        )
        audit(
            conn,
            bid_id,
            actor,
            "deliverable_discovery_source_promoted",
            promotion.promotion_id,
        )
        audit(conn, bid_id, actor, "deliverable_link_created", link_id)
        conn.commit()
        return promotion
    except Exception:
        conn.rollback()
        raise
