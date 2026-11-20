from backend.services.agents.core.contracts.evidence import Evidence, EvidencePack
from backend.services.verification.grounding import verify_grounded_answer


def test_invented_filename_caught() -> None:
    pack = EvidencePack(
        items=[
            Evidence(
                eid="E1",
                kind="mail",
                text="Attached report.pdf from Acme",
                title="Acme",
                metadata={"attachment_names": ["report.pdf"]},
            )
        ]
    )
    report = verify_grounded_answer(
        "See invented_deck.pdf for details [E1]",
        pack,
        citations=["E1"],
    )
    assert not report.ok
    assert any(v.startswith("invented_filename:") for v in report.violations)


def test_real_filename_ok() -> None:
    pack = EvidencePack(
        items=[
            Evidence(
                eid="E1",
                kind="mail",
                text="Please review report.pdf",
                title="Acme",
                metadata={"attachment_names": ["report.pdf"]},
            )
        ]
    )
    report = verify_grounded_answer(
        "The file is report.pdf [E1]",
        pack,
        citations=["E1"],
    )
    assert report.ok


def test_unknown_citation() -> None:
    pack = EvidencePack(items=[Evidence(eid="E1", kind="mail", text="hi", title="x")])
    report = verify_grounded_answer("Claim [E9]", pack)
    assert not report.ok
    assert "unknown_citation:E9" in report.violations
