-- AERODYNE engineering database (PostgreSQL 14+).
--
-- Every object carries: id, version, created_at, updated_at, author, source, status.
-- Large arrays (telemetry, simulation time series, raw logs) live in external
-- object storage; rows hold the URI and SHA-256 of the file (never destroyed).
-- Flown configurations are immutable: see the triggers at the end.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE record_status AS ENUM ('DRAFT', 'ACTIVE', 'FROZEN', 'FLOWN', 'RETIRED');
CREATE TYPE data_kind     AS ENUM ('MEASURED', 'SIMULATED', 'ESTIMATED', 'DERIVED', 'HYPOTHETICAL');
CREATE TYPE data_quality  AS ENUM ('CERTIFIED', 'MANUFACTURER', 'MEASURED', 'ESTIMATED',
                                   'HYPOTHETICAL', 'UNKNOWN');

-- Common columns, repeated per table (PostgreSQL has no mixins; kept explicit on purpose).
-- id uuid PK, version int, created_at, updated_at, author, source, status

CREATE TABLE projects (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'DRAFT',
    name text NOT NULL UNIQUE,
    description text
);

CREATE TABLE materials (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'ACTIVE',
    name text NOT NULL,
    density_kg_m3 double precision NOT NULL CHECK (density_kg_m3 > 0),
    tensile_strength_pa double precision,
    youngs_modulus_pa double precision,
    data_quality data_quality NOT NULL DEFAULT 'UNKNOWN',
    UNIQUE (name, version)
);

CREATE TABLE vehicles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'ACTIVE',
    project_id uuid NOT NULL REFERENCES projects(id),
    vehicle_code text NOT NULL UNIQUE CHECK (vehicle_code ~ '^[A-Z0-9]+-[0-9]{3,}$'),  -- AERODYNE-001
    name text NOT NULL
);

CREATE TABLE vehicle_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'DRAFT',
    vehicle_id uuid NOT NULL REFERENCES vehicles(id),
    revision text NOT NULL CHECK (revision ~ '^REV-[A-Z]+$'),                        -- REV-A
    parent_id uuid REFERENCES vehicle_versions(id),
    change_note text NOT NULL,
    digital_twin jsonb NOT NULL,          -- canonical twin (geometry, mass, motor ref, aero, avionics, recovery)
    config_hash char(64) NOT NULL,        -- SHA-256 of canonical twin JSON
    UNIQUE (vehicle_id, revision)
);

CREATE TABLE components (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'DRAFT',
    vehicle_version_id uuid NOT NULL REFERENCES vehicle_versions(id),
    component_type text NOT NULL,         -- NoseCone, BodyTube, FinSet, ...
    name text NOT NULL,
    station_m double precision NOT NULL,
    parameters jsonb NOT NULL,
    material_id uuid REFERENCES materials(id),
    mass_kg double precision,
    mass_kind data_kind NOT NULL,         -- MEASURED (weighed) vs ESTIMATED (geometry)
    cg_m double precision,
    cad_reference text                    -- STEP/Fusion/SolidWorks document + part ID
);

CREATE TABLE motors (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'ACTIVE',
    manufacturer text NOT NULL,
    designation text NOT NULL,
    classification text,
    diameter_mm double precision,
    length_mm double precision,
    certification text,
    UNIQUE (manufacturer, designation)
);

-- Multiple datasets per motor are kept side by side, never merged.
CREATE TABLE motor_performance (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'ACTIVE',
    motor_id uuid NOT NULL REFERENCES motors(id),
    source_date date,
    data_quality data_quality NOT NULL,
    total_impulse_ns double precision NOT NULL,
    burn_time_s double precision NOT NULL,
    total_mass_kg double precision NOT NULL,
    propellant_mass_kg double precision,
    time_s double precision[] NOT NULL,
    thrust_n double precision[] NOT NULL,
    thrust_uncertainty_rel double precision,
    data_hash char(64) NOT NULL UNIQUE,
    CHECK (cardinality(time_s) = cardinality(thrust_n))
);

CREATE TABLE aerodynamic_models (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'ACTIVE',
    vehicle_version_id uuid NOT NULL REFERENCES vehicle_versions(id),
    model_type text NOT NULL,             -- analytical | rasaero | openrocket | cfd | wind_tunnel
    kind data_kind NOT NULL,
    tool text,                            -- e.g. "OpenFOAM v2406", "RASAero II 1.0.2"
    table_uri text, table_sha256 char(64),
    parameters jsonb
);

