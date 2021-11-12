# This file is part of Auto_Neutron.
# Copyright (C) 2021  Numerlor

import contextlib
import json
import typing as t
from functools import partial

from PySide6 import QtCore, QtWidgets

# noinspection PyUnresolvedReferences
from __feature__ import snake_case, true_property  # noqa F401
from auto_neutron.hub import GameState

from ..constants import JOURNAL_PATH, SPANSH_API_URL
from ..game_state import Location
from ..journal import Journal
from ..route_plots import spansh_exact_callback, spansh_neutron_callback
from ..ship import Ship
from ..utils.network import make_network_request
from ..utils.signal import ReconnectingSignal
from ..utils.utils import create_request_delay_iterator
from .gui.new_route_window import NewRouteWindowGUI
from .nearest_window import NearestWindow


class NewRouteWindow(NewRouteWindowGUI):
    """The UI for plotting a new route, from CSV, Spansh plotters, or the last saved route."""

    route_created_signal = QtCore.Signal(Journal, list)

    def __init__(self, parent: QtWidgets.QWidget, game_state: GameState):
        super().__init__(parent)
        self.game_state = game_state

        self.current_ship = self.game_state.ship
        self.selected_journal: t.Optional[Journal] = None

        # region spansh tabs init
        self.spansh_neutron_tab.nearest_button.pressed.connect(
            self._display_nearest_window
        )
        self.spansh_exact_tab.nearest_button.pressed.connect(
            self._display_nearest_window
        )

        # Disable submit plot buttons and set them to be enabled when their respective from/to fields are filled
        self.spansh_neutron_tab.submit_button.enabled = False
        self.spansh_exact_tab.submit_button.enabled = False

        self.spansh_neutron_tab.source_edit.textChanged.connect(
            self._set_neutron_submit
        )
        self.spansh_neutron_tab.target_edit.textChanged.connect(
            self._set_neutron_submit
        )

        self.spansh_exact_tab.source_edit.textChanged.connect(self._set_exact_submit)
        self.spansh_exact_tab.target_edit.textChanged.connect(self._set_exact_submit)

        self.spansh_neutron_tab.efficiency_spin.value = 80  # default to 80% efficiency

        self.spansh_neutron_tab.cargo_slider.valueChanged.connect(
            self._recalculate_range
        )

        self.spansh_neutron_tab.journal_combo.currentIndexChanged.connect(
            self._change_journal
        )
        self.spansh_exact_tab.journal_combo.currentIndexChanged.connect(
            self._change_journal
        )
        self.spansh_neutron_tab.submit_button.pressed.connect(self._submit_neutron)
        self.spansh_exact_tab.submit_button.pressed.connect(self._submit_exact)

        # endregion

        self.combo_signals = (
            ReconnectingSignal(
                self.csv_tab.journal_combo.currentIndexChanged,
                self._sync_journal_combos,
            ),
            ReconnectingSignal(
                self.spansh_neutron_tab.journal_combo.currentIndexChanged,
                self._sync_journal_combos,
            ),
            ReconnectingSignal(
                self.spansh_exact_tab.journal_combo.currentIndexChanged,
                self._sync_journal_combos,
            ),
            ReconnectingSignal(
                self.last_route_tab.journal_combo.currentIndexChanged,
                self._sync_journal_combos,
            ),
        )
        for signal in self.combo_signals:
            signal.connect()

        self._change_journal(0)

    # region spansh plotters
    def _submit_neutron(self) -> None:
        """Submit a neutron plotter request to spansh."""
        make_network_request(
            SPANSH_API_URL + "/route",
            params={
                "efficiency": self.spansh_neutron_tab.efficiency_spin.value,
                "range": self.spansh_neutron_tab.range_spin.value,
                "from": self.spansh_neutron_tab.source_edit.text,
                "to": self.spansh_neutron_tab.target_edit.text,
            },
            reply_callback=partial(
                spansh_neutron_callback,
                delay_iterator=create_request_delay_iterator(),
                result_callback=partial(
                    self.route_created_signal.emit, self.selected_journal
                ),
            ),
        )

    def _submit_exact(self) -> None:
        """Submit an exact plotter request to spansh."""
        if self.spansh_exact_tab.use_clipboard_checkbox.checked:
            ship = Ship.from_coriolis(
                json.loads(QtWidgets.QApplication.instance().clipboard().text())
            )
        else:
            ship = self.current_ship

        make_network_request(
            SPANSH_API_URL + "/generic/route",
            params={
                "source": self.spansh_exact_tab.source_edit.text,
                "destination": self.spansh_exact_tab.target_edit.text,
                "is_supercharged": int(
                    self.spansh_exact_tab.is_supercharged_checkbox.checked
                ),
                "use_supercharge": int(
                    self.spansh_exact_tab.supercarge_checkbox.checked
                ),
                "use_injections": int(
                    self.spansh_exact_tab.fsd_injections_checkbox.checked
                ),
                "exclude_secondary": int(
                    self.spansh_exact_tab.exclude_secondary_checkbox.checked
                ),
                "fuel_power": ship.fsd.size_const,
                "fuel_multiplier": ship.fsd.rating_const / 1000,
                "optimal_mass": ship.fsd.optimal_mass,
                "base_mass": ship.unladen_mass,
                "tank_size": ship.tank_size,
                "internal_tank_size": ship.reserve_size,
                "max_fuel_per_jump": ship.fsd.max_fuel_usage,
                "range_boost": ship.jump_range_boost,
            },
            reply_callback=partial(
                spansh_exact_callback,
                delay_iterator=create_request_delay_iterator(),
                result_callback=partial(
                    self.route_created_signal.emit, self.selected_journal
                ),
            ),
        )

    def _set_widget_values(
        self, location: Location, ship: Ship, current_cargo: int
    ) -> None:
        """Update the UI with values from `location`, `ship` and `current_cargo`."""
        self.spansh_neutron_tab.source_edit.text = location.name
        self.spansh_exact_tab.source_edit.text = location.name

        self.spansh_neutron_tab.cargo_slider.maximum = ship.max_cargo
        self.spansh_neutron_tab.cargo_slider.value = current_cargo

        self.spansh_exact_tab.cargo_slider.maximum = ship.max_cargo
        self.spansh_exact_tab.cargo_slider.value = current_cargo

        self.spansh_neutron_tab.range_spin.value = ship.jump_range(
            cargo_mass=current_cargo
        )

    def _recalculate_range(self, cargo_mass: int) -> None:
        """Recalculate jump range with the new cargo_mass."""
        self.spansh_neutron_tab.range_spin.value = self.current_ship.jump_range(
            cargo_mass=cargo_mass
        )

    def _set_neutron_submit(self) -> None:
        """Enable the neutron submit button if both inputs are filled, disable otherwise."""
        self.spansh_neutron_tab.submit_button.enabled = bool(
            self.spansh_neutron_tab.source_edit.text
            and self.spansh_neutron_tab.target_edit.text
        )

    def _set_exact_submit(self) -> None:
        """Enable the exact submit button if both inputs are filled, disable otherwise."""
        self.spansh_exact_tab.submit_button.enabled = bool(
            self.spansh_exact_tab.source_edit.text
            and self.spansh_exact_tab.target_edit.text
        )

    def _display_nearest_window(self) -> None:
        """Display the nearest system finder window and link its signals."""
        current_loc = self.game_state.location
        if current_loc is not None:
            coordinates = (current_loc.x, current_loc.y, current_loc.z)
        else:
            coordinates = (0, 0, 0)
        window = NearestWindow(self, *coordinates)
        window.copy_to_source_button.pressed.connect(
            partial(
                self._set_line_edits_from_nearest,
                self.spansh_neutron_tab.source_edit,
                self.spansh_exact_tab.source_edit,
                window=window,
            )
        )
        window.copy_to_destination_button.pressed.connect(
            partial(
                self._set_line_edits_from_nearest,
                self.spansh_neutron_tab.target_edit,
                self.spansh_exact_tab.target_edit,
                window=window,
            )
        )

    def _set_line_edits_from_nearest(
        self, *line_edits: QtWidgets.QLineEdit, window: NearestWindow
    ) -> None:
        """Update the line edits with `system_name_result_label` contents from `window`."""
        for line_edit in line_edits:
            line_edit.text = window.system_name_result_label.text

    # endregion

    def _sync_journal_combos(self, index: int) -> None:
        """Assign all journal combo boxes to display the item at `index`."""
        exit_stack = contextlib.ExitStack()
        with exit_stack:
            for signal in self.combo_signals:
                exit_stack.enter_context(signal.temporarily_disconnect())
            self.csv_tab.journal_combo.current_index = index
            self.spansh_neutron_tab.journal_combo.current_index = index
            self.spansh_exact_tab.journal_combo.current_index = index
            self.last_route_tab.journal_combo.current_index = index

    def _change_journal(self, index: int) -> None:
        """Change the current journal and update the UI with its data, or display an error if shut down."""
        journal_path = sorted(
            JOURNAL_PATH.glob("Journal.*.log"),
            key=lambda path: path.stat().st_ctime,
            reverse=True,
        )[index]

        journal = Journal(journal_path)
        loadout, location, cargo_mass, shut_down = journal.get_static_state()

        if shut_down:
            self.status_bar.show_message(
                "Selected journal ended with a shut down event.", 10_000
            )
            self.csv_tab.submit_button.enabled = False
            self.spansh_neutron_tab.submit_button.enabled = False
            self.spansh_exact_tab.submit_button.enabled = False
            self.last_route_tab.submit_button.enabled = False
            return
        self.current_ship = Ship.from_loadout(loadout)
        self.selected_journal = journal

        self.status_bar.clear_message()
        self.csv_tab.submit_button.enabled = True
        self.spansh_neutron_tab.submit_button.enabled = True
        self.spansh_exact_tab.submit_button.enabled = True
        self.last_route_tab.submit_button.enabled = True

        self._set_widget_values(location, self.current_ship, cargo_mass)
