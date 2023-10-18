import json
import os
import re
from contextlib import closing
from datetime import datetime
from difflib import unified_diff
from inspect import cleandoc
from tempfile import gettempdir

if not os.environ.get("CACHE_DIR"):
    from conda_oci_mirror import defaults

    os.environ["CACHE_DIR"] = defaults.CACHE_DIR = os.path.join(
        gettempdir(), "conda-oci-mirror-cache"
    )

import requests
import streamlit as st
from conda_forge_metadata.oci import get_oci_artifact_data
from conda_forge_metadata.feedstock_outputs import package_to_feedstock
from conda_package_streaming.package_streaming import stream_conda_component
from conda_package_streaming.url import conda_reader_for_url
from streamlit.logger import get_logger

from version_order import VersionOrder


logger = get_logger(__name__)
st.set_page_config(
    page_title="conda metadata browser",
    page_icon="📦",
    initial_sidebar_state="expanded",
)
ONE_DAY = 60 * 60 * 24
TWO_HOURS = 60 * 60 * 4


@st.cache_data(ttl=TWO_HOURS, max_entries=100)
def channeldata(channel="conda-forge"):
    r = requests.get(f"https://conda.anaconda.org/{channel}/channeldata.json")
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=TWO_HOURS, max_entries=100)
def api_data(package_name, channel="conda-forge"):
    r = requests.get(f"https://api.anaconda.org/package/{channel}/{package_name}/files")
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=ONE_DAY, max_entries=2)
def repodata_patches(channel="conda-forge"):
    package_name = f"{channel}-repodata-patches"
    data = api_data(package_name, channel)
    most_recent = sorted(data, key=lambda x: x["attrs"]["timestamp"], reverse=True)[0]
    filename, conda = conda_reader_for_url(f"https:{most_recent['download_url']}")

    patches = {}
    with closing(conda):
        for tar, member in stream_conda_component(filename, conda, component="pkg"):
            if member.name.endswith("patch_instructions.json"):
                patches[member.name.split("/")[0]] = json.load(tar.extractfile(member))
    return patches


@st.cache_data(ttl=ONE_DAY, max_entries=1000)
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


@st.cache_data(show_spinner=False)
def package_names(channel="conda-forge"):
    return "", *sorted(
        channeldata(channel)["packages"].keys(),
        key=lambda x: f"zzzzzzz{x}" if x.startswith("_") else x,
    )


@st.cache_data(show_spinner=False)
def subdirs(package_name, channel="conda-forge"):
    if not package_name:
        return []
    return sorted(channeldata(channel)["packages"][package_name]["subdirs"])


@st.cache_data(show_spinner=False)
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


@st.cache_data(show_spinner=False)
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


@st.cache_data(show_spinner=False)
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


@st.cache_data(show_spinner=False)
def patched_repodata(channel, subdir, artifact):
    patches = repodata_patches(channel)[subdir]
    key = "packages.conda" if artifact.endswith(".conda") else "packages"
    patched_data = patches[key].get(artifact, {})
    yanked = artifact in patches["remove"]
    return patched_data, yanked


def is_archived_feedstock(feedstock):
    owner, repo = feedstock.split("/")[-2:]
    if owner != "conda-forge":
        return False
    r = requests.get(f"https://api.github.com/repos/{owner}/{repo}")
    if r.ok:
        return r.json().get("archived", False)
    return False


def parse_url_params():
    channel, subdir, artifact, package_name, version, build, extension = [None] * 7
    url_params = st.experimental_get_query_params()
    ok = True
    if "q" in url_params:
        query = url_params["q"][0]
        try:
            channel, subdir, artifact = query.rsplit("/", 2)
        except Exception as exc:
            logger.error(exc)
            ok = False
        else:
            if artifact:
                if artifact.endswith(".conda"):
                    extension = "conda"
                elif artifact.endswith(".tar.bz2"):
                    extension = "tar.bz2"
                else:
                    extension = None
                    channel, subdir, artifact = [None] * 3
                    ok = False
                if extension:
                    try:
                        package_name, version, build = artifact[
                            : -len(f".{extension}")
                        ].rsplit("-", 2)
                    except Exception as exc:
                        logger.error(exc)
                        ok = False
    elif url_params:
        ok = False
    return {
        "channel": channel,
        "subdir": subdir,
        "artifact": artifact,
        "package_name": package_name,
        "version": version,
        "build": build,
        "extension": extension,
    }, ok


url_params, url_ok = parse_url_params()
if not url_ok:
    st.experimental_set_query_params()
    st.error(
        f"Invalid URL params: `{url_params}`.\n\n"
        "Use syntax `/?q=channel/subdir/package_name-version-build.extension`."
    )
