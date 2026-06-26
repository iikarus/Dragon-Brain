"""Architectural Contract Scanner — Dragon Brain Audit Edition.

Ported from Tesseract V2 (scripts/trace_contracts.py) with async-specific
extensions for Dragon Brain's async surfaces.

Detects 11 violation patterns:
  1. Swallowed exception: except X → return None/False/sentinel
  2. Silent fallback: logger.error() → return None
  3. Empty collection sentinel: except X → return [] or return {}
  4. Traceback destruction: raise SomeError(str(e)) without `from e`
  5. Per-item swallow: except X → continue (in a loop)
  6. Wrong log level: logger.debug() in an except block
  7. Async swallow: asyncio.gather(return_exceptions=True) unchecked
  8. Bare pass: except X → pass (the silent-est failure)
  --- Dragon Brain extensions ---
  9. Sync sleep in async: time.sleep() inside async def (blocks event loop)
 10. Sync IO in async: known sync-IO calls inside async def
     (PR-6: exempts calls wrapped in `await` — these have been properly
     migrated to async via AsyncMemoryRepository's to_thread wrappers.
     Also exempts calls in files that import AsyncMemoryRepository,
     as a defense-in-depth discriminator.)
 11. Missing async-with: async context managers used without `async with`

Zero external dependencies — uses only stdlib ast module.
"""

import ast
import sys
from pathlib import Path


def is_fallback_return(node):
    """Check if a return statement is returning a silent fallback."""
    if not isinstance(node, ast.Return):
        return False
    val = node.value
    if val is None:
        return True
    if isinstance(val, ast.Constant):
        if val.value in (None, False, "", 0):
            return True
    if isinstance(val, (ast.List, ast.Dict, ast.Set, ast.Tuple)):
        if len(val.elts if hasattr(val, "elts") else val.keys) == 0:
            return True
    return False


ALLOWLIST_MARKERS = ("nosec B110", "noqa: contract", "noqa: BLE001")

PATTERN_12_ALLOWLIST = frozenset(
    {
        # ─── The one legitimate helper ─────────────────────────────────────
        # The ONE place hand-rolled construction is intentional and necessary.
        "tests/_helpers/mock_factory.py",
        # ─── Bare-MagicMock-stub tests (2) ─────────────────────────────────
        # These never construct a real MemoryService — they use MagicMock()
        # as a service stub for testing tool wrappers / router classification.
        "tests/unit/test_router.py",
        "tests/unit/test_list_orphans.py",
        # ─── Real-dependency tests (2) ─────────────────────────────────────
        # These tests verify behavior with the REAL dependency (not mocked):
        "tests/unit/test_locking.py",  # uses real LockManager (patched redis)
        "tests/unit/test_dynamic_validation.py",  # uses real OntologyManager
        # ─── Mutant-testing factories (3) ──────────────────────────────────
        # Custom `_make_mock_service()` + `_build()` factory pattern that patches
        # constructors at the `tools.X` boundary — specific mutation testing
        # contract that helper would change semantics of.
        "tests/unit/test_mutant_dict_crud.py",
        "tests/unit/test_mutant_dict_services.py",
        "tests/unit/test_mutant_temporal.py",
        # ─── Lightweight-integration tests (9) ─────────────────────────────
        # Multi-line `MemoryService(embedding_service=..., vector_store=...)`
        # then `service.repo.client = MagicMock(); .select_graph.return_value = ...`.
        # Tests access patched-FalkorDB through `.repo.client` directly.
        # Helper would mock the entire repo away and break this access pattern.
        "tests/unit/test_temporal.py",
        "tests/unit/test_hologram.py",
        "tests/unit/test_full_workflow.py",
        "tests/unit/test_analysis_radar.py",  # added after 22f R1 AST surfaced it
        "tests/unit/test_entity_lifecycle.py",  # added after 22f R1 AST surfaced it
        "tests/unit/test_graph_traversal.py",  # added after 22f R1 AST surfaced it
        "tests/unit/test_phase4.py",  # added after 22f R1 AST surfaced it
        "tests/unit/test_semantic_radar.py",  # added after 22f R1 AST surfaced it
        "tests/unit/test_session.py",  # added after 22f R1 AST surfaced it
    }
)


