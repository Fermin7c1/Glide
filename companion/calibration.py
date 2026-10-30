"""Checking what the capture sees: Settings > Game capture > Check now (diagnose: the captured corner, where the
addon's strip is in it and what it decodes to, or what is wrong), and `app.py --calibrate` (save_snapshot)."""
import base64
import os

import protocol


def diagnose(img, grid, summary_fn, attempts=0, decoded=0):
    """What a captured frame says about the game link -> {"ok", "problem" ("strip" | "version" | "decode"), "text",
    "png", "w", "h", "attempts", "decoded_frames", "strip", "fields"}. summary_fn: -> the game state summary, for
    the fields shown when the strip reads."""
    from capture import png_bytes
    out = {"ok": False, "png": base64.b64encode(png_bytes(img)).decode("ascii"), "w": img.w, "h": img.h,
           "attempts": attempts, "decoded_frames": decoded}
    loc = protocol.locate(img)
    if not loc:
        out.update(problem="strip", text="WoW is there, but the addon's strip isn't in the captured corner. "
                   "Check that the Glide addon is enabled (/glide strip on), that WoW runs Windowed or Windowed "
                   "(Fullscreen) (exclusive fullscreen captures black), and that nothing covers the window's "
                   "top-left corner.")
        return out
    out["strip"] = {"x": loc[0], "y": loc[1], "cell": loc[2],
                    "kind": "binary" if protocol.strip_kind(loc) == protocol.BINARY else "6-bit"}
    res, _ = protocol.decode_image(img, grid["cols"], grid["rows"], loc)
    if isinstance(res, protocol.OtherVersion):
        out.update(problem="version", text="The strip reads cleanly, but the addon speaks strip version %d and this "
                   "coach reads version %d: the Glide addon and the coach don't match. The Status page says what "
                   "to update." % (res.version, protocol.PROTO))
        return out
    if not res:
        out.update(problem="decode", text="The strip is found but doesn't read cleanly. Something may cover "
                   "it (another addon, a screen overlay, HDR or a colour filter); a UI scale change can also "
                   "need a moment to settle.")
        return out
    core, page = protocol.parse_payload(res[1])
    s = summary_fn()
    out.update(ok=True, text="The coach reads the game.", fields={
        "name": s.get("name"), "level": s.get("level") or core.get("l"), "zone": s.get("zone"),
        "class": s.get("class"), "frame": res[0], "page": page.get("pg")})
    return out


def save_snapshot(cfg, out):
    """Grab the WoW window's corner once, save it as a PNG at `out` (all of the strip when it is bigger than the
    default corner) and print where the strip is and what it decodes to."""
    from capture import WowCapture, save_png
    cap = WowCapture(cfg["capture"]["width"], cfg["capture"]["height"], cfg["capture"]["window_title"])
    img = cap.frame()
    if img is None:
        print("WoW window not found.")
        return
    os.makedirs(os.path.dirname(out), exist_ok=True)
    save_png(img, out)
    loc = protocol.locate(img)
    if loc and cap.resize(*protocol.fit_size(loc, cfg["grid"]["cols"], cfg["grid"]["rows"],
                                             (cfg["capture"]["width"], cfg["capture"]["height"]))):
        img = cap.frame() or img                  # the strip is bigger than the default corner: grab all of it
        save_png(img, out)
    print("saved", out, "| strip at", loc, "| corner %d x %d" % (img.w, img.h))
    if loc:
        res, _ = protocol.decode_image(img, cfg["grid"]["cols"], cfg["grid"]["rows"], loc)
        print("decoded:", res)
