"""Walk one search on a real emulator, and assert WHERE it was asked.

    with-emulator medium_phone -- \
        python3 ~/dev-team/scripts/search_walk_check.py <debug apk>

Exit 0 only when the app's own search request carried the place its profile row
names (within 2 km) and the screen named that place. Any other answer is the
2026-11-20 regression: a phone searching a city its reader does not live in.

The device under test is the debug build of the tree in argv[1]; the backend is
`team.mock_server`, whose profile row is shaped like production (hex EWKB) and
whose sentence door answers only when the app searched around the place that row
names. So this walk fails for exactly the reason production failed on
2026-11-20: the app cannot read its own profile's coordinates.
"""
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))
from team import mock_server  # noqa: E402

APK = sys.argv[1]
PKG = "agrawal.bhanu.bookswap"
ADB = os.path.expanduser("~/Library/Android/sdk/platform-tools/adb")
OUT = "/tmp/emu-search-evidence.json"
SHOT = "/tmp/emu-search.png"


def log(msg):
    print(f"[emu] {msg}", flush=True)


def adb(*args, timeout=120, binary=False):
    r = subprocess.run([ADB, *args], capture_output=True, timeout=timeout)
    if r.returncode != 0 and not binary:
        log(f"adb {' '.join(args)[:80]} -> {r.stderr.decode(errors='ignore')[:160]}")
    return r.stdout if binary else r.stdout.decode(errors="ignore")


def wait_boot(timeout=300):
    adb("wait-for-device", timeout=timeout)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if "1" in adb("shell", "getprop", "sys.boot_completed").strip():
            return True
        time.sleep(3)
    return False


def ui_nodes():
    adb("shell", "uiautomator", "dump", "/sdcard/ui.xml", timeout=90)
    xml = adb("shell", "cat", "/sdcard/ui.xml")
    nodes = []
    for m in re.finditer(r'<node[^>]*>', xml):
        tag = m.group(0)
        def attr(name):
            mm = re.search(name + r'="([^"]*)"', tag)
            return mm.group(1) if mm else ""
        bounds = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', tag)
        if not bounds:
            continue
        x1, y1, x2, y2 = map(int, bounds.groups())
        nodes.append({
            "text": attr("text"), "desc": attr("content-desc"),
            "cls": attr("class"), "clickable": attr("clickable") == "true",
            "center": ((x1 + x2) // 2, (y1 + y2) // 2),
            "bounds": (x1, y1, x2, y2),
        })
    return nodes


def find_search_field(nodes):
    for n in nodes:
        blob = f'{n["text"]} {n["desc"]}'.lower()
        if "search" in blob or "find" in blob or "looking for" in blob:
            return n
    for n in nodes:
        if "edittext" in n["cls"].lower():
            return n
    return None


def main():
    server = mock_server.MockServer(mock_server.DEFAULT_PORT).start()
    server.wait_ready()
    log(f"mock backend {server.url} (profile place {mock_server.PROFILE_PLACE})")

    assert wait_boot(), "the emulator never finished booting"
    for cmd in (["shell", "settings", "put", "global", "window_animation_scale", "0"],
                ["shell", "settings", "put", "global", "transition_animation_scale", "0"],
                ["shell", "settings", "put", "global", "animator_duration_scale", "0"]):
        adb(*cmd)

    log("installing the debug build")
    adb("install", "-r", "-g", APK, timeout=600)
    adb("shell", "pm", "clear", PKG)
    # LOCATION IS DELIBERATELY DENIED. A reader who never granted it is exactly
    # whose search broke on 2026-11-20: with no device fix, the app must read its
    # own profile row (hex EWKB) to know the area. Granting it here would let the
    # walk pass while the profile parser is broken.
    adb("shell", "pm", "revoke", PKG, "android.permission.ACCESS_FINE_LOCATION")
    adb("shell", "pm", "revoke", PKG, "android.permission.ACCESS_COARSE_LOCATION")

    log("launching against the mock as the seeded reader")
    adb("shell", "am", "force-stop", PKG)
    adb("shell", "am", "start", "-n", f"{PKG}/.MainActivity",
        "--es", "devteam_mock_base_url", server.url,
        "--es", "devteam_qa_reader", "aarav", timeout=90)

    field = None
    seen = []
    for _ in range(20):
        time.sleep(4)
        nodes = ui_nodes()
        seen = [f'{n["text"]} | {n["desc"]}' for n in nodes if (n["text"] or n["desc"])]
        field = find_search_field(nodes)
        if field:
            break
    if not field:
        log("no search field found — this is what the screen says:")
        for line in seen[:25]:
            log("   " + line)
        shot = adb("exec-out", "screencap", "-p", binary=True)
        with open("/tmp/emu-no-field.png", "wb") as fh:
            fh.write(shot)
        with open("/tmp/emu-no-field.json", "w") as fh:
            json.dump({"screen": seen, "screenshot": "/tmp/emu-no-field.png",
                       "requests": server.requests[-12:]}, fh, indent=2)
        print(json.dumps({"ok": False, "reason": "no search field",
                          "screen": seen[:25]}, indent=2))
        return 1

    log(f"tapping the search field at {field['center']}")
    adb("shell", "input", "tap", str(field["center"][0]), str(field["center"][1]))
    time.sleep(2)
    adb("shell", "input", "text", "added%sthis%sweek")
    time.sleep(2)
    adb("shell", "input", "keyevent", "66")   # ENTER = the field's search action
    time.sleep(12)

    shot = adb("exec-out", "screencap", "-p", binary=True)
    with open(SHOT, "wb") as fh:
        fh.write(shot)

    searches = [r for r in server.requests if r.get("path") == "/api/v1/search"]
    evidence = {
        "searches": searches,
        "requests": [{"path": r["path"], "query": r.get("query") or {}} for r in server.requests],
        "logcat": [l for l in adb("logcat", "-d", "-v", "brief", timeout=90).splitlines()
                   if "bookswap" in l.lower() or "discovery" in l.lower()
                   or "Location" in l or "profile" in l.lower()][-60:],
        "screen_text": [n["text"] for n in ui_nodes() if n["text"]],
        "screenshot": SHOT,
        "profile_place": list(mock_server.PROFILE_PLACE),
    }
    with open(OUT, "w") as fh:
        json.dump(evidence, fh, indent=2)

    if not searches:
        log("the app never called /api/v1/search")
        print(json.dumps(evidence, indent=2)[:2000])
        return 1

    last = searches[-1].get("query") or {}
    lat = float(last.get("lat") or "nan")
    lng = float(last.get("lng") or "nan")
    away = mock_server._km(lat, lng, *mock_server.PROFILE_PLACE)
    lines = [n["text"] for n in ui_nodes() if n["text"]]
    log(f"search sent lat={lat} lng={lng} -> {away:.1f} km from the profile's place")
    verdict = {
        "ok": away <= 2.0 and any("Noida" in t for t in lines),
        "away_km": round(away, 1),
        "query": last,
        "screen": lines[:20],
        "screenshot": SHOT,
    }
    print(json.dumps(verdict, indent=2, ensure_ascii=False))
    return 0 if verdict["ok"] else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    finally:
        pass
