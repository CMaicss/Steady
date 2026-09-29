from gi.repository import Adw, Gtk, Pango

from .i18n import _

def label(text="", style=None, wrap=True, selectable=False):
    widget = Gtk.Label(label=text, xalign=0, wrap=wrap, selectable=selectable)
    widget.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if style:
        widget.add_css_class(style)
    return widget


def text_of(view):
    buffer = view.get_buffer()
    return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)


def text_editor(height=100, accessible_label=None):
    view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=10, bottom_margin=10, left_margin=12, right_margin=12)
    view.update_property([Gtk.AccessibleProperty.LABEL], [accessible_label or _("Description")])
    scroll = Gtk.ScrolledWindow(min_content_height=height, max_content_height=height + 80, propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
    scroll.set_child(view)
    frame = Gtk.Frame(child=scroll)
    return view, frame


def clear_box(box):
    while box.get_first_child():
        box.remove(box.get_first_child())


def confirm(parent, heading, body, action, callback, destructive=False):
    dialog = Adw.AlertDialog(heading=heading, body=body)
    dialog.add_response("cancel", _("Cancel"))
    dialog.add_response("confirm", action)
    dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE if destructive else Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("cancel")
    dialog.set_close_response("cancel")
    dialog.connect("response", lambda current, response: callback() if response == "confirm" else None)
    dialog.present(parent)
    return dialog


def error_dialog(parent, heading, error):
    dialog = Adw.AlertDialog(heading=heading, body=str(error))
    dialog.add_response("ok", _("OK"))
    dialog.set_default_response("ok")
    dialog.set_close_response("ok")
    dialog.present(parent)
