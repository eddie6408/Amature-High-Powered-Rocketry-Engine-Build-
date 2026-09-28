
import numpy as np
import pytest

from aerodyne.core import DataKind
from aerodyne.environment import (
    ConstantWind,
    GustWind,
    LayeredWind,
    PowerLawWind,
    ProfileAtmosphere,
    StandardAtmosphere,
    wind_vector,
)


@pytest.mark.parametrize("h,T,p,rho", [
    (0.0, 288.15, 101325.0, 1.2250),
    (11000.0, 216.77, 22700.0, 0.3648),
    (20000.0, 216.65, 5529.0, 0.0889),
])
def test_us1976_reference_values(h, T, p, rho):
    s = StandardAtmosphere().at(h)
    assert s.temperature == pytest.approx(T, abs=0.2)
    assert s.pressure == pytest.approx(p, rel=0.01)
    assert s.density == pytest.approx(rho, rel=0.01)
    assert s.kind == DataKind.ESTIMATED


def test_speed_of_sound_and_offsets():
    assert StandardAtmosphere().at(0).speed_of_sound == pytest.approx(340.29, abs=0.1)
    hot = StandardAtmosphere(temperature_offset=15).at(0)
    assert hot.density < 1.225


def test_baro_altitude_inversion():
    atm = StandardAtmosphere()
    for h in (0.0, 500.0, 3000.0, 15000.0):
        assert atm.altitude_from_pressure(atm.at(h).pressure) == pytest.approx(h, abs=1.0)


def test_profile_atmosphere_marks_measured_and_extends():
    prof = ProfileAtmosphere([0, 1000, 2000], [290, 284, 277], [100000, 88800, 78600])
    assert prof.at(500).kind == DataKind.MEASURED
    assert prof.at(500).temperature == pytest.approx(287.0)
    above = prof.at(5000)
    assert above.kind == DataKind.ESTIMATED and above.pressure < 78600


def test_wind_conventions():
    # wind FROM the west blows toward the east
    v = wind_vector(10, 270)
    assert v[0] == pytest.approx(10) and abs(v[1]) < 1e-9
    assert ConstantWind(5, 0).at(100, 0)[1] == pytest.approx(-5)
    pl = PowerLawWind(5, 270)
    assert np.linalg.norm(pl.at(100, 0)) > np.linalg.norm(pl.at(10, 0))
    lw = LayeredWind([0, 1000], [0, 10], [270, 270])
    assert lw.at(500, 0)[0] == pytest.approx(5)


def test_gust_wind_reproducible():
    a = GustWind(ConstantWind(3, 0), 1.0, seed=4)
    b = GustWind(ConstantWind(3, 0), 1.0, seed=4)
    for t in np.linspace(0, 10, 50):
        assert np.allclose(a.at(10, t), b.at(10, t))
