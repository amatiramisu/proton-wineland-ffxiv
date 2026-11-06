"""Resolve a desktop's monitor preference for Wine at launch."""

import ctypes
import json
import shlex
import shutil
import subprocess


def _plasma_monitors(text):
    config = json.loads(text)
    if not isinstance(config, dict) or not isinstance(config.get("outputs"), list):
        return []
    return [output.get("name") for output in config["outputs"]
            if isinstance(output, dict) and output.get("connected") and output.get("enabled")
            and output.get("priority") == 1 and output.get("name")]


def _gnome_monitors(text):
    # GetResources exposes output flags; GetCurrentState's logical-monitor flags
    # can remain stale after changing the primary monitor in Mutter.
    config = json.loads(text)
    data = config.get("data") if isinstance(config, dict) else None
    if not isinstance(data, list) or len(data) != 6 or not isinstance(data[2], list):
        return []
    monitors = []
    for output in data[2]:
        if (not isinstance(output, list) or len(output) != 8
                or type(output[2]) is not int or not isinstance(output[7], dict)):
            return []
        primary = output[7].get("primary", {})
        if not isinstance(primary, dict):
            return []
        if output[2] >= 0 and primary.get("type") == "b" and primary.get("data") is True:
            monitors.append(output[4])
    return monitors


def _gnome_session_bus():
    """Mutter's primary output, asked over the session bus through GIO.

    The session bus reaches Mutter from inside the Steam Runtime container too,
    so this works for launchers without Steam (umu). Returns None when GIO, the
    bus or Mutter is unavailable.
    """
    try:
        gio = ctypes.CDLL("libgio-2.0.so.0")
        glib = ctypes.CDLL("libglib-2.0.so.0")
        gobject = ctypes.CDLL("libgobject-2.0.so.0")
    except OSError:
        return None
    ptr = ctypes.c_void_p
    gio.g_bus_get_sync.restype = ptr
    gio.g_bus_get_sync.argtypes = [ctypes.c_int, ptr, ptr]
    gio.g_dbus_connection_call_sync.restype = ptr
    gio.g_dbus_connection_call_sync.argtypes = [
        ptr, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
        ptr, ptr, ctypes.c_int, ctypes.c_int, ptr, ptr]
    glib.g_variant_n_children.restype = ctypes.c_size_t
    glib.g_variant_n_children.argtypes = [ptr]
    glib.g_variant_get_child_value.restype = ptr
    glib.g_variant_get_child_value.argtypes = [ptr, ctypes.c_size_t]
    glib.g_variant_get_int32.restype = ctypes.c_int32
    glib.g_variant_get_int32.argtypes = [ptr]
    glib.g_variant_get_string.restype = ctypes.c_char_p
    glib.g_variant_get_string.argtypes = [ptr, ptr]
    glib.g_variant_lookup_value.restype = ptr
    glib.g_variant_lookup_value.argtypes = [ptr, ctypes.c_char_p, ctypes.c_char_p]
    glib.g_variant_get_boolean.restype = ctypes.c_int
    glib.g_variant_get_boolean.argtypes = [ptr]
    glib.g_variant_unref.argtypes = [ptr]
    gobject.g_object_unref.argtypes = [ptr]

    bus = gio.g_bus_get_sync(2, None, None)  # G_BUS_TYPE_SESSION
    if not bus:
        return None
    # G_DBUS_CALL_FLAGS_NO_AUTO_START, two seconds as for the command route
    reply = gio.g_dbus_connection_call_sync(
        bus, b"org.gnome.Mutter.DisplayConfig", b"/org/gnome/Mutter/DisplayConfig",
        b"org.gnome.Mutter.DisplayConfig", b"GetResources", None, None, 1, 2000, None, None)
    gobject.g_object_unref(bus)
    if not reply:
        return None

    # Same fields as _gnome_monitors: current CRTC (2), name (4), properties (7)
    monitors = []
    outputs = glib.g_variant_get_child_value(reply, 2)
    for i in range(glib.g_variant_n_children(outputs)):
        output = glib.g_variant_get_child_value(outputs, i)
        crtc = glib.g_variant_get_child_value(output, 2)
        name = glib.g_variant_get_child_value(output, 4)
        props = glib.g_variant_get_child_value(output, 7)
        primary = glib.g_variant_lookup_value(props, b"primary", b"b")
        if glib.g_variant_get_int32(crtc) >= 0 and primary and glib.g_variant_get_boolean(primary):
            monitors.append(glib.g_variant_get_string(name, None).decode())
        if primary:
            glib.g_variant_unref(primary)
        for value in (props, name, crtc, output):
            glib.g_variant_unref(value)
    glib.g_variant_unref(outputs)
    glib.g_variant_unref(reply)
    return monitors


