import pytest

from core.proposals import ContentOrigin, ProposalSection, SectionRole


def test_customer_firewall_rejects_internal_content() -> None:
    with pytest.raises(ValueError):
        ProposalSection(
            role=SectionRole.CUSTOM,
            heading="Internal",
            text="Gross margin is 20%",
            origin=ContentOrigin.OPERATOR_AUTHORED,
        )


def test_structured_content_requires_source() -> None:
    with pytest.raises(ValueError):
        ProposalSection(
            role=SectionRole.SCOPE,
            heading="Scope",
            text="Synthetic scope",
            origin=ContentOrigin.STRUCTURED_SOURCE,
        )
