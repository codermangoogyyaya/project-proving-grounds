"""Talk to botcore.dll (v3 shared memory) from Python.

usage: python -I botctl.py status
       python -I botctl.py add_bot [machine] [team]  create a bot player (team 0 red, 1 blue; default: game's)
       python -I botctl.py set_team <player index> <team>   (takes effect on the next spawn)
       python -I botctl.py adopt <player index>  drive an existing player (e.g. a bot made earlier)
       python -I botctl.py set_loadout <player index> <slot> <reach|h4|h2a>   a bot's loadout from its next spawn
       python -I botctl.py act <slot> <forward> <strafe> <yaw> <pitch> [buttons] [seconds]
                                                 hold one action for a bot slot, then stop it
"""
import mmap
import os
import re
import struct
import sys
import time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from brain import paths  # noqa: E402  (where MCC, the mod kits and Visual Studio are)

SHARED_H = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "native", "shared.h")
NAME = re.search(r'HB_SHM_NAME L"(.+?)"', open(SHARED_H).read()).group(1).replace("\\\\", "\\")
OFF_BTN_MAP, BTN_MAP_N = 1328 + 0x400 + 8 + 132, 32   # v8: btn_map_seq, then 32 x hb_btn_entry (16 bytes)
OFF_LOOK = OFF_BTN_MAP + 4 + 16 * BTN_MAP_N            # v9: cmd_look_game, cmd_look_off (u32), cmd_look[64], mask[64]
OFF_CLAN = OFF_LOOK + 8 + 64 + 64                      # v10: the next bot's clan tag (wchar_t[8])
OFF_EMBLEM = OFF_CLAN + 16                             # v11: its MCC emblem (u16 x 5, brain/emblems.py ids)
OFF_SPAWN = OFF_EMBLEM + 10                            # v12: spawn_at (16 x x, y, z, yaw f32), spawn_seq, spawn_done (16 u32)
OFF_CHAT = OFF_SPAWN + 16 * 16 + 2 * 16 * 4            # v13: chat_seq (u32), then chat (char[64]), reserved (unused): the player's chat line
OFF_USE_TARGET = OFF_CHAT + 4                          # v14: use_target[16] (u32 each, by bot slot), where the chat line was
SIZE = OFF_CHAT + 4 + 64                               # = sizeof(hb_shared), native/shared.h static_assert 3622
# (a bigger view of an older botcore's block still maps: Windows keeps it in whole 4 KB pages; its chat_seq stays 0)
OFF_GAME, OFF_DBG_SEQ, OFF_DBG, OFF_RAW_VALID, OFF_RAW, OFF_BIG = 804, 808, 812, 1068, 1072, 1328   # v7: 0x100-byte buffers
OFF_STATUS = 20 + 64 + 384
OFF_CMD = OFF_STATUS + 256          # cmd, cmd_seq, cmd_done_seq (u32), cmd_arg (i32), result (i64), fault (u32)
OFF_BOTS = 20
OFF_ACTIONS = OFF_BOTS + 64        # 16 x (forward, strafe, yaw, pitch: f32; buttons, seq: u32)
CMD_ADD_BOT, CMD_ADOPT, CMD_SET_TEAM, CMD_SET_LOADOUT = 1, 2, 3, 4
BTN = dict(fire=1, jump=2, crouch=4, grenade=8, melee=16, action=32, reload=64, equipment=128, sprint=2048,
           fire2=1 << 21, cycle=1 << 22, ability=1 << 23)
# (fire2: a vehicle's second input, cycle: the weapon cycle, ability: a hold-mode armor ability's held bit; shared.h)
GAME_ID = {"ce": 1, "h2": 2, "h3": 3, "reach": 4, "odst": 5, "h2a": 6, "h4": 7}
GAME_BIT = {"h2": 1 << 24, "h3": 1 << 25, "reach": 1 << 26, "odst": 1 << 27, "h2a": 1 << 28, "h4": 1 << 29}
MAX_PLAYERS, FIREFIGHT_PLAYERS, REACH_FIREFIGHT_PLAYERS = 16, 4, 8
REACH_FIREFIGHT_BOTS = 7           # Reach Firefight: at most this many bots (the 8 players in all, one of them the person)


def player_cap(game, firefight=False):
    """Players a match may hold, bots and people. ODST's Firefight (its only multiplayer) is built for 4; more crashed
    ODST every time (-25 live: 7, 15 and 16 players, the renderer, halo3odst +0x15950b). Reach's Firefight custom games
    take 8 (REACH_FIREFIGHT_PLAYERS), split red / blue as in a multiplayer custom game."""
    if game == "odst":
        return FIREFIGHT_PLAYERS
    return REACH_FIREFIGHT_PLAYERS if firefight else MAX_PLAYERS


