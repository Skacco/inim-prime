"""The troubleshooting actions only read, and only an admin can call them."""
import pytest

from homeassistant.core import Context
from homeassistant.exceptions import Unauthorized

from custom_components.inim_prime import debug_services
from custom_components.inim_prime.const import DOMAIN

READ_ONLY = {
    "debug_read_terminals",
    "debug_watch_terminals",
    "debug_read_memory",
    "debug_memory_diff",
    "debug_primelan_outputs",
}


async def test_only_read_actions_are_registered(hass):
    debug_services.async_register(hass)

    registered = {name for name in hass.services.async_services_for_domain(DOMAIN) if name.startswith("debug_")}
    assert registered == READ_ONLY


@pytest.mark.parametrize("service", sorted(READ_ONLY))
async def test_non_admin_cannot_call_them(hass, hass_read_only_user, service):
    debug_services.async_register(hass)
    data = {"address": 0, "length": 1} if service in ("debug_read_memory", "debug_memory_diff") else {}

    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN, service, data, blocking = True, return_response = True,
            context = Context(user_id = hass_read_only_user.id),
        )