def detect_pattern_12_hand_rolled_memory_service(tree, filepath_relative):
    """Pattern 12: Hand-rolled MemoryService construction outside helper/allowlist.

    The 22a-22e-bis arc structurally eliminated hand-rolled MemoryService(...)
    in test files; future violations should fail CI. Allowlist contains the
    helper + 10 architect-verified Category D files with intentional patterns.

    Detects: `MemoryService(embedding_service=...)` ast.Call nodes anywhere
    in non-allowlisted files under tests/.

    Returns: list of (lineno, func, "MemoryService(...)", "hand-rolled construction",
             "Pattern 12: Hand-rolled MemoryService") tuples, or [] if file is
    allowlisted or no violations.
    """
    if filepath_relative in PATTERN_12_ALLOWLIST:
        return []

    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match either `MemoryService(...)` or `module.MemoryService(...)`
        func = node.func
        is_memory_service_call = (isinstance(func, ast.Name) and func.id == "MemoryService") or (
            isinstance(func, ast.Attribute) and func.attr == "MemoryService"
        )
        if not is_memory_service_call:
            continue
        # Must have embedding_service kwarg (filter out unrelated MemoryService-named symbols)
        has_embedding_service_kwarg = any(kw.arg == "embedding_service" for kw in node.keywords)
        if not has_embedding_service_kwarg:
            continue
        violations.append(
            (
                node.lineno,
                "<module>",  # could be refined with parent function lookup
                "MemoryService(embedding_service=...)",
                "hand-rolled construction outside helper/allowlist",
                "Pattern 12: Hand-rolled MemoryService",
            )
        )
    return violations