OFF_SETTINGS = 752
DIFFICULTY_TIERS = ["novice", "average", "strong", "top", "strong,average,top,novice"]


OFF_NAME = 772
LOOK_ROLL = 0x100      # native/shared.h HB_LOOK_ROLL: the look is a roll from the game's Armory (seed, slot mask)


def add_bot(m, machine, team=0xFF, name="", game="ce", look=None, clan="", emblem=None):
    """Create a bot (game thread); name up to 15 characters, empty = "Bot N". game "h2" / "h3" marks the command for
    that game's hook (cmd_arg bit 24 / 25 / 26 for reach; each hook only takes its own). look: (offset, {byte
    offset from it: value}) laid over the bot's configuration (brain/looks.py), ("roll", seed, slot mask[, none
    mask]): its armor drawn from the game's own Armory, the none mask's slots set to the game's "none" (Reach,
    botcore s74 / s78), or None: the player's look. clan: its clan tag; emblem: its MCC emblem (the 5 ids of
    brain/emblems.ids, None: MCC's default), both what MCC shows for it (native/report.cpp)."""
    m[OFF_NAME:OFF_NAME + 32] = name[:15].encode("utf-16-le").ljust(32, b"\0")
    if len(m) >= OFF_EMBLEM:                     # (botcore v10 and later)
        m[OFF_CLAN:OFF_CLAN + 16] = clan[:7].encode("utf-16-le").ljust(16, b"\0")
        if len(m) >= SIZE:
            struct.pack_into("<5H", m, OFF_EMBLEM, *(emblem or (0,) * 5))
        body, mask = bytearray(64), bytearray(64)
        if look and look[0] == "roll":           # ("roll", seed, slots[, none[, effect[, bytes]]]): effect = Halo 4 / H2A's value
            struct.pack_into("<IHHB", body, 0, look[1] & 0xFFFFFFFF, look[2] & 0xFFFF, (look[3] if len(look) > 3 else 0) & 0xFFFF,
                             look[4] & 0xFF if len(look) > 4 and look[4] is not None else 0xFF)
            extra = sorted(dict(look[5] if len(look) > 5 else {}).items())[:18]   # {config offset: value} after the roll
            body[9] = len(extra)                 # (Halo 4 colours and visor, native/h4f.cpp)
            for k, (off, v) in enumerate(extra):
                struct.pack_into("<HB", body, 10 + 3 * k, off, v & 0xFF)
            struct.pack_into("<II", m, OFF_LOOK, GAME_ID.get(game, 0) | LOOK_ROLL, 0)
            m[OFF_LOOK + 8:OFF_LOOK + 72] = bytes(body)
            return command(m, CMD_ADD_BOT, machine | team << 8 | GAME_BIT.get(game, 0))
        off, vals = look if look else (0, {})
        for k, v in vals.items():
            if 0 <= k < 64:
                body[k], mask[k] = v & 0xFF, 1
        struct.pack_into("<II", m, OFF_LOOK, GAME_ID.get(game, 0) if vals else 0, off)
        m[OFF_LOOK + 8:OFF_LOOK + 72] = bytes(body)
        m[OFF_LOOK + 72:OFF_LOOK + 136] = bytes(mask)
    return command(m, CMD_ADD_BOT, machine | team << 8 | GAME_BIT.get(game, 0))


def set_use_target(m, slot, handle):
    """Halo 4: the object handle (a Dominion terminal) a bot's view rests on for its Use, 0 = none (native/shared.h
    use_target; h4f.cpp f_use_target writes it into the bot's action each tick)."""
    struct.pack_into("<I", m, OFF_USE_TARGET + 4 * slot, handle & 0xFFFFFFFF)


def set_button_map(m, entries):
    """Write the decoded buttons (native/shared.h hb_btn_entry): [(game, button name, offset, mask,
    press_only)], at most BTN_MAP_N; the rest are cleared. botcore applies them to every bot action."""
    for k in range(BTN_MAP_N):
        g, name, off, mask, press = entries[k] if k < len(entries) else ("ce", None, 0, 0, False)
        struct.pack_into("<IHBBQ", m, OFF_BTN_MAP + 4 + 16 * k, BTN[name] if name else 0, off, int(press),
                         GAME_ID[g], mask)
    seq = struct.unpack_from("<I", m, OFF_BTN_MAP)[0]
    struct.pack_into("<I", m, OFF_BTN_MAP, seq + 1)


