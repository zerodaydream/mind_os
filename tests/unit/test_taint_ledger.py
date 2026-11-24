from mindos.domain import TaintLedger, TaintSource


def test_taint_is_per_run_and_only_code_adds_it() -> None:
    ledger = TaintLedger()
    assert not ledger.is_tainted("run_1")
    ledger.mark("run_1", TaintSource.EXTERNAL_EMAIL)
    ledger.mark("run_1", TaintSource.WEB)
    assert ledger.sources("run_1") == {TaintSource.EXTERNAL_EMAIL, TaintSource.WEB}
    assert not ledger.is_tainted("run_2")


def test_forget_clears_a_run_and_none_is_a_valid_run_key() -> None:
    ledger = TaintLedger()
    ledger.mark(None, TaintSource.MCP)
    assert ledger.is_tainted(None)
    ledger.forget(None)
    ledger.forget("never_seen")  # harmless
    assert not ledger.is_tainted(None)
