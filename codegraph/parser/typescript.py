"""Tree-sitter based TypeScript/JavaScript parser (new API, tree-sitter >= 0.22)."""

from __future__ import annotations

import logging
from pathlib import Path

import tree_sitter_javascript as tsjs
import tree_sitter_typescript as tsts
from tree_sitter import Language, Node, Parser

from codegraph.parser.base import (
    ParsedCall,
    ParsedImport,
    ParsedSymbol,
    ParseResult,
    SymbolKind,
)

logger = logging.getLogger(__name__)

TS_LANGUAGE = Language(tsts.language_typescript())
TSX_LANGUAGE = Language(tsts.language_tsx())
JS_LANGUAGE = Language(tsjs.language())

_LANG_MAP: dict[str, Language] = {
    ".ts": TS_LANGUAGE,
    ".tsx": TSX_LANGUAGE,
    ".js": JS_LANGUAGE,
    ".jsx": JS_LANGUAGE,
    ".mjs": JS_LANGUAGE,
    ".cjs": JS_LANGUAGE,
}


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _first_child_of_type(node: Node, *type_names: str) -> Node | None:
    for child in node.children:
        if child.type in type_names:
            return child
    return None


def _children_of_type(node: Node, *type_names: str) -> list[Node]:
    return [c for c in node.children if c.type in type_names]


def _extract_jsdoc(node: Node, source: bytes) -> str | None:
    """Extract a JSDoc comment immediately preceding *node*."""
    parent = node.parent
    if parent is None:
        return None
    prev: Node | None = None
    for child in parent.children:
        if child.id == node.id:
            break
        prev = child

    if prev is None:
        return None

    if prev.type == "comment":
        text = _node_text(prev, source).strip()
        if text.startswith("/**"):
            # Strip /** ... */
            text = text[3:]
            if text.endswith("*/"):
                text = text[:-2]
            lines = [line.strip().lstrip("* ").strip() for line in text.split("\n")]
            return "\n".join(line for line in lines if line).strip() or None
    return None


