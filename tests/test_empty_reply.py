"""空返答（thinking だけで打ち切られる形）から立ち直れるかを確かめる。"""
import sys, json
from pathlib import Path
sys.path.insert(0, str(Path("src").resolve()))
import os
os.environ["ANTHROPIC_API_KEY"] = "test"
import script_writer as SW

calls = []
class R:
    def __init__(self, body): self.status_code=200; self._b=body
    def json(self): return self._b

def fake_post(url, json=None, headers=None, timeout=None):
    calls.append(json["max_tokens"])
    if len(calls) < 3:   # 2回続けて thinking だけで打ち切られる
        return R({"stop_reason":"max_tokens","content":[{"type":"thinking","thinking":"..."}],
                  "usage":{"output_tokens":json["max_tokens"]}})
    return R({"stop_reason":"end_turn",
              "content":[{"type":"text","text":'{"turns":[{"speaker":"analyst","text":"本文"}]}'}]})

SW.requests.post = fake_post
out = SW._call("m", "p", 16000)
assert "本文" in out, out
assert calls == [16000, 32000, 48000], calls
print("余白の広げ方:", calls, "→ 3回目で本文が返り、成功")

# 3回とも空なら、きちんと理由つきで失敗すること
calls.clear()
def always_empty(url, json=None, headers=None, timeout=None):
    calls.append(json["max_tokens"])
    return R({"stop_reason":"max_tokens","content":[{"type":"thinking"}],"usage":{}})
SW.requests.post = always_empty
try:
    SW._call("m", "p", 16000); raise SystemExit("失敗するはずが成功した")
except RuntimeError as e:
    assert "stop_reason=max_tokens" in str(e), e
    print("3回とも空:", calls, "→ 理由つきで失敗  OK")
print("\nすべて通りました")
