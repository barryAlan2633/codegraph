"""Tree-sitter based Rust parser (new API, tree-sitter >= 0.22)."""

from __future__ import annotations

import logging
from pathlib import Path

import tree_sitter_rust as tsrust
from tree_sitter import Language, Node, Parser

from codegraph.parser.base import (
    ParsedCall,
    ParsedImport,
    ParsedSymbol,
    ParseResult,
    SymbolKind,
)

logger = logging.getLogger(__name__)

RS_LANGUAGE = Language(tsrust.language())


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _first_child_of_type(node: Node, *type_names: str) -> Node | None:
    for child in node.children:
        if child.type in type_names:
            return child
    return None


def _children_of_type(node: Node, *type_names: str) -> list[Node]:
    return [c for c in node.children if c.type in type_names]


def _has_visibility(node: Node) -> bool:
    """Check if the item has a pub/pub(crate)/etc. visibility modifier."""
    for child in node.children:
        if child.type == "visibility_modifier":
            return True
    return False


def _extract_doc_comments(node: Node, source: bytes) -> str | None:
    """Collect consecutive /// or //! doc comments immediately before *node*."""
    lines: list[str] = []
    # In tree-sitter-rust, doc comments appear as siblings preceding the node
    parent = node.parent
    if parent is None:
        return None
    found_self = False
    prev_siblings: list[Node] = []
    for child in parent.children:
        if child.id == node.id:
            found_self = True
            break
        prev_siblings.append(child)

    if not found_self:
        return None

    # Walk backwards through preceding siblings collecting doc comments
    for sib in reversed(prev_siblings):
        if sib.type in ("line_comment", "block_comment"):
            text = _node_text(sib, source)
            if text.startswith("///") or text.startswith("//!"):
                lines.insert(0, text.lstrip("/!").strip())
            else:
                break
        else:
            break

    return "\n".join(lines) if lines else None


def _function_signature(node: Node, source: bytes) -> str:
    """Build a signature string for a function_item."""
    parts: list[str] = []
    if _has_visibility(node):
        parts.append("pub")
    # Check for async
    for child in node.children:
        if child.type == "async":
            parts.append("async")
            break
    parts.append("fn")
    name_node = _first_child_of_type(node, "identifier")
    if name_node:
        parts.append(_node_text(name_node, source))
    # Generic params
    generics = _first_child_of_type(node, "type_parameters")
    if generics:
        parts[-1] += _node_text(generics, source)
    params = _first_child_of_type(node, "parameters")
    if params:
        parts[-1] += _node_text(params, source)
    # Return type
    for i, child in enumerate(node.children):
        if child.type == "->":
            ret_node = node.children[i + 1] if i + 1 < len(node.children) else None
            if ret_node:
                parts.append("->")
                parts.append(_node_text(ret_node, source))
            break
    return " ".join(parts)


