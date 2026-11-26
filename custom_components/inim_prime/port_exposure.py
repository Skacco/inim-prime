"""Optional check that the Native port cannot be reached from the Internet.

Whoever reaches that port can send commands to the panel knowing only the
connection password (the default one is the same on every panel), and can make
it drop its network connections even without knowing it.

The integration runs inside the local network and cannot see the port "from
outside" by itself: when the user enables the check in the connection form,
submitting the form asks an external service to try the port on the public address the request comes from. The
service is any server running "echoip" (https://github.com/mpolden/echoip):
GET <server>/port/<port> answers {"ip": ..., "port": ..., "reachable": bool}.
Nothing about the panel is sent, only the port number.
"""
from __future__ import annotations

import asyncio
import logging

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN, NATIVE_PORT_CHECK_SERVER_DEFAULT

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20


def _issue_id(entry_id: str) -> str:
    return f"native_port_exposed_{entry_id}"


def _check_url(server: str | None, port: int) -> str:
    server = (server or NATIVE_PORT_CHECK_SERVER_DEFAULT).strip().rstrip("/")
    if "://" not in server:
        server = f"https://{server}"
    return f"{server}/port/{port}"


async def async_port_reachable(hass: HomeAssistant, server: str | None, port: int) -> bool | None:
    """True/False as answered by the service, None when it gave no answer."""
    url = _check_url(server, port)
    try:
        response = await async_get_clientsession(hass).get(
            url,
            headers = {"Accept": "application/json"},
            timeout = aiohttp.ClientTimeout(total = REQUEST_TIMEOUT),
        )
        async with response:
            response.raise_for_status()
            answer = await response.json(content_type = None)
    except (aiohttp.ClientError, asyncio.TimeoutError, TimeoutError, ValueError) as err:
        _LOGGER.debug("Port check: no answer from %s (%r)", url, err)
        return None
    reachable = answer.get("reachable") if isinstance(answer, dict) else None
    if not isinstance(reachable, bool):
        _LOGGER.debug("Port check: unexpected answer from %s: %r", url, answer)
        return None
    return reachable


def clear_port_exposed_notice(hass: HomeAssistant, entry_id: str) -> None:
    """Remove the repair notice an earlier build raised at start-up."""
    ir.async_delete_issue(hass, DOMAIN, _issue_id(entry_id))


_RESULT_TEXTS = {
    "it": {
        False: "✅ La porta Native {port} non è raggiungibile da Internet.",
        True: "⚠️ La porta Native {port} è raggiungibile da Internet: rimuovi l'inoltro di quella porta sul router.",
        None: "❓ Controllo non riuscito: il servizio di verifica non ha risposto, quindi non si sa se la porta {port} è raggiungibile da Internet. Riprova più tardi.",
    },
    "en": {
        False: "✅ Native port {port} cannot be reached from the Internet.",
        True: "⚠️ Native port {port} can be reached from the Internet: remove the forwarding of that port on the router.",
        None: "❓ Check failed: the checking service did not answer, so it is unknown whether port {port} can be reached from the Internet. Try again later.",
    },
}


def port_check_result_text(hass: HomeAssistant, port, reachable: bool | None, smartliving: bool = False) -> str:
    """The outcome of the check, in the language of Home Assistant."""
    language = (getattr(hass.config, "language", None) or "en").split("-")[0]
    texts = _RESULT_TEXTS.get(language, _RESULT_TEXTS["en"])
    text = texts[reachable].format(port = port)
    if smartliving:
        # the SmartLAN port, whose protocol is not encrypted
        text = text.replace("porta Native", "porta SmartLAN").replace("Native port", "SmartLAN port")
    return text
