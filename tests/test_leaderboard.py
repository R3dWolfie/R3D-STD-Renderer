"""Map render leaderboard (render/leaderboard.py): the best-per-player DB
query + top-N ordering, sparse-map handling (0/1/2/N others), rank
computation, NEW-BEST / NEW-#1 detection, and the Discord-avatar cache +
graceful fallback (no-token path returns None → the procedural chip)."""
from __future__ import annotations

import os
import sqlite3
import tempfile

from osu_std_renderer.render.leaderboard import (
    BoardData, LeaderboardEntry, avatar_cache_path, build_board, compute_rank,
    fetch_discord_avatar, is_fetchable_id, query_leaderboard,
    resolve_avatar_bytes,
)


# --- DB fixture (real render-table shape) -------------------------------------------

def _make_db(rows):
    """rows: (replay_md5, beatmap_md5, player_name, discord_user_id, score,
    accuracy, grade, max_combo, mods_str, mods, c300, c100, c50, cmiss,
    deleted)."""
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE renders (
        replay_md5 TEXT, beatmap_md5 TEXT, player_name TEXT,
        discord_user_id TEXT, score INTEGER, accuracy REAL, grade TEXT,
        max_combo INTEGER, mods_str TEXT, mods INTEGER, count_300 INTEGER,
        count_100 INTEGER, count_50 INTEGER, count_miss INTEGER,
        deleted INTEGER DEFAULT 0)""")
    con.executemany("""INSERT INTO renders (replay_md5, beatmap_md5,
        player_name, discord_user_id, score, accuracy, grade, max_combo,
        mods_str, mods, count_300, count_100, count_50, count_miss, deleted)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    con.commit()
    con.close()
    return path


def _row(replay, player, did, score, deleted=0, md5="MAP", grade="A", acc=95.0):
    return (replay, md5, player, did, score, acc, grade, 500, "NM", 0,
            400, 5, 2, 1, deleted)


# --- query: best-per-player + ordering ----------------------------------------------

def test_query_best_per_player_dedups_and_orders():
    path = _make_db([
        _row("a1", "R3D", "111", 56717),
        _row("a2", "R3D", "111", 4527),          # R3D's lower render — dropped
        _row("b1", "Mayulody", "222", 6888),
        _row("c1", "VI0", "osu_30196342", 2086),
        _row("z1", "R3D", "111", 900, md5="OTHER"),   # different map — excluded
    ])
    try:
        lb = query_leaderboard(path, "MAP")
        # one row per distinct player, highest score kept, score DESC
        assert [e["player_name"] for e in lb] == ["R3D", "Mayulody", "VI0"]
        assert [e["score"] for e in lb] == [56717, 6888, 2086]
        # the best (not the lower) R3D render survived the GROUP BY
        assert lb[0]["score"] == 56717
        # columns the card needs are present
        assert lb[2]["discord_user_id"] == "osu_30196342"
    finally:
        os.unlink(path)


def test_query_filters_deleted_and_excludes_current_replay():
    path = _make_db([
        _row("keep", "R3D", "111", 50000),
        _row("del", "Ghost", "333", 99999, deleted=1),   # soft-deleted → gone
        _row("cur", "R3D", "111", 70000),                # current replay row
    ])
    try:
        # deleted rows never appear
        assert all(e["player_name"] != "Ghost" for e in query_leaderboard(path, "MAP"))
        # excluding the current replay drops R3D's 70000 → best becomes 50000
        lb = query_leaderboard(path, "MAP", exclude_replay_md5="cur")
        assert lb[0]["player_name"] == "R3D" and lb[0]["score"] == 50000
    finally:
        os.unlink(path)


def test_query_fail_soft():
    # blank md5, missing DB → [] (board degrades to featured only), no raise
    assert query_leaderboard("/no/such.sqlite", "MAP") == []
    assert query_leaderboard("/no/such.sqlite", "") == []


def test_query_limit_top_n():
    rows = [_row(f"r{i}", f"P{i}", str(1000 + i), 10000 - i * 10)
            for i in range(20)]
    path = _make_db(rows)
    try:
        lb = query_leaderboard(path, "MAP", limit=5)
        assert len(lb) == 5
        # the top 5 by score, in order
        assert [e["player_name"] for e in lb] == ["P0", "P1", "P2", "P3", "P4"]
    finally:
        os.unlink(path)


# --- rank computation ---------------------------------------------------------------

def test_compute_rank():
    assert compute_rank([], 500) == 1                       # solo → #1
    assert compute_rank([900, 800, 700], 750) == 3          # 2 above → #3
    assert compute_rank([900, 800], 950) == 1               # tops → #1
    assert compute_rank([900, 800], 100) == 3               # bottom → #3
    # ties: the current play ranks ABOVE an equal other (strict >)
    assert compute_rank([500, 500], 500) == 1


# --- board assembly + sparse handling -----------------------------------------------

def _mkrows(pairs, md5="MAP"):
    """pairs: [(player, score)] → query-shaped dicts."""
    return [{"player_name": p, "discord_user_id": None, "score": s,
             "accuracy": 95.0, "grade": "A", "max_combo": 500,
             "mods_str": "NM", "mods": 0, "count_300": 400, "count_100": 5,
             "count_50": 2, "count_miss": 1, "replay_md5": f"r_{p}"}
            for p, s in pairs]


