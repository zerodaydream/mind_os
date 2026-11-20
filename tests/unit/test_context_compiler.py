from backend.services.agents.core.contracts.evidence import Evidence, EvidencePack
from backend.services.context.compiler import ContextCompiler, format_evidence_block
from backend.services.context.prompts import load_prompt_module


def test_compiler_keeps_task_when_over_budget() -> None:
    compiler = ContextCompiler(total_budget_tokens=200)
    pack = EvidencePack(
        items=[
            Evidence(eid="E1", kind="mail", text="a" * 2000, title="Invoice"),
            Evidence(eid="E2", kind="mail", text="b" * 2000, title="Other"),
        ]
    )
    compiled = compiler.compile_turn(
        core_system=load_prompt_module("core_system"),
        evidence=pack,
        recent_turns="old turn " * 200,
        task="What did Acme invoice?",
    )
    assert "What did Acme invoice?" in compiled.text
    assert "task" not in compiled.evicted


def test_evidence_best_first_second_last() -> None:
    pack = EvidencePack(
        items=[
            Evidence(eid="E1", kind="mail", text="best", title="A"),
            Evidence(eid="E2", kind="mail", text="mid", title="B"),
            Evidence(eid="E3", kind="mail", text="third", title="C"),
        ]
    )
    block = format_evidence_block(pack)
    # Order: E1, E3, E2
    assert block.index("[E1]") < block.index("[E3]") < block.index("[E2]")
