"""Capture Devpost gallery screenshots and a captioned walkthrough video of GridNeighbors.

    pip install playwright imageio-ffmpeg && python -m playwright install chromium
    python tools/capture_media.py [url]          (default: a local run at http://localhost:8501)

Writes media/0N-*.png (3:2, for the Devpost image gallery) and media/gridneighbors-demo.mp4.
Read-only: it never saves a location verification, so the shared database is untouched.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import imageio_ffmpeg
from playwright.sync_api import Page, sync_playwright

URL = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8501"
OUT = Path(__file__).resolve().parent.parent / "media"

# caption bar + a visible cursor (headless recordings don't draw the mouse)
OVERLAY_JS = """
() => {
  if (document.getElementById('demo-caption')) return;
  const cap = document.createElement('div');
  cap.id = 'demo-caption';
  Object.assign(cap.style, {position: 'fixed', left: '50%', bottom: '34px', transform: 'translateX(-50%)',
    maxWidth: '76%', padding: '14px 26px', background: 'rgba(17,24,39,.92)', color: '#fff',
    font: '600 25px/1.35 "Segoe UI", system-ui, sans-serif', borderRadius: '12px', zIndex: 2147483647,
    textAlign: 'center', boxShadow: '0 6px 24px rgba(0,0,0,.35)', opacity: '0', transition: 'opacity .35s',
    pointerEvents: 'none'});
  document.body.appendChild(cap);
  const cur = document.createElement('div');
  cur.id = 'demo-cursor';
  Object.assign(cur.style, {position: 'fixed', width: '22px', height: '22px', borderRadius: '50%',
    background: 'rgba(234,88,12,.45)', border: '2px solid #fff', boxShadow: '0 0 0 2px rgba(234,88,12,.9)',
    zIndex: 2147483647, pointerEvents: 'none', transform: 'translate(-50%,-50%)', left: '-40px', top: '-40px'});
  document.body.appendChild(cur);
  document.addEventListener('mousemove', e => { cur.style.left = e.clientX + 'px'; cur.style.top = e.clientY + 'px'; }, true);
}
"""


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def settle(page: Page, extra_ms: int = 1500) -> None:
    """Wait for Streamlit's rerun to finish (its status widget only exists while running)."""
    page.wait_for_timeout(600)
    page.wait_for_function("() => !document.querySelector('[data-testid=\"stStatusWidget\"]')", timeout=60_000)
    page.wait_for_timeout(extra_ms)


def load(page: Page) -> None:
    page.goto(URL, wait_until="domcontentloaded", timeout=120_000)
    page.wait_for_selector("h1:has-text('GridNeighbors')", timeout=120_000)
    page.wait_for_selector('[data-testid="stDataFrame"]', timeout=60_000)
    settle(page, 6000)  # map tiles + table canvas


def caption(page: Page, text: str | None, hold: float = 0) -> None:
    page.evaluate("""(t) => { const c = document.getElementById('demo-caption'); if (!c) return;
        if (t) { c.textContent = t; c.style.opacity = '1'; } else { c.style.opacity = '0'; } }""", text)
    if hold:
        page.wait_for_timeout(int(hold * 1000))


def scroll_to(page: Page, selector: str, text: str | None = None, block: str = "start") -> None:
    loc = page.locator(selector, has_text=text) if text else page.locator(selector)
    loc.first.evaluate(f"el => el.scrollIntoView({{behavior: 'smooth', block: '{block}'}})")
    page.wait_for_timeout(1400)


def hover(page: Page, selector: str, nth: int = 0) -> None:
    box = page.locator(selector).nth(nth).bounding_box()
    if box:
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=25)


def click_row(page: Page, n: int) -> None:
    """Select row n of the ranked table (the row-marker column holds the selection checkbox)."""
    scroll_to(page, "h1")
    box = page.locator('[data-testid="stDataFrame"]').first.bounding_box()
    x, y = box["x"] + 18, box["y"] + 35 + 35 * (n - 1) + 17
    page.mouse.move(x, y, steps=25)
    page.mouse.click(x, y)
    settle(page, 2500)
    page.wait_for_selector(f"h3:has-text('#{n}:')", timeout=20_000)


