#!/usr/bin/env python
# Copyright 2025 - Nicolas CARPi - Deltablot
# License: MIT

import argparse
import logging
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PosixPath
from typing import List

import elabapi_python
import urllib3
from rocrate.rocrate import ROCrate
from bs4 import BeautifulSoup

LOG_FILE = "import.log"

# disable ssl warnings
urllib3.disable_warnings(category=urllib3.exceptions.InsecureRequestWarning)


def setup_logger(log_file=LOG_FILE):
    # logger stuff: log INFO to console and DEBUG to file
    logger = logging.getLogger("rspace2elabftw")
    logger.setLevel(logging.DEBUG)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    file_handler = logging.FileHandler(log_file)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    return logger


def read_xml_file(path: PosixPath):
    tree = ET.parse(path)
    return tree.getroot()


def create_entity(tags: List[str], dataset, part_source, root_path) -> int:
    xml_data = read_xml_file(part_source)
    title = xml_data.find("name").text
    datatype = xml_data.find("type").text

    entity_type = "experiments"
    logger.info(f"Creating entry: {title}")
    # we add a tag "imported from rspace" to the existing tags list
    tags.append("imported from rspace")
    body = {"title": title, "tags": tags}
    if datatype == "NORMAL:TEMPLATE":
        response_data, status_code, headers = (
            templatesApi.post_experiment_template_with_http_info(body=body)
        )
        entity_type = "experiments_templates"
    elif datatype == "NORMAL":
        response_data, status_code, headers = (
            experimentsApi.post_experiment_with_http_info(body=body)
        )
    else:
        logger.warning(f"WARNING: could not figure out the entity type for: {datatype}")
        return -1

    if status_code != 201:
        logger.error(f"could not create entity: got status: {status_code}")

    entity_id = int(headers.get("Location").split("/").pop())
    logger.debug(f"Created entity ({datatype}) with id: {entity_id}")
    bodies = []
    # will map file names to long_name
    uploads = {}
    for field in xml_data.find("listFields"):
        name = field.find("fieldName").text
        # prevent having all main text start with "Data"
        if name == "Data":
            name = ""
        if name:
            name = name + ": "

        data = field.find("fieldData").text
        # the Data one is processed separately from the others
        if data and field.find("fieldName").text != "Data":
            bodies.append(name + str(data))
        match field.find("fieldName").text:
            # this is where we find the images to attach
            case "Data":
                for image in field.find("imageList"):
                    source = image.find("linkFile").text.removeprefix("../")
                    source_path = root_path.joinpath(source)
                    comment = image.find("description").text
                    response_data, status_code, headers = (
                        uploadsApi.post_upload_with_http_info(
                            entity_type, entity_id, file=source_path, comment=comment
                        )
                    )
                    # get the long_name so we can replace it in the body
                    upload_id = int(headers.get("Location").split("/").pop())
                    upload = uploadsApi.read_upload(entity_type, entity_id, upload_id)
                    uploads[image.find("name").text] = upload.long_name

                # we also need to process the main html to extract equations and insert them normally
                html = field.find("fieldData").text
                if not html:
                    continue
                soup = BeautifulSoup(html, "html.parser")

                # process equations
                for div in soup.find_all("div", class_="rsEquation mceNonEditable"):
                    # retrieve the data-equation attribute from the div and slap $ around it so it is recognized by Mathjax
                    data_equation = "$" + div.get("data-equation", "").strip() + "$"

                    if data_equation:
                        obj_tag = div.find("object")
                        if obj_tag:
                            obj_tag.replace_with(data_equation)

                # process images in text
                for img in soup.find_all("img"):
                    name = img["src"].split("/").pop()
                    if not name in uploads:
                        continue
                    img["src"] = (
                        f"app/download.php?f={uploads[name]}&name={name}&storage=1"
                    )
                bodies.append(soup.prettify())

    body = {"body": "<br />".join(bodies)}
    if datatype == "NORMAL:TEMPLATE":
        templatesApi.patch_experiment_template(entity_id, body=body)
    elif datatype == "NORMAL":
        experimentsApi.patch_experiment(entity_id, body=body)
    # finally upload that xml file
    uploadsApi.post_upload(
        entity_type, entity_id, file=part_source, comment="XML data from Rspace"
    )
    return entity_id


def import_eln_archive(input_file):
    """unzip the RSpace .eln, scan subfolders for .xml files
    extracts desc, creates the exp and uploads all files
    with the "desc" as comment to eLabFTW"""
    logger.debug("====================== Starting import ======================")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        logger.debug(f"Temporary path where crate is extracted: {tmp_path}")
        with zipfile.ZipFile(input_file, "r") as zip_ref:
            zip_ref.extractall(tmp_path)

        crate_root = next(tmp_path.iterdir())
        logger.debug(f"Crate root: {crate_root}")
        crate = ROCrate(crate_root)
        for e in crate.data_entities:
            if e.type == "Dataset" and e.id.startswith("doc_"):
                logger.debug(f"Processing {e.type}: {e.id}")
                dataset = crate.dereference(e.id)
                tags = e.get("keywords", [])
                # do a first loop to find the xml file and create entity
                for part in dataset.get("hasPart", []):
                    # we ignore _form.xml files
                    if part.id.endswith(".xml") and not part.id.endswith("_form.xml"):
                        logger.debug(f"Found XML file to read and import: {part.id}")
                        entity_id = create_entity(
                            tags, dataset, part.source, crate.source
                        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Process an .eln file exported from RSpace, into eLabFTW. It creates the Experiments with their files and descriptions. It must run with API_HOST_URL (e.g. https://elab.example.org/api/v2) and API_KEY env variables set."
    )
    parser.add_argument(
        "input",
        help=f"Path to the input folder, must follow the specifications for the ELN file format (see https://github.com/TheELNConsortium/TheELNFileFormat/blob/master/SPECIFICATION.md).",
    )
    parser.add_argument("--log-file", type=Path, help="Optional path to a log file")

    # ENV config
    API_HOST_URL = os.getenv("API_HOST_URL") or sys.exit(
        "Missing ENV var: API_HOST_URL. Example: https://elab.example.org/api/v2"
    )
    API_KEY = os.getenv("API_KEY") or sys.exit(
        "Missing ENV var: API_KEY. Example: 3-86e9f9...3f6f2e3"
    )
    # configure API Client
    configuration = elabapi_python.Configuration()
    configuration.host = API_HOST_URL
    # set to True if you have a proper certificate, here it is set to False to ease the test in dev
    configuration.verify_ssl = False
    # create an instance of the API class and set headers
    api_client = elabapi_python.ApiClient(configuration)
    api_client.set_default_header(header_name="Authorization", header_value=API_KEY)

    # create api objects we will use in the script
    experimentsApi = elabapi_python.ExperimentsApi(api_client)
    templatesApi = elabapi_python.ExperimentsTemplatesApi(api_client)
    uploadsApi = elabapi_python.UploadsApi(api_client)

    args = parser.parse_args()
    logger = setup_logger(args.log_file)

    try:
        import_eln_archive(args.input)
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received. Exiting...")