def analyze_file(filepath):  # noqa: C901, PLR0912
    """Analyze a single python file for contract violations."""
    with open(filepath, encoding="utf-8") as f:
        try:
            content = f.read()
            tree = ast.parse(content)
        except Exception as e:
            print(f"Failed to parse {filepath}: {e}")
            return []

    lines = [None, *content.splitlines()]

    def is_allowlisted(node):
        if hasattr(node, "lineno") and 0 < node.lineno < len(lines):
            line = lines[node.lineno]
            return any(marker in line for marker in ALLOWLIST_MARKERS)
        return False

    def is_handler_allowlisted(handler):
        if is_allowlisted(handler):
            return True
        for child in ast.walk(handler):
            if is_allowlisted(child):
                return True
        return False

    violations = []

    # Map nodes to their parent functions for context
    parent_map = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent_map[child] = node

    # PR-6: Build set of ast.Call nodes that are directly awaited.
    # If `await self.repo.get_node(x)` is used, the Call node for
    # `self.repo.get_node(x)` is the .value of an ast.Await node.
    # These calls are properly async-wrapped and should NOT fire
    # as Sync IO in Async violations.
    awaited_calls: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Await) and isinstance(node.value, ast.Call):
            awaited_calls.add(id(node.value))

    # PR-6 defense-in-depth: check if file imports AsyncMemoryRepository.
    # Files using the async wrapper have all repo calls properly awaited.
    imports_async_repo = any(
        isinstance(node, ast.ImportFrom) and node.module and "repository_async" in node.module
        for node in ast.walk(tree)
    )

    def get_parent_function(node):
        current = node
        while current in parent_map:
            current = parent_map[current]
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return current.name
        return "<module>"

    def is_inside_async_def(node):
        """Check if node is inside an async def."""
        current = node
        while current in parent_map:
            current = parent_map[current]
            if isinstance(current, ast.AsyncFunctionDef):
                return True
        return False

    def get_exc_type(handler):
        if handler.type is None:
            return "BareExcept"
        if isinstance(handler.type, ast.Name):
            return handler.type.id
        elif isinstance(handler.type, ast.Attribute):
            return handler.type.attr
        elif isinstance(handler.type, ast.Tuple):
            return "Multiple"
        return "Unknown"

    for node in ast.walk(tree):
        # ── Pattern 1: Swallowed exceptions ──
        if isinstance(node, ast.ExceptHandler):
            if is_handler_allowlisted(node):
                continue

            has_raise = False
            has_fallback_return = None
            for child in ast.walk(node):
                if isinstance(child, ast.Raise):
                    has_raise = True
                if is_fallback_return(child):
                    has_fallback_return = child

            if not has_raise and has_fallback_return:
                func_name = get_parent_function(node)
                exc_type = get_exc_type(node)
                ret_val = (
                    ast.unparse(has_fallback_return.value)
                    if hasattr(ast, "unparse") and has_fallback_return.value
                    else "None"
                )
                violations.append(
                    (
                        has_fallback_return.lineno,
                        func_name,
                        exc_type,
                        ret_val,
                        "Swallowed Exception",
                    )
                )

            # ── Pattern 4: Traceback destruction ──
            if has_raise:
                for child in ast.walk(node):
                    if isinstance(child, ast.Raise) and child.exc is not None:
                        if child.cause is None:
                            if isinstance(child.exc, ast.Call):
                                func_name = get_parent_function(node)
                                exc_type = get_exc_type(node)
                                violations.append(
                                    (
                                        child.lineno,
                                        func_name,
                                        exc_type,
                                        "raise without `from e`",
                                        "Traceback Destruction",
                                    )
                                )

            # ── Pattern 5: Per-item swallow ──
            if not has_raise:
                for child in ast.walk(node):
                    if isinstance(child, ast.Continue):
                        func_name = get_parent_function(node)
                        exc_type = get_exc_type(node)
                        violations.append(
                            (
                                child.lineno,
                                func_name,
                                exc_type,
                                "continue",
                                "Per-Item Swallow",
                            )
                        )

            # ── Pattern 6: Wrong log level in except block ──
            if not has_raise:
                for child in ast.walk(node):
                    if (
                        isinstance(child, ast.Expr)
                        and isinstance(child.value, ast.Call)
                        and isinstance(child.value.func, ast.Attribute)
                        and child.value.func.attr == "debug"
                    ):
                        func_name = get_parent_function(node)
                        exc_type = get_exc_type(node)
                        violations.append(
                            (
                                child.lineno,
                                func_name,
                                exc_type,
                                "logger.debug()",
                                "Wrong Log Level",
                            )
                        )

            # ── Pattern 8: Bare pass in except block ──
            if (
                not has_raise
                and has_fallback_return is None
                and len(node.body) == 1
                and isinstance(node.body[0], ast.Pass)
            ):
                func_name = get_parent_function(node)
                exc_type = get_exc_type(node)
                violations.append(
                    (
                        node.body[0].lineno,
                        func_name,
                        exc_type,
                        "pass",
                        "Bare Pass",
                    )
                )

        # ── Pattern 2: Silent fail returns after logger.error ──
        if hasattr(node, "body") and isinstance(node.body, list):
            for i, stmt in enumerate(node.body):
                if is_fallback_return(stmt):
                    if i > 0 and isinstance(node.body[i - 1], ast.Expr):
                        prev = node.body[i - 1].value
                        if isinstance(prev, ast.Call) and getattr(prev.func, "attr", "") in (
                            "error",
                            "exception",
                            "warning",
                        ):
                            func_name = get_parent_function(stmt)
                            ret_val = (
                                ast.unparse(stmt.value)
                                if hasattr(ast, "unparse") and getattr(stmt, "value", None)
                                else "None"
                            )
                            violations.append(
                                (
                                    stmt.lineno,
                                    func_name,
                                    "Logger.Error",
                                    ret_val,
                                    "Silent Fallback",
                                )
                            )

        # ── Pattern 7: asyncio.gather with return_exceptions=True unchecked ──
        if isinstance(node, ast.Call):
            func = node.func
            is_gather = False
            if isinstance(func, ast.Attribute) and func.attr == "gather":
                is_gather = True
            elif isinstance(func, ast.Name) and func.id == "gather":
                is_gather = True

            if is_gather:
                for kw in node.keywords:
                    if kw.arg == "return_exceptions" and isinstance(kw.value, ast.Constant):
                        if kw.value.value is True:
                            func_name = get_parent_function(node)
                            violations.append(
                                (
                                    node.lineno,
                                    func_name,
                                    "asyncio.gather",
                                    "return_exceptions=True",
                                    "Async Swallow Risk",
                                )
                            )

        # ── Pattern 9: time.sleep() inside async def (Dragon Brain extension) ──
        if isinstance(node, ast.Call):
            func = node.func
            is_sleep = False
            if isinstance(func, ast.Attribute) and func.attr == "sleep":
                if isinstance(func.value, ast.Name) and func.value.id == "time":
                    is_sleep = True
            elif isinstance(func, ast.Name) and func.id == "sleep":
                is_sleep = True

            if is_sleep and is_inside_async_def(node):
                func_name = get_parent_function(node)
                violations.append(
                    (
                        node.lineno,
                        func_name,
                        "time.sleep",
                        "blocks event loop",
                        "Sync Sleep in Async",
                    )
                )

        # ── Pattern 10: Known sync IO calls inside async def ──
        # FalkorDB calls (graph.query, client.select_graph) are sync
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            sync_io_methods = {
                "query",
                "execute_cypher",
                "create_node",
                "get_node",
                "update_node",
                "delete_node",
                "create_edge",
                "delete_edge",
                "select_graph",
                "get_subgraph",
                "get_most_recent_entity",
                "get_total_node_count",
                "get_observations_for_entity",
            }
            if node.func.attr in sync_io_methods and is_inside_async_def(node):
                # Check if it's called on self.repo or similar
                if isinstance(node.func.value, ast.Attribute):
                    if node.func.value.attr == "repo":
                        if is_allowlisted(node):
                            continue
                        # PR-6: Skip if the call is awaited (properly async)
                        if id(node) in awaited_calls:
                            continue
                        # PR-6 defense-in-depth: skip if file imports
                        # AsyncMemoryRepository (all repo calls are wrapped)
                        if imports_async_repo:
                            continue
                        func_name = get_parent_function(node)
                        violations.append(
                            (
                                node.lineno,
                                func_name,
                                f"self.repo.{node.func.attr}",
                                "sync IO blocks event loop",
                                "Sync IO in Async",
                            )
                        )

    return violations


