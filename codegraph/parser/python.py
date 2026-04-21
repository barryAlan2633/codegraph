"""Tree-sitter based Python parser (new API, tree-sitter >= 0.22)."""

from __future__ import annotations

import logging
from pathlib import Path

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

from codegraph.parser.base import (
    ParsedCall,
    ParsedImport,
    ParsedSymbol,
    ParseResult,
    SymbolKind,
)

logger = logging.getLogger(__name__)

PY_LANGUAGE = Language(tspython.language())


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _first_child_of_type(node: Node, type_name: str) -> Node | None:
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _children_of_type(node: Node, type_name: str) -> list[Node]:
    return [c for c in node.children if c.type == type_name]


def _extract_docstring(body_node: Node | None, source: bytes) -> str | None:
    """Extract the docstring from a function/class body block."""
    if body_node is None:
        return None
    for child in body_node.children:
        if child.type == "expression_statement":
            expr = child.children[0] if child.children else None
            if expr and expr.type == "string":
                raw = _node_text(expr, source)
                # Strip triple quotes
                for q in ('"""', "'''", '"', "'"):
                    if raw.startswith(q) and raw.endswith(q):
                        return raw[len(q):-len(q)].strip()
                return raw
            break  # first statement wasn't a string
        elif child.type == "comment":
            continue  # skip leading comments
        else:
            break
    return None


def _extract_decorators(node: Node, source: bytes) -> list[str]:
    """Collect decorator names from a decorated_definition or direct node."""
    decorators: list[str] = []
    parent = node.parent
    if parent and parent.type == "decorated_definition":
        for child in parent.children:
            if child.type == "decorator":
                # The decorator text after @
                dec_text = _node_text(child, source).lstrip("@").strip()
                decorators.append(dec_text)
    return decorators


def _build_signature(node: Node, source: bytes, is_async: bool) -> str:
    """Build the function/method signature string."""
    name_node = _first_child_of_type(node, "identifier")
    params_node = _first_child_of_type(node, "parameters")
    ret_node = _first_child_of_type(node, "type")

    prefix = "async def" if is_async else "def"
    name = _node_text(name_node, source) if name_node else "?"
    params = _node_text(params_node, source) if params_node else "()"
    ret = f" -> {_node_text(ret_node, source)}" if ret_node else ""
    return f"{prefix} {name}{params}{ret}"


