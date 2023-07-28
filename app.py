import streamlit as st
from conda_forge_metadata.oci import get_oci_artifact_data


st.title("conda packages metadata app")
with st.form("metadata"):
    channel = st.selectbox("Select a channel:", ["conda-forge", "bioconda"])
    subdir = st.selectbox(
        "Select a subdir:",
        [
            "noarch",
            "linux-64",
            "linux-aarch64",
            "linux-ppc64le",
            "osx-64",
            "osx-arm64",
            "win-64",
        ],
        index=1,
    )
    package_name = st.text_input(
        "Enter a package name (e.g. `conda-forge-ci-setup`):",
        placeholder="conda-forge-ci-setup",
    )
    version = st.text_input("Enter a version (e.g. `3.32.5`):", placeholder="3.32.5")
    build = st.text_input(
        "Enter a build string (e.g. `py39hb2a8d07_100`):", placeholder="py39hb2a8d07_100"
    )
    extension = st.selectbox("Select an extension:", ["tar.bz2", "conda"])
    submitted = st.form_submit_button("Submit")
    if submitted:
        if not all([package_name, version, build]):
            st.error("Please fill out all fields.")
            st.stop()
        with st.spinner("Fetching metadata..."):
            data = get_oci_artifact_data(
                channel=channel,
                subdir=subdir,
                artifact=f"{package_name}-{version}-{build}.{extension}",
            )
        if data:
            st.write(data)
        else:
            st.write(
                f"No metadata found for `{channel}/{subdir}::"
                f"{package_name}-{version}-{build}.{extension}`."
            )
