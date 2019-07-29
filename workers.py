import itertools
import json
from math import ceil

import requests
from PyQt5 import QtCore, QtMultimedia
from ahk import Hotkey, AHK
from pyperclip import copy as set_clip


class AhkWorker(QtCore.QThread):
    sys_signal = QtCore.pyqtSignal(int, bool)  # signal to move grayout to index
    route_finished_signal = QtCore.pyqtSignal()  # route end reached signal
    game_shut_signal = QtCore.pyqtSignal(list, int)  # signal for game shutdown

    def __init__(self, parent, journal, data_values, settings, start_index):
        super(AhkWorker, self).__init__(parent)
        self.journal = journal
        self.data_values = data_values
        self.systems = [data[0].casefold() for data in data_values]
        self.settings = settings
        self.script, self.bind, self.dark, self.copy = settings

        if not self.copy:
            self.ahk = AHK(executable_path=self.settings.value("paths/AHK"))
        self.loop = True
        # set index according to last saved route or new plot, default index 1
        if start_index > 0:
            self.list_index = start_index
        elif len(self.data_values) != 1:
            self.list_index = 1
        else:
            self.list_index = 0
        # connect parent signals
        parent.double_signal.connect(self.set_index)
        parent.edit_signal.connect(self.update_sys)
        parent.script_settings.connect(self.update_script)
        parent.window_quit_signal.connect(self.exit_and_save)
        parent.save_route_signal.connect(self.save_route)
        parent.quit_worker_signal.connect(self.quit_loop)
        parent.script_mode_signal.connect(self.set_copy)

    def run(self):
        self.main()

    def main(self):
        shutdown = False
        # search file for shutdown event, do not continue and send signal if it's found
        with open(self.journal, encoding='utf-8') as f:
            for l in f:
                j = json.loads(l)
                if j['event'] == "Shutdown":
                    shutdown = True
                    self.game_shut_signal.emit(self.data_values, self.list_index)

        if not shutdown:
            if self.copy:
                set_clip(self.systems[self.list_index])
            else:
                self.hotkey = Hotkey(self.ahk, self.bind,
                                     self.script.replace("|SYSTEMDATA|",
                                                         self.systems[self.list_index]))
                self.hotkey.start()
            self.sys_signal.emit(self.list_index, self.dark)
            for line in self.follow_file(open(self.journal, encoding='utf-8')):
                loaded = json.loads(line)
                if (loaded['event'] == "FSDJump" and
                        loaded['StarSystem'].casefold() in self.systems[self.list_index:]):
                    self.list_index = self.systems.index(loaded['StarSystem'].casefold()) + 1

                    if self.list_index == len(self.data_values):
                        self.close_ahk()
                        self.route_finished_signal.emit()
                        break
                    if self.copy:
                        set_clip(self.systems[self.list_index])
                    else:
                        self.reset_ahk()
                    self.sys_signal.emit(self.list_index, self.dark)

                elif loaded['event'] == "Loadout":
                    pass

                elif loaded['event'] == "Shutdown":
                    self.game_shut_signal.emit(self.data_values, self.list_index)
                    self.close_ahk()
                    break

    def set_index(self, index):
        self.list_index = index
        if self.copy:
            set_clip(self.systems[self.list_index])
        else:
            self.close_ahk()
            hotkey = Hotkey(self.ahk, self.bind,
                            self.script.replace("|SYSTEMDATA|",
                                                self.systems[self.list_index]))
            hotkey.start()
        self.sys_signal.emit(self.list_index, self.dark)

    def update_sys(self, index, new_sys):
        self.data_values[index][0] = new_sys
        self.systems[index] = new_sys.casefold()
        if self.list_index == index:
            if self.copy:
                set_clip(self.systems[self.list_index])
            else:
                self.reset_ahk()

    def update_script(self, tup):
        if self.bind != tup[0] or self.script != tup[1]:
            self.bind = tup[0]
            self.script = tup[1]
            if not self.copy:
                self.reset_ahk()
        self.dark = tup[2]

    def set_copy(self, setting):
        if setting is not self.copy:
            self.copy = setting
            if self.copy:
                self.close_ahk()
                set_clip(self.systems[self.list_index])
            else:
                self.ahk = AHK(executable_path=self.settings.value("paths/AHK"))
                self.reset_ahk()

    def reset_ahk(self):
        self.close_ahk()
        self.hotkey = Hotkey(self.ahk,
                             self.bind,
                             self.script.replace("|SYSTEMDATA|",
                                                 self.systems[self.list_index]))
        self.hotkey.start()

    def close_ahk(self):
        try:
            self.hotkey.stop()
        except (RuntimeError, AttributeError):
            pass

    def exit_and_save(self, save_route):
        if save_route:
            self.save_route()
        self.close_ahk()

    def save_route(self):
        self.settings.setValue("last_route", (self.list_index, self.data_values))

    def quit_loop(self):
        self.loop = False
        self.close_ahk()

    def follow_file(self, file):
        file.seek(0, 2)
        while self.loop:
            loopline = file.readline()
            if not loopline:
                self.sleep(1)
                continue
            yield loopline