def set_loadout(m, index, slot, game, timeout=0.5):
    """Ask for a bot's loadout slot (Reach, Halo 4, H2A; used at its next spawn). True if botcore took
    it: an older build answers -1, as it does for any command it does not know."""
    r = command(m, CMD_SET_LOADOUT, index | slot << 8 | GAME_BIT[game], timeout)
    return not r.get("timeout") and r["result"] == slot


def settings(m):
    """The Bots tab settings: (enabled, red players, blue players, difficulty 0-4, seq)."""
    return struct.unpack_from("<5I", m, OFF_SETTINGS)


TIER_KEYS = ("novice", "average", "strong", "top", "scaling")
STYLE_KEYS = ("rusher", "camper", "flanker", "objector", "spammer", "grudger", "sniper", "any")


def roster(path, firefight=False):
    """The BOTS panel's list from botcore.ini (native/bots_panel.cpp roster_write: "bot=<team>,<tier>,<style>,
    <name>"): [{name, team, tier, style}] in the panel's order, exactly the bots of the next match. None when
    the file has no list (written by a build from before). firefight: Firefight's own list ("ffbot=", 3 bots)."""
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        return None
    pre = "ff" if firefight else ""
    if not any(line.startswith(pre + "roster=") for line in lines):
        return None
    out = []
    for line in lines:
        line = line[len(pre):] if pre and line.startswith(pre + "bot=") else ("" if pre else line)
        parts = line[4:].split(",", 3) if line.startswith("bot=") else []
        if len(parts) == 4 and parts[3]:
            team, tier, style, name = parts
            out.append(dict(name=name[:15], team=min(7, int(team)) if team.isdigit() else 0,
                            tier=tier if tier in TIER_KEYS else "top", style=style if style in STYLE_KEYS else "any"))
    return out


def setting(path, key, default=""):
    """One key=value line of botcore.ini (the BOTS panel's settings, e.g. collector=0|1), or default."""
    try:
        for line in open(path, encoding="utf-8", errors="replace"):
            if line.startswith(key + "="):
                return line[len(key) + 1:].strip()
    except OSError:
        pass
    return default


OFF_NOTICE = 1328 + 0x400 + 8            # notice_seq (u32), then notice (char[128]): "TITLE|line"


def notice(m, title, line=""):
    """Show a notice over the game for a few seconds (botcore's overlay)."""
    text = (title + "|" + line).encode("ascii", "replace")[:127]
    m[OFF_NOTICE + 4:OFF_NOTICE + 4 + 128] = text.ljust(128, b"\0")
    struct.pack_into("<I", m, OFF_NOTICE, struct.unpack_from("<I", m, OFF_NOTICE)[0] + 1)   # publish last


CROUCH_BIT = {6: (0x08, 0x1), 7: (0x08, 0x1)}   # game -> (offset, mask) of crouch in player 0's action
_fresh = {}                                         # last dbg_seq seen and when


def owner_crouch(m):
    """Is the player (player 0) holding crouch? From his own action, which botcore copies every tick (H2A
    / H4: +0x08 bit 0, decoded). None where it is not decoded or the copy is stale."""
    game, seq = struct.unpack_from("<2I", m, OFF_GAME)
    bit = CROUCH_BIT.get(game)
    if bit is None:
        return None
    now = time.time()
    if _fresh.get("seq") != seq:
        _fresh.update(seq=seq, t=now)
    if now - _fresh["t"] > 0.5:                       # no new copy: not in a match
        return None
    flags = struct.unpack_from("<I", m, OFF_DBG + bit[0])[0]
    return bool(flags & bit[1]) and not flags & 0x2   # Space sets 0x3 (bit 0 too): crouch is bit 0 alone


def owner_jumping(m):
    """Is the player pressing jump? H2A / H4: +0x08 bit 1 of his copied action (Space: 0x3, Ctrl
    alone 0x1). His move keys do not show in the copy (+0x1C / +0x20 read 1.0 while he stands
    still), so a jump of his is told from a push by the jump press. None where not decoded."""
    game = struct.unpack_from("<I", m, OFF_GAME)[0]
    if CROUCH_BIT.get(game) is None or owner_crouch(m) is None:
        return None
    return bool(struct.unpack_from("<I", m, OFF_DBG + 0x08)[0] & 0x2)


def shm():
    return mmap.mmap(-1, SIZE, tagname=NAME)


def status(m):
    magic, ver, alive, brain, tick = struct.unpack_from("<5I", m, 0)
    text = m[OFF_STATUS:OFF_STATUS + 256].split(b"\0")[0].decode(errors="replace")
    cmd, seq, done, arg, res, fault = struct.unpack_from("<3Iiq I", m, OFF_CMD)
    bots = struct.unpack_from("<16i", m, OFF_BOTS)
    return dict(magic=hex(magic), version=ver, dll_alive=alive, game_tick=tick, status=text,
                bots={i: b for i, b in enumerate(bots) if b >= 0},
                cmd=cmd, seq=seq, done=done, result=res, fault=fault)