def _niri_monitors(text):
    # niri supplies the focused output, not a configured desktop primary.
    output = json.loads(text)
    if not isinstance(output, dict) or not isinstance(output.get("logical"), dict):
        return []
    return [output.get("name")]


def _cosmic_monitors(text):
    # Read cosmic-randr list --kdl's emitted layout, not arbitrary KDL.
    # Only output headers, direct primary flags and block boundaries matter.
    monitors = []
    depth = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        fields = shlex.split(line)
        if not depth:
            if (len(fields) != 4 or fields[0] != "output" or not fields[1]
                    or fields[2] not in ("enabled=#true", "enabled=#false") or fields[3] != "{"):
                return []
            name, enabled = fields[1], fields[2] == "enabled=#true"
            primary = None
            depth = 1
        elif line == "}":
            depth -= 1
            if not depth and enabled and primary:
                monitors.append(name)
        elif line == "modes {" and depth == 1:
            depth = 2
        elif depth == 2:
            if not line.startswith("mode "):
                return []
        elif fields[0] == "xwayland_primary":
            if primary is not None or line not in ("xwayland_primary #true", "xwayland_primary #false"):
                return []
            primary = line == "xwayland_primary #true"
        elif line.endswith(("{", "}")) or line.startswith("output "):
            return []
    return [] if depth else monitors


# Desktop token -> log description, command, parser. Order preserves precedence
# for composite desktop names and KDE_FULL_SESSION.
_DESKTOPS = {
    "KDE": ("Plasma primary", ("kscreen-doctor", "-j"), _plasma_monitors),
    "GNOME": ("GNOME primary", (
        "busctl", "--user", "--json=short", "--timeout=2",
        "--auto-start=no", "--allow-interactive-authorization=no",
        "call", "org.gnome.Mutter.DisplayConfig", "/org/gnome/Mutter/DisplayConfig",
        "org.gnome.Mutter.DisplayConfig", "GetResources"), _gnome_monitors),
    "NIRI": ("niri focused", ("niri", "msg", "--json", "focused-output"), _niri_monitors),
    "COSMIC": ("COSMIC Xwayland-primary", ("cosmic-randr", "list", "--kdl"), _cosmic_monitors),
}


def setup_primary_monitor(env, log):
    """Set Wine's initial monitor in env, without changing desktop settings.

    Explicit overrides (including empty ones) win. Make at most one query;
    missing tools, failed queries and ambiguous answers leave env unchanged.
    """
    if "WAYLANDDRV_PRIMARY_MONITOR" in env:
        return

    desktops = {name.upper() for variable in ("XDG_CURRENT_DESKTOP", "XDG_SESSION_DESKTOP")
                for name in env.get(variable, "").split(":") if name}
    if env.get("KDE_FULL_SESSION", "0") not in ("", "0"):
        desktops.add("KDE")
    for desktop, (description, command, parse) in _DESKTOPS.items():
        if desktop in desktops:
            break
    else:
        return

    if desktop == "GNOME":
        monitors = _gnome_session_bus()
        if monitors:
            if len(monitors) == 1 and monitors[0]:
                env["WAYLANDDRV_PRIMARY_MONITOR"] = monitors[0]
                log("Using " + description + " monitor " + monitors[0])
            return

    launcher = shutil.which("steam-runtime-launch-client", path=env.get("PATH"))
    if launcher:
        command = [launcher, "--alongside-steam", "--", "/usr/bin/" + command[0], *command[1:]]
    else:
        executable = shutil.which(command[0], path=env.get("PATH"))
        if not executable:
            return
        command = [executable, *command[1:]]

    try:
        result = subprocess.run(command, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, text=True, timeout=2, check=True)
        monitors = parse(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return

    if len(monitors) == 1 and isinstance(monitors[0], str) and monitors[0]:
        env["WAYLANDDRV_PRIMARY_MONITOR"] = monitors[0]
        log("Using " + description + " monitor " + monitors[0])