class FuelAlert(QtCore.QThread):
    alert_signal = QtCore.pyqtSignal()

    def __init__(self, parent, max_fuel, file, modifier):
        super(FuelAlert, self).__init__(parent)
        self.file = file
        self.max_fuel = max_fuel
        self.loop = True
        self.alert = False

        self.set_jump_fuel(modifier)
        parent.stop_alert_worker_signal.connect(self.stop_loop)
        parent.next_jump_signal.connect(self.change_alert)
        parent.modifier_signal.connect(self.set_jump_fuel)

    def run(self):
        self.main(self.file)

    def main(self, path):
        hold = False
        for line in self.follow_file(open(path)):
            if len(line) > 0:
                loaded = json.loads(line)
                try:
                    # notify when fuel is low,
                    # fsd is in cooldown and ship in supercruise
                    if (loaded['Fuel']['FuelMain'] < self.jump_fuel
                            and not hold
                            and f"{loaded['Flags']:b}"[-19] == "1"
                            and f"{loaded['Flags']:b}"[-5] == "1"
                            and self.alert):
                        hold = True
                        self.alert_signal.emit()
                    elif loaded['Fuel']['FuelMain'] > self.jump_fuel:
                        hold = False
                except KeyError:
                    pass

    def set_jump_fuel(self, modifier):
        self.jump_fuel = self.max_fuel * modifier / 100

    def change_alert(self, status):
        self.alert = status

    def stop_loop(self):
        self.loop = False

    def follow_file(self, file):
        while self.loop:
            file.seek(0, 0)
            loopline = file.readline()
            self.sleep(2)
            yield loopline


class SpanshPlot(QtCore.QThread):
    finished_signal = QtCore.pyqtSignal(list)  # signal containing output
    status_signal = QtCore.pyqtSignal(str)  # signal for updating statusbar

    def __init__(self, efficiency, jrange, source, to, parent=None):
        super(SpanshPlot, self).__init__(parent)
        self.efficiency = efficiency
        self.jrange = jrange
        self.source = source
        self.to = to

    def run(self):
        self.plot(self.efficiency, self.jrange, self.source, self.to)

    def plot(self, efficiency, jrange, source, to):
        try:
            job_request = requests.get("https://spansh.co.uk/api/route",
                                       params=
                                       f"efficiency={efficiency}"
                                       f"&range={jrange}"
                                       f"&from={source}"
                                       f"&to={to}")
        except requests.exceptions.ConnectionError:
            self.status_signal.emit("Cannot establish a connection to Spansh")
        else:
            job_json = json.loads(job_request.content.decode())
            try:
                if job_json['error'] == "Could not find starting system":
                    self.status_signal.emit("Source system invalid")
                elif job_json['error'] == "Could not find finishing system":
                    self.status_signal.emit("Destination system invalid")
                else:
                    self.status_signal.emit("An error has occured when contacting Spansh's API")

            except KeyError:
                job_id = job_json['job']
                self.status_signal.emit("Plotting")

                for sleep_base in itertools.count(1, 5):
                    encodedjob = requests.get("https://spansh.co.uk/api/results/" + job_id)
                    decodedjob = encodedjob.content.decode()
                    job_json = json.loads(decodedjob)
                    if job_json['status'] == "queued":
                        # 1, 1, 2, 2, 3, 4, 6, 7, 9, 12, 15, 17, 20, 24, 27, 30, 30, 30, …
                        self.sleep(min(ceil(ceil((sleep_base / 10) ** 2) / 1.9), 30))
                    else:
                        self.finished_signal.emit(
                            [[data['system'],
                              round(float(data['distance_jumped']), 2),
                              round(float(data['distance_left']), 2),
                              int(data['jumps'])]
                             for data in job_json['result']['system_jumps']])
                        break


class NearestRequest(QtCore.QThread):
    finished_signal = QtCore.pyqtSignal(dict)  # output signal
    status_signal = QtCore.pyqtSignal(str)  # statusbar change signal

    def __init__(self, link, params, parent=None):
        super(NearestRequest, self).__init__(parent)
        self.link = link
        self.params = params

    def run(self):
        self.request(self.link, self.params)

    def request(self, request_link, parameters):
        try:
            self.status_signal.emit("Waiting for spansh")
            job_request = requests.get(request_link, params=parameters)
        except requests.exceptions.ConnectionError:
            self.status_signal.emit("Unable to establish a connection to Spansh")
        else:
            if job_request.ok:
                response = json.loads(job_request.content.decode())
                self.finished_signal.emit(response['system'])
            else:
                self.status_signal.emit(
                    "An error has occured while communicating with Spansh's API")


class SoundPlayer:
    def __init__(self, path):
        self.sound_file = QtMultimedia.QMediaPlayer()
        self.sound_file.setMedia(QtMultimedia.QMediaContent(
            QtCore.QUrl.fromLocalFile(path)))
        self.sound_file.setVolume(100)

    def play(self):
        self.sound_file.play()
