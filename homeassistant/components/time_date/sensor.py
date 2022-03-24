"""Support for showing the date and the time."""
from __future__ import annotations

from datetime import timedelta
import logging

import voluptuous as vol

from homeassistant.components.sensor import (
    DOMAIN as SENSOR_DOMAIN,
    PLATFORM_SCHEMA,
    SensorEntity,
)
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_DISPLAY_OPTIONS
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
import homeassistant.util.dt as dt_util

from .const import (
    CONF_BEAT,
    CONF_DATE,
    CONF_DATE_TIME,
    CONF_DATE_TIME_ISO,
    CONF_DATE_TIME_UTC,
    CONF_TIME,
    CONF_TIME_DATE,
    CONF_TIME_UTC,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

TIME_STR_FORMAT = "%H:%M"

OPTION_TYPES = {
    CONF_TIME: "Time",
    CONF_DATE: "Date",
    CONF_DATE_TIME: "Date & Time",
    CONF_DATE_TIME_UTC: "Date & Time (UTC)",
    CONF_DATE_TIME_ISO: "Date & Time (ISO)",
    CONF_TIME_DATE: "Time & Date",
    CONF_BEAT: "Internet Time",
    CONF_TIME_UTC: "Time (UTC)",
}

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_DISPLAY_OPTIONS, default=["time"]): vol.All(
            cv.ensure_list, [vol.In(OPTION_TYPES)]
        )
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up Time & Date sensors."""
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": SOURCE_IMPORT},
            data=config,
        )
    )


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Initialize Time & Date config entry."""
    if hass.config.time_zone is None:
        _LOGGER.error("Timezone is not set in Home Assistant configuration")
        return False

    unique_id = config_entry.entry_id
    registry = er.async_get(hass)
    enabled_options = [opt for opt in OPTION_TYPES if config_entry.options[opt]]
    disabled_options = [opt for opt in OPTION_TYPES if not config_entry.options[opt]]

    for option in disabled_options:
        entity_id = registry.async_get_entity_id(
            SENSOR_DOMAIN, DOMAIN, f"{unique_id}_{option}"
        )
        if entity_id:
            registry.async_remove(entity_id)

    async_add_entities(
        [
            TimeDateSensor(hass, option, f"{unique_id}_{option}")
            for option in enabled_options
        ]
    )


class TimeDateSensor(SensorEntity):
    """Implementation of a Time and Date sensor."""

    def __init__(self, hass, option_type, unique_id):
        """Initialize the sensor."""
        self._attr_unique_id = unique_id
        self._name = OPTION_TYPES[option_type]
        self.type = option_type
        self._state = None
        self.hass = hass
        self.unsub = None

        self._update_internal_state(dt_util.utcnow())

    @property
    def name(self):
        """Return the name of the sensor."""
        return self._name

    @property
    def native_value(self):
        """Return the state of the sensor."""
        return self._state

    @property
    def icon(self):
        """Icon to use in the frontend, if any."""
        if "date" in self.type and "time" in self.type:
            return "mdi:calendar-clock"
        if "date" in self.type:
            return "mdi:calendar"
        return "mdi:clock"

    async def async_added_to_hass(self) -> None:
        """Set up first update."""
        self.unsub = async_track_point_in_utc_time(
            self.hass, self.point_in_time_listener, self.get_next_interval()
        )

    async def async_will_remove_from_hass(self) -> None:
        """Cancel next update."""
        if self.unsub:
            self.unsub()
            self.unsub = None

    def get_next_interval(self):
        """Compute next time an update should occur."""
        now = dt_util.utcnow()

        if self.type == "date":
            tomorrow = dt_util.as_local(now) + timedelta(days=1)
            return dt_util.start_of_local_day(tomorrow)

        if self.type == "beat":
            # Add 1 hour because @0 beats is at 23:00:00 UTC.
            timestamp = dt_util.as_timestamp(now + timedelta(hours=1))
            interval = 86.4
        else:
            timestamp = dt_util.as_timestamp(now)
            interval = 60

        delta = interval - (timestamp % interval)
        next_interval = now + timedelta(seconds=delta)
        _LOGGER.debug("%s + %s -> %s (%s)", now, delta, next_interval, self.type)

        return next_interval

    def _update_internal_state(self, time_date):
        time = dt_util.as_local(time_date).strftime(TIME_STR_FORMAT)
        time_utc = time_date.strftime(TIME_STR_FORMAT)
        date = dt_util.as_local(time_date).date().isoformat()
        date_utc = time_date.date().isoformat()

        if self.type == "time":
            self._state = time
        elif self.type == "date":
            self._state = date
        elif self.type == "date_time":
            self._state = f"{date}, {time}"
        elif self.type == "date_time_utc":
            self._state = f"{date_utc}, {time_utc}"
        elif self.type == "time_date":
            self._state = f"{time}, {date}"
        elif self.type == "time_utc":
            self._state = time_utc
        elif self.type == "beat":
            # Calculate Swatch Internet Time.
            time_bmt = time_date + timedelta(hours=1)
            delta = timedelta(
                hours=time_bmt.hour,
                minutes=time_bmt.minute,
                seconds=time_bmt.second,
                microseconds=time_bmt.microsecond,
            )

            # Use integers to better handle rounding. For example,
            # int(63763.2/86.4) = 737 but 637632//864 = 738.
            beat = int(delta.total_seconds() * 10) // 864

            self._state = f"@{beat:03d}"
        elif self.type == "date_time_iso":
            self._state = dt_util.parse_datetime(f"{date} {time}").isoformat()

    @callback
    def point_in_time_listener(self, time_date):
        """Get the latest data and update state."""
        self._update_internal_state(time_date)
        self.async_write_ha_state()
        self.unsub = async_track_point_in_utc_time(
            self.hass, self.point_in_time_listener, self.get_next_interval()
        )