class RustParser:
    """Full Rust source parser using tree-sitter."""

    @property
    def extensions(self) -> list[str]:
        return [".rs"]

    def parse_file(self, file_path: Path, source: bytes | None = None) -> ParseResult:
        if source is None:
            source = Path(file_path).read_bytes()

        parser = Parser(RS_LANGUAGE)
        tree = parser.parse(source)
        result = ParseResult()
        fp = str(file_path)

        self._walk(tree.root_node, source, fp, result, scope="")
        return result

    def _walk(
        self,
        root: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        scope: str,
    ) -> None:
        for node in root.children:
            match node.type:
                case "function_item":
                    self._handle_function(node, source, fp, result, scope)
                case "struct_item":
                    self._handle_struct(node, source, fp, result, scope)
                case "enum_item":
                    self._handle_enum(node, source, fp, result, scope)
                case "trait_item":
                    self._handle_trait(node, source, fp, result, scope)
                case "impl_item":
                    self._handle_impl(node, source, fp, result, scope)
                case "use_declaration":
                    self._handle_use(node, source, fp, result)
                case "mod_item":
                    self._handle_mod(node, source, fp, result, scope)
                case "type_item":
                    self._handle_type_alias(node, source, fp, result, scope)

    # ── Functions ───────────────────────────────────────────────

    def _handle_function(
        self,
        node: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        scope: str,
    ) -> None:
        name_node = _first_child_of_type(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, source)
        qname = f"{scope}::{name}" if scope else name
        is_pub = _has_visibility(node)
        is_async = any(c.type == "async" for c in node.children)

        result.symbols.append(
            ParsedSymbol(
                kind=SymbolKind.FUNCTION,
                name=name,
                qualified_name=qname,
                signature=_function_signature(node, source),
                docstring=_extract_doc_comments(node, source),
                file_path=fp,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                parent=scope or None,
                is_async=is_async,
                is_public=is_pub,
            )
        )

        # Extract calls from function body
        body = _first_child_of_type(node, "block")
        if body:
            self._extract_calls(body, source, fp, result, qname)

    # ── Structs ─────────────────────────────────────────────────

    def _handle_struct(
        self,
        node: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        scope: str,
    ) -> None:
        name_node = _first_child_of_type(node, "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, source)
        qname = f"{scope}::{name}" if scope else name

        sig_parts = []
        if _has_visibility(node):
            sig_parts.append("pub")
        sig_parts.append(f"struct {name}")
        generics = _first_child_of_type(node, "type_parameters")
        if generics:
            sig_parts[-1] += _node_text(generics, source)

        result.symbols.append(
            ParsedSymbol(
                kind=SymbolKind.STRUCT,
                name=name,
                qualified_name=qname,
                signature=" ".join(sig_parts),
                docstring=_extract_doc_comments(node, source),
                file_path=fp,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                parent=scope or None,
                is_public=_has_visibility(node),
            )
        )

    # ── Enums ───────────────────────────────────────────────────

    def _handle_enum(
        self,
        node: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        scope: str,
    ) -> None:
        name_node = _first_child_of_type(node, "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, source)
        qname = f"{scope}::{name}" if scope else name

        sig_parts = []
        if _has_visibility(node):
            sig_parts.append("pub")
        sig_parts.append(f"enum {name}")

        result.symbols.append(
            ParsedSymbol(
                kind=SymbolKind.ENUM,
                name=name,
                qualified_name=qname,
                signature=" ".join(sig_parts),
                docstring=_extract_doc_comments(node, source),
                file_path=fp,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                parent=scope or None,
                is_public=_has_visibility(node),
            )
        )

    # ── Traits ──────────────────────────────────────────────────

    def _handle_trait(
        self,
        node: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        scope: str,
    ) -> None:
        name_node = _first_child_of_type(node, "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, source)
        qname = f"{scope}::{name}" if scope else name

        sig_parts = []
        if _has_visibility(node):
            sig_parts.append("pub")
        sig_parts.append(f"trait {name}")

        result.symbols.append(
            ParsedSymbol(
                kind=SymbolKind.TRAIT,
                name=name,
                qualified_name=qname,
                signature=" ".join(sig_parts),
                docstring=_extract_doc_comments(node, source),
                file_path=fp,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                parent=scope or None,
                is_public=_has_visibility(node),
            )
        )

        # Parse methods declared inside the trait
        body = _first_child_of_type(node, "declaration_list")
        if body:
            self._walk_impl_body(body, source, fp, result, qname)

    # ── Impl blocks ─────────────────────────────────────────────

    def _handle_impl(
        self,
        node: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        scope: str,
    ) -> None:
        # Determine the type being implemented
        type_node = _first_child_of_type(node, "type_identifier")
        # For trait impls: `impl Trait for Type`
        # Walk children to find pattern
        type_name = ""
        trait_name = ""
        children = list(node.children)
        for i, child in enumerate(children):
            if child.type == "type_identifier":
                # Could be trait or type depending on whether "for" follows
                candidate = _node_text(child, source)
                # Check if next non-whitespace sibling is "for"
                has_for = any(c.type == "for" for c in children[i + 1:])
                if has_for and not trait_name:
                    trait_name = candidate
                else:
                    type_name = candidate
            elif child.type == "generic_type":
                candidate = _node_text(child, source)
                has_for = any(c.type == "for" for c in children[i + 1:])
                if has_for and not trait_name:
                    trait_name = candidate
                else:
                    type_name = candidate

        if not type_name:
            type_name = "<unknown>"

        impl_scope = f"{scope}::{type_name}" if scope else type_name
        if trait_name:
            impl_scope = f"{impl_scope}::<{trait_name}>"

        body = _first_child_of_type(node, "declaration_list")
        if body:
            self._walk_impl_body(body, source, fp, result, impl_scope)

    def _walk_impl_body(
        self,
        body: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        scope: str,
    ) -> None:
        """Walk the declaration_list of an impl/trait block."""
        for child in body.children:
            if child.type == "function_item":
                name_node = _first_child_of_type(child, "identifier")
                if not name_node:
                    continue
                name = _node_text(name_node, source)
                qname = f"{scope}::{name}"
                is_pub = _has_visibility(child)
                is_async = any(c.type == "async" for c in child.children)

                result.symbols.append(
                    ParsedSymbol(
                        kind=SymbolKind.METHOD,
                        name=name,
                        qualified_name=qname,
                        signature=_function_signature(child, source),
                        docstring=_extract_doc_comments(child, source),
                        file_path=fp,
                        line_start=child.start_point[0] + 1,
                        line_end=child.end_point[0] + 1,
                        parent=scope,
                        is_async=is_async,
                        is_public=is_pub,
                    )
                )

                fn_body = _first_child_of_type(child, "block")
                if fn_body:
                    self._extract_calls(fn_body, source, fp, result, qname)

    # ── Use declarations ────────────────────────────────────────

    def _handle_use(
        self, node: Node, source: bytes, fp: str, result: ParseResult
    ) -> None:
        """Parse `use foo::bar::{Baz, Qux};` into ParsedImport(s)."""
        # Grab the full use path text and parse it
        # The use_declaration contains a scoped_use_list or use_as_clause or path
        text = _node_text(node, source).strip().rstrip(";")
        # Remove leading "use " and possible "pub "
        text = text.removeprefix("pub ").removeprefix("use ")
        self._parse_use_path(text, fp, node.start_point[0] + 1, result)

    def _parse_use_path(
        self, text: str, fp: str, line: int, result: ParseResult
    ) -> None:
        """Recursively parse a use path like `std::collections::{HashMap, HashSet}`."""
        text = text.strip()

        # Handle `foo as bar`
        if " as " in text and "{" not in text:
            parts = text.split(" as ", 1)
            path = parts[0].strip()
            alias = parts[1].strip()
            segments = path.rsplit("::", 1)
            source_mod = segments[0] if len(segments) > 1 else ""
            name = segments[-1]
            result.imports.append(
                ParsedImport(
                    source=source_mod,
                    names=[name],
                    alias=alias,
                    file_path=fp,
                    line_number=line,
                )
            )
            return

        # Handle brace groups: `prefix::{A, B, C}`
        brace_start = text.find("{")
        if brace_start != -1:
            prefix = text[:brace_start].rstrip(":")
            brace_end = text.rfind("}")
            inner = text[brace_start + 1:brace_end] if brace_end != -1 else text[brace_start + 1:]
            # Split on commas, respecting nested braces
            items = self._split_use_items(inner)
            for item in items:
                item = item.strip()
                if not item:
                    continue
                full = f"{prefix}::{item}" if prefix else item
                self._parse_use_path(full, fp, line, result)
            return

        # Simple path: `std::collections::HashMap`
        segments = text.rsplit("::", 1)
        source_mod = segments[0] if len(segments) > 1 else ""
        name = segments[-1] if segments else text
        if name == "*":
            names = ["*"]
        else:
            names = [name]

        result.imports.append(
            ParsedImport(
                source=source_mod,
                names=names,
                alias=None,
                file_path=fp,
                line_number=line,
            )
        )

    @staticmethod
    def _split_use_items(text: str) -> list[str]:
        """Split comma-separated use items, respecting nested braces."""
        items: list[str] = []
        depth = 0
        current: list[str] = []
        for ch in text:
            if ch == "{":
                depth += 1
                current.append(ch)
            elif ch == "}":
                depth -= 1
                current.append(ch)
            elif ch == "," and depth == 0:
                items.append("".join(current))
                current = []
            else:
                current.append(ch)
        if current:
            items.append("".join(current))
        return items

    # ── Mod statements ──────────────────────────────────────────

    def _handle_mod(
        self,
        node: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        scope: str,
    ) -> None:
        name_node = _first_child_of_type(node, "identifier")
        if not name_node:
            return
        name = _node_text(name_node, source)
        qname = f"{scope}::{name}" if scope else name

        result.symbols.append(
            ParsedSymbol(
                kind=SymbolKind.MODULE,
                name=name,
                qualified_name=qname,
                signature=f"mod {name}",
                docstring=_extract_doc_comments(node, source),
                file_path=fp,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                parent=scope or None,
                is_public=_has_visibility(node),
            )
        )

        # If inline mod (has a declaration_list body), recurse
        body = _first_child_of_type(node, "declaration_list")
        if body:
            self._walk(body, source, fp, result, scope=qname)

    # ── Type aliases ────────────────────────────────────────────

    def _handle_type_alias(
        self,
        node: Node,
        source: bytes,
        fp: str,
        result: ParseResult,
        scope: str,
    ) -> None:
        name_node = _first_child_of_type(node, "type_identifier")
        if not name_node:
            return
        name = _node_text(name_node, source)
        qname = f"{scope}::{name}" if scope else name

        result.symbols.append(
            ParsedSymbol(
                kind=SymbolKind.TYPE_ALIAS,
                name=name,
                qualified_name=qname,
                signature=_node_text(node, source).rstrip(";").strip(),
                docstring=_extract_doc_comments(node, source),
                file_path=fp,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                parent=scope or None,
                is_public=_has_visibility(node),
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
                        caller=scope,
                        callee=callee,
                        file_path=fp,
                        line_number=node.start_point[0] + 1,
                    )
                )
        # Also catch method calls: expr.method(args)
        if node.type == "method_call_expression":
            # The method name is in a field_expression child
            field_expr = _first_child_of_type(node, "field_expression")
            if field_expr:
                method_name = _first_child_of_type(field_expr, "field_identifier")
                if method_name:
                    callee = _node_text(method_name, source)
                    result.calls.append(
                        ParsedCall(
                            caller=scope,
                            callee=callee,
                            file_path=fp,
                            line_number=node.start_point[0] + 1,
                        )
                    )

        for child in node.children:
            self._extract_calls(child, source, fp, result, scope)
