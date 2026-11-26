#!/usr/bin/python
# -*- coding: UTF-8

# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/. */

# Authors:
# Michael Berg-Mohnicke <michael.berg@zalf.de>
#
# Maintainers:
# Currently maintained by the authors.
#
# This file has been created at the Institute of
# Landscape Systems Analysis at the ZALF.
# Copyright (C: Leibniz Centre for Agricultural Landscape Research (ZALF)

import csv
from math import floor
import json
import numpy as np
from pathlib import Path
from pyproj import CRS, Transformer
import sqlite3
import sys
import zmq
from uuid import uuid4

import monica_io3
import it_soil_io3
import monica_run_lib as Mrunlib

PATHS = {
    "re-local-remote": {
        "path-to-climate-dir": "/monica_data/climate-data/",
        "monica-path-to-climate-dir": "/monica_data/climate-data/",
        "path-to-data-dir": "./data/",
        "path-debug-write-folder": "./debug-out/",
    },
    "remoteProducer-remoteMonica": {
        "path-to-climate-dir": "/data/",
        "monica-path-to-climate-dir": "/monica_data/climate-data/",
        "path-to-data-dir": "./data/",
        "path-debug-write-folder": "/out/debug-out/",
    },
}

DATA_SOIL_DB = "italy/it_soil_1000.sqlite"
DATA_GRID_SOIL = "italy/it_soil_1000_32633_etrs89-utm33n.asc"
DATA_GRID_HEIGHT = "italy/it_dem_1000_32633_etrs89-utm33n.asc"
DATA_GRID_SLOPE = "italy/it_slope_1000_32633_etrs89-utm33n.asc"
DATA_GRID_CROPS = "italy/it_crop_1000_32633_etrs89-utm33n.asc"

TEMPLATE_PATH_LATLON = "{path_to_climate_dir}/latlon_to_rowcol.json"
# TEMPLATE_PATH_LATLON = "data/latlon_to_rowcol.json"
TEMPLATE_PATH_CLIMATE_CSV = "{gcm}/{rcm}/{scenario}/{ensmem}/{version}/row-{crow}/col-{ccol}.csv.gz"

STAGE_COLUMNS = {"StageTemperatureSum": 0, "StageTemperatureSum1": 0}
STAGE_COLUMNS.update({f"StageTemperatureSum{stage}": stage - 1 for stage in range(2, 8)})
IRRIGATION_COLUMNS = {"AutomaticIrrigationThreshold": "threshold", "AutomaticIrrigationAmount": "amount"}
SOIL_COLUMNS = {"SOM_FastDecCoeffStandard", "SOM_SlowDecCoeffStandard"}
SENSITIVITY_COLUMNS = {
    "CriticalTemperatureHeatStress", "LimitingTemperatureHeatStress", "PartBiologicalNFixation",
    *STAGE_COLUMNS, *IRRIGATION_COLUMNS, *SOIL_COLUMNS,
}


def read_setups(path):
    with open(path, newline="", encoding="utf-8-sig") as stream:
        dialect = csv.Sniffer().sniff(stream.readline(), delimiters=",;\t")
        stream.seek(0)
        reader = csv.DictReader(stream, dialect=dialect, restval="")
        setups = {}
        booleans = {"true": True, "false": False}
        for row in reader:
            setup_id = int(row["run-id"])
            setups[setup_id] = {
                key: booleans.get(value.strip().casefold(), value.strip())
                for key, value in row.items()
            }
        return setups


def sensitivity_override(setup):
    selected = [
        name for name in sorted(SENSITIVITY_COLUMNS)
        if str(setup.get(name, "")).strip()
    ]
    overrides = []
    for name in selected:
        value = float(str(setup[name].strip()))
        overrides.append((name, value))
    return overrides


def set_parameter(params, key, value):
    previous = params.get(key)
    params[key] = [value, *previous[1:]] if isinstance(previous, list) else value


