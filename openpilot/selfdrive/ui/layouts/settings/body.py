from openpilot.selfdrive.ui.body.companion import companion, MAX_NAME_LENGTH
from openpilot.selfdrive.ui.ui_state import ui_state
from openpilot.system.ui.lib.application import gui_app
from openpilot.system.ui.lib.multilang import tr, tr_noop
from openpilot.system.ui.widgets import Widget, DialogResult
from openpilot.system.ui.widgets.keyboard import Keyboard
from openpilot.system.ui.widgets.list_view import button_item, toggle_item
from openpilot.system.ui.widgets.scroller_tici import Scroller

DESCRIPTIONS = {
  "BodyName": tr_noop(
    "What the comma body answers to when you talk to it. Short names with clear sounds are heard best."
  ),
  "TeachVoice": tr_noop(
    "Teach the body how its name sounds when you say it. It goes back to its face and beeps eight times: say its name once after each beep. " +
    "Needs drive mode, the microphone on, and a computer on your network running its voice programs."
  ),
  "BodyListening": tr_noop(
    "Turn on the comma body's microphone while it is in drive mode, so it can hear you and a computer on your network can listen through it. " +
    "Off by default. A microphone badge on the face shows whenever the microphone is live. Nothing is recorded."
  ),
  "BodySmoothFace": tr_noop(
    "Try the new face: smooth eyes that follow your touch and whoever is in front of the body. " +
    "Turn off to go back to the dot face."
  ),
  "BodyDataSharing": tr_noop(
    "Record and upload video and logs from your comma body to comma connect to help improve the body. " +
    "Off by default: when off, the body records nothing and uploads nothing. Live teleop still works."
  ),
  "BodyConnect": tr_noop(
    "Let comma connect reach your comma body for remote teleop. " +
    "Off by default: when off, the body does not connect to comma's servers. When on, comma's servers can start a live stream."
  ),
}

# param: (title, icon, restarts openpilot when changed in drive mode)
TOGGLES = {
  "BodyListening": (tr_noop("Microphone"), "microphone.png", False),
  "BodySmoothFace": (tr_noop("New Face (beta)"), "monitoring.png", False),
  "BodyDataSharing": (tr_noop("Share comma body Data"), "monitoring.png", True),
  "BodyConnect": (tr_noop("comma connect Remote Control"), "network.png", True),
}


class BodyLayout(Widget):
  def __init__(self):
    super().__init__()
    self._params = ui_state.params
    self._keyboard = Keyboard(max_text_size=MAX_NAME_LENGTH, min_text_size=2)
    self._close_settings = None

    self._name_item = button_item(lambda: tr("Name"), lambda: tr("CHANGE"), DESCRIPTIONS["BodyName"], callback=self._on_change_name)
    self._name_item.action_item.set_value(companion.name)
    self._teach_item = button_item(lambda: tr("Teach My Voice"), lambda: tr("START"), DESCRIPTIONS["TeachVoice"], callback=self._on_teach,
                                   enabled=lambda: ui_state.is_onroad() and self._params.get_bool("BodyListening"))

    self._toggles = {}
    for param, (title, icon, needs_restart) in TOGGLES.items():
      description = DESCRIPTIONS[param]
      if needs_restart:
        description += " " + tr_noop("Changing this setting will restart openpilot if the body is in drive mode.")
      self._toggles[param] = toggle_item(lambda t=title: tr(t), description, self._params.get_bool(param),
                                         callback=lambda state, p=param, r=needs_restart: self._on_toggle(p, state, r), icon=icon)

    self._scroller = Scroller([self._name_item, self._teach_item, *self._toggles.values()], line_separator=True, spacing=0)

  def set_close_callback(self, close):
    self._close_settings = close

  def show_event(self):
    super().show_event()
    self._scroller.show_event()
    self._name_item.action_item.set_value(companion.name)
    for param, toggle in self._toggles.items():
      toggle.action_item.set_state(self._params.get_bool(param))

  def _update_state(self):
    companion.update()

  def _render(self, rect):
    self._scroller.render(rect)

  def _on_toggle(self, param: str, state: bool, needs_restart: bool):
    self._params.put_bool(param, state, block=True)
    if needs_restart:
      self._params.put_bool("OnroadCycleRequested", True, block=True)

  def _on_change_name(self):
    self._keyboard.reset()
    self._keyboard.set_title(tr("Name your comma body"), tr("It answers to this when you talk to it"))
    self._keyboard.set_text(companion.name)
    self._keyboard.set_callback(self._on_name_entered)
    gui_app.push_widget(self._keyboard)

  def _on_name_entered(self, result: DialogResult):
    if result != DialogResult.CONFIRM:
      return
    self._name_item.action_item.set_value(companion.set_name(self._keyboard.text))

  def _on_teach(self):
    companion.send_event("enroll")
    # back to the face: it shows what to say and how each try went
    if self._close_settings is not None:
      self._close_settings()
