import time
from inspect import cleandoc

import requests
import streamlit as st
from streamlit_searchbox import st_searchbox

from conda_metadata_app.app_config import AppConfig


@st.cache_resource(ttl="15m", max_entries=100)
def autocomplete_paths(query):
    time.sleep(0.25)
    try:
        r = requests.get(
            "https://cforge.quansight.dev/path_to_artifacts/find_files.json",
            params={"path": query},
        )
    except Exception as exc:
        data = {"ok": False, "error": str(exc), "title": exc.__class__.__name__}
    else:
        data = r.json()

    if data["ok"]:
        return [row[0] for row in data["rows"]]
    else:
        st.error(
            f"Autocomplete API errored while querying '{query}': "
            f"{data['title']}! {data['error'].strip()}"
        )
        st.stop()


def find_artifacts_by_path(path):
    r = requests.get(
        "https://cforge.quansight.dev/path_to_artifacts/find_artifacts.json",
        params={"path": path},
    )
    r.raise_for_status()
    data = r.json()
    if data["ok"]:
        return [row[0] for row in data["rows"]]
    return data


st.set_page_config(
    page_title="Find artifacts by path | conda metadata browser",
    page_icon="📦",
    initial_sidebar_state="collapsed",
)

if not AppConfig().enable_filepath_search:
    st.error("File path search is disabled in the app configuration.")
    st.stop()

c1, c2 = st.columns([1, 0.25])
with c1:
    path_to_search = st_searchbox(
        autocomplete_paths,
        placeholder="Choose one path (type for autocomplete)",
        key="path_search_input",
        default=st.query_params.get("path"),
    )
with c2:
    submitted = st.button(
        "Submit",
        key="form",
        disabled=not path_to_search,
        use_container_width=True,
    )

if path_to_search:
    data = find_artifacts_by_path(path_to_search)
    st.write("### Search results (most recently published first)")
    st.write(f"> {len(data)} artifacts ship `{path_to_search}`")
    lines = []
    for artifact in data:
        channel, subdir, artifact = artifact.rsplit("/", 2)
        if channel == "cf":
            channel = "conda-forge"
        lines.append(
            f"- [`{channel}/{subdir}::{artifact}`](/?q={channel}/{subdir}/{artifact}&with_broken=true)"
        )
    st.write("\n".join(lines))
else:
    st.write(
        cleandoc(
            """
            # Find conda artifacts by path

            Use this searchbox to find which artifact(s) contain a given file path.

            To note:

            - The search is not case-sensitive.
            - The search will match on directory names, file names, basenames and extensions.
            - The search will _not_ match on partial directories or filenames.
            - The autocomplete API will return 100 path suggestions at most. If the query is too
              broad, it might error out. Try to be more specific.

            Some good examples include:

            - `libcuda`
            - `activate.bat`
            - `bin/conda`
            - `lib/python3.9/site-packages/numpy/core/include/numpy`

            Please raise any issues at [Quansight-Labs/conda-metadata-app](
            https://github.com/Quansight-Labs/conda-metadata-app).
            """
        )
    )