class TypeScriptParser:
    """Handles .ts, .tsx, .js, .jsx, .mjs, .cjs files."""

    @property
    def extensions(self) -> list[str]:
        return [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"]

    def parse_file(self, file_path: Path, source: bytes | None = None) -> ParseResult:
        if source is None:
            source = Path(file_path).read_bytes()

        ext = Path(file_path).suffix.lower()
        lang = _LANG_MAP.get(ext, TS_LANGUAGE)
        parser = Parser(lang)
        tree = parser.parse(source)
        result = ParseResult()
        fp = str(file_path)

        self._walk(tree.root_node, source, fp, result, scope="", is_exported=False)
        return result

    # ── Main walk ───────────────────────────────────────────────

    def _walk(
        self,
        root: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        scope: str,
        is_exported: bool,
    ) -> None:
        for node in root.children:
            exported = is_exported

            # Handle export_statement wrapper
            if node.type == "export_statement":
                self._handle_export(node, source, fp, result, scope)
                continue

            match node.type:
                case "function_declaration":
                    self._handle_function(node, source, fp, result, scope, exported)
                case "generator_function_declaration":
                    self._handle_function(node, source, fp, result, scope, exported)
                case "class_declaration":
                    self._handle_class(node, source, fp, result, scope, exported)
                case "interface_declaration" | "type_alias_declaration":
                    self._handle_interface_or_type(node, source, fp, result, scope, exported)
                case "lexical_declaration" | "variable_declaration":
                    self._handle_variable_declaration(node, source, fp, result, scope, exported)
                case "import_statement":
                    self._handle_import(node, source, fp, result)
                case "expression_statement":
                    self._extract_calls(node, source, fp, result, scope)

    # ── Export statement ────────────────────────────────────────

    def _handle_export(
        self,
        node: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        scope: str,
    ) -> None:
        """Handle `export ...` wrapping declarations, or `export { name }`."""
        # Collect exported names for the result.exports list
        # Check for named exports: export { foo, bar }
        export_clause = _first_child_of_type(node, "export_clause")
        if export_clause:
            for spec in _children_of_type(export_clause, "export_specifier"):
                name_node = spec.children[0] if spec.children else None
                if name_node:
                    result.exports.append(_node_text(name_node, source))
            return

        # Check for `export default`
        is_default = any(
            c.type == "default" or (_node_text(c, source) == "default")
            for c in node.children
            if c.type in ("default", "identifier")
        )

        # Re-walk children that are actual declarations
        for child in node.children:
            match child.type:
                case "function_declaration" | "generator_function_declaration":
                    self._handle_function(child, source, fp, result, scope, exported=True)
                    name_node = _first_child_of_type(child, "identifier")
                    if name_node:
                        result.exports.append(_node_text(name_node, source))
                case "class_declaration":
                    self._handle_class(child, source, fp, result, scope, exported=True)
                    name_node = _first_child_of_type(child, "identifier")
                    if name_node:
                        result.exports.append(_node_text(name_node, source))
                case "interface_declaration" | "type_alias_declaration":
                    self._handle_interface_or_type(child, source, fp, result, scope, exported=True)
                    name_node = _first_child_of_type(child, "identifier", "type_identifier")
                    if name_node:
                        result.exports.append(_node_text(name_node, source))
                case "lexical_declaration" | "variable_declaration":
                    self._handle_variable_declaration(child, source, fp, result, scope, exported=True)
                case "expression_statement":
                    # export default someExpression
                    pass

    # ── Functions ───────────────────────────────────────────────

    def _handle_function(
        self,
        node: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        scope: str,
        exported: bool,
    ) -> None:
        name_node = _first_child_of_type(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, source)
        qname = f"{scope}.{name}" if scope else name
        is_async = any(_node_text(c, source) == "async" for c in node.children if c.type == "async")

        params_node = _first_child_of_type(node, "formal_parameters")
        params = _node_text(params_node, source) if params_node else "()"
        prefix = "async function" if is_async else "function"

        # Return type annotation
        ret = ""
        for i, child in enumerate(node.children):
            if child.type == "type_annotation":
                ret = _node_text(child, source)
                break

        result.symbols.append(
            ParsedSymbol(
                kind=SymbolKind.FUNCTION,
                name=name,
                qualified_name=qname,
                signature=f"{prefix} {name}{params}{ret}",
                docstring=_extract_jsdoc(node, source),
                file_path=fp,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                parent=scope or None,
                is_async=is_async,
                is_public=exported,
            )
        )

        if exported:
            result.exports.append(name)

        body = _first_child_of_type(node, "statement_block")
        if body:
            self._extract_calls(body, source, fp, result, qname)

    # ── Classes ─────────────────────────────────────────────────

    def _handle_class(
        self,
        node: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        scope: str,
        exported: bool,
    ) -> None:
        name_node = _first_child_of_type(node, "identifier", "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, source)
        qname = f"{scope}.{name}" if scope else name

        # Heritage
        heritage = _first_child_of_type(node, "class_heritage")
        ext = f" {_node_text(heritage, source)}" if heritage else ""
        sig = f"class {name}{ext}"

        result.symbols.append(
            ParsedSymbol(
                kind=SymbolKind.CLASS,
                name=name,
                qualified_name=qname,
                signature=sig,
                docstring=_extract_jsdoc(node, source),
                file_path=fp,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                parent=scope or None,
                is_public=exported,
            )
        )

        # Parse class body for methods
        body = _first_child_of_type(node, "class_body")
        if body:
            self._walk_class_body(body, source, fp, result, qname)

    def _walk_class_body(
        self,
        body: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        class_qname: str,
    ) -> None:
        for child in body.children:
            if child.type in ("method_definition", "public_field_definition"):
                self._handle_method(child, source, fp, result, class_qname)

    def _handle_method(
        self,
        node: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        class_qname: str,
    ) -> None:
        name_node = _first_child_of_type(node, "property_identifier", "identifier", "computed_property_name")
        if not name_node:
            return
        name = _node_text(name_node, source)
        qname = f"{class_qname}.{name}"
        is_async = any(_node_text(c, source) == "async" for c in node.children if c.type == "async")

        params_node = _first_child_of_type(node, "formal_parameters")
        params = _node_text(params_node, source) if params_node else ""

        # Check for get/set
        accessor = ""
        for c in node.children:
            if c.type in ("get", "set"):
                accessor = _node_text(c, source) + " "
                break

        prefix = "async " if is_async else ""
        sig = f"{prefix}{accessor}{name}{params}"

        result.symbols.append(
            ParsedSymbol(
                kind=SymbolKind.METHOD,
                name=name,
                qualified_name=qname,
                signature=sig,
                docstring=_extract_jsdoc(node, source),
                file_path=fp,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                parent=class_qname,
                is_async=is_async,
                is_public=not name.startswith("#"),
            )
        )

        fn_body = _first_child_of_type(node, "statement_block")
        if fn_body:
            self._extract_calls(fn_body, source, fp, result, qname)

    # ── Interfaces & Type Aliases ───────────────────────────────

    def _handle_interface_or_type(
        self,
        node: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        scope: str,
        exported: bool,
    ) -> None:
        name_node = _first_child_of_type(node, "identifier", "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, source)
        qname = f"{scope}.{name}" if scope else name

        kind = SymbolKind.INTERFACE if node.type == "interface_declaration" else SymbolKind.TYPE_ALIAS
        keyword = "interface" if kind == SymbolKind.INTERFACE else "type"
        sig = f"{keyword} {name}"

        # Type parameters
        tparams = _first_child_of_type(node, "type_parameters")
        if tparams:
            sig += _node_text(tparams, source)

        # Extends clause
        extends = _first_child_of_type(node, "extends_type_clause", "extends_clause")
        if extends:
            sig += f" {_node_text(extends, source)}"

        result.symbols.append(
            ParsedSymbol(
                kind=kind,
                name=name,
                qualified_name=qname,
                signature=sig,
                docstring=_extract_jsdoc(node, source),
                file_path=fp,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                parent=scope or None,
                is_public=exported,
            )
        )

    # ── Variable declarations (arrow functions, const assignments) ──

    def _handle_variable_declaration(
        self,
        node: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        scope: str,
        exported: bool,
    ) -> None:
        """Handle `const foo = () => {}` and `const bar = function() {}`."""
        for declarator in _children_of_type(node, "variable_declarator"):
            name_node = _first_child_of_type(declarator, "identifier")
            if not name_node:
                continue
            name = _node_text(name_node, source)
            qname = f"{scope}.{name}" if scope else name

            # Find the value (right-hand side)
            value_node: Node | None = None
            saw_eq = False
            for child in declarator.children:
                if child.type == "=":
                    saw_eq = True
                    continue
                if saw_eq:
                    value_node = child
                    break

            if value_node is None:
                continue

            is_arrow = value_node.type == "arrow_function"
            is_func_expr = value_node.type in ("function_expression", "function")
            if not is_arrow and not is_func_expr:
                # Not a function assignment -- skip
                continue

            is_async = any(
                _node_text(c, source) == "async"
                for c in value_node.children
                if c.type == "async"
            )

            params_node = _first_child_of_type(value_node, "formal_parameters")
            # Arrow functions can have a single param (identifier) instead of formal_parameters
            if params_node is None and is_arrow:
                single_param = _first_child_of_type(value_node, "identifier")
                params = f"({_node_text(single_param, source)})" if single_param else "()"
            else:
                params = _node_text(params_node, source) if params_node else "()"

            # Return type
            ret = ""
            type_ann = _first_child_of_type(declarator, "type_annotation")
            if type_ann:
                ret = _node_text(type_ann, source)

            kind_kw = node.children[0]  # const/let/var
            decl_keyword = _node_text(kind_kw, source) if kind_kw else "const"
            prefix = "async " if is_async else ""
            arrow_marker = " =>" if is_arrow else ""
            sig = f"{decl_keyword} {name} = {prefix}{params}{arrow_marker}"

            result.symbols.append(
                ParsedSymbol(
                    kind=SymbolKind.FUNCTION,
                    name=name,
                    qualified_name=qname,
                    signature=sig,
                    docstring=_extract_jsdoc(node, source),
                    file_path=fp,
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    parent=scope or None,
                    is_async=is_async,
                    is_public=exported,
                )
            )

            if exported:
                result.exports.append(name)

            # Extract calls from the function body
            fn_body = _first_child_of_type(value_node, "statement_block")
            if fn_body:
                self._extract_calls(fn_body, source, fp, result, qname)

    # ── Imports ─────────────────────────────────────────────────

    def _handle_import(
        self, node: Node, source: bytes, fp: str, result: ParseResult
    ) -> None:
        """Parse ES module imports: default, named, namespace, side-effect."""
        source_node = _first_child_of_type(node, "string")
        if not source_node:
            return
        mod = _node_text(source_node, source).strip("'\"")

        import_clause = _first_child_of_type(node, "import_clause")
        if import_clause is None:
            # Side-effect import: `import "foo"`
            result.imports.append(
                ParsedImport(
                    source=mod, names=[], alias=None,
                    file_path=fp, line_number=node.start_point[0] + 1,
                )
            )
            return

        names: list[str] = []
        alias: str | None = None

        for child in import_clause.children:
            if child.type == "identifier":
                # Default import: `import Foo from "bar"`
                names.append(_node_text(child, source))
            elif child.type == "named_imports":
                for spec in _children_of_type(child, "import_specifier"):
                    children = spec.children
                    imported = _node_text(children[0], source) if children else ""
                    local_alias = None
                    if len(children) >= 3 and children[1].type == "as":
                        local_alias = _node_text(children[2], source)
                    names.append(imported)
                    if local_alias:
                        alias = local_alias  # last alias wins (rarely >1)
            elif child.type == "namespace_import":
                # `import * as foo from "bar"`
                id_node = _first_child_of_type(child, "identifier")
                names.append("*")
                if id_node:
                    alias = _node_text(id_node, source)

        result.imports.append(
            ParsedImport(
                source=mod,
                names=names,
                alias=alias,
                file_path=fp,
                line_number=node.start_point[0] + 1,
            )
        )

    # ── Call extraction ─────────────────────────────────────────

    def _extract_calls(
        self,
        node: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        scope: str,
    ) -> None:
        """Recursively find call_expression nodes."""
        if node.type == "call_expression":
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
        if node.type == "new_expression":
            # `new Foo()`
            type_node = node.children[1] if len(node.children) > 1 else None
            if type_node:
                callee = _node_text(type_node, source)
                result.calls.append(
                    ParsedCall(
                        caller=scope or "<module>",
                        callee=f"new {callee}",
                        file_path=fp,
                        line_number=node.start_point[0] + 1,
                    )
                )

        for child in node.children:
            self._extract_calls(child, source, fp, result, scope)
