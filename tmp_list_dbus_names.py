import traceback
from pathlib import Path
out = Path(r'c:\Users\t148888\Documents\dev\pnpink\tmp_dbus_names_out.txt')
try:
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    proxy = Gio.DBusProxy.new_sync(bus, Gio.DBusProxyFlags.NONE, None, 'org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus', None)
    names = proxy.call_sync('ListNames', None, Gio.DBusCallFlags.NO_AUTO_START, 2000, None).unpack()[0]
    out.write_text('\n'.join(sorted(names)), encoding='utf-8')
except BaseException as exc:
    out.write_text(repr(exc) + '\n\n' + traceback.format_exc(), encoding='utf-8')
