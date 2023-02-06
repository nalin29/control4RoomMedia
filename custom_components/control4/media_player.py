"""Platform for Control4 Rooms."""
from __future__ import annotations

from datetime import timedelta
import enum
import logging

import attr
from pyControl4.error_handling import C4Exception
from pyControl4.room import C4Room

from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.components.media_player.const import (
    MEDIA_TYPE_MOVIE,
    MEDIA_TYPE_MUSIC,
    MediaPlayerEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_SCAN_INTERVAL,
    STATE_IDLE,
    STATE_OFF,
    STATE_ON,
    STATE_PAUSED,
    STATE_PLAYING,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import Control4Entity
from .const import CONF_DIRECTOR, CONF_DIRECTOR_ALL_ITEMS, CONF_UI_CONFIGURATION, DOMAIN
from .director_utils import update_variables_for_entity

_LOGGER = logging.getLogger(__name__)

CONTROL4_POWER_STATE = "POWER_STATE"
CONTROL4_VOLUME_STATE = "CURRENT_VOLUME"
CONTROL4_MUTED_STATE = "IS_MUTED"
CONTROL4_CURRENT_AUDIO_DEVICE = "CURRENT_AUDIO_DEVICE"
CONTROL4_CURRENT_VIDEO_DEVICE = "CURRENT_VIDEO_DEVICE"
CONTROL4_CURRENT_SELECTED_DEVICE = "CURRENT_SELECTED_DEVICE"
CONTROL4_PLAYING = "PLAYING"
CONTROL4_PAUSED = "PAUSED"
CONTROL4_STOPPED = "STOPPED"
CONTROL4_MEDIA_INFO = "CURRENT MEDIA INFO"

CONTROL4_PARENT_ID = "parentId"

VARIABLES_OF_INTEREST = {
    CONTROL4_POWER_STATE,
    CONTROL4_VOLUME_STATE,
    CONTROL4_MUTED_STATE,
    CONTROL4_CURRENT_AUDIO_DEVICE,
    CONTROL4_CURRENT_VIDEO_DEVICE,
    CONTROL4_CURRENT_SELECTED_DEVICE,
    CONTROL4_PLAYING,
    CONTROL4_PAUSED,
    CONTROL4_STOPPED,
}


class _SourceType(enum.Enum):
    AUDIO = 1
    VIDEO = 2


@attr.s
class RoomSource:
    """Error during condition evaluation."""

    source_type: set[_SourceType] = attr.ib()
    id: int = attr.ib()
    name: str = attr.ib()


async def get_rooms(hass: HomeAssistant, entry: ConfigEntry):
    """Return a list of all Control4 rooms."""
    director_all_items = hass.data[DOMAIN][entry.entry_id][CONF_DIRECTOR_ALL_ITEMS]
    return [
        item
        for item in director_all_items
        if "typeName" in item and item["typeName"] == "room"
    ]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up Control4 rooms from a config entry."""
    all_rooms = await get_rooms(hass, entry)
    if not all_rooms:
        return

    entry_data = hass.data[DOMAIN][entry.entry_id]
    scan_interval = entry_data[CONF_SCAN_INTERVAL]
    _LOGGER.debug(
        "Scan interval = %s",
        scan_interval,
    )

    async def async_update_data():
        """Fetch data from Control4 director."""
        try:
            return await update_variables_for_entity(hass, entry, VARIABLES_OF_INTEREST)
        except C4Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="room",
        update_method=async_update_data,
        update_interval=timedelta(seconds=scan_interval),
    )

    # Fetch initial data so we have data when entities subscribe
    await coordinator.async_refresh()

    items_by_id = {
        int(item["id"]): item
        for item in hass.data[DOMAIN][entry.entry_id][CONF_DIRECTOR_ALL_ITEMS]
    }
    item_to_parent_map = {
        k: int(item["parentId"])
        for k, item in items_by_id.items()
        if "parentId" in item and k > 1
    }

    ui_config = entry_data[CONF_UI_CONFIGURATION]

    entity_list = []
    for room in all_rooms:
        room_id = int(room["id"])

        sources: dict[int, RoomSource] = {}
        for exp in ui_config["experiences"]:
            if room_id == int(exp["room_id"]):
                exp_type = exp["type"]
                if exp_type not in ("listen", "watch"):
                    continue

                dev_type = (
                    _SourceType.AUDIO if exp_type == "listen" else _SourceType.VIDEO
                )
                for source in exp["sources"]["source"]:
                    dev_id = int(source["id"])
                    name = items_by_id.get(dev_id, {}).get(
                        "name", f"Unknown Device - {dev_id}"
                    )
                    if dev_id in sources:
                        sources[dev_id].source_type.add(dev_type)
                    else:
                        sources[dev_id] = RoomSource(
                            source_type={dev_type}, id=dev_id, name=name
                        )

        try:
            hidden = room["roomHidden"]
            entity_list.append(
                Control4Room(
                    entry_data,
                    coordinator,
                    room["name"],
                    room_id,
                    item_to_parent_map,
                    sources,
                    hidden,
                )
            )
        except KeyError:
            _LOGGER.exception(
                "Unknown device properties received from Control4: %s",
                room,
            )
            continue

    async_add_entities(entity_list, True)


class Control4Room(Control4Entity, MediaPlayerEntity):
    """Control4 light entity."""

    def __init__(
        self,
        entry_data: dict,
        coordinator: DataUpdateCoordinator,
        name: str,
        idx: int,
        id_to_parent: dict[int, int],
        sources: dict[int, RoomSource],
        room_hidden: bool,
    ) -> None:
        """Initialize Control4 room entity."""
        super().__init__(
            entry_data,
            coordinator,
            name,
            idx,
            device_name=f"Control4 Room - {name}",
            device_manufacturer="Control4",
            device_model="Control4 Room",
            device_id=idx,
        )
        self._attr_entity_registry_enabled_default = not room_hidden
        self._id_to_parent = id_to_parent
        self._sources = sources
        self._is_soft_on = False
        self._attr_supported_features = (
            MediaPlayerEntityFeature.PLAY
            | MediaPlayerEntityFeature.PAUSE
            | MediaPlayerEntityFeature.STOP
            | MediaPlayerEntityFeature.VOLUME_MUTE
            | MediaPlayerEntityFeature.VOLUME_SET
            | MediaPlayerEntityFeature.VOLUME_STEP
            | MediaPlayerEntityFeature.TURN_OFF
            | MediaPlayerEntityFeature.TURN_ON
            | MediaPlayerEntityFeature.SELECT_SOURCE
            | MediaPlayerEntityFeature.GROUPING
        )

    def _create_api_object(self):
        """
        Create a pyControl4 device object.

        This exists so the director token used is always the latest one, without needing to re-init the entire entity.
        """
        return C4Room(self.entry_data[CONF_DIRECTOR], self._idx)

    def _get_device_from_variable(self, var: str) -> int | None:
        current_device = int(self.coordinator.data[self._idx][var])
        if current_device == 0:
            return None

        return current_device

    def _get_current_playing_device_id(self) -> int | None:
        return self._get_device_from_variable(CONTROL4_CURRENT_SELECTED_DEVICE)

    def _get_current_audio_device_id(self) -> int | None:
        return self._get_device_from_variable(CONTROL4_CURRENT_AUDIO_DEVICE)

    def _get_current_video_device_id(self) -> int | None:
        return self._get_device_from_variable(CONTROL4_CURRENT_VIDEO_DEVICE)

    def _get_current_source_state(self) -> str | None:
        current_source = self._get_current_playing_device_id()
        while current_source:
            current_data = self.coordinator.data.get(current_source, None)
            if current_data:
                if current_data.get(CONTROL4_PLAYING, None):
                    return STATE_PLAYING
                if current_data.get(CONTROL4_PAUSED, None):
                    return STATE_PAUSED
                if current_data.get(CONTROL4_STOPPED, None):
                    return STATE_ON
            current_source = self._id_to_parent.get(current_source, None)
        return None

    @property
    def state(self):
        """Return whether this room is on or off."""

        if source_state := self._get_current_source_state():
            return source_state

        if self.coordinator.data[self._idx][CONTROL4_POWER_STATE]:
            return STATE_ON

        if self._is_soft_on:
            return STATE_IDLE

        return STATE_OFF

    @property
    def source(self):
        """Get the current source."""
        current_source = self._get_current_playing_device_id()
        if not current_source or current_source not in self._sources:
            return None
        return self._sources[current_source].name

    @property
    def media_content_type(self):
        """Get current content type if available."""
        current_source = self._get_current_playing_device_id()
        if not current_source:
            return None
        if current_source == self._get_current_video_device_id():
            return MEDIA_TYPE_MOVIE
        return MEDIA_TYPE_MUSIC

    async def async_media_play_pause(self):
        """
        If possible, toggle the current play/pause state.

        Not every source supports play/pause.
        Unfortunately MediaPlayer capabilities are not dynamic,
        so we must determine if play/pause is supported here
        """
        if self._get_current_source_state():
            await super().async_media_play_pause()

    @property
    def source_list(self) -> list[str]:
        """Get the available source."""
        return [x.name for x in self._sources.values()]

    @property
    def volume_level(self):
        """Get the volume level."""
        return self.coordinator.data[self._idx][CONTROL4_VOLUME_STATE] / 100

    @property
    def is_volume_muted(self):
        """Check if the volume is muted."""
        return bool(self.coordinator.data[self._idx][CONTROL4_MUTED_STATE])

    async def async_select_source(self, source):
        """Select a new source."""
        for avail_source in self._sources.values():
            if avail_source.name == source:
                audio_only = _SourceType.VIDEO not in avail_source.source_type
                await self._create_api_object().setSource(
                    avail_source.id, audio_only=audio_only
                )
                break

        await self.coordinator.async_request_refresh()

    def turn_on(self):
        """Fake turn-on the room.  Actual power on occurs during source select."""
        # We dont have any information about the previously selected source so we cannot "turn on" the room
        # However, we need to trick HA into thinking the room is on in order to display the source list.
        # Selecting a source will _actually_ turn on the system
        self._is_soft_on = True

    async def async_turn_off(self):
        """Turn off the room."""
        self._is_soft_on = False
        await self._create_api_object().setRoomOff()
        await self.coordinator.async_request_refresh()

    async def async_mute_volume(self, mute):
        """Mute the room."""
        await self._create_api_object().setMute(mute)
        await self.coordinator.async_request_refresh()

    async def async_set_volume_level(self, volume):
        """Set room volume, 0-1 scale."""
        await self._create_api_object().setVolume(int(volume * 100))
        await self.coordinator.async_request_refresh()

    async def async_volume_up(self):
        """Increase the volume by 1."""
        await self._create_api_object().setIncrementOrDecrementVolume(increase=True)
        await self.coordinator.async_request_refresh()

    async def async_volume_down(self):
        """Decrease the volume by 1."""
        await self._create_api_object().setIncrementOrDecrementVolume(increase=False)
        await self.coordinator.async_request_refresh()

    async def async_media_pause(self):
        """Issue a pause command."""
        await self._create_api_object().setPause()
        await self.coordinator.async_request_refresh()

    async def async_media_play(self):
        """Issue a play command."""
        await self._create_api_object().setPlay()
        await self.coordinator.async_request_refresh()