CREATE TABLE environmental_conditions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'ACTIVE',
    observed_at timestamptz,
    kind data_kind NOT NULL,              -- MEASURED sounding vs ESTIMATED forecast
    site_name text, latitude double precision, longitude double precision, altitude_msl_m double precision,
    surface_temperature_k double precision, surface_pressure_pa double precision,
    relative_humidity double precision,
    wind_profile jsonb,                   -- [{alt_agl, speed, from_deg}]
    atmosphere_profile jsonb
);

CREATE TABLE simulation_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'ACTIVE',
    vehicle_version_id uuid NOT NULL REFERENCES vehicle_versions(id),
    motor_performance_id uuid NOT NULL REFERENCES motor_performance(id),
    aerodynamic_model_id uuid REFERENCES aerodynamic_models(id),
    environment_id uuid REFERENCES environmental_conditions(id),
    run_type text NOT NULL,               -- nominal | monte_carlo | sil | hil | calibration
    software_version text NOT NULL,
    software_commit text,
    kind data_kind NOT NULL DEFAULT 'SIMULATED',
    summary jsonb NOT NULL,
    timeseries_uri text, timeseries_sha256 char(64)
);

CREATE TABLE simulation_parameters (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'ACTIVE',
    simulation_run_id uuid NOT NULL REFERENCES simulation_runs(id) ON DELETE RESTRICT,
    name text NOT NULL,
    value jsonb NOT NULL,
    distribution jsonb,                   -- Monte Carlo sampling definition
    UNIQUE (simulation_run_id, name)
);

CREATE TABLE hardware_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'ACTIVE',
    code text NOT NULL UNIQUE,            -- FC-HW-001
    schematic_uri text, bom jsonb
);

CREATE TABLE firmware_versions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'ACTIVE',
    code text NOT NULL,                   -- FW-1.0.0
    build_timestamp timestamptz NOT NULL,
    commit_hash text NOT NULL,
    config_hash char(64) NOT NULL,
    firmware_hash char(64) NOT NULL UNIQUE,
    image_uri text
);

CREATE TABLE flight_computers (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'ACTIVE',
    serial_number text NOT NULL UNIQUE,
    hardware_version_id uuid NOT NULL REFERENCES hardware_versions(id)
);

CREATE TABLE sensor_configurations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'ACTIVE',
    code text NOT NULL UNIQUE,            -- SENSOR-CONFIG-001
    sensors jsonb NOT NULL,               -- part numbers, buses, rates, ranges, calibration
    config_hash char(64) NOT NULL
);

CREATE TABLE recovery_configurations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'DRAFT',
    vehicle_version_id uuid NOT NULL REFERENCES vehicle_versions(id),
    devices jsonb NOT NULL                -- [{name, cd, diameter, deploy_event, altitude, delay}]
);

CREATE TABLE flights (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'FLOWN',
    flight_code text NOT NULL UNIQUE,
    flown_at timestamptz NOT NULL,
    vehicle_version_id uuid NOT NULL REFERENCES vehicle_versions(id),
    motor_performance_id uuid REFERENCES motor_performance(id),
    flight_computer_id uuid REFERENCES flight_computers(id),
    firmware_version_id uuid NOT NULL REFERENCES firmware_versions(id),
    sensor_configuration_id uuid NOT NULL REFERENCES sensor_configurations(id),
    recovery_configuration_id uuid REFERENCES recovery_configurations(id),
    environment_id uuid REFERENCES environmental_conditions(id),
    predicted_simulation_id uuid REFERENCES simulation_runs(id),
    telemetry_protocol text NOT NULL,     -- TELEMETRY-2
    flight_configuration jsonb NOT NULL,  -- exact FlightConfiguration record
    flight_configuration_hash char(64) NOT NULL,
    outcome text
);

CREATE TABLE telemetry_sessions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'ACTIVE',
    flight_id uuid REFERENCES flights(id),
    ground_station text NOT NULL,
    started_at timestamptz NOT NULL, ended_at timestamptz,
    link_stats jsonb,                     -- received / lost / crc / duplicates / out_of_order
    raw_capture_uri text NOT NULL, raw_capture_sha256 char(64) NOT NULL
);

CREATE TABLE flight_data (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'ACTIVE',
    flight_id uuid NOT NULL REFERENCES flights(id),
    dataset_type text NOT NULL,           -- raw_log | telemetry | gnss | environment | reconstruction
    kind data_kind NOT NULL,
    uri text NOT NULL, sha256 char(64) NOT NULL,
    derived_from uuid REFERENCES flight_data(id),
    summary jsonb
);

CREATE TABLE test_runs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'ACTIVE',
    test_type text NOT NULL,              -- static_motor_characterization | sil | hil | ground | structural
    performed_at timestamptz,
    subject jsonb NOT NULL,
    raw_data_uri text, raw_data_sha256 char(64)
);

