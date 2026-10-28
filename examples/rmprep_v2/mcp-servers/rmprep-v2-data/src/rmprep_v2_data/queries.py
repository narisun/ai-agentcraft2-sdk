"""The named queries of this service, as text the data source loads.

The data source is configured with ``queries_module`` set to this module (see ``.env.example``).
It reads ``QUERIES``: each query's name and its text, the header lines then the SQL. The queries
are not written here. They are written beside what uses them: ``find_clients`` in ``server.py``,
the sections of ``get_client_records`` in ``client_sections.py`` and the sections of
``get_reference_data`` in ``reference_sections.py``. A name is the section's value, which is also
what the section is called in a call and in the audit log.
"""

from rmprep_v2_data.client_sections import CLIENT_SECTIONS
from rmprep_v2_data.reference_sections import REFERENCE_SECTIONS
from rmprep_v2_data.server import FIND_CLIENTS

__all__ = ["QUERIES"]

QUERIES: dict[str, str] = {
    "find_clients": FIND_CLIENTS.text("find_clients"),
    **{name: section.text(name) for name, section in CLIENT_SECTIONS.items()},
    **{name: section.text(name) for name, section in REFERENCE_SECTIONS.items()},
}
