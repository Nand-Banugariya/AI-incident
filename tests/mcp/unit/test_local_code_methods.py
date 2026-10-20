"""find_method: Python via ast, brace languages via the regex scanner."""

import pytest

from mcp_common.errors import InvalidArgument, NotFound
from mcp_servers.local_code.methods import find_method
from tests.mcp.conftest import CS_SOURCE, JS_SOURCE, PY_SOURCE


def lines_of(source: str, match) -> list[str]:
    return source.splitlines()[match.start_line - 1:match.end_line]


def test_python_module_function():
    match, parser = find_method(PY_SOURCE, "helper", ".py")
    assert parser == "python-ast"
    assert (match.name, match.start_line, match.end_line) == ("helper", 5, 6)


def test_python_class_returns_whole_class():
    match, _ = find_method(PY_SOURCE, "AuditService", ".py")
    assert (match.start_line, match.end_line) == (22, 24)


def test_python_async_method():
    match, _ = find_method(PY_SOURCE, "OrderService.save", ".py")
    assert lines_of(PY_SOURCE, match)[0].strip() == "async def save(self, order):"


def test_python_syntax_error_falls_back_to_regex():
    source = "def broken(:\n    pass\n\ndef good():\n    return 1\n\nx = 1\n"
    match, parser = find_method(source, "good", ".py")
    assert parser == "regex"
    assert (match.start_line, match.end_line) == (4, 5)


def test_csharp_constructor_with_base_call():
    match, _ = find_method(CS_SOURCE, "Billing.Billing", ".cs")
    assert lines_of(CS_SOURCE, match)[0].strip() == "public Billing(IRepo repo) : base(repo)"
    assert lines_of(CS_SOURCE, match)[-1].strip() == "}"


def test_csharp_expression_bodied_member():
    match, _ = find_method(CS_SOURCE, "Count", ".cs")
    assert match.start_line == match.end_line
    assert "=> _items.Count;" in lines_of(CS_SOURCE, match)[0]


def test_csharp_braces_in_strings_and_comments_are_ignored():
    match, _ = find_method(CS_SOURCE, "CalculateTotal", ".cs")
    body = lines_of(CS_SOURCE, match)
    assert body[-1].strip() == "}"
    assert body[-2].strip() == "return 0m;"


def test_csharp_single_line_method():
    match, _ = find_method(CS_SOURCE, "Sum", ".cs")
    assert match.start_line == match.end_line


def test_csharp_interface_declaration_without_body():
    match, _ = find_method(CS_SOURCE, "IBilling.Save", ".cs")
    assert lines_of(CS_SOURCE, match) == ["        Task Save();"]


def test_calls_are_not_definitions():
    # `Sum(orderId)` is called inside CalculateTotal; only the definition may match
    match, _ = find_method(CS_SOURCE, "Sum", ".cs")
    assert "private Task<decimal> Sum" in lines_of(CS_SOURCE, match)[0]


def test_javascript_function_with_template_literal_brace():
    match, _ = find_method(JS_SOURCE, "handle", ".js")
    assert (match.start_line, match.end_line) == (1, 4)


def test_go_method_with_receiver():
    source = "package x\n\nfunc (s *Server) Handle(w http.ResponseWriter) error {\n\treturn nil\n}\n"
    match, _ = find_method(source, "Handle", ".go")
    assert (match.start_line, match.end_line) == (3, 5)


def test_qualified_name_with_wrong_class_is_not_found():
    with pytest.raises(NotFound):
        find_method(CS_SOURCE, "Other.CalculateTotal", ".cs")


@pytest.mark.parametrize("name", ["", "a b", "x;rm", "1abc", "a" * 201])
def test_invalid_names(name):
    with pytest.raises(InvalidArgument):
        find_method(PY_SOURCE, name, ".py")


def test_unsupported_extension():
    with pytest.raises(InvalidArgument):
        find_method("x", "x", ".md")
