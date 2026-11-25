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

from collections import defaultdict
import csv
import numpy as np
import os
from pyproj import CRS
import sys
import zmq
import tarfile
import shutil

import monica_io3
import monica_run_lib as Mrunlib

PATHS = {
    "re-local-remote": {
        "path-to-data-dir": "./data/",
        "path-to-output-dir": "D:/Safe-H2O-Farm/out/out/",
        "path-to-csv-output-dir": "D:/Safe-H2O-Farm/out/csv-out/"
    },
    "mbm-local-remote": {
        "path-to-data-dir": "data/",
        "path-to-output-dir": "out/",
        "path-to-csv-output-dir": "csv-out/"
    },
    "remoteConsumer-remoteMonica": {
        "path-to-data-dir": "./data/",
        "path-to-output-dir": "/out/out/",
        "path-to-csv-output-dir": "/out/csv-out/"
    }
}

TEMPLATE_SOIL_PATH = "{local_path_to_data_dir}italy/it_soil_1000_32633_etrs89-utm33n.asc"
USE_LANDUSE = False


def create_output(msg):
    phenology_keys = ("DOY-3", "DOY-4", "DOY-5", "DOY-6", "DOY-7", "AutoHarvestDOY")
    crop_results = defaultdict(dict)
    phenology_results = defaultdict(dict)
    yearly_results = {}

    for section in msg.get("data", []):
        orig_spec = section.get("origSpec", "")
        for vals in section.get("results", []):
            if orig_spec == '"yearly"' and "Year" in vals:
                yearly_values = yearly_results.setdefault((None, vals["Year"]), {})
                yearly_values.update({key: value for key, value in vals.items() if key != "Year"})
            elif "CM-count" in vals:
                cm_count = vals["CM-count"]
                if orig_spec == '"crop"':
                    crop_results[cm_count].update(vals)
                else:
                    for key in phenology_keys:
                        if key in vals:
                            phenology_results[cm_count].setdefault(key, vals[key])

    for cm_count, vals in crop_results.items():
        if "Year" in vals:
            yearly_results[(cm_count, vals["Year"])] = {**vals, **phenology_results.get(cm_count, {})}

    return yearly_results


