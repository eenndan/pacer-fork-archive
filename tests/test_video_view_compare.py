"""Real-widget regression tests for the cross-recording compare + layout fixes (f7 phase B).

The PR-#80 tests drove a FAKE VideoView (a recorder that only captured the args to set_compare),
so they never exercised the real VideoView -> PlayerPane -> QMediaPlayer playback and missed three
real-GUI bugs:

  1. enter_cross's set_compare flips the compare TOGGLE button checked, which (signal still live)
     re-entered the app's on_toggled -> same-recording enter() -> set_compare(pane_b_source=None),
     REBUILDING pane B on the PRIMARY recording's source. Pane B then played the wrong (original)
     footage. Fixed by syncing the toggle WITHOUT emitting (VideoView._sync_compare_btn).
  2. The compare panes came up unequal and the splitter handle wouldn't drag. Fixed with an
     entry-time 50/50 split from the splitter's real width + a draggable handle (width / no-collapse
     / opaque resize / per-cell size policy / a video-surface inset).
  3. The pane-B lap-start seek could be dropped by an async-load race on a freshly-created secondary
     (a leftover chapter-0 load satisfying the pending cross-chapter seek). Fixed by gating the
     deferred-seek apply on the genuinely-loaded source FILE (PlayerPane._source_is_chapter).

These tests use the REAL VideoView / PlayerPane with PACER_NO_MEDIA=1 (the production widget tree,
an inert media triplet) where a real decoder isn't needed, plus an OPT-IN real-media test on the
D24 footage when present (skipped otherwise) that proves pane B's actual QMediaPlayer source is the
reference file at the reference lap's S/F. Run: python tests/test_video_view_compare.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Build the panes with the inert media triplet (no decoder/audio device) but the FULL production
# widget tree + signal wiring — set BEFORE importing the studio widgets (read once at construction).
os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import chapters  # noqa: E402
from studio.player_pane import PlayerPane  # noqa: E402
from studio.video_view import VideoView  # noqa: E402


def _cmap(stem: str, n: int = 3, dur: float = 1700.0) -> chapters.ChapterMap:
    """A synthetic n-chapter ChapterMap whose files carry a distinguishing `stem` so a test can tell
    the PRIMARY source from the REFERENCE source by filename."""
    paths = [f"/tmp/{stem}_ch{i}.MP4" for i in range(n)]
    return chapters.ChapterMap(paths, [dur] * n)


# --------------------------------------------------------------- Issue 1 (re-entrancy)
def test_enter_cross_keeps_pane_b_on_reference_source():
    """The Issue-1 bug, reproduced at the VideoView level: with compareToggled wired to a handler
    (as the app wires it to compare.on_toggled), entering cross compare via set_compare(pane_b_source
    = a DIFFERENT recording's ChapterMap) must leave pane B opened on THAT reference source — the
    button-sync must NOT re-enter the handler and rebuild pane B on the primary source."""
    primary = _cmap("PRIMARY")
    reference = _cmap("REFERENCE")
    view = VideoView(primary)

    # Mimic the app: a USER toggle / a same-recording enter rebuilds pane B on the PRIMARY source.
    # If the programmatic button-sync inside set_compare re-emits, THIS fires and clobbers pane B.
    reentries = []

    def on_toggled(on):
        reentries.append(on)
        if on:
            view.set_compare(0, 0, (0.0, 10.0), (0.0, 10.0), "A", "B",
                             [0], None, pane_b_source=None)  # same-recording -> PRIMARY source

    view.compareToggled.connect(on_toggled)

    # Enter the CROSS-recording compare: pane B's source is the reference ChapterMap.
    view.set_compare(0, 0, (0.0, 10.0), (1000.0, 1010.0), "A", "ref B",
                     [0], None, pane_b_source=reference,
                     pane_b_choices=[0], pane_b_choice_labels=["ref B"])

    assert view.secondary is not None
    assert view._secondary_source is reference, (
        f"pane B must be on the REFERENCE source, got {view._secondary_source}")
    assert reentries == [], (
        f"the button-sync must NOT re-enter the toggle handler (got {reentries})")
    # The compare toggle still ends up checked + reads ON (visual sync preserved).
    assert view.compare_btn.isChecked()
    print("test_enter_cross_keeps_pane_b_on_reference_source OK: pane B stayed on reference, "
          "no re-entrant rebuild")


def test_same_recording_compare_still_uses_primary_source():
    """No-regression: a same-recording compare (pane_b_source=None) opens pane B on the PRIMARY
    recording's source, exactly as before. The button-sync change must not disturb this path."""
    primary = _cmap("PRIMARY")
    view = VideoView(primary)
    view.set_compare(0, 1, (0.0, 10.0), (20.0, 30.0), "A", "B", [0, 1], None, pane_b_source=None)
    assert view.secondary is not None
    assert view._secondary_source is primary, view._secondary_source
    assert view.compare_btn.isChecked()
    print("test_same_recording_compare_still_uses_primary_source OK: pane B on the primary source")


# --------------------------------------------------------------- Issue 2 (splitter)
def test_compare_splitter_equal_and_draggable():
    """Issue 2: the two compare panes are EQUAL on entry and the handle is draggable. After
    set_compare the splitter is configured for a real drag (visible handle, no collapse, opaque
    resize), comes up ~50/50, and moveSplitter actually changes sizes() + resizes both cells."""
    view = VideoView(_cmap("PRIMARY"))
    view.resize(800, 400)
    view.show()
    view.set_compare(0, 1, (0.0, 10.0), (20.0, 30.0), "A", "B", [0, 1], None, pane_b_source=None)
    _APP.processEvents()
    sp = view._splitter
    assert sp is not None
    # Draggable handle: a visible width, panes can't collapse over it, live (opaque) resize.
    assert sp.handleWidth() >= 6, sp.handleWidth()
    assert sp.childrenCollapsible() is False
    assert sp.opaqueResize() is True
    # Equal on entry (within a couple px of half — the handle eats a little width).
    sizes = sp.sizes()
    assert len(sizes) == 2 and abs(sizes[0] - sizes[1]) <= 4, sizes
    # A handle drag changes the split AND resizes both cells.
    total = sum(sizes)
    sp.moveSplitter(int(total * 0.30), 1)
    _APP.processEvents()
    moved = sp.sizes()
    assert moved != sizes, (sizes, moved)
    assert view._cell_a.width() < view._cell_b.width(), (view._cell_a.width(), view._cell_b.width())
    print(f"test_compare_splitter_equal_and_draggable OK: entry {sizes} -> drag {moved}")


# --------------------------------------------------------------- Issue 3 (deferred-seek gate)
class _GatePlayer:
    """A QMediaPlayer stand-in that records setSource + setPosition and lets a test report a
    `source()` URL, so PlayerPane._source_is_chapter can be exercised: the deferred cross-chapter
    seek must apply ONLY when the genuinely-loaded source is the pending chapter's file."""
    def __init__(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QMediaPlayer
        self._QUrl = QUrl
        self._status = QMediaPlayer.MediaStatus.LoadedMedia
        self.playing = False
        self.positions = []
        self._source = QUrl()

    def playbackState(self):
        from PySide6.QtMultimedia import QMediaPlayer
        return (QMediaPlayer.PlaybackState.PlayingState if self.playing
                else QMediaPlayer.PlaybackState.PausedState)

    def play(self):
        self.playing = True

    def pause(self):
        self.playing = False

    def setPosition(self, ms):
        self.positions.append(ms)

    def setSource(self, url):
        self._source = url

    def source(self):
        return self._source

    def set_loaded_file(self, path):
        self._source = self._QUrl.fromLocalFile(os.path.abspath(path))

    def mediaStatus(self):
        return self._status


def test_deferred_seek_waits_for_the_right_chapter_file():
    """Issue 3 (the async-seek race): a freshly-created secondary's initial chapter-0 load can land
    its LoadedMedia while a cross-chapter seek to a LATER chapter is already pending (and
    _current_chapter is the target). The OLD index-only gate would apply the seek against the
    chapter-0 file (wrong footage / wrong time). The file-matched gate ignores the chapter-0 load
    and applies the seek only once the TARGET chapter's file is genuinely loaded."""
    from PySide6.QtMultimedia import QMediaPlayer
    cmap = _cmap("REFERENCE", n=3, dur=1700.0)
    pane = PlayerPane(cmap)
    player = _GatePlayer()
    pane.player = player
    # A cross-chapter seek to chapter 1 (~local 200 s) is now pending; _current_chapter is 1.
    pane._current_chapter = 1
    pane._pending = (1, 200.0, False)
    pane._switching = False  # gate already opened (a LoadingMedia consumed it)

    # The chapter-0 file is what is actually loaded at this instant (the leftover initial load).
    player.set_loaded_file(cmap.chapters[0].path)
    pane._on_media_status(QMediaPlayer.MediaStatus.LoadedMedia)
    assert pane._pending == (1, 200.0, False), "seek must NOT apply against the chapter-0 file"
    assert player.positions == [], player.positions

    # Now the genuine TARGET (chapter 1) file is loaded — the seek applies exactly once.
    player.set_loaded_file(cmap.chapters[1].path)
    pane._on_media_status(QMediaPlayer.MediaStatus.LoadedMedia)
    assert pane._pending is None, "seek must apply once the target chapter's file is loaded"
    assert player.positions == [200000], player.positions  # 200.0 s -> ms
    print("test_deferred_seek_waits_for_the_right_chapter_file OK: gate matched the loaded file")


def test_source_is_chapter_headless_null_player_is_true():
    """The file-match gate must stay byte-identical on the PACER_NO_MEDIA headless path: the inert
    null player has no source(), so _source_is_chapter reports True (the deferred seek there is
    synchronous + raceless) and the legacy apply path is unchanged."""
    pane = PlayerPane(_cmap("REFERENCE"))  # built with the null player under PACER_NO_MEDIA=1
    assert pane._source_is_chapter(0) is True
    assert pane._source_is_chapter(2) is True
    print("test_source_is_chapter_headless_null_player_is_true OK")


# --------------------------------------------------------------- Issue 1+3 real media (opt-in)
def _d24():
    """The D24 cross-recording media for the OPT-IN real-media proof. Skipped unless
    PACER_D24_MEDIA=1 is set AND the footage is present: loading two 3-chapter 12 GB recordings
    takes minutes, so the default ctest run (and any machine without the footage) skips it — the
    headless tests above already cover the three fixes' logic; this is the on-hardware proof."""
    if os.environ.get("PACER_D24_MEDIA") != "1":
        return (None, None)
    d = os.path.expanduser("~/Desktop/D24")
    prim = os.path.join(d, "GX010060.MP4")
    ref = os.path.join(d, "GX010062.MP4")
    return (prim, ref) if (os.path.exists(prim) and os.path.exists(ref)) else (None, None)


def test_real_media_pane_b_is_reference_at_lap_start():
    """REAL widgets on REAL media (the whole point — the fakes hid this): build a StudioWindow on
    the D24 primary, load the 0062 reference, enter cross compare via the app path, pump the event
    loop until the async load + deferred seek settle, then assert the SECONDARY pane's actual
    QMediaPlayer source is a REFERENCE file (stem != GX010060) resolved to the reference lap's
    CHAPTER + a global time ≈ the reference lap-window start. Skipped when D24 isn't present."""
    import time

    prim_path, ref_path = _d24()
    if prim_path is None:
        print("test_real_media_pane_b_is_reference_at_lap_start SKIPPED "
              "(set PACER_D24_MEDIA=1 with ~/Desktop/D24 footage to run the on-hardware proof)")
        return
    # The full StudioWindow needs a real decoder for this proof, so DROP the headless flag for it.
    os.environ.pop("PACER_NO_MEDIA", None)
    from studio.app import StudioWindow
    from studio.session import Session  # noqa: F401  (ensures the studio import graph is built)

    prim = chapters.discover_siblings(prim_path)
    ref = chapters.discover_siblings(ref_path)

    def pump(secs):
        end = time.time() + secs
        while time.time() < end:
            _APP.processEvents()
            time.sleep(0.01)

    win = StudioWindow(prim)
    win.resize(1440, 900)
    win.show()
    pump(3.0)
    reason = win.session.load_reference(ref)
    assert reason is None, f"reference refused: {reason}"
    win._update_reference_status()
    assert win.compare.enter_cross() is True

    ref_lap = win.session.reference_lap_id()
    win_b = win.session.reference_session().lap_window(ref_lap)
    sec = win.video.secondary
    assert sec is not None
    # Pump until the deferred seek lands (bounded — not an unbounded sleep).
    landed = False
    for _ in range(25):
        pump(1.0)
        if sec._pending is None and sec.current_global_time() > 1.0:
            landed = True
            break
    assert landed, "the reference pane never resolved its lap-start seek"
    src = os.path.basename(sec.player.source().toLocalFile())
    stem = os.path.splitext(src)[0]
    assert "0062" in stem and "0060" not in stem, f"pane B is not a reference file: {src}"
    gl = sec.current_global_time()
    assert abs(gl - win_b[0]) < 1.0, (gl, win_b[0])
    # Restore the flag for any subsequent tests in the module.
    os.environ["PACER_NO_MEDIA"] = "1"
    print(f"test_real_media_pane_b_is_reference_at_lap_start OK: pane B={src} "
          f"global={gl:.2f}s vs lap start {win_b[0]:.2f}s")


def _run_all():
    test_enter_cross_keeps_pane_b_on_reference_source()
    test_same_recording_compare_still_uses_primary_source()
    test_compare_splitter_equal_and_draggable()
    test_deferred_seek_waits_for_the_right_chapter_file()
    test_source_is_chapter_headless_null_player_is_true()
    test_real_media_pane_b_is_reference_at_lap_start()
    print("ALL VIDEO-VIEW COMPARE TESTS PASSED")


if __name__ == "__main__":
    _run_all()