def apply_sensitivity_override(env, override):
    name, value = override
    applied = False
    for method in env["cropRotation"]:
        has_tomato = False
        for step in method["worksteps"]:
            crop_params = step.get("crop", {}).get("cropParams", {})
            if not crop_params:
                continue
            species = crop_params["species"]
            cultivar = crop_params["cultivar"]
            species_name = species["SpeciesName"].casefold()
            if species_name == "field pea" and name == "PartBiologicalNFixation":
                set_parameter(species, name, value)
                applied = True
            if species_name != "tomato":
                continue
            has_tomato = True
            if name == "CriticalTemperatureHeatStress":
                set_parameter(cultivar, name, value)
                applied = True
            elif name == "LimitingTemperatureHeatStress":
                set_parameter(species, name, value)
                applied = True
            elif name in STAGE_COLUMNS:
                sums = cultivar["StageTemperatureSum"]
                values = sums[0] if isinstance(sums[0], list) else sums
                index = STAGE_COLUMNS[name]
                values[index] = value
                applied = True

        if has_tomato and name in IRRIGATION_COLUMNS:
            for step in method["worksteps"]:
                if step.get("type") != "AutomaticIrrigation":
                    continue
                parameters = step["parameters"]
                key = IRRIGATION_COLUMNS[name]
                alternate = "trigger_if_nFC_below_%" if key == "threshold" else "set_to_%nFC"
                parameters.pop(alternate, None)
                if key == "threshold":
                    parameters[key] = value
                else:
                    set_parameter(parameters, key, value)
                applied = True

    if name in SOIL_COLUMNS:
        set_parameter(env["params"]["userSoilOrganicParameters"], name, value)
        applied = True
    if not applied:
        raise ValueError(f"{name} has no matching crop or enabled AutomaticIrrigation workstep in this rotation")


def read_coordinates(raw):
    points = json.loads(raw)
    coordinates = []
    for lat, lon in points:
        coordinates.append((lat, lon))
    return coordinates


def select_cells(coordinates, metadata, transformer):
    nrows, ncols = int(metadata["nrows"]), int(metadata["ncols"])
    size = metadata["cellsize"]
    xll, yll = metadata["xllcorner"], metadata["yllcorner"]
    cells = {}
    for lat, lon in coordinates:
        east, north = transformer.transform(lon, lat)
        col = floor((east - xll) / size)
        row = nrows - 1 - floor((north - yll) / size)
        cells.setdefault((row, col), (lat, lon))
    return cells