def write_row_to_grids(row_col_data, row, ncols, header, path_to_output_dir, path_to_csv_output_dir, setup_id, start_row=0):
    """write grids row by row"""

    if not hasattr(write_row_to_grids, "grid_files"):
        write_row_to_grids.grid_files = defaultdict(set)

    make_dict_nparr = lambda: defaultdict(lambda: np.full((ncols,), -9999, dtype=float))

    output_grids = {
        # "AbBiom": {"data": make_dict_nparr(), "cast-to": "float", "digits": 2},
        # "Yield": {"data": make_dict_nparr(), "cast-to": "float", "digits": 2},
        # "NLeach": {"data": make_dict_nparr(), "cast-to": "float", "digits": 2},
        # "NLeach-yearly": {"section": "yearly", "data": make_dict_nparr(), "cast-to": "float", "digits": 2},
        # "WDrain": {"data": make_dict_nparr(), "cast-to": "float", "digits": 2},
        # "WDrain-yearly": {"section": "yearly", "data": make_dict_nparr(), "cast-to": "float", "digits": 2},
        # "SumNUp": {"data": make_dict_nparr(), "cast-to": "float", "digits": 2},
        # "Irrig": {"data": make_dict_nparr(), "cast-to": "int", "digits": 0},
        # "SOC-avg3": {"data": make_dict_nparr(), "cast-to": "float", "digits": 4},
        # "SOC-sum3": {"data": make_dict_nparr(), "cast-to": "float", "digits": 4},
        # "SOC-X-Y": {"data": make_dict_nparr(), "cast-to": "float", "digits": 4},
        # "TempSum": {"data": make_dict_nparr(), "cast-to": "float", "digits": 2},
        # "Evapotranspiration": {"data": make_dict_nparr(), "cast-to": "float", "digits": 2},
        # "HeatRed": {"data": make_dict_nparr(), "cast-to": "float", "digits": 2},
        # "FrostRed": {"data": make_dict_nparr(), "cast-to": "float", "digits": 2},


        # "DOY-3": {"data": make_dict_nparr(), "cast-to": "int", "digits": 0},
        # "DOY-4": {"data": make_dict_nparr(), "cast-to": "int", "digits": 0},
        # "DOY-5": {"data": make_dict_nparr(), "cast-to": "int", "digits": 0},
        "DOY-6": {"data": make_dict_nparr(), "cast-to": "int", "digits": 0},
        # "DOY-7": {"data": make_dict_nparr(), "cast-to": "int", "digits": 0},
        # "AutoHarvestDOY": {"data": make_dict_nparr(), "cast-to": "int", "digits": 0},

        # "Precip": {"data": make_dict_nparr(), "cast-to": "float", "digits": 2},
        # "Tmin": {"data": make_dict_nparr(), "cast-to": "float", "digits": 2},
        # "Tavg": {"data": make_dict_nparr(), "cast-to": "float", "digits": 2s},
        # "Tmax": {"data": make_dict_nparr(), "cast-to": "float", "digits": 2},
        # "AtmCO2": {"data": make_dict_nparr(), "cast-to": "float", "digits": 2},

    }
    crop_output_keys = [key for key, grid in output_grids.items() if grid.get("section", "crop") == "crop"]
    yearly_output_keys = [key for key, grid in output_grids.items() if grid.get("section") == "yearly"]

    cmc_to_crop = {}

    # skip this part if we write just a nodata line
    if row in row_col_data:
        for col in range(0, ncols):
            if col in row_col_data[row]:
                rcd_val = row_col_data[row][col]
                if rcd_val == -9999:
                    continue
                else:
                    cmc_and_year_to_vals = defaultdict(lambda: defaultdict(list))
                    for cell_data in rcd_val:
                        # if we got multiple datasets per cell, iterate over them and aggregate them in the following step
                        for (cm_count, year), data in cell_data.items():
                            if cm_count is None:
                                for key in yearly_output_keys:
                                    value = data.get(key)
                                    if isinstance(value, list):
                                        for index, item in enumerate(value, start=1):
                                            if type(item) in (int, float):
                                                layer_key = f"{key}_{index}"
                                                if layer_key not in output_grids:
                                                    output_grids[layer_key] = {**output_grids[key], "data": make_dict_nparr()}
                                                cmc_and_year_to_vals[(None, year)][layer_key].append(item)
                                    elif type(value) in (int, float):
                                        cmc_and_year_to_vals[(None, year)][key].append(value)
                                continue

                            for key in crop_output_keys:
                                crop_name = str(data.get("Crop", "")).lower()

                                if "tomato" not in crop_name:
                                    continue
                                # store mapping cm_count to crop name for later file name creation
                                if cm_count not in cmc_to_crop and "Crop" in data:
                                    cmc_to_crop[cm_count] = data["Crop"]

                                # only further process/store data we actually received
                                if key in data:
                                    v = data[key]
                                    if isinstance(v, list):
                                        for i, v_ in enumerate(v):
                                            cmc_and_year_to_vals[(cm_count, year)][f'{key}_{i + 1}'].append(v_)
                                    else:
                                        cmc_and_year_to_vals[(cm_count, year)][key].append(v)
                                # if a key is missing, because that monica event was never raised/reached, create the empty list
                                # so a no-data value is being produced
                                else:
                                    cmc_and_year_to_vals[(cm_count, year)][key]

                    # potentially aggregate multiple data per cell and finally store them for this row
                    for (cm_count, year), key_to_vals in cmc_and_year_to_vals.items():
                        for key, vals in key_to_vals.items():
                            output_vals = output_grids[key]["data"]
                            if len(vals) > 0:
                                output_vals[(cm_count, year)][col] = sum(vals) / len(vals)
                            else:
                                output_vals[(cm_count, year)][col] = -9999

    grid_files = write_row_to_grids.grid_files[setup_id]
    written_files = set()
    nodata_line = " ".join(["-9999"] * ncols) + "\n"

    # iterate over all prepared data for a single row and write row
    for key, grid in output_grids.items():
        grid_key = key.replace("/", "_").replace("\\", "_")

        for (cm_count, year), row_arr in grid["data"].items():
            if cm_count is None:
                filename = f"{grid_key}_{year}.asc"
            else:
                crop = cmc_to_crop[cm_count].replace("/", "").replace(" ", "")
                filename = f"{crop}_{grid_key}_{year}_{cm_count}.asc"

            path_to_file = os.path.join(path_to_output_dir, filename)
            if path_to_file not in grid_files:
                with open(path_to_file, "w") as file_:
                    file_.write(header)
                    file_.write(nodata_line * (row - start_row))
                grid_files.add(path_to_file)

            if grid["cast-to"] == "int":
                row_values = [str(int(value)) for value in row_arr]
            else:
                row_values = ["-9999" if int(value) == -9999 else str(round(value, grid.get("digits", 0)))
                              for value in row_arr]

            with open(path_to_file, "a") as file_:
                file_.write(" ".join(row_values) + "\n")
            written_files.add(path_to_file)

    for path_to_file in grid_files - written_files:
        with open(path_to_file, "a") as file_:
            file_.write(nodata_line)

    if row in row_col_data:
        del row_col_data[row]


