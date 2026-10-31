"""Generate the README's animated SVGs: docs/media/hero.svg and docs/media/demo.svg.

Plain SVG + CSS keyframes, no scripts or external fonts, so GitHub renders and animates them inline.
Every line of the demo shares one timeline (same duration, own keyframes), so the loop stays in sync.
Run: python3 docs/media/make_media.py
"""
import html
import os

HERE = os.path.dirname(os.path.abspath(__file__))
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,'Liberation Mono',monospace"
SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"

GREEN, DIM, TEXT, ACCENT, AMBER = "#7ee787", "#8b949e", "#e6edf3", "#79c0ff", "#f0b357"


def esc(s):
    return html.escape(s, quote=False)


# ---------------------------------------------------------------- terminal demo

# (seconds into the loop, indent, [(text, colour), ...]). Output trimmed from a real realme 7 run.
SCRIPT = [
    (0.6, 0, [("$ ", GREEN), ("./cmp go", TEXT)]),
    (1.6, 0, [("== Phone   ", DIM), ("realme RMX2151 · Android 12 · realme UI 3.0", TEXT)]),
    (2.2, 0, [("== Backup  ", DIM), ("335 packages, settings and default apps saved", TEXT)]),
    (2.8, 0, [("== Bloat   ", DIM), ("61 preinstalled apps in 6 groups · 3 apps unused for 30+ days", TEXT)]),
    (3.6, 0, [("? ", AMBER), ("Which preinstalled bloat groups should I remove?", TEXT)]),
    (4.2, 2, [("[x] ", GREEN), ("Ads & tracking (15)     ", TEXT), ("[x] ", GREEN), ("realme duplicates (11)", TEXT)]),
    (4.6, 2, [("[x] ", GREEN), ("Test & engineering (12) ", TEXT), ("[ ] ", DIM), ("realme extras (11)", DIM)]),
    (5.8, 0, [("$ ", GREEN), ("./cmp go --yes --remove \"1 2 3\" --uninstall \"a\"", TEXT)]),
    (6.8, 0, [("Batch 1/8   ", DIM), ("com.glance.internet  com.heytap.mcs  com.oplus.statistics.rom", TEXT)]),
    (7.3, 12, [("✓ checks ok", GREEN)]),
    (7.8, 0, [("Batch 2/8   ", DIM), ("com.heytap.browser  com.heytap.music  com.coloros.video", TEXT)]),
    (8.3, 12, [("✓ checks ok", GREEN)]),
    (8.8, 0, [("Batch 3/8   ", DIM), ("com.heytap.market  ", TEXT), ("suspend (the phone protects it)", AMBER)]),
    (9.3, 12, [("✓ checks ok", GREEN), ("   ...", DIM)]),
    (10.2, 0, [("== Speed   ", DIM), ("animations 0.5× · 90Hz · 1.5 GB of caches cleared", TEXT)]),
    (11.0, 0, [("== Restart ", DIM), ("✓ phone healthy after reboot", GREEN)]),
    (12.0, 0, [("DONE", ACCENT)]),
    (12.6, 2, [("~60% more free RAM", GREEN)]),
    (13.0, 2, [("~20% fewer apps", GREEN)]),
    (13.4, 2, [("~2× faster animations", GREEN)]),
    (14.0, 2, [("undo everything: ", DIM), ("./cmp restore", TEXT)]),
]
LOOP = 19.0  # seconds; the last lines stay up a few seconds before the loop restarts


def demo_svg():
    width, top, line_h = 940, 64, 24
    height = top + line_h * len(SCRIPT) + 30
    css, rows = [], []
    for i, (t, indent, parts) in enumerate(SCRIPT):
        start = t / LOOP * 100
        css.append("@keyframes l%d{0%%,%.2f%%{opacity:0;transform:translateY(4px)}"
                   "%.2f%%,94%%{opacity:1;transform:none}100%%{opacity:0}}"
                   ".l%d{animation:l%d %.1fs ease-out infinite;opacity:0}" % (
                       i, start, min(start + 1.5, 93), i, i, LOOP))
        spans = "".join('<tspan fill="%s">%s</tspan>' % (c, esc(txt)) for txt, c in parts)
        rows.append('<text class="l%d" x="%d" y="%d" xml:space="preserve">%s</text>' % (
            i, 28 + indent * 9, top + i * line_h, spans))
    # blinking cursor after the last line
    css.append("@keyframes blink{50%{opacity:0}}.cur{animation:blink 1s step-end infinite}")
    last_y = top + (len(SCRIPT) - 1) * line_h
    return """<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img"
 aria-label="Terminal replay of cleanyourphone: one question, apps removed in checked batches, then DONE with
 about 60% more free RAM, 20% fewer apps and 2x faster animations">
<style>{css} text{{font:14.5px {mono};}}</style>
<rect width="{w}" height="{h}" rx="12" fill="#0d1117"/>
<rect width="{w}" height="38" rx="12" fill="#161b22"/><rect y="26" width="{w}" height="12" fill="#161b22"/>
<circle cx="22" cy="19" r="6" fill="#ff5f56"/><circle cx="42" cy="19" r="6" fill="#ffbd2e"/>
<circle cx="62" cy="19" r="6" fill="#27c93f"/>
<text x="{mid}" y="24" text-anchor="middle" fill="{dim}" style="font:13px {sans}">cleanyourphone · old phone, one prompt</text>
{rows}
<rect class="cur" x="{cx}" y="{cy}" width="9" height="17" fill="{text}" opacity="0.8"/>
</svg>
""".format(w=width, h=height, css="".join(css), mono=MONO, sans=SANS, mid=width // 2, dim=DIM, text=TEXT,
           rows="\n".join(rows), cx=28 + 2 * 9 + 9 * len("undo everything: ./cmp restore") + 6, cy=last_y - 13)


