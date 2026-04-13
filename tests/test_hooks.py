"""Tests for the hook system: registries, on_hook, merge_context."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from monet.core.hooks import (
    GraphHookRegistry,
    HookRegistry,
    default_hook_registry,
    merge_context,
    on_hook,
    run_after_agent_hooks,
    run_before_agent_hooks,
)
from monet.exceptions import SemanticError
from monet.types import AgentMeta, AgentResult, AgentRunContext
from tests.conftest import make_ctx

# ---------------------------------------------------------------------------
# merge_context
# ---------------------------------------------------------------------------


class TestMergeContext:
    def test_none_returns_original(self) -> None:
        ctx = make_ctx(task="hello")
        assert merge_context(ctx, None) is ctx

    def test_merges_allowed_fields(self) -> None:
        original = make_ctx(task="old", command="fast")
        modified = AgentRunContext(
            task="new",
            context=[],
            command="deep",
            trace_id="",
            run_id="",
            agent_id="",
            skills=[],
        )
        result = merge_context(original, modified)
        assert result["task"] == "new"
        assert result["command"] == "deep"

    def test_protects_identity_fields(self) -> None:
        original = make_ctx(run_id="run-1", trace_id="trace-1", agent_id="writer")
        modified = AgentRunContext(
            task="",
            context=[],
            command="fast",
            trace_id="hacked",
            run_id="hacked",
            agent_id="hacked",
            skills=[],
        )
        result = merge_context(original, modified)
        assert result["run_id"] == "run-1"
        assert result["trace_id"] == "trace-1"
        assert result["agent_id"] == "writer"

    def test_rejects_unknown_keys(self) -> None:
        original = make_ctx()
        bad: Any = {"task": "x", "unknown_key": "y"}
        with pytest.raises(SemanticError, match="unknown_key"):
            merge_context(original, bad)


# ---------------------------------------------------------------------------
# HookRegistry
# ---------------------------------------------------------------------------


class TestHookRegistry:
    def test_register_and_lookup(self) -> None:
        reg = HookRegistry()
        handler = AsyncMock()
        reg.register("before_agent", handler, match="writer")
        hooks = reg.lookup("before_agent", "writer", "fast")
        assert len(hooks) == 1
        assert hooks[0].handler is handler

    def test_rejects_invalid_event(self) -> None:
        reg = HookRegistry()
        with pytest.raises(ValueError, match=r"before_agent.*after_agent"):
            reg.register("invalid_event", AsyncMock())

    def test_match_wildcard(self) -> None:
        reg = HookRegistry()
        handler = AsyncMock()
        reg.register("before_agent", handler, match="*")
        assert len(reg.lookup("before_agent", "any_agent", "any_command")) == 1

    def test_match_agent_id_only(self) -> None:
        reg = HookRegistry()
        handler = AsyncMock()
        reg.register("before_agent", handler, match="writer")
        assert len(reg.lookup("before_agent", "writer", "fast")) == 1
        assert len(reg.lookup("before_agent", "reader", "fast")) == 0

    def test_match_agent_command(self) -> None:
        reg = HookRegistry()
        handler = AsyncMock()
        reg.register("before_agent", handler, match="writer(deep)")
        assert len(reg.lookup("before_agent", "writer", "deep")) == 1
        assert len(reg.lookup("before_agent", "writer", "fast")) == 0

    def test_match_pipe_separated(self) -> None:
        reg = HookRegistry()
        handler = AsyncMock()
        reg.register("before_agent", handler, match="writer|qa")
        assert len(reg.lookup("before_agent", "writer", "fast")) == 1
        assert len(reg.lookup("before_agent", "qa", "fast")) == 1
        assert len(reg.lookup("before_agent", "researcher", "fast")) == 0

    def test_priority_ordering(self) -> None:
        reg = HookRegistry()
        h1 = AsyncMock(__qualname__="h1")
        h2 = AsyncMock(__qualname__="h2")
        h3 = AsyncMock(__qualname__="h3")
        reg.register("before_agent", h2, priority=10)
        reg.register("before_agent", h1, priority=0)
        reg.register("before_agent", h3, priority=10)
        hooks = reg.lookup("before_agent", "any", "any")
        assert [h.handler for h in hooks] == [h1, h2, h3]

    def test_hook_scope_isolation(self) -> None:
        reg = HookRegistry()

        async def h1(ctx: Any, meta: Any) -> None:
            pass

        async def h2(ctx: Any, meta: Any) -> None:
            pass

        reg.register("before_agent", h1)
        assert len(reg.registered_hooks()) == 1
        with reg.hook_scope():
            reg.register("before_agent", h2)
            assert len(reg.registered_hooks()) == 2
        assert len(reg.registered_hooks()) == 1

    def test_clear(self) -> None:
        reg = HookRegistry()

        async def h(ctx: Any, meta: Any) -> None:
            pass

        reg.register("before_agent", h)
        reg.clear()
        assert len(reg.registered_hooks()) == 0


# ---------------------------------------------------------------------------
# on_hook decorator
# ---------------------------------------------------------------------------


class TestOnHook:
    def test_registers_to_default_registry(self) -> None:
        with default_hook_registry.hook_scope():

            @on_hook("before_agent", match="test-agent")
            async def my_hook(
                ctx: AgentRunContext, meta: AgentMeta
            ) -> AgentRunContext | None:
                return None

            hooks = default_hook_registry.registered_hooks()
            assert len(hooks) == 1
            assert hooks[0][0] == "before_agent"
            assert hooks[0][1] == "test-agent"

    def test_registers_to_custom_registry(self) -> None:
        reg = HookRegistry()

        @on_hook("after_agent", registry=reg)
        async def my_hook(result: AgentResult, meta: AgentMeta) -> AgentResult | None:
            return None

        assert len(reg.registered_hooks()) == 1

    def test_rejects_sync_function(self) -> None:
        with pytest.raises(TypeError, match="async"):

            @on_hook("before_agent")
            def sync_hook(ctx: AgentRunContext, meta: AgentMeta) -> None:  # type: ignore[type-var]
                pass


# ---------------------------------------------------------------------------
# run_before_agent_hooks
# ---------------------------------------------------------------------------


class TestRunBeforeAgentHooks:
    async def test_no_hooks_returns_original(self) -> None:
        reg = HookRegistry()
        ctx = make_ctx(task="hello")
        result = await run_before_agent_hooks(ctx, "writer", "fast", registry=reg)
        assert result is ctx

    async def test_hook_modifies_context(self) -> None:
        reg = HookRegistry()

        async def inject_tone(ctx: AgentRunContext, meta: AgentMeta) -> AgentRunContext:
            return AgentRunContext(
                task=ctx["task"],
                context=[{"role": "system", "content": "be formal"}],
                command=ctx["command"],
                trace_id=ctx["trace_id"],
                run_id=ctx["run_id"],
                agent_id=ctx["agent_id"],
                skills=ctx["skills"],
            )

        reg.register("before_agent", inject_tone, match="writer")
        ctx = make_ctx(task="write something", agent_id="writer")
        result = await run_before_agent_hooks(ctx, "writer", "fast", registry=reg)
        assert result["context"] == [{"role": "system", "content": "be formal"}]

    async def test_hook_returns_none_passes_through(self) -> None:
        reg = HookRegistry()

        async def noop_hook(ctx: AgentRunContext, meta: AgentMeta) -> None:
            pass

        reg.register("before_agent", noop_hook)
        ctx = make_ctx(task="original")
        result = await run_before_agent_hooks(ctx, "any", "any", registry=reg)
        assert result["task"] == "original"

    async def test_hook_error_raises_semantic_error(self) -> None:
        reg = HookRegistry()

        async def bad_hook(ctx: AgentRunContext, meta: AgentMeta) -> None:
            raise RuntimeError("boom")

        reg.register("before_agent", bad_hook)
        with pytest.raises(SemanticError, match="boom"):
            await run_before_agent_hooks(make_ctx(), "any", "any", registry=reg)

    async def test_hook_timeout_raises_semantic_error(self) -> None:
        import asyncio

        reg = HookRegistry()

        async def slow_hook(ctx: AgentRunContext, meta: AgentMeta) -> None:
            await asyncio.sleep(10)

        reg.register("before_agent", slow_hook, timeout=0.01)
        with pytest.raises(SemanticError, match="timed out"):
            await run_before_agent_hooks(make_ctx(), "any", "any", registry=reg)

    async def test_chain_of_hooks_sequential(self) -> None:
        reg = HookRegistry()
        order: list[str] = []

        async def hook_a(ctx: AgentRunContext, meta: AgentMeta) -> None:
            order.append("a")

        async def hook_b(ctx: AgentRunContext, meta: AgentMeta) -> None:
            order.append("b")

        reg.register("before_agent", hook_a, priority=0)
        reg.register("before_agent", hook_b, priority=1)
        await run_before_agent_hooks(make_ctx(), "any", "any", registry=reg)
        assert order == ["a", "b"]

    async def test_protected_fields_cannot_be_overwritten(self) -> None:
        reg = HookRegistry()

        async def evil_hook(ctx: AgentRunContext, meta: AgentMeta) -> AgentRunContext:
            return AgentRunContext(
                task=ctx["task"],
                context=ctx["context"],
                command=ctx["command"],
                trace_id="evil",
                run_id="evil",
                agent_id="evil",
                skills=ctx["skills"],
            )

        reg.register("before_agent", evil_hook)
        ctx = make_ctx(run_id="safe", trace_id="safe", agent_id="safe")
        result = await run_before_agent_hooks(ctx, "safe", "fast", registry=reg)
        assert result["run_id"] == "safe"
        assert result["trace_id"] == "safe"
        assert result["agent_id"] == "safe"


# ---------------------------------------------------------------------------
# run_after_agent_hooks
# ---------------------------------------------------------------------------


class TestRunAfterAgentHooks:
    async def test_no_hooks_returns_original(self) -> None:
        reg = HookRegistry()
        result = AgentResult(success=True, output="hello")
        out = await run_after_agent_hooks(result, "writer", "fast", registry=reg)
        assert out is result

    async def test_hook_modifies_result(self) -> None:
        reg = HookRegistry()
        from dataclasses import replace

        async def enrich(result: AgentResult, meta: AgentMeta) -> AgentResult:
            return replace(result, output=f"[enriched] {result.output}")

        reg.register("after_agent", enrich)
        result = AgentResult(success=True, output="original")
        out = await run_after_agent_hooks(result, "writer", "fast", registry=reg)
        assert out.output == "[enriched] original"

    async def test_hook_returns_none_passes_through(self) -> None:
        reg = HookRegistry()

        async def noop(result: AgentResult, meta: AgentMeta) -> None:
            pass

        reg.register("after_agent", noop)
        result = AgentResult(success=True, output="kept")
        out = await run_after_agent_hooks(result, "writer", "fast", registry=reg)
        assert out.output == "kept"

    async def test_hook_returning_wrong_type_raises(self) -> None:
        reg = HookRegistry()

        async def bad_return(result: AgentResult, meta: AgentMeta) -> dict[str, Any]:
            return {"bad": "type"}

        reg.register("after_agent", bad_return)
        with pytest.raises(SemanticError, match="AgentResult or None"):
            await run_after_agent_hooks(
                AgentResult(success=True), "any", "any", registry=reg
            )


# ---------------------------------------------------------------------------
# GraphHookRegistry
# ---------------------------------------------------------------------------


class TestGraphHookRegistry:
    async def test_run_no_hooks(self) -> None:
        reg = GraphHookRegistry()
        obs = {"key": "value"}
        result = await reg.run("nonexistent", obs)
        assert result is obs

    async def test_run_modifies_observation(self) -> None:
        reg = GraphHookRegistry()

        async def add_field(obs: dict[str, Any]) -> dict[str, Any]:
            return {**obs, "added": True}

        reg.register("before_wave", add_field)
        result = await reg.run("before_wave", {"original": True})
        assert result["added"] is True
        assert result["original"] is True

    async def test_run_sequential_chain(self) -> None:
        reg = GraphHookRegistry()
        order: list[str] = []

        async def hook_a(obs: dict[str, Any]) -> dict[str, Any]:
            order.append("a")
            return {**obs, "a": True}

        async def hook_b(obs: dict[str, Any]) -> dict[str, Any]:
            order.append("b")
            assert obs.get("a") is True  # sees output of previous hook
            return {**obs, "b": True}

        reg.register("test_event", hook_a)
        reg.register("test_event", hook_b)
        result: dict[str, bool] = await reg.run("test_event", {})
        assert order == ["a", "b"]
        assert result == {"a": True, "b": True}

    async def test_on_error_raise_propagates(self) -> None:
        reg = GraphHookRegistry()

        async def bad_hook(obs: Any) -> None:
            raise RuntimeError("graph hook failed")

        reg.register("test_event", bad_hook, on_error="raise")
        with pytest.raises(RuntimeError, match="graph hook failed"):
            await reg.run("test_event", {})

    async def test_on_error_log_swallows(self) -> None:
        reg = GraphHookRegistry()

        async def bad_hook(obs: Any) -> None:
            raise RuntimeError("swallowed")

        reg.register("test_event", bad_hook, on_error="log")
        result = await reg.run("test_event", {"safe": True})
        assert result == {"safe": True}

    async def test_hook_returns_none_passes_through(self) -> None:
        reg = GraphHookRegistry()

        async def noop(obs: dict[str, Any]) -> None:
            pass

        reg.register("test_event", noop)
        result = await reg.run("test_event", {"kept": True})
        assert result == {"kept": True}

    def test_has_hooks(self) -> None:
        reg = GraphHookRegistry()
        assert not reg.has_hooks("test_event")
        reg.register("test_event", AsyncMock())
        assert reg.has_hooks("test_event")
        assert not reg.has_hooks("other_event")


# ---------------------------------------------------------------------------
# Integration: hooks fire through @agent decorator
# ---------------------------------------------------------------------------


class TestDecoratorIntegration:
    async def test_before_agent_hook_fires(self, clean_registry: Any) -> None:
        from monet import agent

        hook_called = False

        async def mark_called(ctx: AgentRunContext, meta: AgentMeta) -> None:
            nonlocal hook_called
            hook_called = True
            assert meta["agent_id"] == "test-int"
            assert meta["command"] == "fast"

        with default_hook_registry.hook_scope():
            default_hook_registry.register(
                "before_agent", mark_called, match="test-int"
            )

            @agent(agent_id="test-int", command="fast")
            async def my_agent(task: str) -> str:
                return "done"

            result = await my_agent(make_ctx(task="go", agent_id="test-int"))
            assert result.success
            assert hook_called

    async def test_after_agent_hook_fires(self, clean_registry: Any) -> None:
        from dataclasses import replace

        from monet import agent

        async def enrich_result(result: AgentResult, meta: AgentMeta) -> AgentResult:
            return replace(result, output=f"[post] {result.output}")

        with default_hook_registry.hook_scope():
            default_hook_registry.register(
                "after_agent", enrich_result, match="test-int2"
            )

            @agent(agent_id="test-int2", command="fast")
            async def my_agent(task: str) -> str:
                return "original"

            result = await my_agent(make_ctx(task="go", agent_id="test-int2"))
            assert result.success
            assert result.output == "[post] original"

    async def test_before_hook_failure_prevents_agent(
        self, clean_registry: Any
    ) -> None:
        from monet import agent

        agent_ran = False

        async def block_hook(ctx: AgentRunContext, meta: AgentMeta) -> None:
            raise SemanticError(type="blocked", message="not allowed")

        with default_hook_registry.hook_scope():
            default_hook_registry.register(
                "before_agent", block_hook, match="test-int3"
            )

            @agent(agent_id="test-int3", command="fast")
            async def my_agent(task: str) -> str:
                nonlocal agent_ran
                agent_ran = True
                return "should not run"

            result = await my_agent(make_ctx(task="go", agent_id="test-int3"))
            assert not result.success
            assert not agent_ran
            assert result.has_signal("semantic_error")
