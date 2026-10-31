"""The window's colors and fonts, and the styling for its ttk widgets."""

from tkinter import TclError, ttk

from ossi.drawing import blend

BG = "#15171C"
SIDEBAR = "#1C1F26"
SURFACE = "#262A33"
BORDER = "#343944"
TEXT = "#E8EAEE"
MUTED = "#8D93A0"
# Bright enough to read on the dark background, and for dark text to read on them.
ACCENTS = {
    "Mint": "#4CC9A4",
    "Blue": "#5B9DF9",
    "Violet": "#A78BFA",
    "Pink": "#F472B6",
    "Red": "#F87171",
    "Orange": "#FB923C",
    "Yellow": "#FACC15",
}
DEFAULT_ACCENT = "Mint"
ACCENT = ""
ACCENT_HOVER = ""
ON_ACCENT = ""

FAMILY = "Segoe UI"
FONT = (FAMILY, 11)
SMALL = (FAMILY, 10)
SMALL_BOLD = (FAMILY, 9, "bold")
SIDEBAR_SCROLLBAR = "Sidebar.Vertical.TScrollbar"


def use_accent(name):
    """Choose the accent color. Anything already drawn in the old one needs redrawing."""
    global ACCENT, ACCENT_HOVER, ON_ACCENT
    ACCENT = ACCENTS.get(name, ACCENTS[DEFAULT_ACCENT])
    ACCENT_HOVER = blend(ACCENT, "#FFFFFF", 0.25)
    ON_ACCENT = blend(ACCENT, "#000000", 0.85)


use_accent(DEFAULT_ACCENT)


def recolor(widget, changes):
    """Swap colors on a widget and everything inside it. `changes` maps old colors to new."""
    for option in ("fg", "bg", "highlightcolor", "highlightbackground"):
        try:
            color = str(widget.cget(option))
        except TclError:  # This kind of widget doesn't have that option.
            continue
        if color in changes:
            widget.configure(**{option: changes[color]})
    for child in widget.winfo_children():
        recolor(child, changes)


def style_widgets(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    style.layout(
        "Vertical.TScrollbar",
        [
            (
                "Vertical.Scrollbar.trough",
                {"sticky": "ns", "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]},
            )
        ],
    )
    style.layout(SIDEBAR_SCROLLBAR, style.layout("Vertical.TScrollbar"))
    # The trough matches what's behind it, so that only the thumb shows.
    for name, behind in (("Vertical.TScrollbar", BG), (SIDEBAR_SCROLLBAR, SIDEBAR)):
        style.configure(
            name,
            background=BORDER,
            troughcolor=behind,
            arrowsize=8,
            bordercolor=behind,
            lightcolor=BORDER,
            darkcolor=BORDER,
        )
        style.map(name, background=[("active", MUTED)])

    style.configure(
        "TCombobox",
        fieldbackground=SURFACE,
        background=SURFACE,
        foreground=TEXT,
        arrowcolor=TEXT,
        bordercolor=BORDER,
        lightcolor=SURFACE,
        darkcolor=SURFACE,
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", SURFACE)],
        foreground=[("readonly", TEXT)],
        selectbackground=[("readonly", SURFACE)],
        selectforeground=[("readonly", TEXT)],
        background=[("active", BORDER)],
    )
    root.option_add("*TCombobox*Listbox.background", SURFACE)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", BORDER)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    root.option_add("*TCombobox*Listbox.borderWidth", 0)


def add_scrollbar(parent, text, row, column, pady=0, style="Vertical.TScrollbar"):
    """Attach a scrollbar that only shows when there's something to scroll."""
    scrollbar = ttk.Scrollbar(parent, command=text.yview, style=style)
    # Reserving the column keeps the text from reflowing when the bar hides.
    parent.columnconfigure(column, minsize=10)
    scrollbar.grid(row=row, column=column, sticky="ns", pady=pady)

    def update(first, last):
        scrollbar.set(first, last)
        if float(first) <= 0 and float(last) >= 1:
            scrollbar.grid_remove()
        else:
            scrollbar.grid()

    text.configure(yscrollcommand=update)
