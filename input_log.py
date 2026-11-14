"""Owner input log: records the person's own input block (the copy botcore keeps of player 0's action, botctl OFF_DBG)
while the bots run, so what a real E at a Dominion terminal sets can be read off afterwards, with nothing to run or press
beyond playing. One line per change, at most `hz` a second: [brain time, the 0x100 bytes as hex]. The time is the same
clock as the trace's "t", so the lines can be set beside the trace's human rows (where he stood, how long he stood still).
"""
import json
import os
import struct
import time

BLOCK = 0x100


class OwnerInput:
    def __init__(s, path, m, botctl, hz=10.0):
        s.path, s.m, s.b, s.step = path, m, botctl, 1.0 / hz
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        s.f = open(path, "w", encoding="utf-8", buffering=1)
        s.f.write(json.dumps({"owner_input": 1, "block": BLOCK, "started": time.strftime("%Y-%m-%d %H:%M:%S")}) + "\n")
        s.last = -9.0
        s.seq = None
        s.prev = None

    def tick(s, now):
        if now - s.last < s.step:
            return
        seq = struct.unpack_from("<I", s.m, s.b.OFF_DBG_SEQ)[0]
        if seq == s.seq:                           # no new copy this pass
            return
        s.seq, s.last = seq, now
        buf = bytes(s.m[s.b.OFF_DBG:s.b.OFF_DBG + BLOCK])
        if buf == s.prev:
            return
        s.prev = buf
        s.f.write(json.dumps([round(now, 3), buf.hex()]) + "\n")
