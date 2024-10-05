"""
This workaround is needed because streamlit does not support an app from a module.
https://github.com/streamlit/streamlit/issues/662#issuecomment-553356419
"""

import runpy

runpy.run_module("conda_metadata_app.app", run_name="__main__", alter_sys=True)
