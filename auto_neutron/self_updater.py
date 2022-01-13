# This file is part of Auto_Neutron.
# Copyright (C) 2019  Numerlor

from __future__ import annotations

import io
import logging
import shutil
import subprocess  # noqa S404
import sys
import typing as t
from functools import partial
from pathlib import Path
from zipfile import ZipFile

from PySide6 import QtCore, QtNetwork, QtWidgets

# noinspection PyUnresolvedReferences
from __feature__ import snake_case, true_property  # noqa: F401
from auto_neutron.constants import VERSION
from auto_neutron.settings import General
from auto_neutron.utils.network import (
    NetworkError,
    json_from_network_req,
    make_network_request,
)
from auto_neutron.windows.download_confirm_dialog import VersionDownloadConfirmDialog
from auto_neutron.windows.update_error_window import UpdateErrorWindow

log = logging.getLogger(__name__)

LATEST_RELEASE_URL = (
    "https://api.github.com/repos/Numerlor/Auto_Neutron/releases/latest"
)
IS_ONEFILE = Path(getattr(sys, "_MEIPASS", "")) != Path(sys.executable).parent

TEMP_NAME = "temp_auto_neutron"
EXECUTABLE_PATH = Path(sys.argv[0])


class Updater(QtCore.QObject):
    """Check for a new release, and prompt the user for download if one is found and not skipped."""

    _download_started = QtCore.Signal(QtNetwork.QNetworkReply)

    def __init__(self, parent: t.Optional[QtWidgets.QWidget] = None):
        super().__init__(parent)
        self._download_started.connect(self._show_progress_dialog)

    def check_update(self) -> None:
        """
        Check for a new version, if one is found download it and restart with the new file.

        The current executable's file will be renamed to a temporary name, and deleted by this method on the next run.
        """
        if not __debug__:
            log.info("Requesting version info.")
            make_network_request(
                LATEST_RELEASE_URL, finished_callback=self._check_new_version
            )
            try:
                EXECUTABLE_PATH.with_stem(TEMP_NAME).unlink(
                    missing_ok=True
                )  # Try to delete old executable
            except OSError as e:
                log.warning("Unable to delete temp executable.", exc_info=e)
            temp_dir = EXECUTABLE_PATH.parent.with_name(TEMP_NAME)

            if temp_dir.exists():
                try:
                    shutil.rmtree(temp_dir)
                except OSError as e:
                    log.warning("Unable to delete temp directory files.", exc_info=e)

    def _show_ask_dialog(self, release_json: dict[str, t.Any]) -> None:
        """Show the download confirmation dialog."""
        dialog = VersionDownloadConfirmDialog(
            self.parent(),
            changelog=release_json["body"],
            version=release_json["tag_name"],
        )
        dialog.confirmed_signal.connect(
            partial(self._download_new_release, release_json)
        )
        dialog.show()

    def _show_progress_dialog(self, reply: QtNetwork.QNetworkReply) -> None:
        """Show the download progress dialog reporting on `reply`."""
        dialog = QtWidgets.QProgressDialog(self.parent())
        dialog.canceled.connect(reply.abort)
        dialog.canceled.connect(dialog.close)
        dialog.set_modal(True)

        def update_progress(received_bytes: int, total_bytes: int) -> None:
            dialog.maximum = total_bytes
            dialog.value = received_bytes

        reply.downloadProgress.connect(update_progress)
        dialog.label_text = _("Downloading new release")
        dialog.show()

    def _show_error_window(self, error: str) -> None:
        """Show the error window for `error`."""
        window = UpdateErrorWindow(self.parent(), error)
        window.show()

    def _check_new_version(self, network_reply: QtNetwork.QNetworkReply) -> None:
        """Check the json from `network_reply` for a new version, show the ask dialog if it's not skipped or current."""
        try:
            release_json = json_from_network_req(
                network_reply, json_error_key="message"
            )
        except NetworkError as e:
            if e.reply_error is not None:
                error_msg = e.reply_error
            else:
                error_msg = e.error_message
            self._show_error_window(error_msg)
        else:
            version = release_json["tag_name"]
            log.info(f"Received version info with version {version}.")
            if version not in {VERSION, General.last_checked_release}:
                self._show_ask_dialog(release_json)

    def _download_new_release(self, release_json: dict[str, t.Any]) -> None:
        """Start downloading the appropriate new release asset."""
        if IS_ONEFILE:
            asset_name = "Auto_Neutron.exe"
        else:
            asset_name = "Auto_Neutron.zip"

        asset_json = next(
            (asset for asset in release_json["assets"] if asset["name"] == asset_name),
            None,
        )

        if asset_json is None:
            self._show_error_window(_("Unable to find appropriate new release."))
        else:
            download_url = asset_json["browser_download_url"]
            log.info(f"Downloading release from {download_url}.")
            self._download_started.emit(
                make_network_request(
                    download_url, finished_callback=self._create_new_and_restart
                )
            )

    def _create_new_and_restart(self, reply: QtNetwork.QNetworkReply) -> None:
        """
        Create the new executable/directory from the reply data and start it.

        In the one directory move, the current contents of this directory are moved to the `TEMP_NAME` directory next
        to it, and the new contents are unpacked into the original.

        For one file, the current executable is renamed to the `TEMP_NAME` name and a new one is created in its place.

        After a successful download and moving, the app immediately exits
        and any temp cleanup is left to the new process.
        """
        try:
            if reply.error() is QtNetwork.QNetworkReply.NetworkError.NoError:
                download_bytes = reply.read_all().data()
            elif (
                reply.error()
                is QtNetwork.QNetworkReply.NetworkError.OperationCanceledError
            ):
                return
            else:
                self._show_error_window(reply.error_string())
                return
        finally:
            reply.delete_later()

        if IS_ONEFILE:
            temp_path = EXECUTABLE_PATH.with_stem(TEMP_NAME)
            try:
                EXECUTABLE_PATH.rename(temp_path)
            except OSError as e:
                self._show_error_window(_("Unable to rename executable: ") + str(e))
                return
            try:
                Path(EXECUTABLE_PATH).write_bytes(download_bytes)
            except OSError as e:
                self._show_error_window(_("Unable to create new executable: ") + str(e))
                return

        else:
            dir_path = EXECUTABLE_PATH.parent
            temp_path = dir_path.with_name(TEMP_NAME)

            try:
                temp_path.mkdir(exist_ok=True)
            except OSError as e:
                self._show_error_window(
                    _("Unable to create temporary directory: ") + str(e)
                )
                return

            for file in dir_path.glob("*"):
                try:
                    shutil.move(file, temp_path)
                except OSError as e:
                    self._show_error_window(
                        _("Unable to move files into temporary directory: ") + str(e)
                    )
                    return

            try:
                ZipFile(io.BytesIO(download_bytes)).extractall(path=dir_path)
            except OSError as e:
                self._show_error_window(
                    _("Unable to extract new release files: ") + str(e)
                )
                return

        subprocess.Popen(str(EXECUTABLE_PATH))  # noqa S603
        QtWidgets.QApplication.instance().exit()