# commandline parameters e.g "server=localhost port=6666 shared_id=2"
def run_producer(server={"server": None, "port": None}, shared_id=None):
    config = {
        "mode": "re-local-remote",
        "server-port": server["port"] if server["port"] else "6667",
        "server": server["server"] if server["server"] else "login01.cluster.zalf.de",
        "sim.json": "sim_projection.json",
        "crop.json": "crop_projection_N80_cover.json",
        "site.json": "site_projection.json",
        "setups-file": "sim_setups_sensitivity.csv",
        "coordinates": "",
        "run-setups": "[1]",
        "shared_id": shared_id
    }

    # read commandline args only if script is invoked directly from commandline
    if len(sys.argv) > 1 and __name__ == "__main__":
        for arg in sys.argv[1:]:
            k, sep, v = arg.partition("=")
            if not sep or k not in config:
                raise ValueError(f"Unknown producer option {arg!r}. Use key=value.")
            config[k] = v

    print("config:", config)

    paths = PATHS[config["mode"]]

    # read setup from csv file
    setups = read_setups(config["setups-file"])
    rs_ranges = config["run-setups"][1:-1].split(",")
    run_setups = []
    for rsr in rs_ranges:
        rs_r = rsr.split("-")
        if 1 < len(rs_r) <= 2:
            run_setups.extend(range(int(rs_r[0]), int(rs_r[1])+1))
        elif len(rs_r) == 1:
            run_setups.append(int(rs_r[0]))
    print("read sim setups: ", config["setups-file"])

    wgs84_crs = CRS.from_epsg(4326)
    soil_data_dir = paths["path-to-data-dir"]
    path_to_soil_grid = soil_data_dir + DATA_GRID_SOIL
    soil_epsg_code = int(path_to_soil_grid.split("/")[-1].split("_")[3])
    soil_crs = CRS.from_epsg(soil_epsg_code)
    to_soil_grid = Transformer.from_crs(wgs84_crs, soil_crs, always_xy=True)
    soil_metadata, _ = Mrunlib.read_header(path_to_soil_grid)
    soil_grid = np.loadtxt(path_to_soil_grid, dtype=int, skiprows=6)
    print("read: ", path_to_soil_grid)

    # Prepare all selected setups before submitting environments.
    selections = {}
    climate_root = Path(paths["path-to-climate-dir"])
    climate_interpolators = {}
    crop_grid = None
    for setup_id in run_setups:
        setup = setups[setup_id]
        try:
            overrides = sensitivity_override(setup)
            raw_coordinates = setup.get("coordinates") or config["coordinates"]
            if not raw_coordinates:
                raise ValueError("Supply coordinates in the setup CSV or with coordinates=")
            coordinates = read_coordinates(raw_coordinates)
            if not coordinates:
                raise ValueError("Supply at least one coordinate pair")
            cells = select_cells(coordinates, soil_metadata, to_soil_grid)
            check_landcover = setup.get("landcover") is True
            if check_landcover and crop_grid is None:
                path_to_crop_grid = soil_data_dir + DATA_GRID_CROPS
                crop_grid = np.loadtxt(path_to_crop_grid, dtype=int, skiprows=6)
                print("read: ", path_to_crop_grid)
            climate_dir = climate_root / setup.get("climate_path_to_latlon_file", "")
            climate_map = Path(TEMPLATE_PATH_LATLON.format(path_to_climate_dir=climate_dir))
            if climate_map not in climate_interpolators:
                climate_interpolators[climate_map] = Mrunlib.create_climate_geoGrid_interpolator_from_json_file(
                    climate_map, wgs84_crs, soil_crs, {}
                )
                print("created climate grid interpolator: ", climate_map)
            selections[setup_id] = (cells, overrides, climate_interpolators[climate_map])
        except ValueError as error:
            raise ValueError(f"run-id {setup_id}: {error}") from error

    coordinate_batch = {
        "batch_id": uuid4().hex,
        "batch_size": sum(len(cells) for cells, _, _ in selections.values()),
    }

    soil_crs_to_x_transformers = {wgs84_crs: Transformer.from_crs(soil_crs, wgs84_crs)}

    # height data
    path_to_dem_grid = paths["path-to-data-dir"] + DATA_GRID_HEIGHT
    dem_epsg_code = int(path_to_dem_grid.split("/")[-1].split("_")[3])
    dem_crs = CRS.from_epsg(dem_epsg_code)
    if dem_crs not in soil_crs_to_x_transformers:
        soil_crs_to_x_transformers[dem_crs] = Transformer.from_crs(soil_crs, dem_crs)
    dem_metadata, _ = Mrunlib.read_header(path_to_dem_grid)
    dem_grid = np.loadtxt(path_to_dem_grid, dtype=float, skiprows=6)
    dem_interpolate = Mrunlib.create_ascii_grid_interpolator(dem_grid, dem_metadata)
    print("read: ", path_to_dem_grid)

    # slope data
    path_to_slope_grid = paths["path-to-data-dir"] + DATA_GRID_SLOPE
    slope_epsg_code = int(path_to_slope_grid.split("/")[-1].split("_")[3])
    slope_crs = CRS.from_epsg(slope_epsg_code)
    if slope_crs not in soil_crs_to_x_transformers:
        soil_crs_to_x_transformers[slope_crs] = Transformer.from_crs(soil_crs, slope_crs)
    slope_metadata, _ = Mrunlib.read_header(path_to_slope_grid)
    slope_grid = np.loadtxt(path_to_slope_grid, dtype=float, skiprows=6)
    slope_interpolate = Mrunlib.create_ascii_grid_interpolator(slope_grid, slope_metadata)
    print("read: ", path_to_slope_grid)

    srows = int(soil_metadata["nrows"])
    scellsize = soil_metadata["cellsize"]
    xllcorner = soil_metadata["xllcorner"]
    yllcorner = soil_metadata["yllcorner"]

    soil_db_con = sqlite3.connect(soil_data_dir + DATA_SOIL_DB)

    context = zmq.Context()
    socket = context.socket(zmq.PUSH)  # pylint: disable=no-member
    socket.connect("tcp://" + config["server"] + ":" + str(config["server-port"]))

    sent_env_count = total_env_count = 0

    for setup_id, (cells, overrides, climate_data_interpolator) in selections.items():
        setup = setups[setup_id]
        gcm = setup["gcm"]
        rcm = setup["rcm"]
        scenario = setup["scenario"]
        ensmem = setup["ensmem"]
        version = setup["version"]
        crop_id = setup["crop-id"]

        # Read simulation template
        with open(setup.get("sim.json") or config["sim.json"], encoding="utf-8-sig") as _:
            sim_json = json.load(_)
        # change start and end date according to setup
        if setup["start_date"]:
            sim_json["climate.csv-options"]["start-date"] = str(setup["start_date"])
        if setup["end_date"]:
            sim_json["climate.csv-options"]["end-date"] = str(setup["end_date"])

        # read template site.json
        with open(setup.get("site.json") or config["site.json"], encoding="utf-8-sig") as _:
            site_json = json.load(_)

        climate_scenario = scenario

        # Map of SSP scenarios to corresponding RCP scenarios
        ssp_to_rcp = {
            "ssp119": "rcp19",
            "ssp126": "rcp26",
            "ssp245": "rcp45",
            "ssp460": "rcp60",
            "ssp370": "rcp70",
            "ssp585": "rcp85"
        }

        rcp_scenario = climate_scenario.lower()

        if rcp_scenario:
            # Convert SSP to RCP if needed
            if rcp_scenario in ssp_to_rcp:
                rcp_scenario = ssp_to_rcp[rcp_scenario]

            if rcp_scenario.startswith("rcp"):
                site_json["EnvironmentParameters"]["rcp"] = rcp_scenario

        # read template crop.json
        with open(setup.get("crop.json") or config["crop.json"], encoding="utf-8-sig") as _:
            crop_json = json.load(_)

        crop_json["CropParameters"]["__enable_vernalisation_factor_fix__"] = setup[
            "use_vernalisation_fix"] if "use_vernalisation_fix" in setup else False

        # set the current crop used for this run id
        crop_json["cropRotation"][2] = crop_id

        irrigate_crop = setup.get("irrigation")
        if isinstance(irrigate_crop, bool):
            rotation = crop_json["cropRotationTemplates"][crop_id]
            for cultivation_method in rotation:
                worksteps = cultivation_method["worksteps"]
                if irrigate_crop:
                    for workstep in worksteps:
                        if workstep.get("type") == "AutomaticIrrigation":
                            workstep["irrigateCrop"] = True
                else:
                    cultivation_method["worksteps"] = [workstep for workstep in worksteps
                                                       if workstep.get("type") != "AutomaticIrrigation"]

        # create environment template from json templates
        env_template = monica_io3.create_env_json_from_json_config({
            "crop": crop_json,
            "site": site_json,
            "sim": sim_json,
            "climate": ""
        })

        for override in overrides:
            apply_sensitivity_override(env_template, override)

        # set shared id in template
        if config["shared_id"]:
            env_template["sharedId"] = config["shared_id"]

        soil_id_cache = {}
        for (srow, scol), (latitude, longitude) in cells.items():
            soil_id = int(soil_grid[srow, scol])
            if soil_id in soil_id_cache:
                soil_profile = soil_id_cache[soil_id]
            else:
                soil_profile = it_soil_io3.soil_parameters(soil_db_con, soil_id)
                soil_id_cache[soil_id] = soil_profile

            if not soil_profile:
                raise ValueError(f"run-id {setup_id}: No soil profile for soil ID {soil_id} "
                                 f"at row {srow}, column {scol}")

            # get coordinate of closest climate element of real soil-cell
            sh = yllcorner + (scellsize / 2) + (srows - srow - 1) * scellsize
            sr = xllcorner + (scellsize / 2) + scol * scellsize

            crow, ccol = climate_data_interpolator(sr, sh)

            slat, _ = soil_crs_to_x_transformers[wgs84_crs].transform(sr, sh)

            tcoords = {}

            if dem_crs not in tcoords:
                tcoords[dem_crs] = soil_crs_to_x_transformers[dem_crs].transform(sr, sh)
            demr, demh = tcoords[dem_crs]
            height_nn = dem_interpolate(demr, demh)

            if slope_crs not in tcoords:
                tcoords[slope_crs] = soil_crs_to_x_transformers[slope_crs].transform(sr, sh)
            slr, slh = tcoords[slope_crs]
            slope = slope_interpolate(slr, slh)

            env_template["params"]["userCropParameters"]["__enable_T_response_leaf_expansion__"] = setup[
                "LeafExtensionModifier"]

            env_template["params"]["siteParameters"]["SoilProfileParameters"] = soil_profile

            if setup["elevation"]:
                env_template["params"]["siteParameters"]["heightNN"] = float(height_nn)

            if setup["slope"]:
                env_template["params"]["siteParameters"]["slope"] = float(slope)

            if setup["latitude"]:
                env_template["params"]["siteParameters"]["Latitude"] = float(slat)

            env_template["params"]["simulationParameters"]["UseNMinMineralFertilisingMethod"] = setup["fertilization"]
            env_template["params"]["simulationParameters"]["NitrogenResponseOn"] = setup["NitrogenResponseOn"]
            env_template["params"]["simulationParameters"]["WaterDeficitResponseOn"] = setup["WaterDeficitResponseOn"]
            env_template["params"]["simulationParameters"]["EmergenceMoistureControlOn"] = setup[
                "EmergenceMoistureControlOn"]
            env_template["params"]["simulationParameters"]["EmergenceFloodingControlOn"] = setup[
                "EmergenceFloodingControlOn"]

            env_template["csvViaHeaderOptions"] = sim_json["climate.csv-options"]
            if setup["latitude"]:
                env_template["csvViaHeaderOptions"]["latitude"] = float(slat)

            subpath_to_csv = TEMPLATE_PATH_CLIMATE_CSV.format(gcm=gcm, rcm=rcm, scenario=climate_scenario, ensmem=ensmem,
                                                              version=version, crow=str(int(crow)), ccol=str(int(ccol)))
            for _ in range(4):
                subpath_to_csv = subpath_to_csv.replace("//", "/")

            env_template["pathToClimateCSV"] = [(paths["monica-path-to-climate-dir"]).rstrip("/\\")
                                                + "/" + setup["climate_path_to_csvs"] + "/" + subpath_to_csv]

            env_template["customId"] = {
                "setup_id": setup_id,
                "srow": srow, "scol": scol,
                "crow": int(crow), "ccol": int(ccol),
                "soil_id": soil_id,
                "env_id": sent_env_count,
                "nodata": False,
                "latitude": latitude,
                "longitude": longitude,
                **coordinate_batch,
            }

            socket.send_json(env_template)
            sent_env_count += 1

        total_env_count += sent_env_count
        sent_env_count = 0

    print("exiting run_producer()")
    soil_db_con.close()
    socket.close()
    context.term()


if __name__ == "__main__":
    run_producer()
