"""Real HA objects with a simulated device; no Bluetooth hardware required."""
from types import MappingProxyType
import pytest
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant import loader
from homeassistant.helpers import device_registry, frame


@pytest.fixture
async def hass(tmp_path):
    instance = HomeAssistant(str(tmp_path))
    loader.async_setup(instance)
    frame.async_setup(instance)
    device_registry.async_setup(instance)
    instance.config_entries = ConfigEntries(instance, {})
    await instance.config_entries.async_initialize()
    yield instance
    await instance.async_stop(force=True)
    instance.import_executor.shutdown(wait=True)


@pytest.fixture
def entry():
    return ConfigEntry(
        domain='catch_control', title='Test Catch', version=2, minor_version=1,
        data={'address': 'AA:BB:CC:DD:EE:FF', 'transport': 'bluetooth', 'serial': 4242, 'firmware': 12718},
        options={'password': 'test-password', 'scan_interval': 30},
        source='user', unique_id='10004-4242',
        discovery_keys=MappingProxyType({}), subentries_data=None,
    )