class PythonParser:
    """Full Python source parser using tree-sitter."""

    @property
    def extensions(self) -> list[str]:
        return [".py", ".pyi"]

    def parse_file(self, file_path: Path, source: bytes | None = None) -> ParseResult:
        if source is None:
            source = Path(file_path).read_bytes()

        parser = Parser(PY_LANGUAGE)
        tree = parser.parse(source)
        result = ParseResult()
        fp = str(file_path)

        self._walk_module(tree.root_node, source, fp, result)
        self._extract_exports(tree.root_node, source, fp, result)
        return result

    # ── Symbol extraction ───────────────────────────────────────

    def _walk_module(
        self,
        root: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        scope: str = "",
    ) -> None:
        """Recursively walk the AST and extract symbols, imports, calls."""
        for node in root.children:
            actual = node
            # Unwrap decorated_definition
            if node.type == "decorated_definition":
                for child in node.children:
                    if child.type in (
                        "function_definition",
                        "class_definition",
                        "async_function_definition",  # tree-sitter >=0.22 variant
                    ):
                        actual = child
                        break

            if actual.type in ("function_definition",):
                self._handle_function(actual, source, fp, result, scope, is_async=False)
            elif actual.type == "async_function_definition":
                self._handle_function(actual, source, fp, result, scope, is_async=True)
            elif actual.type == "class_definition":
                self._handle_class(actual, source, fp, result, scope)
            elif actual.type == "import_statement":
                self._handle_import(actual, source, fp, result)
            elif actual.type == "import_from_statement":
                self._handle_from_import(actual, source, fp, result)
            elif actual.type == "expression_statement":
                # Detect top-level calls
                self._extract_calls_from_node(actual, source, fp, result, scope)

    def _handle_function(
        self,
        node: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        scope: str,
        is_async: bool,
    ) -> None:
        name_node = _first_child_of_type(node, "identifier")
        if name_node is None:
            return
        name = _node_text(name_node, source)
        qname = f"{scope}.{name}" if scope else name
        body = _first_child_of_type(node, "block")
        decorators = _extract_decorators(node, source)

        kind = SymbolKind.METHOD if scope and any(
            c.type == "class_definition"
            for c in (node.parent.children if node.parent else [])
            if c != node
        ) else SymbolKind.FUNCTION
        # Heuristic: if we have a scope containing a class, it's a method
        if scope and "." not in scope:
            # Check parent context -- could be nested function or method
            pass
        # Better heuristic: check if immediate container is a class body
        if node.parent and node.parent.type == "block":
            grandparent = node.parent.parent
            if grandparent and grandparent.type == "class_definition":
                kind = SymbolKind.METHOD
            # Also handle decorated methods
            if grandparent and grandparent.type == "decorated_definition":
                great_gp = grandparent.parent
                if great_gp and great_gp.type == "block":
                    ggp2 = great_gp.parent
                    if ggp2 and ggp2.type == "class_definition":
                        kind = SymbolKind.METHOD

        is_public = not name.startswith("_") or name.startswith("__") and name.endswith("__")

        result.symbols.append(
            ParsedSymbol(
                kind=kind,
                name=name,
                qualified_name=qname,
                signature=_build_signature(node, source, is_async),
                docstring=_extract_docstring(body, source),
                file_path=fp,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                parent=scope or None,
                decorators=decorators,
                is_async=is_async,
                is_public=is_public,
            )
        )

        # Recurse into body for nested definitions and calls
        if body:
            self._walk_module(body, source, fp, result, scope=qname)

    def _handle_class(
        self,
        node: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        scope: str,
    ) -> None:
        name_node = _first_child_of_type(node, "identifier")
        if name_node is None:
            return
        name = _node_text(name_node, source)
        qname = f"{scope}.{name}" if scope else name
        body = _first_child_of_type(node, "block")
        decorators = _extract_decorators(node, source)

        # Build a class signature like "class Foo(Base, Mixin)"
        bases_node = _first_child_of_type(node, "argument_list")
        bases = f"({_node_text(bases_node, source)[1:-1]})" if bases_node else ""
        sig = f"class {name}{bases}"

        result.symbols.append(
            ParsedSymbol(
                kind=SymbolKind.CLASS,
                name=name,
                qualified_name=qname,
                signature=sig,
                docstring=_extract_docstring(body, source),
                file_path=fp,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                parent=scope or None,
                decorators=decorators,
                is_async=False,
                is_public=not name.startswith("_"),
            )
        )

        # Recurse into body
        if body:
            self._walk_module(body, source, fp, result, scope=qname)

    # ── Imports ─────────────────────────────────────────────────

    def _handle_import(
        self, node: Node, source: bytes, fp: str, result: ParseResult
    ) -> None:
        """Handle `import foo` and `import foo as bar`."""
        for child in node.children:
            if child.type == "dotted_name":
                mod = _node_text(child, source)
                result.imports.append(
                    ParsedImport(
                        source=mod,
                        names=[mod.split(".")[-1]],
                        alias=None,
                        file_path=fp,
                        line_number=node.start_point[0] + 1,
                    )
                )
            elif child.type == "aliased_import":
                dotted = _first_child_of_type(child, "dotted_name")
                alias_node = _first_child_of_type(child, "identifier")
                mod = _node_text(dotted, source) if dotted else ""
                alias = _node_text(alias_node, source) if alias_node else None
                result.imports.append(
                    ParsedImport(
                        source=mod,
                        names=[mod.split(".")[-1]],
                        alias=alias,
                        file_path=fp,
                        line_number=node.start_point[0] + 1,
                    )
                )

    def _handle_from_import(
        self, node: Node, source: bytes, fp: str, result: ParseResult
    ) -> None:
        """Handle `from foo import bar, baz` and `from foo import bar as b`."""
        module_parts: list[str] = []
        names: list[str] = []
        alias: str | None = None

        for child in node.children:
            if child.type == "dotted_name":
                if not module_parts:
                    module_parts.append(_node_text(child, source))
                else:
                    names.append(_node_text(child, source))
            elif child.type == "relative_import":
                module_parts.append(_node_text(child, source))
            elif child.type == "import_from_specifier":
                # tree-sitter 0.22+: identifier or aliased
                imported_name_node = child.children[0] if child.children else None
                if imported_name_node:
                    names.append(_node_text(imported_name_node, source))
                if len(child.children) >= 3:
                    alias = _node_text(child.children[2], source)
            elif child.type == "wildcard_import":
                names.append("*")

        source_mod = module_parts[0] if module_parts else ""
        result.imports.append(
            ParsedImport(
                source=source_mod,
                names=names,
                alias=alias,
                file_path=fp,
                line_number=node.start_point[0] + 1,
            )
        )

    # ── Calls ───────────────────────────────────────────────────

    def _extract_calls_from_node(
        self,
        node: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        scope: str,
    ) -> None:
        """Recursively find call expressions in *node*."""
        if node.type == "call":
            func_node = node.children[0] if node.children else None
            if func_node:
                callee = _node_text(func_node, source)
                result.calls.append(
                    ParsedCall(
                        caller=scope or "<module>",
                        callee=callee,
                        file_path=fp,
                        line_number=node.start_point[0] + 1,
                    )
                )
        for child in node.children:
            self._extract_calls_from_node(child, source, fp, result, scope)

    # ── Exports ─────────────────────────────────────────────────

    def _extract_exports(
        self, root: Node, source: bytes, fp: str, result: ParseResult
    ) -> None:
        """Look for __all__ = [...] at module level."""
        for node in root.children:
            if node.type != "expression_statement":
                continue
            for child in node.children:
                if child.type == "assignment":
                    left = child.children[0] if child.children else None
                    if left and _node_text(left, source) == "__all__":
                        # Find the list node
                        for rhs in child.children:
                            if rhs.type == "list":
                                for elem in rhs.children:
                                    if elem.type == "string":
                                        raw = _node_text(elem, source)
                                        for q in ('"""', "'''", '"', "'"):
                                            if raw.startswith(q) and raw.endswith(q):
                                                raw = raw[len(q):-len(q)]
                                                break
                                        result.exports.append(raw)
