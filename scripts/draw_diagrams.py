"""Draw the case-study diagrams as SVG, in the same light/dark palette as the charts.

    uv run python scripts/draw_diagrams.py

Hand-placed boxes rather than an auto-layout tool, so each diagram stays small and
readable. Writes docs/images/diagram-{loop,azure}.svg and their -dark variants;
the case study shows the one that matches the reader's GitHub theme.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "docs" / "images"

# Same tokens as algae_sim/autolab/plot_campaign.py THEMES.
THEMES = {
    "light": {"surface": "#fcfcfb", "ink": "#0b0b0b", "secondary": "#52514e",
              "muted": "#898781", "box": "#f0efec", "edge": "#c3c2b7",
              "accent": "#2a78d6", "accent_box": "#e6f0fb"},
    "dark": {"surface": "#1a1a19", "ink": "#f0efec", "secondary": "#c3c2b7",
             "muted": "#898781", "box": "#262624", "edge": "#383835",
             "accent": "#3987e5", "accent_box": "#1d2a3a"},
}
FONT = "-apple-system, 'Helvetica Neue', Arial, sans-serif"


@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float
    title: str
    lines: tuple[str, ...] = ()
    step: str | None = None  # a numbered badge, e.g. "1"
    accent: bool = False


def box(b: Box, t: dict[str, str]) -> str:
    fill = t["accent_box"] if b.accent else t["box"]
    edge = t["accent"] if b.accent else t["edge"]
    out = [f'<rect x="{b.x}" y="{b.y}" width="{b.w}" height="{b.h}" rx="10" '
           f'fill="{fill}" stroke="{edge}" stroke-width="1.2"/>']
    tx = b.x + 16
    if b.step:
        cx, cy = b.x + 22, b.y + 24
        out.append(f'<circle cx="{cx}" cy="{cy}" r="11" fill="{t["accent"]}"/>')
        out.append(f'<text x="{cx}" y="{cy + 4.5}" text-anchor="middle" font-size="12" '
                   f'font-weight="700" fill="{t["surface"]}">{b.step}</text>')
        tx = b.x + 42
    out.append(f'<text x="{tx}" y="{b.y + 29}" font-size="15" font-weight="700" '
               f'fill="{t["ink"]}">{escape(b.title)}</text>')
    for i, line in enumerate(b.lines):
        out.append(f'<text x="{b.x + 16}" y="{b.y + 54 + 19 * i}" font-size="13" '
                   f'fill="{t["secondary"]}">{escape(line)}</text>')
    return "\n".join(out)


def arrow(points: list[tuple[float, float]], t: dict[str, str], *, label: str = "",
          at: tuple[float, float] | None = None, dashed: bool = False,
          anchor: str = "middle") -> str:
    d = "M " + " L ".join(f"{x} {y}" for x, y in points)
    dash = ' stroke-dasharray="5 5"' if dashed else ""
    colour = t["muted"] if dashed else t["accent"]
    marker = "head-muted" if dashed else "head"
    out = [f'<path d="{d}" fill="none" stroke="{colour}" stroke-width="1.8"{dash} '
           f'marker-end="url(#{marker})"/>']
    if label and at:
        out.append(f'<text x="{at[0]}" y="{at[1]}" text-anchor="{anchor}" font-size="12.5" '
                   f'fill="{t["secondary"]}">{escape(label)}</text>')
    return "\n".join(out)


def svg(width: int, height: int, body: str, t: dict[str, str], title: str) -> str:
    defs = (
        f'<marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
        f'fill="{t["accent"]}"/></marker>'
        f'<marker id="head-muted" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" '
        f'fill="{t["muted"]}"/></marker>'
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'width="{width}" height="{height}" font-family="{FONT}" role="img" '
        f'aria-label="{escape(title)}">\n<title>{escape(title)}</title>\n'
        f'<defs>{defs}</defs>\n'
        f'<rect width="{width}" height="{height}" rx="14" fill="{t["surface"]}"/>\n'
        f"{body}\n</svg>\n"
    )


def loop_diagram(t: dict[str, str]) -> str:
    """The closed loop: propose, hold, check, learn; the pipeline below the device."""
    w, gap, y, h = 230, 96, 84, 108
    propose = Box(30, y, w, h, "Propose",
                  ("AutoLab's optimiser picks", "temperature, light and pH"), step="1")
    hold = Box(30 + w + gap, y, w, h, "Hold", ("3 reactors keep the conditions",
                                                "with their own control and safety"),
               step="2", accent=True)
    check = Box(30 + 2 * (w + gap), y, w, h, "Check", ("QC on every replicate;",
                                                        "2 of 3 must pass"), step="3")
    pipe = Box(30 + w + gap, y + h + 70, w, 88, "Data pipeline",
               ("capture → validate → store", "locally or on Azure"))
    body = [box(b, t) for b in (propose, hold, check, pipe)]
    mid = y + h / 2
    for left, right, label in ((propose, hold, "setpoints"), (hold, check, "readings")):
        x0, x1 = left.x + w, right.x
        body.append(arrow([(x0 + 4, mid), (x1 - 6, mid)], t, label=label,
                          at=((x0 + x1) / 2, mid - 10)))
    # 4: back over the top, so no line crosses another.
    top = 34
    body.append(arrow([(check.x + w / 2, y - 2), (check.x + w / 2, top),
                       (propose.x + w / 2, top), (propose.x + w / 2, y - 6)], t))
    cx = hold.x + w / 2
    body.append(f'<rect x="{cx - 50}" y="{top - 13}" width="100" height="26" rx="13" '
                f'fill="{t["surface"]}"/>')
    body.append(f'<circle cx="{cx - 28}" cy="{top}" r="11" fill="{t["accent"]}"/>'
                f'<text x="{cx - 28}" y="{top + 4.5}" text-anchor="middle" font-size="12" '
                f'font-weight="700" fill="{t["surface"]}">4</text>')
    body.append(f'<text x="{cx - 10}" y="{top + 5}" font-size="14" font-weight="700" '
                f'fill="{t["ink"]}">Learn</text>')
    body.append(arrow([(cx, y + h + 2), (cx, pipe.y - 6)], t, dashed=True))
    body.append(f'<text x="{cx + 10}" y="{y + h + 40}" font-size="12.5" '
                f'fill="{t["secondary"]}">measured data</text>')
    width = int(check.x + w + 30)
    return svg(width, int(pipe.y + pipe.h + 28), "\n".join(body), t,
               "The closed loop: AutoLab proposes, the device holds, QC checks,"
               " the optimiser learns")


def azure_diagram(t: dict[str, str]) -> str:
    """The Azure setup: one sign-in, a laptop uploader, Blob for raw, Data Explorer for clean."""
    entra = Box(30, 30, 270, 84, "Microsoft Entra ID", ("one sign-in, cached;", "no keys anywhere"))
    laptop = Box(30, 176, 270, 132, "Laptop", ("simulated device's capture log",
                                                "→ uploader, same parse rules",
                                                "retries after outages"))
    blob = Box(420, 30, 300, 112, "Blob Storage", ("every batch, byte for byte",
                                                    "role: write blobs only"), accent=True)
    adx = Box(420, 196, 300, 132, "Data Explorer", ("tables → dedupe views → report",
                                                     "role: ingest + read only",
                                                     "free cluster"), accent=True)
    body = [box(b, t) for b in (entra, laptop, blob, adx)]
    body.append(arrow([(165, entra.y + entra.h + 2), (165, laptop.y - 6)], t, dashed=True))
    body.append(f'<text x="175" y="{(entra.y + entra.h + laptop.y) / 2 + 4}" font-size="12.5" '
                f'fill="{t["secondary"]}">signs in</text>')
    body.append(arrow([(300, 214), (360, 214), (360, 86), (414, 86)], t,
                      label="raw batches", at=(352, 150), anchor="end"))
    body.append(arrow([(300, 262), (414, 262)], t, label="clean rows", at=(357, 252)))
    body.append(f'<text x="30" y="352" font-size="12.5" fill="{t["muted"]}">'
                "£0: free account with a hard spending limit, plus the free Data Explorer"
                " cluster</text>")
    return svg(750, 372, "\n".join(body), t,
               "Azure setup: one Entra ID sign-in; the laptop uploader sends raw batches to"
               " Blob Storage and clean rows to Data Explorer")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for mode, theme in THEMES.items():
        suffix = "" if mode == "light" else "-dark"
        for name, draw in (("loop", loop_diagram), ("azure", azure_diagram)):
            path = OUT / f"diagram-{name}{suffix}.svg"
            path.write_text(draw(theme))
            print(f"wrote {path.relative_to(OUT.parents[1])}")


if __name__ == "__main__":
    main()
