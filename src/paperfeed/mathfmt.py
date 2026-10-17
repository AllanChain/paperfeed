"""Render the LaTeX fragments in arXiv titles and model prose as Unicode.

arXiv titles arrive with `$...$` shards (`La$_6$Ni$_5$O$_{12}$`), and the chat
model sometimes answers in LaTeX too. Feishu's `lark_md` has no guaranteed LaTeX
support, so math is approximated with Unicode instead: Greek letters, operators,
and sub/superscripts where the character exists. Anything exotic degrades to
readable plain text rather than shipping raw TeX to a chat client.
"""

from __future__ import annotations

GREEK = {
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "epsilon": "ε",
    "varepsilon": "ε",
    "zeta": "ζ",
    "eta": "η",
    "theta": "θ",
    "iota": "ι",
    "kappa": "κ",
    "lambda": "λ",
    "mu": "μ",
    "nu": "ν",
    "xi": "ξ",
    "omicron": "ο",
    "pi": "π",
    "rho": "ρ",
    "sigma": "σ",
    "tau": "τ",
    "upsilon": "υ",
    "phi": "φ",
    "varphi": "φ",
    "chi": "χ",
    "psi": "ψ",
    "omega": "ω",
    "Gamma": "Γ",
    "Delta": "Δ",
    "Theta": "Θ",
    "Lambda": "Λ",
    "Xi": "Ξ",
    "Pi": "Π",
    "Sigma": "Σ",
    "Upsilon": "Υ",
    "Phi": "Φ",
    "Psi": "Ψ",
    "Omega": "Ω",
}

SYMBOLS = {
    "partial": "∂",
    "nabla": "∇",
    "infty": "∞",
    "ell": "ℓ",
    "hbar": "ℏ",
    "pm": "±",
    "mp": "∓",
    "times": "×",
    "cdot": "·",
    "sim": "∼",
    "simeq": "≃",
    "approx": "≈",
    "propto": "∝",
    "leq": "≤",
    "le": "≤",
    "geq": "≥",
    "ge": "≥",
    "neq": "≠",
    "ll": "≪",
    "gg": "≫",
    "to": "→",
    "rightarrow": "→",
    "leftarrow": "←",
    "Rightarrow": "⇒",
    "leftrightarrow": "↔",
    "sum": "∑",
    "prod": "∏",
    "int": "∫",
    "oint": "∮",
    "langle": "⟨",
    "rangle": "⟩",
    "dagger": "†",
    "ddagger": "‡",
    "circ": "∘",
    "prime": "′",
    "ldots": "…",
    "dots": "…",
    "cdots": "⋯",
    "in": "∈",
    "subset": "⊂",
    "cup": "∪",
    "cap": "∩",
    "otimes": "⊗",
    "oplus": "⊕",
    "perp": "⊥",
    "parallel": "∥",
    "Vert": "‖",
    "|": "‖",
}

# Commands that only wrap their argument or size the following delimiter; the
# argument or delimiter itself is what matters.
UNWRAP = {
    "mathrm",
    "text",
    "mathbf",
    "mathit",
    "mathcal",
    "mathsf",
    "operatorname",
    "rm",
    "it",
    "bf",
    "mathnormal",
    "left",
    "right",
    "middle",
    "big",
    "Big",
    "bigg",
    "Bigg",
    "bigl",
    "bigr",
    "Bigl",
    "Bigr",
    "biggl",
    "biggr",
    "Biggl",
    "Biggr",
}

MATHBB = {"Z": "ℤ", "R": "ℝ", "C": "ℂ", "N": "ℕ", "Q": "ℚ", "P": "ℙ"}

SPACING = {",", ";", ":", "!", " ", "quad", "qquad", "thinspace"}

SUBSCRIPTS = dict(
    zip(
        "0123456789+-=()aehijklmnoprstuvx",
        "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ",
        strict=True,
    )
)
SUPERSCRIPTS = dict(
    zip(
        "0123456789+-=()abcdefghijklmnoprstuvwxyz",
        "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ",
        strict=True,
    )
)

# Longest delimiters first so "$$" is not mistaken for an empty inline span.
DELIMITERS = (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$"))


def to_display(text: str) -> str:
    """Replace every math span in `text` with its Unicode approximation."""
    out: list[str] = []
    i = 0
    while i < len(text):
        span = _math_span(text, i)
        if span is None:
            out.append(text[i])
            i += 1
            continue
        start, end, close = span
        out.append(_convert(text[i + len(start) : close]))
        i = close + len(end)
    return "".join(out)


def _math_span(text: str, i: int) -> tuple[str, str, int] | None:
    for start, end in DELIMITERS:
        if text.startswith(start, i):
            close = text.find(end, i + len(start))
            if close != -1:
                return start, end, close
    return None


def _convert(src: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(src):
        char = src[i]
        if char == "\\":
            text, i = _convert_command(src, i)
            out.append(text)
        elif char in "_^":
            if i + 1 < len(src) and src[i + 1] == "\\":
                command, i = _convert_command(src, i + 1)
                out.append(_script_text(command, char))
            else:
                group, i = _read_group(src, i + 1)
                out.append(_script(group, char))
        elif char == "{":
            group, i = _read_group(src, i)
            out.append(_convert(group))
        elif char == "}":
            i += 1
        else:
            # In math mode "-" is a minus sign, not a hyphen.
            out.append("−" if char == "-" else char)
            i += 1
    return "".join(out)


def _convert_command(src: str, i: int) -> tuple[str, int]:
    """Convert the command at `i` (a backslash), returning text and the next index."""
    name, i = _read_command(src, i)
    if name == "mathbb":
        group, i = _read_group(src, i)
        return "".join(MATHBB.get(c, c) for c in group), i
    if name in ("frac", "dfrac", "tfrac"):
        numerator, i = _read_group(src, i)
        denominator, i = _read_group(src, i)
        return f"({_convert(numerator)})/({_convert(denominator)})", i
    if name == "sqrt":
        group, i = _read_group(src, i)
        root = _convert(group)
        return (f"√({root})" if len(root) > 1 else f"√{root}"), i
    if name in UNWRAP and i < len(src) and src[i] == "{":
        group, i = _read_group(src, i)
        return _convert(group), i
    return _command(name), i


def _read_command(src: str, i: int) -> tuple[str, int]:
    start = i + 1
    if start < len(src) and src[start].isalpha():
        end = start
        while end < len(src) and src[end].isalpha():
            end += 1
        return src[start:end], end
    if start < len(src):
        return src[start], start + 1
    return "", start


def _read_group(src: str, i: int) -> tuple[str, int]:
    """Read `{...}` (with nesting) or a single character, starting at `i`."""
    if i >= len(src):
        return "", i
    if src[i] != "{":
        return src[i], i + 1
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i + 1 : j], j + 1
    return src[i + 1 :], len(src)


def _command(name: str) -> str:
    if name in GREEK:
        return GREEK[name]
    if name in SYMBOLS:
        return SYMBOLS[name]
    if name in SPACING:
        return " "
    if name in UNWRAP:
        return ""
    return name


def _script(group: str, kind: str) -> str:
    return _script_text(_convert(group), kind)


def _script_text(converted: str, kind: str) -> str:
    # Unicode minus has no superscript form; ASCII "-" does.
    key = converted.replace("−", "-")
    table = SUBSCRIPTS if kind == "_" else SUPERSCRIPTS
    if key and all(char in table for char in key):
        return "".join(table[char] for char in key)
    if len(converted) > 1:
        return f"{kind}({converted})"
    return f"{kind}{converted}"