def run_consumer(leave_after_finished_run=True, server={"server": None, "port": None}, shared_id=None):
    """collect data from workers"""

    config = {
        "mode": "re-local-remote",  # "mbm-local-remote",
        "port": server["port"] if server["port"] else "7778",
        "server": server["server"] if server["server"] else "login01.cluster.zalf.de",
        "start-row": "0",
        "end-row": "-1",
        "shared_id": shared_id,
        "timeout": 600000  # 10 minutes
    }

    if len(sys.argv) > 1 and __name__ == "__main__":
        for arg in sys.argv[1:]:
            k, v = arg.split("=")
            if k in config:
                config[k] = v

    paths = PATHS[config["mode"]]

    if not "out" in config:
        config["out"] = paths["path-to-output-dir"]
    if not "csv-out" in config:
        config["csv-out"] = paths["path-to-csv-output-dir"]

    print("consumer config:", config)

    context = zmq.Context()
    if config["shared_id"]:
        socket = context.socket(zmq.DEALER)
        socket.setsockopt(zmq.IDENTITY, config["shared_id"])
    else:
        socket = context.socket(zmq.PULL)

    socket.connect("tcp://" + config["server"] + ":" + config["port"])
    socket.RCVTIMEO = config["timeout"]
    leave = False
    write_normal_output_files = False

    path_to_soil_grid = TEMPLATE_SOIL_PATH.format(local_path_to_data_dir=paths["path-to-data-dir"])
    soil_epsg_code = int(path_to_soil_grid.split("/")[-1].split("_")[3])
    soil_crs = CRS.from_epsg(soil_epsg_code)
    soil_metadata, header = Mrunlib.read_header(path_to_soil_grid)
    soil_grid_template = np.loadtxt(path_to_soil_grid, dtype=int, skiprows=6)

    scols = int(soil_metadata["ncols"])
    srows = int(soil_metadata["nrows"])
    scellsize = int(soil_metadata["cellsize"])
    xllcorner = int(soil_metadata["xllcorner"])
    yllcorner = int(soil_metadata["yllcorner"])
    nodata_value = int(soil_metadata["nodata_value"])

    # set all data values to one, to count them later
    soil_grid_template[soil_grid_template != nodata_value] = 1
    # set all no-data values to 0, to ignore them while counting
    soil_grid_template[soil_grid_template == nodata_value] = 0

    # count cols in rows
    datacells_per_row = np.sum(soil_grid_template, axis=1)

    start_row = int(config["start-row"])
    end_row = int(config["end-row"])
    ncols = int(soil_metadata["ncols"])
    setup_id_to_data = defaultdict(lambda: {
        "start_row": start_row,
        "end_row": end_row,
        "nrows": end_row - start_row + 1 if start_row > 0 and end_row >= start_row else int(soil_metadata["nrows"]),
        "ncols": ncols,
        "header": header,
        "out_dir_exists": False,
        "row-col-data": defaultdict(lambda: defaultdict(list)),
        "datacell-count": datacells_per_row.copy(),
        "next-row": start_row
    })

    def process_message(msg):
        if len(msg["errors"]) > 0:
            print("There were errors in message:", msg, "\nSkipping message!")
            return

        if not hasattr(process_message, "wnof_count"):
            process_message.wnof_count = 0
            process_message.setup_count = 0

        leave = False

        if not write_normal_output_files:
            custom_id = msg["customId"]
            setup_id = custom_id["setup_id"]
            is_nodata = custom_id["nodata"]

            data = setup_id_to_data[setup_id]

            row = custom_id["srow"]
            col = custom_id["scol"]
            # crow = custom_id.get("crow", -1)
            # ccol = custom_id.get("ccol", -1)
            # soil_id = custom_id.get("soil_id", -1)

            debug_msg = "received work result " + str(process_message.received_env_count) + " customId: " + str(
                msg.get("customId", "")) \
                        + " next row: " + str(data["next-row"]) \
                        + " cols@row to go: " + str(data["datacell-count"][row]) + "@" + str(
                row) + " cells_per_row: " + str(datacells_per_row[row])
            # + " rows unwritten: " + str(data["row-col-data"].keys())
            # print(debug_msg)
            # debug_file.write(debug_msg + "\n")
            if is_nodata:
                data["row-col-data"][row][col] = -9999
            else:
                data["row-col-data"][row][col].append(create_output(msg))
            data["datacell-count"][row] -= 1

            process_message.received_env_count = process_message.received_env_count + 1

            while (data["next-row"] in data["row-col-data"] and data["datacell-count"][data["next-row"]] == 0) \
                    or (
                    len(data["datacell-count"]) > data["next-row"] and data["datacell-count"][data["next-row"]] == 0):

                path_to_out_dir = config["out"] + str(setup_id) + "/"
                path_to_csv_out_dir = config["csv-out"] + str(setup_id) + "/"
                print(path_to_out_dir)
                if not data["out_dir_exists"]:
                    if os.path.isdir(path_to_out_dir) and os.path.exists(path_to_out_dir):
                        data["out_dir_exists"] = True
                    else:
                        try:
                            os.makedirs(path_to_out_dir)
                            data["out_dir_exists"] = True
                        except OSError:
                            print("c: Couldn't create dir:", path_to_out_dir, "! Exiting.")
                            exit(1)
                    if os.path.isdir(path_to_csv_out_dir) and os.path.exists(path_to_csv_out_dir):
                        data["out_dir_exists"] = True
                    else:
                        try:
                            os.makedirs(path_to_csv_out_dir)
                            data["out_dir_exists"] = True
                        except OSError:
                            print("c: Couldn't create dir:", path_to_csv_out_dir, "! Exiting.")
                            exit(1)

                write_row_to_grids(data["row-col-data"], data["next-row"], data["ncols"], data["header"],
                                   path_to_out_dir, path_to_csv_out_dir, setup_id, start_row)

                # debug_msg = "wrote row: " + str(data["next-row"]) + " next-row: " + str(
                #     data["next-row"] + 1) + " rows unwritten: " + str(list(data["row-col-data"].keys()))
                # print(debug_msg)
                # debug_file.write(debug_msg + "\n")

                data["next-row"] += 1  # move to next row (to be written)

                if leave_after_finished_run \
                        and ((data["end_row"] < 0 and data["next-row"] > data["nrows"] - 1)
                             or (0 <= data["end_row"] < data["next-row"])):
                    process_message.setup_count += 1

                    folder = os.path.join(config["out"], str(setup_id))
                    archive = folder + ".tar.gz"

                    if os.path.isdir(folder):
                        with tarfile.open(archive, "w:gz") as tar:
                            tar.add(folder, arcname=os.path.basename(folder))

                        shutil.rmtree(folder)

        elif write_normal_output_files:
            if msg.get("type", "") in ["jobs-per-cell", "no-data", "setup_data"]:
                # print "ignoring", result.get("type", "")
                return

            print("received work result ", process_message.received_env_count, " customId: ",
                  str(msg.get("customId", "")))

            custom_id = msg["customId"]
            is_nodata = custom_id["nodata"]
            if is_nodata:
                return leave
            setup_id = custom_id["setup_id"]
            row = custom_id["srow"]
            col = custom_id["scol"]
            # crow = custom_id.get("crow", -1)
            # ccol = custom_id.get("ccol", -1)
            # soil_id = custom_id.get("soil_id", -1)

            process_message.wnof_count += 1

            path_to_out_dir = config["out"] + str(setup_id) + "/" + str(row) + "/"
            print(path_to_out_dir)
            if not os.path.exists(path_to_out_dir):
                try:
                    os.makedirs(path_to_out_dir)
                except OSError:
                    print("c: Couldn't create dir:", path_to_out_dir, "! Exiting.")
                    exit(1)

            # with open("out/out-" + str(i) + ".csv", 'wb') as _:
            with open(path_to_out_dir + "col-" + str(col) + ".csv", "w", newline='', encoding="utf-8") as _:

                writer = csv.writer(_, delimiter=",")
                for data_ in msg.get("data", []):
                    results = data_.get("results", [])
                    orig_spec = data_.get("origSpec", "")
                    output_ids = data_.get("outputIds", [])

                    if len(results) > 0:
                        writer.writerow([orig_spec.replace("\"", "")])
                        for row in monica_io3.write_output_header_rows(output_ids,
                                                                       include_header_row=True,
                                                                       include_units_row=True,
                                                                       include_time_agg=False):
                            writer.writerow(row)

                        for result in results:
                            row = []
                            for output_id in output_ids:
                                field_name = output_id["name"]
                                row.append(result.get(field_name, ""))
                            writer.writerow(row)

                writer.writerow([])

            process_message.received_env_count = process_message.received_env_count + 1

        return leave

    process_message.received_env_count = 1

    while not leave:
        try:
            # start_time_recv = timeit.default_timer()
            msg = socket.recv_json()  # encoding="latin-1"
            # elapsed = timeit.default_timer() - start_time_recv
            # print("time to receive message" + str(elapsed))
            # start_time_proc = timeit.default_timer()
            leave = process_message(msg)
            # elapsed = timeit.default_timer() - start_time_proc
            # print("time to process message" + str(elapsed))
        except zmq.error.Again as _e:
            print('no response from the server (with "timeout"=%d ms) ' % socket.RCVTIMEO)
            return
        except Exception as e:
            print("Exception:", e)
            # continue

    print("exiting run_consumer()")
    # debug_file.close()


if __name__ == "__main__":
    run_consumer()
