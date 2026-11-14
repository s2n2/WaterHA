"""Config flow: one WaterHA hub, then each tap added as a subentry."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_AMOUNT,
    CONF_DAYS,
    CONF_ENABLED,
    CONF_FLOW_SENSOR,
    CONF_FORECAST_SOURCE,
    CONF_MAX_MINUTES,
    CONF_MODE,
    CONF_NAME,
    CONF_RAIN_THRESHOLD,
    CONF_SEQUENTIAL,
    CONF_START_TIME,
    CONF_TAP_ENTITY,
    CONF_WEATHER_ENTITY,
    DAYS,
    DEFAULT_AMOUNT_MINUTES,
    DEFAULT_MAX_MINUTES,
    DEFAULT_RAIN_THRESHOLD,
    DEFAULT_START_TIME,
    DOMAIN,
    MODE_LITRES,
    MODE_MINUTES,
    SOURCE_OPEN_METEO,
    SOURCE_WEATHER,
    SUBENTRY_TAP,
)


def _hub_schema(defaults: dict[str, Any]) -> vol.Schema:
    weather_default = defaults.get(CONF_WEATHER_ENTITY)
    return vol.Schema(
        {
            vol.Required(
                CONF_FORECAST_SOURCE, default=defaults.get(CONF_FORECAST_SOURCE, SOURCE_OPEN_METEO)
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[SOURCE_OPEN_METEO, SOURCE_WEATHER],
                    translation_key="forecast_source",
                )
            ),
            vol.Optional(
                CONF_WEATHER_ENTITY,
                description={"suggested_value": weather_default} if weather_default else None,
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="weather")),
            vol.Required(CONF_SEQUENTIAL, default=defaults.get(CONF_SEQUENTIAL, True)): bool,
        }
    )


def _hub_errors(data: dict[str, Any]) -> dict[str, str]:
    if data.get(CONF_FORECAST_SOURCE) == SOURCE_WEATHER and not data.get(CONF_WEATHER_ENTITY):
        return {CONF_WEATHER_ENTITY: "weather_required"}
    return {}


class WaterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up the WaterHA hub."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _hub_errors(user_input)
            if not errors:
                return self.async_create_entry(title="Garden watering", data={}, options=user_input)
        return self.async_show_form(
            step_id="user", data_schema=_hub_schema(user_input or {}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return WaterOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {SUBENTRY_TAP: TapSubentryFlow}


class WaterOptionsFlow(OptionsFlow):
    """Change the rain source and whether taps run one at a time."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _hub_errors(user_input)
            if not errors:
                return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=_hub_schema(user_input or dict(self.config_entry.options)),
            errors=errors,
        )


def _tap_schema(d: dict[str, Any]) -> vol.Schema:
    flow_default = d.get(CONF_FLOW_SENSOR)
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=d.get(CONF_NAME, "")): str,
            vol.Required(CONF_TAP_ENTITY, default=d.get(CONF_TAP_ENTITY, vol.UNDEFINED)): (
                selector.EntitySelector(selector.EntitySelectorConfig(domain=["switch", "valve"]))
            ),
            vol.Required(CONF_MODE, default=d.get(CONF_MODE, MODE_MINUTES)): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[MODE_MINUTES, MODE_LITRES],
                    translation_key="mode",
                    mode=selector.SelectSelectorMode.LIST,
                )
            ),
            vol.Required(CONF_AMOUNT, default=d.get(CONF_AMOUNT, DEFAULT_AMOUNT_MINUTES)): (
                selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=2000, step=1, mode=selector.NumberSelectorMode.BOX
                    )
                )
            ),
            vol.Optional(
                CONF_FLOW_SENSOR,
                description={"suggested_value": flow_default} if flow_default else None,
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="volume_flow_rate")
            ),
            vol.Required(CONF_START_TIME, default=d.get(CONF_START_TIME, DEFAULT_START_TIME)): (
                selector.TimeSelector()
            ),
            vol.Required(CONF_DAYS, default=d.get(CONF_DAYS, list(DAYS))): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=list(DAYS), multiple=True, translation_key="days"
                )
            ),
            vol.Required(
                CONF_RAIN_THRESHOLD, default=d.get(CONF_RAIN_THRESHOLD, DEFAULT_RAIN_THRESHOLD)
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=0, max=200, step=0.5, unit_of_measurement="mm",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
            vol.Required(CONF_MAX_MINUTES, default=d.get(CONF_MAX_MINUTES, DEFAULT_MAX_MINUTES)): (
                selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=240, step=1, unit_of_measurement="min",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                )
            ),
            vol.Required(CONF_ENABLED, default=d.get(CONF_ENABLED, True)): bool,
        }
    )


class TapSubentryFlow(ConfigSubentryFlow):
    """Add or change one automated tap."""

    def _errors(self, data: dict[str, Any], current_id: str | None = None) -> dict[str, str]:
        if data.get(CONF_MODE) == MODE_LITRES and not data.get(CONF_FLOW_SENSOR):
            return {CONF_FLOW_SENSOR: "flow_required"}
        if not data.get(CONF_DAYS):
            return {CONF_DAYS: "days_required"}
        for sid, sub in self._get_entry().subentries.items():
            if sid != current_id and sub.data.get(CONF_TAP_ENTITY) == data.get(CONF_TAP_ENTITY):
                return {CONF_TAP_ENTITY: "already_added"}
        return {}

    @staticmethod
    def _clean(data: dict[str, Any]) -> dict[str, Any]:
        data = dict(data)
        data[CONF_AMOUNT] = float(data[CONF_AMOUNT])
        data[CONF_RAIN_THRESHOLD] = float(data[CONF_RAIN_THRESHOLD])
        data[CONF_MAX_MINUTES] = int(data[CONF_MAX_MINUTES])
        data[CONF_DAYS] = [d for d in DAYS if d in data[CONF_DAYS]]
        if not data.get(CONF_FLOW_SENSOR):
            data.pop(CONF_FLOW_SENSOR, None)
        return data

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = self._errors(user_input)
            if not errors:
                data = self._clean(user_input)
                return self.async_create_entry(title=data[CONF_NAME], data=data)
        return self.async_show_form(
            step_id="user", data_schema=_tap_schema(user_input or {}), errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = self._errors(user_input, subentry.subentry_id)
            if not errors:
                data = self._clean(user_input)
                return self.async_update_and_abort(
                    self._get_entry(), subentry, title=data[CONF_NAME], data=data
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_tap_schema(user_input or dict(subentry.data)),
            errors=errors,
        )
