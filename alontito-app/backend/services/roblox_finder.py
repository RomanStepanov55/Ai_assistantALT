"""Надёжный поиск/запуск Roblox: обычный, Store, переименованный/пиратский клиент."""
from __future__ import annotations
import csv
import ctypes
import glob
import io
import os
import subprocess
import sys
import time
import webbrowser

PLACE_ID = 3623096087
ROBLOX_URI = "roblox://experiences/start?placeId=%d" % PLACE_ID
STORE_APP = "shell:AppsFolder\\RobloxCorporation.Roblox_55nm5h9r29jhc!App"


def _processes():
    out = []
    try:
        r = subprocess.run(["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True)
        for row in csv.reader(io.StringIO(r.stdout)):
            if len(row) >= 2:
                out.append((row[0], int(row[1])))
    except Exception:
        pass
    return out


def _windows():
    if sys.platform != "win32":
        return []
    u = ctypes.windll.user32
    res = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def cb(h, l):
        if u.IsWindowVisible(h):
            n = u.GetWindowTextLengthW(h)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                u.GetWindowTextW(h, buf, n + 1)
                pid = ctypes.c_ulong()
                u.GetWindowThreadProcessId(h, ctypes.byref(pid))
                res.append({"title": buf.value, "pid": pid.value, "hwnd": int(h)})
        return True

    try:
        u.EnumWindows(proc(cb), 0)
    except Exception:
        pass
    return res


def find_target(hint_title=None, hint_process=None):
    wins = _windows()
    procs = _processes()
    pid2exe = {p: e for e, p in procs}
    ht = (hint_title or "").lower()
    hp = (hint_process or "").lower()
    w = None
    if ht:
        w = next((x for x in wins if ht in x["title"].lower()), None)
    if not w:
        w = next((x for x in wins if "roblox" in x["title"].lower() or "muscle" in x["title"].lower()), None)
    pr = None
    if hp:
        pr = next(((e, p) for e, p in procs if hp in e.lower()), None)
    if not pr:
        pr = next(((e, p) for e, p in procs if "roblox" in e.lower()), None)
    if not w and pr:
        w = next((x for x in wins if x["pid"] == pr[1]), None)
    if not w and not pr:
        return None
    pid = w["pid"] if w else pr[1]
    exe = pid2exe.get(pid, pr[0] if pr else "")
    return {"title": w["title"] if w else "", "pid": pid, "exe": exe, "hwnd": w["hwnd"] if w else 0}


def launch_attempts():
    tried = []
    try:
        os.startfile(ROBLOX_URI)
        tried.append("protocol roblox://")
        return tried
    except Exception:
        pass
    base = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("ProgramFiles(x86)", "")
    cands = (glob.glob(base + "\\Roblox\\Versions\\*\\RobloxPlayerBeta.exe")
             + glob.glob(base + "\\Roblox\\Versions\\*\\RobloxPlayerLauncher.exe")
             + glob.glob(pf + "\\Roblox\\Versions\\*\\RobloxPlayerBeta.exe"))
    desk = os.path.join(os.environ.get("USERPROFILE", ""), "Desktop")
    try:
        cands += [os.path.join(desk, f) for f in os.listdir(desk)
                  if f.lower().endswith(".lnk") and "roblox" in f.lower()]
    except Exception:
        pass
    for c in cands:
        try:
            os.startfile(c)
            tried.append("exe/lnk: " + c)
            return tried
        except Exception:
            continue
    try:
        subprocess.Popen(["explorer.exe", STORE_APP])
        tried.append("Microsoft Store app")
        return tried
    except Exception:
        pass
    try:
        webbrowser.open("https://www.roblox.com/games/%d/" % PLACE_ID)
        tried.append("browser fallback")
    except Exception:
        pass
    return tried


def wait_for(timeout=90, hint_title=None, hint_process=None):
    t0 = time.time()
    while time.time() - t0 < timeout:
        t = find_target(hint_title, hint_process)
        if t:
            return t
        time.sleep(2)
    return None