# ---------------------------------------------------------------- hero banner

STATS = ["~60% more free RAM", "~20% fewer apps", "~2× faster animations"]


def hero_svg():
    w, h = 940, 300
    pills, css = [], []
    x = 0
    widths = [len(s) * 10.2 + 44 for s in STATS]
    gap = 16
    x = (w - (sum(widths) + gap * (len(STATS) - 1))) / 2
    for i, (s, pw) in enumerate(zip(STATS, widths)):
        css.append(".p%d{animation:pop 6s cubic-bezier(.2,.9,.3,1.3) infinite;animation-delay:%.1fs;"
                   "opacity:0;transform-box:fill-box;transform-origin:center}" % (i, 0.6 + i * 0.35))
        pills.append('<g class="p%d"><rect x="%.0f" y="196" width="%.0f" height="44" rx="22" fill="#12261a" '
                     'stroke="#2ea043"/><text x="%.0f" y="224" text-anchor="middle" fill="%s" '
                     'style="font:600 17px %s">%s</text></g>' % (i, x, pw, x + pw / 2, GREEN, SANS, esc(s)))
        x += pw + gap
    return """<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img"
 aria-label="cleanyourphone: make an old Android phone fast again. About 60% more free RAM, 20% fewer apps,
 2x faster animations. No root, one prompt, every step reversible.">
<defs>
 <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0d1117"/><stop offset="1" stop-color="#101a2c"/></linearGradient>
 <radialGradient id="glow" cx=".5" cy=".35" r=".6"><stop offset="0" stop-color="#2ea043" stop-opacity=".22"/><stop offset="1" stop-color="#2ea043" stop-opacity="0"/></radialGradient>
</defs>
<style>
 @keyframes pop{{0%{{opacity:0;transform:scale(.6)}}8%{{opacity:1;transform:scale(1)}}85%{{opacity:1;transform:scale(1)}}100%{{opacity:0;transform:scale(.95)}}}}
 @keyframes breathe{{0%,100%{{opacity:.7}}50%{{opacity:1}}}}
 @keyframes sweep{{0%{{transform:translateX(-120px)}}100%{{transform:translateX(1060px)}}}}
 .glow{{animation:breathe 5s ease-in-out infinite}}
 .sweep{{animation:sweep 6s linear infinite}}
 {css}
</style>
<rect width="{w}" height="{h}" rx="16" fill="url(#bg)"/>
<rect class="glow" width="{w}" height="{h}" rx="16" fill="url(#glow)"/>
<g opacity=".05" class="sweep"><rect x="0" y="0" width="80" height="{h}" fill="#ffffff" transform="skewX(-20)"/></g>
<text x="{mid}" y="98" text-anchor="middle" fill="#ffffff" style="font:800 54px {sans};letter-spacing:-1px">clean<tspan fill="{green}">your</tspan>phone</text>
<text x="{mid}" y="142" text-anchor="middle" fill="#c9d1d9" style="font:400 21px {sans}">Make an old Android phone fast again: less bloat, fewer ads, measured.</text>
<text x="{mid}" y="172" text-anchor="middle" fill="{dim}" style="font:400 15px {sans}">No root · one prompt · every step reversible</text>
{pills}
<text x="{mid}" y="278" text-anchor="middle" fill="{dim}" style="font:400 13px {sans}">Measured on a 2020 realme 7 · approximate, rounded to 5%</text>
</svg>
""".format(w=w, h=h, mid=w // 2, sans=SANS, green=GREEN, dim=DIM, css="".join(css), pills="".join(pills))


if __name__ == "__main__":
    for name, svg in (("hero.svg", hero_svg()), ("demo.svg", demo_svg())):
        with open(os.path.join(HERE, name), "w", encoding="utf-8") as f:
            f.write(svg)
        print("wrote docs/media/%s (%d bytes)" % (name, len(svg.encode())))