def command(m, cmd, arg, timeout=5.0):
    seq = struct.unpack_from("<I", m, OFF_CMD + 4)[0] + 1
    struct.pack_into("<I", m, OFF_CMD, cmd)
    struct.pack_into("<i", m, OFF_CMD + 12, arg)
    struct.pack_into("<I", m, OFF_CMD + 4, seq)          # publish last
    t = time.time()
    while time.time() - t < timeout:
        if struct.unpack_from("<I", m, OFF_CMD + 8)[0] == seq:
            return status(m)
        time.sleep(0.02)
    return dict(timeout=True, **status(m))


def injector(root=None, restore=None):
    """native/bin/inject.exe, put back when it is gone (Windows Defender deleted it and 23:52: it flags DLL injectors),
    from the project's git copy, else built from inject.cpp. None, with a message saying what to do printed, when
    neither works. restore(path) -> bool: how it is put back (tests pass their own)."""
    root = root or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    exe = os.path.join(root, "native", "bin", "inject.exe")
    if os.path.exists(exe):
        return exe
    if restore is None:
        def restore(path):
            import subprocess
            try:                                     # (a developer's copy: git; an install has no git)
                r = subprocess.run(["git", "-C", root, "checkout", "--", "native/bin/inject.exe"], capture_output=True)
                if r.returncode == 0 and os.path.exists(path):
                    return True
            except OSError:
                pass
            vc = paths.vcvars()
            if vc and os.path.exists(vc):
                nat = os.path.join(root, "native")
                subprocess.run(f'call "{vc}" >nul && cd /d "{nat}" && cl /nologo /O2 /W4 /EHsc /MT inject.cpp '
                               f'/Fe:bin\\inject.exe /Fo:bin\\', shell=True, capture_output=True)
            return os.path.exists(path)
    if restore(exe):
        print("inject.exe was missing (Windows Defender removes it now and then); put it back.")
        return exe
    print("Bots can't load: native\\bin\\inject.exe is missing, most likely deleted by Windows Defender, and it could not "
          "be put back. Restore it from Windows Security > Protection history (or run native\\build.bat), then start "
          "again. A Defender exclusion for the native\\bin folder stops it happening.")
    return None


def spawn_at(m, index, x, y, z, yaw):
    """Where a dead bot (player index) respawns: botcore moves its next unit there (Reach, native/reach.cpp
    hr_spawn_moves). Sent while it is dead; one sent while it lives is dropped; x NaN calls one sent off."""
    struct.pack_into("<4f", m, OFF_SPAWN + 16 * index, x, y, z, yaw)
    seq = OFF_SPAWN + 256 + 4 * index
    struct.pack_into("<I", m, seq, struct.unpack_from("<I", m, seq)[0] + 1)   # publish last


def act(m, slot, forward, strafe, yaw, pitch, buttons=0):
    off = OFF_ACTIONS + slot * 24
    seq = struct.unpack_from("<I", m, off + 20)[0] + 1
    struct.pack_into("<4fII", m, off, forward, strafe, yaw, pitch, buttons, seq)


def main():
    m = shm()
    if sys.argv[1:2] == ["add_bot"]:
        machine = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        team = int(sys.argv[3]) if len(sys.argv) > 3 else 0xFF
        print(command(m, CMD_ADD_BOT, machine | team << 8))
    elif sys.argv[1:2] == ["set_team"]:
        print(command(m, CMD_SET_TEAM, int(sys.argv[2]) | int(sys.argv[3]) << 8))
    elif sys.argv[1:2] == ["adopt"]:
        print(command(m, CMD_ADOPT, int(sys.argv[2])))
    elif sys.argv[1:2] == ["set_loadout"]:
        print(set_loadout(m, int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]))
    elif sys.argv[1:2] == ["act"]:
        slot = int(sys.argv[2])
        f, st, yaw, pitch = map(float, sys.argv[3:7])
        names = sys.argv[7] if len(sys.argv) > 7 else ""
        buttons = sum(BTN[n] for n in names.split("+") if n)
        secs = float(sys.argv[8]) if len(sys.argv) > 8 else 2.0
        act(m, slot, f, st, yaw, pitch, buttons)
        time.sleep(secs)
        act(m, slot, 0, 0, yaw, pitch, 0)          # stand still (stays driven)
        print(status(m))
    else:
        a = status(m)
        time.sleep(1)
        b = status(m)
        print(a, "| ticks per s:", b["game_tick"] - a["game_tick"])


if __name__ == "__main__":
    main()
