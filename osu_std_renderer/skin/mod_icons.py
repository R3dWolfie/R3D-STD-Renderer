"""Stable gameplay mod filenames; presentation only, never mod selection.

Authority: osu.stable-source osu!/GameModes/Play/Player.cs:784-801 loads
``selection-mod-`` + the lowercased Mods enum name in
osu!common/SharedEnums.cs:7-38. Cross-checked against Mania's
beatmap/mods.py legacy_mod_icons(). Unsupported lazer acronyms (including
CL/DA/DC/WU/WD) and flags absent from that enum (TD/V2/MR) keep their pills.
"""

LEGACY_MOD_TEXTURES: dict[str, str] = {
    "NF": "selection-mod-nofail",
    "EZ": "selection-mod-easy",
    "HD": "selection-mod-hidden",
    "HR": "selection-mod-hardrock",
    "SD": "selection-mod-suddendeath",
    "DT": "selection-mod-doubletime",
    "RX": "selection-mod-relax",
    "HT": "selection-mod-halftime",
    "NC": "selection-mod-nightcore",
    "FL": "selection-mod-flashlight",
    "AT": "selection-mod-autoplay",
    "SO": "selection-mod-spunout",
    "AP": "selection-mod-relax2",
    "PF": "selection-mod-perfect",
    "4K": "selection-mod-key4",
    "5K": "selection-mod-key5",
    "6K": "selection-mod-key6",
    "7K": "selection-mod-key7",
    "8K": "selection-mod-key8",
    "FI": "selection-mod-fadein",
    "RD": "selection-mod-random",
    "CN": "selection-mod-cinema",
    "TP": "selection-mod-target",
    "9K": "selection-mod-key9",
    "KC": "selection-mod-keycoop",
    "1K": "selection-mod-key1",
    "3K": "selection-mod-key3",
    "2K": "selection-mod-key2",
}