elif url_params["artifact"] and "channel" not in st.session_state:
    # Initialize state from URL params, only on first run
    # These state keys match the sidebar widgets keys below
    st.session_state.channel = url_params["channel"]
    st.session_state.subdir = url_params["subdir"]
    st.session_state.package_name = url_params["package_name"]
    st.session_state.version = url_params["version"]
    st.session_state.build = url_params["build"]
    st.session_state.extension = url_params["extension"]

with st.sidebar:
    st.title(
        "conda metadata browser",
        help="Web UI to browse the conda package metadata exposed at "
        "https://github.com/orgs/channel-mirrors/packages.\n\n "
        "If you need programmatic usage, check the [REST API]"
        "(https://condametadata-1-n5494491.deta.app).",
    )
    channel = st.selectbox(
        "Select a channel:",
        ["conda-forge", "bioconda"],
        key="channel",
    )
    package_name = st.selectbox(
        "Enter a package name:",
        options=package_names(channel),
        key="package_name",
    )
    subdir = st.selectbox(
        "Select a subdir:",
        options=subdirs(package_name, channel),
        key="subdir",
    )
    version = st.selectbox(
        "Select a version:",
        options=versions(package_name, subdir, channel),
        key="version",
    )
    build = st.selectbox(
        "Select a build:",
        options=builds(package_name, subdir, version, channel),
        key="build",
    )
    extension = st.selectbox(
        "Select an extension:",
        options=extensions(package_name, subdir, version, build, channel),
        key="extension",
    )
    with_patches = st.checkbox(
        "Show patches and broken packages",
        value=False,
        key="with_patches",
    )
    if channel == "conda-forge":
        mark_archived_feedstocks = st.checkbox(
            "Mark archived feedstocks",
            value=False,
            key="mark_archived_feedstocks",
        )
    else:
        mark_archived_feedstocks = False


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
        key="query_input",
    )
with c2:
    submitted = st.button(
        "Submit",
        key="form",
        disabled=disable_button(query),
        use_container_width=True,
    )

if submitted or all([channel, subdir, package_name, version, build, extension]):
    channel_subdir, artifact = query.split("::")
    channel, subdir = channel_subdir.split("/", 1)
    st.experimental_set_query_params(q=f"{channel}/{subdir}/{artifact}")
    with st.spinner("Fetching metadata..."):
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
        for url in feedstock_url(package_name, channel):
            name = url.split("/")[-1]
            if mark_archived_feedstocks:
                if is_archived_feedstock(url):
                    name = f"~~{name}~~"
            feedstocks.append(f"[{name}]({url})")
        feedstocks = ", ".join(feedstocks)
    except Exception as exc:
        logger.error(exc, exc_info=True)

    if with_patches:
        patched_data, yanked = patched_repodata(channel, subdir, artifact)
    else:
        patched_data, yanked = {}, False

    st.markdown(
        f'## {"❌ " if yanked else ""}{data["name"]} {data["version"]}',
    )
    if yanked:
        st.error(
            "This artifact has been removed from the index and it's only available via URL."
        )
    about = data.get("about") or data.get("rendered_recipe", {}).get("about", {})
    home_url = f"[Home]({about['home']})" if about.get("home") else "N/A"
    st.write(
        cleandoc(
            f"""
            > {" ".join(about.get("summary", "N/A").splitlines())}

            | **Channel** | **Subdir** | **Build** | **Extension** |
            | :---: | :---: | :---: | :---: |
            | `{channel}` | `{subdir}` | `{data.get("index", {}).get("build", "N/A")}` | `{extension}` |
            | **License** | **Uploaded** | **Website** | **Recipe(s)** |
            | `{about.get("license", "N/A")}` | {uploaded} | {home_url} | {feedstocks} |
            """
        )
    )
    st.markdown(" ")
    dependencies = data.get("index", {}).get("depends", ())
    constraints = data.get("index", {}).get("constrains", ())
    if dependencies or constraints:
        c1, c2 = st.columns([1, 1])
        for title, key, specs, col in [
            ("Dependencies", "depends", dependencies, c1),
            ("Constraints", "constrains", constraints, c2),
        ]:
            with col:
                st.write(f"### {title}")
                patched_specs = patched_data.get(key, {})
                if patched_specs:
                    specs = list(unified_diff(specs, patched_specs, n=100))[3:]
                specs = "\n".join([s.strip() for s in specs])
                if specs:
                    st.code(specs, language="diff", line_numbers=True)

        st.markdown(" ")

    if data.get("files"):
        st.write("### Files")
        all_files = "\n".join(data["files"])
        st.code(all_files, language="text", line_numbers=True)

    st.write("### Raw JSON")
    st.json(data, expanded=False)
