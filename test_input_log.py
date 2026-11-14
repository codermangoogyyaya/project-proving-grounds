"""python3 tests/test_input_log.py  (tools/input_log.py against a fake shared memory)"""
import json, os, struct, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from input_log import OwnerInput, BLOCK

class B: OFF_DBG_SEQ, OFF_DBG = 100, 120
m = bytearray(1024); d = tempfile.mkdtemp(); path = os.path.join(d, "logs", "o.jsonl")
log = OwnerInput(path, m, B, hz=10.0)
def put(seq, word8=0):
    struct.pack_into("<I", m, B.OFF_DBG_SEQ, seq); struct.pack_into("<Q", m, B.OFF_DBG + 0x40, word8)
put(1, 0); log.tick(0.0)               # first copy logged
put(2, 0); log.tick(0.05)              # too soon
put(3, 0); log.tick(0.2)               # same bytes: skipped
put(4, 0xC00000); log.tick(0.4)        # a change: logged
put(4, 0xC00000); log.tick(1.0)        # no new copy: skipped
put(5, 0xC00000 | 1 << 40); log.tick(1.2)
lines = [json.loads(l) for l in open(path)]
assert lines[0]["owner_input"] == 1 and len(lines) == 4, lines
t, h = lines[2]; b = bytes.fromhex(h)
assert len(b) == BLOCK and struct.unpack_from("<Q", b, 0x40)[0] == 0xC00000 and t == 0.4
print("input_log ok")
