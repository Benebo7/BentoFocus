#!/usr/bin/python3
"""Janelas nativas (GTK + WebKit) do painel.

    window.py          painel completo
    window.py --mini   miniplayer: timer sempre por cima, num canto da tela

Roda com o Python do sistema, que já traz os bindings do GTK; o servidor e o
coletor rodam à parte, como serviço do systemd. Fechar uma janela não para
o rastreio nem o bloco em andamento.
"""

import json
import os
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, GLib, Gtk, WebKit2  # noqa: E402

BASE = f"http://127.0.0.1:{os.environ.get('BENTOFOCUS_PORT', '7777')}/"
BG = "#121014"
ICON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "icon.svg")
OFFLINE = f"""<body style="background:{BG};color:#8f859c;font:15px system-ui;display:grid;place-items:center;height:90vh">
<div style="text-align:center">O serviço do BentoFocus não está respondendo.<br>
<code style="color:#ece6f5">systemctl --user status bentofocus</code><br><small>tentando de novo…</small></div></body>"""

MINI_W, MINI_H, MINI_MARGIN = 380, 190, 24  # tamanho inicial (pixels de tela)
MINI_MIN_W, MINI_MIN_H = 240, 96
MINI_STATE = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "bentofocus", "mini.json")


class App(Gtk.Application):
    def __init__(self, mini: bool) -> None:
        # application_id garante instância única: abrir de novo só traz a janela pra frente.
        app_id = "dev.bento.BentoFocus.Mini" if mini else "dev.bento.BentoFocus"
        super().__init__(application_id=app_id)
        self.mini = mini
        self.url = BASE + ("mini" if mini else "")
        self.win: Gtk.ApplicationWindow | None = None

    def do_activate(self) -> None:
        if self.win is not None:
            self.win.present()
            return
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)

        # Miniplayer = popup (override-redirect): o gerenciador de janelas não o
        # controla, então fica sempre por cima, em todo workspace, nunca pega o
        # foco e o GNOME não mostra "BentoFocus está pronto".
        kind = Gtk.WindowType.POPUP if self.mini else Gtk.WindowType.TOPLEVEL
        win = Gtk.ApplicationWindow(application=self, title="BentoFocus", type=kind)
        self._drag_origin: tuple[int, int] | None = None
        self._resize_origin: tuple[int, int] | None = None
        if os.path.exists(ICON):
            win.set_icon_from_file(ICON)

        manager = WebKit2.UserContentManager()
        view = WebKit2.WebView.new_with_user_content_manager(manager)
        bg = Gdk.RGBA()
        bg.parse("rgba(0,0,0,0)" if self.mini else BG)  # mini: transparente p/ cantos arredondados
        view.set_background_color(bg)  # painel: sem flash branco ao abrir
        view.connect("load-failed", self._on_fail)
        # window.close() na página fecha a janela (o miniplayer some sozinho).
        view.connect("close", lambda _v: win.destroy())

        if self.mini:
            self._setup_mini(win, manager)
            view.set_size_request(MINI_MIN_W, MINI_MIN_H)  # senão o WebKit impõe ~200px de altura
        else:
            win.set_default_size(1300, 920)
            # O título da página mostra o timer ("18:42 · intenção"): aparece no dock.
            view.connect("notify::title", lambda v, _: win.set_title(v.get_title() or "BentoFocus"))

        view.load_uri(self.url)
        win.add(view)
        win.show_all()
        self.win = win

    def _setup_mini(self, win: Gtk.ApplicationWindow, manager: WebKit2.UserContentManager) -> None:
        win.set_title("BentoFocus mini")
        # Visual com alfa: a página desenha um card arredondado sobre fundo transparente.
        screen = win.get_screen()
        if screen.get_rgba_visual() and screen.is_composited():
            win.set_visual(screen.get_rgba_visual())
            win.set_app_paintable(True)

        geo = Gdk.Display.get_default().get_primary_monitor() or Gdk.Display.get_default().get_monitor(0)
        area = geo.get_workarea()
        st = self._load_state()
        w, h = st.get("w", MINI_W), st.get("h", MINI_H)
        x = st.get("x", area.x + area.width - w - MINI_MARGIN)
        y = st.get("y", area.y + MINI_MARGIN)
        # Se o monitor mudou, não deixa a janela nascer fora da tela.
        x = min(max(x, area.x), area.x + area.width - w)
        y = min(max(y, area.y), area.y + area.height - h)
        win.set_default_size(w, h)
        win.move(x, y)

        # Popup não tem arraste nem redimensionamento do gerenciador de janelas:
        # a página manda o deslocamento do mouse e a janela acompanha.
        manager.register_script_message_handler("bento")
        manager.connect("script-message-received::bento", lambda _m, msg: self._on_message(win, msg))

    def _on_message(self, win: Gtk.ApplicationWindow, msg) -> None:
        action = msg.get_js_value().to_string()
        if action == "dragstart":
            self._drag_origin = win.get_position()
        elif action.startswith("move:") and self._drag_origin:
            _, dx, dy = action.split(":")
            win.move(self._drag_origin[0] + int(float(dx)), self._drag_origin[1] + int(float(dy)))
        elif action == "resizestart":
            self._resize_origin = win.get_size()
        elif action.startswith("resize:") and self._resize_origin:
            _, dx, dy = action.split(":")
            win.resize(max(MINI_MIN_W, self._resize_origin[0] + int(float(dx))),
                       max(MINI_MIN_H, self._resize_origin[1] + int(float(dy))))
        elif action == "dragend":
            self._drag_origin = self._resize_origin = None
            self._save_state(win)
        elif action == "close":
            win.destroy()

    @staticmethod
    def _load_state() -> dict:
        try:
            with open(MINI_STATE) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _save_state(win: Gtk.ApplicationWindow) -> None:
        (x, y), (w, h) = win.get_position(), win.get_size()
        os.makedirs(os.path.dirname(MINI_STATE), exist_ok=True)
        with open(MINI_STATE, "w") as f:
            json.dump({"x": x, "y": y, "w": w, "h": h}, f)

    def _on_fail(self, view: WebKit2.WebView, _event, _uri, _error) -> bool:
        if self.mini:
            self.win.destroy()  # sem serviço não há timer para mostrar
            return True
        view.load_html(OFFLINE, None)
        GLib.timeout_add_seconds(3, lambda: view.load_uri(self.url) or False)
        return True


if __name__ == "__main__":
    mini = "--mini" in sys.argv
    name = "bentofocus-mini" if mini else "bentofocus"
    # WM_CLASS: casa com StartupWMClass do .desktop, e o dock mostra o ícone certo.
    GLib.set_prgname(name)
    Gdk.set_program_class(name)
    App(mini).run(None)
