"""Terminal UI for Alontito - zero dependencies, pure ANSI truecolor.

Presentation only: no game logic, no measurements. The palette and layout
follow the reference mock: deep violet background, lilac accents, pixel logo
in a dotted frame, two-panel home screen, commands in two columns, and a
"ready" footer.

Two rules keep everything aligned:
  1. width is measured on the VISIBLE text (ANSI escapes stripped);
  2. padding is applied only to plain text, never with str.ljust on a painted
     string;
  3. no emoji anywhere: every glyph here is one terminal cell wide, so frames
     stay square in Windows Terminal, conhost and VS Code alike.

home_screen() composes two framed panels of EQUAL height; if a panel's text is
longer, the other one gets blank framed lines instead of a ragged edge.
"""
import ctypes
import os
import re
import shutil
import sys
import time
import unicodedata

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
TOKEN_RE = re.compile(r"\s+|\S+")

# ---------------------------------------------------------------- capabilities


def enable_ansi() -> bool:
    """Turn on VT sequences in the Windows console (no-op elsewhere)."""
    if os.name != "nt":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)          # STD_OUTPUT_HANDLE
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
            return True
    except Exception:
        pass
    return False


ENABLE_ANSI = enable_ansi()

# --------------------------------------------------------------------- palette

RGB = {
    "violet":  (169, 123, 255),
    "lilac":   (204, 176, 255),
    "purple":  (126, 78, 224),
    "deep":    (17, 6, 31),
    "panel":   (46, 20, 76),
    "edge":    (92, 56, 160),
    "text":    (238, 232, 252),
    "muted":   (150, 130, 182),
    "green":   (126, 231, 135),
    "yellow":  (255, 214, 102),
    "red":     (255, 122, 146),
    "cyan":    (120, 220, 255),
    "white":   (255, 255, 255),
}

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"

_BACKDROP = RGB["deep"]
_BG_ON = True
_BG_CODE = "\x1b[48;2;%d;%d;%dm" % _BACKDROP


def set_background(on: bool):
    """Some terminals look better without the painted background."""
    global _BG_ON
    _BG_ON = bool(on)


def _bg_active() -> bool:
    return bool(ENABLE_ANSI and _BG_ON)


def _reset() -> str:
    return RESET + (_BG_CODE if _bg_active() else "")


def fg(color) -> str:
    if not ENABLE_ANSI:
        return ""
    rgb = color if isinstance(color, tuple) else RGB.get(color or "", None)
    if not rgb:
        return ""
    return "\x1b[38;2;%d;%d;%dm" % rgb


def bg(color) -> str:
    if not ENABLE_ANSI:
        return ""
    rgb = color if isinstance(color, tuple) else RGB.get(color or "", None)
    if not rgb:
        return ""
    return "\x1b[48;2;%d;%d;%dm" % rgb


def paint(text, color=None, bold=False, dim=False, on=None) -> str:
    """Colour a string. The background is re-applied after every reset, so
    painted text never punches a hole in the screen background."""
    if not ENABLE_ANSI:
        return text
    prefix = ""
    if on:
        prefix += bg(on)
    if bold:
        prefix += BOLD
    if dim:
        prefix += DIM
    prefix += fg(color)
    if not prefix:
        return text
    return f"{prefix}{text}{_reset()}"


def gradient(text: str, start="violet", end="lilac") -> str:
    """Left-to-right colour ramp, used for the wordmark."""
    if not ENABLE_ANSI:
        return text
    a, b = RGB[start], RGB[end]
    n = max(1, len(text) - 1)
    parts = []
    for i, ch in enumerate(text):
        t = i / n
        rgb = tuple(int(a[k] + (b[k] - a[k]) * t) for k in range(3))
        parts.append(fg(rgb) + ch)
    return "".join(parts) + _reset()


def char_width(ch: str) -> int:
    """Cells taken by one character.

    East-Asian Wide/Fullwidth glyphs (emoji, CJK) take two cells; combining
    marks take none. Ambiguous glyphs are counted as one, which is how Windows
    Terminal, conhost and VS Code draw them by default.
    """
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def width_of(text: str) -> int:
    """Visible width in terminal cells, ANSI escapes excluded."""
    return sum(char_width(ch) for ch in ANSI_RE.sub("", text))


