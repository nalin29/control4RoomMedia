"""Platform for Media Players"""
from __future__ import annotations

import logging
import asyncio
from datetime import timedelta
import logging
from re import M
from typing import Any
import json

from pyC4Room.error_handling import C4Exception
from pyC4Room.room import C4Room

from homeassistant.components.media_player import (
   MediaPlayerEntity,
   SUPPORT_PLAY,
   SUPPORT_PAUSE,
   SUPPORT_SELECT_SOURCE,
   SUPPORT_STOP,
   SUPPORT_VOLUME_STEP,
   SUPPORT_VOLUME_MUTE,
   SUPPORT_VOLUME_SET,
   SUPPORT_TURN_OFF,
   SUPPORT_TURN_ON,
   MediaPlayerDeviceClass,
   STATE_OFF,
   STATE_IDLE,
   STATE_PLAYING
)

SUPPORT_FLAGS = [ SUPPORT_PLAY,
                  SUPPORT_PAUSE,
                  SUPPORT_SELECT_SOURCE,
                  SUPPORT_STOP,
                  SUPPORT_VOLUME_STEP,
                  SUPPORT_VOLUME_MUTE,
                  SUPPORT_VOLUME_SET,
                  SUPPORT_TURN_OFF,
                  SUPPORT_TURN_ON
               ]

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import Control4Entity, get_items_of_category
from .const import CONF_DIRECTOR, CONTROL4_ENTITY_TYPE, DOMAIN
from .director_utils import director_get_all_items, director_update_data, director_get_entry_variables, director_update_data_mult

CONTROL4_ROOM_TYPE = 8

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
   """Set up Control4 Rooms from a config entry"""
   entry_data = hass.data[DOMAIN][entry.entry_id]
   scan_interval = entry_data[CONF_SCAN_INTERVAL]

   _LOGGER.debug("Scan interval = %s", scan_interval,)

   entity_list = []

   director = entry_data[CONF_DIRECTOR]

   async def async_update_data_room():
      try:
         return await director_update_data_mult(hass, entry, ["POWER_STATE", "CURRENT_VOLUME", "IS_MUTED", "PLAYING_AUDIO_DEVICE"])
      except C4Exception as err:
         raise UpdateFailed(f"Error communicating with API: {err}") from err
   
   room_coordinator = DataUpdateCoordinator(
      hass,
      _LOGGER,
      name="room",
      update_method=async_update_data_room,
      update_interval=timedelta(seconds=scan_interval)
   )

   await room_coordinator.async_refresh()

   all_items = await director.getAllItemInfo()
   all_items = json.loads(all_items)

   for item in all_items:
      try:
         if item["type"] == CONTROL4_ROOM_TYPE and item['id']:
            item_name = str(item["name"])
            item_id = item["id"]

            item_manufacturer = "Control4"
            item_device_name = item_name
            item_model = "Room"

         else:
            continue
      except KeyError:
         _LOGGER.exception(
                "Unknown device properties received from Control4: %s",
                item,
         )
         continue

      entity_list.append(
            Control4MediaPlayer(
                entry_data,
                room_coordinator,
                item_name,
                item_id,
                item_device_name,
                item_manufacturer,
                item_model,
                item_id,
            )
      )

   async_add_entities(entity_list, True)


