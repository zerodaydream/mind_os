from backend.services.commitments.core.rules import extract_rules


def test_rules_finds_i_will() -> None:
    out = extract_rules("I will send the doc by Friday.")
    assert len(out) == 1
    assert out[0].confidence == 0.55