def pad_to(text: str, width: int) -> str:
    """Pad a possibly-coloured string to a visible width."""
    gap = width - width_of(text)
    return text + " " * gap if gap > 0 else text


def clip(text: str, width: int) -> str:
    """Cut a possibly-coloured string to a visible width, closing the colour."""
    if width_of(text) <= width:
        return text
    out, visible, i = "", 0, 0
    while i < len(text):
        m = ANSI_RE.match(text, i)
        if m:
            out += m.group(0)
            i = m.end()
            continue
        w = char_width(text[i])
        if visible + w > width:
            break
        out += text[i]
        visible += w
        i += 1
    return out + (_reset() if ENABLE_ANSI else "")


def term_width(limit: int = 112) -> int:
    try:
        cols = shutil.get_terminal_size((limit, 30)).columns
    except Exception:
        cols = limit
    return max(72, min(limit, cols - 2))


def clear_screen():
    if ENABLE_ANSI:
        sys.stdout.write("\x1b[2J\x1b[H" + (_BG_CODE if _bg_active() else ""))
        sys.stdout.flush()
    else:
        os.system("cls" if os.name == "nt" else "clear")


# ------------------------------------------------------------------- structure

DOT_H = "╌"


def hr(width: int = 0, char: str = "─", color: str = "purple") -> str:
    return paint(char * (width or term_width()), color=color)


def title_bar(version: str, width: int = 0) -> str:
    """Window-style header: prompt glyph, name, decorative controls."""
    width = width or term_width()
    left = "  >_   Алонтито   ·   Open Source Bot"
    right = f"v{version}   ─   ▢   ✕  "
    pad = max(1, width - width_of(left) - width_of(right))
    strip = (paint(left, "lilac", bold=True) + " " * pad +
             paint(right, "muted"))
    band = paint(" " * width, on="panel")
    return band + "\n" + strip + "\n" + band


def _hard_split(text: str, width: int):
    """Split an over-long line by visible width, keeping ANSI sequences whole
    and never cutting a double-width glyph in half."""
    out, cur, visible, i = [], "", 0, 0
    while i < len(text):
        m = ANSI_RE.match(text, i)
        if m:
            cur += m.group(0)
            i = m.end()
            continue
        w = char_width(text[i])
        if visible + w > width and cur:
            out.append(cur)
            cur, visible = "", 0
            continue
        cur += text[i]
        visible += w
        i += 1
    if cur:
        out.append(cur)
    return out


def _wrap(text: str, width: int):
    """Wrap by visible width, keeping the author's spacing intact.

    Lines that already fit are returned untouched. Long lines break at spaces,
    but runs of spaces inside a line survive, so hand-aligned columns (command
    lists, status rows) keep their shape. A single word wider than the panel is
    hard-split, because there is no honest break point left.
    """
    if width <= 0:
        return [text]
    if width_of(text) <= width:
        return [text]
    lines, cur = [], ""
    for tok in TOKEN_RE.findall(text):
        if width_of(cur + tok) <= width:
            cur += tok
            continue
        if tok.isspace():
            if cur.strip():
                lines.append(cur.rstrip())
            cur = ""
            continue
        if cur.strip():
            lines.append(cur.rstrip())
        pieces = _hard_split(tok, width)
        lines.extend(pieces[:-1])
        cur = pieces[-1]
    if cur.strip():
        lines.append(cur.rstrip())
    return lines or [""]


