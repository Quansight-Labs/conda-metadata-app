from pathlib import Path

import streamlit as st

"""
If deploying a streamlit app as a Python module, we cannot use
the automatic pages/ subpages. Instead, we need to define the
pages manually.
"""

pages_dir = Path(__file__).parent / "pages"

main_page = st.Page(
    pages_dir / "main_page.py",
    title="app",
)

search_by_file_path_page = st.Page(
    pages_dir / "search_by_file_path_page.py",
    title="Search By File Path",
)

pg = st.navigation([main_page, search_by_file_path_page])
pg.run()
