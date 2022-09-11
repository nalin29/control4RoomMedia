"""Provides data updates from the Control4 controller for platforms."""
import logging
import json
from pkgutil import iter_modules

from .pyControl42.account import C4Account
from .pyControl42.director import C4Director
from .pyControl42.error_handling import BadToken

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_TOKEN, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import aiohttp_client

from .const import (
    CONF_ACCOUNT,
    CONF_CONTROLLER_UNIQUE_ID,
    CONF_DIRECTOR,
    CONF_DIRECTOR_TOKEN_EXPIRATION,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def director_update_data(
    hass: HomeAssistant, entry: ConfigEntry, var: str | list
) -> dict:
    """Retrieve data from the Control4 director for update_coordinator."""
    # possibly implement usage of director_token_expiration to start
    # token refresh without waiting for error to occur
    try:
        director = hass.data[DOMAIN][entry.entry_id][CONF_DIRECTOR]
        data = await director.getAllItemVariableValue(var)
    except BadToken:
        _LOGGER.info("Updating Control4 director token")
        await refresh_tokens(hass, entry)
        director = hass.data[DOMAIN][entry.entry_id][CONF_DIRECTOR]
        data = await director.getAllItemVariableValue(var)
    return {key["id"]: key for key in data}

async def director_update_data_mult(
    hass: HomeAssistant, entry: ConfigEntry, var: str | list
) -> dict:
    """Retrieve data from the Control4 director for update_coordinator."""
    # possibly implement usage of director_token_expiration to start
    # token refresh without waiting for error to occur
    try:
        director = hass.data[DOMAIN][entry.entry_id][CONF_DIRECTOR]
        data = await director.getAllItemVariableValue(var)
    except BadToken:
        _LOGGER.info("Updating Control4 director token")
        await refresh_tokens(hass, entry)
        director = hass.data[DOMAIN][entry.entry_id][CONF_DIRECTOR]
        data = await director.getAllItemVariableValue(var)
    res = {}
    for item in data:
        id = item['id']
        if id not in res:
            res[id] = {}
        varname = item['varName']
        value = item['value']
        res[id][varname] = value
    return res


async def director_get_entry_variables(
    hass: HomeAssistant, entry: ConfigEntry, item_id: int
) -> dict:
    try:
        director = hass.data[DOMAIN][entry.entry_id][CONF_DIRECTOR]
        data = await director.getItemVariables(item_id)
    except BadToken:
        _LOGGER.info("Updating Control4 director token")
        await refresh_tokens(hass, entry)
        director = hass.data[DOMAIN][entry.entry_id][CONF_DIRECTOR]
        data = await director.getItemVariables(item_id)

    result = {}
    for item in json.loads(data):
        result[item["varName"]] = item["value"]

    return result

async def director_get_all_items( hass: HomeAssistant, entry: ConfigEntry) -> dict:
    try:
        director = hass.data[DOMAIN][entry.entry_id][CONF_DIRECTOR]
        data = await director.getAllItemInfo()
    except BadToken:
        _LOGGER.info("Updating Control4 director token")
        await refresh_tokens(hass, entry)
        director = hass.data[DOMAIN][entry.entry_id][CONF_DIRECTOR]
        data = await director.getAllItemInfo()
    result = {}
    for item in json.loads(data):
        result[item['id']] = item
    return result

async def refresh_tokens(hass: HomeAssistant, entry: ConfigEntry):
    """Store updated authentication and director tokens in hass.data."""
    config = entry.data
    account_session = aiohttp_client.async_get_clientsession(hass)

    account = C4Account(config[CONF_USERNAME], config[CONF_PASSWORD], account_session)
    await account.getAccountBearerToken()

    controller_unique_id = config[CONF_CONTROLLER_UNIQUE_ID]
    director_token_dict = await account.getDirectorBearerToken(controller_unique_id)
    director_session = aiohttp_client.async_get_clientsession(hass, verify_ssl=False)

    director = C4Director(
        config[CONF_HOST], director_token_dict[CONF_TOKEN], director_session
    )
    #director_token_expiry = director_token_dict["token_expiration"]

    _LOGGER.debug("Saving new tokens in hass data")
    entry_data = hass.data[DOMAIN][entry.entry_id]
    entry_data[CONF_ACCOUNT] = account
    entry_data[CONF_DIRECTOR] = director
    #entry_data[CONF_DIRECTOR_TOKEN_EXPIRATION] = director_token_expiry
