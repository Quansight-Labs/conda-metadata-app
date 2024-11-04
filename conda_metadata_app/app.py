"""
If deploying a streamlit app as a Python module, we cannot use
the automatic pages/ subpages. Instead, we need to define the
pages manually.
"""

from pathlib import Path

import streamlit as st

from conda_metadata_app.app_config import AppConfig

pages_dir = Path(__file__).parent / "pages"

pages = [
    st.Page(
        pages_dir / "main_page.py",
        title="conda metadata browser",
        icon="📦",
        default=True,
    )
]

if AppConfig().enable_filepath_search:
    pages.append(
        st.Page(
            pages_dir / "search_by_file_path_page.py",
            title="Search by file path",
            icon="🔎",
            url_path="Search_by_file_path",
        )
    )

pg = st.navigation(pages)
pg.run()
