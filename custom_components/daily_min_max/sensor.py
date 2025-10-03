import logging
from datetime import time as dtime

import voluptuous as vol
from homeassistant.components.sensor import (
    PLATFORM_SCHEMA,
    SensorEntity,
    RestoreSensor
)
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_NAME,
    CONF_TYPE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN
)
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change
)
from homeassistant.helpers.reload import async_setup_reload_service
from . import DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)

ATTR_MIN_VALUE = "min_value"
ATTR_MIN_ENTITY_ID = "min_entity_id"
ATTR_MAX_VALUE = "max_value"
ATTR_MAX_ENTITY_ID = "max_entity_id"
ATTR_COUNT_SENSORS = "count_sensors"
ATTR_LAST = "last"
ATTR_LAST_ENTITY_ID = "last_entity_id"

ATTR_TO_PROPERTY = [
    ATTR_COUNT_SENSORS,
    ATTR_MAX_VALUE,
    ATTR_MAX_ENTITY_ID,
    ATTR_MIN_VALUE,
    ATTR_MIN_ENTITY_ID,
    ATTR_LAST,
    ATTR_LAST_ENTITY_ID,
]

CONF_ENTITY_IDS = "entity_ids"
CONF_ROUND_DIGITS = "round_digits"
CONF_TIME = "time"
CONF_MANUAL_RESET_ONLY = "manual_reset_only"

ICON = "mdi:calculator"

SENSOR_TYPES = {
    ATTR_MIN_VALUE: "min",
    ATTR_MAX_VALUE: "max",
}

SERVICE_RESET = "reset"

PLATFORM_SCHEMA = vol.All(
    PLATFORM_SCHEMA.extend(
        {
            vol.Optional(CONF_TYPE, default=SENSOR_TYPES[ATTR_MAX_VALUE]): vol.All(
                cv.string, vol.In(SENSOR_TYPES.values())
            ),
            vol.Optional(CONF_NAME): cv.string,
            vol.Required(CONF_ENTITY_IDS): cv.entity_ids,
            vol.Optional(CONF_ROUND_DIGITS, default=2): vol.Coerce(int),
            vol.Optional(CONF_TIME, default="00:00:00"): cv.string,
            vol.Optional(CONF_MANUAL_RESET_ONLY, default=False): cv.boolean
        }
    )
)


def _calc_min(sensor_values):
    val, entity_id = None, None
    for sid, sval in sensor_values:
        if sval not in [STATE_UNKNOWN, STATE_UNAVAILABLE] and (
            val is None or val > sval
        ):
            entity_id, val = sid, sval
    return entity_id, val


def _calc_max(sensor_values):
    val, entity_id = None, None
    for sid, sval in sensor_values:
        if sval not in [STATE_UNKNOWN, STATE_UNAVAILABLE] and (
            val is None or val < sval
        ):
            entity_id, val = sid, sval
    return entity_id, val


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    entity_ids = config[CONF_ENTITY_IDS]
    name = config.get(CONF_NAME)
    sensor_type = config[CONF_TYPE]
    round_digits = config[CONF_ROUND_DIGITS]
    time_str = config[CONF_TIME]
    manual_reset_only = config[CONF_MANUAL_RESET_ONLY]

    reset_time = dtime.fromisoformat(time_str)

    await async_setup_reload_service(hass, DOMAIN, PLATFORMS)

    entity = DailyMinMaxSensor(
        entity_ids, name, sensor_type, round_digits, reset_time, manual_reset_only
    )
    async_add_entities([entity])

    hass.services.async_register(
        DOMAIN, SERVICE_RESET, entity.async_reset
    )


