import os, sys
os.environ["QT_QPA_PLATFORM"]="offscreen"; sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QEventLoop
from PySide6.QtGui import QGuiApplication
app=QApplication([])
import inject, textdrag as td
calls=[]; seq=[100]; selection={"text":"Hello world, copied by Shift-drag"}
cb=QGuiApplication.clipboard()
def rec(name):
    def f(*a):
        calls.append((name,)+a)
        if name=="copy" and selection["text"] is not None:
            seq[0]+=1; cb.setText(selection["text"])
    return f
for n in ["release_shift","press_shift","copy","paste","move","left_down","left_up"]: setattr(inject,n,rec(n))
inject.clipboard_seq=lambda: seq[0]
def wait(ms):
    l=QEventLoop(); QTimer.singleShot(ms,l.quit); l.exec()
M,D,U,RD=td.WM_MOUSEMOVE,td.WM_LBUTTONDOWN,td.WM_LBUTTONUP,td.WM_RBUTTONDOWN
def mk(eligible=True):
    f=td.TextDragFilter(lambda x,y: eligible); return f, td.TextDrag(f)

# 1. plain shift-click is replayed untouched
f,t=mk(); calls.clear()
assert f.handle(D,100,100,True)=="suppress"; assert f.handle(M,102,101,True)=="pass"; assert f.handle(U,102,101,True)=="suppress"
wait(50); print("1 click replay:", calls); assert calls==[("left_down",100,100),("left_up",102,101)]

# 2. drag with text selected -> copy, chip, paste at drop, clipboard restored
cb.setText("ORIGINAL CLIPBOARD"); f,t=mk(); calls.clear()
f.handle(D,100,100,True); f.handle(M,140,130,True); wait(300)
print("2a chip visible:", t.chip.isVisible(), repr(t.chip.label.text()[:80]))
assert t.chip.isVisible()
assert f.handle(U,500,400,True)=="suppress"; wait(1000)
print("2b calls:", calls); print("   clipboard after:", cb.text())
names=[c[0] for c in calls]
assert names==["release_shift","copy","release_shift","left_down","left_up","paste"], names
assert ("left_down",500,400) in calls and cb.text()=="ORIGINAL CLIPBOARD" and not t.chip.isVisible()

# 3. drag with nothing selected -> fall back to normal shift-drag
selection["text"]=None; f,t=mk(); calls.clear()
f.handle(D,100,100,True); f.handle(M,150,100,True); wait(300)
print("3a fallback:", calls, "state passthru:", f.state==f.PASSTHRU)
assert f.state==f.PASSTHRU and ("left_down",100,100) in calls and "paste" not in [c[0] for c in calls]
assert f.handle(U,300,100,True)=="pass"  # user's real release reaches the app

# 4. release before the copy decision -> paste still happens at the drop point
selection["text"]="quick"; cb.setText("PREV"); f,t=mk(); calls.clear()
f.handle(D,10,10,True); f.handle(M,60,60,True); f.handle(U,70,70,True); wait(1000)
print("4 fast drop:", calls, cb.text()); assert ("left_down",70,70) in calls and ("paste",) in calls and cb.text()=="PREV"

# 5. right-click cancels; following left-up swallowed; clipboard restored
cb.setText("KEEP"); f,t=mk(); calls.clear()
f.handle(D,10,10,True); f.handle(M,60,60,True); wait(300)
assert f.handle(RD,60,60,True)=="suppress"; wait(50); assert f.handle(U,60,60,True)=="suppress"; wait(800)
print("5 cancel:", calls, cb.text(), t.chip.isVisible()); assert "paste" not in [c[0] for c in calls] and cb.text()=="KEEP"

# 6. no shift / not eligible -> never intercept
f,t=mk(); assert f.handle(D,1,1,False)=="pass" and f.handle(U,1,1,False)=="pass"
f,t=mk(eligible=False); assert f.handle(D,1,1,True)=="pass"
# 7. text panel: drop on a tool -> transform pasted over the selection, clipboard restored after
class FakePanel:
    def __init__(self, key): self.key=key; self.closed=False
    def hit(self, pos): return self.key
    def close(self): self.closed=True
selection["text"]="make me loud"; cb.setText("BEFORE"); calls.clear()
panel=FakePanel("upper")
f=td.TextDragFilter(lambda x,y: True); t=td.TextDrag(f, panel_factory=lambda: panel)
f.handle(D,10,10,True); f.handle(M,60,60,True); wait(300)
assert t.panel is panel
seen=[]
orig_paste=inject.paste
inject.paste=lambda: (calls.append(("paste",)), seen.append(cb.text()))
f.handle(U,70,70,True); wait(1000)
print("7 panel upper:", calls, seen, cb.text(), panel.closed)
assert seen==["MAKE ME LOUD"] and cb.text()=="BEFORE" and panel.closed and ("left_down",70,70) not in calls

# 8. text panel: non-transform tool goes to the app callback with the original text
got=[]; selection["text"]="scan me"; calls.clear()
f=td.TextDragFilter(lambda x,y: True); t=td.TextDrag(f, panel_factory=lambda: FakePanel("qr"), on_action=lambda k,txt: got.append((k,txt)))
f.handle(D,10,10,True); f.handle(M,60,60,True); wait(300); f.handle(U,70,70,True); wait(300)
print("8 panel qr:", got); assert got==[("qr","scan me")]
assert td.TRANSFORMS["title"]("it's a TEST") == "It's A Test"
print("ALL OK")