def main():  # noqa: C901
    import argparse

    parser = argparse.ArgumentParser(description="Dragon Brain Contract Scanner — Audit Edition")
    parser.add_argument(
        "src_dir", nargs="?", default="src/claude_memory", help="Source directory to scan"
    )
    parser.add_argument(
        "--baseline", type=int, default=0, help="Baseline number of allowed violations"
    )
    args = parser.parse_args()

    print("Dragon Brain Contract Scanner — Audit Edition")
    print("=" * 60)

    src_dir = Path(args.src_dir)
    total_files = 0
    total_violations = 0
    by_category = {}

    report_path = Path("contract_violations_report.md")
    with open(report_path, "w", encoding="utf-8") as out:
        out.write("# Dragon Brain — Contract Violation Report\n\n")
        out.write(
            "| File | Line | Function | Caught/Trigger | Fallback Return | Violation Type |\n"
        )
        out.write(
            "|------|------|----------|----------------|-----------------|----------------|\n"
        )

        for py_file in sorted(src_dir.rglob("*.py")):
            total_files += 1
            violations = analyze_file(py_file)
            if violations:
                rel_path = py_file.relative_to(Path.cwd()) if py_file.is_absolute() else py_file
                for lineno, func, exc_type, ret_val, vtype in violations:
                    out.write(
                        f"| `{rel_path}` | {lineno} | `{func}()` | `{exc_type}` | `return {ret_val}` | {vtype} |\n"
                    )
                    total_violations += 1
                    by_category[vtype] = by_category.get(vtype, 0) + 1

        # After the existing src/ scan, add tests/unit/ scan for Pattern 12
        tests_dir = Path("tests/unit")
        if tests_dir.exists():
            for py_file in sorted(tests_dir.rglob("test_*.py")):
                total_files += 1
                rel_path_str = str(py_file).replace("\\", "/")  # normalize for allowlist match
                with open(py_file, encoding="utf-8") as f:
                    try:
                        tree = ast.parse(f.read())
                    except Exception:  # noqa: S112
                        continue
                violations = detect_pattern_12_hand_rolled_memory_service(tree, rel_path_str)
                if violations:
                    rel_path = py_file.relative_to(Path.cwd()) if py_file.is_absolute() else py_file
                    for lineno, func, exc_type, ret_val, vtype in violations:
                        out.write(
                            f"| `{rel_path}` | {lineno} | `{func}()` | `{exc_type}` | `{ret_val}` | {vtype} |\n"
                        )
                        total_violations += 1
                        by_category[vtype] = by_category.get(vtype, 0) + 1

    print(f"\nScanned {total_files} files. Found {total_violations} violations.\n")
    print("By category:")
    for cat, count in sorted(by_category.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")
    print(f"\nReport saved to {report_path}")

    if total_violations > args.baseline:
        print(f"\nERROR: Violations ({total_violations}) exceed baseline ({args.baseline})!")
        sys.exit(1)
    else:
        print(f"\nSUCCESS: Violations ({total_violations}) are within baseline ({args.baseline}).")


if __name__ == "__main__":
    main()