class Control4MediaPlayer(Control4Entity, MediaPlayerEntity):
   """Control4 Media Player Entity"""
   def __init__(
      self, 
      entry_data: dict, 
      coordinator: DataUpdateCoordinator, 
      name: str, 
      idx: int, 
      device_name: str | None, 
      device_manufacturer: str | None, 
      device_model: str | None, 
      device_id: int
   ) -> None:
      super().__init__(
         entry_data, 
         coordinator, 
         name, 
         idx, 
         device_name, 
         device_manufacturer, 
         device_model, 
         device_id
      )

   def create_api_object(self):
      """Create a pyControl4 device object.

      This exists so the director token used is always the latest one, without needing to re-init the entire entity.
      """
      return C4Room(self.entry_data[CONF_DIRECTOR], self._idx)
   
   @property
   def device_class(self) -> MediaPlayerDeviceClass | str | None:
      return MediaPlayerDeviceClass.SPEAKER

   @property
   def supported_features(self) -> int:
      """Flag supported features."""
      return  SUPPORT_SELECT_SOURCE | SUPPORT_VOLUME_STEP | SUPPORT_VOLUME_MUTE | SUPPORT_VOLUME_SET | SUPPORT_TURN_OFF | SUPPORT_TURN_ON
   
   @property
   def state(self) -> str:
      if "POWER_STATE" in self.coordinator.data[self._idx]:
         if self.coordinator.data[self._idx]["POWER_STATE"] > 0:
            return STATE_PLAYING
         else:
            return STATE_OFF
      return STATE_OFF
   
   @property
   def volume_level(self) -> float | None:
      if "CURRENT_VOLUME" in self.coordinator.data[self._idx]:
         return  float(self.coordinator.data[self._idx]["CURRENT_VOLUME"]) / 100
      return 0
   
   @property
   def is_volume_muted(self) -> bool | None:
      if "IS_MUTED" in self.coordinator.data[self._idx]:
         return bool(self.coordinator.data[self._idx]['IS_MUTED'])
      return False
   
   @property
   def media_title(self) -> str | None:
      if "PLAYING_AUDIO_DEVICE" in self.coordinator.data[self._idx]:
         if int(self.coordinator.data[self._idx]['PLAYING_AUDIO_DEVICE']) == 937:
            return "Spotify Connect"
         if int(self.coordinator.data[self._idx]['PLAYING_AUDIO_DEVICE']) == 306:
            return "ShairBridge"
         return "Unknown"
      return "None"
   
   @property
   def source(self) -> str | None:
      if "PLAYING_AUDIO_DEVICE" in self.coordinator.data[self._idx]:
         if int(self.coordinator.data[self._idx]['PLAYING_AUDIO_DEVICE']) == 937:
            return "Spotify Connect"
         if int(self.coordinator.data[self._idx]['PLAYING_AUDIO_DEVICE']) == 306:
            return "ShairBridge"
         return "Unknown"
      return "None"
   
   @property
   def source_list(self) -> list[str] | None:
      return ['Spotify Connect', "Shairbridge"]
   
   async def async_turn_on(self):
      c4_room = self.create_api_object()
      await c4_room.setVolume(30)
      await c4_room.setAudioSource(937)
      _LOGGER.debug("Turning on Room: %s", self.name)
      await self.coordinator.async_request_refresh()
   
   async def async_turn_off(self):
      c4_room = self.create_api_object()
      await c4_room.setRoomOff()
      _LOGGER.debug("Turning off Room: %s", self.name)
      await self.coordinator.async_request_refresh()
   
   async def async_mute_volume(self, mute):
      c4_room = self.create_api_object()
      await c4_room.setMute(bool(mute))
      _LOGGER.debug("Muting Room: %s", self.name)
      await self.coordinator.async_request_refresh()
   
   async def async_set_volume_level(self, volume):
      c4_room = self.create_api_object()
      await c4_room.setVolume(int(volume * 100))
      _LOGGER.debug("Setting Volume in Room: %s", self.name)
      await self.coordinator.async_request_refresh()
   
   async def async_media_pause(self):
      c4_room = self.create_api_object()
      await c4_room.setPause()
      _LOGGER.debug("Pausing in Room: %s", self.name)
      await self.coordinator.async_request_refresh()
   
   async def async_media_play(self):
      c4_room = self.create_api_object()
      await c4_room.setPlay()
      _LOGGER.debug("Playing in Room: %s", self.name)
      await self.coordinator.async_request_refresh()
   
   async def async_media_stop(self):
      c4_room = self.create_api_object()
      await c4_room.setStop()
      _LOGGER.debug("Stopping in Room: %s", self.name)
      await self.coordinator.async_request_refresh()
   
   async def async_select_source(self, source):
      _LOGGER.debug("Selecting Source in Room: %s", self.name)
      c4_room = self.create_api_object()
      if(source == 'Shairbridge'):
         await c4_room.setAudioSource(306)
      elif(source == 'Spotify Connect'):
         await c4_room.setAudioSource(937)
      _LOGGER.debug("Selecting Source in Room: %s", self.name)
      await self.coordinator.async_request_refresh()
      