CREATE TABLE test_results (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'ACTIVE',
    test_run_id uuid NOT NULL REFERENCES test_runs(id),
    kind data_kind NOT NULL,
    passed boolean,
    results jsonb NOT NULL
);

CREATE TABLE structural_cases (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'ACTIVE',
    vehicle_version_id uuid NOT NULL REFERENCES vehicle_versions(id),
    name text NOT NULL,
    load_type text NOT NULL CHECK (load_type IN ('acceleration', 'aerodynamic', 'recovery', 'landing')),
    kind data_kind NOT NULL,
    loads jsonb NOT NULL,
    fea_tool text,
    fea_results jsonb,                    -- [{component, max_stress_pa, allowable_pa, design_factor, margin}]
    simulation_run_id uuid REFERENCES simulation_runs(id)
);

CREATE TABLE design_iterations (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'DRAFT',
    from_version_id uuid REFERENCES vehicle_versions(id),
    to_version_id uuid REFERENCES vehicle_versions(id),
    motivated_by_flight_id uuid REFERENCES flights(id),
    summary text NOT NULL
);

CREATE TABLE engineering_decisions (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version int NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    author text NOT NULL, source text NOT NULL, status record_status NOT NULL DEFAULT 'ACTIVE',
    title text NOT NULL,
    context text NOT NULL,
    decision text NOT NULL,
    alternatives text,
    evidence jsonb,                       -- references to flights, tests, simulations
    design_iteration_id uuid REFERENCES design_iterations(id)
);

-- ---------------------------------------------------------------------------
-- Integrity rules
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END $$ LANGUAGE plpgsql;

-- A vehicle version that is FLOWN or FROZEN can never be edited or deleted.
CREATE OR REPLACE FUNCTION protect_released_version() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status IN ('FLOWN', 'FROZEN') THEN
            RAISE EXCEPTION 'vehicle version % is %; it cannot be deleted', OLD.revision, OLD.status;
        END IF;
        RETURN OLD;
    END IF;
    IF OLD.status = 'FLOWN' AND (NEW.digital_twin IS DISTINCT FROM OLD.digital_twin
                                 OR NEW.config_hash IS DISTINCT FROM OLD.config_hash
                                 OR NEW.status <> 'FLOWN') THEN
        RAISE EXCEPTION 'vehicle version % has flown; create a new revision', OLD.revision;
    END IF;
    IF OLD.status = 'FROZEN' AND NEW.digital_twin IS DISTINCT FROM OLD.digital_twin THEN
        RAISE EXCEPTION 'vehicle version % is frozen; create a new revision', OLD.revision;
    END IF;
    NEW.updated_at := now();
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER vehicle_versions_protect BEFORE UPDATE OR DELETE ON vehicle_versions
    FOR EACH ROW EXECUTE FUNCTION protect_released_version();

-- Recording a flight marks its vehicle version FLOWN.
CREATE OR REPLACE FUNCTION mark_version_flown() RETURNS trigger AS $$
BEGIN
    UPDATE vehicle_versions SET status = 'FLOWN' WHERE id = NEW.vehicle_version_id;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER flights_mark_flown AFTER INSERT ON flights
    FOR EACH ROW EXECUTE FUNCTION mark_version_flown();

-- Raw data is never destroyed: forbid deletes of raw measured datasets.
CREATE OR REPLACE FUNCTION protect_raw_data() RETURNS trigger AS $$
BEGIN
    IF OLD.kind = 'MEASURED' THEN
        RAISE EXCEPTION 'measured dataset % cannot be deleted', OLD.id;
    END IF;
    RETURN OLD;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER flight_data_protect BEFORE DELETE ON flight_data
    FOR EACH ROW EXECUTE FUNCTION protect_raw_data();

DO $$
DECLARE t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['projects','materials','vehicles','components','motors',
        'motor_performance','aerodynamic_models','environmental_conditions','simulation_runs',
        'simulation_parameters','hardware_versions','firmware_versions','flight_computers',
        'sensor_configurations','recovery_configurations','flights','telemetry_sessions',
        'flight_data','test_runs','test_results','structural_cases','design_iterations',
        'engineering_decisions']
    LOOP
        EXECUTE format('CREATE TRIGGER %I_touch BEFORE UPDATE ON %I FOR EACH ROW
                        EXECUTE FUNCTION touch_updated_at()', t, t);
    END LOOP;
END $$;

CREATE INDEX ON motor_performance (motor_id, data_quality);
CREATE INDEX ON simulation_runs (vehicle_version_id, run_type);
CREATE INDEX ON flight_data (flight_id, dataset_type);
CREATE INDEX ON vehicle_versions (vehicle_id);