def panel(title: str, lines, width: int, color: str = "border",
          title_color: str = "lilac") -> list:
    """Render a framed panel and return its LINES.

    Body lines are wrapped and clipped to the inner width, so a long word can
    never push the right border out of place. Returned as a list to let the
    caller join two panels of equal height.
    """
    width = max(24, width)
    inner = width - 4                       # between "┆ " and " ┆"
    label = f"  ✦ {title} " if title else ""
    if label:
        rest = max(1, width - 3 - width_of(label))
        top = (paint("╭" + DOT_H, color) + paint(label, title_color, bold=True) +
               paint(DOT_H * rest + "╮", color))
    else:
        top = paint("╭" + DOT_H * (width - 2) + "╮", color)

    body = []
    for line in lines:
        for piece in str(line).split("\n"):
            body.extend([""] if piece == "" else _wrap(piece, inner))

    out = [top]
    for chunk in body:
        out.append(paint("┆ ", color) + pad_to(clip(chunk, inner), inner) +
                   paint(" ┆", color))
    out.append(paint("╰" + DOT_H * (width - 2) + "╯", color))
    return out


def _panel_blank(color: str, width: int) -> str:
    inner = max(0, width - 4)
    return paint("┆ ", color) + " " * inner + paint(" ┆", color)


def box(title: str, lines, width: int = 0, color: str = "border",
        title_color: str = "lilac") -> str:
    """Rounded dotted box with an inline title. Long lines wrap, never cut."""
    width = width or term_width()
    return "\n".join(panel(title, lines, width, color=color,
                           title_color=title_color))


def dotted_frame(lines, width: int, color: str = "border") -> str:
    """Frame made of dotted lines - the pixel-logo container."""
    return "\n".join(panel("", lines, width, color=color))


