import os
import re
from inspect import cleandoc
from tempfile import gettempdir
from datetime import datetime

if not os.environ.get("CACHE_DIR"):
    from conda_oci_mirror import defaults

    os.environ["CACHE_DIR"] = defaults.CACHE_DIR = os.path.join(
        gettempdir(), "conda-oci-mirror-cache"
    )

import requests
import streamlit as st

from conda_forge_metadata.oci import get_oci_artifact_data
from conda_forge_metadata.feedstock_outputs import package_to_feedstock
from streamlit.logger import get_logger

from version_order import VersionOrder

logger = get_logger(__name__)


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
    return sorted(
        {
            pkg["version"]: None
            for pkg in data
            if pkg["attrs"]["subdir"] == subdir and "main" in pkg["labels"]
        },
        key=VersionOrder,
        reverse=True,
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
    return [
        k
        for k, _ in sorted(
            build_str_to_num.items(), key=lambda kv: (kv[1], kv[0]), reverse=True
        )
    ]


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


@st.cache_data
def feedstock_url(package_name, channel="conda-forge"):
    if not package_name:
        return ""
    if channel == "conda-forge":
        feedstocks = package_to_feedstock(package_name)
        return [f"https://github.com/conda-forge/{f}-feedstock" for f in feedstocks]
    elif channel == "bioconda":
        return [
            f"https://github.com/bioconda/bioconda-recipes/tree/master/recipes/{package_name}"
        ]
    return ""

url_params = st.experimental_get_query_params()
channel, subdir, artifact, package_name, version, build, ext = [None] * 7
bad_url = False
if "q" in url_params:
    query = url_params["q"][0]
    try:
        channel, subdir, artifact = query.rsplit("/", 2)
    except Exception as exc:
        logger.error(exc)
        bad_url = True
    else:
        if artifact:
            if artifact.endswith(".conda"):
                ext = "conda"
            elif artifact.endswith(".tar.bz2"):
                ext = "tar.bz2"
            else:
                ext = None
                channel, subdir, artifact = [None] * 3
                bad_url = True
            if ext:
                try:
                    package_name, version, build = artifact[:-len(f".{ext}")].rsplit("-", 2)
                except Exception as exc:
                    logger.error(exc)
                    bad_url = True
elif url_params:
    bad_url = True

if bad_url:
    st.experimental_set_query_params()
    st.error(
        f"Invalid URL params: `{url_params}`.\n\n"
        "Use syntax `/?q=channel/subdir/package_name-version-build.extension`."
    )
        


with st.sidebar:
    st.title(
        "conda metadata browser",
        help="Web UI to browse the conda package metadata exposed at "
        "https://github.com/orgs/channel-mirrors/packages.\n\n "
        "If you need programmatic usage, check the [REST API]"
        "(https://condametadata-1-n5494491.deta.app).",
    )
    channels = ["conda-forge", "bioconda"]
    channel = st.selectbox("Select a channel:", channels, index=channels.index(channel) if channel else 0)
    with st.spinner("Fetching package names..."):
        package_name = st.selectbox(
            "Enter a package name:",
            options=package_names(channel),
            index=package_names(channel).index(package_name) if package_name else 0,
        )
        subdir = st.selectbox(
            "Select a subdir:",
            options=subdirs(package_name, channel),
            index=subdirs(package_name, channel).index(subdir) if subdir else 0,
        )
        version = st.selectbox(
            "Select a version:",
            options=versions(package_name, subdir, channel),
            index=versions(package_name, subdir, channel).index(version) if version else 0,
        )
        build = st.selectbox(
            "Select a build:",
            options=builds(package_name, subdir, version, channel),
            index=builds(package_name, subdir, version, channel).index(build) if build else 0,
        )
        extension = st.selectbox(
            "Select an extension:",
            options=extensions(package_name, subdir, version, build, channel),
            index=extensions(package_name, subdir, version, build, channel).index(ext) if ext else 0,
        )


def input_value_so_far():
    value = ""
    if channel:
        value = f"{channel}/"
    if package_name:
        if subdir:
            value = f"{value}{subdir}::"
        value = f"{value}{package_name}-"
        if version:
            value = f"{value}{version}-"
            if build:
                value = f"{value}{build}."
                if extension:
                    value = f"{value}{extension}"
    return value


def disable_button(query):
    if re.match(
        r"^[a-z0-9-]+/[a-z0-9-]+::[a-z0-9-]+-[0-9.]+-[a-z0-9_]+.[a-z0-9]+$", query
    ):
        return False
    if all([channel, subdir, package_name, version, build, extension]):
        return False
    return True


c1, c2 = st.columns([1, 0.25])
with c1:
    query = st.text_input(
        label="Search artifact metadata:",
        placeholder="channel/subdir::package_name-version-build.ext",
        value=input_value_so_far(),
        label_visibility="collapsed",
    )
with c2:
    submitted = st.button(
        "Submit",
        key="form",
        disabled=disable_button(query),
        use_container_width=True,
    )

if submitted or all([channel, subdir, package_name, version, build, extension]):
    with st.spinner("Fetching metadata..."):
        channel_subdir, artifact = query.split("::")
        channel, subdir = channel_subdir.split("/", 1)
        data = get_oci_artifact_data(
            channel=channel,
            subdir=subdir,
            artifact=artifact,
        )
        if not data:
            logger.error(f"No metadata found for `{query}`.")
            st.error(f"No metadata found for `{query}`.")
            st.stop()
else:
    data = ""

if data:
    uploaded = "N/A"
    try:
        timestamp = data.get("index", {}).get("timestamp", 0) / 1000
        if timestamp:
            uploaded = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
    except Exception as exc:
        logger.error(exc, exc_info=True)

    feedstocks = "N/A"
    try:
        feedstocks = []
        for f in feedstock_url(package_name, channel):
            feedstocks.append(f"[{f.split('/')[-1]}]({f})")
        feedstocks = ", ".join(feedstocks)
    except Exception as exc:
        logger.error(exc, exc_info=True)

    st.write(
        cleandoc(
            f"""
            ## {data["name"]} {data["version"]}
            > {data.get("about", {}).get("summary", "N/A")}

            | **Channel** | **Subdir** | **Build** | **Extension** |
            | :---: | :---: | :---: | :---: |
            | `{channel}` | `{subdir}` | `{data.get("index", {}).get("build", "N/A")}` | `{extension}` |
            | **License** | **Uploaded** | **Website** | **Recipe(s)** | 
            | `{data.get("about", {}).get("license", "N/A")}` | {uploaded} | [Home]({data.get("about", {}).get("home", "N/A")}) | {feedstocks} |
            """
        )
    )
    st.markdown(" ")
    dependencies = data.get("index", {}).get("depends", ())
    constraints = data.get("index", {}).get("constrains", ())
    if dependencies or constraints:
        c1, c2 = st.columns([1, 1])
        with c1:
            st.write("### Dependencies")
            deps = "\n".join(dependencies)
            if deps:
                st.code(deps, language="text", line_numbers=True)

        with c2:
            st.write("### Constraints")
            deps = "\n".join(constraints)
            if deps:
                st.code(deps, language="text", line_numbers=True)

        st.markdown(" ")

    if data.get("files"):
        st.write("### Files")
        all_files = "\n".join(data["files"])
        st.code(all_files, language="text", line_numbers=True)

    st.write("### Raw JSON")
    st.json(data, expanded=False)
