import pytest

from sysbench_devices.api_keys import create_api_key, resolve_attribution, verify_secret
from sysbench_devices.errors import AuthenticationError
from sysbench_devices.models import ApiKeyRecord, DeviceRegistration
from sysbench_devices.registry import RegistryData, RegistryStore


def test_registry_round_trip_with_api_keys(tmp_path):
    path = tmp_path / "registry.toml"
    created = create_api_key("autograder", "CS140E autograder")
    store = RegistryStore(path)

    store.save(
        RegistryData(
            devices=(DeviceRegistration(id="a4c91f2b", name="nexys-a7-01", tags=("fpga", "uart")),),
            api_keys=(created.record,),
        )
    )

    loaded = store.load()
    assert loaded.devices[0].id == "a4c91f2b"
    assert loaded.devices[0].tags == ("fpga", "uart")
    assert loaded.api_keys[0].id == "autograder"
    assert loaded.api_keys[0].key_hash != created.secret
    assert verify_secret(created.secret, loaded.api_keys[0].key_hash)


def test_resolve_attribution_rejects_revoked_key():
    created = create_api_key("worker", "Worker")
    revoked = type(created.record)(
        id=created.record.id,
        label=created.record.label,
        key_hash=created.record.key_hash,
        revoked=True,
    )

    try:
        resolve_attribution(created.secret, (revoked,))
    except AuthenticationError:
        pass
    else:
        raise AssertionError("expected revoked key to be rejected")


def test_admin_api_key_id_is_reserved(tmp_path):
    store = RegistryStore(tmp_path / "registry.toml")

    with pytest.raises(Exception, match="admin"):
        store.save(
            RegistryData(
                api_keys=(
                    ApiKeyRecord(
                        id="admin",
                        label="Bad",
                        key_hash="pbkdf2_sha256$1$c2FsdA==$ZGlnZXN0",
                    ),
                )
            )
        )