class DailyMinMaxSensor(RestoreSensor, SensorEntity):
    _attr_should_poll = False
    _attr_icon = ICON

    def __init__(self, entity_ids, name, sensor_type, round_digits, reset_time, manual_reset_only):
        self._entity_ids = entity_ids
        self._sensor_type = sensor_type
        self._reset_time = reset_time
        self._round_digits = round_digits
        self._manual_reset_only = manual_reset_only
        self._name = name or f"{sensor_type.capitalize()} sensor"
        self._unit_of_measurement = None
        self._unit_of_measurement_mismatch = False
        self.min_value = self.max_value = self.last = None
        self.min_entity_id = self.max_entity_id = self.last_entity_id = None
        self.count_sensors = len(entity_ids)
        self.states = {}

    @property
    def name(self):
        return self._name

    @property
    def native_value(self):
        if self._unit_of_measurement_mismatch:
            self._attr_available = False
            return None
        if self._sensor_type == "min":
            return self.min_value
        return self.max_value

    @property
    def native_unit_of_measurement(self):
        return self._unit_of_measurement

    @property
    def extra_state_attributes(self):
        return {
            attr: getattr(self, attr)
            for attr in ATTR_TO_PROPERTY
            if getattr(self, attr) is not None
        }

    async def async_added_to_hass(self):
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, self._entity_ids, self._async_sensor_state_listener
            )
        )
        self.async_on_remove(
            async_track_time_change(
                self.hass, self.reset, hour=[self._reset_time.hour],
                minute=[self._reset_time.minute],
                second=[self._reset_time.second]
            )
        )

        if (last_state := await self.async_get_last_state()):
            try:
                self.min_value = float(last_state.attributes.get("min_value"))
            except (TypeError, ValueError):
                pass
            try:
                self.max_value = float(last_state.attributes.get("max_value"))
            except (TypeError, ValueError):
                pass
            try:
                self.last = float(last_state.attributes.get("last"))
            except (TypeError, ValueError):
                pass
            self.min_entity_id = last_state.attributes.get("min_entity_id")
            self.max_entity_id = last_state.attributes.get("max_entity_id")
            self.last_entity_id = last_state.attributes.get("last_entity_id")

        self._calc_values()

    @callback
    def _async_sensor_state_listener(self, event):
        new_state = event.data.get("new_state")
        entity = event.data.get("entity_id")

        if not new_state or new_state.state in [STATE_UNKNOWN, STATE_UNAVAILABLE]:
            self.states[entity] = STATE_UNKNOWN
            self._calc_values()
            self.async_write_ha_state()
            return

        if self._unit_of_measurement is None:
            self._unit_of_measurement = new_state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)

        if self._unit_of_measurement != new_state.attributes.get(ATTR_UNIT_OF_MEASUREMENT):
            _LOGGER.warning("Unit mismatch for %s", self.entity_id)
            self._unit_of_measurement_mismatch = True
            self._attr_available = False
            return

        try:
            val = round(float(new_state.state), self._round_digits)
            self.states[entity] = val
            self.last = val
            self.last_entity_id = entity
        except ValueError:
            _LOGGER.warning("Non-numeric state for %s", entity)
            return

        self._calc_values()
        self.async_write_ha_state()

    @callback
    def _calc_values(self):
        sensor_values = [
            (eid, self.states[eid])
            for eid in self._entity_ids
            if eid in self.states
        ]
        min_id, min_val = _calc_min(sensor_values)
        max_id, max_val = _calc_max(sensor_values)

        if min_val is not None and (self.min_value is None or min_val < self.min_value):
            self.min_entity_id, self.min_value = min_id, min_val
        if max_val is not None and (self.max_value is None or max_val > self.max_value):
            self.max_entity_id, self.max_value = max_id, max_val

    @callback
    def reset(self, _now):
        if self._manual_reset_only:
            return
        self.min_value = self.max_value = self.last
        self.min_entity_id = self.max_entity_id = self.last_entity_id = None
        self.async_write_ha_state()

    async def async_reset(self, _call=None):
        _LOGGER.debug("Manual reset %s", self._name)
        self.reset(None)
