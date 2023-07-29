import os
from tempfile import mkdtemp

if not os.environ.get("CACHE_DIR"):
    from conda_oci_mirror import defaults

    os.environ["CACHE_DIR"] = defaults.CACHE_DIR = mkdtemp(
        suffix="conda-oci-mirror-cache"
    )

import requests
import streamlit as st
from conda_forge_metadata.oci import get_oci_artifact_data


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
