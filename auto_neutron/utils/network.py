# This file is part of Auto_Neutron.
# Copyright (C) 2021  Numerlor

import collections.abc
import json
import logging
import typing as t
import urllib.parse
from functools import partial

from PySide6 import QtCore, QtNetwork

import auto_neutron

# noinspection PyUnresolvedReferences
from __feature__ import snake_case, true_property  # noqa F401

log = logging.getLogger(__name__)


class NetworkError(Exception):
    """Raised for Qt network errors."""

    def __init__(self, qt_error: str, spansh_response: t.Optional[None]):
        self.error_message = qt_error
        self.spansh_error = spansh_response
        super().__init__(qt_error, spansh_response)


def make_network_request(
    url: str,
    *,
    params: dict = {},  # noqa B006
    reply_callback: collections.abc.Callable[[QtNetwork.QNetworkReply], t.Any],
) -> None:
    """Make a network request to `url` with a `params` query and connect its reply to `callback`."""
    log.debug(f"Sending request to {url} with {params=}")
    if params:
        url += "?" + urllib.parse.urlencode(params)
    qurl = QtCore.QUrl(url)
    request = QtNetwork.QNetworkRequest(qurl)
    reply = auto_neutron.network_mgr.get(request)
    reply.finished.connect(partial(reply_callback, reply))


def json_from_network_req(reply: QtNetwork.QNetworkReply) -> dict:
    """Decode bytes from the `QNetworkReply` object or raise an error on failed requests."""
    try:
        if reply.error() is QtNetwork.QNetworkReply.NetworkError.NoError:
            return json.loads(reply.read_all().data())
        else:
            text_response = reply.read_all().data()
            if text_response:
                spansh_error = json.loads(text_response)["error"]
            else:
                spansh_error = None
            raise NetworkError(reply.error_string(), spansh_error)
    finally:
        reply.delete_later()