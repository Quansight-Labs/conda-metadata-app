import json
import os
import re
from tempfile import mkdtemp
from typing import List, Dict, Any, Union, Tuple

if not os.environ.get("CACHE_DIR"):
    from conda_oci_mirror import defaults

    os.environ["CACHE_DIR"] = defaults.CACHE_DIR = mkdtemp(
        suffix="conda-oci-mirror-cache"
    )

import requests
import streamlit as st
from conda_forge_metadata.oci import get_oci_artifact_data

from st_route import st_route


@st_route(path=r"meta/(.*)", globally=True)
def any_name(
    path_args: List[str],
    path_kwargs: Dict[str, Any],
    method: str,
    body: bytes,
    arguments: Dict[str, Any],
) -> Union[
    int,
    bytes,
    str,
    Tuple[int, Union[bytes, str]],
    Tuple[int, Union[bytes, str], Dict[str, Any]],
]:
    """
    path_args: path regex unnamed groups
    path_kwargs: path regex named groups
    method: HTTP method
    body: the request body in bytes
    arguments: The query and body arguments

    returns with any of the followings:
      int: HTTP response code
      bytes/str: HTTP 200 with body. str encoded with python default
      Tuple[int, bytes/str]: HTTP response code with body
      Tuple[int, bytes/str, Dict[str, Any]]: HTTP response code with body and additional headers

    If you don't need any of the arguments, just use **kwargs.
    """
    query = path_args[0].decode()
    fields = re.match(r"(?P<channel>\S+)/(?P<subdir>\S+)/(?P<artifact>\S+)", query)
    if not fields:
        return (
            400,
            "{'ok': false, 'resp': 'Bad request. Use `meta/<channel>/<subdir>/<artifact.extension>`.'}",
            {"Content-Type": "application/json"},
        )
    channel = fields["channel"]
    if len(channel) > 50:
        return (
            400,
            "{'ok': false, 'resp': 'Channel name too long'}",
            {"Content-Type": "application/json"},
        )
    subdir = fields["subdir"]
    if len(subdir) > 20:
        return (
            400,
            "{'ok': false, 'resp': 'Subdir name too long'}",
            {"Content-Type": "application/json"},
        )
    artifact = fields["artifact"]
    if len(artifact) > 100:
        return (
            400,
            "{'ok': false, 'resp': 'Artifact name too long'}",
            {"Content-Type": "application/json"},
        )
    if not artifact.endswith(".conda") and not artifact.endswith(".tar.bz2"):
        return (
            400,
            "{'ok': false, 'resp': 'Artifact extension not supported. Use .conda or .tar.bz2.'}",
            {"Content-Type": "application/json"},
        )
    data = get_oci_artifact_data(
        channel=channel,
        subdir=subdir,
        artifact=artifact,
    )
    if data:
        return (
            200,
            json.dumps({"resp": data, "ok": True}),
            {"Content-Type": "application/json"},
        )
    return (
        404,
        "{'ok': false, 'resp': 'No data returned'}",
        {"Content-Type": "application/json"},
    )


@st.cache_data
def channeldata(channel="conda-forge"):
    r = requests.get(f"https://conda.anaconda.org/{channel}/channeldata.json")
    r.raise_for_status()
    return r.json()


@st.cache_data
def api_data(package_name, channel="conda-forge"):
    r = requests.get(f"https://api.anaconda.org/package/{channel}/{package_name}/files")
    r.raise_for_status()
    return r.json()


@st.cache_data
def package_names(channel="conda-forge"):
    return "", *sorted(
        channeldata(channel)["packages"].keys(),
        key=lambda x: f"zzzzzzz{x}" if x.startswith("_") else x,
    )


@st.cache_data
def subdirs(package_name, channel="conda-forge"):
    if not package_name:
        return []
    return sorted(channeldata(channel)["packages"][package_name]["subdirs"])


@st.cache_data
def versions(package_name, subdir, channel="conda-forge"):
    if not package_name or not subdir:
        return []
    data = api_data(package_name, channel)
    return list(
        {pkg["version"]: None for pkg in data if pkg["attrs"]["subdir"] == subdir}
    )


@st.cache_data
def builds(package_name, subdir, version, channel="conda-forge"):
    if not package_name or not subdir or not version:
        return []
    data = api_data(package_name, channel)
    build_str_to_num = {
        pkg["attrs"]["build"]: pkg["attrs"]["build_number"]
        for pkg in data
        if pkg["attrs"]["subdir"] == subdir and pkg["version"] == version
    }
    return [k for k, _ in sorted(build_str_to_num.items(), key=lambda kv: kv[1])]


@st.cache_data
def extensions(package_name, subdir, version, build, channel="conda-forge"):
    if not package_name or not subdir or not version or not build:
        return []
    data = api_data(package_name, channel)
    return list(
        {
            ("conda" if pkg["basename"].endswith(".conda") else "tar.bz2"): None
            for pkg in data
            if pkg["attrs"]["subdir"] == subdir
            and pkg["version"] == version
            and pkg["attrs"]["build"] == build
        }
    )


with st.sidebar:
    st.title("conda metadata browser")
    channel = st.selectbox("Select a channel:", ["conda-forge", "bioconda"])
    with st.spinner("Fetching package names..."):
        package_name = st.selectbox(
            "Enter a package name (e.g. `python`):",
            options=package_names(channel),
            index=0,
        )
        subdir = st.selectbox(
            "Select a subdir:",
            options=subdirs(package_name, channel),
            index=0,
        )
        version = st.selectbox(
            "Select a version (e.g. `3.32.5`):",
            options=versions(package_name, subdir, channel),
        )
        build = st.selectbox(
            "Select a build (e.g. `py39hb2a8d07_100`):",
            options=builds(package_name, subdir, version, channel),
        )
        extension = st.selectbox(
            "Select an extension:",
            options=extensions(package_name, subdir, version, build, channel),
        )

    submitted_form = st.button(
        "Submit",
        key="form",
        disabled=not all([channel, package_name, subdir, version, build, extension]),
    )
    with st.expander("Advanced mode"):
        advanced = st.text_input(
            "Use `channel/subdir::package_name-version-build.ext`",
            placeholder="conda-forge/linux-64::conda-forge-ci-setup-3.32.5-py39hb2a8d07_100.conda",
            key="advanced",
        )
        submitted_adv = st.button("Query", key="adv", disabled=not advanced)


if submitted_adv and advanced:
    with st.spinner("Fetching metadata..."):
        channel_subdir, artifact = advanced.split("::")
        channel, subdir = channel_subdir.split("/")
        data = get_oci_artifact_data(
            channel=channel,
            subdir=subdir,
            artifact=artifact,
        )
        if not data:
            data = f"No metadata found for `{advanced}`."
elif submitted_form and all([package_name, version, build]):
    with st.spinner("Fetching metadata..."):
        data = get_oci_artifact_data(
            channel=channel,
            subdir=subdir,
            artifact=f"{package_name}-{version}-{build}.{extension}",
        )
        if not data:
            data = f"No metadata found for `{channel}/{subdir}::"
            f"{package_name}-{version}-{build}.{extension}`."
else:
    data = "> _Use the sidebar to show the metadata of a package._"
st.write(data)