def test_board_solo_when_no_others():
    # only the current play exists → empty flanks, rank #1, no false NEW #1
    b = build_board([], "R3D", 500000)
    assert isinstance(b, BoardData)
    assert b.left == [] and b.right == []
    assert b.rank == 1 and b.n_players == 1 and b.moment is None


def test_board_one_other_below():
    b = build_board(_mkrows([("Foo", 100)]), "R3D", 500000, prev_best_score=None)
    assert b.rank == 1 and b.n_players == 2
    assert [e.player_name for e in b.right] == ["Foo"]
    assert b.left == []
    # tops a populated board → NEW #1
    assert b.moment == "NEW #1"
    assert b.right[0].rank == 2                    # absolute board rank


def test_board_two_others_straddle_current():
    rows = _mkrows([("Hi", 900000), ("Lo", 100000)])
    b = build_board(rows, "R3D", 500000)
    assert b.rank == 2 and b.n_players == 3
    assert [e.player_name for e in b.left] == ["Hi"]     # ranked above
    assert [e.player_name for e in b.right] == ["Lo"]    # ranked below
    assert b.left[0].rank == 1 and b.right[0].rank == 3
    assert b.moment is None                              # not #1, no prev best


def test_board_excludes_current_player_from_flanks():
    # the current player's own other renders never appear as a rival card
    rows = _mkrows([("R3D", 999999), ("Other", 200000)])
    b = build_board(rows, "r3d", 500000)                 # case-insensitive
    names = [e.player_name for e in b.left + b.right]
    assert "R3D" not in names and names == ["Other"]
    assert b.rank == 1                                   # no *other* beats it


def test_board_centres_on_current_and_caps_per_side():
    # 5 above + 5 below, cap 2 per side → the nearest neighbours only
    above = [("A%d" % i, 900000 - i) for i in range(5)]   # 900000..899996
    below = [("B%d" % i, 100000 - i) for i in range(5)]
    b = build_board(_mkrows(above + below), "R3D", 500000, max_per_side=2)
    assert b.rank == 6                                    # 5 others above
    # left holds the 2 nearest-above, ascending rank (closest score rightmost)
    assert [e.rank for e in b.left] == [4, 5]
    assert [e.rank for e in b.right] == [7, 8]            # 2 nearest-below


def test_board_new_best_flourish():
    # beats own previous best but not #1 → NEW BEST
    rows = _mkrows([("King", 999999)])
    b = build_board(rows, "R3D", 500000, prev_best_score=400000)
    assert b.rank == 2 and b.moment == "NEW BEST"
    # did NOT beat own previous best → no flourish
    b2 = build_board(rows, "R3D", 500000, prev_best_score=600000)
    assert b2.moment is None


def test_entry_shape():
    rows = _mkrows([("Foo", 123456)])
    e = build_board(rows, "R3D", 999999).right[0]
    assert isinstance(e, LeaderboardEntry)
    assert e.counts == (400, 5, 2, 1) and e.accuracy == 95.0


# --- avatar cache + graceful fallback -----------------------------------------------

def test_is_fetchable_id():
    assert is_fetchable_id("111166802121281536") is True     # real snowflake
    assert is_fetchable_id("osu_30196342") is False          # placeholder
    assert is_fetchable_id("") is False
    assert is_fetchable_id(None) is False


def test_resolve_avatar_no_token_returns_none(monkeypatch=None):
    # no token in env → no fetch → None (caller draws the procedural chip)
    import osu_std_renderer.render.leaderboard as lb
    old_tok = os.environ.pop("DISCORD_BOT_TOKEN", None)
    old_tok2 = os.environ.pop("R3D_DISCORD_BOT_TOKEN", None)
    try:
        assert resolve_avatar_bytes("111166802121281536") is None
        # a non-fetchable placeholder is always None regardless of token
        assert resolve_avatar_bytes("osu_30196342") is None
        assert resolve_avatar_bytes(None) is None
    finally:
        if old_tok is not None:
            os.environ["DISCORD_BOT_TOKEN"] = old_tok
        if old_tok2 is not None:
            os.environ["R3D_DISCORD_BOT_TOKEN"] = old_tok2


def test_resolve_avatar_cache_hit_skips_fetch():
    import osu_std_renderer.render.leaderboard as lb
    did = "424242424242424242"
    path = avatar_cache_path(did)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"PNGDATA")
    # sabotage the network path — a cache hit must not call it
    called = {"n": 0}
    orig = lb.fetch_discord_avatar

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("fetch attempted on a cache hit")
    lb.fetch_discord_avatar = _boom
    try:
        assert resolve_avatar_bytes(did, token="tok", allow_fetch=True) == b"PNGDATA"
        assert called["n"] == 0
    finally:
        lb.fetch_discord_avatar = orig
        os.unlink(path)


def test_resolve_avatar_allow_fetch_false_returns_none():
    # a fresh (uncached) fetchable id with fetching disabled → None
    did = "515151515151515151"
    p = avatar_cache_path(did)
    if os.path.exists(p):
        os.unlink(p)
    assert resolve_avatar_bytes(did, token="tok", allow_fetch=False) is None


def test_fetch_discord_avatar_failsoft_on_bad_host():
    # a transport failure must return None, not raise
    got = fetch_discord_avatar("111", "badtoken", proxy_url=None, timeout=0.001)
    assert got is None