def two_columns(pairs, width: int, gap: int = 3, cmd_color: str = "violet",
                desc_color: str = "muted") -> str:
    """Render (command, description) pairs in two columns like the mock."""
    if not pairs:
        return ""
    left_n = (len(pairs) + 1) // 2
    left, right = pairs[:left_n], pairs[left_n:]
    cmd_w = max(len(c) for c, _ in pairs) + 1
    col_w = max(18, (width - 2 - gap) // 2)
    gap = max(gap, width - 2 - 2 * col_w)      # spread columns over the panel
    room = max(6, col_w - cmd_w - 2)

    def cell(pair):
        if pair is None:
            return " " * col_w
        cmd, desc = pair
        if len(desc) > room:
            desc = desc[:room - 1] + "…"
        text = (paint(cmd.ljust(cmd_w), cmd_color, bold=True) +
                paint("— ", "muted") + paint(desc, desc_color))
        return pad_to(text, col_w)

    rows = []
    for i in range(left_n):
        a = left[i]
        b = right[i] if i < len(right) else None
        rows.append("  " + cell(a) + " " * gap + cell(b))
    return "\n".join(rows)


def table(headers, rows, color: str = "lilac", width: int = 0,
          header_color: str = "violet") -> str:
    """Simple aligned table; the first column is left, the rest are padded."""
    width = width or term_width()
    rows = [[str(c) for c in row] for row in rows]
    cols = len(headers)
    widths = [len(str(h)) for h in headers]
    for row in rows:
        for i in range(min(cols, len(row))):
            widths[i] = max(widths[i], width_of(row[i]))
    gap = 2
    total = sum(widths) + gap * (cols - 1)
    while total > width and max(widths) > 8:
        widths[widths.index(max(widths))] -= 1
        total = sum(widths) + gap * (cols - 1)

    def line(cells, colour=None, strong=False):
        parts = []
        for i, cell in enumerate(cells[:cols]):
            text = str(cell)
            if width_of(text) > widths[i]:
                text = clip(text, widths[i])
            text = pad_to(text, widths[i])
            parts.append(paint(text, colour, bold=strong) if colour else text)
        return "  ".join(parts).rstrip()

    out = [line([str(h).upper() for h in headers], header_color, strong=True)]
    out.append(paint("·" * min(width, total), "purple", dim=True))
    for row in rows:
        out.append(line(row, color))
    return "\n".join(out)


# ------------------------------------------------------------------ pixel art

# "AL" wordmark: one character per pixel, each pixel printed as two cells so
# the letters read square instead of squeezed. 15 pixels wide + 2 gap + 14 = 30.
_A_PIXELS = [
    "..###..",
    ".##.##.",
    "##...##",
    "##...##",
    "#######",
    "##...##",
    "##...##",
    "##...##",
]
_L_PIXELS = [
    "##.....",
    "##.....",
    "##.....",
    "##.....",
    "##.....",
    "##.....",
    "##.....",
    "######.",
]


def _pixel_rows() -> list:
    rows = []
    for a_row, l_row in zip(_A_PIXELS, _L_PIXELS):
        line = ""
        for px in a_row:
            line += paint("██", "lilac") if px == "#" else "  "
        line += "  "
        for px in l_row:
            line += paint("██", "violet") if px == "#" else "  "
        rows.append(line)
    return rows


def logo_body(width: int) -> list:
    """Content lines for the logo panel (no frame, caller adds it)."""
    inner = width - 4
    rows = _pixel_rows()
    art_w = max(width_of(r) for r in rows)
    left = " " * max(0, (inner - art_w) // 2)

    body = [""]
    # Sparkles sit on the frame's inner corners, not near the art.
    body.append(paint("✦", "lilac") + " " * max(1, inner - 2) +
                paint("✦", "purple"))
    body.extend(left + row for row in rows)
    body.append("")
    body.append(" " * max(0, (inner - 7) // 2) + paint("✦ · ✦", "purple"))
    body.append("")
    return body


def pixel_logo(width: int = 36) -> str:
    """Pixel 'AL' inside a dotted frame with sparkles."""
    return "\n".join(panel("", logo_body(width), width))


def robot_mascot_lines() -> list:
    """No-emoji mascot kept for API compatibility: a small idle robot face."""
    return [
        paint("  ╭──────╮  ", "edge"),
        paint("  │ ◉  ◉ │  ", "lilac"),
        paint("  │  ──  │  ", "lilac"),
        paint("  ╰──┬───╯  ", "edge"),
        paint("   ╭─┴─╮    ", "edge"),
        paint("   │ ▲ │    ", "purple", bold=True),
    ]


# -------------------------------------------------------------------- messages


def ok(text):    print("  " + paint("✔", "green", bold=True) + " " + text)
def err(text):   print("  " + paint("✖", "red", bold=True) + " " + text)
def warn(text):  print("  " + paint("!", "yellow", bold=True) + " " + text)
def info(text):  print("  " + paint("›", "violet", bold=True) + " " + text)
def step(text):  print("  " + paint("·", "purple") + " " + paint(text, "muted"))
def say(text):   print(text)
def blank():     print()


def ready(text: str = "Алонтито готов к работе.") -> str:
    return (paint("  › ", "violet", bold=True) + paint(text, "text") + "  " +
            paint("✦", "lilac") + paint(" ▌", "purple"))


# --------------------------------------------------------------------- widgets


class Timer:
    """Wall-clock for honest reporting: every action prints what it cost."""

    def __enter__(self):
        self.t0 = time.time()
        return self

    def __exit__(self, *exc):
        self.seconds = time.time() - self.t0
        return False


class Spinner:
    """Minimal activity label for calls that take seconds.

    No animation thread: redirected output stays readable.
    """

    def __init__(self, label: str, color: str = "violet"):
        self.label = label
        self.color = color
        self.t0 = 0.0

    def __enter__(self):
        self.t0 = time.time()
        sys.stdout.write("  " + paint("⠿", self.color) + " " + self.label +
                         " … ")
        sys.stdout.flush()
        return self

    def __exit__(self, exc_type, exc, tb):
        dt = time.time() - self.t0
        sys.stdout.write("\r" + " " * (len(self.label) + 14) + "\r")
        if exc_type is None:
            sys.stdout.write("  " + paint("✔", "green", bold=True) + " " +
                             self.label + paint(f"  {dt:.1f}s", "muted") + "\n")
        else:
            sys.stdout.write("  " + paint("✖", "red", bold=True) + " " +
                             self.label + paint(f"  {dt:.1f}s", "muted") + "\n")
        sys.stdout.flush()
        return False


def prompt(text="  › ") -> str:
    """Read a command.

    With piped input there is no echo and no newline after the prompt, so the
    next panel would be glued to "  › ". In that case the prompt is skipped
    entirely: machine-readable output stays square.
    """
    try:
        interactive = sys.stdin is not None and sys.stdin.isatty()
    except Exception:
        interactive = False
    if not interactive:
        return input()
    return input(paint(text, "violet", bold=True))


# --------------------------------------------------------------- home screen

def _status_chip(label: str, on: bool) -> str:
    """A one-cell dot plus label; colour carries the state, so it stays quiet."""
    dot = paint("●" if on else "○", "green" if on else "muted", bold=on)
    return dot + " " + paint(label, "text" if on else "muted")


def info_body(version: str, game: str, facts: int, questions: int,
              skills: int, mcp_active: bool, pet_status: str,
              width: int) -> list:
    inner = width - 4
    rule = paint("─" * max(4, inner - 6), "purple", dim=True)

    chips = (paint("◈ open-source", "lilac") + "   " +
             paint("⌘ MIT", "lilac") + "   " +
             paint("♥ Alontito Dev Team", "lilac"))

    status = (_status_chip(f"MCP · {'вкл' if mcp_active else 'выкл'}", mcp_active)
              + "     " + pet_status)

    return [
        gradient("АЛОНТИТО", "violet", "lilac") +
        paint(f"   v{version}", "purple", bold=True),
        "",
        paint("Опенсорс агент твоей компании", "text") + "   " +
        paint("✦", "lilac"),
        "  " + rule,
        "Алонтито — это больше, чем бот.",
        paint("Личный ИИ-компаньон: помнит факты и решения, подсказывает",
              "muted"),
        paint("и напоминает о прошлых ситуациях. Игра за тебя — никогда.", "muted"),
        "",
        chips,
        "",
        "  " + status,
        "",
        paint("агент   ", "muted") + paint(game, "lilac"),
        paint("память  ", "muted") +
        paint(f"{facts} факт · {questions} вопрос · {skills} навык", "lilac"),
    ]


def home_screen(version: str, game: str, facts: int, questions: int,
                skills: int, commands, mcp_active: bool = False,
                pet_status: str = "", pet_on: bool = False) -> str:
    """The start screen: header, logo panel, info panel, commands, footer."""
    width = term_width()
    parts = [title_bar(version, width), ""]

    if not pet_status:
        pet_status = _status_chip("3D · выкл", bool(pet_on))
    elif not ENABLE_ANSI:
        # A caller may hand us a pre-coloured badge. On a console without VT
        # those bytes would print as visible junk and break the frame, so the
        # status degrades to plain text.
        pet_status = ANSI_RE.sub("", pet_status)
    elif pet_on and not ANSI_RE.search(pet_status):
        pet_status = paint(pet_status, "green")

    if width >= 94:
        logo_w, gap = 36, 3
        info_w = width - logo_w - gap
        left = panel("", logo_body(logo_w), logo_w)
        right = panel("Алонтито", info_body(version, game, facts, questions,
                                            skills, mcp_active, pet_status,
                                            info_w), info_w)
        diff = len(left) - len(right)
        if diff > 0:
            right = (right[:-1] +
                     [_panel_blank("border", info_w)] * diff + right[-1:])
        elif diff < 0:
            left = (left[:-1] + [_panel_blank("border", logo_w)] * (-diff) +
                    left[-1:])
        for l_line, r_line in zip(left, right):
            parts.append(l_line + " " * gap + r_line)
    else:
        parts.extend(panel("", logo_body(width), width))
        parts.append("")
        parts.extend(panel("Алонтито", info_body(version, game, facts,
                                                 questions, skills, mcp_active,
                                                 pet_status, width), width))

    parts.append("")
    parts.append(box("Доступные команды", [
        two_columns(commands, width - 6),
        "",
        paint("свободный текст понимаю тоже: «запусти игру», «тренируйся», "
              "«что видишь», «mcp»", "muted", dim=True),
    ], width=width))
    parts.append("")
    return "\n".join(parts)