def open_brief(page: Page) -> None:
    btn = page.get_by_role("button", name="Generate brief with Gemini")
    btn.evaluate("el => el.scrollIntoView({behavior: 'smooth', block: 'center'})")
    page.wait_for_timeout(1200)
    box = btn.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=20)
    btn.click()
    page.wait_for_selector("text=Why coordinate", timeout=90_000)
    settle(page, 800)


def screenshots(browser) -> None:
    ctx = browser.new_context(viewport={"width": 1500, "height": 1000})  # 3:2, Devpost's recommended ratio
    page = ctx.new_page()
    load(page)
    page.screenshot(path=OUT / "01-map-and-ranked-opportunities.png")
    log("01 map")
    scroll_to(page, "h3", "#1:")
    page.screenshot(path=OUT / "02-top-opportunity-cost-estimate.png")
    log("02 cost")
    open_brief(page)
    scroll_to(page, "h4", "Verify these locations")
    page.screenshot(path=OUT / "03-location-verification-and-gemini-brief.png")
    log("03 verify + brief")
    click_row(page, 3)
    scroll_to(page, "h1")
    page.wait_for_timeout(4000)  # map tiles for the new bounds
    page.screenshot(path=OUT / "04-must-coordinate-pair-thurmond-dam.png")
    log("04 thurmond")
    page.get_by_role("tab", name="Method & data quality").click()
    settle(page, 1500)
    page.screenshot(path=OUT / "05-method-and-data-quality.png")
    log("05 method")
    ctx.close()


def video(browser) -> None:
    raw = OUT / "raw"
    ctx = browser.new_context(viewport={"width": 1600, "height": 900}, record_video_dir=str(raw),
                              record_video_size={"width": 1600, "height": 900})
    page = ctx.new_page()
    t0 = time.monotonic()
    load(page)
    page.evaluate(OVERLAY_JS)
    start = time.monotonic() - t0  # trim the loading screen off the recording
    log(f"video: app ready after {start:.1f}s")

    caption(page, "Neighboring utilities plan grid construction separately, and customers pay for it through their rates.", 5)
    caption(page, "GridNeighbors reads their public plans and finds where projects overlap in place and time.", 5)
    hover(page, '[data-testid="stMetric"]', 0)
    caption(page, "252 planned projects from Dominion Energy South Carolina and Georgia's 10-year plan, parsed from their own filings.", 6)
    hover(page, '[data-testid="stDataFrame"]', 0)
    caption(page, "Blue = Dominion, orange = Georgia. Dashed lines = 55 overlaps within 25 miles, ranked by distance, then timing.", 7)

    caption(page, None)
    scroll_to(page, "h3", "#1:")
    caption(page, "#1: two 230 kV projects near Savannah, 3 miles apart and finishing 5 months apart.", 6)
    hover(page, '[data-testid="stMetric"]', 8)  # "Estimated savings" (after 4 header + 4 detail metrics)
    caption(page, "Sharing laydown yards and deliveries is worth about $200K. Dominion's filing puts $23.8M of this project into its rate base.", 8)

    caption(page, None)
    open_brief(page)
    caption(page, "Gemini turns the computed numbers into a brief a planner can act on. It explains; it never invents numbers.", 8)
    caption(page, None)
    scroll_to(page, "h4", "Verify these locations")
    caption(page, "Planners can confirm or correct any substation. Saved to MongoDB Atlas, it updates everyone's map and rankings.", 8)

    caption(page, None)
    click_row(page, 3)
    scroll_to(page, "h1")
    page.wait_for_timeout(2500)
    caption(page, "Here both utilities are rebuilding lines into the same substation at Thurmond Dam: a must-coordinate pair.", 7)

    caption(page, None)
    page.get_by_role("tab", name="Method & data quality").click()
    settle(page, 800)
    caption(page, "Every location has a confidence grade, and the pipeline reproduces all 6 overlaps in Sperry's answer key.", 7)
    caption(page, "GridNeighbors  ·  built at ShellHacks 2026", 5)
    caption(page, None, 1)

    src = Path(page.video.path())
    ctx.close()  # finalizes the .webm
    mp4 = OUT / "gridneighbors-demo.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-ss", f"{max(0.0, start - 0.2):.2f}",
                    "-i", str(src), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "veryfast",
                    "-movflags", "+faststart", str(mp4)], check=True)
    src.unlink(missing_ok=True)
    log(f"video -> {mp4}")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            screenshots(browser)
            video(browser)
        finally:
            browser.close()
