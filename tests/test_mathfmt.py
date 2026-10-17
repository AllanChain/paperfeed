from __future__ import annotations

from paperfeed.mathfmt import to_display


def test_strips_delimiters_and_decodes_greek() -> None:
    assert to_display("$\\nu=2/3$ fractional quantum Hall") == "ν=2/3 fractional quantum Hall"
    assert to_display("$\\Delta$-wave and $\\hbar\\Omega$") == "Δ-wave and ℏΩ"


def test_subscripts_and_superscripts() -> None:
    assert to_display("La$_6$Ni$_5$O$_{12}$") == "La₆Ni₅O₁₂"
    assert to_display("$E=mc^2$") == "E=mc²"
    assert to_display("$T^{-1}$") == "T⁻¹"


def test_unmappable_script_falls_back_to_parentheses() -> None:
    assert to_display("$d_{x^2-y^2}$") == "d_(x²−y²)"
    assert to_display("$T_c$") == "T_c"


def test_scripts_can_take_a_command() -> None:
    assert to_display("$x_\\alpha$") == "x_α"
    assert to_display("$x^\\alpha$") == "x^α"
    assert to_display("$x_\\mathrm{eff}$") == "x_(eff)"


def test_fraction_sqrt_and_blackboard() -> None:
    assert to_display("$\\frac{a}{b}$") == "(a)/(b)"
    assert to_display("$\\sqrt{3}$") == "√3"
    assert to_display("$\\sqrt{x+1}$") == "√(x+1)"
    assert to_display("$\\mathbb{Z}_2$") == "ℤ₂"


def test_wrappers_keep_their_argument() -> None:
    assert to_display("$\\mathrm{DMRG}$") == "DMRG"
    assert to_display("$\\text{eff}$") == "eff"
    assert to_display("$\\mathcal{O}(N)$") == "O(N)"


def test_delimiter_sizing_commands_are_dropped() -> None:
    assert to_display("$\\left(\\frac{a}{b}\\right)$") == "((a)/(b))"
    assert to_display("$\\big\\{x\\big\\}$") == "{x}"


def test_escaped_symbols_and_other_delimiters() -> None:
    assert to_display("$50\\%$ yield") == "50% yield"
    assert to_display("\\(\\alpha\\) and \\[\\beta\\]") == "α and β"
    assert to_display("$$\\psi$$") == "ψ"


def test_plain_text_is_untouched() -> None:
    plain = "Entanglement scaling in a Fermi gas"
    assert to_display(plain) == plain
    assert to_display("$d$-wave") == "d-wave"
    assert to_display("cost: 5$ and rising") == "cost: 5$ and rising"


def test_unknown_command_loses_its_backslash() -> None:
    assert to_display("$\\unknown{x}$") == "unknownx"


def test_empty_math_span_disappears() -> None:
    assert to_display("a$$b") == "ab"
